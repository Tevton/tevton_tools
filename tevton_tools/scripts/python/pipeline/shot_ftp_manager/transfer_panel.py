import os
import zipfile
import re as _re
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Optional, List
from PySide6 import QtWidgets, QtCore, QtGui

import tvt_utils
from ftp.ftp_utils import format_size as _format_size

# Names of the four operation buttons managed by the cancel/restore cycle.
_TRANSFER_BTN_NAMES = [
    "upload_renders_btn",
    "upload_selected_btn",
    "download_selected_btn",
    "download_source_btn",
]



class _ProgressDelegate(QtWidgets.QStyledItemDelegate):
    """Paints a translucent progress fill behind each files-queue item."""

    def paint(self, painter, option, index):
        progress = index.data(QtCore.Qt.ItemDataRole.UserRole) or 0
        color = index.data(QtCore.Qt.ItemDataRole.UserRole + 1) or QtGui.QColor(60, 120, 60)
        painter.save()
        fill_w = int(option.rect.width() * progress / 100)
        fill_rect = QtCore.QRect(option.rect).adjusted(0, 1, 0, -1)
        fill_rect.setWidth(fill_w)
        fill_color = QtGui.QColor(color)
        fill_color.setAlpha(80)
        painter.fillRect(fill_rect, fill_color)
        painter.restore()
        super().paint(painter, option, index)


