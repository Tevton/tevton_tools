import hou
from qt_shim import QtCore, QtGui, QtWidgets, QtUiTools


class RenderNodesManager(QtWidgets.QMainWindow):

    VERSION = "v1.0.1"

    def __init__(self, parent=None):
        super().__init__(parent)
        self._load_ui()
        self._find_widgets()
        self._setup_ui()
        self._setup_prefix_suffix()
        self._connect_signals()

    def _load_ui(self):
        ui_path = hou.text.expandString("$TVT/ui/RenderNodesManager.ui")
        self.ui = QtUiTools.QUiLoader().load(ui_path, parentWidget=self)
        self.setCentralWidget(self.ui)
        self.setParent(hou.qt.mainWindow(), QtCore.Qt.Window)
        self.setAttribute(QtCore.Qt.WA_DeleteOnClose)
        self.setWindowTitle(f"RNM - {self.VERSION}")

    def _find_widgets(self):
        self.prefix_find = self.ui.findChild(QtWidgets.QLineEdit, "lineEdit")
        self.prefix_create = self.ui.findChild(QtWidgets.QLineEdit, "lineEdit_2")
        self.btn_create = self.ui.findChild(QtWidgets.QPushButton, "pushButton")
        self.chk_solaris = self.ui.findChild(QtWidgets.QCheckBox, "checkBox")

    def _setup_ui(self):
        self.setMaximumSize(350, 250)

    def _setup_prefix_suffix(self):
        for le in (self.prefix_find, self.prefix_create):
            placeholder = le.placeholderText() or ""
            le.setText(placeholder + "_")
            le.setPlaceholderText("")
            le.textChanged.connect(lambda _, widget=le: self._enforce_suffix(widget))

    def _enforce_suffix(self, widget):
        text = widget.text()
        clean = text.strip("_")
        corrected = clean + "_"
        if text != corrected:
            widget.blockSignals(True)
            widget.setText(corrected)
            widget.setCursorPosition(len(clean))
            widget.blockSignals(False)

    def _get_prefixes(self):
        find = self.prefix_find.text().upper()
        create = self.prefix_create.text().upper()
        return find, create

    def _connect_signals(self):
        self.btn_create.clicked.connect(self._on_create_clicked)

    def _msgbox(self, icon, title, text, buttons=QtWidgets.QMessageBox.Ok):
        font = QtGui.QFont("Unispace", 8, QtGui.QFont.Bold)
        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle(title)

        layout = QtWidgets.QVBoxLayout()
        layout.setContentsMargins(15, 15, 15, 15)
        layout.setSpacing(15)

        icon_map = {
            QtWidgets.QMessageBox.Warning: QtWidgets.QStyle.SP_MessageBoxWarning,
            QtWidgets.QMessageBox.Critical: QtWidgets.QStyle.SP_MessageBoxCritical,
            QtWidgets.QMessageBox.Question: QtWidgets.QStyle.SP_MessageBoxQuestion,
            QtWidgets.QMessageBox.Information: QtWidgets.QStyle.SP_MessageBoxInformation,
        }
        icon_layout = QtWidgets.QHBoxLayout()
        icon_layout.setSpacing(20)
        if icon in icon_map:
            icon_label = QtWidgets.QLabel()
            icon_label.setPixmap(self.style().standardIcon(icon_map[icon]).pixmap(48, 48))
            icon_layout.addWidget(icon_label)
        msg_label = QtWidgets.QLabel(text)
        msg_label.setFont(font)
        msg_label.setWordWrap(True)
        icon_layout.addWidget(msg_label, 1)
        layout.addLayout(icon_layout)

        line = QtWidgets.QFrame()
        line.setFrameShape(QtWidgets.QFrame.HLine)
        line.setFrameShadow(QtWidgets.QFrame.Sunken)
        layout.addWidget(line)

        btn_layout = QtWidgets.QHBoxLayout()
        btn_layout.setSpacing(10)
        btn_layout.addStretch()

        result = [QtWidgets.QMessageBox.Cancel]

        def _accept():
            result[0] = QtWidgets.QMessageBox.Ok
            dialog.accept()

        if buttons & QtWidgets.QMessageBox.Ok:
            ok_btn = QtWidgets.QPushButton("OK")
            ok_btn.setFont(font)
            ok_btn.setMinimumSize(70, 30)
            ok_btn.clicked.connect(_accept)
            btn_layout.addWidget(ok_btn)
        if buttons & QtWidgets.QMessageBox.Cancel:
            cancel_btn = QtWidgets.QPushButton("Cancel")
            cancel_btn.setFont(font)
            cancel_btn.setMinimumSize(70, 30)
            cancel_btn.clicked.connect(dialog.reject)
            btn_layout.addWidget(cancel_btn)

        layout.addLayout(btn_layout)
        dialog.setLayout(layout)
        dialog.adjustSize()
        sz = dialog.size()
        dialog.setFixedSize(sz.width() + 20, sz.height())

        dialog.exec()
        return result[0]

    def _on_create_clicked(self):
        find_prefix, create_prefix = self._get_prefixes()

        selected = hou.selectedNodes()
        if not selected:
            self._msgbox(
                QtWidgets.QMessageBox.Warning, "Warning", "Please select nodes first."
            )
            return

        for node in selected:
            if node.parent().name() != "obj":
                self._msgbox(
                    QtWidgets.QMessageBox.Warning,
                    "Warning",
                    "Please select nodes only in /obj context.",
                )
                return

        source_nodes = []
        render_nodes = []
        for n in selected:
            if n.type().name() != "geo":
                continue
            if n.name().upper().startswith(create_prefix):
                render_nodes.append(n)
            else:
                source_nodes.append(n)

        if source_nodes and render_nodes:
            self._msgbox(
                QtWidgets.QMessageBox.Warning,
                "Warning",
                "Please select either source or render nodes, not both.",
            )
            return

        if render_nodes:
            self._handle_render_nodes_selected(render_nodes, create_prefix)
        elif source_nodes:
            self._handle_source_nodes_selected(source_nodes, find_prefix, create_prefix)
        else:
            self._msgbox(
                QtWidgets.QMessageBox.Information,
                "Info",
                "No geo nodes found in selection.",
            )

    def _solaris_name_for(self, node_name, create_prefix):
        if node_name.upper().startswith(create_prefix):
            name = node_name[len(create_prefix) :]
        else:
            name = node_name
        name = name.strip("_")
        if not name or name[0].isdigit():
            name = node_name
        return name

    def _handle_source_nodes_selected(self, geo_nodes, find_prefix, create_prefix):
        matches = []
        for geo in geo_nodes:
            for child in geo.children():
                if child.type().name() == "null" and child.name().upper().startswith(
                    find_prefix
                ):
                    suffix = child.name()[len(find_prefix) :].lstrip("_")
                    render_name = create_prefix + suffix
                    if hou.node("/obj/" + render_name) is None:
                        matches.append((child, render_name))

        if not matches:
            self._msgbox(
                QtWidgets.QMessageBox.Information,
                "Info",
                "No new render nodes to create.\n"
                "All matching nodes already exist or no nulls with the specified prefix found.",
            )
            return

        new_solaris_count = 0
        if self.chk_solaris.isChecked():
            for _, render_name in matches:
                if (
                    hou.node(
                        "/stage/" + self._solaris_name_for(render_name, create_prefix)
                    )
                    is None
                ):
                    new_solaris_count += 1

        msg = f"Will create {len(matches)} render node(s) in /obj"
        if self.chk_solaris.isChecked() and new_solaris_count > 0:
            msg += f"\nand {new_solaris_count} SOP Import node(s) in /stage"

        result = self._msgbox(
            QtWidgets.QMessageBox.Question,
            "Confirm",
            msg,
            QtWidgets.QMessageBox.Ok | QtWidgets.QMessageBox.Cancel,
        )
        if result != QtWidgets.QMessageBox.Ok:
            return

        ref_pos = geo_nodes[0].position()
        created_nodes = []
        for i, (source_null, render_name) in enumerate(matches):
            node = self._create_render_node(source_null, render_name, ref_pos, -i)
            if node:
                created_nodes.append(node)

        if self.chk_solaris.isChecked() and created_nodes:
            self._create_solaris_imports(created_nodes, create_prefix)

        if created_nodes:
            self.close()

    def _handle_render_nodes_selected(self, render_nodes, create_prefix):
        new_imports = []
        for rn in render_nodes:
            if (
                hou.node("/stage/" + self._solaris_name_for(rn.name(), create_prefix))
                is None
            ):
                new_imports.append(rn)

        if not new_imports:
            self._msgbox(
                QtWidgets.QMessageBox.Information,
                "Info",
                "All SOP Import nodes already exist in /stage.",
            )
            return

        result = self._msgbox(
            QtWidgets.QMessageBox.Question,
            "Confirm",
            f"Will create {len(new_imports)} SOP Import node(s) in /stage",
            QtWidgets.QMessageBox.Ok | QtWidgets.QMessageBox.Cancel,
        )
        if result != QtWidgets.QMessageBox.Ok:
            return

        self._create_solaris_imports(new_imports, create_prefix)
        self.close()

    def _create_render_node(self, source_null, render_name, ref_pos, offset):
        purple = hou.Color(0.451, 0.369, 0.796)

        geo = hou.node("/obj").createNode("geo")
        geo.setColor(purple)
        geo.setName(render_name, unique_name=True)
        geo.setPosition(hou.Vector2(ref_pos[0] + 4, ref_pos[1] + offset))
        geo.setGenericFlag(hou.nodeFlag.Display, 0)

        objmerge = geo.createNode("object_merge")
        objmerge.setColor(hou.Color(1, 0, 0))
        objmerge.parm("objpath1").set(source_null.path())
        objmerge.parm("xformtype").set(1)

        null = geo.createNode("null")
        null.setColor(purple)
        null.setName(render_name)
        null.setGenericFlag(hou.nodeFlag.Render, 1)
        null.setInput(0, objmerge)

        null_pos = null.position()
        objmerge.setPosition(hou.Vector2(null_pos[0], null_pos[1] + 3))

        return geo

    def _create_solaris_imports(self, render_nodes, create_prefix):
        stage = hou.node("/stage")
        if not stage:
            return

        children = stage.children()
        if children:
            min_y = min(c.position()[1] for c in children)
            ref_pos = hou.Vector2(children[0].position()[0], min_y - 1.2)
        else:
            ref_pos = hou.Vector2(0, 0)

        prev_node = None
        for i, render_node in enumerate(render_nodes):
            name = self._solaris_name_for(render_node.name(), create_prefix)

            if hou.node("/stage/" + name) is not None:
                continue

            sop_import = stage.createNode("sopimport")
            sop_import.setName(name, unique_name=True)
            sop_import.setPosition(hou.Vector2(ref_pos[0], ref_pos[1] - (i * 1.2)))

            render_null = next(
                (c for c in render_node.children() if c.type().name() == "null"), None
            )
            sop_import.parm("soppath").set(
                render_null.path() if render_null else render_node.path()
            )
            sop_import.parm("asreference").set(True)
            sop_import.parm("enable_prefixabsolutepaths").set(True)
            sop_import.parm("prefixabsolutepaths").set(True)

            if prev_node is not None:
                sop_import.setInput(0, prev_node)

            prev_node = sop_import
