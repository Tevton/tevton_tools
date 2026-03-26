import queue
import threading
from qt_shim import QtCore
from .base_worker import BaseFTPWorker


class FTPDeleteWorker(BaseFTPWorker):
    """Deletes files and/or directories from FTP server recursively.

    Phase 1 (scan): Expands folders into a flat list of (remote_path, is_dir)
    entries and emits files_scanned so the UI can show individual items.

    Phase 2a (delete files): Files are deleted in parallel using MAX_CONNECTIONS
    threads, each with its own FTP connection.

    Phase 2b (delete dirs): Directories are deleted serially in reverse order
    (deepest first) to ensure parents are removed after their children.
    """

    files_scanned = QtCore.Signal(list)  # [(remote_path, is_dir), ...]

    MAX_CONNECTIONS = 4

    def __init__(self, host, user, password, port, remote_files, use_tls=False):
        super().__init__(host, user, password, port, use_tls=use_tls)
        self.remote_files = remote_files or []
        self._active_ftp_connections: list = []
        self._connections_lock = threading.Lock()
        self._deleted = 0
        self._count_lock = threading.Lock()

    def stop(self):
        """Cancel: set flag and force-close all active FTP sockets immediately."""
        self._is_running = False
        with self._connections_lock:
            for ftp in self._active_ftp_connections:
                try:
                    ftp.close()
                except Exception:
                    pass
            self._active_ftp_connections.clear()
        self._disconnect()

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

            # Phase 2 — split into files (parallelisable) and dirs (serial).
            self.log(f"Deleting {len(all_items)} item(s)...", "info")
            files = [p for p, is_dir in all_items if not is_dir]
            dirs = [p for p, is_dir in all_items if is_dir]

            total = len(all_items)
            self._deleted = 0
            failed = []

            # Phase 2a — delete files in parallel.
            file_queue = queue.Queue()
            for path in files:
                file_queue.put(path)

            def _delete_loop():
                try:
                    conn = self._make_connection()
                except Exception:
                    return
                with self._connections_lock:
                    self._active_ftp_connections.append(conn)
                try:
                    while True:
                        try:
                            remote_path = file_queue.get_nowait()
                        except queue.Empty:
                            break
                        if not self._is_running:
                            break
                        try:
                            conn.delete(remote_path)
                            with self._file_progress_lock:
                                self._file_progress[remote_path] = 100
                        except Exception as e:
                            err = str(e).lower()
                            if (
                                "no such file" in err
                                or "no such file or directory" in err
                            ):
                                pass
                            else:
                                name = remote_path.rstrip("/").split("/")[-1]
                                self.log(f"Failed to delete {name}: {e}", "warning")
                                with self._count_lock:
                                    failed.append(remote_path)
                                continue
                        with self._count_lock:
                            self._deleted += 1
                            pct = int(self._deleted / total * 100)
                        self.update_progress(pct)
                finally:
                    with self._connections_lock:
                        try:
                            self._active_ftp_connections.remove(conn)
                        except ValueError:
                            pass
                    try:
                        conn.quit()
                    except Exception:
                        pass

            n_threads = min(self.MAX_CONNECTIONS, len(files)) if files else 0
            threads = [
                threading.Thread(
                    target=_delete_loop, daemon=True, name=f"DeleteThread-{i}"
                )
                for i in range(n_threads)
            ]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

            if not self._is_running:
                self._safe_finish(False, "Operation cancelled")
                return

            # Phase 2b — delete directories serially, deepest first.
            for remote_path in reversed(dirs):
                if not self._is_running:
                    self._safe_finish(False, "Operation cancelled")
                    return
                try:
                    ftp.rmd(remote_path)
                    self._deleted += 1
                    self.update_progress(int(self._deleted / total * 100))
                except Exception as e:
                    err = str(e).lower()
                    if "no such file" in err or "no such file or directory" in err:
                        self._deleted += 1
                        continue
                    if self._force_rmdir(ftp, remote_path):
                        self._deleted += 1
                        self.update_progress(int(self._deleted / total * 100))
                        continue
                    name = remote_path.rstrip("/").split("/")[-1]
                    self.log(f"Failed to remove directory {name}: {e}", "warning")
                    failed.append(remote_path)

            ftp.quit()

            if failed:
                self._safe_finish(
                    False,
                    f"Deleted {self._deleted}, failed {len(failed)}: {', '.join(failed)}",
                )
            else:
                self._safe_finish(True, f"Deleted {self._deleted} item(s)")

        except Exception as e:
            self._finish_with_error(f"Delete failed: {e}")

    def _scan_item(self, ftp, remote_path: str, items: list):
        """Recursively scan a path into the items list.

        Files are appended immediately. For directories, children come first,
        then the directory entry — so reversed iteration deletes correctly.

        Uses _list_dir (MLSD/LIST) to detect directories instead of ftp.cwd(),
        avoiding 2 extra round-trips per item.
        """
        try:
            children = self._list_dir(ftp, remote_path)
        except Exception:
            items.append((remote_path, False))
            return

        # Guard: some FTP servers return the file itself as the sole MLSD entry.
        basename = remote_path.rstrip("/").split("/")[-1]
        if len(children) == 1 and not children[0][1] and children[0][0] == basename:
            items.append((remote_path, False))
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
            self.log(
                f"Failed to remove directory {remote_path.split('/')[-1]}: {e}",
                "warning",
            )
            return False
