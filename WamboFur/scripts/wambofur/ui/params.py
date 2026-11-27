"""Auto-generated parameter editor for the selected graph node."""
from .qt import QtWidgets, Qt, Signal
from ..core.nodes import NODE_TYPES, CATEGORY_COLORS


def _soft_max(p):
    if p.hi is None:
        return max(abs(float(p.default)) * 4.0, 1.0)
    d = abs(float(p.default))
    soft = max(d * 4.0, 1.0)
    return min(float(p.hi), soft)


class FloatField(QtWidgets.QWidget):
    """Spin box + slider. Slider covers a 'soft' range, the box the full range."""
    changed = Signal(object)

    def __init__(self, p, value, is_int=False, parent=None):
        super(FloatField, self).__init__(parent)
        self.is_int = is_int
        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        if is_int:
            self.box = QtWidgets.QSpinBox()
            self.box.setRange(int(p.lo if p.lo is not None else -10 ** 9), int(p.hi if p.hi is not None else 10 ** 9))
        else:
            self.box = QtWidgets.QDoubleSpinBox()
            self.box.setDecimals(4)
            self.box.setRange(float(p.lo if p.lo is not None else -1e9), float(p.hi if p.hi is not None else 1e9))
            self.box.setSingleStep(0.01 if _soft_max(p) <= 2 else 0.1)
        self.box.setFixedWidth(84)
        self.box.setKeyboardTracking(False)
        self.slider = QtWidgets.QSlider(Qt.Horizontal)
        self.lo = float(p.lo if p.lo is not None else 0.0)
        self.hi = _soft_max(p)
        self.slider.setRange(0, 1000)
        lay.addWidget(self.box)
        lay.addWidget(self.slider, 1)
        self._block = False
        self.set_value(value)
        self.box.valueChanged.connect(self._box_changed)
        self.slider.valueChanged.connect(self._slider_changed)
        self.slider.sliderReleased.connect(lambda: self.changed.emit(self.value()))

    def value(self):
        return int(self.box.value()) if self.is_int else float(self.box.value())

    def set_value(self, v):
        self._block = True
        self.box.setValue(v)
        span = max(self.hi - self.lo, 1e-9)
        self.slider.setValue(int(round(1000 * (min(max(v, self.lo), self.hi) - self.lo) / span)))
        self._block = False

    def _box_changed(self, v):
        if self._block:
            return
        self.set_value(v)
        self.changed.emit(self.value())

    def _slider_changed(self, s):
        if self._block:
            return
        v = self.lo + (self.hi - self.lo) * s / 1000.0
        self._block = True
        self.box.setValue(int(round(v)) if self.is_int else v)
        self._block = False
        self.changed.emit(self.value())


class VectorField(QtWidgets.QWidget):
    changed = Signal(object)

    def __init__(self, value, parent=None):
        super(VectorField, self).__init__(parent)
        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.boxes = []
        for i, axis in enumerate("XYZ"):
            b = QtWidgets.QDoubleSpinBox()
            b.setRange(-1e6, 1e6)
            b.setDecimals(3)
            b.setSingleStep(0.1)
            b.setValue(float(value[i]))
            b.setKeyboardTracking(False)
            b.setPrefix(axis + " ")
            b.valueChanged.connect(lambda *_: self.changed.emit(self.value()))
            lay.addWidget(b)
            self.boxes.append(b)

    def value(self):
        return tuple(b.value() for b in self.boxes)


class PickField(QtWidgets.QWidget):
    changed = Signal(object)

    def __init__(self, value, pick, picker, parent=None):
        super(PickField, self).__init__(parent)
        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.edit = QtWidgets.QLineEdit(value)
        self.edit.setPlaceholderText({"mesh": "mesh", "curves": "guide group",
                                      "texture": "texture node (optional)"}.get(pick, ""))
        btn = QtWidgets.QToolButton()
        btn.setText(u"◀ Sel")
        btn.setToolTip("Use the current Maya selection")
        lay.addWidget(self.edit, 1)
        lay.addWidget(btn)
        self.edit.editingFinished.connect(lambda: self.changed.emit(self.edit.text().strip()))

        def do_pick():
            v = picker(pick) if picker else None
            if v:
                self.edit.setText(v)
                self.changed.emit(v)
        btn.clicked.connect(do_pick)


