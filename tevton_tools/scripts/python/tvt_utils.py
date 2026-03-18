import hou
import os
import re
import sys
from pathlib import Path
from qt_shim import QtWidgets, QtCore


def reload_packages():
    """
    Safe development reload:
    - Closes all windows
    - Clears hou.session window references
    - Removes pipeline modules from sys.modules
    - Reloads package definition
    """
    print("---- DEV RELOAD START ----")

    _cleanup_session_windows()
    _cleanup_pipeline_modules()

    package_path = (
        hou.text.expandString("$HOUDINI_USER_PREF_DIR/packages/") + "tevton_tools.json"
    )

    try:
        hou.ui.reloadPackage(package_path)
    except Exception as e:
        print(f"Package reload error: {e}")

    print("---- DEV RELOAD COMPLETE ----")


def _cleanup_session_windows():
    """
    Close and remove all stored UI windows from hou.session.
    Prevents old class instances from surviving reload.
    """
    for attr in dir(hou.session):
        if attr.endswith("_window"):
            try:
                win = getattr(hou.session, attr)

                if isinstance(win, QtWidgets.QWidget):
                    try:
                        win.isVisible()  # raises RuntimeError if C++ object was already deleted
                        win.blockSignals(True)
                        win.close()
                        win.deleteLater()
                    except RuntimeError:
                        pass  # уже удалён Qt — просто идём дальше

                delattr(hou.session, attr)

            except Exception as e:
                print(f"Failed to cleanup window {attr}: {e}")


def _cleanup_pipeline_modules():
    """
    Remove all project modules from sys.modules
    so they are fully re-imported after package reload.
    """
    prefixes = ("pipeline", "ftp", "tvt_utils", "ui_state", "tools")

    modules_to_delete = [
        name
        for name in sys.modules
        if any(name == p or name.startswith(p + ".") for p in prefixes)
        and not name.startswith("ftplib")
    ]

    for name in modules_to_delete:
        try:
            del sys.modules[name]
            print(f"Cleared: {name}")
        except Exception:
            pass


def reload_shelves():

    shelves_path = hou.text.expandString("$TVT/toolbar")

    for root, dirs, files in os.walk(shelves_path):
        for file in files:
            shelf_name = file.removesuffix(".shelf")
            if file.endswith(".shelf"):
                shelf_path = os.path.join(root, file).replace(os.sep, "/")
                hou.shelves.loadFile(shelf_path)
                print(f"Shelf {shelf_name} reloaded")


def is_valid_path(path):
    """
    Check if a given path points to an existing directory.
    Returns the path if valid, otherwise shows a warning dialog.
    """
    if Path(hou.text.expandString(path)).is_dir():
        return path
    else:
        hou.ui.displayMessage(
            f"Path: {path} invalid.\nPlease try again.", title="Warning"
        )


def create_field_validator(widget, allowed_symbols="", allow_empty=False):
    """
    Create a validator for a specific widget
    """
    from string import ascii_letters, digits

    valid_chars = set(ascii_letters + digits + allowed_symbols)

    def validate():
        if isinstance(widget, QtWidgets.QLineEdit):
            text = widget.text()
            is_valid = all(c in valid_chars for c in text) and (
                allow_empty or bool(text)
            )

        elif isinstance(widget, (QtWidgets.QPlainTextEdit, QtWidgets.QTextEdit)):
            text = widget.toPlainText()
            items = [f.strip() for f in text.split(",") if f.strip()]

            if not items and not allow_empty:
                is_valid = False
            else:
                is_valid = all(all(c in valid_chars for c in f) for f in items)
        else:
            return False

        set_widget_validity(widget, is_valid)
        return is_valid

    return validate


def create_form_validator(validators):
    """
    Create a validator that checks multiple fields
    """

    def validate_form():
        return all(v() for v in validators)

    return validate_form


def force_upper(widget):
    """
    Forcing upper symbols in widget, for stability block signal for change char
    """
    widget.blockSignals(True)
    widget.setText(widget.text().upper())
    widget.blockSignals(False)


def set_widget_validity(widget, is_valid):
    """
    Check validity of text in widget and set red highlight to borders if False
    """
    widget.setProperty("valid", is_valid)
    widget.style().unpolish(widget)
    widget.style().polish(widget)


