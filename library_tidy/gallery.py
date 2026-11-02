"""A read-only web view of the library for phones and browsers.

Why this exists: WebDAV/SMB clients decide for themselves whether to fetch a
thumbnail, and several never do for remote shares, so a correctly-named library
looks like a wall of blank tiles. Serving pre-built JPEGs removes the client's
opinion from the loop.

Routes: / (single page app), /data.json, /thumb?p=, /full?p=. Every path is
resolved and must stay inside the root. Nothing here ever writes to the library.
"""
from __future__ import annotations

import base64
import hashlib
import json
import mimetypes
import os
import re
import shutil
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

IMG_EXT = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
NO_PREVIEW = {".heic", ".psd", ".svg", ".tif", ".tiff"}

PAGE = r"""<!doctype html>
<html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>__TITLE__</title>
<style>
:root{--bg:#0d1117;--fg:#e6edf3;--dim:#8b949e;--line:#21262d;--acc:#58a6ff;--card:#161b22}
*{box-sizing:border-box;-webkit-tap-highlight-color:transparent}
body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.45 -apple-system,system-ui,"Segoe UI",Roboto,sans-serif}
header{position:sticky;top:0;z-index:20;background:rgba(13,17,23,.96);backdrop-filter:blur(8px);border-bottom:1px solid var(--line);padding:10px 12px}
h1{font-size:16px;margin:0 0 8px;font-weight:600}
h1 small{color:var(--dim);font-weight:400;font-size:12px;margin-left:6px}
#search{width:100%;padding:9px 12px;border-radius:9px;border:1px solid var(--line);background:var(--card);color:var(--fg);font-size:15px}
#crumb{padding:8px 12px;font-size:13px;color:var(--dim);border-bottom:1px solid var(--line);overflow-x:auto;white-space:nowrap}
#crumb a{color:var(--acc);text-decoration:none}
main{padding:10px 12px 80px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(108px,1fr));gap:8px}
.cell{background:var(--card);border:1px solid var(--line);border-radius:10px;overflow:hidden;display:flex;flex-direction:column}
.cell img{width:100%;aspect-ratio:1/1;object-fit:cover;background:#0b0f14;display:block}
.cell .cap{font-size:11px;color:var(--dim);padding:5px 6px;line-height:1.25;word-break:break-word;max-height:52px;overflow:hidden}
.row{display:flex;align-items:center;gap:10px;padding:10px 8px;border-bottom:1px solid var(--line);text-decoration:none;color:var(--fg)}
.row .ico{width:26px;text-align:center;font-size:17px;flex:0 0 auto}
.row .nm{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:14px}
.row .meta{color:var(--dim);font-size:11px;flex:0 0 auto}
.folder{color:var(--acc)}
#status{position:fixed;bottom:0;left:0;right:0;padding:8px 12px;background:rgba(13,17,23,.96);border-top:1px solid var(--line);color:var(--dim);font-size:12px}
#viewer{position:fixed;inset:0;background:#000;z-index:50;display:none;flex-direction:column}
#viewer.on{display:flex}
#viewer img{flex:1;object-fit:contain;width:100%;min-height:0}
#viewer .bar{display:flex;gap:10px;padding:10px 12px;background:#0d1117;color:var(--dim);font-size:12px;align-items:center}
#viewer .bar a{color:var(--acc);margin-left:auto;text-decoration:none}
#viewer .bar button{background:none;border:0;color:var(--dim);font-size:18px}
.tabs{display:flex;gap:6px;margin:8px 0 0}
.tab{padding:5px 12px;border-radius:20px;border:1px solid var(--line);background:var(--card);color:var(--dim);font-size:12px}
.tab.on{background:var(--acc);color:#04121f;border-color:var(--acc);font-weight:600}
</style></head><body>
<header>
  <h1>__TITLE__ <small id="stats"></small></h1>
  <input id="search" placeholder="Filter current folder…" autocomplete="off">
  <div class="tabs">
    <div class="tab on" data-mode="all">All files</div>
    <div class="tab" data-mode="img">Photos only</div>
  </div>
</header>
<div id="crumb"></div>
<main><div id="out"></div></main>
<div id="status">loading…</div>
<div id="viewer"><div class="bar"><button id="vclose">✕</button><span id="vname"></span><a id="vraw" target="_blank" rel="noopener">open original ⤢</a></div><img id="vimg" alt=""></div>
<script>
let DATA=[], CUR='', MODE='all', Q='';
const IMG=/\.(jpg|jpeg|png|webp|gif)$/i;
const ICON={pdf:'📕',docx:'📘',doc:'📘',txt:'📄',md:'📝',xlsx:'📗',xls:'📗',csv:'📊',pptx:'📙',eml:'✉️',json:'⚙️',py:'🐍',ipynb:'📓',js:'📜',html:'🌐',psd:'🎨',heic:'🖼️',svg:'🖼️',flac:'🎵',mp3:'🎵',m4a:'🎵',zip:'📦',mp4:'🎬',mov:'🎬',mkv:'🎬'};
const ext=n=>{const m=n.match(/\.([^.]+)$/);return m?m[1].toLowerCase():''};
const fmt=n=>{if(n<1024)return n+' B';if(n<1048576)return (n/1024).toFixed(0)+' KB';if(n<1073741824)return (n/1048576).toFixed(1)+' MB';return (n/1073741824).toFixed(2)+' GB'};
const esc=s=>s.replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const out=document.getElementById('out'), crumb=document.getElementById('crumb'), status=document.getElementById('status');
function kids(){
  const p = CUR ? CUR + '/' : '';
  const dirs=[], files=[], seen=new Set();
  for(const it of DATA){
    if(!it.d) continue;
    if(!it.p.startsWith(p)) continue;
    const rest=it.p.slice(p.length), first=rest.split('/')[0];
    if(!first) continue;
    if(rest.includes('/')){ if(!seen.has(first)){seen.add(first); dirs.push(first);} }
    else dirs.push(first);
  }
  for(const it of DATA){
    if(it.d || !it.p.startsWith(p)) continue;
    const rest=it.p.slice(p.length);
    if(rest.includes('/') || !rest) continue;
    files.push(it);
  }
  return {dirs:[...new Set(dirs)].sort(), files};
}
function render(){
  const {dirs, files} = kids();
  const q = Q.toLowerCase();
  const fdirs = dirs.filter(d=>!q || d.toLowerCase().includes(q));
  const ffiles = files.filter(f=>{const n=f.p.split('/').pop(); return !q || n.toLowerCase().includes(q);});
  crumb.innerHTML = CUR ? '<a href="#" data-go="">Library</a> / ' + CUR.split('/').map((s,i,a)=>
      '<a href="#" data-go="'+esc(a.slice(0,i+1).join('/'))+'">'+esc(s)+'</a>').join(' / ') : 'Library';
  let html='';
  if(fdirs.length) html += fdirs.map(d=>{
    const sub=(CUR?CUR+'/':'')+d;
    const n=DATA.filter(x=>!x.d && x.p.startsWith(sub+'/')).length;
    return `<a class="row" href="#" data-go="${esc(sub)}"><span class="ico">📁</span><span class="nm folder">${esc(d)}</span><span class="meta">${n} files</span></a>`;
  }).join('');
  if(MODE==='img'){
    const prefix = CUR ? CUR + '/' : '';
    let imgs = DATA.filter(x=>!x.d && x.p.startsWith(prefix) && IMG.test(x.p));
    if(q) imgs = imgs.filter(x=>x.p.split('/').pop().toLowerCase().includes(q));
    const total = imgs.length;
    const CAP = 800;
    if(imgs.length > CAP) imgs = imgs.slice(0, CAP);
    if(imgs.length){
      html += `<div class="grid">`+imgs.map(f=>{
        const n=f.p.split('/').pop();
        const rel=f.p.slice(prefix.length);
        const sub=rel.includes('/') ? rel.split('/').slice(0,-1).join('/')+'/' : '';
        return `<div class="cell" data-open="${esc(f.p)}"><img loading="lazy" src="/thumb?p=${encodeURIComponent(f.p)}" alt=""><div class="cap">${esc(sub)}<b>${esc(n)}</b></div></div>`;
      }).join('')+`</div>`;
      if(total > CAP) html += `<p style="color:#8b949e">showing first ${CAP} of ${total} — open a subfolder to narrow down</p>`;
    }
    crumb.innerHTML += total ? ` <span style="color:#58a6ff">· ${total} photos below</span>` : '';
  } else {
    html += ffiles.map(f=>{
      const n=f.p.split('/').pop(), e=ext(n);
      return `<a class="row" href="#" data-open="${esc(f.p)}"><span class="ico">${ICON[e]||'📄'}</span><span class="nm">${esc(n)}</span><span class="meta">${fmt(f.s||0)}</span></a>`;
    }).join('');
  }
  if(!html) html='<p style="color:#8b949e">Nothing here.</p>';
  out.innerHTML=html;
  status.textContent = `${fdirs.length} folders · ${ffiles.length} files`;
}
document.addEventListener('click', e=>{
  if(e.target.id==='vclose' || e.target.id==='vimg'){ document.getElementById('viewer').classList.remove('on'); return; }
  const go=e.target.closest('[data-go]');
  if(go){ e.preventDefault(); CUR=go.dataset.go; Q=''; document.getElementById('search').value=''; render(); window.scrollTo(0,0); return; }
  const op=e.target.closest('[data-open]');
  if(op){ e.preventDefault();
    const p=op.dataset.open;
    document.getElementById('vname').textContent=p.split('/').pop();
    document.getElementById('vraw').href='/full?p='+encodeURIComponent(p);
    document.getElementById('vimg').src='/full?p='+encodeURIComponent(p);
    document.getElementById('viewer').classList.add('on'); }
});
document.getElementById('search').addEventListener('input', e=>{Q=e.target.value; render();});
document.querySelectorAll('.tab').forEach(t=>t.addEventListener('click',()=>{
  document.querySelectorAll('.tab').forEach(x=>x.classList.remove('on'));
  t.classList.add('on'); MODE=t.dataset.mode; render();
}));
fetch('/data.json').then(r=>r.json()).then(d=>{
  DATA=d;
  const files=d.filter(x=>!x.d);
  document.getElementById('stats').textContent=`${files.length} files · ${d.filter(x=>!x.d&&IMG.test(x.p)).length} photos · ${(files.reduce((a,b)=>a+(b.s||0),0)/1e9).toFixed(1)} GB`;
  render();
}).catch(e=>{status.textContent='load error: '+e;});
</script></body></html>"""


