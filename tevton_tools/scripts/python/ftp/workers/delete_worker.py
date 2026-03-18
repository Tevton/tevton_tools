from qt_shim import QtCore
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
                    if is_dir or "not a directory" in err:
                        # Directory not empty (or scanned as file but actually a dir).
                        # Purge contents and retry rmd().
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
        """Recursively purge a directory tree via NLST and remove it.

        Used as a fallback when rmd() fails with "Directory not empty" — handles
        files that were invisible to MLSD during the scan phase (e.g. hidden files,
        or directories the server refused to list until cwd'd into them).
        Returns True on success.
        """
        # Collect entries via absolute-path NLST, then relative NLST from inside.
        entries = set()
        for listing_path in (remote_path, "."):
            if listing_path == ".":
                try:
                    ftp.cwd(remote_path)
                except Exception:
                    break
            try:
                raw = ftp.nlst(listing_path)
            except Exception:
                raw = []
            if listing_path == ".":
                try:
                    ftp.cwd("/")
                except Exception:
                    pass
            for entry in raw:
                name = entry.rstrip("/").split("/")[-1]
                if name and name not in (".", ".."):
                    entries.add(f"{remote_path.rstrip('/')}/{name}")
            if entries:
                break  # absolute NLST was sufficient

        for child in entries:
            try:
                ftp.delete(child)
            except Exception:
                # Might be a subdirectory — recurse.
                self._force_rmdir(ftp, child)

        try:
            ftp.rmd(remote_path)
            return True
        except Exception as e:
            self.log(f"Failed to remove directory {remote_path.split('/')[-1]}: {e}", "warning")
            return False
