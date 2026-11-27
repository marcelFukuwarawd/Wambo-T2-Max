"""Node-graph editor (QGraphicsView) for the fur graph.

Mouse / keys
  LMB drag output->input port .... connect      drag a connected input ... re-route / detach
  Tab or RMB on empty space ...... create node   RMB on node ............. node menu
  D ... set display (viewport) node   B ... bypass   Del ... delete   F ... frame
  MMB / Alt+LMB ... pan              wheel ... zoom
"""
from .qt import QtCore, QtGui, QtWidgets, Qt, Signal, exec_, event_pos
from . import style
from ..core.nodes import NODE_TYPES, CATEGORY_COLORS

NODE_W = 178.0
HEADER_H = 34.0
ROW_H = 20.0
PORT_R = 5.5


def _color(hexstr, alpha=255):
    c = QtGui.QColor(hexstr)
    c.setAlpha(alpha)
    return c


class PortItem(QtWidgets.QGraphicsEllipseItem):
    def __init__(self, node_item, index, is_output, label=""):
        super(PortItem, self).__init__(-PORT_R, -PORT_R, 2 * PORT_R, 2 * PORT_R, node_item)
        self.node_item = node_item
        self.index = index
        self.is_output = is_output
        self.label = label
        self.setAcceptHoverEvents(True)
        self.setZValue(2)
        self._hover = False
        self._refresh()

    def _refresh(self):
        col = _color(CATEGORY_COLORS.get(self.node_item.op.category, "#888"))
        self.setBrush(QtGui.QBrush(col.lighter(130) if self._hover else col))
        self.setPen(QtGui.QPen(_color("#111111"), 1.5))

    def hoverEnterEvent(self, e):
        self._hover = True
        self._refresh()

    def hoverLeaveEvent(self, e):
        self._hover = False
        self._refresh()

    def scene_center(self):
        return self.mapToScene(QtCore.QPointF(0, 0))


