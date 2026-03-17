import threading
from PySide6 import QtCore
from .base_worker import BaseFTPWorker


class FTPDeleteWorker(BaseFTPWorker):
    """Deletes files and/or directories from FTP server recursively.

    Phase 1 (scan): Expands folders into a flat list of (remote_path, is_dir)
    entries and emits files_scanned so the UI can show individual items.

    Phase 2 (delete): Walks the list in reverse (files before parent dirs),
    deleting each item and updating per-file progress.
    """

    files_scanned = QtCore.Signal(list)  # [(remote_path, is_dir), ...]

    def __init__(self, host, user, password, port, remote_files):
        super().__init__(host, user, password, port)
        self.remote_files = remote_files or []
        self._file_progress: dict = {}
        self._file_progress_lock = threading.Lock()

    def get_file_progress(self) -> dict:
        with self._file_progress_lock:
            return dict(self._file_progress)

    def run(self):
        if not self.remote_files:
            self._safe_finish(False, "No files specified for deletion")
            return

        try:
            ftp = self._connect()

            # Phase 1 — scan: expand folders to flat item list.
            self.log("Scanning items to delete...", "info")
            all_items = []  # [(remote_path, is_dir), ...]
            for path in self.remote_files:
                if self.check_cancelled():
                    return
                self._scan_item(ftp, path, all_items)

            if not all_items:
                self._safe_finish(False, "Nothing to delete")
                return

            self.log(f"Found {len(all_items)} items to delete", "info")
            self.files_scanned.emit(all_items)

            # Phase 2 — delete in reverse order (files first, then parent dirs).
            total = len(all_items)
            deleted = 0
            failed = []

            for remote_path, is_dir in reversed(all_items):
                if self.check_cancelled():
                    return
                try:
                    if is_dir:
                        ftp.rmd(remote_path)
                    else:
                        ftp.delete(remote_path)
                    with self._file_progress_lock:
                        self._file_progress[remote_path] = 100
                    deleted += 1
                    self.update_progress(int(deleted / total * 100))
                except Exception as e:
                    err = str(e).lower()
                    if "no such file" in err or "no such file or directory" in err:
                        # Already gone — treat as success.
                        with self._file_progress_lock:
                            self._file_progress[remote_path] = 100
                        deleted += 1
                        self.update_progress(int(deleted / total * 100))
                        continue
                    if is_dir:
                        # Directory not empty — may contain hidden files not returned
                        # by MLSD (e.g. .ftpquota). Purge them and retry rmd().
                        if self._force_rmdir(ftp, remote_path):
                            with self._file_progress_lock:
                                self._file_progress[remote_path] = 100
                            deleted += 1
                            self.update_progress(int(deleted / total * 100))
                            continue
                    name = remote_path.rstrip("/").split("/")[-1]
                    self.log(f"Failed to delete {name}: {e}", "warning")
                    failed.append(remote_path)

            ftp.quit()

            if failed:
                self._safe_finish(
                    False,
                    f"Deleted {deleted}, failed {len(failed)}: {', '.join(failed)}",
                )
            else:
                self._safe_finish(True, f"Deleted {deleted} item(s)")

        except Exception as e:
            self._finish_with_error(f"Delete failed: {e}")

    def _scan_item(self, ftp, remote_path: str, items: list):
        """Recursively scan a path into the items list.

        Files are appended immediately. For directories, children come first,
        then the directory entry — so reversed iteration deletes correctly.
        """
        # ftp.cwd() succeeds on directories, fails on files — most reliable discriminator.
        try:
            ftp.cwd(remote_path)
            ftp.cwd("/")
        except Exception:
            # Can't cd into it — it's a file.
            items.append((remote_path, False))
            return

        # It's a directory — list children.
        try:
            children = self._list_dir(ftp, remote_path)
        except Exception:
            # Can't list — add as empty dir so rmd() can clean it up.
            items.append((remote_path, True))
            return

        for name, is_dir in children:
            if self.check_cancelled():
                return
            child = f"{remote_path.rstrip('/')}/{name}"
            if is_dir:
                self._scan_item(ftp, child, items)
            else:
                items.append((child, False))
        items.append((remote_path, True))

    def _force_rmdir(self, ftp, remote_path: str) -> bool:
        """Purge any remaining contents of a directory (e.g. hidden files not
        returned by MLSD) then remove the directory. Returns True on success."""
        try:
            children = self._list_dir(ftp, remote_path)
        except Exception:
            children = []

        for name, is_dir in children:
            child = f"{remote_path.rstrip('/')}/{name}"
            try:
                if is_dir:
                    self._force_rmdir(ftp, child)
                else:
                    ftp.delete(child)
            except Exception:
                pass

        # Also sweep with NLST which may reveal files MLSD hides.
        try:
            for entry in ftp.nlst(remote_path):
                name = entry.rstrip("/").split("/")[-1]
                if name in (".", ".."):
                    continue
                child = f"{remote_path.rstrip('/')}/{name}"
                try:
                    ftp.delete(child)
                except Exception:
                    try:
                        ftp.rmd(child)
                    except Exception:
                        pass
        except Exception:
            pass

        try:
            ftp.rmd(remote_path)
            return True
        except Exception:
            return False
