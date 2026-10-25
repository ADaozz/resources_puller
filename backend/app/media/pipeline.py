from __future__ import annotations

import asyncio
import logging
import re
import time
import uuid
from pathlib import Path

from app.config import data_dir
from app.hub import Hub
from app.media.backends import BackendRegistry, DownloadPlan, plan_for
from app.media.extractor import Extractor
from app.media.fetch import open_response, page_request_headers
from app.resources.classify import extension_of
from app.resources.store import Store
from app.security.ssrf import SSRFError, assert_safe

log = logging.getLogger(__name__)

_TIME = re.compile(br"time=(\d+):(\d+):(\d+(?:\.\d+)?)")


class DownloadManager:
    def __init__(self, store: Store, hub: Hub) -> None:
        self.store = store
        self.hub = hub
        self._tasks: dict[str, asyncio.Task] = {}
        self._cancelled: set[str] = set()
        self._referers: dict[str, str] = {}
        self._plans: dict[str, DownloadPlan] = {}
        self._preview_jobs: dict[tuple[str, str], str] = {}
        self.extractor = Extractor()
        self.cookie_provider = None
        self.backends = BackendRegistry()
        self.backends.register("http", self._http_backend)
        self.backends.register("playlist", self._playlist_backend)
        self.backends.register("extractor", self._extractor_backend)

    def session_dir(self, session_id: str) -> Path:
        path = data_dir() / "downloads" / session_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def enqueue(
        self,
        session_id: str,
        variant: dict,
        rendition_index: int | None = None,
        referer: str | None = None,
    ) -> dict:
        plan = plan_for(variant)
        key = (session_id, variant["id"])
        if plan.metadata.get("file_preview"):
            previous = self._preview_jobs.get(key)
            cached = self.store.get_download(session_id, previous) if previous else None
            if cached and (cached["id"] in self._tasks or
                           (cached["state"] == "COMPLETED" and Path(cached["path"]).is_file())):
                return cached
        filename = safe_filename(variant.get("group_title") or variant.get("label") or "media", variant["url"])
        if plan.metadata.get("ext"):
            filename = _split_ext(filename)[0] + "." + plan.metadata["ext"]
        if rendition_index is not None:
            stem, ext = _split_ext(filename)
            filename = f"{stem}-r{rendition_index}{ext}"
        job_id = uuid.uuid4().hex
        if referer:
            self._referers[job_id] = referer
        self._plans[job_id] = plan
        job = self.store.insert_download(
            job_id=job_id,
            session_id=session_id,
            variant_id=variant["id"],
            rendition_index=rendition_index,
            url=variant["url"],
            filename=filename,
        )
        task = asyncio.create_task(self._run(job))
        self._tasks[job_id] = task
        if plan.metadata.get("file_preview"):
            self._preview_jobs[key] = job_id
        return job

    async def cancel(self, session_id: str, job_id: str) -> dict | None:
        job = self.store.get_download(session_id, job_id)
        if not job:
            return None
        if job["state"] in {"COMPLETED", "FAILED", "CANCELLED"}:
            return job
        self._cancelled.add(job_id)
        task = self._tasks.get(job_id)
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        current = self.store.get_download(session_id, job_id)
        if current and current["state"] not in {"COMPLETED", "FAILED", "CANCELLED"}:
            self._discard_partial(current)
            current = self.store.update_download(
                session_id, job_id, state="CANCELLED", error="已取消"
            )
        return current

    async def cancel_session(self, session_id: str) -> None:
        for key in list(self._preview_jobs):
            if key[0] == session_id:
                self._preview_jobs.pop(key, None)
        jobs = self.store.list_downloads(session_id)
        for job in jobs:
            if job["state"] not in {"COMPLETED", "FAILED", "CANCELLED"}:
                await self.cancel(session_id, job["id"])

    async def _run(self, job: dict) -> None:
        session_id = job["session_id"]
        job_id = job["id"]
        try:
            plan = self._plans[job_id]
            assert_safe(plan.url)
            await self.backends.execute(job, plan)
        except asyncio.CancelledError:
            self._discard_partial(self.store.get_download(session_id, job_id) or job)
            self.store.update_download(session_id, job_id, state="CANCELLED", error="已取消")
            raise
        except Exception as exc:
            log.info("下载失败 %s: %s", job_id, exc)
            current = self.store.get_download(session_id, job_id) or job
            self._discard_partial(current)
            self.store.update_download(
                session_id,
                job_id,
                state="FAILED",
                error=str(exc)[:500],
                path=None,
            )
        finally:
            self._tasks.pop(job_id, None)
            self._referers.pop(job_id, None)
            self._plans.pop(job_id, None)
            self._cancelled.discard(job_id)

    async def _http_backend(self, job: dict, plan: DownloadPlan) -> None:
        await self._run_http({**job, "url": plan.url})

    async def _playlist_backend(self, job: dict, plan: DownloadPlan) -> None:
        await self._run_ffmpeg({**job, "url": plan.url})

    async def _extractor_backend(self, job: dict, plan: DownloadPlan) -> None:
        sid, job_id = job["session_id"], job["id"]
        cookies = await self.cookie_provider(sid, plan.url) if self.cookie_provider else []
        path = self.session_dir(sid) / f"{job_id}-{job['filename']}"
        self.store.update_download(sid, job_id, state="DOWNLOADING", progress=2, path=str(path))
        # Each component has its own percentage; keep the visible job monotonic.
        last_progress = 2.0
        def progress(value: float) -> None:
            nonlocal last_progress
            next_progress = min(95.0, max(last_progress, value * 0.9))
            if next_progress - last_progress >= 1:
                last_progress = next_progress
                self.store.update_download(sid, job_id, state="DOWNLOADING", progress=round(next_progress, 1))
        def stage(state: str) -> None:
            self.store.update_download(sid, job_id, state=state, progress=max(95, last_progress))

        await self.extractor.download(plan.url, plan.metadata["selector"], path, cookies, progress, stage)
        self.store.update_download(sid, job_id, state="COMPLETED", progress=100, path=str(path), error=None)

    async def preview_file(self, session_id: str, variant: dict) -> Path | None:
        if not variant.get("metadata", {}).get("file_preview"):
            return None
        key = (session_id, variant["id"])
        job_id = self._preview_jobs.get(key)
        job = self.store.get_download(session_id, job_id) if job_id else None
        if not job or job["state"] in {"FAILED", "CANCELLED"}:
            job = self.enqueue(session_id, variant)
            self._preview_jobs[key] = job["id"]
        task = self._tasks.get(job["id"])
        if task:
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                if task.cancelled():
                    raise RuntimeError("预览下载已取消") from None
                raise
        current = self.store.get_download(session_id, job["id"])
        if not current or current["state"] != "COMPLETED":
            raise RuntimeError((current or {}).get("error") or "预览文件未就绪")
        path = Path(current["path"])
        if not path.is_file():
            self._preview_jobs.pop(key, None)
            raise RuntimeError("预览文件已清理，请重试")
        return path

    def _saved_headers(self, job_id: str) -> dict[str, str]:
        return page_request_headers(self._referers.get(job_id))

    async def _run_http(self, job: dict) -> None:
        session_id = job["session_id"]
        job_id = job["id"]
        self.store.update_download(session_id, job_id, state="PROBING", progress=1)
        client, response, _final = await open_response(job["url"], headers=self._saved_headers(job_id))
        path = self.session_dir(session_id) / f"{job_id}-{job['filename']}"
        try:
            if response.status_code >= 400:
                body = (await response.aread())[:300]
                raise RuntimeError(
                    f"HTTP {response.status_code} {body.decode('utf-8', errors='replace')}"
                )
            total = int(response.headers.get("content-length") or 0)
            self.store.update_download(session_id, job_id, state="DOWNLOADING", progress=5, path=str(path))
            done = 0
            last_emit = 0.0
            with path.open("wb") as handle:
                async for chunk in response.aiter_bytes(64 * 1024):
                    if job_id in self._cancelled:
                        raise asyncio.CancelledError()
                    handle.write(chunk)
                    done += len(chunk)
                    now = time.monotonic()
                    if now - last_emit > 0.3:
                        progress = (done / total * 100.0) if total else min(95.0, 5 + done / 100_000)
                        self.store.update_download(
                            session_id, job_id, state="DOWNLOADING", progress=round(progress, 1)
                        )
                        last_emit = now
            self.store.update_download(
                session_id, job_id, state="COMPLETED", progress=100, path=str(path), error=None
            )
        finally:
            await response.aclose()
            await client.aclose()

    async def _run_ffmpeg(self, job: dict) -> None:
        session_id = job["session_id"]
        job_id = job["id"]
        self.store.update_download(session_id, job_id, state="PROBING", progress=2)
        duration = await _probe_duration(job["url"])
        path = self.session_dir(session_id) / f"{job_id}-{_force_mp4(job['filename'])}"
        self.store.update_download(session_id, job_id, state="MERGING", progress=8, path=str(path))
        header_args = []
        saved = self._saved_headers(job_id)
        if saved:
            header_args = ["-headers", "".join(f"{key}: {value}\r\n" for key, value in saved.items())]
        cmd = [
            "ffmpeg",
            "-y",
            *header_args,
            "-allowed_extensions",
            "ALL",
            "-i",
            job["url"],
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-movflags",
            "+faststart",
            str(path),
        ]
        index = job.get("rendition_index")
        if isinstance(index, int):
            cmd[cmd.index("-c:v"):cmd.index("-c:v")] = ["-map", f"0:v:{index}"]
        try:
            await self._exec_ffmpeg(job, cmd, path, duration)
        except RuntimeError:
            if job_id in self._cancelled:
                raise
            if path.exists():
                path.unlink()
            fallback = [
                "ffmpeg",
                "-y",
                *header_args,
                "-allowed_extensions",
                "ALL",
                "-i",
                job["url"],
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-c:a",
                "aac",
                "-movflags",
                "+faststart",
                str(path),
            ]
            self.store.update_download(session_id, job_id, state="MERGING", progress=12, error=None)
            await self._exec_ffmpeg(job, fallback, path, duration)

    async def _exec_ffmpeg(self, job: dict, cmd: list[str], path: Path, duration: float | None) -> None:
        session_id = job["session_id"]
        job_id = job["id"]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        stderr_tail = bytearray()
        assert proc.stderr is not None
        buf = b""
        last_emit = 0.0
        try:
            while True:
                if job_id in self._cancelled:
                    proc.kill()
                    raise asyncio.CancelledError()
                chunk = await proc.stderr.read(512)
                if not chunk:
                    break
                stderr_tail.extend(chunk[-4000:])
                buf += chunk
                parts = re.split(br"[\r\n]", buf)
                buf = parts.pop() if parts else b""
                for part in parts:
                    matched = _TIME.search(part)
                    if not matched or not duration:
                        continue
                    seconds = (
                        int(matched.group(1)) * 3600
                        + int(matched.group(2)) * 60
                        + float(matched.group(3))
                    )
                    progress = min(99.0, max(8.0, seconds / duration * 100.0))
                    now = time.monotonic()
                    if now - last_emit > 0.3:
                        self.store.update_download(
                            session_id, job_id, state="MERGING", progress=round(progress, 1)
                        )
                        last_emit = now
            code = await proc.wait()
        finally:
            if proc.returncode is None:
                proc.kill()
                await proc.wait()
        if code != 0:
            tail = bytes(stderr_tail[-500:]).decode("utf-8", errors="replace").strip()
            raise RuntimeError(tail or f"ffmpeg 退出码 {code}")
        self.store.update_download(
            session_id, job_id, state="COMPLETED", progress=100, path=str(path), error=None
        )

    def _discard_partial(self, job: dict) -> None:
        raw = job.get("path")
        if not raw:
            return
        path = Path(raw)
        if path.exists():
            path.unlink(missing_ok=True)


