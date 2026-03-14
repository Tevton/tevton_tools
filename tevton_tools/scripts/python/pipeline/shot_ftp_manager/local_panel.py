import os
import shutil
from PySide6 import QtCore, QtWidgets


class _LocalSortProxy(QtCore.QSortFilterProxyModel):
    """
    Proxy that:
    - Hides ".." when already at the navigation root (can't go higher)
    - Pins ".." first when visible
    - Sorts dirs before files, then alphabetically
    - Shows folder icon for ".." entry
    """

    def __init__(self):
        super().__init__()
        self._nav_root = ""
        self._sort_order = QtCore.Qt.AscendingOrder

    def sort(self, column, order):
        self._sort_order = order
        super().sort(column, order)

    def set_nav_root(self, path: str):
        self._nav_root = os.path.normpath(path) if path else ""
        self.invalidateFilter()

    def filterAcceptsRow(self, source_row, source_parent):
        src = self.sourceModel()
        index = src.index(source_row, 0, source_parent)
        name = src.fileName(index)
        if name == ".." and self._nav_root:
            current_dir = src.filePath(source_parent)
            if os.path.normpath(current_dir) == self._nav_root:
                return False
        return True

    def lessThan(self, left, right):
        src = self.sourceModel()
        left_name = src.fileName(left)
        right_name = src.fileName(right)
        # For ascending: lessThan("..", x)=True pins ".." first.
        # For descending: Qt inverts the result, so lessThan("..", x)=False → !False=True → ".." first.
        asc = self._sort_order == QtCore.Qt.AscendingOrder
        if left_name == "..":
            return asc
        if right_name == "..":
            return not asc
        if left.column() != 0:
            # Size/Date columns: defer to Qt's default comparison (uses actual model data)
            return super().lessThan(left, right)
        left_is_dir = src.isDir(left)
        right_is_dir = src.isDir(right)
        if left_is_dir != right_is_dir:
            return left_is_dir  # dirs before files
        return left_name.lower() < right_name.lower()

    def data(self, index, role=QtCore.Qt.DisplayRole):
        if role == QtCore.Qt.DecorationRole and index.column() == 0:
            src_index = self.mapToSource(index)
            if self.sourceModel().fileName(src_index) == "..":
                return QtWidgets.QApplication.style().standardIcon(
                    QtWidgets.QStyle.SP_DirIcon
                )
        return super().data(index, role)


class LocalPanel:
    """
    Manages the local file tree: model setup, navigation, selection, and deletion.

    No safety checks needed! WindowManager ensures signals only fire when window is alive.
    All methods assume window and widgets exist.
    """

    def __init__(self, window):
        self._win = window
        self.file_model: QtWidgets.QFileSystemModel = None
        self._proxy: _LocalSortProxy = None

    # ------------------------------------------------------------------
    # Setup
    # ------------------------------------------------------------------

    def setup_model(self, root_path: str):
        """Create and attach QFileSystemModel (via sort proxy) to the local tree."""
        tree = self._win.local_tree
        if not tree:
            return

        # Create file system model
        self.file_model = QtWidgets.QFileSystemModel()
        self.file_model.setReadOnly(False)
        self.file_model.setFilter(
            QtCore.QDir.AllDirs | QtCore.QDir.Files | QtCore.QDir.NoDot
        )

        # Wrap with sort proxy
        self._proxy = _LocalSortProxy()
        self._proxy.setSourceModel(self.file_model)

        # Set up model if root path exists
        if root_path and os.path.isdir(root_path):
            self.file_model.setRootPath(root_path)
            nav_root = self._win.local_root_path or root_path
            self._proxy.set_nav_root(nav_root)
            tree.setModel(self._proxy)
            tree.setRootIndex(
                self._proxy.mapFromSource(self.file_model.index(root_path))
            )

            tree.setColumnWidth(0, 300)
            tree.setColumnWidth(1, 100)
            tree.setColumnHidden(2, True)
            tree.setColumnWidth(3, 140)

            header = tree.header()
            header.setSectionResizeMode(0, QtWidgets.QHeaderView.Stretch)
            header.setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeToContents)
            header.setSectionResizeMode(3, QtWidgets.QHeaderView.ResizeToContents)
            header.setStretchLastSection(False)

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
        if not tree or not self.file_model or not self._proxy:
            return

        tree.clearSelection()
        proxy_root = tree.rootIndex()
        src_root = self._proxy.mapToSource(proxy_root)
        src_parent = src_root.parent()
        parent_path = self.file_model.filePath(src_parent)
        nav_root = self._win.local_root_path or self._win.local_shot_path

        if not parent_path or not nav_root:
            self._win.log("Already at root", "warning")
            return

        if len(os.path.normpath(parent_path)) < len(os.path.normpath(nav_root)):
            self._win.log("Cannot navigate above project root", "warning")
            return

        # Navigate to parent
        tree.setRootIndex(self._proxy.mapFromSource(src_parent))
        self._win.current_local_path = parent_path
        self._win.local_path_edit.setText(parent_path)
        self._win.log(f"Local: {parent_path}", "info")
        tree.sortByColumn(0, QtCore.Qt.SortOrder.AscendingOrder)

    def on_double_clicked(self, index):
        """Navigate into a local directory on double-click. '..' delegates to navigate_back()."""
        if not self.file_model or not self._proxy:
            return

        src_index = self._proxy.mapToSource(index)
        if not self.file_model.isDir(src_index):
            return

        dir_path = self.file_model.filePath(src_index)

        # Detect ".." — target is the parent of the current root
        src_root = self._proxy.mapToSource(self._win.local_tree.rootIndex())
        current_root = self.file_model.filePath(src_root)
        if os.path.normpath(dir_path) == os.path.normpath(
            os.path.dirname(current_root)
        ):
            self.navigate_back()
            return

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
        if not self.file_model or not self._proxy:
            return []

        tree = self._win.local_tree
        seen = set()
        paths = []

        for index in tree.selectionModel().selectedIndexes():
            if index.column() != 0:
                continue
            src_index = self._proxy.mapToSource(index)
            path = self.file_model.filePath(src_index)
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
        if not self.file_model or not self._proxy:
            return
        indexes = [
            i for i in tree.selectionModel().selectedIndexes() if i.column() == 0
        ]
        if len(indexes) == 1:
            src_index = self._proxy.mapToSource(indexes[0])
            if self.file_model.fileName(src_index) == "..":
                return
            tree.edit(indexes[0])

    # ------------------------------------------------------------------
    # Deletion
    # ------------------------------------------------------------------

    def delete_files(self, paths: list):
        """Delete local files/directories with error reporting."""

        model = self.file_model
        current_root = model.rootPath() if model else ""
        if model and current_root:
            model.setRootPath("")

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

        if model and current_root:
            model.setRootPath(current_root)

        if errors:
            self._win.log(f"Failed to delete: {'; '.join(errors)}", "error")
        else:
            self._win.log(f"✓ Deleted {len(paths)} local item(s)", "success")
