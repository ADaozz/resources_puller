from __future__ import annotations

import mimetypes
import os
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, Response, StreamingResponse
from pydantic import BaseModel, Field

from app.browser.manager import descendant_has_chromium
from app.demo_site import local_media_file
from app.media.fetch import open_response, read_text
from app.media.pipeline import ensure_poster
from app.media.rewrite import rewrite_m3u8
from app.security.ssrf import SSRFError, assert_safe

router = APIRouter()


class NavigateIn(BaseModel):
    url: str


class DownloadItem(BaseModel):
    variant_id: str
    rendition_index: int | None = None


class DownloadIn(BaseModel):
    items: list[DownloadItem] = Field(min_length=1)


def _browser(request: Request):
    return request.app.state.browser


def _store(request: Request):
    return request.app.state.store


def _downloads(request: Request):
    return request.app.state.downloads


def _session_or_404(request: Request, session_id: str):
    browser = _browser(request)
    if not browser.session or browser.session.id != session_id:
        raise HTTPException(404, "会话不存在")
    return browser


@router.get("/api/health")
def health(request: Request) -> dict:
    browser = _browser(request)
    pid = browser.browser_pid
    return {
        "ok": browser.browser is not None and not browser.start_error,
        "browser_pid": pid,
        "launch_count": browser.launch_count,
        "display": os.environ.get("DISPLAY"),
        "session_id": browser.session.id if browser.session else None,
        "chromium_in_tree": descendant_has_chromium(pid) if pid else False,
        "start_error": browser.start_error,
    }


@router.post("/api/sessions")
async def create_session(request: Request) -> dict:
    browser = _browser(request)
    try:
        return await browser.create_session()
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc


@router.get("/api/sessions/{session_id}")
def get_session(session_id: str, request: Request) -> dict:
    browser = _session_or_404(request, session_id)
    return browser.describe()


@router.delete("/api/sessions/{session_id}")
async def delete_session(session_id: str, request: Request) -> dict:
    browser = _browser(request)
    try:
        await browser.close_session(session_id)
    except KeyError as exc:
        raise HTTPException(404, "会话不存在") from exc
    return {"ok": True}