def safe_filename(title: str, url: str) -> str:
    ext = extension_of(url) or ".bin"
    if ext in {".m3u8", ".m3u", ".mpd"}:
        ext = ".mp4"
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", title).strip("._")[:80] or "media"
    return f"{stem}{ext}"


def _split_ext(filename: str) -> tuple[str, str]:
    if "." not in filename:
        return filename, ""
    stem, ext = filename.rsplit(".", 1)
    return stem, "." + ext


def _force_mp4(filename: str) -> str:
    stem, _ext = _split_ext(filename)
    return f"{stem}.mp4"


async def _probe_duration(url: str) -> float | None:
    proc = await asyncio.create_subprocess_exec(
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=nw=1:nk=1",
        url,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        stdout, _stderr = await asyncio.wait_for(proc.communicate(), timeout=25)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return None
    try:
        value = float((stdout or b"").strip() or 0)
    except ValueError:
        return None
    return value or None


async def ensure_poster(
    url: str,
    variant_id: str,
    headers: dict[str, str] | None = None,
) -> Path | None:
    try:
        assert_safe(url)
    except SSRFError:
        return None
    dest = data_dir() / "posters" / f"{variant_id}.jpg"
    if dest.exists() and dest.stat().st_size > 0:
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    header_args: list[str] = []
    if headers:
        header_args = ["-headers", "".join(f"{key}: {value}\r\n" for key, value in headers.items())]
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg",
        "-y",
        *header_args,
        "-ss",
        "0.2",
        "-i",
        url,
        "-frames:v",
        "1",
        "-vf",
        "scale=320:-1",
        str(dest),
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        code = await asyncio.wait_for(proc.wait(), timeout=25)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return None
    if code != 0 or not dest.exists() or dest.stat().st_size == 0:
        dest.unlink(missing_ok=True)
        return None
    return dest
