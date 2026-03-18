from datetime import datetime
from .base_worker import BaseFTPWorker
from ..ftp_utils import format_size as _format_size
from qt_shim import QtCore


def _format_modify(raw: str) -> str:
    """Convert MLSD modify timestamp (YYYYMMDDHHmmss) to readable date."""
    if not raw or len(raw) < 8:
        return ""
    try:
        return datetime.strptime(raw[:14], "%Y%m%d%H%M%S").strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return raw


class FTPListWorker(BaseFTPWorker):
    """Lists files and directories at a given FTP path."""

    files_ready = QtCore.Signal(list)

    def __init__(self, host, user, password, port, path):
        super().__init__(host, user, password, port)
        self.path = path

    def run(self):
        try:
            ftp = self._connect()

            try:
                ftp.cwd(self.path)
            except Exception as e:
                raise Exception(f"Cannot access directory '{self.path}': {e}")

            entries = self._list_mlsd(ftp) or self._list_fallback(ftp)

            ftp.quit()

            self.log(f"Found {len(entries)} items", "info")
            self.files_ready.emit(entries)
            self._safe_finish(True, f"Listed {len(entries)} items")

        except Exception as e:
            self._finish_with_error(f"List failed: {e}")

    def _list_mlsd(self, ftp):
        """List using MLSD (modern method). Returns None if not supported."""
        try:
            entries = []
            for name, attrs in ftp.mlsd():
                if name in (".", ".."):
                    continue
                is_dir = attrs.get("type") == "dir"
                size = int(attrs.get("size", 0))
                entries.append(
                    {
                        "name": name,
                        "path": f"{self.path}/{name}".replace("//", "/"),
                        "is_dir": is_dir,
                        "size": size,
                        "size_str": "" if is_dir else _format_size(size),
                        "modify": attrs.get("modify", ""),
                        "modify_str": _format_modify(attrs.get("modify", "")),
                    }
                )
            return entries
        except Exception:
            self.log("MLSD not supported, falling back to LIST", "warning")
            return None

    def _list_fallback(self, ftp):
        """List using LIST command as fallback for servers without MLSD."""
        lines = []
        ftp.dir(self.path, lines.append)

        entries = []
        for line in lines:
            if not line or line.startswith("total"):
                continue
            parts = line.split()
            if len(parts) < 9:
                continue

            is_dir = parts[0].startswith("d")
            name = " ".join(parts[8:])
            if name in (".", ".."):
                continue

            size = 0
            try:
                size = int(parts[4])
            except (ValueError, IndexError):
                pass

            entries.append(
                {
                    "name": name,
                    "path": f"{self.path}/{name}".replace("//", "/"),
                    "is_dir": is_dir,
                    "size": size,
                    "size_str": "" if is_dir else _format_size(size),
                    "modify": "",
                    "modify_str": "",
                }
            )
        return entries
