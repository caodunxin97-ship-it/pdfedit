#!/usr/bin/env python3
"""
pdfedit 核心引擎 —— 仿 WPS PDF 文本编辑（MVP）
==============================================
架构（对应逆向报告中的 WPS 机制）:
  - 基线文档字节不可变（base_bytes）—— 对应"原文件字节不动"
  - 编辑操作 journal + 重放指针 —— 对应 RedoUndo 事务栈
  - 每次渲染 = 从基线重放 ops[0:ptr] —— 确定性，天然支持 undo/redo
  - 保存 = 剥离旧增量层后追加"单层合并增量" —— 对应 WPS strip-and-reappend

编辑模型（覆盖重绘法）:
  定位 span -> 采样背景色 -> 覆盖矩形 -> 以原字号/颜色/基线写入新文字
  （优先复用页面已有字体的资源别名，其次内置字体/CJK 嵌入字体）
"""
from __future__ import annotations
import os, io, copy
import fitz

CJK_FONT = r"C:\Windows\Fonts\simhei.ttf"

# base-14 族 → PyMuPDF 内置字体名（只对纯 Latin 文本安全）
BASE14 = {
    "helvetica": "helv", "helvetica-bold": "hebo",
    "helvetica-oblique": "heit", "helvetica-boldoblique": "hebi",
    "times-roman": "tiro", "times-bold": "tibo",
    "times-italic": "tiit", "times-bolditalic": "tibi",
    "courier": "cour", "courier-bold": "cobo",
    "courier-oblique": "coit", "courier-boldoblique": "cobi",
    "arial": "helv", "arial-bold": "hebo",
}

# 常见字体族名 → Windows 字体文件（用于新文字嵌入，保证字形齐全）
SYS_FONTS = {
    "simsun": r"C:\Windows\Fonts\simsun.ttc",
    "nsimsun": r"C:\Windows\Fonts\simsun.ttc",
    "宋体": r"C:\Windows\Fonts\simsun.ttc",
    "simhei": r"C:\Windows\Fonts\simhei.ttf",
    "黑体": r"C:\Windows\Fonts\simhei.ttf",
    "microsoft yahei": r"C:\Windows\Fonts\msyh.ttc",
    "微软雅黑": r"C:\Windows\Fonts\msyh.ttc",
    "kaiti": r"C:\Windows\Fonts\simkai.ttf",
    "楷体": r"C:\Windows\Fonts\simkai.ttf",
    "fangsong": r"C:\Windows\Fonts\simfang.ttf",
    "仿宋": r"C:\Windows\Fonts\simfang.ttf",
    "arial": r"C:\Windows\Fonts\arial.ttf",
    "times new roman": r"C:\Windows\Fonts\times.ttf",
    "calibri": r"C:\Windows\Fonts\calibri.ttf",
    "courier new": r"C:\Windows\Fonts\cour.ttf",
}

_FONT_CACHE: dict = {}   # fontfile -> fitz.Font（覆盖检查用）


def _rgb(c) -> tuple:
    if isinstance(c, (tuple, list)):
        return tuple(c)[:3]
    return ((c >> 16 & 255) / 255, (c >> 8 & 255) / 255, (c & 255) / 255)


def _norm_font(name: str) -> str:
    """去掉子集前缀 'ABCDEF+FontName' 与 '#20' 转义，用于匹配"""
    n = name.split("+")[-1].replace("#20", " ")
    return n.strip().lower()


class Op:
    """编辑操作。kind ∈ replace | move | annot_add | annot_del |
    insert_text | insert_image | watermark | page_rotate
    文字类（replace/move）定位规则：在 page 上找当前文本 == old_text 的
    第 occurrence 个 span，bbox 距离校验（容差 2pt）。其余见 data。
    """
    __slots__ = ("kind", "page", "data")

    def __init__(self, kind, page, data):
        self.kind, self.page, self.data = kind, page, data

    @property
    def old_text(self):     # replace/move 兼容属性
        return self.data["old_text"]

    def to_json(self):
        return dict(kind=self.kind, page=self.page, data=self.data)


