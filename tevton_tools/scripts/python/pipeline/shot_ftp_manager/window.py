import hou
from pathlib import Path
from qt_shim import QtCore, QtUiTools, QtWidgets, QtGui

from ftp import FTPManager
from ui_state import UIStateController
import tvt_utils
from config.config import FTP_SHOT_PATH, FTP_SOURCE_PATH

from .ftp_panel import FTPPanel
from .local_panel import LocalPanel
from .transfer_panel import TransferPanel
from pipeline.window_manager import get_window_manager
from ftp.workers.connect_worker import FTPConnectWorker


FTP_MIME_TYPE = "application/x-tvt-ftp-paths"


class ShotFTPManager(QtWidgets.QMainWindow):
    """
    Per-shot FTP manager window.

    Manages the UI lifecycle (widgets, signals, menus) and delegates all
    transfer logic to TransferPanel, FTP browsing to FTPPanel, and local
    browsing to LocalPanel.
    """

    MAX_LOG_LINES = 100

    # ──────────────────────────────────────────────────────────────────────
    # INIT & UI SETUP
    # ──────────────────────────────────────────────────────────────────────

    def __init__(self, parent=None, project_name=None, shot_name=None, shot_path=None):
        super().__init__(parent)

        self._wm = get_window_manager()

        self.project_name = project_name
        self.shot_name = shot_name
        self.local_shot_path = shot_path
        self.local_root_path = (
            str(Path(shot_path).parent.parent) if shot_path else shot_path
        )
        self.shot_root_ftp_path = FTP_SHOT_PATH.format(shot_name=self.shot_name)
        self.current_ftp_path = self.shot_root_ftp_path
        self.current_ftp_source_path = FTP_SOURCE_PATH.format(shot_name=self.shot_name)
        self.current_local_path = self.local_shot_path

        self.ftp_manager = FTPManager(self)
        self.ftp_manager.set_project(project_name)

        self._ui_state = UIStateController()
        self._stats_timer = QtCore.QTimer(self)
        self._stats_timer.setInterval(200)
        self._stats_timer.timeout.connect(self._on_stats_timer)

        # When True, the next "list failed" operation_finished event will not
        # trigger the "create shot folder?" prompt.  Used to suppress the prompt
        # during background list checks (mode-2 renders, download-source probe).
        self._suppress_list_fail_dialog = False
        self._suppress_op_finished = 0  # counter: >0 means suppress unblock/refresh

        self._connection_animation = tvt_utils.ConnectionAnimator(self)
        self._connection_animation.timeout_reached.connect(self._on_connection_timeout)

        self.ftp_panel: FTPPanel = None
        self.local_panel: LocalPanel = None
        self.transfer_panel: TransferPanel = None

        self._clipboard_paths: list = []
        self._clipboard_source: str = ""  # "local" or "ftp"
        self._ftp_move_queue: list = []

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

        # Upload renders section
        self.upload_text = f(QtWidgets.QLabel, "lb_upload_text")
        self.upload_renders_btn = f(QtWidgets.QPushButton, "pb_renders_upload")
        self.renders_op_mode = f(QtWidgets.QComboBox, "qcb_renders_op_mode")
        self.renders_app_add_files = f(QtWidgets.QCheckBox, "cb_renders_app_add_files")

        # Upload selected section
        self.upload_selected_btn = f(QtWidgets.QPushButton, "pb_upload_selected")
        self.zip_up_files = f(QtWidgets.QCheckBox, "cb_zip_up_files")
        self.del_up_zip = f(QtWidgets.QCheckBox, "cb_del_up_zip")

        # Download section
        self.download_text = f(QtWidgets.QLabel, "lb_download_text")
        self.download_source_btn = f(QtWidgets.QPushButton, "pb_download_source")
        self.download_selected_btn = f(QtWidgets.QPushButton, "pb_download_selected")
        self.unzip_down_files = f(QtWidgets.QCheckBox, "cb_unzip_down_files")
        self.del_zip_down = f(QtWidgets.QCheckBox, "cb_del_zip_down")

        # Operations section
        self.operations_text = f(QtWidgets.QLabel, "lb_operations_text")
        self.new_folder_btn = f(QtWidgets.QPushButton, "pb_new_folder")
        self.delete_selected_btn = f(QtWidgets.QPushButton, "pb_delete_selected")
        self.zip_selected_btn = f(QtWidgets.QPushButton, "pb_zip_selected")

        # Files queue section
        self.files_queue = f(QtWidgets.QListWidget, "lw_files_queue")
        self.files_queue_text = f(QtWidgets.QLabel, "lb_files_queue_text")

        # Status section
        self.status_text = f(QtWidgets.QLabel, "lb_status_text")
        self.reconnect_btn = f(QtWidgets.QPushButton, "pb_reconnect")
        self.con_status_ind = f(QtWidgets.QLabel, "lb_con_status_ind")
        self.con_status = f(QtWidgets.QLabel, "lb_con_status")
        self.progress_bar = f(QtWidgets.QProgressBar, "pbar_progress_bar")
        self.speed_status = f(QtWidgets.QLabel, "lb_speed")
        self.total_status = f(QtWidgets.QLabel, "lb_total")
        self.eta_status = f(QtWidgets.QLabel, "lb_eta")

        # Browser section
        self.log_text = f(QtWidgets.QPlainTextEdit, "qpt_log_text")
        self.ftp_tree = f(QtWidgets.QTreeWidget, "qtw_ftp_list")
        self.ftp_text = f(QtWidgets.QLabel, "lb_ftp")
        self.ftp_back_btn = f(QtWidgets.QPushButton, "pb_ftp_back_path")
        self.ftp_path_edit = f(QtWidgets.QLineEdit, "le_ftp_current_path")
        self.local_text = f(QtWidgets.QLabel, "lb_local")
        self.local_tree = f(QtWidgets.QTreeView, "tv_local_list")
        self.local_back_btn = f(QtWidgets.QPushButton, "pb_local_back_path")
        self.local_path_edit = f(QtWidgets.QLineEdit, "le_local_current_path")

    def _setup_ui(self):
        self.setWindowTitle(f"FTP Manager - {self.shot_name}")
        self.setMinimumSize(1400, 900)
        self.setMaximumSize(2100, 1300)
        self.ftp_path_edit.setText(self.current_ftp_path)
        self.local_path_edit.setText(self.current_local_path or "")
        self.log_text.setMaximumBlockCount(self.MAX_LOG_LINES)
        self.log_text.setReadOnly(True)
        self.log_text.document().setDocumentMargin(2)
        self.con_status.setAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)
        for lbl in (
            self.ftp_text,
            self.local_text,
            self.operations_text,
            self.upload_text,
            self.download_text,
            self.files_queue_text,
            self.status_text,
        ):
            lbl.setAlignment(QtCore.Qt.AlignCenter)

        self.speed_status.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        self.total_status.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        self.eta_status.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        tvt_utils.set_connection_status(self, "disconnected")
        self.delshortcut = QtGui.QShortcut(QtGui.QKeySequence("Delete"), self)
        self.delshortcut.activated.connect(self._delete_selected)
        self.f2shortcut = QtGui.QShortcut(QtGui.QKeySequence("F2"), self)
        self.f2shortcut.activated.connect(self._rename_selected)
        self.copyshortcut = QtGui.QShortcut(QtGui.QKeySequence("Ctrl+C"), self)
        self.copyshortcut.activated.connect(self._copy_selected)
        self.pasteshortcut = QtGui.QShortcut(QtGui.QKeySequence("Ctrl+V"), self)
        self.pasteshortcut.activated.connect(self._paste)
        self.f5shortcut = QtGui.QShortcut(QtGui.QKeySequence("F5"), self)
        self.f5shortcut.activated.connect(self._reconnect_to_ftp)

    def _setup_ftp_tree(self):
        if not self.ftp_tree:
            return
        self.ftp_tree.setColumnWidth(0, 300)
        self.ftp_tree.setColumnWidth(1, 80)
        self.ftp_tree.setColumnWidth(2, 140)
        header = self.ftp_tree.header()
        header.setSectionResizeMode(0, QtWidgets.QHeaderView.Stretch)
        header.setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QtWidgets.QHeaderView.ResizeToContents)

    # ──────────────────────────────────────────────────────────────────────
    # DRAG & DROP
    # ──────────────────────────────────────────────────────────────────────

    def _setup_drag_drop(self):
        self._ftp_drag_start_pos = None
        self._local_drag_start_pos = None
        if self.ftp_tree:
            self.ftp_tree.setAcceptDrops(True)
            self.ftp_tree.setDropIndicatorShown(True)
            self.ftp_tree.viewport().installEventFilter(self)
        if self.local_tree:
            self.local_tree.setAcceptDrops(True)
            self.local_tree.setDropIndicatorShown(True)
            self.local_tree.viewport().installEventFilter(self)

    def eventFilter(self, obj, event):
        etype = event.type()

        if self.ftp_tree and obj is self.ftp_tree.viewport():
            if etype == QtCore.QEvent.MouseButtonPress:
                if event.button() == QtCore.Qt.LeftButton:
                    if self.ftp_tree.indexAt(event.pos()).isValid():
                        self._ftp_drag_start_pos = event.pos()
            elif etype == QtCore.QEvent.MouseButtonRelease:
                self._ftp_drag_start_pos = None
            elif etype == QtCore.QEvent.MouseMove:
                if (
                    self._ftp_drag_start_pos is not None
                    and event.buttons() & QtCore.Qt.LeftButton
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
                            pm = QtGui.QPixmap(24, 24)
                            pm.fill(QtCore.Qt.transparent)
                            icon = self.ftp_tree.style().standardIcon(
                                QtWidgets.QStyle.StandardPixmap.SP_FileIcon
                            )
                            painter = QtGui.QPainter(pm)
                            painter.drawPixmap(0, 0, icon.pixmap(24, 24))
                            painter.end()
                            drag.setPixmap(pm)
                            drag.setHotSpot(QtCore.QPoint(12, 12))
                            drag.exec(QtCore.Qt.CopyAction)
                        return True
            elif etype == QtCore.QEvent.DragEnter:
                if event.mimeData().hasUrls() or event.mimeData().hasFormat(
                    FTP_MIME_TYPE
                ):
                    event.acceptProposedAction()
                    return True
            elif etype == QtCore.QEvent.DragMove:
                if event.mimeData().hasUrls() or event.mimeData().hasFormat(
                    FTP_MIME_TYPE
                ):
                    event.acceptProposedAction()
                    return True
            elif etype == QtCore.QEvent.Drop:
                mime = event.mimeData()
                if mime.hasFormat(FTP_MIME_TYPE):
                    raw = bytes(mime.data(FTP_MIME_TYPE)).decode("utf-8")
                    src_paths = [p for p in raw.splitlines() if p]
                    if src_paths and self.ftp_panel:
                        hit_item = self.ftp_tree.itemAt(event.pos())
                        info = (
                            hit_item.data(0, QtCore.Qt.UserRole) if hit_item else None
                        )
                        if info and info.get("is_dir") and not info.get("is_parent"):
                            target_dir = info["path"].rstrip("/")
                            to_move = [
                                p
                                for p in src_paths
                                if "/".join(p.rstrip("/").split("/")[:-1]) != target_dir
                                and p.rstrip("/") != target_dir
                            ]
                            if to_move:
                                if self.ftp_manager.is_busy():
                                    self.log("Cannot move: FTP busy", "warning")
                                else:
                                    self._ui_state.disable_group("ftp_ops")
                                    self._ui_state.disable_group("transfer")
                                    moves = [
                                        (
                                            p,
                                            f"{target_dir}/{p.rstrip('/').split('/')[-1]}",
                                        )
                                        for p in to_move
                                    ]
                                    self._start_ftp_moves(moves)
                    event.acceptProposedAction()
                    return True
                elif mime.hasUrls():
                    local_paths = [
                        u.toLocalFile() for u in mime.urls() if u.isLocalFile()
                    ]
                    if local_paths and self.transfer_panel:
                        self._ui_state.disable_group("ftp_ops")
                        self.transfer_panel.start_upload(
                            local_paths, self.current_ftp_path, "selected"
                        )
                    event.acceptProposedAction()
                    return True

        if self.local_tree and obj is self.local_tree.viewport():
            if etype == QtCore.QEvent.MouseButtonPress:
                if event.button() == QtCore.Qt.LeftButton:
                    if self.local_tree.indexAt(event.pos()).isValid():
                        self._local_drag_start_pos = event.pos()
            elif etype == QtCore.QEvent.MouseButtonRelease:
                self._local_drag_start_pos = None
            elif etype == QtCore.QEvent.MouseMove:
                if (
                    self._local_drag_start_pos is not None
                    and event.buttons() & QtCore.Qt.LeftButton
                ):
                    dist = (event.pos() - self._local_drag_start_pos).manhattanLength()
                    if dist >= QtWidgets.QApplication.startDragDistance():
                        self._local_drag_start_pos = None
                        paths = (
                            self.local_panel.get_selected_paths()
                            if self.local_panel
                            else []
                        )
                        if paths:
                            mime = QtCore.QMimeData()
                            mime.setUrls([QtCore.QUrl.fromLocalFile(p) for p in paths])
                            drag = QtGui.QDrag(self.local_tree)
                            drag.setMimeData(mime)
                            pm = QtGui.QPixmap(24, 24)
                            pm.fill(QtCore.Qt.transparent)
                            icon = self.local_tree.style().standardIcon(
                                QtWidgets.QStyle.StandardPixmap.SP_FileIcon
                            )
                            painter = QtGui.QPainter(pm)
                            painter.drawPixmap(0, 0, icon.pixmap(24, 24))
                            painter.end()
                            drag.setPixmap(pm)
                            drag.setHotSpot(QtCore.QPoint(12, 12))
                            drag.exec(QtCore.Qt.MoveAction)
                        return True
            elif etype == QtCore.QEvent.DragEnter:
                if (
                    event.mimeData().hasFormat(FTP_MIME_TYPE)
                    or event.mimeData().hasUrls()
                ):
                    event.acceptProposedAction()
                    return True
            elif etype == QtCore.QEvent.DragMove:
                if (
                    event.mimeData().hasFormat(FTP_MIME_TYPE)
                    or event.mimeData().hasUrls()
                ):
                    event.acceptProposedAction()
                    return True
            elif etype == QtCore.QEvent.Drop:
                mime = event.mimeData()
                if mime.hasFormat(FTP_MIME_TYPE):
                    raw = bytes(mime.data(FTP_MIME_TYPE)).decode("utf-8")
                    remote_paths = [p for p in raw.splitlines() if p]
                    if remote_paths and self.transfer_panel:
                        proxy_index = self.local_tree.indexAt(event.pos())
                        if (
                            proxy_index.isValid()
                            and self.local_panel
                            and self.local_panel._proxy
                            and self.local_panel.file_model
                        ):
                            fm_index = self.local_panel._proxy.to_file_model_index(
                                proxy_index
                            )
                            hit_path = self.local_panel.file_model.filePath(fm_index)
                            import os as _os

                            local_dir = (
                                hit_path
                                if _os.path.isdir(hit_path)
                                else _os.path.dirname(hit_path)
                            )
                        else:
                            local_dir = self.current_local_path
                        self._ui_state.disable_group("ftp_ops")
                        self.transfer_panel.start_download(
                            remote_paths, local_dir, "selected"
                        )
                    event.acceptProposedAction()
                    return True
                elif mime.hasUrls():
                    src_paths = [
                        u.toLocalFile() for u in mime.urls() if u.isLocalFile()
                    ]
                    if src_paths and self.local_panel:
                        proxy_index = self.local_tree.indexAt(event.pos())
                        if (
                            proxy_index.isValid()
                            and self.local_panel._proxy
                            and self.local_panel.file_model
                        ):
                            fm_index = self.local_panel._proxy.to_file_model_index(
                                proxy_index
                            )
                            hit = self.local_panel.file_model.filePath(fm_index)
                            import os as _os

                            target_dir = (
                                hit if _os.path.isdir(hit) else _os.path.dirname(hit)
                            )
                        else:
                            target_dir = self.current_local_path
                        if target_dir:
                            self.local_panel.move_files(src_paths, target_dir)
                    event.acceptProposedAction()
                    return True

        return super().eventFilter(obj, event)

    # ──────────────────────────────────────────────────────────────────────
    # WIDGET REGISTRATION & SIGNALS
    # ──────────────────────────────────────────────────────────────────────

    def _register_widgets(self):
        # Always-free: never touched by operation blocking.
        self._ui_state.register_many(
            {
                "reconnect_btn": self.reconnect_btn,
                "local_tree": self.local_tree,
                "local_back_btn": self.local_back_btn,
                "local_path_edit": self.local_path_edit,
            }
        )
        # Blocked during ANY FTP operation.
        self._ui_state.register_many(
            {
                "ftp_back_btn": self.ftp_back_btn,
                "ftp_path_edit": self.ftp_path_edit,
                "new_folder_btn": self.new_folder_btn,
                "delete_selected_btn": self.delete_selected_btn,
                "zip_selected_btn": self.zip_selected_btn,
            },
            groups=["ftp_ops"],
        )
        # Blocked during non-transfer ops; managed individually during transfers.
        self._ui_state.register_many(
            {
                "upload_renders_btn": self.upload_renders_btn,
                "upload_selected_btn": self.upload_selected_btn,
                "download_source_btn": self.download_source_btn,
                "download_selected_btn": self.download_selected_btn,
            },
            groups=["transfer"],
        )
        self._ui_state.disable_group("ftp_ops")
        self._ui_state.disable_group("transfer")
        if self.reconnect_btn:
            self.reconnect_btn.setEnabled(False)

        # Temp disable, waiting until functionality be done
        if self.unzip_down_files:
            self.unzip_down_files.setEnabled(False)
        if self.del_zip_down:
            self.del_zip_down.setEnabled(False)

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
        if self.zip_selected_btn:
            self.zip_selected_btn.clicked.connect(self._zip_selected)
        if self.upload_renders_btn:
            self.upload_renders_btn.clicked.connect(self._upload_renders)
        if self.download_source_btn:
            self.download_source_btn.clicked.connect(self._download_source)

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

    # ──────────────────────────────────────────────────────────────────────
    # FTP CONNECTION
    # ──────────────────────────────────────────────────────────────────────

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
            self._ui_state.enable_group("ftp_ops")
            self._ui_state.enable_group("transfer")
            if self.reconnect_btn:
                self.reconnect_btn.setEnabled(True)
            self._wm.safe_timer(self, self._safe_refresh_ftp, 100)
        else:
            self.log("Disconnected", "warning")
            self._ui_state.disable_group("ftp_ops")
            self._ui_state.disable_group("transfer")
            if self.reconnect_btn:
                self.reconnect_btn.setEnabled(True)
            if self.ftp_tree:
                try:
                    self.ftp_tree.clear()
                except RuntimeError:
                    pass

    def _on_connection_timeout(self):
        self._ui_state.enable_group("ftp_ops")
        self._ui_state.enable_group("transfer")

    # ──────────────────────────────────────────────────────────────────────
    # FTP NAVIGATION
    # ──────────────────────────────────────────────────────────────────────

    def _safe_refresh_ftp(self):
        """Refresh the FTP tree, retrying after 500 ms if the manager is still busy."""
        if self.ftp_manager.is_busy():
            self._wm.safe_timer(self, self._safe_refresh_ftp, 500)
            return
        if self.ftp_panel:
            try:
                self.ftp_panel.refresh()
                self.log("FTP list refreshed", "info")
            except RuntimeError:
                pass

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

    # ──────────────────────────────────────────────────────────────────────
    # LOCAL NAVIGATION
    # ──────────────────────────────────────────────────────────────────────

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

    # ──────────────────────────────────────────────────────────────────────
    # TRANSFER ACTIONS
    # ──────────────────────────────────────────────────────────────────────

    def _upload_selected(self):
        if self.transfer_panel:
            try:
                self.transfer_panel.start_upload_selected()
            except RuntimeError:
                pass

    def _upload_renders(self):
        if self.transfer_panel:
            try:
                self.transfer_panel.start_upload_renders()
            except RuntimeError:
                pass

    def _download_selected(self):
        if self.transfer_panel:
            try:
                self.transfer_panel.start_download_selected()
            except RuntimeError:
                pass

    def _download_source(self):
        if self.transfer_panel:
            try:
                self.transfer_panel.start_download_source()
            except RuntimeError:
                pass

    # ──────────────────────────────────────────────────────────────────────
    # FILE & FOLDER OPERATIONS
    # ──────────────────────────────────────────────────────────────────────

    def _start_ftp_moves(self, moves: list):
        """Start a sequential FTP move queue. moves: list of (old_path, new_path)."""
        self._ftp_move_queue = list(moves)
        self._ftp_move_total = len(moves)
        if self.transfer_panel:
            self._ftp_move_op_id = f"mv_{id(self)}"
            move_paths = [m[0] for m in moves]
            self.transfer_panel._queue_add(
                self._ftp_move_op_id,
                "Move",
                move_paths,
                QtGui.QColor(160, 130, 40),
                progress=50,
            )
        self._drain_ftp_move_queue(True, "")

    def _drain_ftp_move_queue(self, success: bool, message: str):
        """Execute the next item in _ftp_move_queue, or finish when empty."""
        if not success:
            self.log(f"Move failed: {message}", "error")
            self._ftp_move_queue.clear()
            if self.transfer_panel and hasattr(self, "_ftp_move_op_id"):
                self.transfer_panel._queue_remove(self._ftp_move_op_id)
            self._ui_state.enable_group("ftp_ops")
            self._ui_state.enable_group("transfer")
            self._wm.safe_timer(self, self._safe_refresh_ftp, 200)
            return
        if not self._ftp_move_queue:
            if self.transfer_panel and hasattr(self, "_ftp_move_op_id"):
                self.transfer_panel._queue_remove(self._ftp_move_op_id)
            self._ui_state.enable_group("ftp_ops")
            self._ui_state.enable_group("transfer")
            self._wm.safe_timer(self, self._safe_refresh_ftp, 200)
            return
        old_path, new_path = self._ftp_move_queue.pop(0)
        done = self._ftp_move_total - len(self._ftp_move_queue)
        self.log(
            f"Moving {done}/{self._ftp_move_total}: {old_path.rstrip('/').split('/')[-1]}",
            "info",
        )
        self._wm.safe_connect_once(
            self.ftp_manager.operation_finished, self._drain_ftp_move_queue, self
        )
        self.ftp_manager.rename_file(old_path, new_path)

    def _on_new_folder_clicked(self):
        if self.transfer_panel:
            try:
                self.transfer_panel.create_ftp_folder(self.current_ftp_path)
            except RuntimeError:
                pass

    def _on_local_new_folder_clicked(self):
        if self.transfer_panel:
            try:
                self.transfer_panel.create_local_folder(self.current_local_path)
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

    def _zip_selected(self):
        if not self.transfer_panel:
            return
        try:
            local_paths = (
                self.local_panel.get_selected_paths() if self.local_panel else []
            )
            archive_name = self._wm.show_input_field_dialog(
                self,
                "Create New Archive",
                self.shot_name,
                [("Create", True), ("Cancel", False)],
                icon=QtWidgets.QMessageBox.Question,
            )
            self.transfer_panel.create_archive(local_paths, archive_name)
        except RuntimeError:
            pass

    def _copy_selected(self):
        try:
            ftp_paths = self.ftp_panel.get_selected_paths() if self.ftp_panel else []
            local_paths = (
                self.local_panel.get_selected_paths() if self.local_panel else []
            )
            if ftp_paths:
                self._clipboard_paths = ftp_paths
                self._clipboard_source = "ftp"
                self.log(f"Copied {len(ftp_paths)} FTP item(s)", "info")
            elif local_paths:
                self._clipboard_paths = local_paths
                self._clipboard_source = "local"
                self.log(f"Copied {len(local_paths)} local item(s)", "info")
            else:
                self.log("No items selected to copy", "warning")
        except RuntimeError:
            pass

    def _paste_to_ftp(self):
        if not self._clipboard_paths:
            self.log("Clipboard is empty", "warning")
            return
        if self.transfer_panel:
            try:
                self.transfer_panel.paste(
                    self._clipboard_paths, self._clipboard_source, dest="ftp"
                )
            except RuntimeError:
                pass

    def _paste_to_local(self):
        if not self._clipboard_paths:
            self.log("Clipboard is empty", "warning")
            return
        if self.transfer_panel:
            try:
                self.transfer_panel.paste(
                    self._clipboard_paths, self._clipboard_source, dest="local"
                )
            except RuntimeError:
                pass

    def _paste(self):
        """Ctrl+V: paste to FTP if ftp_tree has focus, else paste to local."""
        if not self._clipboard_paths:
            self.log("Clipboard is empty", "warning")
            return
        ftp_focused = self.ftp_tree and (
            self.ftp_tree.hasFocus() or self.ftp_tree.viewport().hasFocus()
        )
        if ftp_focused:
            self._paste_to_ftp()
        else:
            self._paste_to_local()

    # ──────────────────────────────────────────────────────────────────────
    # FTP SIGNAL HANDLERS
    # ──────────────────────────────────────────────────────────────────────

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

    def _on_status(self, level: str, message: str):
        self.log(message, level)

    def _on_operation_finished(self, success: bool, message: str):
        """Central handler for every ftp_manager.operation_finished signal.

        Restores transfer buttons, unblocks navigation, and handles special
        failure cases (cancelled, list-failed → shot-folder prompt).
        """
        suppress = self._suppress_op_finished > 0
        if suppress:
            self._suppress_op_finished -= 1

        if not suppress and not self.ftp_manager.is_busy():
            if self.transfer_panel:
                try:
                    self.transfer_panel.restore_button()
                except RuntimeError:
                    pass
            self._ui_state.enable_group("ftp_ops")
            self._ui_state.enable_group("transfer")

        msg_lower = message.lower()

        if not success and "cancelled" in msg_lower:
            self.log("Transfer cancelled", "warning")
            return

        if not success and ("list failed" in msg_lower or "cannot access" in msg_lower):
            suppress_list = self._suppress_list_fail_dialog
            self._suppress_list_fail_dialog = False
            if not suppress_list and self.ftp_panel:
                try:
                    self.ftp_panel.prompt_create_shot_folder_if_at_base()
                except RuntimeError:
                    pass
            return

        # Upload/download completions are logged by transfer_panel callbacks.
        if not any(k in msg_lower for k in ("uploaded", "downloaded")):
            self.log(message, "success" if success else "error")

        if (
            not suppress
            and success
            and not self.ftp_manager.is_busy()
            and any(k in msg_lower for k in ("upload", "delete"))
        ):
            self._wm.safe_timer(self, self._safe_refresh_ftp, 200)

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

    # ──────────────────────────────────────────────────────────────────────
    # CONTEXT MENUS
    # ──────────────────────────────────────────────────────────────────────

    def _show_ftp_menu(self, tree, pos):
        item = tree.itemAt(pos)
        menu = QtWidgets.QMenu(tree)
        menu.setFont(self._wm.menu_font(font_size=8))
        if item:
            info = item.data(0, QtCore.Qt.UserRole)
            if info and not info.get("is_parent"):
                menu.addAction("Download").triggered.connect(self._download_selected)
                menu.addAction("Copy").triggered.connect(self._copy_selected)
                copy_url_action = menu.addAction("Copy URL")
                copy_url_action.triggered.connect(
                    lambda: self._copy_ftp_url(info.get("path", ""))
                )
                menu.addAction("Rename").triggered.connect(self._rename_selected)
                menu.addAction("Delete").triggered.connect(self._delete_selected)
                menu.addSeparator()
        copy_dir_url_action = menu.addAction("Copy Directory URL")
        copy_dir_url_action.triggered.connect(
            lambda: self._copy_ftp_url(self.current_ftp_path)
        )
        menu.addAction("Paste here").triggered.connect(self._paste_to_ftp)
        menu.addAction("New Folder").triggered.connect(self._on_new_folder_clicked)
        menu.exec(tree.viewport().mapToGlobal(pos))

    def _show_local_menu(self, tree, pos):
        index = tree.indexAt(pos)
        menu = QtWidgets.QMenu(tree)
        menu.setFont(self._wm.menu_font(font_size=8))
        if index.isValid():
            menu.addAction("Upload").triggered.connect(self._upload_selected)
            menu.addAction("Copy").triggered.connect(self._copy_selected)
            menu.addAction("Rename").triggered.connect(self._rename_selected)
            menu.addAction("Delete").triggered.connect(self._delete_selected)
            menu.addAction("Zip").triggered.connect(self._zip_selected)
            menu.addSeparator()
        menu.addAction("Paste here").triggered.connect(self._paste_to_local)
        menu.addAction("New Folder").triggered.connect(
            self._on_local_new_folder_clicked
        )
        menu.exec(tree.viewport().mapToGlobal(pos))

    # ──────────────────────────────────────────────────────────────────────
    # UTILITIES
    # ──────────────────────────────────────────────────────────────────────

    def _copy_ftp_url(self, path: str):
        """Build an FTP URL from current credentials + path and copy it to clipboard."""
        creds = self.ftp_manager.credentials
        if not creds or not creds.get("host"):
            self.log("No FTP credentials available", "warning")
            return
        scheme = "ftpes" if creds.get("use_tls") else "ftp"
        host = creds["host"]
        port = creds.get("port", 21)
        user = creds.get("user", "")
        ftp_path = ("/" + path.lstrip("/")) if path else "/"
        url = f"{scheme}://{user}@{host}:{port}{ftp_path}"
        QtWidgets.QApplication.clipboard().setText(url)
        self.log(f"URL copied: {url}", "info")

    def log(self, message: str, level: str = "info", skip_duplicates: bool = False):
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
