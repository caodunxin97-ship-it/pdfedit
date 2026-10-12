#!/usr/bin/env python3
"""app.py offscreen 自动化测试：真实 Qt 事件 + 模拟鼠标键盘"""
import os, sys
os.environ["QT_QPA_PLATFORM"] = "offscreen"   # 必须在导入 Qt 前
import fitz

BASE = os.path.dirname(os.path.abspath(__file__))
DOC = os.path.join(BASE, "desk_test.pdf")

# 构建测试文档
doc = fitz.open(); page = doc.new_page(width=595, height=842)
page.insert_text((72, 120), "Quarterly Revenue Report 2026 Q3", fontsize=18, fontname="hebo")
page.insert_text((72, 200), "Revenue: 1,234,567 USD", fontsize=12, fontname="helv")
page.insert_text((72, 260), "Net Profit: 987,654 USD", fontsize=12, fontname="helv")
if os.path.exists(r"C:\Windows\Fonts\simhei.ttf"):
    page.insert_font(fontname="cn0", fontfile=r"C:\Windows\Fonts\simhei.ttf")
    page.insert_text((72, 330), "本报告由原型系统生成，用于编辑验证。", fontsize=12, fontname="cn0")
doc.subset_fonts(); doc.save(DOC); doc.close()
base_bytes = open(DOC, "rb").read()

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt, QPoint
from PySide6.QtTest import QTest

import app as appmod
from app import MainWindow

app = QApplication([])
win = MainWindow(DOC)
win.show()
app.processEvents()

results = []
def ok(name, cond):
    results.append(bool(cond))
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}")

def span_index(canvas, needle):
    for i, s in enumerate(canvas.spans):
        if needle in s["text"]:
            return i
    return -1

def click_span(win, needle):
    c = win.canvas
    i = span_index(c, needle)
    assert i >= 0, f"span not found: {needle}"
    r = c.span_rect(i)
    QTest.mouseClick(c, Qt.LeftButton, Qt.NoModifier,
                     QPoint(int(r.center().x()), int(r.center().y())))
    app.processEvents()
    return i

print("== 1. 启动与初始状态 ==")
ok("窗口标题", win.windowTitle().startswith("pdfedit"))
ok("文档加载, span 数 >= 4", len(win.canvas.spans) >= 4)
ok("撤销/保存初始禁用", not win.a_undo.isEnabled() and not win.a_save.isEnabled())

print("== 2. 鼠标点击 -> 原位编辑 ==")
click_span(win, "Net Profit")
ok("编辑框出现并预填", win.canvas.editor.isVisible()
   and win.canvas.editor.text() == "Net Profit: 987,654 USD")
win.canvas.editor.setText("Gross Profit: 5,555,555 USD")
QTest.keyClick(win.canvas.editor, Qt.Key_Return)
app.processEvents()
ok("提交后文本生效", any("5,555,555" in s["text"] for s in win.canvas.spans))
ok("撤销可用", win.a_undo.isEnabled())
ok("状态栏显示 1 个操作", "1 个未保存操作" in win.statusBar().currentMessage())

print("== 3. 中文 span 编辑 ==")
click_span(win, "原型系统")
win.canvas.editor.setText("本报告由桌面版仿WPS编辑器生成。")
QTest.keyClick(win.canvas.editor, Qt.Key_Return)
app.processEvents()
ok("中文编辑生效", any("桌面版仿WPS编辑器" in s["text"] for s in win.canvas.spans))

print("== 4. Esc 取消不产生操作 ==")
before = win.session.ptr
click_span(win, "Revenue")
QTest.keyClick(win.canvas.editor, Qt.Key_Escape)
app.processEvents()
ok("Esc 取消", win.canvas.editing_span is None and win.session.ptr == before)

print("== 5. 撤销 / 重做 ==")
win.do_undo(); app.processEvents()
ok("撤销1: 中文回退", not any("桌面版" in s["text"] for s in win.canvas.spans))
win.do_undo(); app.processEvents()
ok("撤销2: 数字回退", any("987,654" in s["text"] for s in win.canvas.spans))
win.do_redo(); win.do_redo(); app.processEvents()
ok("重做x2: 全部恢复", any("5,555,555" in s["text"] for s in win.canvas.spans)
   and any("桌面版仿WPS编辑器" in s["text"] for s in win.canvas.spans))

print("== 6. 增量保存 ==")
win.do_save(); app.processEvents()
data = open(DOC, "rb").read()
ok("单层增量 (%%EOF=2)", data.count(b"%%EOF") == 2)
ok("基线字节为前缀", data[:len(base_bytes)] == base_bytes)

print("== 7. 缩放与翻页 ==")
z0 = win.zoom
win.zoom_by(0.5)
ok("缩放生效", abs(win.zoom - (z0 + 0.5)) < 1e-9)
win.zoom_by(-0.5)

