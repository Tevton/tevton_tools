import hou
from datetime import datetime
from pathlib import Path
from PySide6 import QtCore, QtUiTools, QtWidgets, QtGui

from ftp import FTPManager
from ui_state import UIStateController
import tvt_utils
from config.config import FTP_SHOT_PATH

from .ftp_panel import FTPPanel
from .local_panel import LocalPanel
from .transfer_panel import TransferPanel
from pipeline.window_manager import get_window_manager
from ftp.workers.connect_worker import FTPConnectWorker


FTP_MIME_TYPE = "application/x-tvt-ftp-paths"


class ShotFTPManager(QtWidgets.QMainWindow):
    """
    Per-shot FTP manager window for browsing and transferring files between
    local filesystem and FTP.
    """

    MAX_LOG_LINES = 100

    def __init__(self, parent=None, project_name=None, shot_name=None, shot_path=None):
        super().__init__(parent)

        self._wm = get_window_manager()

        self.project_name = project_name
        self.shot_name = shot_name
        self.local_shot_path = shot_path
        self.local_root_path = (
            str(Path(shot_path).parent.parent) if shot_path else shot_path
        )
        self.current_ftp_path = FTP_SHOT_PATH.format(shot_name=self.shot_name)
        self.current_local_path = self.local_shot_path

        self.ftp_manager = FTPManager(self)
        self.ftp_manager.set_project(project_name)

        self._ui_state = UIStateController()
        self._stats_timer = QtCore.QTimer(self)
        self._stats_timer.setInterval(200)
        self._stats_timer.timeout.connect(self._on_stats_timer)

        self._connection_animation = tvt_utils.ConnectionAnimator(self)
        self._connection_animation.timeout_reached.connect(self._on_connection_timeout)

        self.ftp_panel: FTPPanel = None
        self.local_panel: LocalPanel = None
        self.transfer_panel: TransferPanel = None

        self._load_ui()
        self._find_widgets()
        self._setup_ui()
        self._setup_ftp_tree()

        self._wm.register_log_widget(self, self.log_text)

        self.ftp_panel = FTPPanel(self)
        self.local_panel = LocalPanel(self)
        self.transfer_panel = TransferPanel(self)

        self.local_panel.setup_model(self.local_shot_path or "")
        self._setup_drag_drop()
        self._register_widgets()
        self._connect_ftp_signals_safe()
        self._connect_ui_signals()
        self._setup_context_menus()

        self._wm.safe_timer(self, self._connect_to_ftp, 300)

    def _load_ui(self):
        ui_path = hou.text.expandString("$TVT/ui/ShotFTPManager.ui")
        self.ui = QtUiTools.QUiLoader().load(ui_path, parentWidget=self)
        self.setParent(hou.qt.mainWindow(), QtCore.Qt.Window)
        self.setAttribute(QtCore.Qt.WA_DeleteOnClose)

    def _find_widgets(self):
        f = self.ui.findChild
        self.ftp_tree = f(QtWidgets.QTreeWidget, "qtw_ftp_list")
        self.progress_bar = f(QtWidgets.QProgressBar, "pbar_progress_bar")
        self.log_text = f(QtWidgets.QPlainTextEdit, "qpt_log_text")
        self.new_folder_btn = f(QtWidgets.QPushButton, "pb_new_folder")
        self.reconnect_btn = f(QtWidgets.QPushButton, "pb_reconnect")
        self.delete_selected_btn = f(QtWidgets.QPushButton, "pb_delete_selected")
        self.con_status_ind = f(QtWidgets.QLabel, "lb_con_status_ind")
        self.con_status = f(QtWidgets.QLabel, "lb_con_status")
        self.speed_status = f(QtWidgets.QLabel, "lb_speed")
        self.total_status = f(QtWidgets.QLabel, "lb_total")
        self.eta_status = f(QtWidgets.QLabel, "lb_eta")
        self.ftp_text = f(QtWidgets.QLabel, "lb_ftp")
        self.ftp_back_btn = f(QtWidgets.QPushButton, "pb_ftp_back_path")
        self.ftp_path_edit = f(QtWidgets.QLineEdit, "le_ftp_current_path")
        self.local_text = f(QtWidgets.QLabel, "lb_local")
        self.local_tree = f(QtWidgets.QTreeView, "tv_local_list")
        self.local_back_btn = f(QtWidgets.QPushButton, "pb_local_back_path")
        self.local_path_edit = f(QtWidgets.QLineEdit, "le_local_current_path")
        self.download_selected_btn = f(QtWidgets.QPushButton, "pb_download_selected")
        self.upload_selected_btn = f(QtWidgets.QPushButton, "pb_upload_selected")
        self.rb_up_only_missing = f(QtWidgets.QRadioButton, "rb_up_only_missing")
        self.rb_up_all_versions = f(QtWidgets.QRadioButton, "rb_up_all_versions")
        self.rb_up_only_latest = f(QtWidgets.QRadioButton, "rb_up_only_latest")
        self.rb_up_selected = f(QtWidgets.QRadioButton, "rb_up_selected")

    def _setup_ui(self):
        self.setWindowTitle(f"FTP Manager - {self.shot_name}")
        self.setMinimumSize(1100, 700)
        self.setMaximumSize(1800, 900)
        self.ftp_path_edit.setText(self.current_ftp_path)
        self.local_path_edit.setText(self.current_local_path or "")
        self.log_text.setMaximumBlockCount(self.MAX_LOG_LINES)
        self.log_text.setReadOnly(True)
        self.con_status.setAlignment(QtCore.Qt.AlignBottom)
        self.ftp_text.setAlignment(QtCore.Qt.AlignCenter)
        self.local_text.setAlignment(QtCore.Qt.AlignCenter)
        tvt_utils.set_connection_status(self, "disconnected")
        self.delshortcut = QtGui.QShortcut(QtGui.QKeySequence("Delete"), self)
        self.delshortcut.activated.connect(self._delete_selected)
        self.f2shortcut = QtGui.QShortcut(QtGui.QKeySequence("F2"), self)
        self.f2shortcut.activated.connect(self._rename_selected)

    def _setup_ftp_tree(self):
        if not self.ftp_tree:
            return
        self.ftp_tree.setHeaderLabels(["Name", "Size", "Date Modified"])
        self.ftp_tree.setColumnWidth(0, 300)
        self.ftp_tree.setColumnWidth(1, 80)
        self.ftp_tree.setColumnWidth(2, 140)
        self.ftp_tree.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self.ftp_tree.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.ftp_tree.setRootIsDecorated(False)
        self.ftp_tree.setAlternatingRowColors(True)
        self.ftp_tree.setUniformRowHeights(True)
        header = self.ftp_tree.header()
        header.setSectionResizeMode(0, QtWidgets.QHeaderView.Stretch)
        header.setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QtWidgets.QHeaderView.ResizeToContents)

    def _setup_drag_drop(self):
        self._ftp_drag_start_pos = None

        if self.ftp_tree:
            self.ftp_tree.setAcceptDrops(True)
            self.ftp_tree.setDropIndicatorShown(True)
            self.ftp_tree.viewport().installEventFilter(self)

        if self.local_tree:
            self.local_tree.setDragEnabled(True)
            self.local_tree.setAcceptDrops(True)
            self.local_tree.setDropIndicatorShown(True)
            self.local_tree.viewport().installEventFilter(self)

    def eventFilter(self, obj, event):
        etype = event.type()

        if self.ftp_tree and obj is self.ftp_tree.viewport():
            if etype == QtCore.QEvent.Type.MouseButtonPress:
                if event.button() == QtCore.Qt.MouseButton.LeftButton:
                    self._ftp_drag_start_pos = event.pos()
            elif etype == QtCore.QEvent.Type.MouseButtonRelease:
                self._ftp_drag_start_pos = None
            elif etype == QtCore.QEvent.Type.MouseMove:
                if (
                    self._ftp_drag_start_pos is not None
                    and event.buttons() & QtCore.Qt.MouseButton.LeftButton
                ):
                    dist = (event.pos() - self._ftp_drag_start_pos).manhattanLength()
                    if dist >= QtWidgets.QApplication.startDragDistance():
                        self._ftp_drag_start_pos = None
                        paths = (
                            self.ftp_panel.get_selected_paths()
                            if self.ftp_panel
                            else []
                        )
                        if paths:
                            mime = QtCore.QMimeData()
                            mime.setData(
                                FTP_MIME_TYPE, "\n".join(paths).encode("utf-8")
                            )
                            drag = QtGui.QDrag(self.ftp_tree)
                            drag.setMimeData(mime)
                            drag.exec(QtCore.Qt.DropAction.CopyAction)
                        return True
            elif etype == QtCore.QEvent.Type.DragEnter:
                if event.mimeData().hasUrls():
                    event.acceptProposedAction()
                    return True
            elif etype == QtCore.QEvent.Type.DragMove:
                if event.mimeData().hasUrls():
                    event.acceptProposedAction()
                    return True
            elif etype == QtCore.QEvent.Type.Drop:
                mime = event.mimeData()
                if mime.hasUrls():
                    local_paths = [
                        u.toLocalFile() for u in mime.urls() if u.isLocalFile()
                    ]
                    if local_paths and self.transfer_panel:
                        self.transfer_panel.start_upload(
                            local_paths, self.current_ftp_path
                        )
                    event.acceptProposedAction()
                    return True

        if self.local_tree and obj is self.local_tree.viewport():
            if etype == QtCore.QEvent.Type.DragEnter:
                if event.mimeData().hasFormat(FTP_MIME_TYPE):
                    event.acceptProposedAction()
                    return True
            elif etype == QtCore.QEvent.Type.DragMove:
                if event.mimeData().hasFormat(FTP_MIME_TYPE):
                    event.acceptProposedAction()
                    return True
            elif etype == QtCore.QEvent.Type.Drop:
                mime = event.mimeData()
                if mime.hasFormat(FTP_MIME_TYPE):
                    raw = bytes(mime.data(FTP_MIME_TYPE)).decode("utf-8")
                    remote_paths = [p for p in raw.splitlines() if p]
                    if remote_paths and self.transfer_panel:
                        self.transfer_panel.start_download(
                            remote_paths, self.current_local_path
                        )
                    event.acceptProposedAction()
                    return True

        return super().eventFilter(obj, event)

    def _register_widgets(self):
        self._ui_state.register_many(
            {
                "ftp_tree": self.ftp_tree,
                "reconnect_btn": self.reconnect_btn,
                "download_selected_btn": self.download_selected_btn,
                "upload_selected_btn": self.upload_selected_btn,
                "ftp_back_btn": self.ftp_back_btn,
                "new_folder_btn": self.new_folder_btn,
                "delete_selected_btn": self.delete_selected_btn,
            },
            groups=["operations"],
        )
        self._ui_state.disable_group("operations")
        if self.reconnect_btn:
            self.reconnect_btn.setEnabled(True)

    def _connect_ftp_signals_safe(self):
        self._wm.safe_connect(
            self.ftp_manager.connection_changed, self._on_connection_changed, self
        )
        self._wm.safe_connect(self.ftp_manager.status, self._on_status, self)
        self._wm.safe_connect(self.ftp_manager.files_ready, self._on_files_ready, self)
        self._wm.safe_connect(
            self.ftp_manager.operation_finished, self._on_operation_finished, self
        )
        self._wm.safe_connect(self.ftp_manager.progress, self._on_progress, self)
        self._wm.safe_connect(
            self.ftp_manager.transfer_stats, self._on_transfer_stats, self
        )
        self._wm.safe_connect(
            self.ftp_manager.busy_changed, self._on_busy_changed, self
        )
        self._wm.safe_connect(
            self.ftp_manager.overwrite_needed, self._on_overwrite_needed, self
        )

    def _setup_context_menus(self):
        if self.ftp_tree:
            self.ftp_tree.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
            self.ftp_tree.customContextMenuRequested.connect(
                lambda pos: self._show_ftp_menu(self.ftp_tree, pos)
            )
        if self.local_tree:
            self.local_tree.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
            self.local_tree.customContextMenuRequested.connect(
                lambda pos: self._show_local_menu(self.local_tree, pos)
            )

    def _connect_ui_signals(self):
        if self.reconnect_btn:
            self.reconnect_btn.clicked.connect(self._reconnect_to_ftp)
        if self.ftp_tree:
            self.ftp_tree.itemDoubleClicked.connect(self._on_ftp_item_double_clicked)
            self.ftp_tree.itemSelectionChanged.connect(self._on_ftp_selection_changed)
            self.ftp_tree.itemChanged.connect(self._on_ftp_item_changed)
        if self.ftp_back_btn:
            self.ftp_back_btn.clicked.connect(self._on_ftp_back_clicked)
        if self.local_back_btn:
            self.local_back_btn.clicked.connect(self._on_local_back_clicked)
        if self.local_tree:
            self.local_tree.doubleClicked.connect(self._on_local_item_double_clicked)
        if self.download_selected_btn:
            self.download_selected_btn.clicked.connect(self._download_selected)
        if self.upload_selected_btn:
            self.upload_selected_btn.clicked.connect(self._upload_selected)
        if self.new_folder_btn:
            self.new_folder_btn.clicked.connect(self._on_new_folder_clicked)
        if self.delete_selected_btn:
            self.delete_selected_btn.clicked.connect(self._delete_selected)

    def _safe_refresh_ftp(self):
        if self.ftp_manager.is_busy():
            self._wm.safe_timer(self, self._safe_refresh_ftp, 500)
            return

        if self.ftp_panel:
            try:
                self.ftp_panel.refresh()
                self.log("FTP list refreshed", "info")
            except RuntimeError:
                pass

    def _connect_to_ftp(self):
        if self.ftp_manager.is_busy():
            self.log("FTP manager is busy, skipping connection", "warning")
            return
        self.log("Connecting to FTP...", "connect")
        if self.reconnect_btn:
            self.reconnect_btn.setEnabled(False)
        self._connection_animation.start()
        self.ftp_manager.connect_to_server()

    def _reconnect_to_ftp(self):
        if self.ftp_manager.is_busy():
            self.log("Cannot reconnect: operation in progress", "warning")
            return
        if self.ftp_manager.is_connected():
            self.log("Disconnecting...", "info")
            try:
                self.ftp_manager.disconnect_from_server()
            except Exception as e:
                self.log(f"Disconnect error: {e}", "warning")
        self._wm.safe_timer(self, self._connect_to_ftp, 500)

    def _on_connection_changed(self, connected: bool):
        self._connection_animation.stop(success=connected)

        if connected:
            self._ui_state.enable_group("operations")
            self._wm.safe_timer(self, self._safe_refresh_ftp, 100)
        else:
            self.log("Disconnected", "warning")
            self._ui_state.disable_group("operations")
            if self.reconnect_btn:
                self.reconnect_btn.setEnabled(True)
            if self.ftp_tree:
                try:
                    self.ftp_tree.clear()
                except RuntimeError:
                    pass

    def _on_connection_timeout(self):
        self._ui_state.enable_group("operations")

    def _on_ftp_item_double_clicked(self, item, column):
        if self.ftp_panel:
            try:
                self.ftp_panel.on_double_clicked(item, column)
            except RuntimeError:
                pass

    def _on_ftp_back_clicked(self):
        if self.ftp_panel:
            try:
                self.ftp_panel.navigate_back()
            except RuntimeError:
                pass

    def _on_files_ready(self, files_info):
        if self.ftp_panel:
            try:
                self.ftp_panel.update_list(files_info)
            except RuntimeError:
                pass

    def _on_local_item_double_clicked(self, index):
        if self.local_panel:
            try:
                self.local_panel.on_double_clicked(index)
            except RuntimeError:
                pass

    def _on_local_back_clicked(self):
        if self.local_panel:
            try:
                self.local_panel.navigate_back()
            except RuntimeError:
                pass

    def _download_selected(self):
        if not self.transfer_panel:
            return
        try:
            ftp_paths = self.ftp_panel.get_selected_paths() if self.ftp_panel else []
            self.transfer_panel.start_download(ftp_paths, self.current_local_path)
        except RuntimeError:
            pass

    def _upload_selected(self):
        if not self.transfer_panel:
            return
        try:
            local_paths = (
                self.local_panel.get_selected_paths() if self.local_panel else []
            )
            self.transfer_panel.start_upload(local_paths, self.current_ftp_path)
        except RuntimeError:
            pass

    def _on_new_folder_clicked(self):
        if self.transfer_panel:
            try:
                self.transfer_panel.create_ftp_folder(self.current_ftp_path)
            except RuntimeError:
                pass

    def _delete_selected(self):
        if not self.transfer_panel:
            return
        try:
            ftp_paths = self.ftp_panel.get_selected_paths() if self.ftp_panel else []
            local_paths = (
                self.local_panel.get_selected_paths() if self.local_panel else []
            )
            self.transfer_panel.delete_selected(ftp_paths, local_paths)
        except RuntimeError:
            pass

    def _rename_selected(self):
        try:
            if self.ftp_tree and self.ftp_tree.selectedItems():
                if self.ftp_panel:
                    self.ftp_panel.start_inline_rename()
            elif self.local_tree and self.local_tree.selectionModel().hasSelection():
                if self.local_panel:
                    self.local_panel.start_inline_rename()
            else:
                self.log("No item selected for rename", "warning")
        except RuntimeError:
            pass

    def _on_local_new_folder_clicked(self):
        if self.transfer_panel:
            try:
                self.transfer_panel.create_local_folder(self.current_local_path)
            except RuntimeError:
                pass

    def _on_progress(self, value):
        if self.transfer_panel:
            try:
                self.transfer_panel.on_progress(value)
            except RuntimeError:
                pass

    def _on_transfer_stats(self, speed_mbps, transferred, total, eta):
        if self.transfer_panel:
            try:
                self.transfer_panel.on_transfer_stats(
                    speed_mbps, transferred, total, eta
                )
            except RuntimeError:
                pass

    def _on_busy_changed(self, busy):
        if self.transfer_panel:
            try:
                self.transfer_panel.on_busy_changed(busy)
            except RuntimeError:
                pass

    def _on_stats_timer(self):
        if self.transfer_panel:
            try:
                self.transfer_panel.poll_stats()
            except RuntimeError:
                pass

    def _show_ftp_menu(self, tree, pos):
        item = tree.itemAt(pos)
        menu = QtWidgets.QMenu(tree)
        menu.setFont(self._wm.menu_font())

        if item:
            info = item.data(0, QtCore.Qt.UserRole)
            if info and not info.get("is_parent"):
                menu.addAction("Download").triggered.connect(self._download_selected)
                menu.addAction("Rename").triggered.connect(self._rename_selected)
                menu.addAction("Delete").triggered.connect(self._delete_selected)
                menu.addSeparator()

        menu.addAction("New Folder").triggered.connect(self._on_new_folder_clicked)
        menu.exec(tree.viewport().mapToGlobal(pos))

    def _show_local_menu(self, tree, pos):
        index = tree.indexAt(pos)
        menu = QtWidgets.QMenu(tree)
        menu.setFont(self._wm.menu_font())

        if index.isValid():
            menu.addAction("Upload").triggered.connect(self._upload_selected)
            menu.addAction("Rename").triggered.connect(self._rename_selected)
            menu.addAction("Delete").triggered.connect(self._delete_selected)
            menu.addSeparator()

        menu.addAction("New Folder").triggered.connect(
            self._on_local_new_folder_clicked
        )
        menu.exec(tree.viewport().mapToGlobal(pos))

    def _on_ftp_item_changed(self, item):
        if self.ftp_panel:
            try:
                self.ftp_panel.on_item_changed(item)
            except RuntimeError:
                pass

    def _on_ftp_selection_changed(self):
        if self.ftp_tree and self.ftp_tree.selectedItems() and self.local_panel:
            try:
                self.local_panel.clear_selection()
            except RuntimeError:
                pass

    def _on_local_selection_changed(self):
        if (
            self.local_tree
            and self.local_tree.selectionModel().hasSelection()
            and self.ftp_panel
        ):
            try:
                self.ftp_panel.clear_selection()
            except RuntimeError:
                pass

    def _on_overwrite_needed(self, conflict_names: list):
        names = "\n".join(conflict_names[:10])
        extra = (
            f"\n... and {len(conflict_names) - 10} more"
            if len(conflict_names) > 10
            else ""
        )
        answer = self._wm.show_buttons_dialog(
            self,
            "Files Already Exist",
            f"{len(conflict_names)} file(s) already exist locally:\n\n{names}{extra}\n\nReplace them?",
            buttons=[("Replace", True), ("Cancel", False)],
            icon=QtWidgets.QMessageBox.Warning,
        )
        self.ftp_manager.set_overwrite(answer)

    def _on_status(self, level: str, message: str):
        self.log(message, level)

    def _on_operation_finished(self, success: bool, message: str):
        if self.transfer_panel:
            try:
                self.transfer_panel.restore_button()
            except RuntimeError:
                pass

        msg_lower = message.lower()

        if not success and "cancelled" in msg_lower:
            self.log("Transfer cancelled", "warning")
            return

        if not success and ("list failed" in msg_lower or "cannot access" in msg_lower):
            if self.ftp_panel:
                try:
                    self.ftp_panel.prompt_create_shot_folder_if_at_base()
                except RuntimeError:
                    pass
            return

        prefix = "✓" if success else "✗"
        self.log(f"{prefix} {message}", "success" if success else "error")

        if success and any(k in msg_lower for k in ("upload", "delete", "renamed")):
            self._wm.safe_timer(self, self._safe_refresh_ftp, 200)

    def log(self, message: str, level: str = "info", skip_duplicates: bool = True):
        self._wm.log(self, message, level, skip_duplicates)

    def closeEvent(self, event):
        self.log("Closing FTP Manager...", "info")
        self.hide()

        if hasattr(self, "_stats_timer"):
            self._stats_timer.stop()

        if hasattr(self, "_connection_animation"):
            self._connection_animation.abort()

        if self.ftp_manager.is_busy():
            is_connecting = isinstance(
                self.ftp_manager._current_worker, FTPConnectWorker
            )
            if is_connecting:
                self.log("Aborting connection attempt...", "warning")
                self.ftp_manager.abort_connection()
            else:
                self.log("Stopping active transfer...", "warning")
                self.ftp_manager.stop_current_operation()

        if hasattr(self, "_ui_state"):
            self._ui_state.clear()

        event.accept()
        super().closeEvent(event)
