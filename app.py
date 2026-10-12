#!/usr/bin/env python3
"""
pdfedit 桌面版 —— 仿 WPS PDF 文本编辑器（PySide6）
====================================================
运行:  python app.py [PDF路径]
操作:  点击文字 -> 原位编辑 -> Enter 确认 / Esc 取消
       Ctrl+Z 撤销 | Ctrl+Y 重做 | Ctrl+S 保存（单层增量写回）
       Ctrl+O 打开 | Ctrl+滚轮 缩放
依赖:  pymupdf, PySide6
"""
import os, sys
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QToolBar, QFileDialog, QLabel, QLineEdit,
    QScrollArea, QWidget, QVBoxLayout, QMessageBox, QSpinBox,
)
from PySide6.QtGui import QPixmap, QPainter, QColor, QPen, QKeySequence, QAction
from PySide6.QtCore import Qt, QRectF, QSize

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from core import EditSession
import theme
from PySide6.QtWidgets import QToolButton

APP_TITLE = "pdfedit — PDF 文本编辑器"


def _rect_of(p1, p2):
    """两点（页面坐标）→ QRectF（页面坐标，允许任意方向拖动）"""
    from PySide6.QtCore import QRectF as _RF
    return _RF(min(p1[0], p2[0]), min(p1[1], p2[1]),
               abs(p2[0] - p1[0]), abs(p2[1] - p1[1]))