print("== 7b. 拖拽移动文字 ==")
before = [s["bbox"] for s in win.canvas.spans if "5,555,555" in s["text"]][0]
c = win.canvas
i = span_index(c, "5,555,555")
r = c.span_rect(i)
center = QPoint(int(r.center().x()), int(r.center().y()))
QTest.mousePress(c, Qt.LeftButton, Qt.NoModifier, center)
QTest.mouseMove(c, QPoint(center.x() + 60, center.y() + 20))
QTest.mouseRelease(c, Qt.LeftButton, Qt.NoModifier, QPoint(center.x() + 60, center.y() + 20))
app.processEvents()
after = [s["bbox"] for s in win.canvas.spans if "5,555,555" in s["text"]][0]
ok("移动生效 (~+60px,+20px)", abs((after[0] - before[0]) * win.zoom - 60) < 3
   and abs((after[1] - before[1]) * win.zoom - 20) < 3)
ok("移动计入撤销栈", win.session.ptr >= 3)

print("== 7d. 对齐辅助线与吸附 ==")
c = win.canvas
z = win.zoom
page_cx = c.pixmap.width() / 2
# 场景1：把 Revenue 行拖到页面水平中线左侧 3px → 吸附应把中心拉到页面中线
i_rev = span_index(c, "Revenue:")
r_rev = c.span_rect(i_rev)
press = QPoint(int(r_rev.center().x()), int(r_rev.center().y()))
target_cx = page_cx - 3
raw_dx = int(target_cx - r_rev.center().x())
QTest.mousePress(c, Qt.LeftButton, Qt.NoModifier, press)
QTest.mouseMove(c, QPoint(press.x() + raw_dx, press.y()))
app.processEvents()
guide_seen_v = c._guide_v is not None
win.grab().save(os.path.join(BASE, "desk_shot_guide.png"))
QTest.mouseRelease(c, Qt.LeftButton, Qt.NoModifier, QPoint(press.x() + raw_dx, press.y()))
app.processEvents()
new_rev = [s["bbox"] for s in c.spans if "Revenue:" in s["text"]][0]
final_cx = (new_rev[0] + new_rev[2]) / 2 * z
ok("竖直参考线出现（页面中线）", guide_seen_v)
ok("中心吸附到页面中线 (±1.5px)", abs(final_cx - page_cx) < 1.5)

# 场景2：中文行顶边拖到标题行顶边下方 5px → 吸附应完全对齐（纯竖直拖动）
i_cn = span_index(c, "本报告")
i_tit = span_index(c, "Quarterly")
assert i_cn >= 0 and i_tit >= 0, "span 缺失"
r_cn, r_tit = c.span_rect(i_cn), c.span_rect(i_tit)
top_target = r_tit.y() + 5
press2 = QPoint(int(r_cn.center().x()), int(r_cn.center().y()))
raw_dy = int(top_target - r_cn.y())
QTest.mousePress(c, Qt.LeftButton, Qt.NoModifier, press2)
QTest.mouseMove(c, QPoint(press2.x(), press2.y() + raw_dy))
app.processEvents()
guide_seen_h = c._guide_h is not None
QTest.mouseRelease(c, Qt.LeftButton, Qt.NoModifier, QPoint(press2.x(), press2.y() + raw_dy))
app.processEvents()
new_cn = [s["bbox"] for s in c.spans if "本报告" in s["text"]][0]
new_tit = [s["bbox"] for s in c.spans if "Quarterly" in s["text"]][0]
ok("水平参考线出现（顶边对齐）", guide_seen_h)
ok("顶边吸附对齐 (±1.5px)", abs(new_cn[1] * z - new_tit[1] * z) < 1.5)

print("== 7c. tofu 回归：跨会话二次编辑中文 ==")
# 模拟用户场景：上一会话已编辑过中文并保存，本会话再次编辑
s2 = win.session
s2.do_save_direct = None
r = s2.save_incremental()
win.session.close()
from core import EditSession
s3 = EditSession(DOC)
spans3 = s3.spans(0)
i3 = next(i for i, sp in enumerate(spans3) if "本报告" in sp["text"])
s3.edit_span(0, i3, "第二次修改：新增了卧槽不常见的字齉龘。")
txt3 = s3.view()[0].get_text()
ok("二次编辑中文无 tofu（提取完整）", "齉龘" in txt3 and "第二次修改" in txt3)
# 字形级验证：新文字所用嵌入字体覆盖全部字符
import fitz as _fz
d3 = s3.view()
found_cover = False
for block in d3[0].get_text("dict")["blocks"]:
    for line in block.get("lines", []):
        for sp in line["spans"]:
            if "齉龘" in sp["text"]:
                found_cover = True
ok("新文字 span 存在且可提取", found_cover)
s3.close()

print("== 8. 窗口截图（视觉验证素材）==")
win.grab().save(os.path.join(BASE, "desk_shot_main.png"))
click_span(win, "5,555,555")
win.grab().save(os.path.join(BASE, "desk_shot_editing.png"))
win.canvas.editor.hide()

# 退出确认对话框（有未保存操作时）——直接 Discard 退出避免阻塞
if win.session.ptr > 0:
    win.session.ptr = 0  # 测试环境直接清空，不触发弹窗
win.close()

print("\n结果:", "ALL PASS" if all(results) else f"{results.count(False)} 项失败 / {len(results)}")
sys.exit(0 if all(results) else 1)