class ParamPanel(QtWidgets.QScrollArea):
    """paramChanged(node_id, name, value); renamed(node_id, name)"""
    paramChanged = Signal(str, str, object)
    renamed = Signal(str, str)

    def __init__(self, picker=None, parent=None):
        super(ParamPanel, self).__init__(parent)
        self.setWidgetResizable(True)
        self.picker = picker
        self.node = None
        self.show_node(None)

    def show_node(self, gnode):
        self.node = gnode
        w = QtWidgets.QWidget()
        w.setObjectName("wfPanel")
        lay = QtWidgets.QVBoxLayout(w)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(6)
        if gnode is None:
            lbl = QtWidgets.QLabel("Select a node in the graph to edit it.\n\n"
                                   "Tab  -  add node\nD  -  display in viewport\nB  -  bypass\nF  -  frame all")
            lbl.setObjectName("wfDim")
            lay.addWidget(lbl)
            lay.addStretch(1)
            self.setWidget(w)
            return
        op = NODE_TYPES[gnode.type_name]
        head = QtWidgets.QHBoxLayout()
        chip = QtWidgets.QLabel(op.category.upper())
        chip.setStyleSheet("background:%s;color:#111;border-radius:3px;padding:2px 6px;font-weight:bold;"
                           % CATEGORY_COLORS.get(op.category, "#888"))
        title = QtWidgets.QLabel(op.label)
        title.setObjectName("wfTitle")
        head.addWidget(title)
        head.addStretch(1)
        head.addWidget(chip)
        lay.addLayout(head)
        name = QtWidgets.QLineEdit(gnode.name)
        name.editingFinished.connect(lambda: self.renamed.emit(gnode.id, name.text().strip()))
        form0 = QtWidgets.QFormLayout()
        form0.addRow("Name", name)
        lay.addLayout(form0)
        if op.tip:
            tip = QtWidgets.QLabel(op.tip)
            tip.setWordWrap(True)
            tip.setObjectName("wfDim")
            lay.addWidget(tip)
        line = QtWidgets.QFrame()
        line.setFrameShape(QtWidgets.QFrame.HLine)
        lay.addWidget(line)
        form = QtWidgets.QFormLayout()
        form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        form.setHorizontalSpacing(10)
        for p in op.params:
            val = gnode.params.get(p.name, p.default)
            if p.kind in ("float", "int"):
                wdg = FloatField(p, val, is_int=(p.kind == "int"))
            elif p.kind == "bool":
                wdg = QtWidgets.QCheckBox()
                wdg.setChecked(bool(val))
                wdg.toggled.connect(lambda v, n=p.name: self.paramChanged.emit(gnode.id, n, bool(v)))
            elif p.kind == "enum":
                wdg = QtWidgets.QComboBox()
                wdg.addItems(p.options)
                wdg.setCurrentIndex(max(0, p.options.index(val) if val in p.options else 0))
                wdg.currentTextChanged.connect(lambda v, n=p.name: self.paramChanged.emit(gnode.id, n, v))
            elif p.kind == "vector":
                wdg = VectorField(val)
            elif p.kind == "string" and p.pick:
                wdg = PickField(val, p.pick, self.picker)
            else:
                wdg = QtWidgets.QLineEdit(str(val))
                wdg.editingFinished.connect(lambda w=wdg, n=p.name: self.paramChanged.emit(gnode.id, n, w.text()))
            if hasattr(wdg, "changed"):
                wdg.changed.connect(lambda v, n=p.name: self.paramChanged.emit(gnode.id, n, v))
            lbl = QtWidgets.QLabel(p.label)
            if p.tip:
                lbl.setToolTip(p.tip)
                wdg.setToolTip(p.tip)
            form.addRow(lbl, wdg)
        if not op.params:
            empty = QtWidgets.QLabel("This node has no parameters.")
            empty.setObjectName("wfDim")
            lay.addWidget(empty)
        lay.addLayout(form)
        lay.addStretch(1)
        self.setWidget(w)