class PageCanvas(QWidget):
    """当前页渲染 + 文字 span 命中/高亮 + 原位编辑框"""

    def __init__(self, main):
        super().__init__()
        self.main = main
        self.pixmap = None
        self.spans = []
        self._hover = -1
        self.editor = QLineEdit(self)
        self.editor.hide()
        self.editor.returnPressed.connect(self.commit_edit)
        self.editor.installEventFilter(self)
        self.setMouseTracking(True)
        self.setMinimumSize(200, 200)
        # 拖拽移动状态
        self._press_pos = None      # QPointF
        self._press_span = -1
        self._dragging = False
        self._drag_delta = None     # QPointF 当前拖拽位移（像素）
        self._snap_adj = None       # QPointF 吸附修正量
        self._guide_v = None        # (x, y0, y1) 竖直参考线
        self._guide_h = None        # (y, x0, x1) 水平参考线
        self.SNAP_PX = 8            # 吸附阈值（像素）
        # v1.1 工具交互状态
        self.pending_image = None   # insert_image 待放置图片路径
        self._rubber = None         # QRectF 橡皮筋（页面坐标）
        self._ink_pts = None        # 墨迹点列表（页面坐标）
        self._sel_annot = None      # 选中的批注 (page, type, rect, content)
        self._annots = []           # 当前页批注缓存
        self.setFocusPolicy(Qt.ClickFocus)

    # ---------- 渲染 ----------
    def refresh(self):
        s = self.main.session
        if s is None:
            return
        png = s.render_png(self.main.page_num, self.main.zoom,
                           annots=self.main.show_annots)
        pm = QPixmap()
        pm.loadFromData(png)
        self.pixmap = pm
        self.spans = s.spans(self.main.page_num)
        try:
            self._annots = [a for a in s.list_annots()
                            if a["page"] == self.main.page_num]
        except Exception:
            self._annots = []
        self._hover = -1
        self.editor.hide()
        self.editing_span = None
        self._sel_annot = None
        self._rubber = None
        self._ink_pts = None
        self.resize(pm.size())
        self.setMinimumSize(pm.size())
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        if self.pixmap:
            p.drawPixmap(0, 0, self.pixmap)
        if self._dragging and self._press_span >= 0:
            # 拖拽中：原位置虚线框 + 跟随鼠标的幽灵文字预览 + 对齐参考线
            from PySide6.QtGui import QFont
            from PySide6.QtCore import QPointF
            eff = self._effective_delta()
            r = self.span_rect(self._press_span)
            pen = QPen(QColor(255, 140, 0, 220), 1, Qt.DashLine)
            p.setPen(pen)
            p.drawRect(r)
            sp = self.spans[self._press_span]
            f = QFont()
            f.setFamily("SimHei" if any(ord(c) > 0x2E80 for c in sp["text"]) else "Arial")
            f.setPixelSize(max(8, int(sp["size"] * self.main.zoom)))
            p.setFont(f)
            c = sp["color"]
            col = QColor((c >> 16 & 255), (c >> 8 & 255), (c & 255)) if isinstance(c, int) else QColor("black")
            p.setPen(col)
            p.drawText(QPointF(r.x() + eff.x(), r.y() + eff.y()), sp["text"])
            # 对齐参考线（吸附命中时绘制，红实线，同 WPS/PPT）
            if self._guide_v or self._guide_h:
                p.setPen(QPen(QColor(255, 59, 48, 235), 1))
                if self._guide_v:
                    x, y0, y1 = self._guide_v
                    p.drawLine(QPointF(x, y0), QPointF(x, y1))
                if self._guide_h:
                    y, x0, x1 = self._guide_h
                    p.drawLine(QPointF(x0, y), QPointF(x1, y))
        elif self._hover >= 0 and self._hover < len(self.spans):
            p.fillRect(self.span_rect(self._hover), QColor(30, 144, 255, 60))
            p.setPen(QPen(QColor(30, 144, 255, 200), 1))
            p.drawRect(self.span_rect(self._hover))
        z = self.main.zoom
        # 选中批注高亮框
        if self._sel_annot:
            r = self._sel_annot["rect"]
            p.setPen(QPen(QColor(0, 122, 255), 2))
            p.drawRect(QRectF(r[0] * z, r[1] * z, (r[2] - r[0]) * z, (r[3] - r[1]) * z))
        # 橡皮筋预览（区域高亮/文本框/矩形/椭圆/直线/箭头）
        if self._rubber is not None:
            r = self._rubber
            p.setPen(QPen(QColor(255, 140, 0, 220), 1, Qt.DashLine))
            p.drawRect(QRectF(r.x() * z, r.y() * z, r.width() * z, r.height() * z))
            if self.main.tool in ("line", "arrow"):
                p.drawLine(QPointF(r.left() * z, r.bottom() * z),
                           QPointF(r.right() * z, r.top() * z))
        # 墨迹预览
        if self._ink_pts and len(self._ink_pts) > 1:
            p.setPen(QPen(QColor(200, 0, 0), 2))
            path_pts = [QPointF(x * z, y * z) for x, y in self._ink_pts]
            for i in range(len(path_pts) - 1):
                p.drawLine(path_pts[i], path_pts[i + 1])
        p.end()

    # ---------- span ----------
    def span_rect(self, i) -> QRectF:
        x0, y0, x1, y1 = self.spans[i]["bbox"]
        z = self.main.zoom
        return QRectF(x0 * z, y0 * z, (x1 - x0) * z, (y1 - y0) * z)

    def hit_test(self, pos):
        best, best_d = -1, 6 * self.main.zoom
        for i in range(len(self.spans)):
            r = self.span_rect(i)
            if r.contains(pos):
                return i
            dx = max(r.x() - pos.x(), 0, pos.x() - r.right())
            dy = max(r.y() - pos.y(), 0, pos.y() - r.bottom())
            d = (dx * dx + dy * dy) ** 0.5
            if d < best_d:
                best, best_d = i, d
        return best

    def keyPressEvent(self, ev):
        # Del/Backspace 删除选中的批注
        if ev.key() in (Qt.Key_Delete, Qt.Key_Backspace) and self._sel_annot:
            a = self._sel_annot
            sub = {"Text": "note", "FreeText": "freetext", "Ink": "ink",
                   "Square": "rect", "Circle": "oval", "Line": "line",
                   "Stamp": "stamp", "Highlight": "highlight",
                   "Underline": "underline", "StrikeOut": "strikeout"}.get(a["type"], a["type"])
            try:
                self.main.session.annot_delete(a["page"], sub, a["rect"], a["content"])
                self._sel_annot = None
                self.main.after_document_change()
            except Exception as e:
                QMessageBox.warning(self, "删除批注失败", str(e))
            return
        super().keyPressEvent(ev)

    def _effective_delta(self):
        """拖拽位移 + 吸附修正（未吸附时修正为零向量）"""
        from PySide6.QtCore import QPointF
        d = self._drag_delta if self._drag_delta else QPointF(0, 0)
        a = self._snap_adj if self._snap_adj else QPointF(0, 0)
        return d + a

    def _update_snap(self):
        """计算当前拖拽位置的对齐吸附：
        x 轴 —— 与其他 span 的 左缘/中线/右缘 及页面竖直中线 对齐
        y 轴 —— 与其他 span 的 顶/文字基线/底 及页面水平中线 对齐
        命中阈值内取最近者：修正位移并记录参考线范围。"""
        from PySide6.QtCore import QPointF
        self._snap_adj, self._guide_v, self._guide_h = None, None, None
        if self._press_span < 0 or self._drag_delta is None:
            return
        r = self.span_rect(self._press_span).translated(self._drag_delta)
        TH = self.SNAP_PX

        cand_x, cand_y = [], []
        for i in range(len(self.spans)):
            if i == self._press_span:
                continue
            sr = self.span_rect(i)
            base_y = self.spans[i]["origin"][1] * self.main.zoom
            cand_x += [(sr.x(), sr.top(), sr.bottom()),
                       (sr.center().x(), sr.top(), sr.bottom()),
                       (sr.right(), sr.top(), sr.bottom())]
            cand_y += [(sr.y(), sr.left(), sr.right()),
                       (base_y, sr.left(), sr.right()),
                       (sr.bottom(), sr.left(), sr.right())]
        if self.pixmap:
            W, H = self.pixmap.width(), self.pixmap.height()
            cand_x.append((W / 2, 0, H))
            cand_y.append((H / 2, 0, W))

        adj_x = adj_y = 0.0
        best = None
        for my_x in (r.x(), r.center().x(), r.right()):
            for cx, cy0, cy1 in cand_x:
                d = cx - my_x
                if abs(d) <= TH and (best is None or abs(d) < abs(best[0])):
                    best = (d, cx, cy0, cy1)
        if best:
            adj_x = best[0]
            self._guide_v = (best[1], min(best[2], r.top()) - 24,
                             max(best[3], r.bottom()) + 24)
        best = None
        for my_y in (r.y(), r.center().y(), r.bottom()):
            for cy, cx0, cx1 in cand_y:
                d = cy - my_y
                if abs(d) <= TH and (best is None or abs(d) < abs(best[0])):
                    best = (d, cy, cx0, cx1)
        if best:
            adj_y = best[0]
            self._guide_h = (best[1], min(best[2], r.left()) - 24,
                             max(best[3], r.right()) + 24)
        if adj_x or adj_y:
            self._snap_adj = QPointF(adj_x, adj_y)

    def mouseMoveEvent(self, ev):
        z = self.main.zoom
        pos_pg = (ev.position().x() / z, ev.position().y() / z)
        # 拖拽类工具的橡皮筋/墨迹更新
        if self._rubber is not None and (ev.buttons() & Qt.LeftButton):
            self._rubber = _rect_of(self._rubber_start, pos_pg)
            self.update()
            return
        if self._ink_pts is not None and (ev.buttons() & Qt.LeftButton):
            self._ink_pts.append(pos_pg)
            self.update()
            return
        # 按住左键且命中了 span：超过阈值进入拖拽模式
        if (ev.buttons() & Qt.LeftButton) and self._press_span >= 0 and self._press_pos is not None:
            delta = ev.position() - self._press_pos
            if self._dragging or (delta.x() ** 2 + delta.y() ** 2) ** 0.5 > 4:
                self._dragging = True
                self._drag_delta = delta
                self._update_snap()
                self.setCursor(Qt.SizeAllCursor)
                self.update()
                return
        i = self.hit_test(ev.position())
        if i != self._hover:
            self._hover = i
            self.setCursor(Qt.IBeamCursor if i >= 0 else Qt.ArrowCursor)
            self.update()

    def leaveEvent(self, _):
        self._hover = -1
        self.update()

    def mousePressEvent(self, ev):
        if ev.button() != Qt.LeftButton:
            return
        z = self.main.zoom
        pos_pg = (ev.position().x() / z, ev.position().y() / z)
        tool = self.main.tool

        # ---- 点击类工具 ----
        if tool in ("highlight", "underline", "strikeout"):
            i = self.hit_test(ev.position())
            if i >= 0:
                b = self.spans[i]["bbox"]
                quad = [b[0], b[1], b[2], b[1], b[2], b[3], b[0], b[3]]
                try:
                    self.main.session.annot_add(self.main.page_num, tool, quads=[quad])
                    self.main.after_document_change()
                except Exception as e:
                    QMessageBox.warning(self, "批注失败", str(e))
            return
        if tool == "note":
            from PySide6.QtWidgets import QInputDialog
            text, ok = QInputDialog.getMultiLineText(self, "便签批注", "批注内容：")
            if ok and text:
                self.main.session.annot_add(self.main.page_num, "note",
                                            point=pos_pg, content=text)
                self.main.after_document_change()
            return
        if tool == "stamp":
            from PySide6.QtWidgets import QInputDialog
            names = ["Approved", "NotApproved", "Draft", "Final", "Confidential",
                     "Experimental", "Expired", "Sold", "TopSecret"]
            name, ok = QInputDialog.getItem(self, "图章", "选择图章：", names, 0, False)
            if ok:
                w = h = 80
                rect = [pos_pg[0], pos_pg[1], pos_pg[0] + w, pos_pg[1] + h]
                self.main.session.annot_add(self.main.page_num, "stamp",
                                            rect=rect, stamp=name)
                self.main.after_document_change()
            return
        if tool == "insert_text":
            self._insert_point = pos_pg
            ed = self.editor
            ed.setGeometry(int(ev.position().x()) - 4, int(ev.position().y()) - 16, 220, 24)
            ed.setText("")
            self.editing_span = None        # None = 新插入模式
            ed.show()
            ed.setFocus()
            return
        if tool == "insert_image" and self.pending_image:
            try:
                with open(self.pending_image, "rb") as f:
                    img_bytes = f.read()
                w_pt = 180
                h_pt = 135
                try:
                    import fitz as _fz
                    pm = _fz.Pixmap(img_bytes)
                    h_pt = w_pt * pm.height / pm.width
                except Exception:
                    pass
                rect = [pos_pg[0], pos_pg[1], pos_pg[0] + w_pt, pos_pg[1] + h_pt]
                self.main.session.insert_image(self.main.page_num, rect, img_bytes)
                self.main.after_document_change()
            except Exception as e:
                QMessageBox.warning(self, "插入图片失败", str(e))
            return

        # ---- 拖拽类工具：按下即开始橡皮筋/墨迹 ----
        if tool in ("area_hl", "textbox", "rect", "oval", "line", "arrow"):
            self._rubber = _rect_of(pos_pg, pos_pg)
            self._rubber_start = pos_pg
            return
        if tool == "ink":
            self._ink_pts = [pos_pg]
            return

        # ---- select 工具：原有行为 + 批注命中 ----
        i = self.hit_test(ev.position())
        if i < 0:
            hit = self._annot_at(pos_pg)
            self._sel_annot = hit
            self.update()
        self._press_pos = ev.position()
        self._press_span = i
        self._dragging = False
        self._drag_delta = None

    def _annot_at(self, pos_pg):
        x, y = pos_pg
        for a in self._annots:
            r = a["rect"]
            if r[0] <= x <= r[2] and r[1] <= y <= r[3]:
                return a
        return None

    def mouseReleaseEvent(self, ev):
        if ev.button() != Qt.LeftButton:
            return
        z = self.main.zoom
        pos_pg = (ev.position().x() / z, ev.position().y() / z)
        tool = self.main.tool
        # ---- 拖拽类工具提交 ----
        if self._rubber is not None:
            r = self._rubber
            rect = [r.x(), r.y(), r.x() + r.width(), r.y() + r.height()]
            self._rubber = None
            self.update()
            if r.width() < 3 or r.height() < 3:
                return    # 过小视为误触
            try:
                if tool == "area_hl":
                    quad = [rect[0], rect[1], rect[2], rect[1],
                            rect[2], rect[3], rect[0], rect[3]]
                    self.main.session.annot_add(self.main.page_num, "highlight", quads=[quad])
                elif tool == "textbox":
                    from PySide6.QtWidgets import QInputDialog
                    text, ok = QInputDialog.getMultiLineText(self, "文本框批注", "内容：")
                    if ok and text:
                        self.main.session.annot_add(self.main.page_num, "freetext",
                                                    rect=rect, content=text, size=12)
                    else:
                        return
                elif tool == "rect":
                    self.main.session.annot_add(self.main.page_num, "rect", rect=rect)
                elif tool == "oval":
                    self.main.session.annot_add(self.main.page_num, "oval", rect=rect)
                elif tool in ("line", "arrow"):
                    self.main.session.annot_add(self.main.page_num, tool,
                                                p1=[self._rubber_start[0], self._rubber_start[1]],
                                                p2=[pos_pg[0], pos_pg[1]])
                self.main.after_document_change()
            except Exception as e:
                QMessageBox.warning(self, "批注失败", str(e))
            return
        if self._ink_pts is not None:
            pts, self._ink_pts = self._ink_pts, None
            self.update()
            if len(pts) >= 2:
                try:
                    self.main.session.annot_add(self.main.page_num, "ink", strokes=[pts])
                    self.main.after_document_change()
                except Exception as e:
                    QMessageBox.warning(self, "批注失败", str(e))
            return

        # ---- select 工具：拖拽移动 / 点击编辑 / 批注已选中 ----
        i, dragged = self._press_span, self._dragging
        self._press_span, self._press_pos = -1, None
        self._dragging = False
        self.setCursor(Qt.ArrowCursor)
        if dragged and i >= 0 and self._drag_delta is not None:
            eff = self._effective_delta()      # 含吸附修正
            self._drag_delta = None
            self._snap_adj = None
            dx = eff.x() / self.main.zoom
            dy = eff.y() / self.main.zoom
            try:
                self.main.session.move_span(self.main.page_num, i, dx, dy)
            except Exception as e:
                QMessageBox.warning(self, "移动失败", str(e))
            self.main.after_document_change()
            return
        self._drag_delta = None
        self._snap_adj = None
        self._guide_v = self._guide_h = None
        self.update()
        if i >= 0:
            self.start_edit(i)

    def wheelEvent(self, ev):
        if ev.modifiers() & Qt.ControlModifier:
            dy = ev.angleDelta().y()
            if dy > 0:
                self.main.zoom_by(+0.25)
            elif dy < 0:
                self.main.zoom_by(-0.25)
        else:
            ev.ignore()

    # ---------- 原位编辑 ----------
    editing_span = None

    def start_edit(self, i):
        self.editing_span = i
        r = self.span_rect(i)
        sp = self.spans[i]
        ed = self.editor
        ed.setGeometry(int(r.x()) - 2, int(r.y()) - 2,
                       max(80, int(r.width()) + 40), max(18, int(r.height()) + 4))
        f = ed.font()
        f.setPointSizeF(max(7.5, sp["size"] * self.main.zoom * 0.75))
        ed.setFont(f)
        ed.setText(sp["text"])
        ed.show()
        ed.setFocus()
        ed.selectAll()

    def eventFilter(self, obj, ev):
        if obj is self.editor and ev.type() == ev.Type.KeyPress:
            if ev.key() == Qt.Key_Escape:
                self.editor.hide()
                self.editing_span = None
                return True
        return super().eventFilter(obj, ev)

    def commit_edit(self):
        # 插入文字模式（insert_text 工具）
        if self.editing_span is None and getattr(self, "_insert_point", None):
            text = self.editor.text()
            pt = self._insert_point
            self._insert_point = None
            self.editor.hide()
            if text:
                try:
                    self.main.session.insert_text(self.main.page_num, pt, text, size=12)
                except Exception as e:
                    QMessageBox.warning(self, "插入失败", str(e))
                self.main.after_document_change()
            return
        if self.editing_span is None:
            return
        idx = self.editing_span
        new_text = self.editor.text()
        old = self.spans[idx]["text"] if idx < len(self.spans) else ""
        self.editor.hide()
        self.editing_span = None
        if not new_text or new_text == old:
            return
        try:
            self.main.session.edit_span(self.main.page_num, idx, new_text)
        except Exception as e:
            QMessageBox.warning(self, "编辑失败", str(e))
        self.main.after_document_change()


