from qt_shim import QtWidgets, QtCore


class UIStateController:
    """
    Universal UI state manager.

    Focused solely on UI state management:
    - Widget registration with optional groups
    - Manual enable/disable by name or group
    - Safe block/unblock with state restore
    - Automatic block/unblock bound to busy signals

    Window lifecycle management is handled by WindowManager.
    """

    def __init__(self):
        self._widgets: dict[str, QtWidgets.QWidget] = {}
        self._groups: dict[str, list[str]] = {}
        self._saved_states: dict[str, dict[str, bool]] = {}
        self._active_blocks: set[str] = set()
        self._timers: list[QtCore.QTimer] = []

    # ---------------------------------------------------------
    # Registration
    # ---------------------------------------------------------

    def register(self, name: str, widget: QtWidgets.QWidget, groups=None):
        """Register a widget with optional group membership."""
        if not widget:
            return

        self._widgets[name] = widget

        if groups:
            for group in groups:
                self._groups.setdefault(group, [])
                if name not in self._groups[group]:
                    self._groups[group].append(name)

    def register_many(self, widgets: dict, groups=None):
        """Register multiple widgets at once."""
        for name, widget in widgets.items():
            self.register(name, widget, groups)

    # ---------------------------------------------------------
    # Basic enable / disable
    # ---------------------------------------------------------

    def set_enabled(self, enabled: bool, *names):
        """Enable or disable specific widgets by name."""
        for name in names:
            if name in self._widgets:
                try:
                    self._widgets[name].setEnabled(enabled)
                except RuntimeError:
                    # Widget was deleted, remove from registry
                    del self._widgets[name]

    def enable_group(self, group: str):
        """Enable all widgets in a group."""
        for name in self._groups.get(group, []):
            self.set_enabled(True, name)

    def disable_group(self, group: str):
        """Disable all widgets in a group."""
        for name in self._groups.get(group, []):
            self.set_enabled(False, name)

    # ---------------------------------------------------------
    # Manual block / unblock
    # ---------------------------------------------------------

    def block(self, block_id: str, *names):
        """
        Block widgets, saving their current state.
        If no names provided, blocks all registered widgets.
        """
        if block_id in self._active_blocks:
            return

        widgets = names if names else list(self._widgets.keys())

        # Save current states
        self._saved_states[block_id] = {}
        for name in widgets:
            if name in self._widgets:
                try:
                    self._saved_states[block_id][name] = self._widgets[name].isEnabled()
                except RuntimeError:
                    # Widget was deleted
                    continue

        # Disable all
        for name in widgets:
            self.set_enabled(False, name)

        self._active_blocks.add(block_id)

    def block_group(self, block_id: str, group_name: str):
        """Block only the widgets belonging to a specific group, saving their state."""
        names = tuple(self._groups.get(group_name, []))
        self.block(block_id, *names)

    def unblock(self, block_id: str):
        """Unblock widgets, restoring their previous states."""
        if block_id not in self._active_blocks:
            return

        # Restore saved states
        saved = self._saved_states.get(block_id, {})
        for name, state in saved.items():
            self.set_enabled(state, name)

        # Clean up
        self._saved_states.pop(block_id, None)
        self._active_blocks.remove(block_id)

    def is_blocked(self, block_id=None) -> bool:
        """Check if specific block or any block is active."""
        if block_id:
            return block_id in self._active_blocks
        return bool(self._active_blocks)

    # ---------------------------------------------------------
    # Automatic bind to busy signals
    # ---------------------------------------------------------

    def bind(self, busy_signal, *names, cooldown_ms: int = 0, block_id: str = "busy"):
        """
        Automatically block/unblock widgets in response to a busy_changed signal.

        Args:
            busy_signal: Signal emitting bool (True = busy, False = idle)
            *names: Widget names to block (empty = all registered)
            cooldown_ms: Delay before unblocking after busy ends
            block_id: Identifier for this block
        """
        busy_signal.connect(
            lambda busy: self._on_busy(busy, names, cooldown_ms, block_id)
        )

    def _on_busy(self, busy: bool, names: tuple, cooldown_ms: int, block_id: str):
        """Handle busy state changes."""
        if busy:
            self.block(block_id, *names)
        elif cooldown_ms > 0:
            timer = QtCore.QTimer()
            timer.setSingleShot(True)

            def on_timeout():
                self.unblock(block_id)
                if timer in self._timers:
                    self._timers.remove(timer)

            timer.timeout.connect(on_timeout)
            self._timers.append(timer)
            timer.start(cooldown_ms)
        else:
            self.unblock(block_id)

    # ---------------------------------------------------------
    # Cleanup
    # ---------------------------------------------------------

    def clear(self):
        """Clear all resources."""
        # Stop all timers
        for timer in self._timers:
            timer.stop()
        self._timers.clear()

        # Clear all data
        self._widgets.clear()
        self._groups.clear()
        self._saved_states.clear()
        self._active_blocks.clear()
