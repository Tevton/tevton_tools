import hou
import os
from enum import Enum
from pathlib import Path
from shutil import copy
from qt_shim import QtCore, QtUiTools, QtWidgets, QtGui

import tvt_utils
import pipeline.projects_store as projects_store
from ui_state import UIStateController
from config.config import CUSTOM_OCIO_PATH
from ftp import FTPManager
from pipeline.window_manager import get_window_manager


class ProjectMode(Enum):
    CREATE = "create"
    EDIT = "edit"


class ProjectSetup(QtWidgets.QMainWindow):
    """
    Universal window for creating and editing projects.
    Supports CREATE and EDIT modes via ProjectMode enum.
    """

    project_saved = QtCore.Signal(str)

    def __init__(
        self, parent=None, mode: ProjectMode = ProjectMode.CREATE, project_name=None
    ):
        super().__init__(parent)

        self.mode = mode
        self.current_project_name = project_name
        self.default_ocio = os.path.basename(hou.getenv("OCIO"))
        self.selected_folder = ""
        self.selected_ocio_profile = ""

        self._wm = get_window_manager()
        self._ui_state = UIStateController()
        self._connection_animation = tvt_utils.ConnectionAnimator(self)
        # Fix: Connect timeout to separate handler
        self._connection_animation.timeout_reached.connect(self._on_connection_timeout)

        self.ftp_manager = FTPManager(self)

        self._load_ui()
        self._find_widgets()
        self._setup_ui()
        self._register_widgets()
        self._configure_mode()
        self._connect_signals()
        self._setup_validators()
        self._populate_ocio_box()
        self._connect_ftp_signals()

        if self.mode == ProjectMode.CREATE:
            self._ui_state.disable_group("inputs")
        elif self.mode == ProjectMode.EDIT:
            self._load_project_data()

    # =====================================================
    # INIT
    # =====================================================

    def _load_ui(self):
        scriptpath = hou.text.expandString("$TVT/ui/ProjectCreator.ui")
        self.ui = QtUiTools.QUiLoader().load(scriptpath, parentWidget=self)
        self.setCentralWidget(self.ui)
        self.setParent(hou.qt.mainWindow(), QtCore.Qt.Window)
        self.setAttribute(QtCore.Qt.WA_DeleteOnClose)

    def _find_widgets(self):
        self.select_folder = self.ui.findChild(
            QtWidgets.QPushButton, "pb_select_folder"
        )
        self.project_path = self.ui.findChild(QtWidgets.QLabel, "lb_proj_path")
        self.project_name = self.ui.findChild(QtWidgets.QLineEdit, "le_proj_name")
        self.project_fps = self.ui.findChild(QtWidgets.QSpinBox, "sb_proj_fps")
        self.project_folders = self.ui.findChild(
            QtWidgets.QPlainTextEdit, "qpt_folders"
        )
        self.ocio_profile = self.ui.findChild(QtWidgets.QComboBox, "cb_ocio_profile")
        self.ftp_host = self.ui.findChild(QtWidgets.QLineEdit, "le_ftp_host")
        self.ftp_user = self.ui.findChild(QtWidgets.QLineEdit, "le_ftp_user")
        self.ftp_pw = self.ui.findChild(QtWidgets.QLineEdit, "le_ftp_pw")
        self.ftp_port = self.ui.findChild(QtWidgets.QLineEdit, "le_ftp_port")
        self.check_con_btn = self.ui.findChild(QtWidgets.QPushButton, "pb_check_con")
        self.con_status_ind = self.ui.findChild(QtWidgets.QLabel, "lb_con_status_ind")
        self.con_status = self.ui.findChild(QtWidgets.QLabel, "lb_con_status")
        self.save_btn = self.ui.findChild(QtWidgets.QPushButton, "pb_create_proj")
        self.cancel_btn = self.ui.findChild(QtWidgets.QPushButton, "pb_cancel_proj")

    def _setup_ui(self):
        self.setMaximumSize(400, 600)
        self.project_name.setAlignment(QtCore.Qt.AlignCenter)
        self.project_fps.setAlignment(QtCore.Qt.AlignCenter)
        self.project_fps.setRange(1, 999)
        self.project_fps.setValue(24)
        self.ftp_port.setValidator(QtGui.QIntValidator(1, 65535))
        tvt_utils.set_connection_status(self, "disconnected")

    def _register_widgets(self):
        self._ui_state.register_many(
            {
                "project_name": self.project_name,
                "project_fps": self.project_fps,
                "project_folders": self.project_folders,
                "ocio_profile": self.ocio_profile,
                "ftp_host": self.ftp_host,
                "ftp_port": self.ftp_port,
                "ftp_user": self.ftp_user,
                "ftp_pw": self.ftp_pw,
                "check_con_btn": self.check_con_btn,
            },
            groups=["inputs"],
        )
        self._ui_state.register("save_btn", self.save_btn, groups=["actions"])

    def _configure_mode(self):
        """Apply title, button label and field restrictions based on current mode."""
        if self.mode == ProjectMode.CREATE:
            self.setWindowTitle("Project Creator")
            self.save_btn.setEnabled(False)
            self.save_btn.setText("Create Project")
        else:
            self.setWindowTitle("Project Editor")
            self.select_folder.setText("Change Project Location")
            self.save_btn.setText("Update Project")
            self.project_name.setReadOnly(True)
            self.project_name.setStyleSheet(
                "background-color: #3a3a3a; color: #a0a0a0;"
            )

    def _connect_signals(self):
        self.select_folder.clicked.connect(self._select_directory)
        self.save_btn.clicked.connect(self._on_save)
        self.cancel_btn.clicked.connect(self.close)
        self.check_con_btn.clicked.connect(self.connect_to_ftp)

        self.project_name.textChanged.connect(
            lambda: tvt_utils.force_upper(self.project_name)
        )
        self.project_name.textChanged.connect(self._update_project_path)
        self.project_name.textChanged.connect(self._update_save_button)
        self.project_folders.textChanged.connect(self._update_save_button)

        self.ftp_host.textChanged.connect(
            lambda txt: self.ftp_host.setText(txt.strip())
        )
        self.ftp_user.textChanged.connect(
            lambda txt: self.ftp_user.setText(txt.strip())
        )

        self.ocio_profile.currentTextChanged.connect(self._update_ocio_choice)

    def _connect_ftp_signals(self):
        # Automatic UI block/unblock tied to FTP worker lifecycle.
        # cooldown_ms prevents button spam on fast operations.
        self._ui_state.bind(
            self.ftp_manager.busy_changed,
            "ftp_host",
            "ftp_port",
            "ftp_user",
            "ftp_pw",
            "check_con_btn",
            "save_btn",
            cooldown_ms=500,
            block_id="ftp_busy",
        )

        # Animation driven automatically by busy/connection signals.
        self._connection_animation.bind(
            self.ftp_manager.busy_changed,
            self.ftp_manager.connection_checked,
        )

        self.ftp_manager.connection_checked.connect(self._on_connection_checked)

    # =====================================================
    # VALIDATION
    # =====================================================

    def _setup_validators(self):
        self.validate_name = tvt_utils.create_field_validator(
            self.project_name, "-_", False
        )
        self.validate_folders = tvt_utils.create_field_validator(
            self.project_folders, "-_/", True
        )
        self.validate_host = tvt_utils.create_field_validator(
            self.ftp_host, allowed_symbols=".-", allow_empty=True
        )
        self.validate_user = tvt_utils.create_field_validator(
            self.ftp_user, allowed_symbols=".", allow_empty=True
        )
        self.validate_form = tvt_utils.create_form_validator(
            [self.validate_name, self.validate_folders]
        )

        self.project_name.textChanged.connect(self.validate_name)
        self.project_folders.textChanged.connect(self.validate_folders)
        self.ftp_host.textChanged.connect(self.validate_host)
        self.ftp_user.textChanged.connect(self.validate_user)

    def _update_save_button(self):
        """Enable save button only when form is valid and no FTP operation is running."""
        can_enable = (
            self.validate_form()
            and not self._ui_state.is_blocked("ftp_busy")
            and (self.mode == ProjectMode.EDIT or bool(self.selected_folder))
        )
        self._ui_state.set_enabled(can_enable, "save_btn")

    # =====================================================
    # DIRECTORY
    # =====================================================

    def _select_directory(self):
        """Open directory picker and unlock inputs on valid selection."""
        start_folder = hou.text.expandString("$HIP")
        selected = hou.ui.selectFile(
            start_directory=start_folder, file_type=hou.fileType.Directory
        )

        if not selected or not tvt_utils.is_valid_path(selected):
            return

        self.selected_folder = selected
        self._update_project_path()
        self._ui_state.enable_group("inputs")

    def _update_project_path(self):
        if self.selected_folder:
            full_path = tvt_utils.normalize_project_path(
                self.selected_folder, self.project_name.text().strip().upper()
            )
            self.project_path.setText(full_path)

    # =====================================================
    # OCIO
    # =====================================================

    def _populate_ocio_box(self):
        """Populate OCIO dropdown with available profiles. Default profile is pinned first."""
        self.ocio_profile.clear()
        ocio_dir = Path(CUSTOM_OCIO_PATH)
        ocio_dir.mkdir(parents=True, exist_ok=True)

        default_ocio_path = ocio_dir / self.default_ocio
        if not default_ocio_path.exists():
            try:
                copy(hou.getenv("OCIO"), str(default_ocio_path))
            except Exception as e:
                print(f"Warning: Could not copy default OCIO: {e}")

        ocio_files = sorted(p.name for p in ocio_dir.iterdir() if p.suffix == ".ocio")
        if self.default_ocio in ocio_files:
            ocio_files.remove(self.default_ocio)
            ocio_files.insert(0, self.default_ocio)

        for file in ocio_files:
            self.ocio_profile.addItem(file)

        if self.mode == ProjectMode.CREATE:
            index = self.ocio_profile.findText(self.default_ocio)
            if index >= 0:
                self.ocio_profile.setCurrentIndex(index)
                self.selected_ocio_profile = self.default_ocio

    def _update_ocio_choice(self):
        """Validate selected OCIO profile. Revert to default if incompatible."""
        selected = self.ocio_profile.currentText()

        if selected == self.default_ocio:
            self.selected_ocio_profile = selected
            return

        path = (Path(CUSTOM_OCIO_PATH) / selected).as_posix()
        try:
            import PyOpenColorIO as OCIO

            OCIO.Config.CreateFromFile(path)
        except Exception as e:
            self._wm.show_buttons_dialog(
                self,
                "OCIO Error",
                f"Incompatible OCIO profile:\n{selected}\n\n{str(e)}\n\n"
                f"Reverting to default: {self.default_ocio}",
                buttons=[("OK", True)],
                icon=QtWidgets.QMessageBox.Critical,
            )
            index = self.ocio_profile.findText(self.default_ocio)
            if index >= 0:
                self.ocio_profile.setCurrentIndex(index)
            self.selected_ocio_profile = self.default_ocio
            return

        self.selected_ocio_profile = selected

    # =====================================================
    # LOAD / SAVE
    # =====================================================

    def _load_project_data(self):
        """Load existing project data into form fields for editing."""
        try:
            proj = projects_store.get_project(self.current_project_name)
        except projects_store.ProjectStoreError as e:
            self._wm.show_buttons_dialog(
                self,
                "Error",
                str(e),
                buttons=[("OK", True)],
                icon=QtWidgets.QMessageBox.Critical,
            )
            self.close()
            return

        self.project_name.setText(self.current_project_name)
        self.project_fps.setValue(int(proj.get("PROJECT_FPS", 24)))

        job_path = Path(proj.get("JOB", ""))
        self.project_path.setText(str(job_path))

        if job_path.exists():
            folders = [p.name for p in job_path.iterdir() if p.is_dir()]
            self.project_folders.setPlainText(", ".join(sorted(folders)))

        self.ftp_host.setText(proj.get("PROJECT_FTP_HOST", ""))
        self.ftp_port.setText(proj.get("PROJECT_FTP_PORT", ""))
        self.ftp_user.setText(proj.get("PROJECT_FTP_USER", ""))
        self.ftp_pw.setText(proj.get("PROJECT_FTP_PASSWORD", ""))

        ocio_filename = Path(proj.get("OCIO", "")).name
        index = self.ocio_profile.findText(ocio_filename)
        if index >= 0:
            self.ocio_profile.setCurrentIndex(index)
            self.selected_ocio_profile = ocio_filename

    def _on_save(self):
        if self.mode == ProjectMode.CREATE:
            self._create_project()
        else:
            self._update_project()

    def _create_project(self):
        """Check for name conflicts before writing new project data."""
        project_name = self.project_name.text().strip()
        try:
            if projects_store.project_exists(project_name):
                existing = projects_store.get_project(project_name)
                self._wm.show_buttons_dialog(
                    self,
                    "Error",
                    f"Project '{project_name}' already exists!\nPath: {existing['JOB']}",
                    buttons=[("OK", True)],
                    icon=QtWidgets.QMessageBox.Critical,
                )
                return
        except projects_store.ProjectStoreError:
            pass  # JSON file doesn't exist yet, treat as empty

        self._write_project_data(project_name)

    def _update_project(self):
        """Update existing project with confirmation."""
        # Optional: Ask for confirmation before updating
        if self.mode == ProjectMode.EDIT:
            result = self._wm.show_buttons_dialog(
                self,
                "Confirm Update",
                f"Update project '{self.current_project_name}'?\n"
                "This will update the project settings and create any missing folders.",
                buttons=[("Update", True), ("Cancel", False)],
                icon=QtWidgets.QMessageBox.Information,
            )
            if not result:
                return

        self._write_project_data(self.project_name.text().strip())

    def _write_project_data(self, project_name):
        """
        Serialize form data to JSON, create folders on disk,
        apply env vars and reload OCIO.
        """
        folders = [
            f.strip()
            for f in self.project_folders.toPlainText().split(",")
            if f.strip()
        ]
        if "shots" not in folders:
            folders.append("shots")

        job_path = Path(self.project_path.text())
        ocio_path = (Path(CUSTOM_OCIO_PATH) / self.selected_ocio_profile).as_posix()

        payload = {
            "PROJECT_NAME": project_name,
            "PROJECT_FPS": str(self.project_fps.value()),
            "PROJECT_FOLDERS": folders,
            "JOB": job_path.as_posix(),
            "PROJECT_FTP_HOST": self.ftp_host.text(),
            "PROJECT_FTP_PORT": self.ftp_port.text(),
            "PROJECT_FTP_USER": self.ftp_user.text(),
            "PROJECT_FTP_PASSWORD": self.ftp_pw.text(),
            "OCIO": ocio_path,
        }

        try:
            projects_store.upsert_project(project_name, payload)
        except projects_store.ProjectStoreError as e:
            self._wm.show_buttons_dialog(
                self,
                "Error",
                f"Error saving project data: {e}",
                buttons=[("OK", True)],
                icon=QtWidgets.QMessageBox.Critical,
            )
            return

        try:
            job_path.mkdir(parents=True, exist_ok=True)
            for folder in folders:
                (job_path / folder).mkdir(exist_ok=True)
        except Exception as e:
            self._wm.show_buttons_dialog(
                self,
                "Warning",
                f"Error creating folders: {e}",
                buttons=[("OK", True)],
                icon=QtWidgets.QMessageBox.Warning,
            )

        for var, value in payload.items():
            if var != "PROJECT_FOLDERS":
                hou.putenv(var, str(value))

        try:
            hou.Color.reloadOCIO()
        except Exception:
            pass

        self.project_saved.emit(project_name)
        self._wm.show_buttons_dialog(
            self,
            "Success",
            f"Project '{project_name}' saved successfully!\nPath: {job_path}",
            buttons=[("OK", True)],
            icon=QtWidgets.QMessageBox.Information,
        )
        self.close()

    # =====================================================
    # FTP
    # =====================================================

    def connect_to_ftp(self):
        """Read credentials from form fields and run a one-shot connection check."""
        if self.ftp_manager.is_busy():
            return

        host = self.ftp_host.text()
        user = self.ftp_user.text()
        password = self.ftp_pw.text()
        port_text = self.ftp_port.text()

        if not all([host, user, password, port_text]):
            self._wm.show_buttons_dialog(
                self,
                "Connection Error",
                "Missing Host, User, Password or Port.",
                buttons=[("OK", True)],
                icon=QtWidgets.QMessageBox.Critical,
            )
            return

        try:
            port = int(port_text)
        except ValueError:
            self._wm.show_buttons_dialog(
                self,
                "Connection Error",
                "Invalid port number.",
                buttons=[("OK", True)],
                icon=QtWidgets.QMessageBox.Critical,
            )
            return

        self.ftp_manager.set_credentials(host, user, password, port)
        self.ftp_manager.check_connection()

    def _on_connection_checked(self, success: bool, message: str):
        """Handle result of check_connection() — update animation and show error if needed."""
        self._connection_animation.stop(success=success)

        if not success:
            # Use safe timer to show message after window is ready
            self._wm.safe_timer(
                self,
                lambda: self._wm.show_buttons_dialog(
                    self,
                    "Connection Error",
                    f"FTP connection failed:\n{message}\n\nPlease check your settings.",
                    buttons=[("OK", True)],
                    icon=QtWidgets.QMessageBox.Critical,
                ),
                100,
            )

    def _on_connection_timeout(self):
        """Handle connection timeout separately."""
        self._connection_animation.stop(success=False)

    # =====================================================
    # CLEANUP
    # =====================================================

    def closeEvent(self, event):
        """Handle close event with immediate connection abort."""

        # 1. Stop animation immediately
        if hasattr(self, "_connection_animation"):
            self._connection_animation.stop()

        # 2. If connecting, abort immediately
        if hasattr(self, "ftp_manager") and self.ftp_manager:
            # Check if connection worker is running
            if self.ftp_manager.is_busy():
                worker = getattr(self.ftp_manager, "_current_worker", None)
                if worker and "ConnectWorker" in type(worker).__name__:
                    # Terminate connection attempt immediately
                    worker.terminate()
                    worker.wait(50)

            # Disconnect signals
            try:
                self.ftp_manager.connection_checked.disconnect(
                    self._on_connection_checked
                )
            except (TypeError, RuntimeError):
                pass

        # 3. Clear UI state
        if hasattr(self, "_ui_state"):
            self._ui_state.clear()

        # 4. Accept close
        event.accept()
        super().closeEvent(event)
