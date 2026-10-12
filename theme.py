#!/usr/bin/env python3
"""主题模块：WPS 风格配色 / 全局 QSS / 程序化矢量图标 / ICO 生成"""
from PySide6.QtGui import (QPixmap, QPainter, QColor, QPen, QBrush,
                           QLinearGradient, QIcon, QFont, QPolygonF)
from PySide6.QtCore import Qt, QRectF, QPointF, QSettings

# ---------- 配色（贴近 WPS 2023/2026 浅色主题） ----------
BRAND = QColor("#2B6DE8")          # 主品牌蓝
BRAND_RED = QColor("#FA5151")      # 点缀红（保存等强调）
BG_TOP = "#FFFFFF"                 # 工具栏底
BG_PAGE = "#D6D9DE"                # 页面区底
HOVER = "#EAEFF7"
CHECKED = "#E3EDFF"
BORDER = "#E1E4EA"
TEXT = "#3A3F47"
SUBTLE = "#9AA1AC"

QSS = f"""
* {{ font-family: "Microsoft YaHei UI", "Segoe UI", sans-serif; }}
QMainWindow, QDialog {{ background: {BG_TOP}; }}
QWidget#header {{ background: {BG_TOP}; border-bottom: 1px solid {BORDER}; }}
QLabel#groupLabel {{ color: {SUBTLE}; font-size: 11px; padding: 0 6px 0 10px; }}

QToolButton {{
    background: transparent; border: 0; border-radius: 6px;
    padding: 4px 8px; color: {TEXT}; font-size: 12px;
}}
QToolButton:hover {{ background: {HOVER}; }}
QToolButton:pressed {{ background: {BORDER}; }}
QToolButton:checked {{ background: {CHECKED}; color: #1E5FD0; }}
QToolButton:disabled {{ color: #C2C7CF; }}
QToolButton#accent {{
    background: {BRAND_RED.name()}; color: white; font-weight: 600;
    border-radius: 6px; padding: 5px 14px;
}}
QToolButton#accent:hover {{ background: #E14646; }}
QToolButton#accent:disabled {{ background: #F3B9B9; }}

QSpinBox {{
    background: white; border: 1px solid {BORDER}; border-radius: 5px;
    padding: 2px 6px; color: {TEXT}; font-size: 12px;
}}
QStatusBar {{ background: {BG_TOP}; border-top: 1px solid {BORDER};
              color: {SUBTLE}; font-size: 12px; }}
QAbstractScrollArea {{ background: {BG_PAGE}; border: 0; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: #B9BFC9; border-radius: 4px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: #9AA1AC; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: #B9BFC9; border-radius: 4px; min-width: 30px; }}

QLineEdit#editor {{
    background: white; border: 2px solid {BRAND.name()};
    border-radius: 3px; padding: 1px 5px; font-size: 12px;
}}

QMenu {{ background: white; border: 1px solid {BORDER}; padding: 4px; }}
QMenu::item {{ padding: 6px 24px; border-radius: 4px; }}
QMenu::item:selected {{ background: {HOVER}; }}

QListWidget {{ background: white; border: 1px solid {BORDER}; border-radius: 6px; }}
QPushButton {{
    background: white; border: 1px solid {BORDER}; border-radius: 6px;
    padding: 6px 16px; color: {TEXT}; font-size: 12px;
}}
QPushButton:hover {{ background: {HOVER}; }}
QPushButton#primary {{ background: {BRAND.name()}; color: white; border: 0; }}
QPushButton#primary:hover {{ background: #1E5FD0; }}

QInputDialog QLineEdit, QDialog QLineEdit {{
    background: white; border: 1px solid {BORDER}; border-radius: 5px;
    padding: 4px 8px;
}}
QDialog QLabel {{ color: {TEXT}; font-size: 12px; }}
"""


# ---------- 矢量图标 ----------
def _pm(size):
    p = QPixmap(size, size)
    p.fill(Qt.transparent)
    return p, QPainter(p)


def _base_pen(c, w=1.6):
    return QPen(c, w, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)


ICON = QColor("#4A505B")       # 图标主色
ACC = BRAND                    # 图标点缀


