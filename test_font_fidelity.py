#!/usr/bin/env python3
"""字体保真策略 v3 测试：
1. PDF 内嵌完整字体程序（非子集）→ 编辑后复用原字体程序（真·原字体）
2. Arial（无嵌入程序时）→ 嵌入系统 arial.ttf 真文件，而非内置 Helvetica
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fitz
from core import EditSession, _norm_font

BASE = os.path.dirname(os.path.abspath(__file__))
DENG = r"C:\Windows\Fonts\DENG.TTF"      # 等线：不在 SYS_FONTS 映射内
ARIAL = r"C:\Windows\Fonts\arial.ttf"

results = []
def ok(name, cond):
    results.append(bool(cond))
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}")


def font_programs(doc, page_idx=0):
    """页面（含资源）全部嵌入字体程序字节"""
    progs = {}
    for finfo in doc[page_idx].get_fonts(full=True):
        import re
        xref = finfo[0]
        doc_ = doc
        cands = [xref]
        v = doc_.xref_get_key(xref, "DescendantFonts")
        if v[0] == "array":
            m = re.search(r"(\d+)\s+0\s+R", v[1])
            if m:
                cands.append(int(m.group(1)))
        for fx in cands:
            for key in ("FontFile2", "FontFile3"):
                kv = doc_.xref_get_key(fx, key)
                if kv[0] == "xref":
                    progs[finfo[3]] = doc_.xref_stream(int(kv[1].split()[0]))
    return progs


print("== 1. 内嵌完整字体的原字体复用 ==")
if not os.path.exists(DENG):
    print("  [SKIP] 本机无等线字体")
else:
    # 夹具：insert_text(fontfile=) 直接写 → 完整字体程序随 FontDescriptor 嵌入
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((72, 150), "等线全字体嵌入测试甲行", fontsize=12,
                     fontname="dengfull", fontfile=DENG)
    path = os.path.join(BASE, "font_deng.pdf")
    doc.save(path)
    doc.close()
    d0 = fitz.open(path)
    ok("夹具：完整程序已嵌入 (>1MB)", os.path.getsize(path) > 1_000_000)
    d0.close()

    s = EditSession(path)
    spans = s.spans(0)
    i = next(i for i, sp in enumerate(spans) if "等线" in sp["text"])
    s.edit_span(0, i, "等线全字体嵌入测试乙丙行（新增字符）")
    txt = s.view()[0].get_text()
    ok("编辑内容完整（无 tofu）", "乙丙行（新增字符）" in txt)
    # 关键断言：新文字仍是等线（复用 PDF 内程序），而非黑体回退
    new_fonts = [f[3] for f in s.view()[0].get_fonts(full=True)]
    ok("新文字 = 等线（原字体程序复用）",
       sum(1 for k in new_fonts if "DengXian" in k) >= 2
       and not any("SimHei" in k for k in new_fonts))
    s.save_incremental()
    s2 = EditSession(path)
    txt2 = s2.view()[0].get_text()
    ok("保存后内容完整且仍为等线",
       "乙丙行（新增字符）" in txt2
       and not any("SimHei" in f[3] for f in s2.view()[0].get_fonts(full=True)))
    s.close(); s2.close()

print("== 2. Arial 真文件优先于内置 Helvetica ==")
doc = fitz.open(); doc.new_page(width=595, height=842)
path2 = os.path.join(BASE, "font_arial.pdf")
doc.save(path2); doc.close()
s = EditSession(path2)
fake_span = {"font": "Arial", "color": 0, "size": 11, "origin": (72.0, 100.0)}
s._write_text(s.view()[0], fake_span, "Arial Hello 123", fitz.Point(72, 100))
fonts = [f[3] for f in s.view()[0].get_fonts(full=True)]
ok("嵌入了真 Arial 文件", any("arial" in _norm_font(k) for k in fonts))
ok("没有用 Helvetica 顶替", not any("helvetica" in k.lower() for k in fonts))
s.close()

print("\n结果:", "ALL PASS" if all(results) else f"{results.count(False)} 项失败 / {len(results)}")
sys.exit(0 if all(results) else 1)
