# CLAUDE.md — tevton_tools

Houdini pipeline tool (Python + PySide6). Installed as a Houdini package; `scripts/python/` is on `sys.path`, `scripts/456.py` auto-runs on startup.

**Testing**: Edit files → `tvt_utils.reload_packages()` in Houdini Python console (hot-reload, no restart needed).

---

## Directory Layout

```
scripts/456.py                  → startup: dirs, env vars, active project detection
scripts/python/
  config/config.py              → constants (PROJECTS_JSON_PATH, FTP paths, extensions)
  qt_shim.py                    → Qt compatibility shim: PySide6 (Houdini 21+) / PySide2 (Houdini 19.x)
  tvt_utils.py                  → shared utils + Qt message handler (suppress QFileSystemWatcher noise)
  ui_state.py                   → UIStateController: widget group enable/disable/block
  pipeline/
    projects_store.py           → SINGLE SOURCE OF TRUTH for projects JSON (read/write)
    project_manager.py          → main window: project/shot/file browser
    project_setup.py            → create/edit project form
    shot_setup.py               → create/edit shot form
    file_setup.py               → create/edit .hip file form
    window_manager.py           → singleton: dedup windows, safe signals/timers, centralized logging
    shot_ftp_manager/
      window.py                 → main FTP window: signal routing, drag-and-drop, keyboard shortcuts
      ftp_panel.py              → FTP tree: listing, navigation, sort, inline rename
      local_panel.py            → local tree (QFileSystemModel + proxy), navigation, move/delete
      transfer_panel.py         → upload/download/cancel/folder-ops/progress, button lifecycle
  ftp/
    manager.py                  → FTPManager QObject: credentials, worker lifecycle, signals
    ftp_utils.py                → get_ftp_settings(), format_size()
    workers/                    → QThread workers (connect, list, upload, download, delete, mkdir, rename)
ui/                             → Qt Designer .ui files (loaded at runtime)
toolbar/tevton_tools.shelf      → Houdini shelf
_dev/CLAUDE.md                  → this file
```

---

## Key Architecture Rules

**Data**: All JSON via `pipeline/projects_store.py`. Never `import json` + open the file directly.

**Logging**: ALL log calls go through `window_manager.log(window, msg, level)`. Never `print()` for user-visible messages in pipeline/ftp code. `window.py` exposes `self.log()` as a shortcut.

**Windows**: Always open via `WindowManager.show_window()` — handles dedup, parent blocking, weak refs.

**Imports**: Always `import pipeline.projects_store as projects_store` (bare imports fail — `pipeline/` is a package).

**Qt imports**: Always `from qt_shim import QtCore, QtWidgets, QtGui, QtUiTools` — never import directly from `PySide6`/`PySide2`. The shim handles version differences:
- Exposes `QT_VERSION` (2 or 6)
- PySide2: patches `QShortcut`/`QKeySequence` onto `QtGui`, and wraps `exec_()` → `exec()` for `QDialog`, `QMenu`, `QDrag`
- For `QtSvg` (not in shim): import locally with `try: from PySide6 import QtSvg` / `except: from PySide2 import QtSvg`

---

## UI Pattern

- Every window: `QMainWindow` subclass, loads `.ui` via `QUiLoader`
- Widget names in Python must match `objectName` in `.ui` XML exactly
- `UIStateController`: groups of widgets enabled/disabled together
  - `register_many(dict, groups=[...])` → `enable_group()` / `disable_group()`
  - `block(id, *names)` saves state + disables named widgets; `unblock(id)` restores
  - `block_group(id, group_name)` — convenience: blocks only widgets in a named group
- Buttons inside checkable `QGroupBox`: let Qt manage enable/disable automatically — never call `setEnabled(False)` on children while the GroupBox is unchecked (overwrites Qt's stored "was_enabled" flag permanently)

---

## FTP System

**FTPManager signals**: `connection_changed(bool)`, `busy_changed(bool)`, `progress(int)`, `status(str,str)`, `operation_finished(bool,str)`, `files_ready(list)`, `transfer_stats(float,float,float,float)`, `overwrite_needed(list)`

**Worker pattern**:
- Workers open their own FTP connections (thread-safe)
- Raw `threading.Thread` cannot emit Qt signals — use `_log_queue` + main-thread QTimer (200ms) → `emit_stats_if_due()`
- `stop()` force-closes sockets via `ftp.close()` (not `ftp.quit()`) to unblock mid-transfer
- `_safe_finish()` emits `finished` exactly once

**Upload queue**: `upload_files()` while uploading → adds to live queue (`_add_to_upload_queue`). `is_uploading()` checks this.

**`_on_operation_finished`** in `window.py` fires for EVERY FTP operation (list, upload, delete, rename). `restore_button()` short-circuits when `_cancel_btns` is empty.

---

## ShotFTPManager Panels

| Panel | Responsibility |
|-------|---------------|
| `window.py` | Signal routing, drag-and-drop (eventFilter), keyboard shortcuts (Del, F2), context menus |
| `ftp_panel.py` | FTP tree listing/navigation/sort/rename. Manual Python sort (no Qt `setSortingEnabled`). No ".." item — use Back button |
| `local_panel.py` | Local tree via `QFileSystemModel` + `_LocalSortProxy` (dirs-first). No ".." — `QDir.NoDotAndDotDot`. Supports move_files() for local drag-and-drop |
| `transfer_panel.py` | Upload/download start/cancel, folder/delete ops, progress stats, cancel-button lifecycle |

**Drag-and-drop** (eventFilter in `window.py`):
- FTP tree → local tree drop: FTP download
- Local file URL → FTP tree drop: upload
- Local file URL → local tree drop: `shutil.move` to target folder

---

## Thread Safety

1. Worker threads write only to thread-safe structures (`_transferred_bytes`, `_log_queue`, counters)
2. Main-thread QTimer (200ms) calls `worker.emit_stats_if_due()` → drains log queue, emits stats
3. All UI signal connections use `WindowManager.safe_connect()` (weak-ref guard) or `safe_connect_once()`
4. `safe_timer(widget, cb, ms)` — auto-cancels if widget is closed before firing
