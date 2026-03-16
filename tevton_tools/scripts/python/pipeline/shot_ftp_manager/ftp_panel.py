import hou
from pathlib import Path
from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtWidgets import QFileIconProvider
from PySide6.QtCore import QFileInfo
from config.config import FTP_SHOT_PATH, USER_DATA_PATH
from pipeline.window_manager import WindowManager

_ICON_CACHE_DIR = Path(USER_DATA_PATH) / "cache" / "ftp_icons"


class FTPPanel:
    """
    Manages the FTP file tree: listing, navigation, selection, and folder creation.

    No safety checks needed! WindowManager ensures signals only fire when window is alive.
    All methods assume window and widgets exist.
    """

    def __init__(self, window):
        self._win = window
        self._wm: WindowManager = window._wm
        self._renaming_item = None
        self._rename_old_path = None
        self._sort_column = 0
        self._sort_ascending = True
        _ICON_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        self._icon_provider = QFileIconProvider()
        self._icon_memory: dict = {}
        self._icon_dir = self._load_or_fetch_icon(
            "__dir__", lambda: self._icon_provider.icon(QFileIconProvider.Folder)
        )
        self._icon_file = self._load_or_fetch_icon(
            "__file__", lambda: self._icon_provider.icon(QFileIconProvider.File)
        )
        self._setup_header_sort()

    # ------------------------------------------------------------------
    # Path properties
    # ------------------------------------------------------------------

    @property
    def _path(self) -> str:
        """Get current FTP path from window."""
        return self._win.current_ftp_path

    @_path.setter
    def _path(self, value: str):
        """Set current FTP path on window."""
        self._win.current_ftp_path = value

    @property
    def _base_path(self) -> str:
        """Get base FTP path for this shot."""
        return FTP_SHOT_PATH.format(shot_name=self._win.shot_name)

    # ------------------------------------------------------------------
    # Icon helpers
    # ------------------------------------------------------------------

    def _load_or_fetch_icon(self, key: str, fetch_fn) -> QtGui.QIcon:
        """Return icon for key: load from disk cache PNG if available, else query OS and save."""
        cache_path = _ICON_CACHE_DIR / f"{key}.png"
        if cache_path.exists():
            icon = QtGui.QIcon(QtGui.QPixmap(str(cache_path)))
            if not icon.isNull():
                return icon
        icon = fetch_fn()
        if not icon.isNull():
            pixmap = icon.pixmap(32, 32)
            pixmap.save(str(cache_path), "PNG")
        return icon

    def _get_file_icon(self, name: str) -> QtGui.QIcon:
        """Return a cached OS-native icon for the given filename (by extension)."""
        ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
        if ext in self._icon_memory:
            return self._icon_memory[ext]
        key = ext if ext else "__noext__"
        icon = self._load_or_fetch_icon(
            key, lambda: self._icon_provider.icon(QFileInfo(f"dummy.{ext}" if ext else "dummy"))
        )
        if icon.isNull():
            icon = self._icon_file
        self._icon_memory[ext] = icon
        return icon

    # ------------------------------------------------------------------
    # Header sort setup
    # ------------------------------------------------------------------

    def _setup_header_sort(self):
        """Wire header clicks to manual sort so '..' is always pinned at top."""
        header = self._win.ftp_tree.header()
        header.setSectionsClickable(True)
        header.setSortIndicatorShown(True)
        header.setSortIndicator(0, QtCore.Qt.AscendingOrder)
        header.sectionClicked.connect(self._on_header_clicked)

    def _on_header_clicked(self, col: int):
        if col == self._sort_column:
            self._sort_ascending = not self._sort_ascending
        else:
            self._sort_column = col
            self._sort_ascending = True
        order = (
            QtCore.Qt.AscendingOrder
            if self._sort_ascending
            else QtCore.Qt.DescendingOrder
        )
        self._win.ftp_tree.header().setSortIndicator(self._sort_column, order)
        self._apply_sort()

    def _apply_sort(self):
        """Re-sort tree items in Python, keeping '..' pinned at top and dirs before files."""
        tree = self._win.ftp_tree
        parent_item = None
        dirs = []
        files = []

        for i in range(tree.topLevelItemCount()):
            item = tree.topLevelItem(i)
            info = item.data(0, QtCore.Qt.UserRole) or {}
            if info.get("is_parent"):
                parent_item = item
            elif info.get("is_dir", False):
                dirs.append(item)
            else:
                files.append(item)

        col = self._sort_column
        rev = not self._sort_ascending
        dirs.sort(key=lambda it: it.text(col).lower(), reverse=rev)
        files.sort(key=lambda it: it.text(col).lower(), reverse=rev)

        while tree.topLevelItemCount():
            tree.takeTopLevelItem(0)
        if parent_item:
            tree.addTopLevelItem(parent_item)
        for item in dirs + files:
            tree.addTopLevelItem(item)

    # ------------------------------------------------------------------
    # Listing
    # ------------------------------------------------------------------

    def refresh(self):
        """Request a new directory listing for the current FTP path."""
        if not self._win.ftp_manager.is_connected():
            self._win.log("Cannot refresh: not connected", "warning")
            return

        if self._win.ftp_manager.is_busy():
            self._win.log("Cannot refresh: operation in progress", "warning")
            return

        self._win.ftp_path_edit.setText(self._path)
        self._win.ftp_tree.clear()

        try:
            self._win.ftp_manager.list_files(self._path)
        except Exception as e:
            self._win.log(f"List error: {e}", "error")

    def update_list(self, files_info: list):
        """
        Populate the FTP tree from listing results.
        """
        tree = self._win.ftp_tree
        tree.setSortingEnabled(False)
        tree.clear()
        self.clear_selection()

        # Handle empty folder
        if not files_info:
            placeholder = QtWidgets.QTreeWidgetItem()
            placeholder.setText(0, "(empty folder)")
            placeholder.setFlags(QtCore.Qt.NoItemFlags)
            tree.addTopLevelItem(placeholder)
        else:
            # Separate folders and files with better type checking
            folders = []
            files = []

            for f in files_info:
                # Handle both boolean and string representations
                is_dir = f.get("is_dir", False)
                if isinstance(is_dir, str):
                    is_dir = is_dir.lower() == "true"

                if is_dir:
                    folders.append(f)
                else:
                    files.append(f)

            # Sort using current sort state
            col = self._sort_column
            reverse = not self._sort_ascending
            col_keys = {0: "name", 1: "size_str", 2: "modify_str"}
            key_field = col_keys.get(col, "name")

            # Sort folders
            folders.sort(
                key=lambda f: str(f.get(key_field, "")).lower(),
                reverse=reverse,
            )

            # Sort files
            files.sort(
                key=lambda f: str(f.get(key_field, "")).lower(),
                reverse=reverse,
            )

            # Combine: folders first, then files
            sorted_info = folders + files

            items = []
            for file_info in sorted_info:
                item = QtWidgets.QTreeWidgetItem()
                item.setText(0, file_info.get("name", ""))
                item.setText(1, file_info.get("size_str", ""))
                item.setText(2, file_info.get("modify_str", ""))
                item.setData(0, QtCore.Qt.UserRole, file_info)

                # Check if it's a directory
                is_dir = file_info.get("is_dir", False)
                if isinstance(is_dir, str):
                    is_dir = is_dir.lower() == "true"

                if is_dir:
                    item.setIcon(0, self._icon_dir)
                    # Make folders bold
                    font = item.font(0)
                    font.setBold(True)
                    item.setFont(0, font)
                else:
                    item.setIcon(0, self._get_file_icon(file_info.get("name", "")))

                item.setTextAlignment(1, QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
                item.setTextAlignment(2, QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
                items.append(item)

            tree.addTopLevelItems(items)


    # ------------------------------------------------------------------
    # Navigation
    # ------------------------------------------------------------------

    def on_double_clicked(self, item, column):
        """Navigate into a directory or up via .. on double-click."""
        if not item:
            return

        if self._win.ftp_manager.is_busy():
            return

        file_info = item.data(0, QtCore.Qt.UserRole)
        if not file_info:
            return

        if file_info.get("is_dir", False):
            self._navigate_to(file_info["path"])

    def navigate_back(self):
        """Navigate up one level, unrestricted."""
        if not self._win.ftp_manager.is_connected():
            self._win.log("Cannot navigate: not connected", "warning")
            return

        if self._win.ftp_manager.is_busy():
            self._win.log("Cannot navigate: operation in progress", "warning")
            return

        if self._path == "/" or self._path == "":
            self._win.log("Already at FTP root", "warning")
            return

        parent = "/".join(self._path.rstrip("/").split("/")[:-1]) or "/"
        self._navigate_to(parent)

    def _navigate_to(self, path: str):
        """Navigate to a specific FTP path."""
        self._path = path
        self._win.ftp_path_edit.setText(path)
        self._win.log(f"FTP: {path}", "info")
        self.clear_selection()

        try:
            self.refresh()
        except Exception as e:
            self._win.log(f"Navigation error: {e}", "error")

    # ------------------------------------------------------------------
    # Selection
    # ------------------------------------------------------------------

    def get_selected_paths(self) -> list:
        """Return remote paths of selected items, excluding .. and placeholders."""
        paths = []
        for item in self._win.ftp_tree.selectedItems():
            info = item.data(0, QtCore.Qt.UserRole)
            if info and info.get("path") and not info.get("is_parent"):
                paths.append(info["path"])
        return paths

    def clear_selection(self):
        """Clear FTP tree selection."""
        self._win.ftp_tree.clearSelection()

    # ------------------------------------------------------------------
    # Inline rename
    # ------------------------------------------------------------------

    def start_inline_rename(self):
        """Activate inline editing for the selected FTP item."""
        tree = self._win.ftp_tree
        items = tree.selectedItems()
        if len(items) != 1:
            self._win.log("Select a single item to rename", "warning")
            return
        item = items[0]
        info = item.data(0, QtCore.Qt.UserRole)
        if not info or info.get("is_parent"):
            return
        if not self._win.ftp_manager.is_connected():
            self._win.log("Cannot rename: not connected", "warning")
            return
        if self._win.ftp_manager.is_busy():
            self._win.log("Cannot rename: operation in progress", "warning")
            return

        self._renaming_item = item
        self._rename_old_path = info["path"]
        # Disconnect to suppress the itemChanged signal fired by setFlags
        self._win.ftp_tree.itemChanged.disconnect(self._win._on_ftp_item_changed)
        item.setFlags(item.flags() | QtCore.Qt.ItemIsEditable)
        self._win.ftp_tree.itemChanged.connect(self._win._on_ftp_item_changed)
        tree.editItem(item, 0)

    def on_item_changed(self, item):
        """Handle inline rename completion for FTP items."""
        if item is None or item is not self._renaming_item:
            return

        # Capture and clear state before setFlags to prevent re-entrant calls
        renaming_item = self._renaming_item
        old_path = self._rename_old_path
        self._renaming_item = None
        self._rename_old_path = None

        renaming_item.setFlags(renaming_item.flags() & ~QtCore.Qt.ItemIsEditable)

        if old_path is None:
            return

        new_name = item.text(0).strip()
        old_name = old_path.rstrip("/").split("/")[-1]

        if not new_name or new_name == old_name:
            item.setText(0, old_name)
            return

        parent = "/".join(old_path.rstrip("/").split("/")[:-1])
        new_path = f"{parent}/{new_name}".replace("//", "/")
        self._win.ftp_manager.rename_file(old_path, new_path)

    # ------------------------------------------------------------------
    # Folder creation / missing shot folder prompt
    # ------------------------------------------------------------------

    def prompt_create_shot_folder_if_at_base(self):
        """Show a prompt to create the missing shot folder (deferred to main thread)."""
        if self._path == self._base_path:
            QtCore.QTimer.singleShot(0, self._prompt_create_shot_folder)

    def _prompt_create_shot_folder(self):
        result = self._wm.show_buttons_dialog(
            self._win,
            "FTP Folder Missing",
            f"Shot folder not found on FTP server!\nFTP path: {self._path}\n\nWould you like to create it now?",
            buttons=[("Create Folder", True), ("Cancel", False)],
            icon=QtWidgets.QMessageBox.Warning,
        )

        if not result:
            return

        self._win.log(f"Creating FTP folder: {self._path}", "info")

        def on_folder_created(success, message):
            if success:
                self._win.log(f"✅ Folder created: {self._path}", "success")
                self.refresh()
            else:
                self._win.log(f"❌ Failed to create folder: {message}", "error")

        self._wm.safe_connect_once(
            self._win.ftp_manager.operation_finished, on_folder_created, self._win
        )

        self._win.ftp_manager.create_directories([self._path])
