import os
import time
import queue
import threading
from collections import deque
from PySide6 import QtCore
from .base_transfer_worker import BaseTransferWorker, _format_size


class FTPDownloadWorker(BaseTransferWorker):
    """
    Downloads files and/or directories from FTP to a local directory.

    Phase 1 (scan): Recursively expands remote directories into a flat list of
    (remote_file, local_target_dir, size) tuples. Size is captured from MLSD
    during scan — no separate ftp.size() round-trips needed. Single connection.

    Phase 1.5 (conflict check): If any local files would be overwritten, emits
    overwrite_needed(list[str]) and waits for set_overwrite() to unblock.

    Phase 2 (download): Up to MAX_CONNECTIONS threads pull from a shared queue,
    each with its own FTP connection. _write_chunk only updates counters — all
    signal emission happens via emit_stats_if_due() called from the QTimer on
    the main thread, avoiding unsafe cross-thread signal emission from raw threads.
    """

    overwrite_needed = QtCore.Signal(list)  # list of conflicting filenames
    files_scanned = QtCore.Signal(list)    # list of (remote_file, local_dir, size)
    MAX_CONNECTIONS = 4
    SPEED_WINDOW = 5.0  # seconds for rolling speed average

    def __init__(self, host, user, password, port, remote_paths, local_dir):
        super().__init__(host, user, password, port)
        self.remote_paths = remote_paths or []
        self.local_dir = local_dir
        self._overwrite_event = threading.Event()
        self._overwrite_confirmed = False
        self._active_ftp_connections: list = []
        self._connections_lock = threading.Lock()
        self._speed_samples: deque = deque()
        self._stats_lock = threading.Lock()
        self._start_time: float = 0.0
        self._last_stats_time: float = 0.0
        self._transferred: int = 0
        self._downloaded: int = 0
        self._download_lock = threading.Lock()
        self._errors: list = []
        self._log_queue: queue.Queue = queue.Queue()
    def set_overwrite(self, confirmed: bool):
        """Called from the main thread to unblock the worker after user confirms."""
        self._overwrite_confirmed = confirmed
        self._overwrite_event.set()

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
        # Unblock overwrite wait if pending
        self._overwrite_confirmed = False
        self._overwrite_event.set()

    def run(self):
        if not self.remote_paths:
            self._safe_finish(False, "No files to download")
            return

        try:
            ftp = self._connect()

            # Phase 1 — scan: expand dirs, collect (remote_file, local_dir, size).
            self.log("Scanning remote paths...", "info")
            entries = []

            for remote_path in self.remote_paths:
                if self.check_cancelled():
                    return
                self._scan_path(ftp, remote_path, self.local_dir, entries)

            if not entries:
                self._safe_finish(False, "No files found to download")
                return

            self.total_bytes = sum(size for _, _, size in entries)
            if self.total_bytes <= 0:
                self.log(
                    "Could not determine total size, proceeding without ETA", "warning"
                )
                self.total_bytes = 0

            self.log(
                f"Found {len(entries)} files, total: {_format_size(self.total_bytes)}",
                "info",
            )
            self.files_scanned.emit(entries)

            # Phase 1.5 — conflict check.
            conflicts = [
                os.path.join(local_dir, os.path.basename(remote_file))
                for remote_file, local_dir, _ in entries
                if os.path.exists(
                    os.path.join(local_dir, os.path.basename(remote_file))
                )
            ]
            if conflicts:
                conflict_names = [os.path.basename(p) for p in conflicts]
                self.overwrite_needed.emit(conflict_names)
                self._overwrite_event.wait()
                if not self._overwrite_confirmed:
                    self._safe_finish(False, "Download cancelled")
                    return

            # Close scan connection before spawning parallel threads.
            try:
                ftp.quit()
            except Exception:
                pass

            if not self._is_running:
                self._safe_finish(False, "Operation cancelled")
                return

            # Phase 2 — parallel download.
            download_queue = queue.Queue()
            for entry in entries:
                download_queue.put(entry)

            self._start_time = time.time()
            self._last_stats_time = self._start_time
            self._transferred = 0
            self._downloaded = 0
            self._errors = []

            threads = []
            for _ in range(self.MAX_CONNECTIONS):
                t = threading.Thread(
                    target=self._download_loop,
                    args=(download_queue,),
                    daemon=True,
                )
                t.start()
                threads.append(t)

            for t in threads:
                t.join()

            if not self._is_running:
                return  # Cancelled — manager already handled cleanup

            self.update_progress(100)
            if self._errors:
                self.log(
                    f"{len(self._errors)} connection(s) failed: {self._errors[0]}",
                    "warning",
                )
            self._safe_finish(True, f"Downloaded {self._downloaded} files")

        except Exception as e:
            self._finish_with_error(f"Download failed: {e}")

    def _download_loop(self, download_queue: queue.Queue):
        """Single thread loop: connect, pull from queue, download, repeat."""
        try:
            ftp = self._make_connection()
        except Exception as e:
            self._errors.append(str(e))
            self._log_queue.put((f"Download connection failed: {e}", "warning"))
            return

        with self._connections_lock:
            self._active_ftp_connections.append(ftp)

        try:
            while self._is_running:
                try:
                    remote_file, local_target_dir, file_size = (
                        download_queue.get_nowait()
                    )
                except queue.Empty:
                    break

                try:
                    os.makedirs(local_target_dir, exist_ok=True)
                    filename = os.path.basename(remote_file)
                    local_path = os.path.join(local_target_dir, filename)

                    with self._file_progress_lock:
                        self._file_progress[remote_file] = 0

                    self._log_queue.put((f"Downloading: {remote_file}", "info"))
                    file_start = time.time()
                    fb = [0]  # per-file byte counter

                    with open(local_path, "wb") as f:
                        ftp.retrbinary(
                            f"RETR {remote_file}",
                            lambda data, _f=f, _fb=fb, _fk=remote_file, _fs=file_size: self._write_chunk(
                                data, _f, _fb, _fk, _fs
                            ),
                            blocksize=self.CHUNK_SIZE,
                        )

                    with self._file_progress_lock:
                        self._file_progress[remote_file] = 100

                    elapsed = time.time() - file_start
                    speed = (fb[0] / elapsed / 1024 / 1024) if elapsed > 0 else 0
                    self._log_queue.put((f"Completed: {filename} — {speed:.1f} MB/s", "success"))

                    with self._download_lock:
                        self._downloaded += 1
                except Exception as e:
                    self._log_queue.put((
                        f"Failed to download {remote_file}: {e}",
                        "warning",
                    ))
                finally:
                    download_queue.task_done()

        except Exception as e:
            self._errors.append(str(e))
            self._log_queue.put((f"Download thread error: {e}", "warning"))
        finally:
            with self._connections_lock:
                still_tracked = ftp in self._active_ftp_connections
                if still_tracked:
                    self._active_ftp_connections.remove(ftp)
            if still_tracked:
                try:
                    ftp.close()
                except Exception:
                    pass

    def _write_chunk(self, data, f, fb, file_key, file_size):
        """Write chunk and update byte counters only. No signal emission — unsafe from raw threads."""
        f.write(data)
        n = len(data)
        self._transferred += n
        fb[0] += n
        if file_size > 0:
            pct = min(int(fb[0] / file_size * 100), 99)
            with self._file_progress_lock:
                self._file_progress[file_key] = pct

    # ------------------------------------------------------------------
    # Stats — called from QTimer on main thread (safe signal emission)
    # ------------------------------------------------------------------

    def emit_stats_if_due(self):
        """Called from QTimer every ~200ms. Drain thread log queue, then emit stats."""
        while not self._log_queue.empty():
            try:
                msg, level = self._log_queue.get_nowait()
                self.log(msg, level)
            except Exception:
                pass
        self._emit_stats()

    def _emit_stats(self):
        with self._stats_lock:
            transferred = self._transferred
            total = self.total_bytes
            now = time.time()
            speed = self._compute_speed(now, float(transferred))
            speed_mbps = speed / (1024 * 1024)
            if total > 0:
                remaining = total - transferred
                eta = remaining / speed if speed > 0 else 0
                percent = min(int(transferred / total * 100), 100)
            else:
                eta = 0
                percent = 0
            self.update_progress(percent)
            self.transfer_stats.emit(speed_mbps, float(transferred), float(total), eta)
            self._last_stats_time = now

    def _compute_speed(self, now: float, transferred: float) -> float:
        """Rolling SPEED_WINDOW-second window speed in bytes/sec."""
        self._speed_samples.append((now, transferred))
        cutoff = now - self.SPEED_WINDOW
        while self._speed_samples and self._speed_samples[0][0] < cutoff:
            self._speed_samples.popleft()
        if len(self._speed_samples) < 2:
            elapsed = now - self._start_time
            return transferred / elapsed if elapsed > 0 else 0
        oldest_t, oldest_b = self._speed_samples[0]
        span = now - oldest_t
        delta = transferred - oldest_b
        return delta / span if span > 0 else 0

    # ------------------------------------------------------------------
    # Scan helpers
    # ------------------------------------------------------------------

    def _scan_path(self, ftp, remote_path: str, local_base: str, entries: list):
        """
        Recursively scan remote_path into entries.
        Each entry is (remote_file, local_target_dir, size_bytes).
        Size is taken from MLSD attrs when available, otherwise 0.
        """
        try:
            # ftp.size() succeeds only for files, raises for directories.
            size = ftp.size(remote_path) or 0
            entries.append((remote_path, local_base, size))
            return
        except Exception:
            pass

        # ftp.size() failed — confirm it's truly a directory by trying cwd.
        try:
            ftp.cwd(remote_path)
            ftp.cwd("/")  # restore to root
        except Exception:
            # Can't cd into it — treat as a file with unknown size.
            entries.append((remote_path, local_base, 0))
            return

        # Confirmed directory — walk it recursively.
        dir_name = remote_path.rstrip("/").split("/")[-1]
        local_mirror = os.path.join(local_base, dir_name)

        try:
            children = self._list_dir(ftp, remote_path)
        except Exception as e:
            self.log(f"Cannot list {remote_path}: {e}", "warning")
            entries.append((remote_path, local_base, 0))
            return

        if not children:
            entries.append((remote_path, local_base, 0))
            return

        for name, is_dir, size in children:
            if self.check_cancelled():
                return
            child_remote = f"{remote_path.rstrip('/')}/{name}"
            if is_dir:
                self._scan_path(ftp, child_remote, local_mirror, entries)
            else:
                entries.append((child_remote, local_mirror, size))

    def _list_dir(self, ftp, path: str) -> list:
        """Return list of (name, is_dir, size) for a remote directory. Tries MLSD first."""
        try:
            result = []
            for name, attrs in ftp.mlsd(path):
                if name in (".", ".."):
                    continue
                is_dir = attrs.get("type") == "dir"
                size = int(attrs.get("size", 0)) if not is_dir else 0
                result.append((name, is_dir, size))
            return result
        except Exception:
            # Fallback to LIST — no size info available.
            lines = []
            ftp.dir(path, lines.append)
            result = []
            for line in lines:
                parts = line.split()
                if len(parts) < 9:
                    continue
                name = " ".join(parts[8:])
                if name in (".", ".."):
                    continue
                is_dir = parts[0].startswith("d")
                size = 0
                try:
                    size = int(parts[4]) if not is_dir else 0
                except (ValueError, IndexError):
                    pass
                result.append((name, is_dir, size))
            return result