class TransferPanel:
    """
    Owns all transfer logic for the Shot FTP Manager.

    Responsibilities:
    - High-level operations: upload renders/selected, download selected/source
    - Low-level helpers: start_upload / start_download / cancel
    - Folder and file operations
    - Progress/stats UI updates
    - Cancel-button lifecycle and transfer-button blocking
    """

    def __init__(self, window):
        self._win = window
        self._wm = window._wm
        self._cancel_btns: list = []  # [(btn, original_text), ...]
        self.archive_name = ""
        self._pending_completion = None
        self._render_upload_total = 0
        self._queue_items: dict = {}  # op_id → QListWidgetItem
        self._active_op_id: str = ""  # transfer being tracked for progress updates
        if self._win.files_queue:
            self._win.files_queue.setItemDelegate(_ProgressDelegate(self._win.files_queue))

    def _block_for_transfer(self):
        """Block ftp_ops during a transfer; transfer buttons are managed via cancel-mode."""
        self._win._ui_state.disable_group("ftp_ops")

    def _block_for_other_op(self):
        """Block both groups during a non-transfer FTP op (mkdir, delete, rename/move)."""
        self._win._ui_state.disable_group("ftp_ops")
        self._win._ui_state.disable_group("transfer")

    def _unblock_all(self):
        """Re-enable both groups after any operation (error paths / early returns)."""
        self._win._ui_state.enable_group("ftp_ops")
        self._win._ui_state.enable_group("transfer")

    # ──────────────────────────────────────────────────────────────────────
    # FILES QUEUE
    # ──────────────────────────────────────────────────────────────────────

    def _queue_add(self, op_id: str, label_prefix: str, paths: list, color: QtGui.QColor, progress: int = 0, keys: list = None):
        if not self._win.files_queue:
            return
        for i, path in enumerate(paths):
            name = os.path.basename(str(path).rstrip("/\\")) or str(path)
            item = QtWidgets.QListWidgetItem(f"{label_prefix}: {name}")
            item.setData(QtCore.Qt.ItemDataRole.UserRole, progress)
            item.setData(QtCore.Qt.ItemDataRole.UserRole + 1, color)
            self._win.files_queue.addItem(item)
            item_key = f"{op_id}\0{keys[i]}" if keys else f"{op_id}\0{i}"
            self._queue_items[item_key] = item

    def _queue_update(self, op_id: str, progress: int):
        updated = False
        for key, item in self._queue_items.items():
            if key.startswith(op_id):
                item.setData(QtCore.Qt.ItemDataRole.UserRole, progress)
                updated = True
        if updated and self._win.files_queue:
            self._win.files_queue.viewport().update()

    def _queue_remove(self, op_id: str):
        keys = [k for k in self._queue_items if k.startswith(op_id)]
        for key in keys:
            item = self._queue_items.pop(key)
            if self._win.files_queue:
                row = self._win.files_queue.row(item)
                if row >= 0:
                    self._win.files_queue.takeItem(row)

    def _apply_file_progress(self, file_progress: dict):
        """Update individual queue items from worker's per-file progress dict.
        Items reaching 100% are removed immediately."""
        if not self._active_op_id or not file_progress:
            return
        prefix = self._active_op_id + "\0"
        to_remove = []
        updated = False
        for key, item in self._queue_items.items():
            if not key.startswith(prefix):
                continue
            file_key = key[len(prefix):]
            if file_key in file_progress:
                pct = file_progress[file_key]
                if pct >= 100:
                    to_remove.append(key)
                else:
                    item.setData(QtCore.Qt.ItemDataRole.UserRole, pct)
                    updated = True
        for key in to_remove:
            item = self._queue_items.pop(key)
            if self._win.files_queue:
                row = self._win.files_queue.row(item)
                if row >= 0:
                    self._win.files_queue.takeItem(row)
            updated = True
        if updated and self._win.files_queue:
            self._win.files_queue.viewport().update()

    def _on_files_scanned(self, op_id: str, entries: list):
        """Replace placeholder download items with expanded file list from worker scan."""
        self._queue_remove(op_id)
        paths = [rf for rf, _, _ in entries]
        self._queue_add(op_id, "Download", paths, QtGui.QColor(60, 100, 180), keys=paths)

    def _on_delete_scanned(self, op_id: str, entries: list):
        """Replace placeholder delete items with expanded file list from worker scan."""
        self._queue_remove(op_id)
        paths = [p for p, is_dir in entries if not is_dir]
        self._queue_add(op_id, "Delete", paths, QtGui.QColor(160, 70, 70), keys=paths)

    @staticmethod
    def _set_cancel_btn_stylesheet(widget):
        widget.setStyleSheet(
            """
        QPushButton {
            background-color: #593131;
            color: #e0e0e0;
            border: 1px solid #3d2121;
            box-shadow: 0 2px 4px rgba(0, 0, 0, 0.3);
        }
        QPushButton:hover {
            background-color: #7a4545;
            color: #e0e0e0;
            box-shadow: 0 2px 6px rgba(255, 100, 100, 0.4);
        }
        QPushButton:pressed {
            background-color: #3d2121;
            color: #e0e0e0;
            box-shadow: inset 0 0 5px rgba(255, 200, 200, 0.5), 0 1px 2px rgba(0, 0, 0, 0.3);
            border: 2px solid #2d1818;
            border-top-width: 3px;
            border-bottom-width: 1px;
        }
        """
        )

    # ──────────────────────────────────────────────────────────────────────
    # HIGH-LEVEL TRANSFER OPERATIONS
    # ──────────────────────────────────────────────────────────────────────

    def paste(self, src_paths: list, src_type: str, dest: str):
        """Paste clipboard into the destination panel.

        Args:
            src_paths: Files/folders to paste.
            src_type: 'local' or 'ftp' — where the clipboard came from.
            dest: 'local' or 'ftp' — where to paste.
        """
        win = self._win
        if not src_paths:
            return

        if src_type == "local" and dest == "ftp":
            if not win.ftp_manager.is_connected():
                win.log("Cannot paste: FTP not connected", "warning")
                return
            if win.ftp_manager.is_busy():
                win.log("Cannot paste: FTP busy", "warning")
                return
            self._block_for_transfer()
            try:
                self.start_upload(src_paths, win.current_ftp_path, "selected")
            except RuntimeError:
                self._unblock_all()

        elif src_type == "ftp" and dest == "local":
            if not win.ftp_manager.is_connected():
                win.log("Cannot paste: FTP not connected", "warning")
                return
            if win.ftp_manager.is_busy():
                win.log("Cannot paste: FTP busy", "warning")
                return
            self._block_for_transfer()
            try:
                self.start_download(src_paths, win.current_local_path, "selected")
            except RuntimeError:
                self._unblock_all()

        elif src_type == "local" and dest == "local":
            if not win.current_local_path:
                win.log("Cannot paste: no local path set", "warning")
                return
            self._copy_local_to_local(src_paths, win.current_local_path)

        elif src_type == "ftp" and dest == "ftp":
            target_dir = win.current_ftp_path.rstrip("/")
            to_move = [
                p
                for p in src_paths
                if "/".join(p.rstrip("/").split("/")[:-1]) != target_dir
            ]
            if not to_move:
                win.log("Files are already in this folder", "info")
                return
            if not win.ftp_manager.is_connected():
                win.log("Cannot paste: FTP not connected", "warning")
                return
            if win.ftp_manager.is_busy():
                win.log("Cannot paste: FTP busy", "warning")
                return
            self._block_for_other_op()
            moves = [
                (p, f"{target_dir}/{p.rstrip('/').split('/')[-1]}") for p in to_move
            ]
            win._start_ftp_moves(moves)

    def _copy_local_to_local(self, src_paths: list, dest_dir: str):
        """Copy local files/folders into dest_dir using shutil."""
        import shutil as _shutil

        errors = []
        copied = 0
        for src in src_paths:
            name = os.path.basename(src.rstrip("/\\"))
            dst = os.path.join(dest_dir, name)
            if os.path.normpath(src) == os.path.normpath(dst):
                self._win.log(
                    f"Skipping: source equals destination ({name})", "warning"
                )
                continue
            try:
                if os.path.isdir(src):
                    _shutil.copytree(src, dst, dirs_exist_ok=True)
                else:
                    _shutil.copy2(src, dst)
                copied += 1
            except Exception as e:
                errors.append(f"{name}: {e}")
        if errors:
            self._win.log(f"Copy errors: {'; '.join(errors)}", "error")
        elif copied:
            self._win.log(f"✅ Copied {copied} item(s)", "success")

    def start_upload_selected(self):
        """Upload the currently selected local files/folders to the current FTP path."""
        win = self._win
        if not win.ftp_manager.is_connected():
            win.log("Cannot upload: not connected", "warning")
            return
        if win.ftp_manager.is_busy():
            win.log("Cannot upload: operation in progress", "warning")
            return
        local_paths = win.local_panel.get_selected_paths() if win.local_panel else []
        self._block_for_transfer()
        if win.zip_up_files.isChecked():
            archive_name = self._wm.show_input_field_dialog(
                self._win,
                title="Create Local Archive",
                icon=QtWidgets.QMessageBox.Question,
            )
            if not archive_name:
                self._unblock_all()
                return
            local_paths = self.create_archive(local_paths, archive_name)
        try:
            self.start_upload(local_paths, win.current_ftp_path, "selected")
        except RuntimeError:
            self._unblock_all()

    def start_upload_renders(self):
        """Upload render folders from the local shot using the mode selected in the UI."""
        win = self._win
        local_render_path = Path(win.local_shot_path) / "render"
        if not local_render_path.exists():
            win.log(f"Render folder not found: {local_render_path}", "warning")
            positive = self._wm.show_buttons_dialog(
                win,
                "Error",
                f"Can't find local 'render' folder!\n\n"
                f"Would you like to create 'render' folder inside: {win.local_shot_path}?\n\n",
                buttons=[("Yes", True), ("No", False)],
                icon=QtWidgets.QMessageBox.Critical,
            )
            if not positive:
                win.log("User aborted operation", "error")
                return
            try:
                local_render_path.mkdir()
                win.log(f"Folder created: {local_render_path}", "success")
                self._wm.show_buttons_dialog(
                    win,
                    "Success",
                    "Folder successfuly created!\n\n"
                    f"Path : {local_render_path}\n\n"
                    "Expected structure: render/{render_name}/{version}/files",
                    icon=QtWidgets.QMessageBox.Information,
                )
                return
            except OSError as e:
                self._wm.show_buttons_dialog(
                    win,
                    "Error",
                    f"Error creating render folder!\nError : {e}",
                    icon=QtWidgets.QMessageBox.Critical,
                )
                return

        render_names = [d for d in local_render_path.iterdir() if d.is_dir()]
        if not render_names:
            self._wm.show_buttons_dialog(
                win,
                "Error",
                "The 'render' folder contains no render subfolders.\n\n"
                "Expected structure: render/{render_name}/{version}/files",
                icon=QtWidgets.QMessageBox.Critical,
            )
            return

        if not win.ftp_manager.is_connected():
            win.log("Cannot upload: not connected", "warning")
            return
        if win.ftp_manager.is_busy():
            win.log("Cannot upload: operation in progress", "warning")
            return

        ftp_render_path = str(PurePosixPath(win.shot_root_ftp_path) / "render")
        mode = win.renders_op_mode.currentIndex()  # 0=latest, 1=all, 2=missing

        add_paths = (
            self._collect_add_files() if win.renders_app_add_files.isChecked() else []
        )
        if add_paths is None:
            return  # user aborted

        if mode in (0, 1):
            self._block_for_transfer()

        # Shared callback for modes 0 and 1: uploads list is populated
        # by the mode branch below, then _do_uploads runs after FTP delete.
        uploads = []

        def _do_uploads():
            self._render_upload_total = 0
            for local_paths, remote_dir in uploads:
                self.start_upload(local_paths, remote_dir, "renders")

        try:
            if mode == 0:  # Keep Only Latest Version
                for rn in render_names:
                    versions = [d for d in rn.iterdir() if d.is_dir()]
                    if not versions:
                        win.log(f"No version folders in {rn.name}, skipping", "warning")
                        continue
                    best = tvt_utils.latest_version_dir(versions)
                    uploads.append(
                        ([str(best)], str(PurePosixPath(ftp_render_path) / rn.name))
                    )

                if not uploads:
                    self._wm.show_buttons_dialog(
                        win,
                        "Error",
                        "No files to upload!\n\n"
                        "Expected structure: render/{render_name}/{version}/files",
                        icon=QtWidgets.QMessageBox.Critical,
                    )
                    self._unblock_all()
                    return

                if add_paths:
                    uploads.append((add_paths, win.shot_root_ftp_path))

                self._delete_ftp_then([ftp_render_path], _do_uploads)

            elif mode == 1:  # Upload All Versions
                uploads.append(([str(local_render_path)], win.shot_root_ftp_path))
                if add_paths:
                    uploads.append((add_paths, win.shot_root_ftp_path))

                self._delete_ftp_then([ftp_render_path], _do_uploads)

            elif (
                mode == 2
            ):  # Upload Only Missing — async listing, add_paths handled inside
                self._start_upload_missing_renders(
                    render_names, ftp_render_path, add_paths
                )
                return  # async; block/unblock handled inside _start_missing

        except RuntimeError:
            self._unblock_all()

    def start_download_selected(self):
        """Download the currently selected FTP files/folders to the current local path."""
        win = self._win
        if not win.ftp_manager.is_connected():
            win.log("Cannot download: not connected", "warning")
            return
        if win.ftp_manager.is_busy():
            win.log("Cannot download: operation in progress", "warning")
            return
        ftp_paths = win.ftp_panel.get_selected_paths() if win.ftp_panel else []
        self._block_for_transfer()
        try:
            self.start_download(ftp_paths, win.current_local_path, "selected")
        except RuntimeError:
            self._unblock_all()

    def start_download_source(self):
        """Download the remote source folder for this shot to the local source folder."""
        win = self._win
        if not win.ftp_manager.is_connected():
            win.log("Cannot download: not connected", "warning")
            return
        if win.ftp_manager.is_busy():
            win.log("Cannot download: operation in progress", "warning")
            return

        local_path = Path(win.local_shot_path) / "source"
        if not local_path.exists():
            win.log(
                f"Local source folder not found in: {win.local_shot_path}", "warning"
            )
            positive = self._wm.show_buttons_dialog(
                win,
                "Error",
                f"Can't find local 'source' folder!\n\n"
                f"Would you like to create 'source' folder inside {win.local_shot_path}?",
                buttons=[("Yes", True), ("No", False)],
                icon=QtWidgets.QMessageBox.Critical,
            )
            if not positive:
                win.log("User aborted operation", "error")
                return
            try:
                local_path.mkdir()
            except OSError as e:
                self._wm.show_buttons_dialog(
                    win,
                    "Error",
                    f"Error creating source folder!\nError : {e}",
                    icon=QtWidgets.QMessageBox.Critical,
                )
                return

        self._block_for_other_op()
        # Suppress the list worker's operation_finished so it doesn't re-enable
        # the transfer buttons before the download worker takes over.
        win._suppress_op_finished += 1

        def on_list_result(files_info):
            if files_info is not None:
                win.log("Remote source folder found, starting download...", "info")
                self.start_download(
                    [win.current_ftp_source_path], str(local_path), "source"
                )

        def on_source_list_fail(success, message):
            if not success:
                self._unblock_all()
                msg = message.lower()
                if "list failed" in msg or "cannot access" in msg:
                    self._wm.show_buttons_dialog(
                        win,
                        "Error",
                        f"Remote source folder does not exist:\n\n{win.current_ftp_source_path}\n\n"
                        f"Please ask Svetlana about source files for {win.shot_name}.",
                        icon=QtWidgets.QMessageBox.Critical,
                    )
                win.log(
                    f"Remote source folder not found: {win.current_ftp_source_path}",
                    "error",
                )

        win._suppress_list_fail_dialog = True
        win.ftp_manager.list_files(
            win.current_ftp_source_path,
            callback=on_list_result,
            fail_callback=on_source_list_fail,
        )

    # ──────────────────────────────────────────────────────────────────────
    # UPLOAD HELPERS
    # ──────────────────────────────────────────────────────────────────────

    def _start_upload_missing_renders(self, render_names, ftp_render_path, add_paths):
        """Mode 2: compare each local render name against FTP, upload only missing versions."""
        win = self._win
        pending = list(render_names)
        upload_queue = []  # [(local_paths, remote_dir), ...]

        def _start_missing(queue):
            def _do_upload():
                if not queue and not add_paths:
                    win.log("All render versions are up to date", "success")
                    return
                if not queue:
                    win.log("All render versions are up to date", "success")
                else:
                    win.log(f"Uploading {len(queue)} missing render version(s)", "info")
                self._render_upload_total = 0
                self._block_for_transfer()
                for local_paths, remote_dir in queue:
                    self.start_upload(local_paths, remote_dir, "renders")
                if add_paths:
                    self.start_upload(add_paths, win.shot_root_ftp_path, "renders")

            self._delete_ftp_then([], _do_upload)

        def check_next():
            if not pending:
                _start_missing(upload_queue)
                return

            rn = pending.pop(0)
            local_versions = {d.name: d for d in rn.iterdir() if d.is_dir()}
            ftp_rn_path = str(PurePosixPath(ftp_render_path) / rn.name)
            _handled = [False]

            def on_list(files):
                if _handled[0]:
                    return
                _handled[0] = True
                ftp_versions = {}  # name → ftp_path
                for d in files:
                    is_dir = d.get("is_dir", False)
                    if isinstance(is_dir, str):
                        is_dir = is_dir.lower() == "true"
                    if is_dir:
                        ftp_versions[d["name"]] = d["path"]

                # Versions not on FTP → upload entire folder
                for vname, vdir in local_versions.items():
                    if vname not in ftp_versions:
                        upload_queue.append(([str(vdir)], ftp_rn_path))

                # Versions on both → compare files inside
                versions_to_check = [
                    (vname, vdir, ftp_versions[vname])
                    for vname, vdir in local_versions.items()
                    if vname in ftp_versions
                ]
                _check_version_files(versions_to_check)

            def _check_version_files(versions_to_check):
                """For versions on both sides, list FTP files and compare."""
                if not versions_to_check:
                    check_next()
                    return

                _vname, vdir, ftp_vpath = versions_to_check.pop(0)
                local_files = {f.name for f in vdir.iterdir() if f.is_file()}
                _vhandled = [False]

                def on_vlist(files):
                    if _vhandled[0]:
                        return
                    _vhandled[0] = True
                    ftp_files = set()
                    for f in files:
                        is_dir = f.get("is_dir", False)
                        if isinstance(is_dir, str):
                            is_dir = is_dir.lower() == "true"
                        if not is_dir:
                            ftp_files.add(f["name"])
                    if local_files - ftp_files:
                        upload_queue.append(([str(vdir)], ftp_rn_path))
                    _check_version_files(versions_to_check)

                def on_vlist_fail(success, message):
                    if _vhandled[0]:
                        return
                    if not success:
                        _vhandled[0] = True
                        upload_queue.append(([str(vdir)], ftp_rn_path))
                        _check_version_files(versions_to_check)

                win._suppress_list_fail_dialog = True
                win._suppress_op_finished += 1
                win.ftp_manager.list_files(
                    ftp_vpath, callback=on_vlist, fail_callback=on_vlist_fail
                )

            def on_list_fail(success, message):
                if _handled[0]:
                    return
                if not success:
                    _handled[0] = True
                    for vdir in local_versions.values():
                        upload_queue.append(([str(vdir)], ftp_rn_path))
                    check_next()

            win._suppress_list_fail_dialog = True
            win._suppress_op_finished += 1
            win.ftp_manager.list_files(
                ftp_rn_path, callback=on_list, fail_callback=on_list_fail
            )

        check_next()

    def _delete_ftp_then(self, extra_ftp_paths: list, callback):
        """List shot root for .mov/.nk files, delete them + extra_ftp_paths, then call callback().

        If the shot root listing fails (folder doesn't exist yet), proceeds with
        only extra_ftp_paths. If nothing needs deleting, calls callback() directly.
        """
        win = self._win
        ftp_to_delete = list(extra_ftp_paths)
        _handled = [False]

        def _run_delete():
            if not ftp_to_delete:
                callback()
                return

            names = [PurePosixPath(p).name for p in ftp_to_delete]
            win.log(f"Deleting from FTP: {', '.join(names)}...", "info")
            self._block_for_other_op()
            win._suppress_op_finished += 1

            def on_delete_done(_success, _message):
                self._wm.safe_timer(win, callback, 0)

            win.ftp_manager.delete_files(ftp_to_delete, callback=on_delete_done)

        def on_list(files):
            if _handled[0]:
                return
            _handled[0] = True
            existing_names = set()
            for f in files:
                is_dir = f.get("is_dir", False)
                if isinstance(is_dir, str):
                    is_dir = is_dir.lower() == "true"
                if not is_dir and f["name"].lower().endswith((".mov", ".nk")):
                    ftp_to_delete.append(f["path"])
                existing_names.add(f["name"])
            # Keep only paths whose target actually exists on FTP
            ftp_to_delete[:] = [
                p for p in ftp_to_delete if PurePosixPath(p).name in existing_names
            ]
            _run_delete()

        def on_list_fail(success, message):
            if _handled[0]:
                return
            if not success:
                _handled[0] = True
                _run_delete()

        win._suppress_list_fail_dialog = True
        win._suppress_op_finished += 1
        win.ftp_manager.list_files(
            win.shot_root_ftp_path, callback=on_list, fail_callback=on_list_fail
        )

    def _collect_add_files(self) -> "list | None":
        """Scan the shot root for the latest .nk and .mov files.

        Returns:
            List of path strings to upload, empty list if none found and user
            chose to continue, or None if the user aborted the upload.
        """
        win = self._win
        candidates = [
            p
            for p in Path(win.local_shot_path).iterdir()
            if p.is_file() and p.suffix.lower() in (".nk", ".mov")
        ]

        def _pick_best(files):
            v_files = [p for p in files if _re.search(r"v\d+$", p.stem, _re.IGNORECASE)]
            pool = v_files if v_files else files
            return max(pool, key=lambda p: tvt_utils.extract_trailing_version(p.stem))

        nk_files = [p for p in candidates if p.suffix.lower() == ".nk"]
        mov_files = [p for p in candidates if p.suffix.lower() == ".mov"]
        latest = []
        if nk_files:
            latest.append(_pick_best(nk_files))
        if mov_files:
            latest.append(_pick_best(mov_files))

        if not latest:
            proceed = self._wm.show_buttons_dialog(
                win,
                "No .nk or .mov Files Found",
                f"No .nk or .mov files were found in:\n{win.local_shot_path}\n\n"
                "Continue uploading renders without them?",
                buttons=[("Continue", True), ("Abort", False)],
                icon=QtWidgets.QMessageBox.Warning,
            )
            if not proceed:
                return None
            return []

        return [str(p) for p in latest]

    # ──────────────────────────────────────────────────────────────────────
    # LOW-LEVEL TRANSFER
    # ──────────────────────────────────────────────────────────────────────

    def start_download(self, remote_paths: list, local_dir: str, mode: str):
        """Initiate an FTP download.

        Args:
            remote_paths: FTP paths to download.
            local_dir: Local destination directory.
            mode: 'selected' or 'source' — determines which button becomes Cancel.
        """
        if not remote_paths:
            self._win.log("No FTP files or folders selected", "warning")
            self._unblock_all()
            return
        if mode == "selected":
            self._set_cancel_mode(self._win.download_selected_btn, "Cancel Download")
            self._set_cancel_btn_stylesheet(self._win.download_selected_btn)
        if mode == "source":
            self._set_cancel_mode(self._win.download_source_btn, "Cancel Download")
            self._set_cancel_btn_stylesheet(self._win.download_source_btn)
        self._win.log(f"Downloading {len(remote_paths)} item(s)...", "transfer")

        op_id = f"dl_{id(self)}"
        self._active_op_id = op_id
        self._queue_add(op_id, "Scanning", remote_paths, QtGui.QColor(60, 100, 180))

        def on_complete(success, message):
            self._pending_completion = None
            self._queue_remove(op_id)
            self._active_op_id = ""
            if success:
                # message is e.g. "Downloaded 9 files" — reformat to match upload style.
                count = message.split()[1] if message else ""
                self._win.log(f"Download completed: {count} files", "success")
            else:
                self._win.log(f"Download failed: {message}", "info")
            self._win._safe_refresh_ftp()

        self._wm.safe_connect_once(
            self._win.ftp_manager.files_scanned,
            lambda entries, _oid=op_id: self._on_files_scanned(_oid, entries),
            self._win,
        )

        try:
            self._win.ftp_manager.download_files(remote_paths, local_dir)
        except Exception as e:
            self._win.log(f"Download error: {e}", "error")
            self.restore_button()
            return

        # Defer the completion connection by one event-loop cycle so any
        # in-flight operation_finished from a preceding list worker fires
        # first and is not mistaken for the download's own completion.
        def _connect_completion():
            self._pending_completion = self._wm.safe_connect_once(
                self._win.ftp_manager.operation_finished, on_complete, self._win
            )
        self._wm.safe_timer(self._win, _connect_completion, 0)

    def start_upload(self, local_paths: list, remote_dir: str, mode: str):
        """Initiate an FTP upload.

        Args:
            local_paths: Local files/folders to upload.
            remote_dir: FTP destination directory.
            mode: 'selected' or 'renders' — determines which button becomes Cancel.
        """
        if not self._win.ftp_manager.is_connected():
            self._win.log("Cannot upload: not connected", "warning")
            return
        if not local_paths:
            self._win.log("No local files or folders selected for upload", "warning")
            return

        # Mirroring structure in FTP
        remote_dir_path = PureWindowsPath(remote_dir).as_posix()
        entries = _expand_paths(local_paths, remote_dir_path)
        file_count = len(entries)
        queue_paths = [lf for lf, _ in entries]
        queue_keys = [f"{rd}/{os.path.basename(lf)}" for lf, rd in entries]

        if mode == "renders":
            self._render_upload_total += file_count

        # Always add to queue widget so items are visible even when adding to a live upload.
        op_id = f"ul_{id(self)}"
        self._active_op_id = op_id
        self._queue_add(op_id, "Upload", queue_paths, QtGui.QColor(50, 150, 70), keys=queue_keys)

        if not self._win.ftp_manager.is_uploading():
            if mode == "selected":
                self._set_cancel_mode(self._win.upload_selected_btn, "Cancel Upload")
                self._set_cancel_btn_stylesheet(self._win.upload_selected_btn)

                def on_complete(success, message):
                    self._pending_completion = None
                    self._queue_remove(op_id)
                    self._active_op_id = ""
                    if success:
                        self._win.log(
                            f"Upload completed: {file_count} files", "success"
                        )
                        if (
                            self._win.del_up_zip.isChecked()
                            and self._win.zip_up_files.isChecked()
                        ):
                            try:
                                archive_path = local_paths[0]
                                archive_name = self.archive_name + ".zip"
                                os.remove(archive_path)
                                self._win.log(
                                    f"Archive {archive_name} successfully deleted",
                                    "success",
                                )
                            except Exception as e:
                                self._win.log(
                                    f"Error when deleting {archive_name}: {e}", "error"
                                )
                    else:
                        self._win.log(f"Upload finished: {message}", "info")

                self._pending_completion = self._wm.safe_connect_once(
                    self._win.ftp_manager.operation_finished, on_complete, self._win
                )

            if mode == "renders":
                self._set_cancel_mode(self._win.upload_renders_btn, "Cancel Upload")
                self._set_cancel_btn_stylesheet(self._win.upload_renders_btn)

                def on_complete_renders(success, message):
                    self._pending_completion = None
                    self._queue_remove(op_id)
                    self._active_op_id = ""
                    total = self._render_upload_total
                    self._render_upload_total = 0
                    if success:
                        self._win.log(f"Upload completed: {total} files", "success")
                    else:
                        self._win.log(f"Upload finished: {message}", "info")

                self._pending_completion = self._wm.safe_connect_once(
                    self._win.ftp_manager.operation_finished,
                    on_complete_renders,
                    self._win,
                )

        try:
            self._win.ftp_manager.upload_files(entries)
        except Exception as e:
            self._win.log(f"Upload error: {e}", "error")
            self.restore_button()

    def cancel(self):
        """Cancel the active transfer and restore all buttons to their original state."""
        if self._pending_completion is not None:
            try:
                self._win.ftp_manager.operation_finished.disconnect(
                    self._pending_completion
                )
            except Exception:
                pass
            self._pending_completion = None
        self._win.ftp_manager.stop_current_operation()
        self._queue_remove(self._active_op_id)
        self._active_op_id = ""
        self._win._safe_refresh_ftp()
        self.restore_button()

    # ──────────────────────────────────────────────────────────────────────
    # FOLDER OPERATIONS
    # ──────────────────────────────────────────────────────────────────────

    def create_ftp_folder(self, current_ftp_path: str):
        """Prompt for a name and create a new folder on the FTP server."""
        if not self._win.ftp_manager.is_connected():
            self._win.log("Cannot create folder: not connected", "warning")
            return
        if self._win.ftp_manager.is_busy():
            self._win.log("Cannot create folder: operation in progress", "warning")
            return

        folder_name = self._wm.show_input_field_dialog(
            self._win,
            title="Create FTP Folder",
            icon=QtWidgets.QMessageBox.Question,
        )
        if not folder_name:
            return

        folder_name = folder_name.replace(" ", "_")
        remote_path = f"{current_ftp_path}/{folder_name}".replace("//", "/")
        self._win.log(f"Creating folder: {remote_path}", "info")
        self._block_for_other_op()

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

    def create_local_folder(self, current_local_path: str):
        """Prompt for a name and create a new folder on the local filesystem."""
        if not current_local_path:
            self._win.log("Cannot create folder: no local path set", "warning")
            return

        folder_name = self._wm.show_input_field_dialog(
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

    # ──────────────────────────────────────────────────────────────────────
    # FILE OPERATIONS
    # ──────────────────────────────────────────────────────────────────────

    def delete_selected(self, ftp_paths: list, local_paths: list):
        """Delete selected FTP or local items, with confirmation dialogs."""
        if ftp_paths:
            confirm = self._wm.show_buttons_dialog(
                self._win,
                "Confirm FTP Delete",
                f"Delete {len(ftp_paths)} selected item(s) from FTP server?\n\n",
                buttons=[("Delete", True), ("Cancel", False)],
                icon=QtWidgets.QMessageBox.Warning,
            )
            if confirm:
                self._block_for_other_op()
                self._win.log(f"Deleting {len(ftp_paths)} items...", "info")
                del_op_id = f"del_{id(self)}"
                self._active_op_id = del_op_id
                self._queue_add(del_op_id, "Scanning", ftp_paths, QtGui.QColor(160, 70, 70))

                def on_delete_complete(*_):
                    self._queue_remove(del_op_id)
                    self._active_op_id = ""

                self._wm.safe_connect_once(
                    self._win.ftp_manager.files_scanned,
                    lambda entries, _oid=del_op_id: self._on_delete_scanned(_oid, entries),
                    self._win,
                )
                self._wm.safe_connect_once(
                    self._win.ftp_manager.operation_finished,
                    on_delete_complete,
                    self._win,
                )
                self._win.ftp_manager.delete_files(ftp_paths)
        elif local_paths:
            confirm = self._wm.show_buttons_dialog(
                self._win,
                "Confirm Local Delete",
                f"Permanently delete {len(local_paths)} selected local item(s)?\n\n",
                buttons=[("Delete", True), ("Cancel", False)],
                icon=QtWidgets.QMessageBox.Warning,
            )
            if confirm:
                self._win.log(f"Deleting {len(ftp_paths)} items...", "info")
                self._win.local_panel.delete_files(local_paths)
        else:
            self._win.log("No files selected for deletion", "warning")

    def create_archive(self, local_paths: list, archive_name="archive"):

        win = self._win
        self.archive_name = archive_name
        entries = _expand_paths(local_paths, "")

        if not entries:
            win.log("No files to archive", "warning")
            return local_paths

        archive_path = Path(win.local_shot_path) / f"{archive_name}.zip"

        with zipfile.ZipFile(
            archive_path, mode="w", compression=zipfile.ZIP_DEFLATED
        ) as zf:
            for local_file, _ in entries:
                arc_path = os.path.relpath(local_file, win.local_shot_path)
                zf.write(local_file, arcname=arc_path)

        local_paths = []
        local_paths.append(archive_path)
        win.log(f"Archive {archive_name}.zip successfully created", "success")

        return local_paths

    # ──────────────────────────────────────────────────────────────────────
    # PROGRESS & STATS
    # ──────────────────────────────────────────────────────────────────────

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
        """Called every 200 ms to pull real-time stats from the active transfer worker."""
        try:
            worker = self._win.ftp_manager._current_worker
            if worker is not None and hasattr(worker, "emit_stats_if_due"):
                worker.emit_stats_if_due()
            if worker is not None and hasattr(worker, "get_file_progress"):
                self._apply_file_progress(worker.get_file_progress())
        except Exception:
            pass

    def reset_ui(self):
        """Reset the progress bar and all status labels."""
        if self._win.progress_bar:
            self._win.progress_bar.setValue(0)
        for attr in ("speed_status", "total_status", "eta_status"):
            lbl = getattr(self._win, attr, None)
            if lbl:
                lbl.setText("")

    # ──────────────────────────────────────────────────────────────────────
    # BUTTON LIFECYCLE
    # ──────────────────────────────────────────────────────────────────────

    def _set_cancel_mode(self, btn: Optional[QtWidgets.QPushButton], cancel_text: str):
        """Change `btn` to a cancel button and disable all other transfer buttons."""
        if not btn:
            return
        self._cancel_btns.append((btn, btn.text()))
        btn.setText(cancel_text)
        try:
            btn.clicked.disconnect()
        except (RuntimeError, TypeError):
            pass
        btn.clicked.connect(self.cancel)
        self._unblock_transfer_btn(btn)

    def restore_button(self):
        """Restore the cancel button(s) to their original state."""
        if not self._cancel_btns:
            return
        for btn, original_text in self._cancel_btns:
            btn.setText(original_text)
            try:
                btn.clicked.disconnect()
            except (RuntimeError, TypeError):
                pass
            if btn is self._win.download_selected_btn:
                btn.clicked.connect(self._win._download_selected)
                self._win.download_selected_btn.setStyleSheet("")
            elif btn is self._win.download_source_btn:
                btn.clicked.connect(self._win._download_source)
                self._win.download_source_btn.setStyleSheet("")
            elif btn is self._win.upload_renders_btn:
                btn.clicked.connect(self._win._upload_renders)
                self._win.upload_renders_btn.setStyleSheet("")
            elif btn is self._win.upload_selected_btn:
                btn.clicked.connect(self._win._upload_selected)
                self._win.upload_selected_btn.setStyleSheet("")
            else:
                self._win.log(f"restore_button: unknown button {btn}", "warning")
        self._cancel_btns.clear()

    def _unblock_transfer_btn(self, active_btn: QtWidgets.QPushButton):
        """Enable only the active cancel button; disable all other transfer buttons."""
        for name in _TRANSFER_BTN_NAMES:
            btn = getattr(self._win, name, None)
            if btn:
                btn.setEnabled(btn is active_btn)


# ──────────────────────────────────────────────────────────────────────────────
# MODULE HELPERS
# ──────────────────────────────────────────────────────────────────────────────


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


def _expand_paths(local_paths: List[str], remote_dir: str) -> List[tuple]:
    """
    Expand a mixed list of files and directories into (local_file, remote_target_dir)
    tuples, mirroring directory structure under remote_dir.
    """
    entries = []
    remote_dir = remote_dir.rstrip("/")

    for path in local_paths:
        if os.path.isfile(path):
            entries.append((path, remote_dir))
        elif os.path.isdir(path):
            base = os.path.dirname(path.rstrip("/\\"))
            for root, _dirs, files in os.walk(path):
                rel = os.path.relpath(root, base).replace("\\", "/")
                target = f"{remote_dir}/{rel}".replace("//", "/")
                for filename in files:
                    entries.append((os.path.join(root, filename), target))

    return entries
