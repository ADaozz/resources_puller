"""Isolated yt-dlp process: private cookie files, bounded output, cancellation.

The executable inherits the environment's HTTPS proxy and CA trust. Browser
cookies are exported for this operation only and removed on every exit path.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import signal
import sys
import tempfile
from pathlib import Path
from typing import Callable

from app.security.ssrf import assert_safe


def cookie_file(path: Path, cookies: list[dict]) -> None:
    path.touch(mode=0o600)
    lines = ["# Netscape HTTP Cookie File"]
    for cookie in cookies:
        fields = [
            cookie.get("domain", ""),
            "TRUE" if cookie.get("domain", "").startswith(".") else "FALSE",
            cookie.get("path", "/"),
            "TRUE" if cookie.get("secure") else "FALSE",
            str(max(0, int(cookie.get("expires", 0)))),
            cookie.get("name", ""),
            cookie.get("value", ""),
        ]
        if any("\n" in value or "\r" in value or "\t" in value for value in fields):
            continue
        lines.append("\t".join(fields))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def public_error(text: str) -> str:
    # Never expose signed URLs or raw extractor output (which may include cookies).
    lowered = text.lower()
    messages = {
        "403": "上游拒绝访问（403）；请检查网络出口或在浏览器中重新打开视频",
        "sign in": "视频要求登录或人机验证，请先在当前浏览器中完成验证后重试",
        "private video": "该视频为私有视频，当前会话无权访问",
        "not available": "视频在当前地区或会话中不可用",
        "requested format": "所选清晰度已不可用，请重新打开页面获取资源",
        "timed out": "视频站点请求超时，请稍后重试",
    }
    return next((message for key, message in messages.items() if key in lowered),
                "视频解析或下载失败，请检查站点是否可播放后重试")


class Extractor:
    def arguments(self, cookie_path: Path) -> list[str]:
        return [sys.executable, "-m", "yt_dlp", "--ignore-config", "--no-playlist",
                "--no-cache-dir", "--no-warnings", "--socket-timeout", "15",
                "--retries", "2", "--fragment-retries", "2", "--js-runtimes", "node",
                "--cookies", str(cookie_path)]

    async def run(self, args: list[str], *, timeout: float,
                  progress: Callable[[float], None] | None = None,
                  stage: Callable[[str], None] | None = None) -> bytes:
        proc = await asyncio.create_subprocess_exec(
            *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            start_new_session=True, limit=32 * 1024 * 1024,
        )

        async def stderr_tail() -> bytes:
            tail = b""
            while chunk := await proc.stderr.read(4096):
                tail = (tail + chunk)[-8192:]
            return tail

        async def stdout_data() -> bytes:
            data = bytearray()
            while line := await proc.stdout.readline():
                if progress:
                    matched = re.search(rb"rp-progress:\s*([\d.]+)%", line)
                    if matched:
                        progress(float(matched[1]))
                    if stage and b"rp-merging" in line:
                        stage("MERGING")
                else:
                    data.extend(line)
                    if len(data) > 32 * 1024 * 1024:
                        raise RuntimeError("视频元数据过大")
            return bytes(data)

        async def collect():
            out, err = await asyncio.gather(stdout_data(), stderr_tail())
            code = await proc.wait()
            if code:
                raise RuntimeError(public_error(err.decode("utf-8", "replace")))
            return out

        try:
            return await asyncio.wait_for(collect(), timeout)
        except asyncio.TimeoutError as exc:
            raise RuntimeError("视频站点请求超时，请稍后重试") from exc
        finally:
            # ffmpeg is a descendant; cancellation must stop it as well as yt-dlp.
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            await proc.wait()

    async def inspect(self, url: str, cookies: list[dict]) -> dict:
        assert_safe(url)
        with tempfile.TemporaryDirectory(prefix="rp-extract-") as temp:
            path = Path(temp) / "cookies.txt"
            cookie_file(path, cookies)
            raw = await self.run(self.arguments(path) + ["--dump-single-json", "--skip-download", "--", url], timeout=90)
            return json.loads(raw)

    async def download(self, url: str, selector: str, dest: Path, cookies: list[dict],
                       progress: Callable[[float], None],
                       stage: Callable[[str], None] | None = None) -> None:
        assert_safe(url)
        with tempfile.TemporaryDirectory(prefix="rp-download-", dir=dest.parent) as temp:
            root = Path(temp)
            path = root / "cookies.txt"
            cookie_file(path, cookies)
            template = root / "media.%(ext)s"
            args = self.arguments(path) + [
                "--newline", "--progress", "--progress-template", "download:rp-progress:%(progress._percent_str)s",
                "--progress-template", "postprocess:rp-merging",
                "--format", selector, "--merge-output-format", "mp4", "--recode-video", "mp4",
                "--output", str(template), "--", url,
            ]
            await self.run(args, timeout=3600, progress=progress, stage=stage)
            output = root / "media.mp4"
            if not output.is_file() or output.stat().st_size == 0:
                raise RuntimeError("未生成完整的视频文件")
            output.replace(dest)
