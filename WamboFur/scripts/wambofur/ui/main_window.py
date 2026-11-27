"""Wambo Fur main window (dockable in Maya)."""
import traceback

import maya.cmds as cmds
from maya.app.general.mayaMixin import MayaQWidgetDockableMixin

from .qt import QtCore, QtGui, QtWidgets, Qt, maya_main_window
from . import style
from .graph_view import GraphView
from .params import ParamPanel
from .. import __version__
from ..core.data import EvalError
from ..maya import scene, system as fsys, brushes, render

_WINDOW = None


def _hbox(*widgets, **kw):
    lay = QtWidgets.QHBoxLayout()
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(kw.get("spacing", 6))
    for w in widgets:
        if w is None:
            lay.addStretch(1)
        elif isinstance(w, QtWidgets.QLayout):
            lay.addLayout(w)
        else:
            lay.addWidget(w)
    return lay


def _section(text):
    l = QtWidgets.QLabel(text.upper())
    l.setObjectName("wfSection")
    return l


def _spin(lo, hi, val, dec=3, step=0.1):
    b = QtWidgets.QDoubleSpinBox()
    b.setRange(lo, hi)
    b.setDecimals(dec)
    b.setSingleStep(step)
    b.setValue(val)
    b.setKeyboardTracking(False)
    return b


def _ispin(lo, hi, val):
    b = QtWidgets.QSpinBox()
    b.setRange(lo, hi)
    b.setValue(val)
    b.setKeyboardTracking(False)
    return b


class ColorButton(QtWidgets.QPushButton):
    colorChanged = QtCore.Signal(tuple)

    def __init__(self, rgb=(1, 1, 1), parent=None):
        super(ColorButton, self).__init__(parent)
        self.setFixedWidth(60)
        self.set_rgb(rgb)
        self.clicked.connect(self._pick)

    def set_rgb(self, rgb):
        self.rgb = tuple(float(c) for c in rgb)
        c = QtGui.QColor.fromRgbF(*self.rgb)
        self.setStyleSheet("background:%s; border:1px solid #111;" % c.name())

    def _pick(self):
        c = QtWidgets.QColorDialog.getColor(QtGui.QColor.fromRgbF(*self.rgb), self)
        if c.isValid():
            self.set_rgb((c.redF(), c.greenF(), c.blueF()))
            self.colorChanged.emit(self.rgb)