def on_item_clicked(widget, index):
    """
    Generic click handler for project/shot/file lists.
    widget - ProjectManager instance
    index - clicked item index
    """

    if not index.isValid():
        return

    sender = widget.sender()

    # Select item
    sender.selectionModel().select(
        index,
        QtCore.QItemSelectionModel.ClearAndSelect | QtCore.QItemSelectionModel.Rows,
    )

    # Dispatch based on which list was clicked
    if sender == widget.project_list:
        widget.load_shot_list()
        widget.file_list.clear()
        widget.activate_project()
    elif sender == widget.shot_list:
        widget.load_file_list()
    # Для file_list ничего не делаем


def open_file_or_folder(path, file_type=None):
    """
    Universal function to open files or folders with default system applications.

    Args:
        path (str): Path to file or folder to open
    """
    import platform
    import subprocess

    if not os.path.exists(path):
        hou.ui.displayMessage(
            f"Path not found:\n{path}", severity=hou.severityType.Error, title="Error"
        )
        return

    hip_extensions = [".hip", ".hiplc", ".hipnc"]

    if file_type in hip_extensions:
        if check_houdini_file(path):
            hou.hipFile.load(path)
            return
        else:
            return
    try:
        system = platform.system()

        if system == "Windows":
            os.startfile(path)
        elif system == "Darwin":
            subprocess.Popen(["open", path])
        elif system == "Linux":
            subprocess.Popen(["xdg-open", path])
        else:
            hou.ui.displayMessage(
                f"Unsupported operating system: {system}",
                severity=hou.severityType.Error,
                title="Error",
            )

    except Exception as e:
        hou.ui.displayMessage(
            f"Error opening {'folder' if os.path.isdir(path) else 'file'}: {str(e)}",
            severity=hou.severityType.Error,
            title="Error",
        )


def check_houdini_file(path):
    """
    Check if the Houdini file exists and is not the current open file.
    Returns True if valid, False otherwise.
    """
    if not os.path.exists(path):
        hou.ui.displayMessage(
            f"File not found:\n{path}", severity=hou.severityType.Error, title="Error"
        )
        return False

    elif path == hou.hipFile.path():
        hou.ui.displayMessage(
            "You trying to open current file!",
            title="Error",
            severity=hou.severityType.Error,
        )
        return False

    else:
        return True


def create_empty_hip(path, fps=24, start_frame=1001, end_frame=1120):
    """
    Create a new .hip file via hython without touching
    the current Houdini session.
    """

    import subprocess
    import tempfile

    hython_path = os.path.join(hou.getenv("HFS"), "bin", "hython")

    hy_script = f"""
import hou

hou.hipFile.clear(suppress_save_prompt=True)

hou.setFps({fps})
hou.playbar.setFrameRange({start_frame}, {end_frame})
hou.playbar.setPlaybackRange({start_frame}, {end_frame})
hou.setFrame({start_frame})

hou.hipFile.save(r"{path}")
"""
    with tempfile.NamedTemporaryFile(delete=False, suffix=".py") as tmp:
        tmp.write(hy_script.encode("utf-8"))
        tmp_path = tmp.name

    try:
        subprocess.check_call([hython_path, tmp_path])
    except subprocess.CalledProcessError as e:
        hou.ui.displayMessage(
            f"Failed to create hip file:\n{str(e)}",
            severity=hou.severityType.Error,
            title="Error",
        )
        return False
    finally:
        os.remove(tmp_path)

    return True


def open_as_new_session(hip_path: str):
    """
    Open a .hip file in a new Houdini window (cross-platform)
    """
    import subprocess

    hfs_path = os.environ.get("HFS", "")

    if os.name == "nt":
        exe = str(Path(hfs_path) / "bin" / "houdini.exe") if hfs_path else "houdini.exe"
        subprocess.Popen(
            [exe, hip_path],
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS,
        )
    else:
        exe = str(Path(hfs_path) / "bin" / "houdini") if hfs_path else "houdini"
        subprocess.Popen([exe, hip_path], start_new_session=True)


def get_user_tool_dir():
    """
    Creates necessary subfolders and empty JSON file to store project data, if they don't exist.
    Returns the path to directory with projects data, logs, etc.
    """
    if os.name == "nt":
        tool_dir = Path(os.getenv("APPDATA")) / "TVT"
    else:
        tool_dir = Path.home() / ".tvt" / "TVT"

    for f in ["config", "logs", "cache", "temp"]:
        (tool_dir / f).mkdir(parents=True, exist_ok=True)

    projects_data = tool_dir / "projects_data.json"

    if not projects_data.exists():
        projects_data.write_text("{}", encoding="utf-8")

    return tool_dir


def normalize_project_path(path, project_name):
    """
    Normalize a selected directory to ensure it represents a valid project root.
    """
    path = os.path.normpath(path)

    if os.path.basename(path) != project_name:
        path = os.path.join(path, project_name)

    return path.replace(os.sep, "/")


