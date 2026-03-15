import os
import shutil
from PySide6 import QtCore, QtWidgets


class LocalPanel:
    """
    Manages the local file tree: model setup, navigation, selection, and deletion.

    No safety checks needed! WindowManager ensures signals only fire when window is alive.
    All methods assume window and widgets exist.
    """

    def __init__(self, window):
        self._win = window
        self.file_model: QtWidgets.QFileSystemModel = None

    # ------------------------------------------------------------------
    # Setup
    # ------------------------------------------------------------------

    def setup_model(self, root_path: str):
        """Create and attach QFileSystemModel directly to the local tree."""
        tree = self._win.local_tree
        if not tree:
            return

        self.file_model = QtWidgets.QFileSystemModel()
        self.file_model.setReadOnly(False)
        self.file_model.setFilter(
            QtCore.QDir.AllDirs | QtCore.QDir.Files | QtCore.QDir.NoDotAndDotDot
        )

        if root_path and os.path.isdir(root_path):
            self.file_model.setRootPath(root_path)
            tree.setModel(self.file_model)
            tree.setRootIndex(self.file_model.index(root_path))

            tree.setColumnWidth(0, 300)
            tree.setColumnWidth(1, 100)
            tree.setColumnHidden(2, True)
            tree.setColumnWidth(3, 140)

            header = tree.header()
            header.setSectionResizeMode(0, QtWidgets.QHeaderView.Stretch)
            header.setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeToContents)
            header.setSectionResizeMode(3, QtWidgets.QHeaderView.ResizeToContents)
            header.setStretchLastSection(False)
            header.setMinimumSectionSize(90)

            tree.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
            tree.setSortingEnabled(True)
            tree.sortByColumn(0, QtCore.Qt.AscendingOrder)

            tree.selectionModel().selectionChanged.connect(
                self._win._on_local_selection_changed
            )

            self.file_model.fileRenamed.connect(
                lambda _path, old, new: self._win.log(f"Renamed: {old} → {new}", "info")
            )

            self._win.log(f"Local root: {root_path}", "info")
        else:
            self._win.log(f"Local path not found: {root_path}", "warning")

        self._win.local_path_edit.setText(root_path)

    # ------------------------------------------------------------------
    # Navigation
    # ------------------------------------------------------------------

    def navigate_back(self):
        """Navigate up one level locally, limited to project root."""
        tree = self._win.local_tree
        if not tree or not self.file_model:
            return

        tree.clearSelection()
        current_root = tree.rootIndex()
        src_parent = current_root.parent()
        parent_path = self.file_model.filePath(src_parent)
        nav_root = self._win.local_root_path or self._win.local_shot_path

        if not parent_path or not nav_root:
            self._win.log("Already at root", "warning")
            return

        if len(os.path.normpath(parent_path)) < len(os.path.normpath(nav_root)):
            self._win.log("Cannot navigate above project root", "warning")
            return

        tree.setRootIndex(src_parent)
        self._win.current_local_path = parent_path
        self._win.local_path_edit.setText(parent_path)
        self._win.log(f"Local: {parent_path}", "info")
        tree.sortByColumn(0, QtCore.Qt.SortOrder.AscendingOrder)

    def on_double_clicked(self, index):
        """Navigate into a local directory on double-click."""
        if not self.file_model:
            return

        if not self.file_model.isDir(index):
            return

        dir_path = self.file_model.filePath(index)
        self._win.local_tree.clearSelection()
        self._win.local_tree.setRootIndex(index)
        self._win.current_local_path = dir_path
        self._win.local_path_edit.setText(dir_path)
        self._win.log(f"Local: {dir_path}", "info")
        self._win.local_tree.sortByColumn(0, QtCore.Qt.SortOrder.AscendingOrder)

    # ------------------------------------------------------------------
    # Selection
    # ------------------------------------------------------------------

    def get_selected_paths(self) -> list:
        """Return absolute paths of selected local files and directories."""
        if not self.file_model:
            return []

        tree = self._win.local_tree
        seen = set()
        paths = []

        for index in tree.selectionModel().selectedIndexes():
            if index.column() != 0:
                continue
            path = self.file_model.filePath(index)
            if path not in seen:
                seen.add(path)
                paths.append(path)

        return paths

    def clear_selection(self):
        """Clear local tree selection including the current-item highlight."""
        self._win.local_tree.selectionModel().reset()

    # ------------------------------------------------------------------
    # Inline rename
    # ------------------------------------------------------------------

    def start_inline_rename(self):
        """Activate inline editing for the selected local item."""
        tree = self._win.local_tree
        if not self.file_model:
            return
        indexes = [
            i for i in tree.selectionModel().selectedIndexes() if i.column() == 0
        ]
        if len(indexes) == 1:
            tree.edit(indexes[0])

    # ------------------------------------------------------------------
    # Deletion
    # ------------------------------------------------------------------

    def delete_files(self, paths: list):
        """Delete local files/directories with error reporting."""
        # Temporarily release filesystem watches to prevent Qt watcher errors
        # (FindNextChangeNotification / Access denied) when deleting watched subdirs.
        current_root = self.file_model.rootPath() if self.file_model else None
        if current_root:
            self.file_model.setRootPath("")

        errors = []
        for path in paths:
            try:
                if os.path.isdir(path):
                    shutil.rmtree(path)
                else:
                    os.remove(path)
                self._win.log(f"Deleted: {os.path.basename(path)}", "info")
            except Exception as e:
                errors.append(f"{os.path.basename(path)}: {e}")

        if current_root:
            self.file_model.setRootPath(current_root)

        if errors:
            self._win.log(f"Failed to delete: {'; '.join(errors)}", "error")
        else:
            self._win.log(f"✓ Deleted {len(paths)} local item(s)", "success")

    # ------------------------------------------------------------------
    # Move
    # ------------------------------------------------------------------

    def move_files(self, src_paths: list, target_dir: str):
        """Move local files/folders into target_dir."""
        errors = []
        moved = 0
        for src in src_paths:
            if os.path.normpath(src) == os.path.normpath(target_dir):
                continue
            name = os.path.basename(src)
            dst = os.path.join(target_dir, name)
            if os.path.normpath(src) == os.path.normpath(dst):
                continue
            try:
                shutil.move(src, dst)
                moved += 1
            except Exception as e:
                errors.append(f"{name}: {e}")

        if errors:
            self._win.log(f"Move errors: {'; '.join(errors)}", "error")
        elif moved:
            self._win.log(
                f"Moved {moved} item(s) → {os.path.basename(target_dir)}", "info"
            )
