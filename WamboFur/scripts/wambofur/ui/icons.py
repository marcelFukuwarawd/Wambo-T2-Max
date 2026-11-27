"""Tool icons drawn with QPainter (no image files needed)."""
import math

from .qt import QtCore, QtGui, Qt

_CACHE = {}


def _pen(color, w=2.0):
    p = QtGui.QPen(QtGui.QColor(color), w)
    p.setCapStyle(Qt.RoundCap)
    p.setJoinStyle(Qt.RoundJoin)
    return p


def _strand(p, x, y0, y1, bend=0.0):
    path = QtGui.QPainterPath(QtCore.QPointF(x, y0))
    path.cubicTo(QtCore.QPointF(x, y0 - (y0 - y1) * 0.5), QtCore.QPointF(x + bend, y1 + 4),
                 QtCore.QPointF(x + bend, y1))
    p.drawPath(path)


def make_icon(name, color="#d7dce3", accent="#f0a83a", size=40):
    key = (name, color, accent, size)
    if key in _CACHE:
        return _CACHE[key]
    pm = QtGui.QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QtGui.QPainter(pm)
    p.setRenderHint(QtGui.QPainter.Antialiasing)
    p.scale(size / 40.0, size / 40.0)
    p.setBrush(Qt.NoBrush)
    ground = lambda: (p.setPen(_pen(color, 2.4)), p.drawLine(QtCore.QPointF(6, 33), QtCore.QPointF(34, 33)))
    p.setPen(_pen(color))
    if name == "add":
        ground()
        p.setPen(_pen(color))
        _strand(p, 14, 33, 12)
        p.setPen(_pen(accent, 2.6))
        p.drawLine(QtCore.QPointF(27, 10), QtCore.QPointF(27, 24))
        p.drawLine(QtCore.QPointF(20, 17), QtCore.QPointF(34, 17))
    elif name == "paint":
        ground()
        p.setPen(_pen(color, 1.8))
        for x, h in ((10, 14), (15, 10), (20, 16), (25, 11), (30, 15)):
            _strand(p, x, 33, 33 - h, 2)
        p.setPen(_pen(accent, 1.6))
        p.drawEllipse(QtCore.QPointF(20, 20), 13, 13)
    elif name == "remove":
        ground()
        p.setPen(_pen(color, 1.8))
        for x in (11, 17, 23):
            _strand(p, x, 33, 16, 2)
        p.setPen(_pen("#ff5c5c", 2.8))
        p.drawLine(QtCore.QPointF(24, 8), QtCore.QPointF(35, 19))
        p.drawLine(QtCore.QPointF(35, 8), QtCore.QPointF(24, 19))
    elif name == "comb":
        ground()
        p.setPen(_pen(color, 1.8))
        for x in (10, 16, 22):
            _strand(p, x, 33, 14, 10)
        p.setPen(_pen(accent, 2.0))
        p.drawRoundedRect(QtCore.QRectF(22, 6, 14, 5), 1.5, 1.5)
        for x in (24, 27, 30, 33):
            p.drawLine(QtCore.QPointF(x, 11), QtCore.QPointF(x, 16))
    elif name == "move":
        p.setPen(_pen(accent, 2.2))
        c = QtCore.QPointF(20, 20)
        for a in range(4):
            ang = a * math.pi / 2
            tip = QtCore.QPointF(20 + 14 * math.cos(ang), 20 + 14 * math.sin(ang))
            p.drawLine(c, tip)
            for s in (-1, 1):
                p.drawLine(tip, QtCore.QPointF(tip.x() - 5 * math.cos(ang + s * 0.6),
                                               tip.y() - 5 * math.sin(ang + s * 0.6)))
    elif name == "smooth":
        ground()
        p.setPen(_pen(color, 1.6))
        path = QtGui.QPainterPath(QtCore.QPointF(10, 33))
        for i, (x, y) in enumerate(((13, 28), (8, 23), (14, 18), (9, 12))):
            path.lineTo(QtCore.QPointF(x, y))
        p.drawPath(path)
        p.setPen(_pen(accent, 2.2))
        path = QtGui.QPainterPath(QtCore.QPointF(26, 33))
        path.cubicTo(QtCore.QPointF(26, 24), QtCore.QPointF(30, 18), QtCore.QPointF(30, 10))
        p.drawPath(path)
    elif name == "puff":
        ground()
        p.setPen(_pen(color, 1.8))
        for x, b in ((12, -6), (20, 0), (28, 6)):
            _strand(p, x, 33, 14, b)
        p.setPen(_pen(accent, 2.2))
        p.drawLine(QtCore.QPointF(20, 12), QtCore.QPointF(20, 4))
        p.drawLine(QtCore.QPointF(20, 4), QtCore.QPointF(16, 8))
        p.drawLine(QtCore.QPointF(20, 4), QtCore.QPointF(24, 8))
    elif name == "straighten":
        ground()
        p.setPen(_pen(color, 1.6))
        _strand(p, 12, 33, 12, 9)
        p.setPen(_pen(accent, 2.4))
        p.drawLine(QtCore.QPointF(26, 33), QtCore.QPointF(26, 8))
    elif name == "clump":
        ground()
        p.setPen(_pen(color, 1.8))
        for x in (8, 14, 20, 26, 32):
            path = QtGui.QPainterPath(QtCore.QPointF(x, 33))
            path.cubicTo(QtCore.QPointF(x, 22), QtCore.QPointF(20, 18), QtCore.QPointF(20, 8))
            p.drawPath(path)
        p.setPen(_pen(accent, 2.2))
        p.drawEllipse(QtCore.QPointF(20, 9), 3, 3)
    elif name == "length":
        ground()
        p.setPen(_pen(color, 1.8))
        _strand(p, 13, 33, 18)
        _strand(p, 20, 33, 10)
        p.setPen(_pen(accent, 2.2))
        p.drawLine(QtCore.QPointF(30, 8), QtCore.QPointF(30, 30))
        for y, d in ((8, 1), (30, -1)):
            p.drawLine(QtCore.QPointF(30, y), QtCore.QPointF(26, y + 4 * d))
            p.drawLine(QtCore.QPointF(30, y), QtCore.QPointF(34, y + 4 * d))
    elif name == "cut":
        ground()
        p.setPen(_pen(color, 1.8))
        for x in (10, 16, 22, 28):
            p.drawLine(QtCore.QPointF(x, 33), QtCore.QPointF(x, 20))
        p.setPen(_pen(color, 1.0))
        for x in (10, 16, 22, 28):
            p.drawLine(QtCore.QPointF(x + 2, 14), QtCore.QPointF(x + 3, 6))
        p.setPen(_pen(accent, 2.2, ))
        p.drawLine(QtCore.QPointF(4, 17), QtCore.QPointF(36, 17))
    elif name == "select":
        p.setPen(_pen(color, 1.8))
        p.setBrush(QtGui.QColor(color))
        poly = QtGui.QPolygonF([QtCore.QPointF(12, 6), QtCore.QPointF(12, 32), QtCore.QPointF(19, 25),
                                QtCore.QPointF(24, 35), QtCore.QPointF(28, 33), QtCore.QPointF(23, 23),
                                QtCore.QPointF(32, 23)])
        p.drawPolygon(poly)
    p.end()
    icon = QtGui.QIcon(pm)
    _CACHE[key] = icon
    return icon
