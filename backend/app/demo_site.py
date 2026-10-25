from __future__ import annotations

import asyncio
import hashlib
import hmac
import time
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, Response, StreamingResponse

from app.config import demo_media_dir, self_ports, sign_secret

router = APIRouter()


def _media_root() -> Path:
    return demo_media_dir().resolve()


def _safe_media(relative: str) -> Path:
    root = _media_root()
    path = (root / relative).resolve()
    if path != root and root not in path.parents:
        raise HTTPException(404, "文件不存在")
    if not path.is_file():
        raise HTTPException(404, "文件不存在")
    return path


def _resolve_media(relative: str) -> Path:
    try:
        return _safe_media(relative)
    except HTTPException:
        base = relative.split("@", 1)[0]
        if base and base != relative:
            return _safe_media(base)
        raise


def sign_name(name: str, exp: int) -> str:
    message = f"{name}|{exp}".encode()
    return hmac.new(sign_secret(), message, hashlib.sha256).hexdigest()


def local_media_file(url: str) -> Path | None:
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    port = parts.port or (443 if parts.scheme == "https" else 80)
    if host not in {"127.0.0.1", "localhost"} or port not in self_ports():
        return None
    prefix = "/demo/media/"
    if not parts.path.startswith(prefix):
        return None
    try:
        return _resolve_media(unquote(parts.path[len(prefix) :]))
    except HTTPException:
        return None


def signed_path(name: str, exp: int) -> str:
    signature = sign_name(name, exp)
    return f"/demo/signed/{quote(name)}?exp={exp}&sig={signature}"