def set_connection_status(widget, state):
    """
    Update connection status indicators in the UI.

    Args:
        widget: The UI object containing con_status_ind, con_status, check_con_btn.
        state (str): One of "connected", "disconnected", "connecting".
        error_msg (str, optional): If provided, shows a popup window and forces 'disconnected' state.
    """
    colors = {
        "connected": "#2ecc71",
        "disconnected": "#e74c3c",
        "connecting": "#f1c40f",
    }
    texts = {
        "connected": "Connected",
        "disconnected": "Disconnected",
        "connecting": "Connecting...",
    }

    # Update indicator color
    if hasattr(widget, "con_status_ind") and widget.con_status_ind:
        color = colors.get(state, "#e74c3c")
        widget.con_status_ind.setStyleSheet(
            f"background-color: {color}; border-radius: 6px;"
        )

    # Update status text
    if hasattr(widget, "con_status") and widget.con_status:
        widget.con_status.setText(texts.get(state, "Disconnected"))


def extract_trailing_version(name: str) -> int:
    """Extract the version number from a name string.
    Prefers trailing digits, falls back to embedded _NNN or vNNN patterns.
    Examples: 'v001' -> 1, '02' -> 2, 'snow_v7' -> 7, 'bbb_001_bbb' -> 1, 'fire' -> -1
    """
    m = re.search(r"(\d+)$", name)
    if m:
        return int(m.group(1))
    m = re.search(r"[_v](\d+)", name, re.IGNORECASE)
    return int(m.group(1)) if m else -1


def latest_version_dir(dirs) -> "Path | None":
    """Return the Path with the highest trailing version number from an iterable of dirs."""
    dirs = list(dirs)
    if not dirs:
        return None
    return max(dirs, key=lambda d: (extract_trailing_version(d.name), d.name))


def latest_version_files(files) -> list:
    """Return one Path per base name (highest trailing version) from an iterable of file Paths.
    Groups by stem with trailing version token stripped.
    Example: comp_v001.nk + comp_v002.nk -> [comp_v002.nk]
    """
    groups = {}
    for p in files:
        key = (
            re.sub(r"[_.]?v?\d+$", "", p.stem, flags=re.IGNORECASE).rstrip("_.")
            + p.suffix
        )
        if key not in groups or extract_trailing_version(
            p.stem
        ) > extract_trailing_version(groups[key].stem):
            groups[key] = p
    return list(groups.values())


class ConnectionAnimator(QtCore.QObject):
    """
    UI-only helper to animate connection status with dots and timeout.

    Can be driven manually (start/stop) or bound to FTPManager signals via bind().
    """

    timeout_reached = QtCore.Signal()

    def __init__(self, widget, timeout=10000, interval=400):
        super().__init__(widget)

        self.widget = widget
        self._dots = 0

        self._ani_timer = QtCore.QTimer(self)
        self._ani_timer.timeout.connect(self._update_dots)

        self._timeout_timer = QtCore.QTimer(self)
        self._timeout_timer.setSingleShot(True)
        self._timeout_timer.timeout.connect(self._handle_timeout)

        self._timeout = timeout
        self._interval = interval

    def bind(self, busy_signal, result_signal):
        """
        Automatically drive animation from FTPManager signals.

        Args:
            busy_signal:   busy_changed(bool) — starts animation when True.
            result_signal: connection_changed(bool) or connection_checked(bool, str)
                           — stops animation with correct success state.
                           First argument is always the success bool.
        """
        busy_signal.connect(lambda busy: self.start() if busy else None)
        result_signal.connect(lambda *args: self.stop(success=args[0]))

    def start(self):
        self.stop()

        set_connection_status(self.widget, "connecting")

        self._dots = 0
        self._ani_timer.start(self._interval)
        self._timeout_timer.start(self._timeout)

    def stop(self, success=False):
        self._ani_timer.stop()
        self._timeout_timer.stop()

        if success:
            set_connection_status(self.widget, "connected")
        else:
            set_connection_status(self.widget, "disconnected")

    def abort(self):
        """Immediately abort connection animation without waiting."""
        self._ani_timer.stop()
        self._timeout_timer.stop()
        set_connection_status(self.widget, "disconnected")

    def _update_dots(self):
        if not getattr(self.widget, "con_status", None):
            return

        self._dots = (self._dots % 3) + 1
        self.widget.con_status.setText(f"Connecting{'.' * self._dots}")

    def _handle_timeout(self):
        self.stop()
        self.timeout_reached.emit()
