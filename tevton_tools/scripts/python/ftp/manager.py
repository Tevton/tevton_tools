import os
import queue
import threading
from PySide6 import QtCore
from typing import Optional, List, Callable

from .ftp_utils import get_ftp_settings, format_size as _format_size
from .workers.connect_worker import FTPConnectWorker
from .workers.list_worker import FTPListWorker
from .workers.upload_worker import FTPUploadWorker
from .workers.download_worker import FTPDownloadWorker
from .workers.delete_worker import FTPDeleteWorker
from .workers.mkdir_worker import FTPMakeDirsWorker
from .workers.rename_worker import FTPRenameWorker


class FTPManager(QtCore.QObject):
    """
    Centralised FTP manager.

    Architecture:
    - Stores only credentials (_creds) and connection flag (_connected).
    - Each worker opens and closes its own FTP connection (fully thread-safe).
    - Only one worker runs at a time, except upload which supports a live queue:
      files/folders can be added mid-session via upload_files() while uploading.

    Signals:
        connection_changed(bool)      - persistent connection state changed
        connection_checked(bool, str) - one-shot validation result (for ProjectWindow)
        busy_changed(bool)            - worker started / stopped
        progress(int)                 - transfer progress 0-100
        status(str, str)              - level, message — all log output
        operation_finished(bool, str) - result of file operations
        files_ready(list)             - directory listing results
        transfer_stats(float,float,float,float) - speed, transferred, total, eta
    """

    connection_changed = QtCore.Signal(bool)
    connection_checked = QtCore.Signal(bool, str)
    progress = QtCore.Signal(int)
    status = QtCore.Signal(str, str)
    operation_finished = QtCore.Signal(bool, str)
    files_ready = QtCore.Signal(list)
    busy_changed = QtCore.Signal(bool)
    transfer_stats = QtCore.Signal(float, float, float, float)
    overwrite_needed = QtCore.Signal(list)  # list of conflicting filenames
    files_scanned = QtCore.Signal(list)    # expanded file list from scan phase

    def __init__(self, parent=None):
        super().__init__(parent)

        self._creds: dict = {}
        self._connected: bool = False
        self._current_worker: Optional[QtCore.QThread] = None

        # Upload session state
        self._upload_queue: Optional[queue.Queue] = None
        self._upload_done_event: Optional[threading.Event] = None
        self._upload_queued_paths: set = set()

    # ------------------------------------------------------------------
    # State
    # ------------------------------------------------------------------

    def is_connected(self) -> bool:
        return self._connected and bool(self._creds)

    def is_busy(self) -> bool:
        return self._current_worker is not None and self._current_worker.isRunning()

    def is_uploading(self) -> bool:
        return (
            isinstance(self._current_worker, FTPUploadWorker)
            and self._current_worker.isRunning()
        )

    # ------------------------------------------------------------------
    # Project
    # ------------------------------------------------------------------

    def set_project(self, project_name: str):
        """Load FTP credentials for project. Resets connection if project changed."""
        try:
            new_creds = get_ftp_settings(project_name)
        except Exception as e:
            self.status.emit("error", f"Failed to load FTP settings: {e}")
            return

        if new_creds == self._creds:
            return

        self._creds = new_creds

        if self._connected:
            self.status.emit("warning", "Project changed — disconnecting.")
            self._set_connected(False)

        self.status.emit("info", f"Project set: {project_name}")

    def set_credentials(self, host: str, user: str, password: str, port: int = 21):
        """Set FTP credentials directly."""
        self._creds = {"host": host, "user": user, "password": password, "port": port}

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------

    def connect_to_server(self) -> bool:
        """Connect and stay connected for file operations."""
        if not self._guard_connect():
            return False

        worker = FTPConnectWorker(**self._creds)
        worker.connection_valid.connect(self._on_connection_valid)
        worker.finished.connect(self._on_connect_finished)
        self._run_connect_worker(worker)
        return True

    def check_connection(self) -> bool:
        """One-shot credential validation."""
        if not self._guard_connect():
            return False

        worker = FTPConnectWorker(**self._creds)
        worker.connection_valid.connect(
            lambda _: self.connection_checked.emit(True, "Connection validated")
        )
        worker.finished.connect(self._on_check_finished)
        self._run_connect_worker(worker)
        return True

    def disconnect_from_server(self):
        self.stop_current_operation()
        self._set_connected(False)

    def abort_connection(self):
        """Immediately abort any ongoing connection attempt."""
        if self._current_worker and self._current_worker.isRunning():
            if isinstance(self._current_worker, FTPConnectWorker):
                self._current_worker.stop()  # closes socket → unblocks ftp.connect()
                self._current_worker.wait(500)
                self._current_worker = None
                self.busy_changed.emit(False)
                self._set_connected(False)
                return True
        return False

    # ------------------------------------------------------------------
    # File operations
    # ------------------------------------------------------------------

    def list_files(self, path: str, callback: Callable = None,
                   fail_callback: Callable = None) -> bool:
        if not self._guard_operation():
            return False

        worker = FTPListWorker(**self._creds, path=path)
        worker.files_ready.connect(self.files_ready)
        if callback:
            worker.files_ready.connect(callback)
        if fail_callback:
            worker.finished.connect(fail_callback)
        self._run_worker(worker)
        return True

    def upload_files(self, entries: List[str]) -> bool:
        """
        Upload files and/or folders to remote_dir, mirroring directory structure.
        If upload already in progress, adds items to the live queue instead.
        """
        if not self._guard_operation():
            return False

        if not entries:
            self.status.emit("warning", "No valid files to upload.")
            return False

        if self.is_uploading():
            return self._add_to_upload_queue(entries)

        self._upload_queue = queue.Queue()
        self._upload_done_event = threading.Event()
        self._upload_queued_paths = set()

        for entry in entries:
            self._upload_queue.put(entry)
            self._upload_queued_paths.add(entry[0])

        total_bytes = sum(os.path.getsize(f) for f, _ in entries)
        self.status.emit(
            "info",
            f"Queued {len(entries)} files ({_format_size(total_bytes)}) for upload.",
        )

        worker = FTPUploadWorker(
            **self._creds,
            upload_queue=self._upload_queue,
            done_event=self._upload_done_event,
            total_bytes=total_bytes,
        )
        self._run_worker(worker)

        # Allow adding more files while upload runs
        self._upload_done_event.set()
        return True

    def download_files(
        self, remote_paths: List[str], local_dir: str, callback: Callable = None
    ) -> bool:
        """Download files and/or directories from FTP."""
        if not self._guard_operation():
            return False

        worker = FTPDownloadWorker(
            **self._creds, remote_paths=remote_paths, local_dir=local_dir
        )
        if callback:
            worker.finished.connect(callback)
        self._run_worker(worker)
        return True

    def delete_files(self, remote_files: List[str], callback: Callable = None) -> bool:
        if not self._guard_operation():
            return False

        worker = FTPDeleteWorker(**self._creds, remote_files=remote_files)
        if callback:
            worker.finished.connect(callback)
        self._run_worker(worker)
        return True

    def create_directories(
        self, remote_dirs: List[str], callback: Callable = None
    ) -> bool:
        if not self._guard_operation():
            return False

        worker = FTPMakeDirsWorker(**self._creds, remote_dirs=remote_dirs)
        if callback:
            worker.finished.connect(callback)
        self._run_worker(worker)
        return True

    def set_overwrite(self, confirmed: bool):
        """Unblock a waiting download worker after user confirms/cancels overwrite."""
        if hasattr(self._current_worker, "set_overwrite"):
            self._current_worker.set_overwrite(confirmed)

    def rename_file(
        self, old_path: str, new_path: str, callback: Callable = None
    ) -> bool:
        if not self._guard_operation():
            return False

        worker = FTPRenameWorker(**self._creds, old_path=old_path, new_path=new_path)
        if callback:
            worker.finished.connect(callback)
        self._run_worker(worker)
        return True

    # ------------------------------------------------------------------
    # Worker lifecycle
    # ------------------------------------------------------------------

    def stop_current_operation(self) -> bool:
        """Stop the currently running worker."""
        if self._current_worker and self._current_worker.isRunning():
            if self._upload_done_event:
                self._upload_done_event.set()
            self._current_worker.stop()
            self._current_worker.wait(2000)
            self._current_worker = None
            self.busy_changed.emit(False)
            return True
        return False

    def _run_connect_worker(self, worker: QtCore.QThread):
        """Run a connection worker."""
        self.stop_current_operation()
        self._current_worker = worker
        self.busy_changed.emit(True)
        worker.finished.connect(
            lambda ok, msg, w=worker: self._on_connect_worker_done(ok, msg, w)
        )
        worker.start()

    def _run_worker(self, worker: QtCore.QThread):
        """Run a file operation worker."""
        self.stop_current_operation()
        self._current_worker = worker
        self.busy_changed.emit(True)

        # Connect signals
        worker.progress.connect(self.progress)
        worker.status.connect(self.status)
        if hasattr(worker, "transfer_stats"):
            worker.transfer_stats.connect(self.transfer_stats)
        if hasattr(worker, "overwrite_needed"):
            worker.overwrite_needed.connect(self.overwrite_needed)
        if hasattr(worker, "files_scanned"):
            worker.files_scanned.connect(self.files_scanned)
        worker.finished.connect(
            lambda ok, msg, w=worker: self._on_operation_finished(ok, msg, w)
        )

        worker.start()

    def _on_connect_worker_done(self, _success: bool, _message: str, worker=None):
        """Cleanup for connection workers."""
        if worker is not None and worker is not self._current_worker:
            return  # Stale signal from cancelled worker
        self._current_worker = None
        self.busy_changed.emit(False)

    def _on_operation_finished(self, success: bool, message: str, worker=None):
        """Completion handler for file operation workers."""
        if worker is not None and worker is not self._current_worker:
            # A new worker was already started inside an operation_finished handler
            # (e.g. sequential move queue). Emit the result but don't clear
            # _current_worker or emit busy_changed(False) — the new worker is active.
            self.operation_finished.emit(success, message)
            return
        self.operation_finished.emit(success, message)
        self._current_worker = None
        self._upload_queue = None
        self._upload_done_event = None
        self.busy_changed.emit(False)

    # ------------------------------------------------------------------
    # Connection signal handlers
    # ------------------------------------------------------------------

    def _on_connection_valid(self, settings: dict):
        self._set_connected(True)
        self.status.emit("success", "FTP connected successfully.")

    def _on_connect_finished(self, success: bool, message: str):
        if not success:
            self.status.emit("error", message)
            self._set_connected(False)

    def _on_check_finished(self, success: bool, message: str):
        if not success:
            self.connection_checked.emit(False, message)

    def _set_connected(self, value: bool):
        if self._connected != value:
            self._connected = value
            self.connection_changed.emit(value)

    # ------------------------------------------------------------------
    # Guards
    # ------------------------------------------------------------------

    def _guard_connect(self) -> bool:
        """Pre-flight check for connection operations."""
        if self.is_busy():
            self.status.emit("warning", "Cannot connect: operation in progress.")
            return False
        if not self._creds:
            self.status.emit("error", "No FTP credentials. Call set_project() first.")
            return False
        return True

    def _guard_operation(self) -> bool:
        """Pre-flight check for file operations."""
        if not self.is_connected():
            self.status.emit("warning", "FTP not connected.")
            return False
        return True

    # ------------------------------------------------------------------
    # Upload queue management
    # ------------------------------------------------------------------

    def _add_to_upload_queue(self, entries: List[tuple]) -> bool:
        """Add entries to a running upload session."""
        if not self._upload_queue or not self._upload_done_event:
            return False

        new_entries = [e for e in entries if e[0] not in self._upload_queued_paths]
        skipped = len(entries) - len(new_entries)

        if not new_entries:
            self.status.emit("warning", "All selected files are already queued.")
            return False

        self._upload_done_event.clear()

        extra_bytes = sum(os.path.getsize(f) for f, _ in new_entries)
        for entry in new_entries:
            self._upload_queue.put(entry)
            self._upload_queued_paths.add(entry[0])

        if isinstance(self._current_worker, FTPUploadWorker):
            self._current_worker.add_total_bytes(extra_bytes)

        msg = f"Added {len(new_entries)} files to upload queue."
        if skipped:
            msg += f" ({skipped} already queued, skipped)"
        self.status.emit("info", msg)
        self._upload_done_event.set()
        return True