def _page(valid: str, expired: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <title>资源解析测试页</title>
  <style>
    body {{ margin: 0; font-family: "Noto Sans CJK SC", "Noto Sans SC", sans-serif; background: #f4f1ea; color: #1d1a16; }}
    header {{ position: sticky; top: 0; background: #1d1a16; color: #f4f1ea; padding: 10px 12px; z-index: 2; }}
    button {{ font: inherit; background: #d6ff4a; border: 0; padding: 10px 14px; cursor: pointer; }}
    main {{ padding: 16px; display: grid; gap: 16px; }}
    section {{ background: white; padding: 12px; border: 1px solid #ddd4c6; }}
    img, video {{ max-width: 240px; margin: 6px; vertical-align: top; background: #eee; }}
    h1 {{ font-size: 18px; margin: 0 0 8px; }}
    h2 {{ font-size: 15px; margin: 0 0 8px; }}
  </style>
</head>
<body>
  <header>
    <button id="load-hidden" type="button">点击加载隐藏媒体</button>
    <span id="marker">尚未点击</span>
  </header>
  <main>
    <section>
      <h1>静态图与重复请求</h1>
      <img alt="logo-a" src="/demo/media/logo.png" />
      <img alt="logo-b" src="/demo/media/logo.png" />
    </section>
    <section>
      <h2>多分辨率</h2>
      <img alt="360" src="/demo/media/coast_640x360.jpg" />
      <img alt="720" src="/demo/media/coast_1280x720.jpg" />
      <img alt="1080" src="/demo/media/coast_1920x1080.jpg" />
    </section>
    <section>
      <h2>同一名称的处理后缀</h2>
      <img alt="banner-wide" src="/demo/media/banner.jpg@3840w_360h_1c.avif" />
      <img alt="banner-cover" src="/demo/media/banner.jpg@672w_378h_1c_!web-home-common-cover.webp" />
      <img alt="banner-mid" src="/demo/media/banner.jpg@1280w_720h_1c" />
    </section>
    <section>
      <h2>不同目录同名</h2>
      <img alt="cover-a" src="/demo/media/a/cover.jpg" />
      <img alt="cover-b" src="/demo/media/b/cover.jpg" />
    </section>
    <section>
      <h2>签名链</h2>
      <img id="signed-ok" alt="signed" src="{valid}" />
      <img id="signed-expired" alt="expired" src="{expired}" />
    </section>
    <section>
      <h2>懒加载与动态视频</h2>
      <img id="lazy" alt="lazy" data-src="/demo/media/lazy.jpg" />
      <video id="dyn" preload="none" controls muted></video>
    </section>
  </main>
  <script>
    window.__rpClicked = false;
    const button = document.getElementById("load-hidden");
    button.addEventListener("click", () => {{
      window.__rpClicked = true;
      document.getElementById("marker").textContent = "已点击";
      const lazy = document.getElementById("lazy");
      lazy.src = lazy.dataset.src;
      const video = document.getElementById("dyn");
      video.src = "/demo/media/clip.mp4";
      video.play().catch(() => {{}});
      const extra = document.createElement("img");
      extra.alt = "clicked";
      extra.src = "/demo/media/clicked.png";
      document.querySelector("main").appendChild(extra);
    }});
    async function loadLists() {{
      const master = await fetch("/demo/media/hls/master.m3u8").then((r) => r.text());
      const variantLine = master.split(/\\n/).map((line) => line.trim()).find((line) => line && !line.startsWith("#"));
      if (variantLine) {{
        const variantUrl = new URL(variantLine, location.origin + "/demo/media/hls/master.m3u8");
        const media = await fetch(variantUrl).then((r) => r.text());
        const segment = media.split(/\\n/).map((line) => line.trim()).find((line) => line && !line.startsWith("#"));
        if (segment) await fetch(new URL(segment, variantUrl));
      }}
      await fetch("/demo/media/dash/manifest.mpd");
      await fetch("/demo/media/logo.png", {{ cache: "reload" }});
      await fetch("/demo/media/logo.png", {{ cache: "reload" }});
    }}
    fetch("http://127.0.0.1:22/").catch(() => {{}});
    fetch("http://169.254.169.254/latest/meta-data/").catch(() => {{}});
    loadLists().catch((error) => console.error(error));
  </script>
</body>
</html>
"""


@router.get("/demo", response_class=HTMLResponse)
@router.get("/demo/", response_class=HTMLResponse)
def demo_index() -> HTMLResponse:
    now = int(time.time())
    valid = signed_path("signed_photo.jpg", now + 3600)
    expired = signed_path("signed_photo.jpg", 1)
    return HTMLResponse(_page(valid, expired))


@router.get("/demo/slow-page", response_class=HTMLResponse)
def slow_page() -> HTMLResponse:
    html = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8"><title>慢速视频</title></head>
<body><video id="slow" src="/demo/slow/clip.mp4" preload="metadata" controls></video></body></html>"""
    return HTMLResponse(html)


@router.get("/demo/media/{path:path}")
def demo_media(path: str) -> FileResponse:
    if path.startswith("slow"):
        raise HTTPException(404, "文件不存在")
    return FileResponse(_resolve_media(path))


@router.get("/demo/signed/{name}")
def demo_signed(name: str, exp: int, sig: str) -> FileResponse:
    if "/" in name or "\\" in name or name.startswith("."):
        raise HTTPException(404, "文件不存在")
    expected = sign_name(name, exp)
    if not hmac.compare_digest(expected, sig) or exp < int(time.time()):
        raise HTTPException(403, "签名已失效")
    return FileResponse(_safe_media(name))


@router.api_route("/demo/slow/clip.mp4", methods=["GET", "HEAD"])
async def demo_slow(request: Request):
    source = _safe_media("slow_src.mp4")
    payload = source.read_bytes() * 30
    if request.method == "HEAD":
        return Response(
            status_code=200,
            headers={
                "Content-Length": str(len(payload)),
                "Accept-Ranges": "bytes",
                "Content-Type": "video/mp4",
            },
        )

    async def chunks():
        step = 8 * 1024
        for index in range(0, len(payload), step):
            yield payload[index : index + step]
            await asyncio.sleep(0.25)

    return StreamingResponse(
        chunks(),
        media_type="video/mp4",
        headers={"Content-Length": str(len(payload)), "Accept-Ranges": "bytes"},
    )