class NodeItem(QtWidgets.QGraphicsItem):
    def __init__(self, gnode):
        super(NodeItem, self).__init__()
        self.gnode = gnode
        self.op = NODE_TYPES[gnode.type_name]
        self.edges = []
        self.setFlags(QtWidgets.QGraphicsItem.ItemIsMovable | QtWidgets.QGraphicsItem.ItemIsSelectable |
                      QtWidgets.QGraphicsItem.ItemSendsGeometryChanges)
        self.setPos(QtCore.QPointF(*gnode.pos))
        self.setZValue(1)
        rows = max(len(self.op.inputs), 1)
        self.h = HEADER_H + rows * ROW_H + 10
        self.is_display = False
        self.error = None
        self.stats = None
        self.inputs = [PortItem(self, i, False, name) for i, name in enumerate(self.op.inputs)]
        for i, p in enumerate(self.inputs):
            p.setPos(0, HEADER_H + ROW_H * i + ROW_H * 0.5 + 4)
        self.output = None
        if self.op.category != "Output":
            self.output = PortItem(self, 0, True)
            self.output.setPos(NODE_W, HEADER_H + ROW_H * 0.5 + 4)

    def boundingRect(self):
        return QtCore.QRectF(-8, -8, NODE_W + 16, self.h + 30)

    def paint(self, painter, option, widget=None):
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        cat_col = _color(CATEGORY_COLORS.get(self.op.category, "#888"))
        body = QtCore.QRectF(0, 0, NODE_W, self.h)
        # shadow
        painter.setPen(Qt.NoPen)
        painter.setBrush(_color("#000000", 90))
        painter.drawRoundedRect(body.translated(3, 4), 7, 7)
        # body
        painter.setBrush(_color(style.PANEL_2, 120 if self.gnode.bypass else 245))
        if self.error:
            pen = QtGui.QPen(_color(style.ERROR), 2)
        elif self.isSelected():
            pen = QtGui.QPen(_color(style.ACCENT), 2)
        else:
            pen = QtGui.QPen(_color("#14161a"), 1.2)
        painter.setPen(pen)
        painter.drawRoundedRect(body, 7, 7)
        # header
        hdr = QtGui.QPainterPath()
        hdr.addRoundedRect(QtCore.QRectF(1, 1, NODE_W - 2, HEADER_H), 6, 6)
        hdr.addRect(QtCore.QRectF(1, HEADER_H - 8, NODE_W - 2, 8))
        grad = QtGui.QLinearGradient(0, 0, 0, HEADER_H)
        grad.setColorAt(0, cat_col.lighter(115))
        grad.setColorAt(1, cat_col.darker(135))
        painter.setPen(Qt.NoPen)
        painter.setBrush(QtGui.QBrush(grad))
        painter.drawPath(hdr.simplified())
        # titles
        f = painter.font()
        f.setPointSizeF(9.5)
        f.setBold(True)
        painter.setFont(f)
        painter.setPen(_color("#141414") if self.op.category == "Output" else _color("#ffffff"))
        painter.drawText(QtCore.QRectF(10, 2, NODE_W - 40, 18), Qt.AlignLeft | Qt.AlignVCenter, self.gnode.name)
        f.setBold(False)
        f.setPointSizeF(7.5)
        painter.setFont(f)
        painter.setPen(_color("#202020", 200) if self.op.category == "Output" else _color("#ffffff", 170))
        painter.drawText(QtCore.QRectF(10, 16, NODE_W - 40, 16), Qt.AlignLeft | Qt.AlignVCenter, self.op.label)
        # display flag (right side of header)
        flag = QtCore.QRectF(NODE_W - 26, 8, 18, 18)
        painter.setPen(QtGui.QPen(_color("#101010", 160), 1))
        painter.setBrush(_color(style.ACCENT_2) if self.is_display else _color("#000000", 60))
        painter.drawRoundedRect(flag, 4, 4)
        if self.is_display:
            painter.setPen(QtGui.QPen(_color("#0d2633"), 1.6))
            painter.setBrush(Qt.NoBrush)
            painter.drawEllipse(flag.center(), 5, 3)
            painter.setBrush(_color("#0d2633"))
            painter.drawEllipse(flag.center(), 1.6, 1.6)
        # port labels
        painter.setPen(_color(style.TEXT_DIM))
        for p in self.inputs:
            painter.drawText(QtCore.QRectF(12, p.pos().y() - 9, NODE_W / 2, 18),
                             Qt.AlignLeft | Qt.AlignVCenter, p.label)
        if self.output is not None:
            painter.drawText(QtCore.QRectF(NODE_W / 2, self.output.pos().y() - 9, NODE_W / 2 - 12, 18),
                             Qt.AlignRight | Qt.AlignVCenter, "out")
        if self.gnode.bypass:
            painter.setPen(QtGui.QPen(_color(style.ACCENT, 200), 2, Qt.DashLine))
            painter.drawLine(QtCore.QPointF(8, self.h - 6), QtCore.QPointF(NODE_W - 8, self.h - 6))
        # stats / errors below the node
        f.setPointSizeF(7.5)
        painter.setFont(f)
        if self.error:
            painter.setPen(_color(style.ERROR))
            painter.drawText(QtCore.QRectF(0, self.h + 3, NODE_W + 80, 16), Qt.AlignLeft, u"⚠ " + self.error)
        elif self.stats:
            secs, count = self.stats
            painter.setPen(_color(style.TEXT_DIM))
            painter.drawText(QtCore.QRectF(0, self.h + 3, NODE_W + 40, 16), Qt.AlignLeft,
                             "{:,} strands  ·  {:.0f} ms".format(count, secs * 1000.0))

    def itemChange(self, change, value):
        if change == QtWidgets.QGraphicsItem.ItemPositionHasChanged:
            for e in self.edges:
                e.update_path()
        return super(NodeItem, self).itemChange(change, value)


class EdgeItem(QtWidgets.QGraphicsPathItem):
    def __init__(self, src_item, dst_item, port):
        super(EdgeItem, self).__init__()
        self.src = src_item
        self.dst = dst_item
        self.port = port
        self.setZValue(0)
        col = _color(CATEGORY_COLORS.get(src_item.op.category, "#999"))
        self.setPen(QtGui.QPen(col, 2.2))
        src_item.edges.append(self)
        dst_item.edges.append(self)
        self.update_path()

    def update_path(self):
        a = self.src.output.scene_center()
        b = self.dst.inputs[self.port].scene_center()
        self.setPath(bezier(a, b))

    def detach(self):
        for it in (self.src, self.dst):
            if self in it.edges:
                it.edges.remove(self)


