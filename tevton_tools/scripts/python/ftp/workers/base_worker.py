import ssl
import threading
from qt_shim import QtCore
from ftplib import FTP, FTP_TLS


class BaseFTPWorker(QtCore.QThread):
    """
    Base class for all FTP workers.

    Workers are responsible only for executing their operation and reporting
    the result via finished(success, message). Error handling and display
    is the manager's responsibility.
    """

    CHUNK_SIZE = 1024 * 1024

    progress = QtCore.Signal(int)
    status = QtCore.Signal(str, str)  # level, message — for non-fatal per-file warnings
    finished = QtCore.Signal(bool, str)  # success, message — sole completion signal

    def __init__(self, host=None, user=None, password=None, port=21, use_tls=False):
        super().__init__()
        self.host = host
        self.user = user
        self.password = password
        self.port = port
        self.use_tls = use_tls
        self.ftp = None
        self._is_running = True
        self._finished_emitted = False
        self._ui_state = None
        self._file_progress: dict = {}
        self._file_progress_lock = threading.Lock()

    def get_file_progress(self) -> dict:
        with self._file_progress_lock:
            return dict(self._file_progress)

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------

    def _make_connection(self) -> FTP:
        """Open and return a new FTP connection (does not store in self.ftp)."""
        if self.use_tls:
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            ftp = FTP_TLS(context=ctx)
            ftp.connect(self.host, self.port, timeout=10)
            ftp.login(self.user, self.password)
            ftp.prot_p()
        else:
            ftp = FTP()
            ftp.connect(self.host, self.port, timeout=10)
            ftp.login(self.user, self.password)
        ftp.set_pasv(True)
        return ftp

    def _connect(self) -> FTP:
        """Open FTP connection and store in self.ftp."""
        try:
            ftp = self._make_connection()
            self.ftp = ftp
            return ftp
        except Exception as e:
            self._disconnect()
            raise ConnectionError(f"FTP connection failed: {e}")

    def _disconnect(self):
        """Close connection if open."""
        if self.ftp:
            try:
                self.ftp.quit()
            except Exception:
                try:
                    self.ftp.close()
                except Exception:
                    pass
            finally:
                self.ftp = None

    # ------------------------------------------------------------------
    # Execution control
    # ------------------------------------------------------------------

    def stop(self):
        self._is_running = False
        self._disconnect()

    def check_cancelled(self) -> bool:
        """Return True and finish cleanly if operation was cancelled."""
        if not self._is_running:
            self._safe_finish(False, "Operation cancelled")
            return True
        return False

    # ------------------------------------------------------------------
    # Completion
    # ------------------------------------------------------------------

    def _safe_finish(self, success: bool, message: str):
        """Emit finished exactly once."""
        if not self._finished_emitted:
            self._finished_emitted = True
            try:
                self.finished.emit(success, message)
            except RuntimeError:
                pass

    def _finish_with_error(self, message: str):
        """Finish with failure."""
        self._safe_finish(False, message)

    # ------------------------------------------------------------------
    # UI State
    # ------------------------------------------------------------------

    def set_ui_state(self, ui_state):
        """Set UIStateController for window state checking."""
        self._ui_state = ui_state

    def is_window_valid(self) -> bool:
        """Check if window is still valid."""
        if self._ui_state:
            return not self._ui_state.is_window_closing()
        return True

    # ------------------------------------------------------------------
    # Logging and progress
    # ------------------------------------------------------------------

    def log(self, message: str, level: str = "info"):
        """Emit non-fatal status message."""
        if self.is_window_valid():
            try:
                self.status.emit(level, message)
            except RuntimeError:
                pass

    def update_progress(self, percent: int):
        """Emit progress update if window is still valid."""
        if self.is_window_valid():
            try:
                self.progress.emit(max(0, min(100, percent)))
            except RuntimeError:
                pass

    # ------------------------------------------------------------------
    # Directory listing helper (for workers that need it)
    # ------------------------------------------------------------------

    def _list_dir(self, ftp, path: str) -> list:
        """
        Return list of (name, is_dir) for a remote directory.
        Tries MLSD first, falls back to LIST.
        Names are always returned as plain basenames (some servers send full paths).
        """
        try:
            result = []
            for name, attrs in ftp.mlsd(path):
                name = name.rstrip("/").split("/")[-1]
                if name in (".", "..") or not name:
                    continue
                result.append((name, "dir" in attrs.get("type", "").lower()))
            return result
        except Exception:
            lines = []
            ftp.dir(path, lines.append)
            result = []
            for line in lines:
                parts = line.split()
                if len(parts) < 9:
                    continue
                name = " ".join(parts[8:])
                name = name.rstrip("/").split("/")[-1]
                if name not in (".", "..") and name:
                    result.append((name, parts[0].startswith("d")))
            return result

    # ------------------------------------------------------------------
    # Subclasses must implement run()
    # ------------------------------------------------------------------

    def run(self):
        raise NotImplementedError(f"{self.__class__.__name__} must implement run()")
