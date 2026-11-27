"""'Tools' tab: groom toolbox (create / delete / comb / cut / ...) and guide
operations, working on the native groom node."""
import maya.cmds as cmds

from .qt import QtCore, QtWidgets, Qt
from .icons import make_icon
from ..core.data import EvalError
from ..maya import brushes, groom, scene

TOOL_GROUPS = [
    ("Create", [("add", "Add"), ("paint", "Paint")]),
    ("Delete", [("remove", "Delete")]),
    ("Shape", [("comb", "Comb"), ("move", "Move"), ("smooth", "Smooth"),
               ("puff", "Puff"), ("straighten", "Straighten"), ("clump", "Clump")]),
    ("Length", [("length", "Scale"), ("cut", "Cut")]),
]

TOOL_TIPS = {
    "add": "Click on the mesh to add ONE guide (inherits the groom of its neighbours)",
    "paint": "Drag on the mesh to spray guides. Strength = amount, Spacing = minimum distance",
    "remove": "Delete the guides whose root is inside the brush",
    "comb": "Comb guides along the stroke (tips move most, length preserved)",
    "move": "Move the guide points inside the brush freely",
    "smooth": "Relax kinks (Shift with any tool)",
    "puff": "Lift guides toward the surface normal (Ctrl: flatten)",
    "straighten": "Straighten guides along their root direction",
    "clump": "Pull guide tips together (Ctrl: spread apart)",
    "length": "Drag right to lengthen, left to shorten (Ctrl: shorten)",
    "cut": "Cut guides where the brush passes",
}


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