def bezier(a, b):
    path = QtGui.QPainterPath(a)
    dx = max(abs(b.x() - a.x()) * 0.5, 40.0)
    path.cubicTo(QtCore.QPointF(a.x() + dx, a.y()), QtCore.QPointF(b.x() - dx, b.y()), b)
    return path


class NodeSearch(QtWidgets.QWidget):
    """Tab-key popup: type to filter, Enter to create."""
    chosen = Signal(str)

    def __init__(self, parent=None):
        super(NodeSearch, self).__init__(parent, Qt.Popup)
        self.setFixedWidth(220)
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        self.edit = QtWidgets.QLineEdit()
        self.edit.setPlaceholderText("Search nodes...")
        self.list = QtWidgets.QListWidget()
        self.list.setFixedHeight(260)
        lay.addWidget(self.edit)
        lay.addWidget(self.list)
        self.edit.textChanged.connect(self._fill)
        self.edit.returnPressed.connect(self._accept)
        self.list.itemActivated.connect(self._accept)
        self._fill("")

    def _fill(self, text):
        self.list.clear()
        t = text.lower()
        for name, op in NODE_TYPES.items():
            if t in op.label.lower() or t in op.category.lower() or t in name:
                it = QtWidgets.QListWidgetItem("%s   — %s" % (op.label, op.category))
                it.setData(Qt.UserRole, name)
                it.setForeground(QtGui.QBrush(_color(CATEGORY_COLORS.get(op.category, "#ccc"))))
                it.setToolTip(op.tip)
                self.list.addItem(it)
        if self.list.count():
            self.list.setCurrentRow(0)

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key_Down, Qt.Key_Up):
            QtWidgets.QApplication.sendEvent(self.list, e)
            return
        super(NodeSearch, self).keyPressEvent(e)

    def _accept(self, *a):
        it = self.list.currentItem()
        if it:
            self.chosen.emit(it.data(Qt.UserRole))
        self.close()


class GraphScene(QtWidgets.QGraphicsScene):
    def drawBackground(self, painter, rect):
        painter.fillRect(rect, _color(style.BG))
        for step, col in ((20, _color("#262a31")), (100, _color("#2e333b"))):
            left = int(rect.left()) - (int(rect.left()) % step)
            top = int(rect.top()) - (int(rect.top()) % step)
            lines = []
            x = left
            while x < rect.right():
                lines.append(QtCore.QLineF(x, rect.top(), x, rect.bottom()))
                x += step
            y = top
            while y < rect.bottom():
                lines.append(QtCore.QLineF(rect.left(), y, rect.right(), y))
                y += step
            painter.setPen(QtGui.QPen(col, 1))
            painter.drawLines(lines)


