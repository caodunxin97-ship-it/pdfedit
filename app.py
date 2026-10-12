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

APP_TITLE = "pdfedit — PDF 文本编辑器"


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

    # ---------- 渲染 ----------
    def refresh(self):
        s = self.main.session
        if s is None:
            return
        png = s.render_png(self.main.page_num, self.main.zoom)
        pm = QPixmap()
        pm.loadFromData(png)
        self.pixmap = pm
        self.spans = s.spans(self.main.page_num)
        self._hover = -1
        self.editor.hide()
        self.editing_span = None
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
        i = self.hit_test(ev.position())
        self._press_pos = ev.position()
        self._press_span = i
        self._dragging = False
        self._drag_delta = None

    def mouseReleaseEvent(self, ev):
        if ev.button() != Qt.LeftButton:
            return
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

        self.canvas = PageCanvas(self)
        self.scroll = QScrollArea()
        self.scroll.setWidget(self.canvas)
        self.scroll.setAlignment(Qt.AlignCenter)
        self.setCentralWidget(self.scroll)

        self._build_toolbar()
        self.statusBar().showMessage("就绪 — Ctrl+O 打开 PDF，点击文字开始编辑")

        if doc_path and os.path.exists(doc_path):
            self.load_doc(doc_path)

    # ---------- 工具栏 ----------
    def _build_toolbar(self):
        tb = QToolBar("主工具栏")
        tb.setMovable(False)
        self.addToolBar(tb)

        def act(text, tip, shortcut, slot, checkable=False):
            a = QAction(text, self)
            a.setStatusTip(tip)
            if shortcut:
                a.setShortcut(QKeySequence(shortcut))
            a.triggered.connect(slot)
            tb.addAction(a)
            return a

        act("打开", "打开 PDF 文件 (Ctrl+O)", "Ctrl+O", self.open_doc)
        self.a_undo = act("↶ 撤销", "撤销 (Ctrl+Z)", "Ctrl+Z", self.do_undo)
        self.a_redo = act("↷ 重做", "重做 (Ctrl+Y)", "Ctrl+Y", self.do_redo)
        self.a_save = act("保存（增量）", "单层增量保存 (Ctrl+S)", "Ctrl+S", self.do_save)
        tb.addSeparator()
        act("－", "缩小", "-", lambda: self.zoom_by(-0.25))
        self.lbl_zoom = QLabel(" 150% ")
        tb.addWidget(self.lbl_zoom)
        act("＋", "放大", "=", lambda: self.zoom_by(+0.25))
        tb.addSeparator()
        self.a_prev = act("◀ 上一页", "上一页 (PgUp)", "PgUp", lambda: self.page_by(-1))
        self.spin_page = QSpinBox()
        self.spin_page.setMinimum(1)
        self.spin_page.valueChanged.connect(self.goto_page)
        tb.addWidget(self.spin_page)
        self.a_next = act("下一页 ▶", "下一页 (PgDn)", "PgDn", lambda: self.page_by(+1))
        for a in (self.a_undo, self.a_redo, self.a_save, self.a_prev, self.a_next):
            a.setEnabled(False)
        self.spin_page.setEnabled(False)

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
    path = sys.argv[1] if len(sys.argv) > 1 else None
    win = MainWindow(path)
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
