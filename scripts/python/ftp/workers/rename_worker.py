from .base_worker import BaseFTPWorker


class FTPRenameWorker(BaseFTPWorker):
    """Renames a file or directory on the FTP server."""

    def __init__(self, host, user, password, port, old_path, new_path):
        super().__init__(host, user, password, port)
        self.old_path = old_path
        self.new_path = new_path

    def run(self):
        if not self.old_path or not self.new_path:
            self._safe_finish(False, "Rename failed: missing path")
            return

        try:
            ftp = self._connect()
            ftp.rename(self.old_path, self.new_path)
            ftp.quit()
            name = self.new_path.split("/")[-1]
            self._safe_finish(True, f"Renamed to: {name}")
        except Exception as e:
            self._finish_with_error(f"Rename failed: {e}")