class GraphView(QtWidgets.QGraphicsView):
    """Signals
    nodeSelected(str)  node id ('' when nothing selected)
    graphChanged(bool) structure/params changed; bool = needs re-evaluation
    """
    nodeSelected = Signal(str)
    graphChanged = Signal(bool)

    def __init__(self, parent=None):
        super(GraphView, self).__init__(parent)
        self.setScene(GraphScene(self))
        self.scene().setSceneRect(-5000, -5000, 10000, 10000)
        self.setRenderHint(QtGui.QPainter.Antialiasing)
        self.setViewportUpdateMode(QtWidgets.QGraphicsView.FullViewportUpdate)
        self.setTransformationAnchor(QtWidgets.QGraphicsView.AnchorUnderMouse)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setDragMode(QtWidgets.QGraphicsView.RubberBandDrag)
        self.setFocusPolicy(Qt.StrongFocus)
        self.graph = None
        self.node_items = {}
        self._drag_edge = None
        self._drag_src = None
        self._pan = None
        self._moved = False
        self.scene().selectionChanged.connect(self._sel_changed)

    # ------------------------------------------------------------ building
    def set_graph(self, graph):
        self.scene().clearSelection()
        for it in list(self.node_items.values()):
            self.scene().removeItem(it)
        for it in [i for i in self.scene().items() if isinstance(i, EdgeItem)]:
            self.scene().removeItem(it)
        self.node_items = {}
        self.graph = graph
        if graph is None:
            return
        for nid, n in graph.nodes.items():
            it = NodeItem(n)
            self.node_items[nid] = it
            self.scene().addItem(it)
        for s, d, p in graph.connections:
            if s in self.node_items and d in self.node_items and self.node_items[s].output is not None:
                self.scene().addItem(EdgeItem(self.node_items[s], self.node_items[d], p))
        self.refresh_flags()

    def refresh_flags(self, stats=None, errors=None):
        for nid, it in self.node_items.items():
            it.is_display = (self.graph.display_id == nid)
            if stats is not None:
                it.stats = stats.get(nid)
            if errors is not None:
                it.error = errors.get(nid)
            it.update()

    def frame_all(self):
        if not self.node_items:
            return
        rect = QtCore.QRectF()
        for it in self.node_items.values():
            rect = rect.united(it.sceneBoundingRect())
        self.fitInView(rect.adjusted(-60, -60, 60, 60), Qt.KeepAspectRatio)
        if self.transform().m11() > 1.2:
            self.resetTransform()
            self.centerOn(rect.center())

    def selected_ids(self):
        return [it.gnode.id for it in self.scene().selectedItems() if isinstance(it, NodeItem)]

    def _sel_changed(self):
        ids = self.selected_ids()
        self.nodeSelected.emit(ids[0] if ids else "")

    # ------------------------------------------------------------- editing
    def create_node(self, type_name, scene_pos=None):
        if self.graph is None:
            return None
        if scene_pos is None:
            scene_pos = self.mapToScene(self.viewport().rect().center())
        op = NODE_TYPES[type_name]
        n = self.graph.add_node(type_name, None, (scene_pos.x(), scene_pos.y()))
        it = NodeItem(n)
        self.node_items[n.id] = it
        self.scene().addItem(it)
        # auto-wire after the selected node
        sel = self.selected_ids()
        if sel and op.inputs and self.node_items[sel[0]].output is not None:
            try:
                self.graph.connect(sel[0], n.id, 0)
            except ValueError:
                pass
        if type_name == "output" or self.graph.display_id is None:
            self.graph.display_id = n.id
        self.set_graph(self.graph)
        self.node_items[n.id].setSelected(True)
        self.graphChanged.emit(True)
        return n

    def delete_selected(self):
        ids = self.selected_ids()
        if not ids:
            return
        for nid in ids:
            self.graph.remove_node(nid)
        self.set_graph(self.graph)
        self.graphChanged.emit(True)

    def set_display(self, nid):
        self.graph.display_id = nid
        self.refresh_flags()
        self.graphChanged.emit(True)

    def toggle_bypass(self, ids):
        for nid in ids:
            n = self.graph.nodes[nid]
            n.bypass = not n.bypass
            self.node_items[nid].update()
        self.graphChanged.emit(True)

    # -------------------------------------------------------------- events
    def _port_at(self, view_pos):
        for it in self.items_at(view_pos):
            if isinstance(it, PortItem):
                return it
        return None

    def items_at(self, view_pos):
        r = 7
        return self.items_in_rect(QtCore.QRect(view_pos.x() - r, view_pos.y() - r, 2 * r, 2 * r))

    def items_in_rect(self, rect):
        return QtWidgets.QGraphicsView.items(self, rect)

    def mousePressEvent(self, e):
        pos = event_pos(e)
        if e.button() == Qt.MiddleButton or (e.button() == Qt.LeftButton and e.modifiers() & Qt.AltModifier):
            self._pan = pos
            self.setCursor(Qt.ClosedHandCursor)
            return
        if e.button() == Qt.LeftButton:
            port = self._port_at(pos)
            if port is not None:
                if port.is_output:
                    self._drag_src = port.node_item
                else:
                    # pick up an existing connection from this input
                    src = self.graph.input_of(port.node_item.gnode.id, port.index)
                    if src is None:
                        return
                    self.graph.disconnect(port.node_item.gnode.id, port.index)
                    self.set_graph(self.graph)
                    self._drag_src = self.node_items[src]
                    self.graphChanged.emit(True)
                self._drag_edge = QtWidgets.QGraphicsPathItem()
                self._drag_edge.setPen(QtGui.QPen(_color(style.ACCENT), 2, Qt.DashLine))
                self.scene().addItem(self._drag_edge)
                return
            # flag click -> display
            for it in self.items_at(pos):
                if isinstance(it, NodeItem):
                    local = it.mapFromScene(self.mapToScene(pos))
                    if QtCore.QRectF(NODE_W - 28, 6, 22, 22).contains(local):
                        self.set_display(it.gnode.id)
                        return
                    break
        self._moved = False
        super(GraphView, self).mousePressEvent(e)

    def mouseMoveEvent(self, e):
        pos = event_pos(e)
        if self._pan is not None:
            d = pos - self._pan
            self._pan = pos
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - d.x())
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - d.y())
            return
        if self._drag_edge is not None:
            a = self._drag_src.output.scene_center()
            self._drag_edge.setPath(bezier(a, self.mapToScene(pos)))
            return
        if e.buttons() & Qt.LeftButton:
            self._moved = True
        super(GraphView, self).mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        pos = event_pos(e)
        if self._pan is not None:
            self._pan = None
            self.setCursor(Qt.ArrowCursor)
            return
        if self._drag_edge is not None:
            self.scene().removeItem(self._drag_edge)
            self._drag_edge = None
            port = self._port_at(pos)
            if port is not None and not port.is_output and port.node_item is not self._drag_src:
                try:
                    self.graph.connect(self._drag_src.gnode.id, port.node_item.gnode.id, port.index)
                    self.set_graph(self.graph)
                    self.graphChanged.emit(True)
                except ValueError as err:
                    QtWidgets.QToolTip.showText(QtGui.QCursor.pos(), str(err))
            self._drag_src = None
            return
        super(GraphView, self).mouseReleaseEvent(e)
        if self._moved:
            for it in self.scene().selectedItems():
                if isinstance(it, NodeItem):
                    it.gnode.pos = (it.pos().x(), it.pos().y())
            self._moved = False
            self.graphChanged.emit(False)

    def wheelEvent(self, e):
        f = 1.15 if e.angleDelta().y() > 0 else 1 / 1.15
        s = self.transform().m11() * f
        if 0.15 < s < 3.0:
            self.scale(f, f)

    def mouseDoubleClickEvent(self, e):
        for it in self.items_at(event_pos(e)):
            if isinstance(it, NodeItem):
                self.set_display(it.gnode.id)
                return
        super(GraphView, self).mouseDoubleClickEvent(e)

    def keyPressEvent(self, e):
        k = e.key()
        if k == Qt.Key_Tab:
            self.open_search()
        elif k in (Qt.Key_Delete, Qt.Key_Backspace):
            self.delete_selected()
        elif k == Qt.Key_F:
            self.frame_all()
        elif k == Qt.Key_D:
            ids = self.selected_ids()
            if ids:
                self.set_display(ids[0])
        elif k == Qt.Key_B:
            self.toggle_bypass(self.selected_ids())
        else:
            super(GraphView, self).keyPressEvent(e)

    def focusNextPrevChild(self, nxt):
        return False  # keep Tab for the node search

    def open_search(self, global_pos=None):
        gp = global_pos or QtGui.QCursor.pos()
        scene_pos = self.mapToScene(self.mapFromGlobal(gp))
        pop = NodeSearch(self)
        pop.chosen.connect(lambda t: self.create_node(t, scene_pos))
        pop.move(gp)
        pop.show()
        pop.edit.setFocus()

    def contextMenuEvent(self, e):
        node = None
        for it in self.items_at(e.pos()):
            if isinstance(it, NodeItem):
                node = it
                break
        menu = QtWidgets.QMenu(self)
        scene_pos = self.mapToScene(e.pos())
        if node is not None:
            nid = node.gnode.id
            if not node.isSelected():
                self.scene().clearSelection()
                node.setSelected(True)
            menu.addAction("Set Display  (D)", lambda: self.set_display(nid))
            menu.addAction("Bypass  (B)", lambda: self.toggle_bypass([nid]))
            menu.addSeparator()
            menu.addAction("Delete  (Del)", self.delete_selected)
        else:
            cats = {}
            for name, op in NODE_TYPES.items():
                if op.category not in cats:
                    cats[op.category] = menu.addMenu(op.category)
                act = cats[op.category].addAction(op.label)
                act.setToolTip(op.tip)
                act.triggered.connect(lambda checked=False, t=name: self.create_node(t, scene_pos))
            menu.addSeparator()
            menu.addAction("Frame All  (F)", self.frame_all)
        exec_(menu, e.globalPos())