class EditSession:
    def __init__(self, path: str):
        self.path = path
        with open(path, "rb") as f:
            self.base_bytes = f.read()      # 永远保持最初基线
        self.ops: list[Op] = []
        self.ptr = 0                         # 已生效操作数（undo/redo 指针）
        self._doc = None                     # 当前视图（重放产物）

    # ---------- 视图管理 ----------
    def view(self) -> fitz.Document:
        if self._doc is None:
            self._doc = self._replay()
        return self._doc

    def _replay(self) -> fitz.Document:
        doc = fitz.open(stream=self.base_bytes, filetype="pdf")
        for op in self.ops[:self.ptr]:
            self._apply(doc, op)
        return doc

    def invalidate(self):
        if self._doc is not None:
            self._doc.close()
        self._doc = None

    # ---------- span 定位 ----------
    def spans(self, page_idx: int):
        out = []
        for bi, block in enumerate(self.view()[page_idx].get_text("dict")["blocks"]):
            for li, line in enumerate(block.get("lines", [])):
                for span in line["spans"]:
                    if not span["text"].strip():
                        continue
                    out.append({
                        "id": len(out), "text": span["text"], "bbox": span["bbox"],
                        "size": span["size"], "font": span["font"],
                        "color": span["color"], "origin": span["origin"],
                    })
        return out

    def span_at(self, page_idx: int, x: float, y: float):
        """命中测试：点 (x,y)（页面坐标）落在哪个 span"""
        best, best_d = None, 1e9
        for s in self.spans(page_idx):
            x0, y0, x1, y1 = s["bbox"]
            dx = max(x0 - x, 0, x - x1)
            dy = max(y0 - y, 0, y - y1)
            d = (dx * dx + dy * dy) ** 0.5
            if d < best_d:
                best, best_d = s, d
        return best if best_d <= 6 else None

    # ---------- 编辑 ----------
    def edit_span(self, page_idx: int, span_id: int, new_text: str) -> Op:
        spans = self.spans(page_idx)
        if span_id >= len(spans):
            raise ValueError("span 不存在")
        target = spans[span_id]
        same = [s for s in spans if s["text"] == target["text"]]
        occ = same.index(target)
        op = Op("replace", page_idx, dict(
            old_text=target["text"], occurrence=occ, new_text=new_text,
            anchor=list(target["bbox"])))
        return self._exec(op)

    def move_span(self, page_idx: int, span_id: int, dx: float, dy: float) -> Op:
        """移动文字位置（页面坐标增量，y 向下为正）"""
        spans = self.spans(page_idx)
        if span_id >= len(spans):
            raise ValueError("span 不存在")
        target = spans[span_id]
        same = [s for s in spans if s["text"] == target["text"]]
        occ = same.index(target)
        op = Op("move", page_idx, dict(
            old_text=target["text"], occurrence=occ,
            anchor=list(target["bbox"]), dx=dx, dy=dy))
        return self._exec(op)

    # ---------- 新增操作入口（v1.1） ----------
    def annot_add(self, page_idx: int, subtype: str, **params) -> Op:
        """添加批注。subtype: highlight/underline/strikeout/note/freetext/
        ink/rect/oval/line/arrow/stamp。params 与 subtype 对应（见 _apply_annot_add）"""
        op = Op("annot_add", page_idx, dict(subtype=subtype, **params))
        return self._exec(op)

    def annot_delete(self, page_idx: int, subtype: str, rect, content: str = "") -> Op:
        op = Op("annot_del", page_idx, dict(subtype=subtype, rect=list(rect), content=content))
        return self._exec(op)

    def insert_text(self, page_idx: int, point, text: str, size: float = 12.0,
                    color: int = 0, font: str = "SimSun") -> Op:
        op = Op("insert_text", page_idx, dict(
            point=[point[0], point[1]], text=text, size=size, color=color, font=font))
        return self._exec(op)

    def insert_image(self, page_idx: int, rect, img_bytes: bytes) -> Op:
        import base64
        op = Op("insert_image", page_idx, dict(
            rect=list(rect), img_b64=base64.b64encode(img_bytes).decode()))
        return self._exec(op)

    def add_watermark(self, text: str, color=(0.82, 0.82, 0.82), size: float = 42) -> Op:
        op = Op("watermark", 0, dict(text=text, color=list(color), size=size))
        return self._exec(op)

    def rotate_page(self, page_idx: int, delta: int = 90) -> Op:
        op = Op("page_rotate", page_idx, dict(delta=delta))
        return self._exec(op)

    def _exec(self, op: Op) -> Op:
        self._apply(self.view(), op)
        self.ops = self.ops[:self.ptr]
        self.ops.append(op)
        self.ptr += 1
        return op

    def undo(self):
        if self.ptr == 0:
            return False
        self.ptr -= 1
        self.invalidate()
        return True

    def redo(self):
        if self.ptr >= len(self.ops):
            return False
        self.ptr += 1
        self.invalidate()
        return True

    @property
    def can_undo(self): return self.ptr > 0
    @property
    def can_redo(self): return self.ptr < len(self.ops)

    # ---------- 底层应用（重放与即时共用） ----------
    def _apply(self, doc: fitz.Document, op: Op):
        if op.kind in ("replace", "move"):
            self._apply_text(doc, op)
        elif op.kind == "annot_add":
            self._apply_annot_add(doc[op.page], op.data)
        elif op.kind == "annot_del":
            self._apply_annot_del(doc[op.page], op.data)
        elif op.kind == "insert_text":
            page = doc[op.page]
            p = op.data["point"]
            self._write_text(page, {"font": op.data["font"], "color": op.data["color"],
                                    "size": op.data["size"], "origin": tuple(p)},
                             op.data["text"], fitz.Point(*p))
        elif op.kind == "insert_image":
            import base64
            doc[op.page].insert_image(fitz.Rect(op.data["rect"]),
                                      stream=base64.b64decode(op.data["img_b64"]))
        elif op.kind == "watermark":
            d = op.data
            for i in range(doc.page_count):
                page = doc[i]
                w, h = page.rect.width, page.rect.height
                pivot = fitz.Point(w / 2, h / 2)
                m = fitz.Matrix(1, 1).prerotate(32)
                page.insert_text(fitz.Point(w * 0.12, h * 0.62), d["text"],
                                 fontsize=d["size"], color=tuple(d["color"]),
                                 morph=(pivot, m), overlay=True)
        elif op.kind == "page_rotate":
            page = doc[op.page]
            page.set_rotation((page.rotation + op.data["delta"]) % 360)

    def _apply_annot_add(self, page, d: dict):
        sub = d["subtype"]
        R = fitz.Rect
        P = fitz.Point
        if sub in ("highlight", "underline", "strikeout"):
            quads = [fitz.Quad(fitz.Point(q[0], q[1]), fitz.Point(q[2], q[3]),
                               fitz.Point(q[6], q[7]), fitz.Point(q[4], q[5]))
                     for q in d["quads"]]   # 存储序: ul,ur,lr,ll → Quad(ul,ur,ll,lr)
            if sub == "highlight":
                a = page.add_highlight_annot(quads)
            elif sub == "underline":
                a = page.add_underline_annot(quads)
            else:
                a = page.add_strikeout_annot(quads)
            a.set_colors(stroke=tuple(d.get("color", (1, 1, 0))))
            a.update()
        elif sub == "note":
            a = page.add_text_annot(P(d["point"]), d.get("content", ""),
                                    icon=d.get("icon", "Comment"))
            a.set_colors(stroke=tuple(d.get("color", (0.95, 0.75, 0.05))))
            a.update()
        elif sub == "freetext":
            a = page.add_freetext_annot(R(d["rect"]), d.get("content", ""),
                                        fontsize=d.get("size", 12),
                                        fontname="china-s",
                                        text_color=tuple(d.get("color", (0, 0, 0.8))))
            a.update()
        elif sub == "ink":
            strokes = [[(p[0], p[1]) for p in s] for s in d["strokes"]]
            a = page.add_ink_annot(strokes)
            a.set_colors(stroke=tuple(d.get("color", (0.8, 0, 0))))
            a.update()
        elif sub == "rect":
            a = page.add_rect_annot(R(d["rect"]))
            a.set_colors(stroke=tuple(d.get("color", (0.8, 0, 0))))
            a.update()
        elif sub == "oval":
            a = page.add_circle_annot(R(d["rect"]))
            a.set_colors(stroke=tuple(d.get("color", (0.8, 0, 0))))
            a.update()
        elif sub in ("line", "arrow"):
            p1, p2 = P(d["p1"]), P(d["p2"])
            if sub == "line":
                a = page.add_line_annot(p1, p2)
            else:   # 箭头：主线 + 两笔箭头（ink 组合）
                import math
                x1, y1, x2, y2 = d["p1"][0], d["p1"][1], d["p2"][0], d["p2"][1]
                ang = math.atan2(y2 - y1, x2 - x1)
                L = min(14.0, math.hypot(x2 - x1, y2 - y1) * 0.2)
                s1 = [(x2, y2), (x2 - L * math.cos(ang - 0.5), y2 - L * math.sin(ang - 0.5))]
                s2 = [(x2, y2), (x2 - L * math.cos(ang + 0.5), y2 - L * math.sin(ang + 0.5))]
                a = page.add_ink_annot([[(x1, y1), (x2, y2)], s1, s2])
            a.set_colors(stroke=tuple(d.get("color", (0.8, 0, 0))))
            a.update()
        elif sub == "stamp":
            name = d.get("stamp", "Approved")
            stamp_id = getattr(fitz, f"STAMP_{name}", fitz.STAMP_Approved)
            a = page.add_stamp_annot(R(d["rect"]), stamp=stamp_id)
            a.update()

    @staticmethod
    def _apply_annot_del(page, d: dict):
        sub = d["subtype"]
        want = {"highlight": 8, "underline": 9, "strikeout": 10, "note": 0,
                "freetext": 2, "ink": 15, "rect": 4, "oval": 5,
                "line": 3, "arrow": 15, "stamp": 13}.get(sub)
        target = fitz.Rect(d["rect"])
        best, best_d = None, -1.0
        for a in page.annots():
            if want is not None and a.type[0] != want:
                continue
            r = a.rect & target
            inter = r.get_area() if not r.is_empty else 0
            if inter == 0:
                continue
            if d.get("content") and (a.info.get("content", "") != d["content"]):
                continue
            area = min(a.rect.get_area(), target.get_area()) or 1
            score = inter / area
            if score > best_d:
                best, best_d = a, score
        if best is None:
            raise RuntimeError(f"annot_del: 未找到匹配批注 {sub}")
        page.delete_annot(best)

    def _apply_text(self, doc: fitz.Document, op: Op):
        page = doc[op.page]
        d = op.data
        # 在当前文档状态里找目标 span
        cands = []
        for block in page.get_text("dict")["blocks"]:
            for line in block.get("lines", []):
                for span in line["spans"]:
                    if span["text"] == d["old_text"]:
                        cands.append(span)
        if d["occurrence"] >= len(cands):
            raise RuntimeError(f"重放失败: 找不到 '{d['old_text'][:20]}…' #{d['occurrence']}")
        # anchor 距离校验（容差 2pt），同文本多 span 时选最近的
        ax = (d["anchor"][0] + d["anchor"][2]) / 2
        ay = (d["anchor"][1] + d["anchor"][3]) / 2
        def dist(s):
            bx = (s["bbox"][0] + s["bbox"][2]) / 2
            by = (s["bbox"][1] + s["bbox"][3]) / 2
            return ((bx - ax) ** 2 + (by - ay) ** 2) ** 0.5
        cands.sort(key=dist)
        span = cands[0]
        if dist(span) > 2:
            raise RuntimeError("重放失败: anchor 偏移超限")

        bg = self._sample_bg(page, span["bbox"])
        # 真·删除原文字（重写内容流，对应 WPS 内容流重生成）：
        # redaction 移除 rect 内的文字对象并用采样背景色填充；
        # 不动图片与矢量线条（与 WPS 擦除的对象级边界一致）
        page.add_redact_annot(fitz.Rect(span["bbox"]), fill=bg)
        page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE,
                              graphics=fitz.PDF_REDACT_LINE_ART_NONE)
        # move 操作：同样文字，写入平移后的基线原点
        origin = fitz.Point(*span["origin"])
        text = d["new_text"] if op.kind == "replace" else d["old_text"]
        if op.kind == "move":
            origin = fitz.Point(origin.x + d["dx"], origin.y + d["dy"])
        self._write_text(page, span, text, origin)

    def _sample_bg(self, page, bbox):
        pad = 4
        clip = fitz.Rect(bbox[0] - pad, bbox[1] - pad, bbox[2] + pad, bbox[3] + pad)
        pix = page.get_pixmap(clip=clip, matrix=fitz.Matrix(3, 3))
        w, h, n = pix.width, pix.height, pix.n
        buf = pix.samples
        border = []
        for x in range(w):
            for y in (0, h - 1):
                i = (y * w + x) * n
                border.append(tuple(buf[i:i + 3]))
        for y in range(h):
            for x in (0, w - 1):
                i = (y * w + x) * n
                border.append(tuple(buf[i:i + 3]))
        border.sort()
        m = border[len(border) // 2]
        return tuple(c / 255 for c in m)

    def _write_text(self, page, span, text, origin):
        """为新文字选择字体并写入。策略（v3，优先级从高到低）：
        1. 原字体在 PDF 内嵌了字体程序（FontFile2/3），且覆盖新字符
           → 提取该程序以唯一别名重新嵌入（真·原字体，子集自然被覆盖检查淘汰）
        2. 原字体族匹配 Windows 系统字体文件且覆盖 → 嵌入真文件（同族一致）
        3. 纯 Latin + base-14 族 → 内置度量兼容字体（零嵌入）
        4. 含 CJK → 黑体；否则 helv 兜底
        绝不复用页面已有的字体资源别名（MuPDF 会静默忽略 fontfile 导致子集缺字）。
        """
        col, size = _rgb(span["color"]), span["size"]
        fam = _norm_font(span["font"])
        has_cjk = any(ord(c) > 0x2E80 for c in text)

        buf = self._embedded_program(page, fam)
        if buf is not None and self._buf_covers(buf, text):
            alias = self._alias_for(f"buf:{hash(bytes(buf))}", page)
            page.insert_font(fontname=alias, fontbuffer=buf)
            page.insert_text(origin, text, fontsize=size,
                             fontname=alias, color=col)
            return

        for key, path in SYS_FONTS.items():
            if key in fam and os.path.exists(path):
                if self._file_covers(path, text):
                    alias = self._alias_for(path, page)
                    page.insert_text(origin, text, fontsize=size,
                                     fontname=alias, fontfile=path, color=col)
                    return
                break   # 族名匹配但字形不齐（如 Latin 字体遇中文），走兜底

        if not has_cjk and fam in BASE14:
            page.insert_text(origin, text, fontsize=size,
                             fontname=BASE14[fam], color=col)
        elif has_cjk and os.path.exists(CJK_FONT):
            alias = self._alias_for(CJK_FONT, page)
            page.insert_text(origin, text, fontsize=size,
                             fontname=alias, fontfile=CJK_FONT, color=col)
        else:
            page.insert_text(origin, text, fontsize=size,
                             fontname="helv", color=col)

    @staticmethod
    def _embedded_program(page, fam: str):
        """取与 span 字体族匹配的内嵌字体程序。
        结构：FontFile2/3 可能直接在字体 dict、在其后代 CID 字体、
        或（标准情况）在 FontDescriptor 里；名字做包含式模糊匹配
        （span 名 'DengXian' vs BaseFont 'DengXian Regular'）。"""
        import re
        doc = page.parent

        def ff_from(t) -> bytes | None:
            for key in ("FontFile2", "FontFile3"):
                kv = doc.xref_get_key(t, key)
                if kv[0] == "xref":
                    try:
                        return doc.xref_stream(int(kv[1].split()[0]))
                    except Exception:
                        return None
            return None

        for finfo in page.get_fonts(full=True):
            xref = finfo[0]
            b = _norm_font(finfo[3])
            if not (fam == b or fam in b or b in fam):
                continue
            cands = [xref]
            v = doc.xref_get_key(xref, "DescendantFonts")
            if v[0] == "array":
                for m in re.finditer(r"(\d+)\s+0\s+R", v[1]):
                    cands.append(int(m.group(1)))
            for fx in cands:
                buf = ff_from(fx)                      # 直接挂在字体 dict 上
                if buf is not None:
                    return buf
                fd = doc.xref_get_key(fx, "FontDescriptor")
                if fd[0] == "xref":
                    buf = ff_from(int(fd[1].split()[0]))   # 标准位置
                    if buf is not None:
                        return buf
        return None

    def _buf_covers(self, buf: bytes, text: str) -> bool:
        global _FONT_CACHE
        k = ("buf", hash(bytes(buf)))
        if k not in _FONT_CACHE:
            try:
                _FONT_CACHE[k] = fitz.Font(fontbuffer=buf)
            except Exception:
                return False
        return all(_FONT_CACHE[k].has_glyph(ord(c)) for c in text)

    def _alias_for(self, fontfile: str, page) -> str:
        """会话内同一字体文件复用同一唯一别名；别名不与页面已有资源冲突"""
        if not hasattr(self, "_alias_map"):
            self._alias_map = {}
        if fontfile in self._alias_map:
            return self._alias_map[fontfile]
        existing = {f[4] for f in page.get_fonts(full=True)}
        k = 0
        while f"__pe{k}" in existing or f"__pe{k}" in self._alias_map.values():
            k += 1
        alias = f"__pe{k}"
        self._alias_map[fontfile] = alias
        return alias

    @staticmethod
    def _file_covers(path: str, text: str) -> bool:
        """系统字体文件是否覆盖 text 全部字形"""
        global _FONT_CACHE
        if path not in _FONT_CACHE:
            try:
                _FONT_CACHE[path] = fitz.Font(fontfile=path)
            except Exception:
                return False
        f = _FONT_CACHE[path]
        return all(f.has_glyph(ord(c)) for c in text)

    # ---------- 渲染 ----------
    def render_png(self, page_idx: int, zoom: float = 1.5, annots: bool = True) -> bytes:
        pix = self.view()[page_idx].get_pixmap(matrix=fitz.Matrix(zoom, zoom),
                                               annots=annots)
        return pix.tobytes("png")

    def list_annots(self):
        """全部批注清单（管理/导出用）"""
        out = []
        for pno in range(self.view().page_count):
            for a in self.view()[pno].annots() or []:
                out.append({
                    "page": pno, "xref": a.xref, "type": a.type[1],
                    "rect": list(a.rect), "content": a.info.get("content", ""),
                })
        return out

    # ---------- 保存：WPS 式单层增量 ----------
    def save_incremental(self) -> dict:
        """剥离旧增量层 -> 从基线重放 ops -> 追加单层增量（WPS 同款）"""
        with open(self.path, "wb") as f:
            f.write(self.base_bytes)                       # 还原基线（剥离历史层）
        doc = fitz.open(self.path)
        for op in self.ops[:self.ptr]:
            self._apply(doc, op)
        try:
            doc.subset_fonts()
        except Exception:
            pass
        n_eofs_before = open(self.path, "rb").read().count(b"%%EOF")
        doc.save(self.path, incremental=True, encryption=fitz.PDF_ENCRYPT_KEEP)
        doc.close()
        data = open(self.path, "rb").read()
        return {
            "bytes": len(data), "eof": data.count(b"%%EOF"),
            "layers_added": data.count(b"%%EOF") - n_eofs_before,
            "base_prefix_intact": data[:len(self.base_bytes)] == self.base_bytes,
            "ops_saved": self.ptr,
        }

    def close(self):
        self.invalidate()
