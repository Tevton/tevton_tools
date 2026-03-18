import hou
import weakref
from datetime import datetime
from pathlib import Path
from typing import Type, Optional, Dict, Any, Callable
from qt_shim import QtCore, QtWidgets, QtGui
from functools import wraps
from config.config import USER_DATA_PATH


class WindowManager:
    """
    Centralized manager for all tool windows.

    Features:
    - Prevents duplicate windows
    - Handles parent-child relationships
    - Manages window lifecycle (open/close)
    - Provides safe signal connections
    - Automatic cleanup on close
    - Centralized logging system (SINGLE SOURCE OF TRUTH)
    - Singleton pattern (one manager per Houdini session)
    """

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True

        # Store windows with weak references to avoid memory leaks
        self._windows: Dict[str, weakref.ref] = {}

        # Store parent-child relationships
        self._parent_child: Dict[int, list] = {}

        # Store window class references
        self._window_classes: Dict[str, Type] = {}

        # Track which windows are currently being closed
        self._closing_windows: set = set()

        # Store context menu
        self._context_menus: Dict[int, Callable] = {}

        # =====================================================
        # CENTRALIZED LOGGING SYSTEM - ONLY HERE
        # =====================================================
        self._log_widgets: Dict[str, weakref.ref] = {}
        self._last_log: Dict[str, tuple] = {}  # window_id -> (message, level)
        self._log_file = Path(USER_DATA_PATH) / "logs" / "tevton_tools.log"
        if self._log_file.exists() and self._log_file.stat().st_size > 5 * 1024 * 1024:
            self._log_file.unlink()
        self._log_levels = {
            "success": "✅",
            "error": "❌",
            "warning": "⚠️",
            "connect": "🔌",
            "transfer": "📁",
            "info": "ℹ️",
        }

    # ------------------------------------------------------------------
    # Main window show method
    # ------------------------------------------------------------------

    def show_window(
        self,
        window_class,
        parent=None,
        callback_signal=None,
        centering="screen",
        **kwargs,
    ):
        """
        Universal function to show UI windows in Houdini.
        """
        # Generate unique ID for this window
        window_id = self._get_window_id(window_class, **kwargs)

        # Check if window already exists
        existing = self._get_window(window_id)
        if existing:
            return self._focus_window(existing)

        # Also check hou.session for backward compatibility
        attr_name = self._get_attr_name(window_class, **kwargs)
        if hasattr(hou.session, attr_name):
            try:
                existing_win = getattr(hou.session, attr_name)
                if existing_win is not None:
                    try:
                        existing_win.isVisible()
                        return self._focus_window(existing_win)
                    except RuntimeError:
                        delattr(hou.session, attr_name)
            except:
                pass

        # Create new window instance
        win = window_class(parent=parent, **kwargs)

        # Center the window based on parameter
        if centering == "screen":
            self.center_window(win)
        elif centering == "parent" and parent:
            self.center_window_on_parent(win, parent)

        # Store in hou.session for backward compatibility
        setattr(hou.session, attr_name, win)

        # Store in our manager
        self._windows[window_id] = weakref.ref(win)
        self._window_classes[window_id] = window_class

        # Handle parent blocking
        if parent:
            self._setup_parent_child_relationship(parent, win, window_id)

        # Connect callback signal
        if callback_signal:
            self._connect_callback(win, callback_signal, window_id)

        # Track window closure
        win.destroyed.connect(lambda: self._on_window_closed(window_id, attr_name))

        # Show window
        win.show()

        return win

    def center_window(self, window: QtWidgets.QWidget):
        """Center window on the primary screen."""
        window.adjustSize()
        screen = QtWidgets.QApplication.primaryScreen().geometry()
        x = screen.x() + (screen.width() - window.width()) // 2
        y = screen.y() + (screen.height() - window.height()) // 2
        window.move(x, y)

    def center_window_on_parent(
        self, window: QtWidgets.QWidget, parent: QtWidgets.QWidget
    ):
        """Center window relative to its parent window."""
        try:
            if parent and parent.isVisible():
                parent_center = parent.geometry().center()
                x = parent_center.x() - window.width() // 2
                y = parent_center.y() - window.height() // 2
                window.move(x, y)
                return
        except RuntimeError:
            pass
        self.center_window(window)

    def _get_attr_name(self, window_class, **kwargs) -> str:
        """Generate hou.session attribute name for backward compatibility."""
        mode_suffix = ""
        if "mode" in kwargs and kwargs["mode"] is not None:
            try:
                mode_value = kwargs["mode"]
                mode_suffix = f"_{mode_value.name.lower()}"
            except Exception:
                pass
        return f"{window_class.__name__.lower()}{mode_suffix}_window"

    def _get_window_id(self, window_class, **kwargs) -> str:
        """Generate unique ID for a window instance."""
        base_id = window_class.__name__
        if "mode" in kwargs and kwargs["mode"] is not None:
            try:
                base_id += f"_{kwargs['mode'].name}"
            except:
                pass
        if "project_name" in kwargs and kwargs["project_name"]:
            base_id += f"_{kwargs['project_name']}"
        if "shot_name" in kwargs and kwargs["shot_name"]:
            base_id += f"_{kwargs['shot_name']}"
        return base_id

    def _get_window(self, window_id: str) -> Optional[QtWidgets.QWidget]:
        """Get window by ID if it still exists."""
        if window_id not in self._windows:
            return None
        ref = self._windows[window_id]
        window = ref()
        if window is None:
            del self._windows[window_id]
            return None
        try:
            window.isVisible()
            return window
        except RuntimeError:
            del self._windows[window_id]
            return None

    def _focus_window(self, window: QtWidgets.QWidget):
        """Focus an existing window."""
        try:
            if window.isMinimized():
                window.setWindowState(QtCore.Qt.WindowActive)
            window.show()
            window.raise_()
            window.activateWindow()
        except RuntimeError:
            pass
        return window

    def _setup_parent_child_relationship(self, parent, child, child_id: str):
        """Set up parent-child relationship with blocking."""
        parent_id = id(parent)
        if parent_id not in self._parent_child:
            self._parent_child[parent_id] = []
        self._parent_child[parent_id].append(child_id)
        try:
            parent.setEnabled(False)
        except RuntimeError:
            pass

        def on_child_closed():
            if (
                parent_id in self._parent_child
                and child_id in self._parent_child[parent_id]
            ):
                self._parent_child[parent_id].remove(child_id)
            try:
                if parent:
                    if (
                        parent_id not in self._parent_child
                        or not self._parent_child[parent_id]
                    ):
                        parent.setEnabled(True)
            except RuntimeError:
                pass

        child.destroyed.connect(on_child_closed)

    def _connect_callback(self, window, callback_signal: dict, window_id: str):
        """Connect callback signal safely."""
        signal_name = callback_signal.get("signal")
        slot_func = callback_signal.get("slot")
        if not signal_name or not slot_func:
            return
        signal = getattr(window, signal_name, None)
        if not signal:
            return

        @wraps(slot_func)
        def safe_slot(*args, **kwargs):
            if self._get_window(window_id) is None:
                return
            try:
                return slot_func(*args, **kwargs)
            except Exception as e:
                print(f"Error in callback: {e}")

        signal.connect(safe_slot, QtCore.Qt.UniqueConnection)

    def _on_window_closed(self, window_id: str, attr_name: str):
        """Called when a window is destroyed."""
        if window_id in self._windows:
            del self._windows[window_id]
        if window_id in self._window_classes:
            del self._window_classes[window_id]
        if hasattr(hou.session, attr_name):
            try:
                delattr(hou.session, attr_name)
            except:
                pass
        if window_id in self._log_widgets:
            del self._log_widgets[window_id]
        if window_id in self._last_log:
            del self._last_log[window_id]

        to_remove = []
        for parent_id, children in self._parent_child.items():
            if window_id in children:
                children.remove(window_id)
            if not children:
                to_remove.append(parent_id)
        for parent_id in to_remove:
            del self._parent_child[parent_id]

    # =====================================================
    # CENTRALIZED LOGGING - SINGLE SOURCE OF TRUTH
    # =====================================================

    def register_log_widget(self, window, log_widget):
        """Register a log widget for a window."""
        window_id = self._get_window_id(
            window.__class__,
            project_name=getattr(window, "project_name", None),
            shot_name=getattr(window, "shot_name", None),
        )
        self._log_widgets[window_id] = weakref.ref(log_widget)

    def should_log_message(self, message: str, level: str = "info") -> bool:
        """
        Determine if a message should be logged based on content.
        """
        msg_lower = message.lower()
        if msg_lower.startswith("created:"):
            return False
        if msg_lower.startswith("deleted folder:"):
            return False
        if message.startswith("✓ Listed"):
            return False
        if message.startswith("✓ Uploaded"):
            return False
        if message.startswith("✓ Renamed to:"):
            return False
        if message.startswith("✅ Folder created:"):
            return False
        return True

    def log(
        self, window, message: str, level: str = "info", skip_duplicates: bool = True
    ):
        """Centralized logging - THE ONLY logging method in the entire app."""
        if not self.should_log_message(message, level):
            return

        window_id = self._get_window_id(
            window.__class__,
            project_name=getattr(window, "project_name", None),
            shot_name=getattr(window, "shot_name", None),
        )

        if window_id not in self._log_widgets:
            print(f"[{level.upper()}] {message}")
            self._write_to_log_file(f"[{level.upper()}] [{datetime.now().strftime('%H:%M:%S')}] {message}\n")
            return

        log_widget_ref = self._log_widgets[window_id]
        log_widget = log_widget_ref()
        if log_widget is None:
            return

        if skip_duplicates:
            last = self._last_log.get(window_id)
            if last and last[0] == message and last[1] == level:
                return
            self._last_log[window_id] = (message, level)

        timestamp = datetime.now().strftime("%H:%M:%S")
        icon = self._log_levels.get(level, "•")

        try:
            sb = log_widget.verticalScrollBar()
            at_bottom = sb.value() >= sb.maximum() - 4
            log_widget.appendPlainText(f"{icon} [{timestamp}] {message}")
            if at_bottom:
                sb.setValue(sb.maximum())
        except RuntimeError:
            pass
        self._write_to_log_file(f"{icon} [{timestamp}] {message}\n")

    def _write_to_log_file(self, line: str):
        try:
            with self._log_file.open("a", encoding="utf-8") as f:
                f.write(line)
        except OSError:
            pass

    def clear_logs(self, window):
        """Clear all logs for a window."""
        window_id = self._get_window_id(
            window.__class__,
            project_name=getattr(window, "project_name", None),
            shot_name=getattr(window, "shot_name", None),
        )
        if window_id in self._log_widgets:
            log_widget_ref = self._log_widgets[window_id]
            log_widget = log_widget_ref()
            if log_widget:
                log_widget.clear()
            self._last_log[window_id] = None

    # ------------------------------------------------------------------
    # Dialog helpers
    # ------------------------------------------------------------------

    def show_buttons_dialog(
        self,
        parent=None,
        title=None,
        message="",
        buttons=None,
        font_size=8,
        icon=QtWidgets.QMessageBox.NoIcon,
    ):
        """
        Show a dialog with customizable buttons.

        Args:
            parent: Parent widget
            title: Dialog window title
            message: Message text to display
            buttons: List of (button_text, is_accept) tuples
                    - is_accept=True: accept button (returns True)
                    - is_accept=False: reject button (returns False)
                    Default: [("OK", True)]
            font_size: Font size in points
            icon: Standard icon (Warning, Critical, Question, Information, NoIcon)
                Warning/Critical trigger system beep

        Returns:
            True if accept button clicked, False otherwise

        Example:
            if self.show_buttons_dialog(parent, "Confirm", "Proceed?",
                                    buttons=[("Yes", True), ("No", False)]):
                print("User clicked Yes")
        """
        if buttons is None:
            buttons = [("OK", True)]

        dialog = QtWidgets.QDialog(parent)
        dialog.setWindowTitle(title)
        if icon in [
            QtWidgets.QMessageBox.Warning,
            QtWidgets.QMessageBox.Critical,
        ]:
            QtWidgets.QApplication.beep()

        font = self.menu_font(font_size)

        layout = QtWidgets.QVBoxLayout()
        layout.setContentsMargins(15, 15, 15, 15)
        layout.setSpacing(15)

        if icon != QtWidgets.QMessageBox.NoIcon:
            icon_layout = QtWidgets.QHBoxLayout()
            icon_layout.setSpacing(20)

            icon_label = QtWidgets.QLabel()
            icon_pixmap = None
            style = parent.style() if parent else QtWidgets.QApplication.style()

            if icon == QtWidgets.QMessageBox.Warning:
                icon_pixmap = style.standardIcon(
                    QtWidgets.QStyle.SP_MessageBoxWarning
                ).pixmap(48, 48)
            elif icon == QtWidgets.QMessageBox.Critical:
                icon_pixmap = style.standardIcon(
                    QtWidgets.QStyle.SP_MessageBoxCritical
                ).pixmap(48, 48)
            elif icon == QtWidgets.QMessageBox.Question:
                icon_pixmap = style.standardIcon(
                    QtWidgets.QStyle.SP_MessageBoxQuestion
                ).pixmap(48, 48)
            elif icon == QtWidgets.QMessageBox.Information:
                icon_pixmap = style.standardIcon(
                    QtWidgets.QStyle.SP_MessageBoxInformation
                ).pixmap(48, 48)

            if icon_pixmap:
                icon_label.setPixmap(icon_pixmap)
                icon_layout.addWidget(icon_label)

            msg_label = QtWidgets.QLabel(message)
            msg_label.setFont(font)
            msg_label.setWordWrap(True)
            icon_layout.addWidget(msg_label, 1)
            layout.addLayout(icon_layout)
        else:
            msg_label = QtWidgets.QLabel(message)
            msg_label.setFont(font)
            layout.addWidget(msg_label)

        line = QtWidgets.QFrame()
        line.setFrameShape(QtWidgets.QFrame.HLine)
        line.setFrameShadow(QtWidgets.QFrame.Sunken)
        line.setLineWidth(1)
        layout.addWidget(line)

        button_layout = QtWidgets.QHBoxLayout()
        button_layout.setSpacing(10)
        button_layout.addStretch()

        for btn_text, is_accept in buttons:
            btn = QtWidgets.QPushButton(btn_text)
            btn.setFont(font)
            btn.setMinimumSize(70, 30)
            btn.setSizePolicy(QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Fixed)
            if is_accept:
                btn.clicked.connect(dialog.accept)
            else:
                btn.clicked.connect(dialog.reject)
            button_layout.addWidget(btn)

        layout.addLayout(button_layout)
        dialog.setLayout(layout)

        # Expand to fit content and then block expanding
        dialog.adjustSize()
        size = dialog.size()
        dialog.setFixedSize(size.width() + 20, size.height())

        return dialog.exec() == QtWidgets.QDialog.Accepted

    def show_input_field_dialog(
        self,
        parent=None,
        title="Create",
        initial_text="",
        buttons=None,
        font_size=8,
        icon=QtWidgets.QMessageBox.NoIcon,
    ):
        """
        Show a folder name input dialog.

        Args:
            parent: Parent widget
            title: Dialog window title
            initial_text: Initial text in the input field
            buttons: List of (button_text, is_accept) tuples
                    - is_accept=True for confirmation button (enables Enter key)
                    - is_accept=False for cancel button
                    Default: [("Create", True), ("Cancel", False)]
            font_size: Font size for the dialog
            icon: QMessageBox icon type

        Returns:
            The field name string if user clicked OK, None if cancelled

        Example:
            # Standard usage
            name = self.input_field_dialog(parent, "New Folder")

            # Custom buttons
            name = self.input_field_name_dialog(
                parent,
                "Save As",
                buttons=[
                    ("Save", True),      # Accept role
                    ("Don't Save", False), # Reject role
                    ("Cancel", False)      # Reject role
                ]
            )
        """
        import tvt_utils

        if buttons is None:
            buttons = [("Create", True), ("Cancel", False)]

        if icon in [
            QtWidgets.QMessageBox.Warning,
            QtWidgets.QMessageBox.Critical,
        ]:
            QtWidgets.QApplication.beep()

        dialog = QtWidgets.QDialog(parent)
        dialog.setWindowTitle(title)

        font = self.menu_font(font_size)
        font_amp = self.menu_font(font_size + 1)

        dialog.setStyleSheet(
            """
            QLineEdit[valid="false"], 
            QPlainTextEdit[valid="false"] {
                background-color: #111111; 
                border: 1px solid #d40000;
                border-radius: 2px;
            }
            QLineEdit[valid="true"], 
            QPlainTextEdit[valid="true"] {
                border: 1px solid #555555;
                border-radius: 2px;
            }
        """
        )

        layout = QtWidgets.QVBoxLayout()
        layout.setContentsMargins(15, 15, 15, 15)
        layout.setSpacing(15)

        if icon != QtWidgets.QMessageBox.NoIcon:
            icon_layout = QtWidgets.QHBoxLayout()
            icon_layout.setSpacing(20)

            icon_label = QtWidgets.QLabel()
            icon_label.setFont(font)
            icon_pixmap = None
            style = parent.style() if parent else QtWidgets.QApplication.style()

            if icon == QtWidgets.QMessageBox.Warning:
                icon_pixmap = style.standardIcon(
                    QtWidgets.QStyle.SP_MessageBoxWarning
                ).pixmap(48, 48)
            elif icon == QtWidgets.QMessageBox.Critical:
                icon_pixmap = style.standardIcon(
                    QtWidgets.QStyle.SP_MessageBoxCritical
                ).pixmap(48, 48)
            elif icon == QtWidgets.QMessageBox.Question:
                icon_pixmap = style.standardIcon(
                    QtWidgets.QStyle.SP_MessageBoxQuestion
                ).pixmap(48, 48)
            elif icon == QtWidgets.QMessageBox.Information:
                icon_pixmap = style.standardIcon(
                    QtWidgets.QStyle.SP_MessageBoxInformation
                ).pixmap(48, 48)

            if icon_pixmap:
                icon_label.setPixmap(icon_pixmap)
                icon_layout.addWidget(icon_label)

            input_layout = QtWidgets.QVBoxLayout()
            input_layout.setSpacing(5)

            label = QtWidgets.QLabel("Name:")
            label.setFont(font_amp)
            label.setAlignment(QtCore.Qt.AlignBottom)
            input_layout.addWidget(label)

            line_edit = QtWidgets.QLineEdit()
            line_edit.setFont(font_amp)
            line_edit.setMinimumHeight(30)
            line_edit.setText(initial_text)
            line_edit.setAlignment(QtCore.Qt.AlignCenter)
            line_edit.selectAll()
            input_layout.addWidget(line_edit)

            icon_layout.addLayout(input_layout, 1)
            layout.addLayout(icon_layout)

        else:
            label = QtWidgets.QLabel("Name:")
            label.setFont(font_amp)
            label.setAlignment(QtCore.Qt.AlignBottom)
            layout.addWidget(label)

            line_edit = QtWidgets.QLineEdit()
            line_edit.setFont(font_amp)
            line_edit.setMinimumHeight(30)
            line_edit.setText(initial_text)
            line_edit.setAlignment(QtCore.Qt.AlignCenter)
            line_edit.selectAll()
            layout.addWidget(line_edit)

        line = QtWidgets.QFrame()
        line.setFrameShape(QtWidgets.QFrame.HLine)
        line.setFrameShadow(QtWidgets.QFrame.Sunken)
        line.setLineWidth(1)
        line.setMidLineWidth(0)
        layout.addWidget(line)

        button_layout = QtWidgets.QHBoxLayout()
        button_layout.setSpacing(10)
        button_layout.addStretch()

        ok_btn = None
        for btn_text, is_accept in buttons:
            btn = QtWidgets.QPushButton(btn_text)
            btn.setFont(font)
            btn.setMinimumSize(70, 30)
            btn.setSizePolicy(QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Fixed)

            if is_accept:
                btn.clicked.connect(dialog.accept)
                btn.setDefault(True)
                ok_btn = btn
            else:
                btn.clicked.connect(dialog.reject)

            button_layout.addWidget(btn)

        layout.addLayout(button_layout)
        dialog.setLayout(layout)

        dialog.setMinimumWidth(350)
        dialog.adjustSize()
        dialog.setFixedSize(dialog.size())

        # Validation
        validate = tvt_utils.create_field_validator(
            line_edit,
            allowed_symbols="_-",
            allow_empty=False,
        )

        line_edit.textChanged.connect(lambda: ok_btn.setEnabled(validate()))

        line_edit.returnPressed.connect(
            lambda: dialog.accept() if ok_btn.isEnabled() else None
        )

        if dialog.exec() == QtWidgets.QDialog.Accepted:
            return line_edit.text().strip()
        return None

    # ------------------------------------------------------------------
    # Utility methods for windows
    # ------------------------------------------------------------------

    def close_all_child_windows(self, parent):
        """Close all child windows of a given parent."""
        parent_id = id(parent)
        if parent_id not in self._parent_child:
            return
        child_ids = self._parent_child[parent_id][:]
        for child_id in child_ids:
            window = self._get_window(child_id)
            if window:
                try:
                    window.close()
                except RuntimeError:
                    pass

    def window_exists(self, window_class, **kwargs) -> bool:
        """Check if a window instance already exists."""
        window_id = self._get_window_id(window_class, **kwargs)
        return self._get_window(window_id) is not None

    def get_window(self, window_class, **kwargs) -> Optional[QtWidgets.QWidget]:
        """Get existing window instance if it exists."""
        window_id = self._get_window_id(window_class, **kwargs)
        return self._get_window(window_id)

    def register_context_menu(self, widget: QtWidgets.QWidget, callback: Callable):
        """Register a context menu handler for a widget."""
        if widget is None:
            return
        widget_id = id(widget)
        widget_ref = weakref.ref(widget)
        widget.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)

        def handler(pos):
            w = widget_ref()
            if w is None:
                return
            try:
                w.isVisible()
            except RuntimeError:
                return
            try:
                callback(w, pos)
            except Exception as e:
                print(f"Context menu error: {e}")

        widget.customContextMenuRequested.connect(handler)
        self._context_menus[widget_id] = handler

    def menu_font(self, font_size=10):
        """Get standard menu font."""
        return QtGui.QFont("Unispace", font_size, QtGui.QFont.Bold)

    # ------------------------------------------------------------------
    # Signal safety helpers
    # ------------------------------------------------------------------

    def safe_connect(self, signal, slot, widget):
        """Safely connect a signal to a slot, handling widget deletion."""
        widget_ref = weakref.ref(widget)

        @wraps(slot)
        def safe_slot(*args, **kwargs):
            w = widget_ref()
            if w is None:
                return
            try:
                w.isVisible()
            except RuntimeError:
                return
            try:
                return slot(*args, **kwargs)
            except RuntimeError as e:
                if "already deleted" not in str(e):
                    raise

        return signal.connect(safe_slot, QtCore.Qt.UniqueConnection)

    def safe_connect_once(self, signal, callback, widget):
        """Connect to a signal once with widget lifecycle safety."""
        if signal is None:
            return None

        widget_ref = weakref.ref(widget)

        @wraps(callback)
        def wrapper(*args, **kwargs):
            w = widget_ref()
            if w is None:
                return
            try:
                w.isVisible()
            except RuntimeError:
                return
            try:
                signal.disconnect(wrapper)
            except:
                pass
            try:
                callback(*args, **kwargs)
            except RuntimeError as e:
                if "already deleted" not in str(e):
                    raise

        try:
            signal.connect(wrapper, QtCore.Qt.UniqueConnection)
            return wrapper
        except Exception as e:
            print(f"Error in safe_connect_once: {e}")
            return None

    def safe_timer(self, widget, callback, delay_ms: int) -> Optional[QtCore.QTimer]:
        """Create a timer that's automatically cancelled when widget closes."""
        try:
            widget.isVisible()
        except RuntimeError:
            return None

        widget_ref = weakref.ref(widget)
        timer = QtCore.QTimer()
        timer.setSingleShot(True)

        def safe_callback():
            w = widget_ref()
            if w is None:
                timer.deleteLater()
                return
            try:
                w.isVisible()
            except RuntimeError:
                timer.deleteLater()
                return
            try:
                callback()
            except Exception:
                pass
            timer.deleteLater()

        timer.timeout.connect(safe_callback, QtCore.Qt.QueuedConnection)
        timer.start(delay_ms)
        return timer

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def cleanup(self):
        """Clean up all resources."""
        for window_id in list(self._windows.keys()):
            window = self._get_window(window_id)
            if window:
                try:
                    window.close()
                except:
                    pass
        self._windows.clear()
        self._window_classes.clear()
        self._parent_child.clear()
        self._closing_windows.clear()
        self._log_widgets.clear()
        self._last_log.clear()


# Global instance
_window_manager = None


def get_window_manager() -> WindowManager:
    """Get the global window manager instance."""
    global _window_manager
    if _window_manager is None:
        _window_manager = WindowManager()
    return _window_manager
