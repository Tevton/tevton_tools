import re
import hou
import tvt_utils
from enum import Enum
from pathlib import Path
import pipeline.projects_store as projects_store
from pipeline.window_manager import get_window_manager
from PySide6 import QtCore, QtUiTools, QtWidgets

__all__ = ["FileSetup", "FileMode"]


class FileMode(Enum):
    CREATE = "create"
    EDIT = "edit"


class FileSetup(QtWidgets.QMainWindow):
    file_created = QtCore.Signal(str)

    def __init__(
        self,
        parent=None,
        project_name=None,
        shot_name=None,
        mode: FileMode = FileMode.CREATE,
        file_name=None,
    ):
        super().__init__(parent)

        self.mode = mode
        self.cur_project_name = project_name
        self.cur_shot_name = shot_name
        self.file_name_param = file_name  # existing filename in EDIT mode

        self._load_ui()
        self._find_widgets()
        self._setup_ui()
        self._setup_validators()
        self._connect_signals()
        self._form_name()
        self._configure_mode()

    # =====================================================
    # INIT
    # =====================================================

    def _load_ui(self):
        scriptpath = hou.text.expandString("$TVT/ui/FileCreator.ui")
        self.ui = QtUiTools.QUiLoader().load(scriptpath, parentWidget=self)
        self.setParent(hou.qt.mainWindow(), QtCore.Qt.Window)
        self.setAttribute(QtCore.Qt.WA_DeleteOnClose)

    def _find_widgets(self):
        self.project_name = self.ui.findChild(QtWidgets.QLabel, "lb_project_name")
        self.shot_name = self.ui.findChild(QtWidgets.QLabel, "lb_shot_name")
        self.department = self.ui.findChild(QtWidgets.QComboBox, "cb_department")
        self.file_name_label = self.ui.findChild(QtWidgets.QLabel, "lb_file_name")
        self.file_name = self.ui.findChild(QtWidgets.QLineEdit, "le_file_name")
        self.frame_range_label = self.ui.findChild(QtWidgets.QLabel, "lb_frame_range")
        self.start_frame = self.ui.findChild(QtWidgets.QSpinBox, "sb_start_frame")
        self.end_frame = self.ui.findChild(QtWidgets.QSpinBox, "sb_end_frame")
        self.create_file_btn = self.ui.findChild(
            QtWidgets.QPushButton, "pb_create_file"
        )
        self.name_preview_label = self.ui.findChild(QtWidgets.QLabel, "lb_name_preview")
        self.name_preview = self.ui.findChild(QtWidgets.QLineEdit, "le_name_preview")

    def _setup_ui(self):
        self.setMaximumSize(500, 290)
        self.file_name_label.setAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignBottom)
        self.file_name.setAlignment(QtCore.Qt.AlignCenter)
        self.frame_range_label.setAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignBottom)
        self.name_preview_label.setAlignment(
            QtCore.Qt.AlignLeft | QtCore.Qt.AlignBottom
        )
        self.project_name.setText(f"Project: {self.cur_project_name}")
        self.shot_name.setText(f"Shot: {self.cur_shot_name}")

    def _setup_validators(self):
        self.validate_name = tvt_utils.create_field_validator(
            self.file_name, "-_", True
        )
        self.validate_form = tvt_utils.create_form_validator([self.validate_name])

        self.file_name.textChanged.connect(self.validate_name)
        self.file_name.textChanged.connect(self._update_create_file_button)

    def _connect_signals(self):
        self.file_name.textChanged.connect(
            lambda: tvt_utils.force_upper(self.file_name)
        )
        self.file_name.textChanged.connect(self._form_name)
        self.department.currentTextChanged.connect(self._form_name)
        self.create_file_btn.clicked.connect(self._on_save)

    def _configure_mode(self):
        if self.mode == FileMode.CREATE:
            self.setWindowTitle(f"File Creator - {self.cur_shot_name}")
            self.create_file_btn.setText("Create File")
        else:
            self.setWindowTitle(f"Edit File - {self.file_name_param}")
            self.create_file_btn.setText("Save Changes")
            self.start_frame.setEnabled(False)
            self.end_frame.setEnabled(False)
            self._load_file_data()

    # =====================================================
    # VALIDATION
    # =====================================================

    def _update_create_file_button(self):
        if self.mode == FileMode.EDIT:
            self.create_file_btn.setEnabled(True)
            return
        self.create_file_btn.setEnabled(self.validate_form())

    # =====================================================
    # FILE NAME
    # =====================================================

    def _form_name(self):
        """Generate a standardized file name and update the preview field."""
        if self.mode == FileMode.EDIT:
            return
        shot_name = self.cur_shot_name
        department = self.department.currentText()
        file_name = self.file_name.text().strip()
        final_file_name = [shot_name, department]

        if file_name:
            final_file_name.append(file_name)

        self.name_preview.setText("_".join(final_file_name) + "_v001.hip")

    def _load_file_data(self):
        """Parse existing filename and pre-fill form fields (EDIT mode)."""
        if not self.file_name_param or not self.cur_shot_name:
            return

        # Show current filename in preview
        self.name_preview.setText(self.file_name_param)

        # Parse: {shot}_{dept}_{suffix}_vNNN.ext
        stem = Path(self.file_name_param).stem  # e.g. "PROJ_SH010_FX_EXPLOSION_v001"
        base = re.sub(r"_v\d+$", "", stem)  # "PROJ_SH010_FX_EXPLOSION"

        shot_prefix = self.cur_shot_name + "_"
        if base.startswith(shot_prefix):
            after_shot = base[len(shot_prefix) :]
        else:
            after_shot = base

        parts = after_shot.split("_", 1)
        dept = parts[0] if parts else ""
        suffix = parts[1] if len(parts) > 1 else ""

        # Set department
        dept_index = self.department.findText(dept)
        if dept_index >= 0:
            self.department.setCurrentIndex(dept_index)

        # Set suffix field
        self.file_name.setText(suffix)

        # Re-apply preview with parsed name (keeps it as original filename)
        self.name_preview.setText(self.file_name_param)

        # Connect preview update for EDIT mode (dept/suffix changes update preview)
        self.file_name.textChanged.connect(self._form_name_edit)
        self.department.currentTextChanged.connect(self._form_name_edit)

    def _form_name_edit(self):
        """Update preview in EDIT mode (without _v001 suffix — preserve version from original)."""
        shot_name = self.cur_shot_name
        department = self.department.currentText()
        suffix = self.file_name.text().strip()

        # Preserve original extension and version
        stem = Path(self.file_name_param).stem
        version_match = re.search(r"(_v\d+)$", stem)
        version = version_match.group(1) if version_match else "_v001"
        ext = Path(self.file_name_param).suffix or ".hip"

        parts = [shot_name, department]
        if suffix:
            parts.append(suffix)
        self.name_preview.setText("_".join(parts) + version + ext)

    # =====================================================
    # SAVE
    # =====================================================

    def _on_save(self):
        if self.mode == FileMode.CREATE:
            self.create_file()
        else:
            self._rename_file_on_disk()

    def create_file(self):
        """Create a new Houdini .hip file for the current shot."""
        file_name = self.name_preview.text()
        start = self.start_frame.value()
        end = self.end_frame.value()

        if start > end:
            get_window_manager().show_buttons_dialog(
                self,
                "Warning",
                "Start frame cannot be greater than end frame.",
                buttons=[("OK", True)],
                icon=QtWidgets.QMessageBox.Warning,
            )
            return

        try:
            project = projects_store.get_project(self.cur_project_name)
            cur_proj_fps = project["PROJECT_FPS"]
            cur_shot_path = Path(project["JOB"]) / "shots" / self.cur_shot_name
            cur_shot_path.mkdir(parents=True, exist_ok=True)

            file_path = cur_shot_path / file_name

            if file_path.exists():
                get_window_manager().show_buttons_dialog(
                    self,
                    "Warning",
                    f"File already exists:\n{file_name}\n\n"
                    "Please choose a different name.",
                    buttons=[("OK", True)],
                    icon=QtWidgets.QMessageBox.Warning,
                )
                return

            self.create_file_btn.setEnabled(False)
            QtWidgets.QApplication.processEvents()

            success = tvt_utils.create_empty_hip(
                str(file_path), cur_proj_fps, start, end
            )
            if not success:
                self.create_file_btn.setEnabled(True)

            self.file_created.emit(file_name)
            self.close()

        except projects_store.ProjectStoreError as e:
            get_window_manager().show_buttons_dialog(
                self,
                "Error",
                f"Failed to load project: {str(e)}",
                buttons=[("OK", True)],
                icon=QtWidgets.QMessageBox.Critical,
            )
        except Exception as e:
            get_window_manager().show_buttons_dialog(
                self,
                "Error",
                f"Failed to create file: {str(e)}",
                buttons=[("OK", True)],
                icon=QtWidgets.QMessageBox.Critical,
            )

    def _rename_file_on_disk(self):
        """Rename the existing file on disk to the new name from the preview (EDIT mode)."""
        new_name = self.name_preview.text()
        if new_name == self.file_name_param:
            self.close()
            return

        try:
            project = projects_store.get_project(self.cur_project_name)
        except projects_store.ProjectStoreError as e:
            get_window_manager().show_buttons_dialog(
                self,
                "Error",
                str(e),
                buttons=[("OK", True)],
                icon=QtWidgets.QMessageBox.Critical,
            )
            return

        shot_dir = Path(project["JOB"]) / "shots" / self.cur_shot_name
        old_path = shot_dir / self.file_name_param
        new_path = shot_dir / new_name

        if new_path.exists():
            get_window_manager().show_buttons_dialog(
                self,
                "Warning",
                f"File already exists:\n{new_name}",
                buttons=[("OK", True)],
                icon=QtWidgets.QMessageBox.Warning,
            )
            return

        try:
            old_path.rename(new_path)
            self.file_created.emit(new_name)
            self.close()
        except Exception as e:
            get_window_manager().show_buttons_dialog(
                self,
                "Error",
                f"Failed to rename file: {str(e)}",
                buttons=[("OK", True)],
                icon=QtWidgets.QMessageBox.Critical,
            )


if __name__ == "__main__":
    get_window_manager().show_window(FileSetup)
