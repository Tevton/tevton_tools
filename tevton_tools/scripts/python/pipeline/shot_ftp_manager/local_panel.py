import os
import shutil
from qt_shim import QtCore, QtWidgets
from ftp.ftp_utils import format_size as _format_size


class _FolderFirstProxy(QtCore.QSortFilterProxyModel):
    """Sort proxy: folders always above files, then standard sort within each group."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSortCaseSensitivity(QtCore.Qt.CaseInsensitive)

    def data(self, index, role=QtCore.Qt.DisplayRole):
        if role == QtCore.Qt.DisplayRole and index.column() == 1:
            src = self.mapToSource(index)
            size = self.sourceModel().size(src)
            return _format_size(size) if size > 0 else ""
        return super().data(index, role)

    def lessThan(self, left, right):
        fm = self.sourceModel()
        left_is_dir = fm.isDir(left)
        right_is_dir = fm.isDir(right)
        if left_is_dir != right_is_dir:
            return left_is_dir  # folders first
        return super().lessThan(left, right)

    def to_file_model_index(self, proxy_index):
        return self.mapToSource(proxy_index)


class LocalPanel:
    """
    Manages the local file tree: model setup, navigation, selection, and deletion.

    Proxy stack:  QFileSystemModel → _FolderFirstProxy → QTreeView
    """

    def __init__(self, window):
        self._win = window
        self.file_model: QtWidgets.QFileSystemModel = None
        self._proxy: _FolderFirstProxy = None

    # ------------------------------------------------------------------
    # Setup
    # ------------------------------------------------------------------

    def setup_model(self, root_path: str):
        """Create proxy stack and attach to the local tree."""
        self._win.local_path_edit.setText(root_path)

        if not root_path or not os.path.isdir(root_path):
            self._win.log(f"Local path not found: {root_path}", "warning")
            return

        self.file_model = QtWidgets.QFileSystemModel()
        self.file_model.setReadOnly(False)
        self.file_model.setFilter(
            QtCore.QDir.AllDirs | QtCore.QDir.Files | QtCore.QDir.NoDotAndDotDot
        )
        self.file_model.setRootPath(root_path)

        self._proxy = _FolderFirstProxy()
        self._proxy.setSourceModel(self.file_model)

        tree = self._win.local_tree
        tree.setModel(self._proxy)
        tree.setRootIndex(self._proxy.mapFromSource(self.file_model.index(root_path)))
        tree.setColumnHidden(2, True)
        tree.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        tree.setEditTriggers(QtWidgets.QAbstractItemView.EditKeyPressed)
        tree.setSortingEnabled(True)
        tree.sortByColumn(0, QtCore.Qt.AscendingOrder)

        header = tree.header()
        header.setSectionResizeMode(0, QtWidgets.QHeaderView.Stretch)
        header.setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeToContents)
        header.setSectionResizeMode(3, QtWidgets.QHeaderView.ResizeToContents)
        header.setStretchLastSection(False)

        def _lock_columns(_h=header):
            _h.setSectionResizeMode(1, QtWidgets.QHeaderView.Fixed)
            _h.setSectionResizeMode(3, QtWidgets.QHeaderView.Fixed)

        def _on_dir_loaded(_):
            self.file_model.directoryLoaded.disconnect(_on_dir_loaded)
            self._win._wm.safe_timer(self._win, _lock_columns, 100)  # wait for Qt layout to settle before locking

        self.file_model.directoryLoaded.connect(_on_dir_loaded)
        self.file_model.fileRenamed.connect(
            lambda _, old, new: self._win.log(f"Renamed: {old} → {new}", "info")
        )
        tree.selectionModel().selectionChanged.connect(self._win._on_local_selection_changed)

        self._win.log(f"Local root: {root_path}", "info")

    # ------------------------------------------------------------------
    # Navigation
    # ------------------------------------------------------------------

    def navigate_back(self):
        """Navigate up one level locally, limited to project root."""
        tree = self._win.local_tree
        if not tree or not self.file_model or not self._proxy:
            return

        tree.clearSelection()
        current_proxy_root = tree.rootIndex()
        parent_proxy = current_proxy_root.parent()
        fm_parent = self._proxy.to_file_model_index(parent_proxy)
        parent_path = self.file_model.filePath(fm_parent)
        nav_root = self._win.local_root_path or self._win.local_shot_path

        if not parent_path or not nav_root:
            self._win.log("Already at root", "warning")
            return

        if len(os.path.normpath(parent_path)) < len(os.path.normpath(nav_root)):
            self._win.log("Cannot navigate above project root", "warning")
            return

        tree.setRootIndex(parent_proxy)
        self._win.current_local_path = parent_path
        self._win.local_path_edit.setText(parent_path)
        self._win.log(f"Local: {parent_path}", "info")
        tree.sortByColumn(0, QtCore.Qt.AscendingOrder)

    def on_double_clicked(self, proxy_index):
        """Navigate into a local directory on double-click."""
        if not self.file_model or not self._proxy:
            return

        fm_index = self._proxy.to_file_model_index(proxy_index)
        if not self.file_model.isDir(fm_index):
            return

        dir_path = self.file_model.filePath(fm_index)
        self._win.local_tree.clearSelection()
        self._win.local_tree.setRootIndex(proxy_index)
        self._win.current_local_path = dir_path
        self._win.local_path_edit.setText(dir_path)
        self._win.log(f"Local: {dir_path}", "info")
        self._win.local_tree.sortByColumn(0, QtCore.Qt.AscendingOrder)

    # ------------------------------------------------------------------
    # Selection
    # ------------------------------------------------------------------

    def get_selected_paths(self) -> list:
        """Return absolute paths of selected local files and directories."""
        if not self.file_model or not self._proxy:
            return []

        tree = self._win.local_tree
        seen = set()
        paths = []

        for index in tree.selectionModel().selectedIndexes():
            if index.column() != 0:
                continue
            fm_index = self._proxy.to_file_model_index(index)
            path = self.file_model.filePath(fm_index)
            if path not in seen:
                seen.add(path)
                paths.append(path)

        return paths

    def clear_selection(self):
        """Clear local tree selection."""
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

    def _teardown_model(self):
        """Detach and destroy the file model, releasing all QFileSystemWatcher handles."""
        tree = self._win.local_tree
        if tree:
            tree.setModel(None)
        if self.file_model:
            self.file_model.deleteLater()
            self.file_model = None
        self._proxy = None

    def delete_files(self, paths: list):
        """Delete local files/directories with error reporting."""
        current_root = self.file_model.rootPath() if self.file_model else None
        if self.file_model:
            self._teardown_model()

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
            self.setup_model(current_root)

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