def tool_icon(key: str, size: int = 22) -> QPixmap:
    p, w = _pm(size)
    w.setRenderHint(QPainter.Antialiasing)
    pen = _base_pen(ICON, 1.7)
    w.setPen(pen)
    w.setBrush(Qt.NoBrush)
    m = size * 0.18                       # 边距
    R = QRectF(m, m, size - 2 * m, size - 2 * m)

    if key == "select":                   # 光标箭头
        pts = QPolygonF([QPointF(size*.42,size*.18), QPointF(size*.42,size*.74),
                         QPointF(size*.56,size*.60), QPointF(size*.66,size*.82)])
        w.setPen(Qt.NoPen); w.setBrush(QBrush(ICON))
        w.drawPolygon(pts)
    elif key == "insert_text":            # T+
        f = QFont(); f.setBold(True); f.setPixelSize(int(size*.5)); w.setFont(f)
        w.drawText(R.adjusted(0,-size*.06,0,0), Qt.AlignCenter, "T")
        w.setPen(_base_pen(ACC, 2)); w.drawLine(QPointF(size*.62,size*.78), QPointF(size*.85,size*.78))
        w.drawLine(QPointF(size*.735,size*.665), QPointF(size*.735,size*.895))
    elif key == "insert_image":
        w.drawRoundedRect(R, 3, 3)
        w.drawEllipse(QPointF(size*.35,size*.4), size*.06, size*.06)
        w.setPen(_base_pen(ICON,1.5))
        w.drawLine(QPointF(m+2,size*.78), QPointF(size*.4,size*.5),)
        w.drawLine(QPointF(size*.4,size*.5), QPointF(size*.58,size*.66))
        w.drawLine(QPointF(size*.58,size*.66), QPointF(size*.74,size*.52))
        w.drawLine(QPointF(size*.74,size*.52), QPointF(size*.82-1,size*.6))
    elif key == "highlight":              # 荧光笔斜杠
        w.setPen(_base_pen(QColor("#F7B500"), 3.4))
        w.drawLine(QPointF(size*.22,size*.8), QPointF(size*.8,size*.22))
        w.setPen(_base_pen(ICON,1.7)); w.setBrush(QBrush(ICON))
        w.drawRect(QRectF(size*.68,size*.12,size*.2,size*.2))
    elif key == "area_hl":
        w.setBrush(QBrush(QColor(247,181,0,90))); w.setPen(_base_pen(QColor("#D89E00"),1.4))
        w.drawRoundedRect(R, 2, 2)
    elif key == "underline":
        f = QFont(); f.setPixelSize(int(size*.52)); f.setBold(True); w.setFont(f)
        w.drawText(R.adjusted(0,-size*.05,0,0), Qt.AlignCenter, "U")
        w.setPen(_base_pen(ACC,2)); w.drawLine(QPointF(size*.24,size*.8), QPointF(size*.76,size*.8))
    elif key == "strikeout":
        f = QFont(); f.setPixelSize(int(size*.5)); f.setBold(True); w.setFont(f)
        w.drawText(R, Qt.AlignCenter, "S")
        w.setPen(_base_pen(ACC,2)); w.drawLine(QPointF(size*.2,size*.5), QPointF(size*.8,size*.5))
    elif key == "note":                   # 便签
        w.setPen(_base_pen(ICON,1.5)); w.setBrush(QBrush(QColor("#FFF3C2")))
        path_pts = QPolygonF([R.topLeft(), QPointF(R.right(),R.top()),
                              QPointF(R.right(),R.bottom()-size*.22),
                              QPointF(R.right()-size*.22,R.bottom()), R.bottomLeft()])
        w.drawPolygon(path_pts)
        w.drawLine(QPointF(R.right()-size*.22,R.bottom()), QPointF(R.right()-size*.22,R.bottom()-size*.22))
        w.drawLine(QPointF(R.right()-size*.22,R.bottom()-size*.22), QPointF(R.right(),R.bottom()-size*.22))
    elif key == "textbox":
        w.drawRoundedRect(R, 3, 3)
        w.drawLine(QPointF(size*.32,size*.36), QPointF(size*.68,size*.36))
        w.drawLine(QPointF(size*.32,size*.5), QPointF(size*.68,size*.5))
        w.drawLine(QPointF(size*.32,size*.64), QPointF(size*.56,size*.64))
    elif key == "ink":                    # 铅笔
        w.save(); w.translate(size*.5,size*.5); w.rotate(-45)
        w.setPen(_base_pen(ICON,1.5)); w.setBrush(QBrush(QColor("#F7B500")))
        w.drawRoundedRect(QRectF(-size*.12,-size*.32,size*.24,size*.5),2,2)
        w.setBrush(QBrush(ICON))
        w.drawPolygon(QPolygonF([QPointF(-size*.12,size*.18),QPointF(size*.12,size*.18),QPointF(0,size*.36)]))
        w.restore()
    elif key == "rect":
        w.drawRoundedRect(R, 2, 2)
    elif key == "oval":
        w.drawEllipse(R)
    elif key == "line":
        w.drawLine(R.bottomLeft(), R.topRight())
    elif key == "arrow":
        w.drawLine(QPointF(size*.2,size*.8), QPointF(size*.76,size*.24))
        w.drawLine(QPointF(size*.76,size*.24), QPointF(size*.52,size*.26))
        w.drawLine(QPointF(size*.76,size*.24), QPointF(size*.74,size*.48))
    elif key == "stamp":                  # 圆章
        w.drawEllipse(R)
        w.setPen(_base_pen(ACC,2))
        w.drawLine(QPointF(size*.36,size*.5), QPointF(size*.46,size*.62))
        w.drawLine(QPointF(size*.46,size*.62), QPointF(size*.66,size*.38))
    elif key == "watermark":
        w.drawRoundedRect(R, 2, 2)
        w.setPen(_base_pen(QColor("#9AA1AC"), 1.4))
        w.drawLine(QPointF(size*.26,size*.7), QPointF(size*.74,size*.3))
        w.setPen(_base_pen(ICON,1.5))
        w.drawLine(QPointF(size*.3,size*.38), QPointF(size*.7,size*.38))
    elif key == "rotate":
        w.drawArc(QRectF(size*.2,size*.24,size*.6,size*.55), 30*16, 280*16)
        w.setBrush(QBrush(ICON)); w.setPen(Qt.NoPen)
        w.drawPolygon(QPolygonF([QPointF(size*.24,size*.2),QPointF(size*.5,size*.16),QPointF(size*.36,size*.4)]))
    elif key == "eye":
        w.drawEllipse(QRectF(size*.18,size*.34,size*.64,size*.34))
        w.drawEllipse(QPointF(size*.5,size*.5), size*.08, size*.08)
    elif key == "export":
        w.drawRoundedRect(R, 3, 3)
        w.drawLine(QPointF(size*.5,size*.3), QPointF(size*.5,size*.66))
        w.drawLine(QPointF(size*.36,size*.52), QPointF(size*.5,size*.66))
        w.drawLine(QPointF(size*.64,size*.52), QPointF(size*.5,size*.66))
    elif key == "import":
        w.drawRoundedRect(R, 3, 3)
        w.drawLine(QPointF(size*.5,size*.66), QPointF(size*.5,size*.3))
        w.drawLine(QPointF(size*.36,size*.44), QPointF(size*.5,size*.3))
        w.drawLine(QPointF(size*.64,size*.44), QPointF(size*.5,size*.3))
    elif key == "list":
        for i, y in enumerate((.3, .5, .7)):
            w.drawEllipse(QPointF(size*.26, size*y), size*.04, size*.04)
            w.drawLine(QPointF(size*.38,size*y), QPointF(size*.8,size*y))
    elif key == "open":
        w.drawPolygon(QPolygonF([QPointF(size*.16,size*.28),QPointF(size*.42,size*.28),
                                 QPointF(size*.5,size*.38),QPointF(size*.84,size*.38),
                                 QPointF(size*.84,size*.72),QPointF(size*.16,size*.72)]))
    elif key == "save":
        w.drawRoundedRect(R, 3, 3)
        w.drawLine(QPointF(size*.35,size*.2), QPointF(size*.35,size*.44))
        w.drawRect(QRectF(size*.32,size*.52,size*.36,size*.26))
    elif key == "undo":
        w.drawArc(QRectF(size*.22,size*.3,size*.56,size*.5), 0, 270*16)
        w.setBrush(QBrush(ICON)); w.setPen(Qt.NoPen)
        w.drawPolygon(QPolygonF([QPointF(size*.2,size*.24),QPointF(size*.48,size*.2),QPointF(size*.32,size*.44)]))
    elif key == "redo":
        w.drawArc(QRectF(size*.22,size*.3,size*.56,size*.5), 90*16, 270*16)
        w.setBrush(QBrush(ICON)); w.setPen(Qt.NoPen)
        w.drawPolygon(QPolygonF([QPointF(size*.8,size*.24),QPointF(size*.52,size*.2),QPointF(size*.68,size*.44)]))
    elif key == "zoom_in" or key == "zoom_out":
        c = QPointF(size*.44,size*.44); r = size*.26
        w.drawEllipse(c, r, r)
        w.drawLine(QPointF(c.x()+r*.72,c.y()+r*.72), QPointF(size*.84,size*.84))
        w.drawLine(QPointF(c.x()-r*.5,c.y()), QPointF(c.x()+r*.5,c.y()))
        if key == "zoom_in":
            w.drawLine(QPointF(c.x(),c.y()-r*.5), QPointF(c.x(),c.y()+r*.5))
    elif key == "prev":
        w.drawLine(QPointF(size*.6,size*.24), QPointF(size*.36,size*.5),)
        w.drawLine(QPointF(size*.36,size*.5), QPointF(size*.6,size*.76))
    elif key == "next":
        w.drawLine(QPointF(size*.4,size*.24), QPointF(size*.64,size*.5))
        w.drawLine(QPointF(size*.64,size*.5), QPointF(size*.4,size*.76))
    w.end()
    return p


