#!/usr/bin/env python3
"""
pdfedit 本地 Web 界面 —— 仿 WPS"编辑内容"的最小可用编辑器
==========================================================
启动:  python server.py [PDF路径] [--port 8765]
浏览器打开 http://127.0.0.1:8765
操作:  点击文字 -> 原位修改 -> Enter 确认；工具栏撤销/重做/保存（单层增量写回）
依赖:  pymupdf（标准库 http.server，无其他依赖）
"""
import os, sys, json, threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from core import EditSession

LOCK = threading.Lock()
SESSION: EditSession | None = None
ZOOM_DEFAULT = 1.5

INDEX = """<!doctype html>
<html lang="zh"><head><meta charset="utf-8">
<title>pdfedit — 仿WPS PDF编辑 MVP</title>
<style>
 body{margin:0;font:14px system-ui;background:#3c3f41;color:#eee;display:flex;flex-direction:column;height:100vh}
 header{display:flex;gap:8px;align-items:center;padding:8px 12px;background:#2b2b2b;border-bottom:1px solid #555}
 button{background:#0e639c;color:#fff;border:0;border-radius:4px;padding:6px 14px;cursor:pointer;font-size:13px}
 button:disabled{background:#555;cursor:default}
 button.warn{background:#a33} button.ghost{background:#444}
 #status{margin-left:auto;color:#9fd;font-size:12px}
 main{flex:1;overflow:auto;display:flex;justify-content:center;padding:16px}
 #pageWrap{position:relative;box-shadow:0 0 12px #000}
 #pageImg{display:block}
 .span{position:absolute;border:1px solid transparent;cursor:text}
 .span:hover{background:rgba(30,144,255,.25);border-color:rgba(30,144,255,.8)}
 #editor{position:absolute;display:none;z-index:10;background:#fffbe6;color:#111;border:2px solid #0e639c;
   border-radius:2px;padding:1px 4px;font:inherit;outline:none;min-width:60px}
</style></head><body>
<header>
 <button id="btnOpen" class="ghost">打开</button>
 <button id="btnUndo" disabled>↶ 撤销</button>
 <button id="btnRedo" disabled>↷ 重做</button>
 <button id="btnSave" class="warn">保存（增量）</button>
 <button id="btnZoomOut" class="ghost">－</button><span id="zoomLabel"></span><button id="btnZoomIn" class="ghost">＋</button>
 <span id="status"></span>
</header>
<main><div id="pageWrap"><img id="pageImg"><div id="spans"></div><input id="editor"></div></main>
<script>
let zoom = 1.5, pageNum = 0, pageCount = 1, spans = [], editing = null;
const $ = id => document.getElementById(id);

async function api(path, body){
  const r = await fetch(path, body ? {method:'POST',body:JSON.stringify(body)} : {});
  if(!r.ok){ throw new Error((await r.json()).error || r.status); }
  return r.headers.get('content-type')?.includes('json') ? r.json() : r;
}
async function refreshPage(){
  const img = await fetch(`/api/page/${pageNum}?zoom=${zoom}`).then(r=>r.blob());
  $('pageImg').src = URL.createObjectURL(img);
  spans = await api(`/api/spans/${pageNum}`);
  const box = $('spans'); box.innerHTML = '';
  const st = await api('/api/state');
  $('pageWrap').style.width = st.page_w*zoom+'px';
  $('pageWrap').style.height = st.page_h*zoom+'px';
  for(const s of spans){
    const d = document.createElement('div'); d.className='span';
    const [x0,y0,x1,y1] = s.bbox;
    Object.assign(d.style,{left:x0*zoom+'px',top:y0*zoom+'px',width:(x1-x0)*zoom+'px',height:(y1-y0)*zoom+'px'});
    d.title = s.text;
    d.onclick = e => startEdit(s, d);
    box.appendChild(d);
  }
  syncBar(st);
}
function syncBar(st){
  $('btnUndo').disabled = !st.can_undo; $('btnRedo').disabled = !st.can_redo;
  $('btnSave').disabled = !st.dirty && !st.can_undo && !st.can_redo ? true : !st.dirty && !st.can_undo;
  $('btnSave').disabled = !st.dirty;
  $('zoomLabel').textContent = Math.round(zoom*100)+'%';
  $('status').textContent = `${st.path||'未打开'} | 第 ${pageNum+1}/${st.page_count} 页 | ${st.ops} 个操作${st.saved_hint||''}`;
  pageCount = st.page_count;
}
function startEdit(s, el){
  editing = s;
  const ed = $('editor');
  const [x0,y0,x1,y1] = s.bbox;
  ed.style.display='block';
  ed.style.left = Math.max(0,x0*zoom-2)+'px'; ed.style.top = Math.max(0,y0*zoom-2)+'px';
  ed.style.width = Math.max(60,(x1-x0)*zoom+30)+'px'; ed.style.height = Math.max(16,(y1-y0)*zoom)+'px';
  ed.style.fontSize = Math.max(10, s.size*zoom*0.9)+'px';
  ed.value = s.text; ed.focus(); ed.select();
}
async function commitEdit(){
  const ed = $('editor'); if(!editing) return;
  const newText = ed.value; const old = editing; ed.style.display='none'; editing=null;
  if(newText===old.text || !newText) return;
  try{ await api('/api/edit',{page:pageNum,span_id:old.id,text:newText}); }
  catch(e){ alert('编辑失败: '+e.message); }
  refreshPage();
}
$('editor').onkeydown = e => {
  if(e.key==='Enter') commitEdit();
  if(e.key==='Escape'){ $('editor').style.display='none'; editing=null; }
};
$('btnUndo').onclick = async()=>{ await api('/api/undo', {}); refreshPage(); };
$('btnRedo').onclick = async()=>{ await api('/api/redo', {}); refreshPage(); };
$('btnSave').onclick = async()=>{
  const r = await api('/api/save', {});
  $('status').textContent = `已保存: ${r.bytes.toLocaleString()}字节 %%EOF=${r.eof} 原字节不动=${r.base_prefix_intact}`;
  refreshPage();
};
$('btnZoomIn').onclick = ()=>{ zoom=Math.min(4,zoom+0.25); refreshPage(); };
$('btnZoomOut').onclick = ()=>{ zoom=Math.max(0.5,zoom-0.25); refreshPage(); };
$('btnOpen').onclick = async()=>{
  const p = prompt('PDF 路径：'); if(!p) return;
  try{ const st = await api('/api/open',{path:p}); pageNum=0; zoom=st.zoom||zoom; refreshPage(); }
  catch(e){ alert('打开失败: '+e.message); }
};
document.addEventListener('keydown', e=>{
  if(e.ctrlKey && e.key==='z'){ e.preventDefault(); $('btnUndo').click(); }
  if(e.ctrlKey && e.key==='y'){ e.preventDefault(); $('btnRedo').click(); }
  if(e.ctrlKey && e.key==='s'){ e.preventDefault(); $('btnSave').click(); }
});
refreshPage();
</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # 安静模式
        pass

    # ---------- helpers ----------
    def _json(self, obj, code=200):
        b = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def _need(self):
        global SESSION
        if SESSION is None:
            self._json({"error": "未打开文档"}, 400)
            return None
        return SESSION

    def do_GET(self):
        global SESSION
        u = urlparse(self.path)
        if u.path == "/":
            b = INDEX.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)
        elif u.path == "/api/state":
            if self._need() is None: return
            with LOCK:
                pg = SESSION.view()[0]
                self._json({
                    "path": SESSION.path, "page_count": SESSION.view().page_count,
                    "page_w": pg.rect.width, "page_h": pg.rect.height,
                    "ops": SESSION.ptr, "can_undo": SESSION.can_undo,
                    "can_redo": SESSION.can_redo, "dirty": SESSION.ptr > 0,
                    "zoom": ZOOM_DEFAULT,
                })
        elif u.path.startswith("/api/page/"):
            if self._need() is None: return
            n = int(u.path.rsplit("/", 1)[1])
            z = float(parse_qs(u.query).get("zoom", [1.5])[0])
            with LOCK:
                png = SESSION.render_png(n, z)
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(png)))
            self.end_headers()
            self.wfile.write(png)
        elif u.path.startswith("/api/spans/"):
            if self._need() is None: return
            n = int(u.path.rsplit("/", 1)[1])
            with LOCK:
                spans = SESSION.spans(n)
            self._json(spans)
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self):
        global SESSION
        u = urlparse(self.path)
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length) or b"{}")
        try:
            if u.path == "/api/open":
                p = body["path"]
                if not os.path.exists(p):
                    return self._json({"error": f"文件不存在: {p}"}, 400)
                with LOCK:
                    if SESSION: SESSION.close()
                    SESSION = EditSession(p)
                return self._json({"ok": True, "path": p})
            s = self._need()
            if s is None: return
            if u.path == "/api/edit":
                with LOCK:
                    s.edit_span(int(body["page"]), int(body["span_id"]), body["text"])
                return self._json({"ok": True})
            if u.path == "/api/undo":
                with LOCK: r = s.undo()
                return self._json({"ok": r})
            if u.path == "/api/redo":
                with LOCK: r = s.redo()
                return self._json({"ok": r})
            if u.path == "/api/save":
                with LOCK: r = s.save_incremental()
                return self._json(r)
            self._json({"error": "not found"}, 404)
        except Exception as e:
            self._json({"error": str(e)}, 500)


def main():
    port = 8765
    doc = None
    args = sys.argv[1:]
    if "--port" in args:
        i = args.index("--port"); port = int(args[i + 1]); del args[i:i + 2]
    if args:
        doc = args[0]
    global SESSION
    if doc and os.path.exists(doc):
        SESSION = EditSession(doc)
        print(f"[pdfedit] 已加载: {doc}")
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"[pdfedit] http://127.0.0.1:{port}  (Ctrl+C 退出)")
    httpd.serve_forever()


if __name__ == "__main__":
    main()
