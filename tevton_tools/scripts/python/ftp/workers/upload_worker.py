import os
import queue
import time
import threading
from collections import deque
from .base_transfer_worker import BaseTransferWorker, _format_size


class FTPUploadWorker(BaseTransferWorker):
    """
    Uploads files to FTP from a thread-safe queue using parallel connections.

    Each queue item is a tuple: (local_file_path, remote_target_dir).
    The worker runs until the queue is empty AND the manager signals no more
    items will be added (via done_event). This allows adding files mid-upload.

    Up to MAX_CONNECTIONS threads pull from the shared queue simultaneously,
    each with its own FTP connection. This dramatically improves throughput
    for many small files by overlapping per-file protocol overhead.

    Progress is tracked via _CountingReader — no storbinary callback, so the
    transfer hot path has zero Python overhead per chunk.
    Stats are emitted by emit_stats_if_due(), called from a QTimer in shot_manager.
    """

    QUEUE_TIMEOUT = 1.0
    STATS_INTERVAL = 0.2
    MAX_CONNECTIONS = 4
    SPEED_WINDOW = 5.0

    def __init__(
        self,
        host,
        user,
        password,
        port,
        upload_queue: queue.Queue,
        done_event,
        total_bytes: int = 0,
    ):
        super().__init__(host, user, password, port)
        self._queue = upload_queue
        self._done_event = done_event
        self.total_bytes = total_bytes
        self._transferred_bytes = 0
        self._start_time: float = 0.0
        self._last_stats_time: float = 0.0
        self._created_dirs: set = set()
        self._dir_lock = threading.Lock()
        self._upload_lock = threading.Lock()
        self._active_ftp_connections: list = []
        self._connections_lock = threading.Lock()
        self._speed_samples: deque = deque()
        self._stats_lock = threading.Lock()
        self._log_queue: queue.Queue = queue.Queue()
        self._stop_called = False
    def add_total_bytes(self, n: int):
        self.total_bytes += n

    def get_transferred_bytes(self) -> int:
        return self._transferred_bytes

    def stop(self):
        """Cancel: set flag and force-close all active FTP sockets immediately."""
        if self._stop_called:
            return
        self._stop_called = True
        self._is_running = False
        with self._connections_lock:
            for ftp in self._active_ftp_connections[:]:
                try:
                    ftp.close()
                except Exception:
                    pass
            self._active_ftp_connections.clear()

    def run(self):
        self._start_time = time.time()
        self._last_stats_time = self._start_time
        self._uploaded = 0
        self._errors = []

        threads = []
        for i in range(self.MAX_CONNECTIONS):
            t = threading.Thread(
                target=self._worker_loop, daemon=True, name=f"UploadThread-{i}"
            )
            t.start()
            threads.append(t)

        for t in threads:
            t.join()

        if self._stop_called or not self._is_running:
            self._safe_finish(False, "Upload cancelled by user")
            return

        total = self._uploaded
        if self._errors:
            self.log(
                f"{len(self._errors)} connection(s) failed: {self._errors[0]}",
                "warning",
            )
        self._safe_finish(True, f"Uploaded {total} files")

    def _worker_loop(self):
        try:
            ftp = self._make_connection()
        except Exception as e:
            self._errors.append(str(e))
            self._log_queue.put((f"Connection thread failed: {e}", "warning"))
            return

        with self._connections_lock:
            self._active_ftp_connections.append(ftp)

        try:
            while self._is_running:
                try:
                    local_file, remote_dir = self._queue.get(timeout=self.QUEUE_TIMEOUT)
                except queue.Empty:
                    if self._done_event.is_set():
                        break
                    continue

                if not self._is_running:
                    self._queue.task_done()
                    break

                try:
                    self._upload_file(ftp, local_file, remote_dir)
                    with self._upload_lock:
                        self._uploaded += 1
                except Exception as e:
                    self._log_queue.put(
                        (
                            f"Failed to upload {os.path.basename(local_file)}: {e}",
                            "warning",
                        )
                    )
                finally:
                    self._queue.task_done()

        except Exception as e:
            self._errors.append(str(e))
            self._log_queue.put((f"Upload thread error: {e}", "warning"))
        finally:
            with self._connections_lock:
                if ftp in self._active_ftp_connections:
                    self._active_ftp_connections.remove(ftp)
            try:
                ftp.close()
            except Exception:
                pass

    def _upload_file(self, ftp, local_file: str, remote_dir: str):
        filename = os.path.basename(local_file)
        file_size = os.path.getsize(local_file)
        remote_path = f"{remote_dir}/{filename}"

        with self._file_progress_lock:
            self._file_progress[remote_path] = 0

        self._log_queue.put(
            (f"Uploading: {remote_path} ({_format_size(file_size)})", "transfer")
        )
        self._ensure_remote_dir(ftp, remote_dir)

        t0 = time.time()

        with open(local_file, "rb") as f:
            proxy = _CountingReader(f, self, remote_path, file_size)
            ftp.storbinary(f"STOR {remote_path}", proxy, blocksize=self.CHUNK_SIZE)

        with self._file_progress_lock:
            self._file_progress[remote_path] = 100

        elapsed = time.time() - t0
        speed = (file_size / elapsed / 1024 / 1024) if elapsed > 0 else 0

        self._log_queue.put((f"Completed: {filename} — {speed:.1f} MB/s", "success"))

    def emit_stats_if_due(self):
        while not self._log_queue.empty():
            try:
                msg, level = self._log_queue.get_nowait()
                self.log(msg, level)
            except Exception:
                pass
        self._emit_stats()

    def _emit_stats(self):
        with self._stats_lock:
            transferred = self._transferred_bytes
            total = self.total_bytes
            now = time.time()
            speed = self._compute_speed(now, float(transferred))
            speed_mbps = speed / (1024 * 1024)
            remaining = total - transferred
            eta = remaining / speed if speed > 0 else 0
            percent = int(transferred / total * 100) if total > 0 else 0
            self.update_progress(percent)
            self.transfer_stats.emit(speed_mbps, float(transferred), float(total), eta)
            self._last_stats_time = now

    def _compute_speed(self, now: float, transferred: float) -> float:
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

    def _ensure_remote_dir(self, ftp, remote_path: str):
        with self._dir_lock:
            if remote_path in self._created_dirs:
                return

        current = ""
        parts_to_create = []
        for part in remote_path.split("/"):
            if not part:
                continue
            current += "/" + part
            with self._dir_lock:
                if current not in self._created_dirs:
                    parts_to_create.append(current)

        for p in parts_to_create:
            try:
                ftp.mkd(p)
            except Exception:
                pass
            with self._dir_lock:
                self._created_dirs.add(p)


class _CountingReader:
    __slots__ = ("_f", "_worker", "_file_key", "_file_size", "_file_bytes")

    def __init__(self, f, worker: FTPUploadWorker, file_key: str, file_size: int):
        self._f = f
        self._worker = worker
        self._file_key = file_key
        self._file_size = file_size
        self._file_bytes = 0

    def read(self, size=-1):
        data = self._f.read(size)
        if data:
            n = len(data)
            self._worker._transferred_bytes += n
            self._file_bytes += n
            pct = int(self._file_bytes / self._file_size * 100) if self._file_size > 0 else 0
            with self._worker._file_progress_lock:
                self._worker._file_progress[self._file_key] = min(pct, 99)
        return data
