import os
from typing import Optional
from PySide6 import QtWidgets

from ftp.ftp_utils import format_size as _format_size


class TransferPanel:
    """
    Manages file transfers (upload/download/cancel), folder creation, deletion,
    and all progress/stats UI updates.

    Holds a reference to the parent window for access to widgets and ftp_manager.
    Does not inherit from Qt — purely a logic container.
    """

    def __init__(self, window):
        self._win = window
        self._wm = window._wm
        self._active_btn: Optional[QtWidgets.QPushButton] = None
        self._active_btn_original_text: str = ""
        self._pending_completion = None

    # ------------------------------------------------------------------
    # Download
    # ------------------------------------------------------------------

    def start_download(self, remote_paths: list, local_dir: str):
        if not self._win.ftp_manager.is_connected():
            self._win.log("Cannot download: not connected", "warning")
            return
        if self._win.ftp_manager.is_busy():
            self._win.log("Cannot download: operation in progress", "warning")
            return
        if not remote_paths:
            self._win.log("No FTP files or folders selected", "warning")
            return

        self._win.log(f"Downloading {len(remote_paths)} item(s)...", "transfer")
        self._set_cancel_mode(self._win.download_selected_btn, "Cancel Download")

        def on_download_complete(success, message):
            self._pending_completion = None
            if success:
                self._win.log(f"✅ Download completed", "success")
            else:
                self._win.log(f"Download finished: {message}", "info")
            self._win._safe_refresh_ftp()

        self._pending_completion = self._wm.safe_connect_once(
            self._win.ftp_manager.operation_finished, on_download_complete, self._win
        )

        try:
            self._win.ftp_manager.download_files(remote_paths, local_dir)
        except Exception as e:
            self._win.log(f"Download error: {e}", "error")
            self.restore_button()

    # ------------------------------------------------------------------
    # Upload
    # ------------------------------------------------------------------

    def start_upload(self, local_paths: list, remote_dir: str):
        if not self._win.ftp_manager.is_connected():
            self._win.log("Cannot upload: not connected", "warning")
            return
        if not local_paths:
            self._win.log("No local files or folders selected for upload", "warning")
            return

        # Only switch button on first upload
        if not self._win.ftp_manager.is_uploading():
            self._set_cancel_mode(self._win.upload_selected_btn, "Cancel Upload")

        file_count = len(local_paths)

        def on_upload_complete(success, message):
            self._pending_completion = None
            if success:
                self._win.log(f"✅ Upload completed: {file_count} files", "success")
            else:
                self._win.log(f"Upload finished: {message}", "info")
            self._win._safe_refresh_ftp()

        self._pending_completion = self._wm.safe_connect_once(
            self._win.ftp_manager.operation_finished, on_upload_complete, self._win
        )

        try:
            self._win.ftp_manager.upload_files(local_paths, remote_dir)
        except Exception as e:
            self._win.log(f"Upload error: {e}", "error")
            self.restore_button()

    # ------------------------------------------------------------------
    # Cancel
    # ------------------------------------------------------------------

    def cancel(self):
        """Cancel the active transfer and restore the button."""
        if self._pending_completion is not None:
            try:
                self._win.ftp_manager.operation_finished.disconnect(
                    self._pending_completion
                )
            except Exception:
                pass
            self._pending_completion = None
        self._win.ftp_manager.stop_current_operation()
        self._win._safe_refresh_ftp()
        self.restore_button()

    # ------------------------------------------------------------------
    # Folder creation
    # ------------------------------------------------------------------

    def create_ftp_folder(self, current_ftp_path: str):
        if not self._win.ftp_manager.is_connected():
            self._win.log("Cannot create folder: not connected", "warning")
            return
        if self._win.ftp_manager.is_busy():
            self._win.log("Cannot create folder: operation in progress", "warning")
            return

        folder_name = self._wm.show_folder_name_dialog(
            self._win,
            title="Create FTP Folder",
            icon=QtWidgets.QMessageBox.Question,
        )

        if not folder_name:
            return

        folder_name = folder_name.replace(" ", "_")
        remote_path = f"{current_ftp_path}/{folder_name}".replace("//", "/")

        self._win.log(f"Creating folder: {remote_path}", "info")

        def on_folder_created(success, message):
            if success:
                self._win.log(f"✅ Folder created: {remote_path}", "success")
                self._win._safe_refresh_ftp()
            else:
                self._win.log(f"❌ Failed to create folder: {message}", "error")

        self._wm.safe_connect_once(
            self._win.ftp_manager.operation_finished, on_folder_created, self._win
        )

        self._win.ftp_manager.create_directories([remote_path])

    # ------------------------------------------------------------------
    # Local folder creation
    # ------------------------------------------------------------------

    def create_local_folder(self, current_local_path: str):
        if not current_local_path:
            self._win.log("Cannot create folder: no local path set", "warning")
            return

        folder_name = self._wm.show_folder_name_dialog(
            self._win,
            title="Create Local Folder",
            icon=QtWidgets.QMessageBox.Question,
        )

        if not folder_name:
            return

        folder_name = folder_name.replace(" ", "_")
        new_path = os.path.join(current_local_path, folder_name)

        try:
            os.makedirs(new_path, exist_ok=True)
            self._win.log(f"✅ Created local folder: {folder_name}", "success")
            if hasattr(self._win.local_panel, "refresh"):
                self._win.local_panel.refresh()
        except Exception as e:
            self._win.log(f"❌ Failed to create folder: {e}", "error")

    # ------------------------------------------------------------------
    # Deletion (FTP + local)
    # ------------------------------------------------------------------

    def delete_selected(self, ftp_paths: list, local_paths: list):
        if ftp_paths:
            names = "\n".join(p.split("/")[-1] for p in ftp_paths)
            confirm = self._wm.show_buttons_dialog(
                self._win,
                "Confirm FTP Delete",
                f"Delete {len(ftp_paths)} item(s) from FTP server?\n\n{names}",
                buttons=[("Delete", True), ("Cancel", False)],
                icon=QtWidgets.QMessageBox.Warning,
            )
            if confirm:
                self._win.ftp_manager.delete_files(ftp_paths)
        elif local_paths:
            names = "\n".join(os.path.basename(p) for p in local_paths)
            confirm = self._wm.show_buttons_dialog(
                self._win,
                "Confirm Local Delete",
                f"Permanently delete {len(local_paths)} local item(s)?\n\n{names}",
                buttons=[("Delete", True), ("Cancel", False)],
                icon=QtWidgets.QMessageBox.Warning,
            )
            if confirm:
                self._win.local_panel.delete_files(local_paths)
        else:
            self._win.log("No files selected for deletion", "warning")

    # ------------------------------------------------------------------
    # Progress & stats
    # ------------------------------------------------------------------

    def on_progress(self, value: int):
        if self._win.progress_bar:
            self._win.progress_bar.setValue(value)

    def on_transfer_stats(
        self, speed_mbps: float, transferred: float, total: float, eta: float
    ):
        if self._win.speed_status:
            self._win.speed_status.setText(f"{speed_mbps:.1f} MB/s")
        if self._win.total_status:
            self._win.total_status.setText(
                f"{_format_size(transferred)}/{_format_size(total)}"
            )
        if self._win.eta_status:
            self._win.eta_status.setText(_format_eta(eta))

    def on_busy_changed(self, busy: bool):
        if busy:
            self._win._stats_timer.start()
        else:
            self._win._stats_timer.stop()
            self.reset_ui()

    def poll_stats(self):
        """Called by _stats_timer every 200ms to pull stats from the upload worker."""
        try:
            worker = self._win.ftp_manager._current_worker
            if worker is not None and hasattr(worker, "emit_stats_if_due"):
                worker.emit_stats_if_due()
        except Exception:
            pass

    def reset_ui(self):
        if self._win.progress_bar:
            self._win.progress_bar.setValue(0)
        if self._win.speed_status:
            self._win.speed_status.setText("")
        if self._win.total_status:
            self._win.total_status.setText("")
        if self._win.eta_status:
            self._win.eta_status.setText("")

    # ------------------------------------------------------------------
    # Internal — cancel button lifecycle
    # ------------------------------------------------------------------

    def _set_cancel_mode(self, btn: Optional[QtWidgets.QPushButton], cancel_text: str):
        if not btn:
            return
        self._active_btn = btn
        self._active_btn_original_text = btn.text()
        btn.setText(cancel_text)
        try:
            btn.clicked.disconnect()
        except (RuntimeError, TypeError):
            pass
        btn.clicked.connect(self.cancel)

    def restore_button(self):
        """Restore the transfer button to its original state."""
        btn = self._active_btn
        if not btn:
            return
        btn.setText(self._active_btn_original_text)
        try:
            btn.clicked.disconnect()
        except (RuntimeError, TypeError):
            pass
        if btn is self._win.download_selected_btn:
            btn.clicked.connect(self._win._download_selected)
        else:
            btn.clicked.connect(self._win._upload_selected)
        self._active_btn = None
        self._active_btn_original_text = ""


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _format_eta(seconds: float) -> str:
    if seconds <= 0:
        return ""
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    m, s = divmod(seconds, 60)
    if m < 60:
        return f"{m}m {s:02d}s"
    h, m = divmod(m, 60)
    return f"{h}h {m:02d}m"
