from ftplib import all_errors
from .base_worker import BaseFTPWorker
from qt_shim import QtCore


class FTPConnectWorker(BaseFTPWorker):
    """
    Validates FTP credentials by opening and immediately closing a test connection.
    Emits connection_valid with credentials on success.
    On failure, emits finished(False, message) — manager handles the error display.
    """

    connection_valid = QtCore.Signal(dict)

    def __init__(self, host: str, user: str, password: str, port: int = 21):
        super().__init__(
            host=str(host) if host else "",
            user=str(user) if user else "",
            password=str(password) if password else "",
            port=int(port) if port else 21,
        )

    def run(self):
        if not self.host:
            self._finish_with_error("Host is empty")
            return
        self.log(f"Connecting to {self.host}:{self.port}...", "info")

        try:
            self._connect()
            self._disconnect()
            self.connection_valid.emit(
                {
                    "host": self.host,
                    "user": self.user,
                    "password": self.password,
                    "port": self.port,
                }
            )
            self._safe_finish(True, "Connection validated")
        except all_errors as e:
            self._finish_with_error(f"Connection failed: {e}")
        except Exception as e:
            self._finish_with_error(f"Connection error: {e}")
