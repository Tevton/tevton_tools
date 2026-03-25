from .base_worker import BaseFTPWorker


class FTPMakeDirsWorker(BaseFTPWorker):
    """Creates directory trees on FTP server."""

    def __init__(self, host, user, password, port, remote_dirs, use_tls=False):
        super().__init__(host, user, password, port, use_tls=use_tls)
        self.remote_dirs = remote_dirs or []

    def run(self):
        if not self.remote_dirs:
            self._safe_finish(False, "No directories specified")
            return

        try:
            ftp = self._connect()
            created = 0
            failed = []

            for remote_dir in self.remote_dirs:
                if self.check_cancelled():
                    return
                try:
                    self._ensure_remote_dir(ftp, remote_dir)
                    created += 1
                    self.log(f"Created: {remote_dir}", "info")
                except Exception as e:
                    self.log(f"Failed to create {remote_dir}: {e}", "warning")
                    failed.append(remote_dir)

            ftp.quit()

            if failed:
                self._safe_finish(
                    False,
                    f"Created {created}, failed {len(failed)}: {', '.join(failed)}",
                )
            else:
                self._safe_finish(True, f"Created {created} directories")

        except Exception as e:
            self._finish_with_error(f"Mkdir failed: {e}")

    def _ensure_remote_dir(self, ftp, remote_path: str):
        """
        Create remote directory tree using mkd() only — no cwd() round-trips.
        550 (already exists) is silently ignored.
        """
        current = ""
        for part in remote_path.split("/"):
            if not part:
                continue
            current += "/" + part
            try:
                ftp.mkd(current)
            except Exception:
                pass