class WamboFurWindow(MayaQWidgetDockableMixin, QtWidgets.QWidget):
    OBJECT_NAME = "wamboFurWindow"

    def __init__(self, parent=None):
        super(WamboFurWindow, self).__init__(parent=parent)
        self.setObjectName(self.OBJECT_NAME)
        self.setWindowTitle("Wambo Fur %s" % __version__)
        self.setStyleSheet(style.STYLESHEET)
        self.system = None
        self.graph = None
        self._loading = False
        self._time_job = None
        self._timer = QtCore.QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(250)
        self._timer.timeout.connect(self.update_preview)
        self._build()
        self.refresh_systems()
        self._time_job = cmds.scriptJob(event=["timeChanged", self._on_time], killWithScene=False)

    # =============================================================== layout
    def _build(self):
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(6)

        # ---- top bar
        bar = QtWidgets.QFrame()
        bar.setObjectName("wfBar")
        bl = QtWidgets.QHBoxLayout(bar)
        bl.setContentsMargins(8, 5, 8, 5)
        logo = QtWidgets.QLabel(u"<b style='color:%s;font-size:15px'>WAMBO</b>"
                                u"<span style='font-size:15px'>&nbsp;FUR</span>" % style.ACCENT)
        self.sys_combo = QtWidgets.QComboBox()
        self.sys_combo.setMinimumWidth(160)
        self.sys_combo.currentTextChanged.connect(self.load_system)
        new_btn = QtWidgets.QPushButton("+ New Fur")
        new_btn.setToolTip("Create a fur system on the selected mesh")
        new_btn.clicked.connect(self.new_system)
        del_btn = QtWidgets.QPushButton("Delete")
        del_btn.clicked.connect(self.delete_system)
        self.density = _spin(0.001, 100.0, 10.0, 1, 1.0)
        self.density.setSuffix(" %")
        self.density.setToolTip("Viewport preview density (percent of render count)")
        self.density.valueChanged.connect(self._density_changed)
        self.live = QtWidgets.QCheckBox("Live")
        self.live.setChecked(True)
        self.live.setToolTip("Re-evaluate the viewport preview whenever the graph changes")
        self.time_cb = QtWidgets.QCheckBox("Follow Time")
        self.time_cb.setToolTip("Re-evaluate on frame change (animated meshes)")
        self.time_cb.toggled.connect(self._render_settings)
        upd = QtWidgets.QPushButton(u"⟳  Update")
        upd.setObjectName("wfPrimary")
        upd.clicked.connect(self._full_update)
        bl.addLayout(_hbox(logo, QtWidgets.QLabel("  System"), self.sys_combo, new_btn, del_btn, None,
                           QtWidgets.QLabel("Preview"), self.density, self.live, self.time_cb, upd))
        root.addWidget(bar)

        # ---- centre: graph | tabs
        split = QtWidgets.QSplitter(Qt.Horizontal)
        self.view = GraphView()
        self.view.nodeSelected.connect(self._node_selected)
        self.view.graphChanged.connect(self._graph_changed)
        split.addWidget(self.view)
        self.tabs = QtWidgets.QTabWidget()
        self.params = ParamPanel(picker=scene.selected)
        self.params.paramChanged.connect(self._param_changed)
        self.params.renamed.connect(self._renamed)
        self.tabs.addTab(self.params, "Node")
        self.tabs.addTab(self._build_groom(), "Groom")
        self.tabs.addTab(self._build_render(), "Render")
        self.tabs.setMinimumWidth(330)
        split.addWidget(self.tabs)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 1)
        split.setSizes([800, 360])
        root.addWidget(split, 1)

        self.status = QtWidgets.QLabel("Ready")
        self.status.setObjectName("wfStatus")
        root.addWidget(self.status)

    def _build_groom(self):
        w = QtWidgets.QWidget()
        w.setObjectName("wfPanel")
        lay = QtWidgets.QVBoxLayout(w)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.addWidget(_section("Guides"))
        self.g_count = _ispin(1, 100000, 150)
        self.g_len = _spin(0.001, 1000, 1.0)
        self.g_seg = _ispin(3, 64, 6)
        f = QtWidgets.QFormLayout()
        f.addRow("Count", self.g_count)
        f.addRow("Length", self.g_len)
        f.addRow("Segments", self.g_seg)
        lay.addLayout(f)
        mk = QtWidgets.QPushButton("Create Guides on Mesh")
        mk.setObjectName("wfPrimary")
        mk.clicked.connect(self.create_guides)
        sel = QtWidgets.QPushButton("Select Guides")
        sel.clicked.connect(self.select_guides)
        lay.addLayout(_hbox(mk, sel))

        lay.addWidget(_section("Brushes"))
        grid = QtWidgets.QGridLayout()
        self.brush_group = QtWidgets.QButtonGroup(self)
        self.brush_group.setExclusive(True)
        labels = [("comb", "Comb"), ("length", "Length"), ("smooth", "Smooth"), ("puff", "Puff"),
                  ("straighten", "Straighten"), ("move", "Move"), ("cut", "Cut"), ("add", "Add"),
                  ("remove", "Remove"), ("off", "Off")]
        self.OFF_INDEX = len(labels) - 1
        for i, (mode, text) in enumerate(labels):
            b = QtWidgets.QToolButton()
            b.setText(text)
            b.setCheckable(True)
            b.setMinimumWidth(62)
            b.setProperty("mode", mode)
            self.brush_group.addButton(b, i)
            grid.addWidget(b, i // 5, i % 5)
            b.clicked.connect(lambda checked=False, m=mode: self.set_brush(m))
        lay.addLayout(grid)
        self.b_radius = _ispin(4, 600, int(brushes.STATE.radius))
        self.b_radius.setSuffix(" px")
        self.b_strength = _spin(0.0, 1.0, brushes.STATE.strength, 2, 0.05)
        self.b_keep = QtWidgets.QCheckBox("Preserve length")
        self.b_keep.setChecked(True)
        self.b_upd = QtWidgets.QCheckBox("Update fur after each stroke")
        self.b_upd.setChecked(True)
        self.b_live = QtWidgets.QCheckBox("Live fur while brushing")
        self.b_live.setChecked(True)
        self.b_live.setToolTip("Re-evaluates the preview during the stroke when it is fast enough "
                               "(lower the Preview % for heavy grooms)")
        for wdg in (self.b_radius, self.b_strength):
            wdg.valueChanged.connect(self._brush_settings)
        self.b_keep.toggled.connect(self._brush_settings)
        f2 = QtWidgets.QFormLayout()
        f2.addRow("Radius", self.b_radius)
        f2.addRow("Strength", self.b_strength)
        f2.addRow("", self.b_keep)
        f2.addRow("", self.b_upd)
        f2.addRow("", self.b_live)
        lay.addLayout(f2)
        hint = QtWidgets.QLabel("Drag in the viewport over the guide curves - the circle shows the brush.\n"
                                "B + left-drag: radius   \u00b7   B + middle-drag: strength\n"
                                "Shift: smooth   \u00b7   Ctrl: invert (Length shortens, Puff flattens)\n"
                                "Each stroke is one undo step (Ctrl+Z). Add/Remove: click on the mesh.")
        hint.setWordWrap(True)
        hint.setObjectName("wfDim")
        lay.addWidget(hint)
        lay.addStretch(1)
        return w

    def _build_render(self):
        w = QtWidgets.QWidget()
        w.setObjectName("wfPanel")
        lay = QtWidgets.QVBoxLayout(w)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.addWidget(_section("Renderer"))
        self.r_engine = QtWidgets.QComboBox()
        self.r_engine.addItems(["Arnold", "V-Ray"])
        self.r_density = _spin(0.001, 10.0, 1.0, 3, 0.1)
        self.r_width = _spin(0.0, 100.0, 1.0, 3, 0.1)
        self.r_subdiv = _ispin(0, 8, 1)
        f = QtWidgets.QFormLayout()
        f.addRow("Engine", self.r_engine)
        f.addRow("Render Density", self.r_density)
        f.addRow("Width Scale", self.r_width)
        f.addRow("Smooth Subdiv", self.r_subdiv)
        lay.addLayout(f)
        lay.addWidget(_section("Hair Shader"))
        self.s_color = ColorButton()
        self.s_mel = _spin(0, 1, 0.45, 2, 0.05)
        self.s_red = _spin(0, 1, 0.5, 2, 0.05)
        self.s_rand = _spin(0, 1, 0.1, 2, 0.05)
        self.s_rough = _spin(0, 1, 0.25, 2, 0.05)
        f2 = QtWidgets.QFormLayout()
        f2.addRow("Base Color", self.s_color)
        f2.addRow("Melanin", self.s_mel)
        f2.addRow("Melanin Redness", self.s_red)
        f2.addRow("Melanin Random", self.s_rand)
        f2.addRow("Roughness", self.s_rough)
        lay.addLayout(f2)
        note = QtWidgets.QLabel("Arnold: shader is embedded in the .ass (standard_hair).\n"
                                "V-Ray: a VRayHairNextMtl is created and assigned to the proxy.")
        note.setWordWrap(True)
        note.setObjectName("wfDim")
        lay.addWidget(note)
        lay.addWidget(_section("Output"))
        self.r_dir = QtWidgets.QLineEdit()
        self.r_dir.setPlaceholderText("<project>/cache/wambofur")
        browse = QtWidgets.QToolButton()
        browse.setText("...")
        browse.clicked.connect(self._browse)
        lay.addLayout(_hbox(self.r_dir, browse))
        one = QtWidgets.QPushButton("Export Current Frame")
        one.setObjectName("wfPrimary")
        one.clicked.connect(lambda: self.export(False))
        self.r_start = _spin(-1e6, 1e6, cmds.playbackOptions(q=True, minTime=True), 1, 1)
        self.r_end = _spin(-1e6, 1e6, cmds.playbackOptions(q=True, maxTime=True), 1, 1)
        seq = QtWidgets.QPushButton("Export Sequence")
        seq.clicked.connect(lambda: self.export(True))
        self.r_auto = QtWidgets.QCheckBox("Export automatically when rendering")
        self.r_auto.setToolTip("Adds a Pre Render Frame hook: every frame rendered (Render View, batch, farm)\n"
                               "exports the fur first - just hit Render, no manual export needed.")
        self.r_auto.setChecked(render.render_hook_enabled())
        self.r_auto.toggled.connect(render.set_render_hook)
        lay.addWidget(self.r_auto)
        lay.addWidget(one)
        lay.addLayout(_hbox(QtWidgets.QLabel("Frames"), self.r_start, self.r_end, seq))
        lay.addStretch(1)
        for wdg in (self.r_density, self.r_width, self.s_mel, self.s_red, self.s_rand, self.s_rough):
            wdg.valueChanged.connect(self._render_settings)
        self.r_subdiv.valueChanged.connect(self._render_settings)
        self.r_engine.currentIndexChanged.connect(self._render_settings)
        self.r_dir.editingFinished.connect(self._render_settings)
        self.s_color.colorChanged.connect(self._render_settings)
        return w

    # ============================================================== systems
    def refresh_systems(self, select=None):
        cur = select or self.sys_combo.currentText()
        self.sys_combo.blockSignals(True)
        self.sys_combo.clear()
        names = fsys.list_systems()
        self.sys_combo.addItems(names)
        self.sys_combo.blockSignals(False)
        if names:
            self.sys_combo.setCurrentText(cur if cur in names else names[0])
            self.load_system(self.sys_combo.currentText())
        else:
            self.load_system("")

    def load_system(self, name):
        if not name or not cmds.objExists(name):
            self.system, self.graph = None, None
            self.view.set_graph(None)
            self.params.show_node(None)
            self._status("No fur system - select a mesh and click '+ New Fur'.")
            return
        self.system = fsys.FurSystem(name)
        self.graph = self.system.load_graph()
        self.view.set_graph(self.graph)
        QtCore.QTimer.singleShot(0, self.view.frame_all)
        self.params.show_node(None)
        self._load_settings()
        self._status("Loaded %s" % name)

    def _load_settings(self):
        st = self.system.settings()
        self._loading = True
        try:
            self.density.setValue(st["preview_density"] * 100.0)
            self.time_cb.setChecked(bool(st.get("live_time")))
            self.r_engine.setCurrentIndex(1 if st["renderer"] == "vray" else 0)
            self.r_density.setValue(st["render_density"])
            self.r_width.setValue(st["width_scale"])
            self.r_subdiv.setValue(int(st["render_subdiv"]))
            sh = st["shader"]
            self.s_color.set_rgb(sh["base_color"])
            self.s_mel.setValue(sh["melanin"])
            self.s_red.setValue(sh["melanin_redness"])
            self.s_rand.setValue(sh["melanin_randomize"])
            self.s_rough.setValue(sh["roughness"])
            self.r_dir.setText(st.get("output_dir", ""))
        finally:
            self._loading = False

    def new_system(self):
        mesh = scene.selected("mesh")
        if not mesh:
            QtWidgets.QMessageBox.warning(self, "Wambo Fur", "Select a polygon mesh first.")
            return
        sysm = fsys.FurSystem.create(mesh, name=mesh.split("|")[-1] + "_fur1")
        self.refresh_systems(select=sysm.name)
        self.update_preview()

    def delete_system(self):
        if not self.system:
            return
        if QtWidgets.QMessageBox.question(self, "Wambo Fur", "Delete %s and its guides?" % self.system.name) \
                != QtWidgets.QMessageBox.Yes:
            return
        self.system.delete()
        self.refresh_systems()

    # ================================================================ graph
    def _node_selected(self, nid):
        self.params.show_node(self.graph.nodes.get(nid) if (self.graph and nid) else None)
        if nid:
            self.tabs.setCurrentIndex(0)

    def _graph_changed(self, needs_eval):
        if not self.system:
            return
        self.system.save_graph(self.graph)
        if needs_eval:
            self._schedule()

    def _param_changed(self, nid, name, value):
        node = self.graph.nodes.get(nid)
        if node is None:
            return
        node.params[name] = value
        self.system.save_graph(self.graph)
        self._schedule()

    def _renamed(self, nid, name):
        if name:
            self.graph.rename(nid, name)
            self.system.save_graph(self.graph)
            self.view.refresh_flags()

    def _schedule(self):
        if self.live.isChecked():
            self._timer.start()

    def update_preview(self):
        if not self.system:
            return
        QtWidgets.QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            data = self.system.update_preview(self.graph)
            self.view.refresh_flags(self.system.last_stats, {})
            self._status(u"{:,} strands in viewport  \u00b7  {:,} points  \u00b7  eval {:.2f}s  "
                         u"\u00b7  draw {:.2f}s".format(len(data.strands), data.point_count(),
                                                    self.system.last_time, self.system.last_draw_time))
        except EvalError as e:
            self.view.refresh_flags(self.system.last_stats, self.system.last_errors)
            self._status(str(e), error=True)
        except Exception as e:
            traceback.print_exc()
            self._status("Error: %s (see Script Editor)" % e, error=True)
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()

    def _full_update(self):
        scene.clear_cache()      # re-read mesh topology (after modelling changes)
        self.update_preview()

    def _density_changed(self, v):
        if self.system and not self._loading:
            self.system.set_setting("preview_density", v / 100.0)
            self._schedule()

    def _on_time(self):
        if self.system and self.time_cb.isChecked():
            self.update_preview()

    # ================================================================ groom
    def create_guides(self):
        if not self.system:
            return
        try:
            grp = self.system.create_guides(self.g_count.value(), self.g_len.value(), self.g_seg.value())
        except EvalError as e:
            self._status(str(e), error=True)
            return
        self.graph = self.system.load_graph()
        self.view.set_graph(self.graph)
        self.view.frame_all()
        self.update_preview()
        self._status("Created guides in %s - pick a brush to groom them." % grp)

    def select_guides(self):
        g = self.system.guides_group() if self.system else None
        if g:
            cmds.select(g)

    def set_brush(self, mode):
        if mode == "off":
            brushes.deactivate()
            return
        g = self.system.guides_group() if self.system else None
        if not g:
            self._status("Create guides first (Groom tab).", error=True)
            self.brush_group.button(self.OFF_INDEX).setChecked(True)
            return
        self._brush_settings()
        brushes.activate(mode, g, on_stroke=self._after_stroke, on_settings=self._brush_from_viewport)

    def _brush_settings(self, *a):
        brushes.STATE.radius = float(self.b_radius.value())
        brushes.STATE.strength = float(self.b_strength.value())
        brushes.STATE.preserve_length = self.b_keep.isChecked()
        brushes.sync_settings()

    def _brush_from_viewport(self):
        """B + drag in the viewport changed radius/strength: mirror it in the UI."""
        for wdg, val in ((self.b_radius, int(brushes.STATE.radius)), (self.b_strength, brushes.STATE.strength)):
            wdg.blockSignals(True)
            wdg.setValue(val)
            wdg.blockSignals(False)

    def _after_stroke(self, group, final=True):
        if final:
            if self.b_upd.isChecked():
                self._quiet_update()
        elif self.b_live.isChecked() and getattr(self.system, "last_time", 1.0) < 0.25:
            self._quiet_update()

    def _quiet_update(self):
        """Preview update without the wait cursor (used while brushing)."""
        if not self.system:
            return
        try:
            data = self.system.update_preview(self.graph)
            self._status(u"{:,} strands  \u00b7  eval {:.2f}s  \u00b7  draw {:.2f}s".format(
                len(data.strands), self.system.last_time, self.system.last_draw_time))
        except EvalError as e:
            self._status(str(e), error=True)

    # =============================================================== render
    def _render_settings(self, *a):
        if not self.system or self._loading:
            return
        st = self.system.settings()
        st["renderer"] = "vray" if self.r_engine.currentIndex() == 1 else "arnold"
        st["render_density"] = self.r_density.value()
        st["width_scale"] = self.r_width.value()
        st["render_subdiv"] = self.r_subdiv.value()
        st["output_dir"] = self.r_dir.text().strip()
        st["live_time"] = self.time_cb.isChecked()
        st["shader"] = {"base_color": self.s_color.rgb, "melanin": self.s_mel.value(),
                        "melanin_redness": self.s_red.value(), "melanin_randomize": self.s_rand.value(),
                        "roughness": self.s_rough.value(), "ior": 1.55}
        self.system.save_settings(st)

    def _browse(self):
        d = QtWidgets.QFileDialog.getExistingDirectory(self, "Output folder", self.r_dir.text())
        if d:
            self.r_dir.setText(d)
            self._render_settings()

    def export(self, sequence):
        if not self.system:
            return
        self._render_settings()
        QtWidgets.QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            if sequence:
                paths, n = render.render(self.system, self.r_start.value(), self.r_end.value())
            else:
                paths, n = render.render(self.system)
            self._status(u"Exported {:,} curves → {} ({} file{})".format(
                n, paths[0], len(paths), "s" if len(paths) > 1 else ""))
        except Exception as e:
            traceback.print_exc()
            self._status("Export failed: %s" % e, error=True)
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()

    # ================================================================= misc
    def _status(self, text, error=False):
        self.status.setText(text)
        self.status.setStyleSheet("color:%s" % (style.ERROR if error else style.TEXT_DIM))

    def closeEvent(self, e):
        self._cleanup()
        super(WamboFurWindow, self).closeEvent(e)

    def dockCloseEventTriggered(self):
        self._cleanup()

    def _cleanup(self):
        if self._time_job and cmds.scriptJob(exists=self._time_job):
            cmds.scriptJob(kill=self._time_job, force=True)
        self._time_job = None


def show():
    global _WINDOW
    ctrl = WamboFurWindow.OBJECT_NAME + "WorkspaceControl"
    if _WINDOW is not None:
        try:
            _WINDOW._cleanup()
            _WINDOW.close()
            _WINDOW.deleteLater()
        except Exception:
            pass
    if cmds.workspaceControl(ctrl, exists=True):
        cmds.deleteUI(ctrl)
    _WINDOW = WamboFurWindow(parent=maya_main_window())
    _WINDOW.show(dockable=True, floating=True, width=1250, height=720)
    return _WINDOW