class MainWindow(QMainWindow):
    def __init__(self, doc_path=None):
        super().__init__()
        self.setWindowTitle(APP_TITLE)
        self.resize(1100, 850)
        self.session: EditSession | None = None
        self.zoom = 1.5
        self.page_num = 0
        self.tool = "select"
        self.show_annots = True

        self.canvas = PageCanvas(self)
        self.scroll = QScrollArea()
        self.scroll.setWidget(self.canvas)
        self.scroll.setAlignment(Qt.AlignCenter)
        self.scroll.setViewportMargins(28, 24, 28, 24)
        self.scroll.setFrameShape(QScrollArea.NoFrame)
        self.setCentralWidget(self.scroll)
        # 页面阴影（贴近 WPS 阅读视图的页面浮层效果）
        from PySide6.QtWidgets import QGraphicsDropShadowEffect
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(30)
        shadow.setOffset(0, 4)
        shadow.setColor(QColor(30, 40, 60, 70))
        self.canvas.setGraphicsEffect(shadow)

        self._build_toolbar()
        self.statusBar().showMessage("就绪 — Ctrl+O 打开 PDF，点击文字开始编辑")

        if doc_path and os.path.exists(doc_path):
            self.load_doc(doc_path)

    # ---------- 工具栏（WPS 风格双行头部） ----------
    def _build_toolbar(self):
        from PySide6.QtWidgets import QWidget, QHBoxLayout, QVBoxLayout, QLabel
        from PySide6.QtGui import QActionGroup
        header = QWidget(objectName="header")
        h = QHBoxLayout(header)
        h.setContentsMargins(10, 6, 10, 6)
        h.setSpacing(4)
        row1 = QHBoxLayout()
        row1.setSpacing(4)

        self._acts = {}

        self._mk_row_btn(row1, "open", "打开", "打开 PDF 文件 (Ctrl+O)", self.open_doc, "Ctrl+O", icon="open")
        self.a_undo = self._mk_row_btn(row1, "undo", "↶ 撤销", "撤销 (Ctrl+Z)", self.do_undo, "Ctrl+Z", icon="undo")
        self.a_redo = self._mk_row_btn(row1, "redo", "↷ 重做", "重做 (Ctrl+Y)", self.do_redo, "Ctrl+Y", icon="redo")
        self.a_save = self._mk_row_btn(row1, "save", "保存", "单层增量保存 (Ctrl+S)", self.do_save, "Ctrl+S", accent=True)
        row1.addSpacing(12)
        self._mk_row_btn(row1, "zoom_out", "－", "缩小", lambda: self.zoom_by(-0.25), "-", icon="zoom_out")
        self.lbl_zoom = QLabel(" 150% ")
        self.lbl_zoom.setStyleSheet("color:#9AA1AC; font-size:12px;")
        row1.addWidget(self.lbl_zoom)
        self._mk_row_btn(row1, "zoom_in", "＋", "放大", lambda: self.zoom_by(+0.25), "=", icon="zoom_in")
        row1.addSpacing(12)
        self.a_prev = self._mk_row_btn(row1, "prev", "◀", "上一页 (PgUp)", lambda: self.page_by(-1), "PgUp", icon="prev")
        self.spin_page = QSpinBox()
        self.spin_page.setMinimum(1)
        self.spin_page.valueChanged.connect(self.goto_page)
        row1.addWidget(self.spin_page)
        self.a_next = self._mk_row_btn(row1, "next", "▶", "下一页 (PgDn)", lambda: self.page_by(+1), "PgDn", icon="next")
        h.addLayout(row1)
        h.addStretch(1)
        lbl_app = QLabel("pdfedit")
        lbl_app.setStyleSheet(f"color:{theme.BRAND.name()}; font-size:15px; font-weight:700; padding-right:4px;")
        h.addWidget(lbl_app)
        v = QVBoxLayout()
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(2)
        v.addWidget(header)
        row2 = QHBoxLayout()
        row2.setContentsMargins(10, 0, 10, 8)
        row2.setSpacing(2)

        self.tool_group = QActionGroup(self)
        self.tool_group.setExclusive(True)
        self.tools = {}

        def group_label(t):
            l = QLabel(t, objectName="groupLabel")
            row2.addWidget(l)

        def tool(key, text, tip, icon):
            a = QAction(text, self)
            a.setStatusTip(tip)
            a.setCheckable(True)
            a.setIcon(theme.tool_icon(icon))
            a.triggered.connect(lambda: self.set_tool(key))
            self.tool_group.addAction(a)
            b = QToolButton()
            b.setDefaultAction(a)
            b.setToolButtonStyle(Qt.ToolButtonIconOnly)
            b.setIconSize(QSize(22, 22))
            row2.addWidget(b)
            self.tools[key] = a
            return a

        group_label("编辑")
        tool("select", "选择/编辑", "点击文字编辑，拖动移动；点击批注选中（Del 删除）", "select")
        tool("insert_text", "插入文字", "点击页面空白处新建文字", "insert_text")
        tool("insert_image", "插入图片", "选择图片后点击页面放置", "insert_image")
        group_label("批注")
        tool("highlight", "高亮", "点击文字行高亮", "highlight")
        tool("area_hl", "区域高亮", "拖动矩形区域高亮", "area_hl")
        tool("underline", "下划线", "点击文字行加下划线", "underline")
        tool("strikeout", "删除线", "点击文字行加删除线", "strikeout")
        tool("note", "便签", "点击位置插入文字便签", "note")
        tool("textbox", "文本框", "拖动矩形区域插入文本框批注", "textbox")
        tool("ink", "随意画", "按住拖动自由手绘", "ink")
        tool("rect", "矩形", "拖动绘制矩形批注", "rect")
        tool("oval", "椭圆", "拖动绘制椭圆批注", "oval")
        tool("line", "直线", "拖动绘制直线", "line")
        tool("arrow", "箭头", "拖动绘制箭头", "arrow")
        tool("stamp", "图章", "点击位置加盖图章", "stamp")
        group_label("文档")
        self._mk_row_btn(row2, "watermark", "水印", "全文档文字水印", self.act_watermark, None, text_mode=True)
        self._mk_row_btn(row2, "rotate", "⟳ 旋转页", "当前页旋转90°", self.act_rotate, None, text_mode=True)
        self.a_hide = self._mk_row_btn(row2, "hide", "隐藏批注", "切换批注显示/隐藏 (F9)", self.act_toggle_annots, "F9", checkable=True, text_mode=True)
        self._mk_row_btn(row2, "export_annots", "导出批注", "批注导出为 JSON", self.act_export_annots, None, text_mode=True)
        self._mk_row_btn(row2, "import_annots", "导入批注", "从 JSON 导入批注", self.act_import_annots, None, text_mode=True)
        self._mk_row_btn(row2, "list_annots", "批注列表", "查看全部批注", self.act_list_annots, None, text_mode=True)
        self.tools["select"].setChecked(True)
        row2.addStretch(1)
        row2w = QWidget()
        row2w.setLayout(row2)
        v.addWidget(row2w)
        wrap = QWidget()
        wrap.setLayout(v)
        self.menuBar().hide()
        self.setMenuWidget(wrap)      # 头部占据菜单栏位置，始终置顶

        for a in (self.a_undo, self.a_redo, self.a_save, self.a_prev, self.a_next):
            a.setEnabled(False)
        self.spin_page.setEnabled(False)

    def _mk_row_btn(self, row, key, text, tip, slot, shortcut=None,
                    checkable=False, accent=False, icon=None, text_mode=False):
        a = QAction(text, self)
        a.setStatusTip(tip)
        a.setCheckable(checkable)
        if shortcut:
            a.setShortcut(QKeySequence(shortcut))
        if slot is not None:
            a.triggered.connect(slot)
        b = QToolButton()
        b.setDefaultAction(a)
        b.setIconSize(QSize(18, 18))
        if icon is not None:
            a.setIcon(theme.tool_icon(icon))
        if accent:
            b.setObjectName("accent")
            b.setToolButtonStyle(Qt.ToolButtonTextOnly)
        elif text_mode:
            b.setToolButtonStyle(Qt.ToolButtonTextOnly)
        elif a.icon().isNull():
            b.setToolButtonStyle(Qt.ToolButtonTextOnly)
        else:
            b.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        row.addWidget(b)
        self._acts[key] = a
        return a

    # ---------- 工具状态 ----------
    def set_tool(self, key):
        self.canvas.pending_image = self.canvas.pending_image if key != "insert_image" else self.canvas.pending_image
        if key == "insert_image" and not self.canvas.pending_image:
            path, _ = QFileDialog.getOpenFileName(
                self, "选择图片", "", "图片 (*.png *.jpg *.jpeg *.bmp)")
            if not path:
                self.tools["select"].setChecked(True)
                return
            self.canvas.pending_image = path
        if key != "insert_image":
            self.canvas.pending_image = None
        self.tool = key
        cur = {"select": Qt.ArrowCursor, "ink": Qt.CrossCursor,
               "textbox": Qt.IBeamCursor}.get(key, Qt.CrossCursor)
        self.canvas.setCursor(cur)
        self.statusBar().showMessage(f"当前工具: {key} — {self.tools[key].statusTip()}", 4000)

    # ---------- 工具动作 ----------
    def act_watermark(self):
        if not self.session:
            return
        from PySide6.QtWidgets import QInputDialog
        text, ok = QInputDialog.getText(self, "水印", "水印文字：", text="DRAFT")
        if ok and text:
            self.session.add_watermark(text)
            self.after_document_change()

    def act_rotate(self):
        if self.session:
            self.session.rotate_page(self.page_num, 90)
            self.after_document_change()

    def act_toggle_annots(self, checked):
        self.show_annots = not checked
        self.canvas.refresh()

    def act_export_annots(self):
        if not self.session:
            return
        import json
        path, _ = QFileDialog.getSaveFileName(self, "导出批注", "annotations.json", "JSON (*.json)")
        if not path:
            return
        data = self.session.list_annots()
        json.dump(data, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        self.statusBar().showMessage(f"已导出 {len(data)} 条批注 -> {path}", 8000)

    def act_import_annots(self):
        if not self.session:
            return
        import json
        path, _ = QFileDialog.getOpenFileName(self, "导入批注", "", "JSON (*.json)")
        if not path:
            return
        data = json.load(open(path, encoding="utf-8"))
        n = 0
        for a in data:
            sub = {"Text": "note", "FreeText": "freetext", "Ink": "ink",
                   "Square": "rect", "Circle": "oval", "Line": "line",
                   "Stamp": "stamp", "Highlight": "highlight",
                   "Underline": "underline", "StrikeOut": "strikeout"}.get(a["type"])
            if not sub:
                continue
            params = {k: v for k, v in a.items() if k not in ("type",)}
            if sub in ("highlight", "underline", "strikeout"):
                r = a["rect"]
                params["quads"] = [[r[0], r[1], r[2], r[1], r[2], r[3], r[0], r[3]]]
            try:
                self.session.annot_add(a["page"], sub, **params)
                n += 1
            except Exception:
                pass
        self.statusBar().showMessage(f"已导入 {n} 条批注", 8000)
        self.after_document_change()

    def act_list_annots(self):
        if not self.session:
            return
        from PySide6.QtWidgets import QDialog, QVBoxLayout, QListWidget, QPushButton, QHBoxLayout
        dlg = QDialog(self)
        dlg.setWindowTitle("批注列表")
        dlg.resize(500, 400)
        lst = QListWidget()
        annots = self.session.list_annots()
        for a in annots:
            label = f"第{a['page']+1}页 [{a['type']}] {a['content'][:40] or '(无文字)'}"
            lst.addItem(label)
        btn_del = QPushButton("删除选中")
        btn_close = QPushButton("关闭")

        def del_selected():
            row = lst.currentRow()
            if 0 <= row < len(annots):
                a = annots[row]
                try:
                    self.session.annot_delete(a["page"], a["type"].lower().replace(" ", ""), a["rect"], a["content"])
                except Exception:
                    # 类型名映射失败时退化按矩形删除
                    self.session.annot_delete(a["page"], a["type"], a["rect"], a["content"])
                self.after_document_change()
                dlg.accept()

        btn_del.clicked.connect(del_selected)
        btn_close.clicked.connect(dlg.reject)
        lay = QVBoxLayout(dlg)
        lay.addWidget(lst)
        h = QHBoxLayout()
        h.addWidget(btn_del)
        h.addWidget(btn_close)
        lay.addLayout(h)
        dlg.exec()

    # ---------- 文档 ----------
    def load_doc(self, path):
        try:
            if self.session:
                self.session.close()
            self.session = EditSession(path)
        except Exception as e:
            QMessageBox.critical(self, "打开失败", f"{path}\n{e}")
            return
        self.page_num = 0
        self.spin_page.setEnabled(True)
        self.spin_page.setRange(1, self.session.view().page_count)
        self.spin_page.setValue(1)
        self.canvas.refresh()
        self.after_document_change()

    def open_doc(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "打开 PDF", "", "PDF 文档 (*.pdf)")
        if path:
            self.load_doc(path)

    def after_document_change(self):
        s = self.session
        if s is None:
            return
        self.a_undo.setEnabled(s.can_undo)
        self.a_redo.setEnabled(s.can_redo)
        self.a_save.setEnabled(s.ptr > 0)
        self.spin_page.setRange(1, s.view().page_count)
        self.lbl_zoom.setText(f" {int(self.zoom * 100)}% ")
        self.statusBar().showMessage(
            f"{s.path}  |  第 {self.page_num + 1}/{s.view().page_count} 页  |  "
            f"{s.ptr} 个未保存操作")
        self.canvas.refresh()

    def do_undo(self):
        if self.session and self.session.undo():
            self._clamp_page()
            self.after_document_change()

    def do_redo(self):
        if self.session and self.session.redo():
            self._clamp_page()
            self.after_document_change()

    def do_save(self):
        if not self.session or self.session.ptr == 0:
            return
        try:
            r = self.session.save_incremental()
        except Exception as e:
            QMessageBox.critical(self, "保存失败", str(e))
            return
        self.statusBar().showMessage(
            f"已保存: {r['bytes']:,} 字节 | %%EOF={r['eof']} | "
            f"原字节不动={r['base_prefix_intact']}", 10000)
        self.after_document_change()

    # ---------- 视图 ----------
    def zoom_by(self, dz):
        self.zoom = max(0.5, min(4.0, self.zoom + dz))
        self.canvas.refresh()
        self.lbl_zoom.setText(f" {int(self.zoom * 100)}% ")

    def page_by(self, d):
        self.goto_page_value(self.page_num + d)

    def goto_page_value(self, n):
        if self.session is None:
            return
        n = max(0, min(self.session.view().page_count - 1, n))
        self.page_num = n
        self.spin_page.setValue(n + 1)  # 触发 goto_page
        self.canvas.refresh()
        self.after_document_change()

    def goto_page(self, v):
        if self.session is None:
            return
        self.page_num = v - 1
        self.canvas.refresh()
        self.after_document_change()

    def _clamp_page(self):
        if self.session:
            self.page_num = min(self.page_num, self.session.view().page_count - 1)

    # ---------- 关闭保护 ----------
    def closeEvent(self, ev):
        if self.session and self.session.ptr > 0:
            ret = QMessageBox.question(
                self, "未保存的修改",
                f"还有 {self.session.ptr} 个未保存的修改。保存后退出？",
                QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel)
            if ret == QMessageBox.Save:
                try:
                    self.session.save_incremental()
                except Exception as e:
                    QMessageBox.critical(self, "保存失败", str(e))
                    ev.ignore()
                    return
            elif ret == QMessageBox.Cancel:
                ev.ignore()
                return
        if self.session:
            self.session.close()
        ev.accept()


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("pdfedit")
    app.setStyle("Fusion")
    app.setStyleSheet(theme.QSS)
    app.setWindowIcon(theme.app_icon())
    path = sys.argv[1] if len(sys.argv) > 1 else None
    win = MainWindow(path)
    win.setWindowIcon(theme.app_icon())
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
