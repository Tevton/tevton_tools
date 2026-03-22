# Tevton Tools

A Houdini pipeline package for project, shot, and .hip file management with integrated FTP transfer support.

---

## Requirements

- Houdini 19.5 or later
- Qt binding: **PySide2** (Houdini 19.x) or **PySide6** (Houdini 21+) — detected automatically at runtime

---

## Installation

1. **Clone or download** this repository.

2. **Copy the package descriptor** `tevton_tools.json` into your Houdini packages folder:
   ```
   Documents/houdiniXX.X/packages/
   ```

3. **Copy the tool folder** `tevton_tools/` into the same packages folder:
   ```
   Documents/houdiniXX.X/packages/tevton_tools/
   ```

   Your final layout should look like:
   ```
   Documents/houdiniXX.X/
   └── packages/
       ├── tevton_tools.json
       └── tevton_tools/
           ├── scripts/
           ├── toolbar/
           └── ui/
   ```

4. **Restart Houdini.** The **Tevton Tools** shelf will appear automatically.

---

## Tools

### Project Manager
A hierarchical browser for managing your pipeline data. Organizes everything into three levels — **Projects → Shots → Files** — letting you create, edit, and open .hip files directly from the UI. Also provides quick access to the Shot FTP Manager for each shot.

### Shot FTP Manager
A dual-panel file transfer window (FTP remote on the left, local filesystem on the right). Supports drag-and-drop uploads and downloads, inline rename (F2), delete (Del), folder creation, and real-time transfer progress. Opened per-shot from the Project Manager.

### Increment Save
Saves the current .hip file with an automatically incremented version number (e.g. `scene_v001.hip` → `scene_v002.hip`).

### Render Nodes Manager
Scans selected geo nodes for null nodes with a specified prefix and creates render geometry nodes in /obj. Optionally creates chained SOP Import nodes in /stage for Solaris workflows. Auto-detects mode based on selection — select source nodes to create render nodes, or select existing render nodes to create SOP imports only.