def app_icon() -> QIcon:
    """应用图标：圆角蓝渐变方块 + 白色文档与红笔"""
    icon = QIcon()
    for size in (16, 24, 32, 48, 64, 128, 256):
        p, w = _pm(size)
        w.setRenderHint(QPainter.Antialiasing)
        g = QLinearGradient(0, 0, size, size)
        g.setColorAt(0, QColor("#3D7BFF")); g.setColorAt(1, QColor("#1F55CF"))
        w.setBrush(QBrush(g)); w.setPen(Qt.NoPen)
        r = QRectF(size*.04, size*.04, size*.92, size*.92)
        w.drawRoundedRect(r, size*.22, size*.22)
        # 白色文档
        doc = QRectF(size*.28, size*.16, size*.44, size*.62)
        w.setBrush(QBrush(QColor("#FFFFFF"))); w.drawRoundedRect(doc, size*.05, size*.05)
        w.setPen(QPen(QColor("#9DB8E8"), max(1.0, size*.03)))
        for i, yy in enumerate((.30, .40, .50)):
            w.drawLine(QPointF(size*.34, size*yy), QPointF(size*(.66 if i == 2 else .6), size*yy))
        # 红色斜笔（编辑意象）
        w.save(); w.translate(size*.5, size*.62); w.rotate(-40)
        w.setPen(Qt.NoPen); w.setBrush(QBrush(QColor("#FA5151")))
        w.drawRoundedRect(QRectF(-size*.045, -size*.3, size*.09, size*.44), size*.03, size*.03)
        w.drawPolygon(QPolygonF([QPointF(-size*.045,size*.14),QPointF(size*.045,size*.14),QPointF(0,size*.28)]))
        w.restore()
        w.end()
        icon.addPixmap(p)
    return icon


def write_ico(path: str):
    """生成多尺寸 PNG-in-ICO（Vista+ 格式）"""
    import struct
    sizes = [(16,16),(24,24),(32,32),(48,48),(64,64),(128,128),(256,256)]
    blobs = []
    for (w_, h_) in sizes:
        pm = app_icon().pixmap(w_, h_)
        b = QByteArray_to_bytes(pm.toImage())
        blobs.append((w_, h_, b))
    with open(path, "wb") as f:
        f.write(struct.pack("<HHH", 0, 1, len(blobs)))
        off = 6 + 16 * len(blobs)
        for (w_, h_, b) in blobs:
            f.write(struct.pack("<BBBBHHII", w_ % 256, h_ % 256, 0, 0, 1, 32, len(b), off))
            off += len(b)
        for (_, _, b) in blobs:
            f.write(b)


def QByteArray_to_bytes(qimage):
    from PySide6.QtCore import QBuffer, QIODevice
    buf = QBuffer()
    buf.open(QIODevice.WriteOnly)
    qimage.save(buf, "PNG")
    return bytes(buf.data())
