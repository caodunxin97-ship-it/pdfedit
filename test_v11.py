#!/usr/bin/env python3
"""v1.1 功能回归：批注全家桶 + 插入 + 水印 + 旋转 + 隐藏/删除批注"""
import os, sys
os.environ["QT_QPA_PLATFORM"] = "offscreen"
import fitz

BASE = os.path.dirname(os.path.abspath(__file__))
DOC = os.path.join(BASE, "v11_test.pdf")

doc = fitz.open(); page = doc.new_page(width=595, height=842)
page.insert_text((72, 150), "Revenue: 1,234,567 USD", fontsize=12, fontname="helv")
page.insert_text((72, 200), "Net Profit: 987,654 USD", fontsize=12, fontname="helv")
if os.path.exists(r"C:\Windows\Fonts\simhei.ttf"):
    page.insert_font(fontname="cn0", fontfile=r"C:\Windows\Fonts\simhei.ttf")
    page.insert_text((72, 260), "中文测试行用于批注。", fontsize=12, fontname="cn0")
doc.subset_fonts(); doc.save(DOC); doc.close()
base_bytes = open(DOC, "rb").read()

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt, QPoint
from PySide6.QtTest import QTest
from app import MainWindow

app = QApplication([])
win = MainWindow(DOC)
win.show(); app.processEvents()
c = win.canvas

results = []
def ok(name, cond):
    results.append(bool(cond))
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}")

def n_annots():
    return len(win.session.list_annots())

print("== 1. 文字标记批注（UI 点击）==")
n0 = n_annots()
win.set_tool("highlight"); app.processEvents()
r = c.span_rect(next(i for i, s in enumerate(c.spans) if "Revenue" in s["text"]))
QTest.mouseClick(c, Qt.LeftButton, Qt.NoModifier, QPoint(int(r.center().x()), int(r.center().y())))
app.processEvents()
ok("高亮: +1", n_annots() == n0 + 1)
win.set_tool("underline"); app.processEvents()
QTest.mouseClick(c, Qt.LeftButton, Qt.NoModifier, QPoint(int(r.center().x()), int(r.center().y())))
app.processEvents()
win.set_tool("strikeout"); app.processEvents()
QTest.mouseClick(c, Qt.LeftButton, Qt.NoModifier, QPoint(int(r.center().x()), int(r.center().y())))
app.processEvents()
ok("下划线+删除线: 共+3", n_annots() == n0 + 3)

print("== 2. 拖拽绘制（UI）==")
win.set_tool("rect"); app.processEvents()
QTest.mousePress(c, Qt.LeftButton, Qt.NoModifier, QPoint(100, 500))
QTest.mouseMove(c, QPoint(200, 550))
QTest.mouseRelease(c, Qt.LeftButton, Qt.NoModifier, QPoint(200, 550))
app.processEvents()
n1 = n_annots()
ok("矩形: +1", n1 == n0 + 4)
win.set_tool("ink"); app.processEvents()
QTest.mousePress(c, Qt.LeftButton, Qt.NoModifier, QPoint(300, 600))
for x in range(300, 400, 10):
    QTest.mouseMove(c, QPoint(x, 600 + (x % 20)))
QTest.mouseRelease(c, Qt.LeftButton, Qt.NoModifier, QPoint(400, 610))
app.processEvents()
ok("随意画: +1", n_annots() == n1 + 1)

print("== 3. 便签/文本框/图章/箭头（session API）==")
s = win.session
s.annot_add(0, "note", point=(300, 300), content="便签内容A")
s.annot_add(0, "freetext", rect=[300, 320, 480, 350], content="文本框B", size=12)
s.annot_add(0, "stamp", rect=[400, 400, 480, 445], stamp="Draft")
s.annot_add(0, "arrow", p1=[100, 420], p2=[250, 420])
ok("四类批注 +4", n_annots() == n1 + 5)

print("== 4. 插入文字/图片/水印/旋转 ==")
s.insert_text(0, (72, 480), "插入的新文字行 New 99", size=12)
ok("插入文字生效", any("New 99" in sp["text"] for sp in s.spans(0)))
png = open(os.path.join(BASE, "desk_shot_main.png"), "rb").read()
s.insert_image(0, [500, 470, 570, 520], png)
s.add_watermark("V11")
s.rotate_page(0, 90)
ok("旋转 90°", s.view()[0].rotation == 90)
s.rotate_page(0, -90)
ok("旋转回 0°", s.view()[0].rotation == 0)

print("== 5. 批注选中删除（Delete 键）==")
win.after_document_change(); app.processEvents()
c.setFocus()
c._sel_annot = next(a for a in c._annots if a["type"] == "Text")
n2 = n_annots()
QTest.keyClick(c, Qt.Key_Delete)
app.processEvents()
ok("Delete 删除便签: -1", n_annots() == n2 - 1)

print("== 6. 隐藏批注切换 ==")
p1 = s.render_png(0, 1.0, annots=True)
p2 = s.render_png(0, 1.0, annots=False)
ok("隐藏批注渲染不同", p1 != p2)

print("== 7. 撤销链与保存 ==")
total_ops = s.ptr
while s.can_undo: s.undo()
ok("全部撤销回基线", n_annots() == 0 and s.view()[0].get_text().count("New 99") == 0)
while s.can_redo: s.redo()
ok("全部重做恢复", n_annots() == n2 - 1 and total_ops == s.ptr)
r = s.save_incremental()
data = open(DOC, "rb").read()
ok("单层增量 (EOF=2)", r["eof"] == 2)
ok("基线字节为前缀", data[:len(base_bytes)] == base_bytes)

import pypdfium2 as pdfium
p = pdfium.PdfDocument(DOC)
p[0].render(scale=1.2).to_pil().save(os.path.join(BASE, "v11_test_pdfium.png"))
p.close()
ok("PDFium 渲染含批注文件", os.path.getsize(os.path.join(BASE, "v11_test_pdfium.png")) > 5000)

win.session.ptr = 0
win.close()
print("\n结果:", "ALL PASS" if all(results) else f"{results.count(False)} 项失败 / {len(results)}")
sys.exit(0 if all(results) else 1)
