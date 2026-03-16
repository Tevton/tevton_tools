import hou
import shutil
import tvt_utils
from pathlib import Path
from PySide6 import QtCore, QtGui, QtUiTools, QtWidgets
import pipeline.projects_store as projects_store
from pipeline.project_setup import ProjectSetup, ProjectMode
from pipeline.shot_setup import ShotSetup, ShotMode
from pipeline.file_setup import FileSetup, FileMode
from pipeline.shot_ftp_manager import ShotFTPManager
from pipeline.window_manager import get_window_manager
from config.config import HIP_EXTENSIONS


class ProjectManager(QtWidgets.QMainWindow):

    def __init__(self, parent=None):
        super().__init__(parent)

        self._wm = get_window_manager()

        self._load_ui()
        self._find_widgets()
        self._setup_ui()
        self._connect_signals()
        self.load_projects()
        self._load_initial_shots()

    # =====================================================
    # PROPERTIES
    # =====================================================

    @property
    def current_project(self) -> QtWidgets.QListWidgetItem | None:
        """Get the currently selected project item."""
        item = self.project_list.currentItem()
        return item if item and item.isSelected() else None

    @property
    def current_shot(self) -> QtWidgets.QListWidgetItem | None:
        """Get the currently selected shot item."""
        item = self.shot_list.currentItem()
        return item if item and item.isSelected() else None

    @property
    def current_file(self) -> QtWidgets.QListWidgetItem | None:
        """Get the currently selected file item."""
        item = self.file_list.currentItem()
        return item if item and item.isSelected() else None

    @property
    def current_project_name(self) -> str | None:
        """Return the name of the currently selected project."""
        return self.current_project.text() if self.current_project else None

    @property
    def current_shot_name(self) -> str | None:
        """Return the name of the currently selected shot."""
        return self.current_shot.text() if self.current_shot else None

    @property
    def current_file_name(self) -> str | None:
        """Return the name of the currently selected file."""
        return self.current_file.text() if self.current_file else None

    @property
    def has_project_selected(self) -> bool:
        return self.current_project is not None

    @property
    def has_shot_selected(self) -> bool:
        return self.current_shot is not None

    @property
    def has_file_selected(self) -> bool:
        return self.current_file is not None

    @property
    def can_create_shot(self) -> bool:
        return self.has_project_selected

    @property
    def can_create_file(self) -> bool:
        return self.has_project_selected and self.has_shot_selected

    # =====================================================
    # INIT
    # =====================================================

    def _load_ui(self):
        scriptpath = hou.text.expandString("$TVT/ui/ProjectManager.ui")
        self.ui = QtUiTools.QUiLoader().load(scriptpath, parentWidget=self)
        self.setParent(hou.qt.mainWindow(), QtCore.Qt.Window)
        self.setAttribute(QtCore.Qt.WA_DeleteOnClose)

    def _find_widgets(self):
        # Projects
        self.projects_text = self.ui.findChild(QtWidgets.QLabel, "lb_projects")
        self.project_list = self.ui.findChild(QtWidgets.QListWidget, "lw_project_list")
        self.create_project_button = self.ui.findChild(
            QtWidgets.QPushButton, "pb_create_project"
        )
        self.delete_project_button = self.ui.findChild(
            QtWidgets.QPushButton, "pb_delete_project"
        )
        self.project_settings_button = self.ui.findChild(
            QtWidgets.QPushButton, "pb_project_settings"
        )
        self.open_project_folder_button = self.ui.findChild(
            QtWidgets.QPushButton, "pb_open_project_folder"
        )
        # Shots
        self.shots_text = self.ui.findChild(QtWidgets.QLabel, "lb_shots")
        self.shot_list = self.ui.findChild(QtWidgets.QListWidget, "lw_shot_list")
        self.create_shot_button = self.ui.findChild(
            QtWidgets.QPushButton, "pb_create_shot"
        )
        self.edit_shot_btn = self.ui.findChild(QtWidgets.QPushButton, "pb_edit_shot")
        self.delete_shot_button = self.ui.findChild(
            QtWidgets.QPushButton, "pb_delete_shot"
        )
        self.ftp_manager_button = self.ui.findChild(
            QtWidgets.QPushButton, "pb_ftp_manager"
        )
        self.open_shot_folder_button = self.ui.findChild(
            QtWidgets.QPushButton, "pb_open_shot_folder"
        )
        # Files
        self.files_text = self.ui.findChild(QtWidgets.QLabel, "lb_files")
        self.file_list = self.ui.findChild(QtWidgets.QListWidget, "lw_file_list")
        self.new_file_button = self.ui.findChild(QtWidgets.QPushButton, "pb_new_file")
        self.rename_file_btn = self.ui.findChild(
            QtWidgets.QPushButton, "pb_rename_file"
        )
        self.delete_file_button = self.ui.findChild(
            QtWidgets.QPushButton, "pb_delete_file"
        )
        self.open_file_button = self.ui.findChild(QtWidgets.QPushButton, "pb_open_file")
        self.filter_nk_check = self.ui.findChild(QtWidgets.QCheckBox, "cb_filter_nk")
        self.show_all_check = self.ui.findChild(QtWidgets.QCheckBox, "cb_show_all")

    def _setup_ui(self):
        self.setWindowTitle("Project Manager")
        self.project_list.setSelectionMode(QtWidgets.QAbstractItemView.NoSelection)
        self.shot_list.setSelectionMode(QtWidgets.QAbstractItemView.NoSelection)
        self.file_list.setSelectionMode(QtWidgets.QAbstractItemView.NoSelection)
        #!!! .nk filter right now not working
        self.filter_nk_check.setEnabled(False)

        self.projects_text.setAlignment(QtCore.Qt.AlignCenter)
        self.shots_text.setAlignment(QtCore.Qt.AlignCenter)
        self.files_text.setAlignment(QtCore.Qt.AlignCenter)

        self.show_all_check.setEnabled(False)
        self._update_button_states()

        self._f2_shortcut = QtGui.QShortcut(QtGui.QKeySequence("F2"), self.ui)
        self._f2_shortcut.activated.connect(self._edit_selected)
        self._del_shortcut = QtGui.QShortcut(QtGui.QKeySequence("Delete"), self.ui)
        self._del_shortcut.activated.connect(self._delete_focused)

    def _connect_signals(self):
        # Lists
        self.project_list.clicked.connect(self._on_item_clicked)
        self.shot_list.clicked.connect(self._on_item_clicked)
        self.file_list.clicked.connect(self._on_item_clicked)

        # Projects
        self.create_project_button.clicked.connect(self.create_new_project)
        self.delete_project_button.clicked.connect(
            lambda: self.delete_object_by_type("project")
        )
        self.project_settings_button.clicked.connect(self.modify_project)
        self.open_project_folder_button.clicked.connect(
            lambda: self.reveal_folder_by_type("project")
        )
        self.project_list.itemDoubleClicked.connect(
            lambda: self.reveal_folder_by_type("project")
        )

        # Shots
        self.create_shot_button.clicked.connect(self.create_new_shot)
        if self.edit_shot_btn:
            self.edit_shot_btn.clicked.connect(self._edit_shot)
        self.delete_shot_button.clicked.connect(
            lambda: self.delete_object_by_type("shot")
        )
        self.ftp_manager_button.clicked.connect(self.manage_shot)
        self.open_shot_folder_button.clicked.connect(
            lambda: self.reveal_folder_by_type("shot")
        )
        self.shot_list.itemDoubleClicked.connect(
            lambda: self.reveal_folder_by_type("shot")
        )

        # Files
        self.new_file_button.clicked.connect(self.create_new_file)
        if self.rename_file_btn:
            self.rename_file_btn.clicked.connect(self._edit_file)
        self.delete_file_button.clicked.connect(
            lambda: self.delete_object_by_type("file")
        )
        self.open_file_button.clicked.connect(self.open_selected)
        self.file_list.itemDoubleClicked.connect(self.open_selected)
        self.filter_nk_check.stateChanged.connect(self.load_file_list)
        self.show_all_check.stateChanged.connect(self.load_file_list)

        self._setup_context_menus()

    def _setup_context_menus(self):
        self.project_list.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.project_list.customContextMenuRequested.connect(self._show_project_menu)
        self.shot_list.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.shot_list.customContextMenuRequested.connect(self._show_shot_menu)
        self.file_list.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.file_list.customContextMenuRequested.connect(self._show_file_menu)

    def _show_project_menu(self, pos):
        item = self.project_list.itemAt(pos)
        if item:
            self.project_list.setCurrentItem(item)
            self.project_list.selectionModel().select(
                self.project_list.indexFromItem(item),
                QtCore.QItemSelectionModel.ClearAndSelect
                | QtCore.QItemSelectionModel.Rows,
            )
            self.load_shot_list()
            self.file_list.clear()
            self._update_button_states()
        menu = QtWidgets.QMenu(self.project_list)
        menu.setFont(self._wm.menu_font())
        menu.addAction("Create Project").triggered.connect(self.create_new_project)
        if item:
            menu.addSeparator()
            menu.addAction("Edit / Settings").triggered.connect(self.modify_project)
            menu.addAction("Reveal in Explorer").triggered.connect(
                lambda: self.reveal_folder_by_type("project")
            )
            menu.addAction("Delete").triggered.connect(
                lambda: self.delete_object_by_type("project")
            )
        menu.exec(self.project_list.viewport().mapToGlobal(pos))

    def _show_shot_menu(self, pos):
        item = self.shot_list.itemAt(pos)
        if item:
            self.shot_list.setCurrentItem(item)
            self.shot_list.selectionModel().select(
                self.shot_list.indexFromItem(item),
                QtCore.QItemSelectionModel.ClearAndSelect
                | QtCore.QItemSelectionModel.Rows,
            )
            self.load_file_list()
            self._update_button_states()
        menu = QtWidgets.QMenu(self.shot_list)
        menu.setFont(self._wm.menu_font())
        menu.addAction("Create Shot").triggered.connect(self.create_new_shot)
        if item:
            menu.addSeparator()
            menu.addAction("Edit Shot").triggered.connect(self._edit_shot)
            menu.addAction("Reveal in Explorer").triggered.connect(
                lambda: self.reveal_folder_by_type("shot")
            )
            menu.addAction("FTP Manager").triggered.connect(self.manage_shot)
            menu.addAction("Delete").triggered.connect(
                lambda: self.delete_object_by_type("shot")
            )
        menu.exec(self.shot_list.viewport().mapToGlobal(pos))

    def _show_file_menu(self, pos):
        item = self.file_list.itemAt(pos)
        if item:
            self.file_list.setCurrentItem(item)
            self.file_list.selectionModel().select(
                self.file_list.indexFromItem(item),
                QtCore.QItemSelectionModel.ClearAndSelect
                | QtCore.QItemSelectionModel.Rows,
            )
            self._update_button_states()
        menu = QtWidgets.QMenu(self.file_list)
        menu.setFont(self._wm.menu_font())
        menu.addAction("Create File").triggered.connect(self.create_new_file)
        if item:
            menu.addSeparator()
            menu.addAction("Open").triggered.connect(self.open_selected)
            menu.addAction("Rename").triggered.connect(self._edit_file)
            menu.addAction("Reveal in Explorer").triggered.connect(
                lambda: self.reveal_folder_by_type("shot")
            )
            menu.addAction("Delete").triggered.connect(
                lambda: self.delete_object_by_type("file")
            )
        menu.exec(self.file_list.viewport().mapToGlobal(pos))

    def _edit_selected(self):
        if self.file_list.hasFocus() and self.has_file_selected:
            self._edit_file()
        elif self.shot_list.hasFocus() and self.has_shot_selected:
            self._edit_shot()
        elif self.project_list.hasFocus() and self.has_project_selected:
            self.modify_project()

    def _delete_focused(self):
        if self.file_list.hasFocus() and self.has_file_selected:
            self.delete_object_by_type("file")
        elif self.shot_list.hasFocus() and self.has_shot_selected:
            self.delete_object_by_type("shot")
        elif self.project_list.hasFocus() and self.has_project_selected:
            self.delete_object_by_type("project")

    def _edit_shot(self):
        if not self.has_shot_selected:
            return
        self._wm.show_window(
            ShotSetup,
            parent=self,
            mode=ShotMode.EDIT,
            project_name=self.current_project_name,
            shot_name=self.current_shot_name,
            callback_signal={"signal": "shot_created", "slot": self._on_shot_created},
        )

    def _edit_file(self):
        if not self.has_file_selected:
            return
        self._wm.show_window(
            FileSetup,
            parent=self,
            mode=FileMode.EDIT,
            project_name=self.current_project_name,
            shot_name=self.current_shot_name,
            file_name=self.current_file_name,
            callback_signal={"signal": "file_created", "slot": self._on_file_created},
        )

    def _update_button_states(self):
        """Enable/disable buttons based on current selection."""
        # Project buttons
        self.delete_project_button.setEnabled(self.has_project_selected)
        self.project_settings_button.setEnabled(self.has_project_selected)
        self.open_project_folder_button.setEnabled(self.has_project_selected)

        # Shot buttons
        self.create_shot_button.setEnabled(self.can_create_shot)
        if self.edit_shot_btn:
            self.edit_shot_btn.setEnabled(self.has_shot_selected)
        self.delete_shot_button.setEnabled(self.has_shot_selected)
        self.ftp_manager_button.setEnabled(self.has_shot_selected)
        self.open_shot_folder_button.setEnabled(self.has_shot_selected)

        # File buttons
        self.new_file_button.setEnabled(self.can_create_file)
        if self.rename_file_btn:
            self.rename_file_btn.setEnabled(self.has_file_selected)
        self.delete_file_button.setEnabled(self.has_file_selected)
        self.open_file_button.setEnabled(self.has_file_selected)
        self.show_all_check.setEnabled(self.has_shot_selected)

    # =====================================================
    # LOAD DATA
    # =====================================================

    def load_projects(self):
        try:
            project_names = projects_store.list_projects()
        except projects_store.ProjectStoreError as e:
            self._wm.show_buttons_dialog(
                self,
                "Error",
                str(e),
                buttons=[("OK", True)],
                icon=QtWidgets.QMessageBox.Critical,
            )
            return

        self.project_list.clear()
        for project in sorted(project_names):
            self.project_list.addItem(project)

        active_project = hou.getenv("PROJECT_NAME")
        if active_project:
            items = self.project_list.findItems(active_project, QtCore.Qt.MatchExactly)
            if items:
                items[0].setIcon(
                    self.style().standardIcon(QtWidgets.QStyle.SP_CommandLink)
                )

    def load_shot_list(self):
        """
        Load shot list for selected project and check if project exist
        """
        self.shot_list.clear()
        if not self.has_project_selected:
            return

        try:
            project = projects_store.get_project(self.current_project_name)
        except projects_store.ProjectStoreError:
            return

        cur_project_path = Path(project["JOB"])

        if not cur_project_path.exists():
            confirm = self._wm.show_buttons_dialog(
                self,
                "Missing Project",
                f"Project '{self.current_project_name}' not found on disk!\n"
                f"Path: {cur_project_path}\n\n"
                f"Do you want to remove it from the database?",
                buttons=[("Yes", True), ("No", False)],
                icon=QtWidgets.QMessageBox.Warning,
            )
            if confirm:
                try:
                    projects_store.remove_project(self.current_project_name)
                    self.load_projects()
                    self.shot_list.clear()
                    self.file_list.clear()
                except projects_store.ProjectStoreError as e:
                    self._wm.show_buttons_dialog(
                        self,
                        "Error",
                        str(e),
                        buttons=[("OK", True)],
                        icon=QtWidgets.QMessageBox.Critical,
                    )
                return

        shots_path = cur_project_path / "shots"

        if not shots_path.exists():
            self._wm.show_buttons_dialog(
                self,
                "Error",
                f'Missing folder "shots" in {cur_project_path}\n'
                f'Please create a "shots" folder for the project manager to work correctly.',
                buttons=[("OK", True)],
                icon=QtWidgets.QMessageBox.Critical,
            )
            return

        shots_folders = []
        for p in shots_path.iterdir():
            if p.is_dir():
                try:
                    mtime = p.stat().st_mtime
                    shots_folders.append((mtime, p.name))
                except OSError:
                    continue

        # Sort by mtime
        shots_folders.sort(reverse=True)
        for _, folder_name in shots_folders:
            self.shot_list.addItem(folder_name)

        self._update_button_states()

    def load_file_list(self):
        self.file_list.clear()

        if not self.has_project_selected or not self.has_shot_selected:
            return

        try:
            project = projects_store.get_project(self.current_project_name)
        except projects_store.ProjectStoreError:
            return

        shot_path = Path(project["JOB"]) / "shots" / self.current_shot_name
        if not shot_path.exists():
            return

        show_all = self.show_all_check.isChecked()
        filter_nk = self.filter_nk_check.isChecked()

        for f in sorted(shot_path.iterdir(), key=lambda p: p.name, reverse=True):
            if not f.is_file():
                continue
            if show_all:
                self.file_list.addItem(f.name)
            elif any(f.suffix == ext for ext in HIP_EXTENSIONS):
                self.file_list.addItem(f.name)
            elif not filter_nk and f.suffix == ".nk":
                self.file_list.addItem(f.name)

        self._update_button_states()

    # =====================================================
    # PROJECTS
    # =====================================================

    def create_new_project(self):
        """
        Open Project Creator window using universal utility.
        Pass a callback to refresh list when project is created.
        """
        self._wm.show_window(
            ProjectSetup,
            parent=self,
            mode=ProjectMode.CREATE,
            callback_signal={
                "signal": "project_saved",
                "slot": self._on_project_created,
            },
        )

    def modify_project(self):
        """
        Open Project Settings window for the selected project.
        """
        if not self.has_project_selected:
            self._wm.show_buttons_dialog(
                self,
                "Warning",
                "Please select a project to edit!",
                buttons=[("OK", True)],
                icon=QtWidgets.QMessageBox.Warning,
            )
            return

        self._wm.show_window(
            ProjectSetup,
            parent=self,
            mode=ProjectMode.EDIT,
            project_name=self.current_project_name,
            callback_signal={
                "signal": "project_saved",
                "slot": self._on_project_created,
            },
        )

    def _on_project_created(self, project_name):
        """
        Slot: Called when Project Creator emits 'project_created'.
        """
        self.load_projects()

        items = self.project_list.findItems(project_name, QtCore.Qt.MatchExactly)
        if not items:
            return
        item = items[0]
        self.project_list.setCurrentItem(item)
        self.project_list.selectionModel().select(
            self.project_list.indexFromItem(item),
            QtCore.QItemSelectionModel.ClearAndSelect | QtCore.QItemSelectionModel.Rows,
        )
        self.load_shot_list()
        self.file_list.clear()
        self._update_button_states()

    # =====================================================
    # SHOTS
    # =====================================================

    def create_new_shot(self):
        """
        Open Shot Creator window using universal utility.
        Pass a callback to refresh list when shot is created.
        """
        if not self.can_create_shot:
            return

        self._wm.show_window(
            ShotSetup,
            parent=self,
            callback_signal={
                "signal": "shot_created",
                "slot": self._on_shot_created,
            },
            project_name=self.current_project_name,
        )

    def manage_shot(self):
        if not self.has_shot_selected:
            return

        try:
            project = projects_store.get_project(self.current_project_name)
        except projects_store.ProjectStoreError:
            return

        if not project.get("PROJECT_FTP_HOST", "").strip():
            answer = self._wm.show_buttons_dialog(
                self,
                "No FTP Settings",
                f"Project '{self.current_project_name}' has no FTP settings configured.\n\n"
                "Would you like to set up FTP now?",
                buttons=[("Set Up FTP", True), ("Cancel", False)],
                icon=QtWidgets.QMessageBox.Warning,
            )
            if answer:
                self._wm.show_window(
                    ProjectSetup,
                    parent=self,
                    mode=ProjectMode.EDIT,
                    project_name=self.current_project_name,
                    callback_signal={
                        "signal": "project_saved",
                        "slot": self._on_project_created,
                    },
                )
            return

        cur_shot_path = Path(project["JOB"]) / "shots" / self.current_shot_name

        self._wm.show_window(
            ShotFTPManager,
            parent=self,
            callback_signal={
                "signal": "shot_created",
                "slot": self._on_shot_created,
            },
            project_name=self.current_project_name,
            shot_name=self.current_shot_name,
            shot_path=str(cur_shot_path),
        )

    def _on_shot_created(self, shot_name: str = ""):
        """
        Slot: Called when Shot Creator emits 'shot_created'.
        """
        self.load_shot_list()
        self.load_file_list()
        self._update_button_states()

    # =====================================================
    # FILES
    # =====================================================

    def create_new_file(self):
        """
        Open File Creator window using universal utility.
        Pass a callback to refresh list when file is created.
        """
        self._wm.show_window(
            FileSetup,
            parent=self,
            callback_signal={
                "signal": "file_created",
                "slot": self._on_file_created,
            },
            project_name=self.current_project_name,
            shot_name=self.current_shot_name,
        )

    def open_selected(self):
        if not self.has_file_selected:
            return

        try:
            project = projects_store.get_project(self.current_project_name)
        except projects_store.ProjectStoreError:
            return

        file_path = (
            Path(project["JOB"])
            / "shots"
            / self.current_shot_name
            / self.current_file_name
        )

        if file_path.is_dir():
            tvt_utils.open_file_or_folder(str(file_path))
        else:
            tvt_utils.open_file_or_folder(str(file_path), file_path.suffix)

    def _on_file_created(self, file_name: str = ""):
        """
        Slot: Called when File Creator emits 'file_created'.
        """
        self.load_file_list()
        self._update_button_states()

    # =====================================================
    # DELETE
    # =====================================================

    def delete_object_by_type(self, object_type):
        """
        Deletes project, shot, or file and its contents.

        Args:
            object_type: Type of object to delete ("project", "shot", or "file")
        """
        if not self.has_project_selected:
            return

        project_name = self.current_project_name
        try:
            project = projects_store.get_project(project_name)
        except projects_store.ProjectStoreError as e:
            self._wm.show_buttons_dialog(
                self,
                "Error",
                str(e),
                buttons=[("OK", True)],
                icon=QtWidgets.QMessageBox.Critical,
            )
            return

        # Build path
        project_root = Path(project["JOB"])
        target_path = project_root
        name = project_name

        if object_type == "shot":
            if not self.has_shot_selected:
                self._wm.show_buttons_dialog(
                    self,
                    "Warning",
                    "Please select a shot to delete!",
                    buttons=[("OK", True)],
                    icon=QtWidgets.QMessageBox.Warning,
                )
                return
            name = self.current_shot_name
            target_path = project_root / "shots" / name

        elif object_type == "file":
            if not self.has_file_selected:
                self._wm.show_buttons_dialog(
                    self,
                    "Warning",
                    "Please select a file to delete!",
                    buttons=[("OK", True)],
                    icon=QtWidgets.QMessageBox.Warning,
                )
                return
            name = self.current_file_name
            target_path = project_root / "shots" / self.current_shot_name / name

        if not self._confirm_deletion(object_type, name, target_path):
            return

        # Perform deletion
        success = self._perform_deletion(object_type, name, target_path, project_name)

        if success:
            self._update_ui_after_deletion(object_type)
            self._show_success_message(object_type, name)

    def _confirm_deletion(self, object_type: str, name: str, path: Path) -> bool:
        """Show confirmation dialog. Returns True if user confirms."""
        messages = {
            "project": f"Are you sure you want to delete project: {name}?\n\n"
            f"All content inside: '{path}' will be permanently deleted!\n"
            f"This includes ALL shots, renders, caches, and files!",
            "shot": f"Are you sure you want to delete shot: {name}?\n\n"
            f"All content inside: '{path}' will be permanently deleted!\n"
            f"This includes all renders, caches, and files for this shot!",
            "file": f"Are you sure you want to delete file: {name}?",
        }
        message = messages.get(object_type)
        if message is None:
            return False

        result = self._wm.show_buttons_dialog(
            self,
            "WARNING: Permanent Deletion",
            message,
            buttons=[("Confirm", True), ("Cancel", False)],
            icon=QtWidgets.QMessageBox.Warning,
        )
        return result

    def _perform_deletion(
        self, object_type: str, name: str, target_path: Path, project_name: str
    ) -> bool:
        """Perform the actual deletion. Returns True if successful."""
        try:
            # Step 1: Remove from database FIRST (for projects)
            if object_type == "project":
                projects_store.remove_project(project_name)

            # Step 2: Delete from filesystem
            if target_path.exists():
                if object_type == "file":
                    target_path.unlink()
                else:
                    shutil.rmtree(target_path)
            else:
                if object_type != "file":
                    self._wm.show_buttons_dialog(
                        self,
                        "Warning",
                        f"Path not found:\n{target_path}\n\nRemoving from database only.",
                        buttons=[("OK", True)],
                        icon=QtWidgets.QMessageBox.Warning,
                    )

            return True

        except FileNotFoundError:
            return True
        except PermissionError as e:
            self._wm.show_buttons_dialog(
                self,
                "Error",
                f"Permission denied: {str(e)}\n\nCannot delete: {target_path}",
                buttons=[("OK", True)],
                icon=QtWidgets.QMessageBox.Critical,
            )
        except Exception as e:
            self._wm.show_buttons_dialog(
                self,
                "Error",
                f"An error occurred while deleting {object_type}:\n{str(e)}\n\nPath: {target_path}",
                buttons=[("OK", True)],
                icon=QtWidgets.QMessageBox.Critical,
            )
        return False

    def _update_ui_after_deletion(self, object_type: str):
        if object_type == "project":
            self.load_projects()
            self.shot_list.clear()
            self.file_list.clear()
        elif object_type == "shot":
            self.load_shot_list()
            self.file_list.clear()
        elif object_type == "file":
            self.load_file_list()

        self._update_button_states()

    def _show_success_message(self, object_type: str, name: str):
        """Show success message."""
        messages = {
            "project": f"✅ Project '{name}' has been permanently deleted\n"
            f"All project folders and files have been removed.",
            "shot": f"✅ Shot '{name}' has been permanently deleted\n"
            f"All shot folders and files have been removed.",
            "file": f"✅ File '{name}' has been deleted",
        }

        message = messages.get(object_type)
        if message is None:
            return
        self._wm.show_buttons_dialog(
            self,
            "Success",
            message,
            buttons=[("OK", True)],
            icon=QtWidgets.QMessageBox.Information,
        )

    # =====================================================
    # NAVIGATION
    # =====================================================

    def reveal_folder_by_type(self, folder_type):
        """
        Opens project or shot folder in system file explorer.
        """
        if not self.has_project_selected:
            return

        try:
            project = projects_store.get_project(self.current_project_name)
        except projects_store.ProjectStoreError:
            return

        path = Path(project["JOB"])

        if folder_type == "shot":
            if not self.has_shot_selected:
                return
            path = path / "shots" / self.current_shot_name

        tvt_utils.open_file_or_folder(str(path))

    def _on_item_clicked(self, index):
        """
        Universal click handler for project/shot/file lists.
        """
        if not index.isValid():
            return

        sender = self.sender()
        if not sender:
            return

        sender.selectionModel().select(
            index,
            QtCore.QItemSelectionModel.ClearAndSelect | QtCore.QItemSelectionModel.Rows,
        )

        if sender == self.project_list:
            self.load_shot_list()
            self.file_list.clear()
        elif sender == self.shot_list:
            self.load_file_list()

        self._update_button_states()

    def _load_initial_shots(self):
        """
        Load shots for active project when open project manager
        """
        active_project = hou.getenv("PROJECT_NAME")
        if not active_project:
            return

        items = self.project_list.findItems(active_project, QtCore.Qt.MatchExactly)
        if not items:
            return

        item = items[0]
        self.project_list.setCurrentItem(item)
        self.project_list.selectionModel().select(
            self.project_list.indexFromItem(item),
            QtCore.QItemSelectionModel.ClearAndSelect | QtCore.QItemSelectionModel.Rows,
        )
        self.load_shot_list()
        self.file_list.clear()
        self._update_button_states()


if __name__ == "__main__":
    get_window_manager().show_window(ProjectManager)