@router.post("/api/sessions/{session_id}/navigate")
async def navigate(session_id: str, body: NavigateIn, request: Request) -> dict:
    browser = _session_or_404(request, session_id)
    try:
        assert_safe(body.url)
        current = await browser.navigate(session_id, body.url)
    except SSRFError as exc:
        raise HTTPException(400, str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(404, "会话不存在") from exc
    except Exception as exc:
        raise HTTPException(502, f"打开页面失败：{exc}") from exc
    return {"url": current}


@router.post("/api/sessions/{session_id}/back")
async def back(session_id: str, request: Request) -> dict:
    return await _move(session_id, request, "back")


@router.post("/api/sessions/{session_id}/forward")
async def forward(session_id: str, request: Request) -> dict:
    return await _move(session_id, request, "forward")


@router.post("/api/sessions/{session_id}/reload")
async def reload(session_id: str, request: Request) -> dict:
    return await _move(session_id, request, "reload")


async def _move(session_id: str, request: Request, action: str) -> dict:
    browser = _session_or_404(request, session_id)
    try:
        current = await getattr(browser, action)(session_id)
    except Exception as exc:
        raise HTTPException(400, f"无法{action}：{exc}") from exc
    return {"url": current}


@router.get("/api/sessions/{session_id}/resources")
def resources(session_id: str, request: Request) -> dict:
    _session_or_404(request, session_id)
    return {"groups": _store(request).list_groups(session_id)}


@router.get("/api/sessions/{session_id}/downloads")
def downloads(session_id: str, request: Request) -> dict:
    _session_or_404(request, session_id)
    return {"downloads": _store(request).list_downloads(session_id)}


@router.post("/api/sessions/{session_id}/downloads")
async def create_downloads(session_id: str, body: DownloadIn, request: Request) -> dict:
    _session_or_404(request, session_id)
    store = _store(request)
    manager = _downloads(request)
    created = []
    for item in body.items:
        variant = store.get_variant(session_id, item.variant_id)
        if not variant:
            raise HTTPException(404, f"找不到资源版本 {item.variant_id}")
        try:
            assert_safe(variant["url"])
        except SSRFError as exc:
            raise HTTPException(400, str(exc)) from exc
        created.append(manager.enqueue(session_id, variant, item.rendition_index))
    return {"downloads": created}


@router.post("/api/sessions/{session_id}/downloads/{job_id}/cancel")
async def cancel_download(session_id: str, job_id: str, request: Request) -> dict:
    _session_or_404(request, session_id)
    job = await _downloads(request).cancel(session_id, job_id)
    if not job:
        raise HTTPException(404, "下载任务不存在")
    return job


@router.get("/api/sessions/{session_id}/downloads/{job_id}/file")
def download_file(session_id: str, job_id: str, request: Request):
    _session_or_404(request, session_id)
    job = _store(request).get_download(session_id, job_id)
    if not job or job["state"] != "COMPLETED" or not job.get("path"):
        raise HTTPException(404, "文件尚未就绪")
    path = Path(job["path"])
    if not path.is_file():
        raise HTTPException(404, "文件不存在")
    return FileResponse(path, filename=job["filename"])


@router.get("/api/sessions/{session_id}/variants/{variant_id}/preview")
async def preview(session_id: str, variant_id: str, request: Request):
    _session_or_404(request, session_id)
    variant = _store(request).get_variant(session_id, variant_id)
    if not variant:
        raise HTTPException(404, "资源不存在")
    url = variant["url"]
    try:
        assert_safe(url)
    except SSRFError as exc:
        raise HTTPException(400, str(exc)) from exc
    if _is_m3u8(url, variant.get("mime")):
        text = _read_playlist(url)
        if text is None:
            text, _mime = await read_text(url)
        return Response(rewrite_m3u8(text, url, session_id), media_type="application/vnd.apple.mpegurl")
    if _is_mpd(url, variant.get("mime")):
        path = await _ensure_dash_mp4(url, variant_id)
        return FileResponse(path, media_type="video/mp4")
    local = local_media_file(url)
    if local:
        return FileResponse(local)
    return await _stream(url, request)


@router.get("/api/sessions/{session_id}/variants/{variant_id}/poster")
async def poster(session_id: str, variant_id: str, request: Request):
    _session_or_404(request, session_id)
    variant = _store(request).get_variant(session_id, variant_id)
    if not variant:
        raise HTTPException(404, "资源不存在")
    path = await ensure_poster(variant["url"], variant_id)
    if not path:
        raise HTTPException(404, "暂无封面")
    return FileResponse(path, media_type="image/jpeg")


@router.get("/api/sessions/{session_id}/media-proxy")
async def media_proxy(session_id: str, url: str, request: Request):
    _session_or_404(request, session_id)
    store = _store(request)
    try:
        assert_safe(url)
    except SSRFError as exc:
        raise HTTPException(400, str(exc)) from exc
    if not store.known_or_playlist_origin(session_id, url):
        raise HTTPException(403, "只代理已捕获的资源或其同源分片")
    if _is_m3u8(url, None):
        text = _read_playlist(url)
        if text is None:
            text, _mime = await read_text(url)
        return Response(rewrite_m3u8(text, url, session_id), media_type="application/vnd.apple.mpegurl")
    local = local_media_file(url)
    if local:
        return FileResponse(local)
    return await _stream(url, request)


@router.get("/api/sessions/{session_id}/debug")
async def debug(session_id: str, request: Request) -> dict:
    browser = _session_or_404(request, session_id)
    try:
        return await browser.debug(session_id)
    except Exception as exc:
        raise HTTPException(500, str(exc)) from exc


@router.websocket("/api/sessions/{session_id}/events")
async def events(websocket: WebSocket, session_id: str) -> None:
    browser = websocket.app.state.browser
    hub = websocket.app.state.hub
    if not browser.session or browser.session.id != session_id:
        await websocket.close(code=4404)
        return
    await websocket.accept()
    queue = hub.subscribe(session_id)
    try:
        await websocket.send_json(browser.snapshot(session_id))
        while True:
            event = await queue.get()
            await websocket.send_json(event)
    except WebSocketDisconnect:
        return
    finally:
        hub.unsubscribe(session_id, queue)


def _is_m3u8(url: str, mime: str | None) -> bool:
    path = url.split("?", 1)[0].lower()
    media = (mime or "").lower()
    return path.endswith(".m3u8") or "mpegurl" in media


def _is_mpd(url: str, mime: str | None) -> bool:
    path = url.split("?", 1)[0].lower()
    media = (mime or "").lower()
    return path.endswith(".mpd") or "dash+xml" in media


def _read_playlist(url: str) -> str | None:
    local = local_media_file(url)
    if local and local.is_file():
        return local.read_text(encoding="utf-8", errors="replace")
    return None


async def _ensure_dash_mp4(url: str, variant_id: str) -> Path:
    from app.config import data_dir

    dest = data_dir() / "cache" / f"{variant_id}.mp4"
    if dest.exists() and dest.stat().st_size > 0:
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    import asyncio

    proc = await asyncio.create_subprocess_exec(
        "ffmpeg",
        "-y",
        "-allowed_extensions",
        "ALL",
        "-i",
        url,
        "-c",
        "copy",
        "-movflags",
        "+faststart",
        str(dest),
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    _stdout, stderr = await proc.communicate()
    if proc.returncode != 0 or not dest.exists():
        dest.unlink(missing_ok=True)
        fallback = await asyncio.create_subprocess_exec(
            "ffmpeg",
            "-y",
            "-allowed_extensions",
            "ALL",
            "-i",
            url,
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-c:a",
            "aac",
            "-movflags",
            "+faststart",
            str(dest),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        _stdout, stderr = await fallback.communicate()
        if fallback.returncode != 0 or not dest.exists():
            detail = (stderr or b"")[-300:].decode("utf-8", errors="replace")
            raise HTTPException(502, f"DASH 预览失败：{detail}")
    return dest


async def _stream(url: str, request: Request) -> StreamingResponse:
    headers = {}
    if request.headers.get("range"):
        headers["Range"] = request.headers["range"]
    client, response, _final = await open_response(url, headers=headers)
    if response.status_code >= 400:
        body = (await response.aread())[:300]
        await response.aclose()
        await client.aclose()
        raise HTTPException(
            response.status_code,
            body.decode("utf-8", errors="replace") or "上游请求失败",
        )
    out_headers = {}
    for key in ("content-type", "content-length", "content-range", "accept-ranges"):
        if key in response.headers:
            out_headers[key] = response.headers[key]
    if "content-type" not in out_headers:
        guessed = mimetypes.guess_type(url.split("?", 1)[0])[0]
        if guessed:
            out_headers["content-type"] = guessed

    async def body():
        try:
            async for chunk in response.aiter_bytes(64 * 1024):
                yield chunk
        finally:
            await response.aclose()
            await client.aclose()

    return StreamingResponse(body(), status_code=response.status_code, headers=out_headers)