class Gallery:
    """Stateful server: inventory cache, thumbnail cache, optional Basic auth."""

    def __init__(self, root: Path, port: int = 8451, cache_dir: Path | None = None,
                 title: str = "Library", user: str = "", password: str = "",
                 host: str = "127.0.0.1", thumb_px: int = 400):
        self.root = root.resolve()
        self.port = port
        self.host = host
        self.title = title
        self.user = user
        self.password = password
        self.thumb_px = thumb_px
        self.thumbs = (cache_dir or Path.home() / ".cache" / "library-tidy" / "thumbs")
        self._inv = None
        self._inv_at = 0.0
        self._lock = threading.Lock()

    # -- data ------------------------------------------------------------
    def inventory(self) -> list[dict]:
        if self._inv is not None and time.time() - self._inv_at < 300:
            return self._inv
        items = []
        for p in self.root.rglob("*"):
            try:
                st = p.stat()
            except OSError:
                continue
            rel = p.relative_to(self.root).as_posix()
            items.append({"p": rel, "d": 1} if p.is_dir()
                         else {"p": rel, "s": st.st_size, "m": int(st.st_mtime)})
        self._inv, self._inv_at = items, time.time()
        return items

    def resolve(self, rel: str) -> Path:
        rel = urllib.parse.unquote(rel or "").lstrip("/")
        target = (self.root / rel).resolve()
        if target != self.root and self.root not in target.parents:
            raise PermissionError("outside root")
        return target

    def thumb(self, path: Path) -> Path:
        key = hashlib.sha1(str(path).encode()).hexdigest()
        out = self.thumbs / f"{key}.jpg"
        if out.exists() and out.stat().st_mtime >= path.stat().st_mtime:
            return out
        from PIL import Image, ImageOps
        with self._lock:
            if out.exists() and out.stat().st_mtime >= path.stat().st_mtime:
                return out
            out.parent.mkdir(parents=True, exist_ok=True)
            tmp = out.with_suffix(".tmp")
            with Image.open(path) as im:
                im = ImageOps.exif_transpose(im)
                if im.mode not in ("RGB", "L"):
                    im = im.convert("RGB")
                im.thumbnail((self.thumb_px, self.thumb_px), Image.LANCZOS)
                im.save(tmp, "JPEG", quality=82, optimize=True)
            os.replace(tmp, out)
        return out

    # -- serving ---------------------------------------------------------
    def handler(self):
        gallery = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "library-tidy/0.1"

            def log_message(self, fmt, *args):
                pass

            def _auth_ok(self) -> bool:
                if not gallery.password:
                    return True
                hdr = self.headers.get("Authorization", "")
                if not hdr.startswith("Basic "):
                    return False
                try:
                    u, p = base64.b64decode(hdr[6:]).decode().split(":", 1)
                except Exception:
                    return False
                return u == gallery.user and p == gallery.password

            def _send(self, body: bytes, ctype: str, status: int = 200, cache: str | None = None):
                self.send_response(status)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                if cache:
                    self.send_header("Cache-Control", cache)
                self.end_headers()
                if self.command != "HEAD":
                    self.wfile.write(body)

            def do_HEAD(self):
                self.do_GET()

            def do_GET(self):
                if not self._auth_ok():
                    self.send_response(401)
                    self.send_header("WWW-Authenticate", 'Basic realm="Library"')
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                parsed = urllib.parse.urlparse(self.path)
                qs = urllib.parse.parse_qs(parsed.query)
                try:
                    if parsed.path == "/":
                        return self._send(PAGE.replace("__TITLE__", gallery.title).encode(),
                                          "text/html; charset=utf-8", cache="no-store")
                    if parsed.path == "/data.json":
                        body = json.dumps(gallery.inventory(), separators=(",", ":")).encode()
                        return self._send(body, "application/json", cache="no-store")
                    if parsed.path in ("/thumb", "/full"):
                        target = gallery.resolve(qs.get("p", [""])[0])
                        if not target.is_file():
                            return self._send(b"not found", "text/plain", 404)
                        if parsed.path == "/thumb":
                            if target.suffix.lower() in NO_PREVIEW:
                                return self._send(b"no preview", "text/plain", 415)
                            try:
                                tp = gallery.thumb(target)
                            except Exception as exc:
                                return self._send(f"thumb error: {exc}".encode(), "text/plain", 500)
                            return self._send(tp.read_bytes(), "image/jpeg", cache="private, max-age=3600")
                        return self._send_file(target)
                    return self._send(b"not found", "text/plain", 404)
                except PermissionError:
                    return self._send(b"forbidden", "text/plain", 403)
                except BrokenPipeError:
                    return

            def _send_file(self, target: Path):
                ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
                size = target.stat().st_size
                rng = self.headers.get("Range", "")
                if rng.startswith("bytes="):
                    m = re.match(r"bytes=(\d*)-(\d*)", rng)
                    start = int(m.group(1) or 0)
                    end = int(m.group(2)) if m.group(2) else size - 1
                    end = min(end, size - 1)
                    length = max(0, end - start + 1)
                    self.send_response(206)
                    self.send_header("Content-Type", ctype)
                    self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
                    self.send_header("Accept-Ranges", "bytes")
                    self.send_header("Content-Length", str(length))
                    self.end_headers()
                    with target.open("rb") as f:
                        f.seek(start)
                        left = length
                        while left > 0:
                            chunk = f.read(min(1 << 16, left))
                            if not chunk:
                                break
                            self.wfile.write(chunk)
                            left -= len(chunk)
                    return
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(size))
                self.send_header("Accept-Ranges", "bytes")
                self.end_headers()
                with target.open("rb") as f:
                    shutil.copyfileobj(f, self.wfile, 1 << 16)

        return Handler

    def serve_forever(self):
        self.thumbs.mkdir(parents=True, exist_ok=True)
        srv = ThreadingHTTPServer((self.host, self.port), self.handler())
        print(f"gallery: http://{self.host}:{self.port} → {self.root}")
        srv.serve_forever()
