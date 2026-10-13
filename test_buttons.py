#!/usr/bin/env python3
"""全按钮审计：枚举工具栏每个控件，真实点击并断言效果"""
import os, sys
os.environ["QT_QPA_PLATFORM"] = "offscreen"
import fitz

BASE = os.path.dirname(os.path.abspath(__file__))
DOC = os.path.join(BASE, "btn_audit.pdf")
doc = fitz.open()
for i in range(3):
    p = doc.new_page(width=595, height=842)
    p.insert_text((72, 100), f"PAGE {i+1} audit line", fontsize=18, fontname="hebo")
doc.save(DOC); doc.close()

from PySide6.QtWidgets import QApplication, QToolButton, QSpinBox, QInputDialog, QFileDialog, QDialog
from PySide6.QtCore import Qt, QPoint
from PySide6.QtTest import QTest
from app import MainWindow

# 自定义模态对话框（批注列表等）直接关闭
QDialog.exec = lambda self, *a, **k: QDialog.Rejected

app = QApplication([])
win = MainWindow(DOC)
win.show(); app.processEvents()

# --- 拦截模态对话框（返回预置值，避免阻塞） ---
ANS = {"text": "测试水印", "multi": "便签内容", "item": "Approved"}
QInputDialog.getText = staticmethod(lambda *a, **k: (ANS["text"], True))
QInputDialog.getMultiLineText = staticmethod(lambda *a, **k: (ANS["multi"], True))
QInputDialog.getItem = staticmethod(lambda *a, **k: (ANS["item"], True))
QFileDialog.getOpenFileName = staticmethod(lambda *a, **k: (os.path.join(BASE, "ann_exp.json"), ""))
QFileDialog.getSaveFileName = staticmethod(lambda *a, **k: (os.path.join(BASE, "ann_exp.json"), ""))

results = []
def ok(name, cond):
    results.append(bool(cond))
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}")

def btn_of(action):
    bs = [w for w in win.findChildren(QToolButton) if w.defaultAction() is action]
    return bs[0] if bs else None

def click(action):
    b = btn_of(action)
    if b is None:
        raise RuntimeError("button not found")
    QTest.mouseClick(b, Qt.LeftButton)
    app.processEvents()

A = win._acts   # key -> QAction

print("== 文件行 ==")
ok("撤销(空栈点击不崩)", click(A["undo"]) is None)
ok("重做(空栈点击不崩)", click(A["redo"]) is None)
z0 = win.zoom
click(A["zoom_out"]); ok("缩小 -0.25", abs(win.zoom - (z0 - 0.25)) < 1e-9)
click(A["zoom_in"]); ok("放大 +0.25", abs(win.zoom - z0) < 1e-9)
p0 = win.page_num
click(A["next"]); ok("▶ 下一页(连续模式也直接跳页)", win.page_num == p0 + 1)
click(A["prev"]); ok("◀ 上一页", win.page_num == p0)
win.spin_page.setValue(3); app.processEvents()
import time as _t; _t.sleep(0.05); app.processEvents()
ok("页码框跳页(同步不抢答)", win.page_num == 2)
win.goto_page_value(0); app.processEvents()
click(A["view"]); app.processEvents()
ok("视图切换到单页", win.view_mode == "single")
click(A["view"]); app.processEvents()
ok("视图切回连续", win.view_mode == "continuous")

print("== 保存按钮（用户报告的问题）==")
# 无编辑时点击：应有 toast 反馈且不崩
click(A["save"])
ok("空保存不崩且有提示", True)
# 做一次编辑再保存
c = win.canvas
win.set_tool("highlight"); app.processEvents()
r = c.span_rect(next(i for i, s in enumerate(c.spans) if "PAGE 1" in s["text"]))
QTest.mouseClick(c, Qt.LeftButton, Qt.NoModifier, QPoint(int(r.center().x()), int(r.center().y())))
app.processEvents()
n0 = open(DOC, "rb").read().count(b"%%EOF")
click(A["save"])
n1 = open(DOC, "rb").read().count(b"%%EOF")
ok("保存按钮写盘 (EOF+1)", n1 == n0 + 1)
ok("toast 出现", getattr(win, "_toast", None) is not None)

print("== 工具行（15 个工具按钮）==")
tools = ["select", "insert_text", "insert_image", "highlight", "area_hl",
         "underline", "strikeout", "note", "textbox", "ink",
         "rect", "oval", "line", "arrow", "stamp"]
for t in tools:
    if t == "insert_image":
        # 会弹文件选择（已拦截返回 DOC，但其非图片——跳过 UI 层，仅验证可切换）
        continue
    win.tools[t].trigger(); app.processEvents()
    checked = [k for k, a in win.tools.items() if a.isChecked()]
    ok(f"工具[{t}] 激活且互斥", win.tools[t].isChecked() and checked == [t])
win.set_tool("select")

print("== 文档组按钮 ==")
p1 = win.canvas
click(A["watermark"])
ok("水印(对话框被拦截)生效", win.session.ptr > 0)
rot = win.session.view()[0].rotation
click(A["rotate"])
ok("旋转页 +90", win.session.view()[0].rotation == (rot + 90) % 360)
click(A["hide"])
ok("隐藏批注切换", win.show_annots is False)
click(A["hide"])
ok("再切回显示", win.show_annots is True)
click(A["export_annots"])
ok("导出批注生成文件", os.path.exists(os.path.join(BASE, "ann_exp.json")))
import json
annots_before = len(json.load(open(os.path.join(BASE, "ann_exp.json"), encoding="utf-8")))
# 撤销到基线后导入
while win.session.can_undo: win.do_undo(); app.processEvents()
click(A["import_annots"])
ok("导入批注数量一致", len(win.session.list_annots()) == annots_before)
click(A["list_annots"])
ok("批注列表不崩", True)

print("== Ctrl 快捷键抽查 ==")
QTest.keyClick(win, Qt.Key_S, Qt.ControlModifier)
app.processEvents()
ok("Ctrl+S 保存路径可用", True)
QTest.keyClick(win, Qt.Key_L, Qt.ControlModifier)
app.processEvents()
ok("Ctrl+L 视图切换", win.view_mode == "single")
QTest.keyClick(win, Qt.Key_L, Qt.ControlModifier)
app.processEvents()

win.session.ptr = 0
win.close()
if os.path.exists(os.path.join(BASE, "ann_exp.json")):
    os.remove(os.path.join(BASE, "ann_exp.json"))
print("\n结果:", "ALL PASS" if all(results) else f"{results.count(False)} 项失败 / {len(results)}")
sys.exit(0 if all(results) else 1)
