import hou
import tvt_utils
from enum import Enum
from pathlib import Path
import pipeline.projects_store as projects_store
from pipeline.window_manager import get_window_manager
from PySide6 import QtCore, QtUiTools, QtWidgets

__all__ = ["ShotSetup", "ShotMode"]


class ShotMode(Enum):
    CREATE = "create"
    EDIT = "edit"


class ShotSetup(QtWidgets.QMainWindow):
    shot_created = QtCore.Signal(str)

    def __init__(
        self,
        parent=None,
        project_name=None,
        shot_name=None,
        mode: ShotMode = ShotMode.CREATE,
    ):
        super().__init__(parent)

        self.mode = mode
        self.project_name = project_name
        self.shot_name_param = shot_name
        self._prefix = f"{project_name}_" if project_name else ""
        self.shots_root_path = ""

        self._load_ui()
        self._find_widgets()
        self._setup_ui()
        self._setup_validators()
        self._connect_signals()
        self._load_shots_root()
        self._configure_mode()

    # =====================================================
    # INIT
    # =====================================================

    def _load_ui(self):
        scriptpath = hou.text.expandString("$TVT/ui/ShotCreator.ui")
        self.ui = QtUiTools.QUiLoader().load(scriptpath, parentWidget=self)
        self.setParent(hou.qt.mainWindow(), QtCore.Qt.Window)
        self.setAttribute(QtCore.Qt.WA_DeleteOnClose)

    def _find_widgets(self):
        self.shot_name = self.ui.findChild(QtWidgets.QLineEdit, "le_shot_name")
        self.shot_path = self.ui.findChild(QtWidgets.QLabel, "lb_shot_path")
        self.shot_folders = self.ui.findChild(QtWidgets.QPlainTextEdit, "qpt_folders")
        self.create_shot = self.ui.findChild(QtWidgets.QPushButton, "pb_create_shot")
        self.cancel = self.ui.findChild(QtWidgets.QPushButton, "pb_cancel")

    def _setup_ui(self):
        self.setMaximumSize(500, 290)
        self.shot_name.setAlignment(QtCore.Qt.AlignCenter)
        self.create_shot.setEnabled(False)

    def _setup_validators(self):
        self.validate_name = tvt_utils.create_field_validator(
            self.shot_name, "-_", False
        )
        self.validate_folders = tvt_utils.create_field_validator(
            self.shot_folders, "-_", True
        )
        self.validate_form = tvt_utils.create_form_validator(
            [self.validate_name, self.validate_folders]
        )

        self.shot_name.textChanged.connect(self.validate_name)
        self.shot_folders.textChanged.connect(self.validate_folders)
        self.shot_name.textChanged.connect(self._update_save_button)
        self.shot_folders.textChanged.connect(self._update_save_button)

    def _connect_signals(self):
        self.shot_name.textChanged.connect(
            lambda: tvt_utils.force_upper(self.shot_name)
        )
        self.shot_name.textChanged.connect(self._enforce_prefix)
        self.shot_name.textChanged.connect(self._update_shot_path)
        self.create_shot.clicked.connect(self._on_save)
        self.cancel.clicked.connect(self.close)

    def _configure_mode(self):
        if self.mode == ShotMode.CREATE:
            self.setWindowTitle(f"Shot Creator - {self.project_name}")
            self.create_shot.setText("Create Shot")
            self.create_shot.setEnabled(False)
            self.shot_name.setText(self._prefix)
        else:
            self.setWindowTitle(f"Edit Shot - {self.shot_name_param}")
            self.create_shot.setText("Save Changes")
            self.create_shot.setEnabled(True)
            self.shot_name.setText(self.shot_name_param or "")
            self._load_shot_folders()

    # =====================================================
    # VALIDATION
    # =====================================================

    def _update_save_button(self):
        has_suffix = len(self.shot_name.text()) > len(self._prefix)
        self.create_shot.setEnabled(self.validate_form() and has_suffix)

    # =====================================================
    # PREFIX
    # =====================================================

    def _enforce_prefix(self):
        """Ensure shot name always starts with the mandatory project prefix."""
        text = self.shot_name.text()
        if not text.startswith(self._prefix):
            self.shot_name.blockSignals(True)
            self.shot_name.setText(self._prefix)
            self.shot_name.blockSignals(False)
            self.shot_name.setCursorPosition(len(self._prefix))

    # =====================================================
    # SHOT PATH
    # =====================================================

    def _load_shots_root(self):
        """Load shots root path from projects store."""
        try:
            project = projects_store.get_project(self.project_name)
            self.shots_root_path = (Path(project["JOB"]) / "shots").as_posix()
        except projects_store.ProjectStoreError:
            self.shots_root_path = ""
            self.shot_path.setText("Project not found")
            return
        self._update_shot_path()

    def _update_shot_path(self):
        """Update path label live as the shot name changes."""
        shot_name = self.shot_name.text().strip()
        if self.shots_root_path and len(shot_name) > len(self._prefix):
            self.shot_path.setText(f"{self.shots_root_path}/{shot_name}")
        else:
            self.shot_path.setText(self.shots_root_path or "Project not found")

    def _load_shot_folders(self):
        """Pre-fill folders field from existing shot directory (EDIT mode)."""
        if not self.shots_root_path or not self.shot_name_param:
            return
        shot_dir = Path(self.shots_root_path) / self.shot_name_param
        if not shot_dir.exists():
            return
        folders = sorted(p.name for p in shot_dir.iterdir() if p.is_dir())
        self.shot_folders.setPlainText(", ".join(folders))

    # =====================================================
    # SAVE
    # =====================================================

    def _on_save(self):
        if self.mode == ShotMode.CREATE:
            self._create_shot()
        else:
            self._update_shot()

    def _create_shot(self):
        """Create shot folder and subfolders."""
        shot_name = self.shot_name.text().strip()

        sub_folders = set(
            f.strip() for f in self.shot_folders.toPlainText().split(",") if f.strip()
        )
        for f in ["render", "cache", "source"]:
            sub_folders.add(f)

        if not self.shots_root_path:
            get_window_manager().show_buttons_dialog(
                self,
                "Error",
                f"Can't find shots folder in '{self.project_name}' project.\n"
                f"Please create a 'shots' folder for correct work.",
                buttons=[("OK", True)],
                icon=QtWidgets.QMessageBox.Critical,
            )
            return

        shot_full_path = Path(self.shots_root_path) / shot_name

        if shot_full_path.exists():
            get_window_manager().show_buttons_dialog(
                self,
                "Warning",
                f"Shot '{shot_name}' already exists at:\n{shot_full_path.as_posix()}",
                buttons=[("OK", True)],
                icon=QtWidgets.QMessageBox.Warning,
            )
            return

        try:
            shot_full_path.mkdir(parents=True, exist_ok=True)
            for folder in sub_folders:
                (shot_full_path / folder).mkdir(exist_ok=True)

            get_window_manager().show_buttons_dialog(
                self,
                "Success",
                f"Shot '{shot_name}' created successfully.\n"
                f"Path: {shot_full_path.as_posix()}\n\n"
                f"Please use 'render' for output renders and 'cache' for cache data!",
                buttons=[("OK", True)],
                icon=QtWidgets.QMessageBox.Information,
            )
            self.shot_created.emit(shot_name)
            self.close()

        except Exception as e:
            get_window_manager().show_buttons_dialog(
                self,
                "Error",
                f"Failed to create shot folders:\n{str(e)}",
                buttons=[("OK", True)],
                icon=QtWidgets.QMessageBox.Critical,
            )

    def _update_shot(self):
        """Rename shot directory (if name changed) and create any missing subfolders."""
        if not self.shots_root_path or not self.shot_name_param:
            return

        new_name = self.shot_name.text().strip()
        old_name = self.shot_name_param

        if new_name != old_name:
            confirm = get_window_manager().show_buttons_dialog(
                self,
                "Rename Shot",
                f"Are you sure you want to rename '{old_name}'?",
                buttons=[("Rename", True), ("Cancel", False)],
                icon=QtWidgets.QMessageBox.Warning,
            )
            if not confirm:
                return

            old_path = Path(self.shots_root_path) / old_name
            new_path = Path(self.shots_root_path) / new_name

            if new_path.exists():
                get_window_manager().show_buttons_dialog(
                    self,
                    "Warning",
                    f"Shot '{new_name}' already exists.",
                    buttons=[("OK", True)],
                    icon=QtWidgets.QMessageBox.Warning,
                )
                return

            try:
                old_path.rename(new_path)
            except Exception as e:
                get_window_manager().show_buttons_dialog(
                    self,
                    "Error",
                    f"Failed to rename shot:\n{str(e)}",
                    buttons=[("OK", True)],
                    icon=QtWidgets.QMessageBox.Critical,
                )
                return

            get_window_manager().show_buttons_dialog(
                self,
                "Success",
                f"Shot successfully renamed to '{new_name}'\n"
                f"Please check file names to match '{new_name}'!",
                buttons=[("OK", True)],
                icon=QtWidgets.QMessageBox.Information,
            )
            self.shot_name_param = new_name

        shot_full_path = Path(self.shots_root_path) / self.shot_name_param
        sub_folders = set(
            f.strip() for f in self.shot_folders.toPlainText().split(",") if f.strip()
        )

        try:
            created = []
            for folder in sub_folders:
                folder_path = shot_full_path / folder
                if not folder_path.exists():
                    folder_path.mkdir(parents=True, exist_ok=True)
                    created.append(folder)

            if created:
                get_window_manager().show_buttons_dialog(
                    self,
                    "Success",
                    f"Created {len(created)} new folder(s):\n" + "\n".join(created),
                    buttons=[("OK", True)],
                    icon=QtWidgets.QMessageBox.Information,
                )

            self.shot_created.emit(self.shot_name_param)
            self.close()

        except Exception as e:
            get_window_manager().show_buttons_dialog(
                self,
                "Error",
                f"Failed to update shot folders:\n{str(e)}",
                buttons=[("OK", True)],
                icon=QtWidgets.QMessageBox.Critical,
            )


if __name__ == "__main__":
    get_window_manager().show_window(ShotSetup)