class ToolsTab(QtWidgets.QScrollArea):
    def __init__(self, win, parent=None):
        super(ToolsTab, self).__init__(parent)
        self.win = win
        self.setWidgetResizable(True)
        w = QtWidgets.QWidget()
        w.setObjectName("wfPanel")
        lay = QtWidgets.QVBoxLayout(w)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(6)

        # ------------------------------------------------------- toolbox
        lay.addWidget(_section("Groom Tools"))
        self.group = QtWidgets.QButtonGroup(self)
        self.group.setExclusive(True)
        self.buttons = {}
        for title, tools in TOOL_GROUPS:
            lbl = QtWidgets.QLabel(title)
            lbl.setObjectName("wfDim")
            lay.addWidget(lbl)
            row = QtWidgets.QGridLayout()
            row.setSpacing(4)
            for i, (mode, text) in enumerate(tools):
                b = QtWidgets.QToolButton()
                b.setText(text)
                b.setIcon(make_icon(mode))
                b.setIconSize(QtCore.QSize(30, 30))
                b.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
                b.setCheckable(True)
                b.setFixedSize(74, 58)
                b.setToolTip(TOOL_TIPS.get(mode, ""))
                self.group.addButton(b)
                self.buttons[mode] = b
                b.clicked.connect(lambda checked=False, m=mode: self.set_tool(m))
                row.addWidget(b, i // 4, i % 4)
            row.setColumnStretch(4, 1)
            lay.addLayout(row)
        sel = QtWidgets.QToolButton()
        sel.setText("Select tool (exit groom)")
        sel.setIcon(make_icon("select"))
        sel.setIconSize(QtCore.QSize(18, 18))
        sel.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        sel.setCheckable(True)
        self.group.addButton(sel)
        self.buttons["off"] = sel
        sel.clicked.connect(lambda: self.set_tool("off"))
        lay.addWidget(sel)

        # --------------------------------------------------- brush options
        lay.addWidget(_section("Brush"))
        self.radius = _ispin(4, 1000, int(brushes.STATE.radius))
        self.radius.setSuffix(" px")
        self.strength = _spin(0.0, 1.0, brushes.STATE.strength, 2, 0.05)
        self.keep = QtWidgets.QCheckBox("Preserve length")
        self.keep.setChecked(True)
        self.front = QtWidgets.QCheckBox("Front guides only")
        self.front.setChecked(True)
        self.front.setToolTip("Ignore guides on the back side of the mesh")
        f = QtWidgets.QFormLayout()
        f.addRow("Radius", self.radius)
        f.addRow("Strength", self.strength)
        f.addRow("", self.keep)
        f.addRow("", self.front)
        lay.addLayout(f)

        lay.addWidget(_section("New Guides"))
        self.n_count = _ispin(1, 100000, 150)
        self.n_len = _spin(0.001, 1000, 1.0)
        self.n_seg = _ispin(2, 64, 6)
        self.n_spacing = _spin(0.0, 1000, 0.0, 3, 0.05)
        self.n_spacing.setSpecialValueText("auto")
        self.n_inherit = QtWidgets.QCheckBox("Inherit groom from neighbours")
        self.n_inherit.setChecked(True)
        f2 = QtWidgets.QFormLayout()
        f2.addRow("Length", self.n_len)
        f2.addRow("Points", self.n_seg)
        f2.addRow("Paint Spacing", self.n_spacing)
        f2.addRow("", self.n_inherit)
        f2.addRow("Fill Count", self.n_count)
        lay.addLayout(f2)
        fill = QtWidgets.QPushButton("Fill Mesh with Guides")
        fill.setObjectName("wfPrimary")
        fill.setToolTip("Scatter Fill Count straight guides over the whole mesh (replaces the groom)")
        fill.clicked.connect(self.fill)
        add_more = QtWidgets.QPushButton("Add to Groom")
        add_more.setToolTip("Scatter Fill Count more guides, keeping the existing ones")
        add_more.clicked.connect(lambda: self.fill(replace=False))
        lay.addLayout(self._row(fill, add_more))

        for wdg in (self.radius, self.strength, self.n_len, self.n_seg, self.n_spacing):
            wdg.valueChanged.connect(self.push_settings)
        for wdg in (self.keep, self.front, self.n_inherit):
            wdg.toggled.connect(self.push_settings)

        # ------------------------------------------------------ operations
        lay.addWidget(_section("Guide Operations"))
        ops = [("Smooth All", lambda: self._op(brushes.smooth_all)),
               ("Straighten All", lambda: self._op(brushes.reset_straight)),
               ("Clear All", self.clear),
               ("Select Groom", self.select_groom)]
        grid = QtWidgets.QGridLayout()
        for i, (text, fn) in enumerate(ops):
            b = QtWidgets.QPushButton(text)
            b.clicked.connect(fn)
            grid.addWidget(b, i // 2, i % 2)
        lay.addLayout(grid)
        self.op_scale = _spin(0.01, 100.0, 1.2, 2, 0.05)
        sc = QtWidgets.QPushButton("Scale All")
        sc.clicked.connect(lambda: self._op(lambda: brushes.scale_all(self.op_scale.value())))
        self.op_points = _ispin(2, 64, 8)
        rs = QtWidgets.QPushButton("Resample")
        rs.clicked.connect(lambda: self._op(lambda: brushes.resample_all(self.op_points.value())))
        lay.addLayout(self._row(QtWidgets.QLabel("x"), self.op_scale, sc))
        lay.addLayout(self._row(QtWidgets.QLabel("points"), self.op_points, rs))
        conv = QtWidgets.QPushButton("Curves → Groom")
        conv.setToolTip("Convert selected Maya curves (or the old curve guides) into native guides")
        conv.clicked.connect(self.convert_curves)
        exp = QtWidgets.QPushButton("Groom → Curves")
        exp.setToolTip("Bake the guides to Maya curves (e.g. for simulation)")
        exp.clicked.connect(self.export_curves)
        lay.addLayout(self._row(conv, exp))

        lay.addWidget(_section("Viewport"))
        self.upd = QtWidgets.QCheckBox("Update fur after each stroke")
        self.upd.setChecked(True)
        self.live = QtWidgets.QCheckBox("Live fur while brushing")
        self.live.setChecked(True)
        self.roots = QtWidgets.QCheckBox("Show guide roots")
        self.roots.setChecked(True)
        self.roots.toggled.connect(self._show_roots)
        for wdg in (self.upd, self.live, self.roots):
            lay.addWidget(wdg)
        hint = QtWidgets.QLabel(
            "B + left-drag: radius  ·  B + middle-drag: strength\n"
            "Shift: smooth  ·  Ctrl: invert (shorten / flatten / spread)\n"
            "Every stroke is one undo step (Ctrl+Z).")
        hint.setObjectName("wfDim")
        hint.setWordWrap(True)
        lay.addWidget(hint)
        lay.addStretch(1)
        self.setWidget(w)
        self.buttons["off"].setChecked(True)
        self.push_settings()

    @staticmethod
    def _row(*widgets):
        l = QtWidgets.QHBoxLayout()
        l.setSpacing(4)
        for w in widgets:
            l.addWidget(w)
        return l

    # ---------------------------------------------------------------- tools
    def _groom(self, create=False):
        sysm = self.win.system
        if not sysm:
            self.win._status("Create a fur system first (+ New Fur).", error=True)
            return None
        try:
            return sysm.groom(create=create)
        except EvalError as e:
            self.win._status(str(e), error=True)
            return None

    def set_tool(self, mode):
        if mode == "off":
            brushes.deactivate()
            return
        shape = self._groom(create=True)
        if not shape:
            self.buttons["off"].setChecked(True)
            return
        self.win.reload_graph()
        self.push_settings()
        brushes.activate(mode, shape, on_stroke=self.win._after_stroke,
                         on_settings=self._from_viewport)
        self.win._status("%s: %s" % (brushes.LABELS[mode], TOOL_TIPS.get(mode, "")))

    def push_settings(self, *a):
        st = brushes.STATE
        st.radius = float(self.radius.value())
        st.strength = float(self.strength.value())
        st.preserve_length = self.keep.isChecked()
        st.front_only = self.front.isChecked()
        st.new_length = float(self.n_len.value())
        st.new_segments = int(self.n_seg.value()) - 1
        st.spacing = float(self.n_spacing.value())
        st.inherit = self.n_inherit.isChecked()
        brushes.sync_settings()

    def _from_viewport(self):
        for wdg, val in ((self.radius, int(brushes.STATE.radius)), (self.strength, brushes.STATE.strength)):
            wdg.blockSignals(True)
            wdg.setValue(val)
            wdg.blockSignals(False)

    # ------------------------------------------------------------ actions
    def fill(self, replace=True):
        sysm = self.win.system
        if not sysm:
            return
        try:
            sysm.create_guides(self.n_count.value(), self.n_len.value(), max(2, self.n_seg.value() - 1),
                               replace=replace)
        except EvalError as e:
            self.win._status(str(e), error=True)
            return
        self.win.reload_graph()
        self.win.update_preview()

    def _op(self, fn):
        shape = self._groom()
        if not shape:
            return
        brushes.STATE.group = shape
        n = fn()
        self.win.update_preview()
        self.win._status("%d guides updated" % (n or 0))

    def clear(self):
        if QtWidgets.QMessageBox.question(self, "Wambo Fur", "Delete all guides? (undoable)") \
                == QtWidgets.QMessageBox.Yes:
            self._op(brushes.clear_all)

    def select_groom(self):
        shape = self._groom()
        if shape:
            cmds.select(cmds.listRelatives(shape, parent=True, fullPath=True)[0])

    def convert_curves(self):
        sysm = self.win.system
        shape = self._groom(create=True)
        if not shape:
            return
        sel = [s for s in (cmds.ls(selection=True, long=True) or []) if scene.curve_shapes(s)]
        if not sel and sysm.legacy_curve_group():
            sel = [sysm.legacy_curve_group()]
        if not sel:
            self.win._status("Select Maya curves to convert.", error=True)
            return
        n = sum(groom.convert_curves(s, shape) for s in sel)
        self.win.reload_graph()
        self.win.update_preview()
        self.win._status("Converted %d curves into native guides." % n)

    def export_curves(self):
        shape = self._groom()
        if shape:
            grp = groom.export_curves(shape, name=self.win.system.name + "_guideCurves")
            self.win._status("Guides baked to %s" % grp)

    def _show_roots(self, on):
        shape = self._groom()
        if shape:
            cmds.setAttr(shape + ".showRoots", bool(on))
