from .base_worker import BaseFTPWorker


class FTPDeleteWorker(BaseFTPWorker):
    """Deletes files and/or directories from FTP server recursively."""

    def __init__(self, host, user, password, port, remote_files):
        super().__init__(host, user, password, port)
        self.remote_files = remote_files or []

    def run(self):
        if not self.remote_files:
            self._safe_finish(False, "No files specified for deletion")
            return

        try:
            ftp = self._connect()
            deleted = 0
            failed = []

            for remote_path in self.remote_files:
                if self.check_cancelled():
                    return
                try:
                    is_dir, file_count = self._delete_item(ftp, remote_path)
                    deleted += file_count
                    kind = "folder" if is_dir else "file"
                    self.log(f"Deleted {kind}: {remote_path.split('/')[-1]}", "info")
                except Exception as e:
                    self.log(f"Failed to delete {remote_path.split('/')[-1]}: {e}", "warning")
                    failed.append(remote_path)

            ftp.quit()

            if failed:
                self._safe_finish(False, f"Deleted {deleted}, failed {len(failed)}: {', '.join(failed)}")
            else:
                self._safe_finish(True, f"Deleted {deleted} file(s)")

        except Exception as e:
            self._finish_with_error(f"Delete failed: {e}")

    def _delete_item(self, ftp, remote_path: str) -> tuple:
        """Delete a file or directory. Returns (is_dir, file_count)."""
        try:
            ftp.delete(remote_path)
            return False, 1
        except Exception:
            count = self._rmdir_recursive(ftp, remote_path)
            return True, count

    def _rmdir_recursive(self, ftp, remote_path: str) -> int:
        """Recursively delete a directory. Returns total files deleted."""
        total = 0
        for name, is_dir in self._list_dir(ftp, remote_path):
            if self.check_cancelled():
                return total
            child = f"{remote_path.rstrip('/')}/{name}"
            if is_dir:
                total += self._rmdir_recursive(ftp, child)
            else:
                ftp.delete(child)
                total += 1
        ftp.rmd(remote_path)
        return total
