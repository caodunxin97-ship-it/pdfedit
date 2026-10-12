#!/usr/bin/env python3
"""core.py 无头自测：连续编辑 / 撤销重做 / 单层增量保存 / 文本干净度"""
import os, sys, shutil
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fitz
from core import EditSession

BASE = os.path.dirname(os.path.abspath(__file__))
DOC = os.path.join(BASE, "mvp_test.pdf")


def make_doc():
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((72, 120), "Quarterly Revenue Report 2026 Q3", fontsize=18, fontname="hebo")
    page.insert_text((72, 200), "Revenue: 1,234,567 USD", fontsize=12, fontname="helv")
    page.insert_text((72, 260), "Net Profit: 987,654 USD", fontsize=12, fontname="helv")
    page.insert_text((72, 300), "Revenue: 555 USD", fontsize=12, fontname="helv")  # 同文本干扰项
    if os.path.exists(r"C:\Windows\Fonts\simhei.ttf"):
        page.insert_font(fontname="cn0", fontfile=r"C:\Windows\Fonts\simhei.ttf")
        page.insert_text((72, 340), "本报告由原型系统生成，用于编辑验证。", fontsize=12, fontname="cn0")
    doc.subset_fonts()
    doc.save(DOC, deflate=True)
    doc.close()


def text_of(s, page=0):
    return s.view()[page].get_text().strip()


def main():
    ok = lambda name, cond: print(f"  [{'PASS' if cond else 'FAIL'}] {name}") or cond

    print("== 1. 构建与首次编辑 ==")
    make_doc()
    base_bytes = open(DOC, 'rb').read()
    s = EditSession(DOC)
    spans = s.spans(0)
    target = next(i for i, sp in enumerate(spans) if "1,234,567" in sp["text"])
    s.edit_span(0, target, "Revenue: 9,999,999 USD")
    all_ok = ok("编辑生效", "9,999,999" in text_of(s))
    all_ok &= ok("旧文本已从内容流删除（真编辑而非覆盖）", "1,234,567" not in text_of(s))
    all_ok &= ok("同文本干扰项不受影响", "Revenue: 555 USD" in text_of(s))

    print("== 2. 同一 span 连续编辑（span 内连续修改场景）==")
    spans = s.spans(0)
    t2 = next(i for i, sp in enumerate(spans) if "9,999,999" in sp["text"])
    s.edit_span(0, t2, "Revenue: 8,888,888 USD (audited)")
    all_ok &= ok("二次编辑链式生效", "8,888,888" in text_of(s))

    print("== 3. 中文编辑（嵌入字体子集复用）==")
    spans = s.spans(0)
    t3 = next(i for i, sp in enumerate(spans) if "原型系统" in sp["text"])
    s.edit_span(0, t3, "本报告由仿WPS编辑器生成，用于编辑验证。")
    all_ok &= ok("中文编辑生效", "仿WPS编辑器" in text_of(s))

    print("== 4. 撤销 / 重做 ==")
    s.undo()
    all_ok &= ok("撤销1次 -> 回到上一状态", "仿WPS编辑器" not in text_of(s) and "原型系统" in text_of(s))
    s.undo(); s.undo()
    all_ok &= ok("撤销3次 -> 回到基线", "1,234,567" in text_of(s) and "9,999,999" not in text_of(s))
    s.redo(); s.redo(); s.redo()
    all_ok &= ok("重做3次 -> 全部恢复", "8,888,888" in text_of(s) and "仿WPS编辑器" in text_of(s))

    print("== 5. 保存：WPS 式单层增量 ==")
    r1 = s.save_incremental()
    data = open(DOC, 'rb').read()
    all_ok &= ok("3 个操作 = 恰好 1 个增量层", r1["eof"] == 2)
    all_ok &= ok("基线字节为前缀（原文件不动）", data[:len(base_bytes)] == base_bytes)
    # 再改再存：仍是单层（剥离旧层重追加）
    spans = s.spans(0)
    t4 = next(i for i, sp in enumerate(spans) if "Net Profit" in sp["text"])
    s.edit_span(0, t4, "Gross Profit: 777,777 USD")
    r2 = s.save_incremental()
    all_ok &= ok("二次保存仍为单层（strip-and-reappend）", r2["eof"] == 2)

    print("== 6. 保存后文件完整性 ==")
    d2 = fitz.open(DOC)
    txt = d2[0].get_text()
    all_ok &= ok("保存文件含全部编辑", all(k in txt for k in ["8,888,888", "仿WPS编辑器", "Gross Profit"]))
    all_ok &= ok("无残留旧文本", all(k not in txt for k in ["1,234,567", "Net Profit", "原型系统"]))
    d2.close()

    import pypdfium2 as pdfium
    pdoc = pdfium.PdfDocument(DOC)
    pdoc[0].render(scale=1.5).to_pil().save(os.path.join(BASE, "mvp_selftest_pdfium.png"))
    pdoc.close()
    all_ok &= ok("PDFium 引擎可打开并渲染", os.path.getsize(os.path.join(BASE, "mvp_selftest_pdfium.png")) > 5000)

    s.close()
    print("\n结果:", "ALL PASS" if all_ok else "存在失败项")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
