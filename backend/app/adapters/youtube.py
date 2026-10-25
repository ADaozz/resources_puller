from __future__ import annotations

import re
from urllib.parse import parse_qs, urlsplit

from app.adapters.base import MediaResource
from app.media.extractor import Extractor

_VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
_FORMAT_ID = re.compile(r"^[A-Za-z0-9_-]+$")
_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com", "www.youtube-nocookie.com", "youtube-nocookie.com"}


class YouTubeAdapter:
    name = "youtube"

    def __init__(self, extractor: Extractor):
        self.extractor = extractor

    def page_key(self, url: str) -> str | None:
        parts = urlsplit(url)
        if parts.scheme not in {"http", "https"}:
            return None
        segments = parts.path.strip("/").split("/")
        candidates = {
            "watch": lambda: parse_qs(parts.query).get("v", [""])[0],
            "shorts": lambda: segments[1] if len(segments) > 1 else "",
            "embed": lambda: segments[1] if len(segments) > 1 else "",
            "live": lambda: segments[1] if len(segments) > 1 else "",
        }
        if parts.hostname == "youtu.be":
            video_id = segments[0]
        elif parts.hostname in _HOSTS:
            video_id = candidates.get(segments[0], lambda: "")()
        else:
            return None
        return video_id if _VIDEO_ID.fullmatch(video_id) else None

    def cookie_urls(self, url: str) -> list[str]:
        return ["https://www.youtube.com/"]

    def owns_media(self, url: str, page_url: str) -> bool:
        host = urlsplit(url).hostname or ""
        return (urlsplit(page_url).hostname in _HOSTS | {"youtu.be"}
                and (host == "googlevideo.com" or host.endswith(".googlevideo.com")))

    async def resolve(self, url: str, cookies: list[dict]) -> list[MediaResource]:
        video_id = self.page_key(url)
        if not video_id:
            return []
        canonical = f"https://www.youtube.com/watch?v={video_id}"
        info = await self.extractor.inspect(canonical, cookies)
        return self.resources(info, canonical, video_id)

    def resources(self, info: dict, page_url: str, video_id: str) -> list[MediaResource]:
        if info.get("is_live") or info.get("live_status") in {"is_live", "is_upcoming"}:
            raise ValueError("暂不支持录制 YouTube 直播，请选择已结束的视频")
        formats = [fmt for fmt in info.get("formats", []) if not fmt.get("has_drm")]
        audio = [fmt for fmt in formats if fmt.get("vcodec") == "none" and fmt.get("acodec") not in {None, "none"}]
        result = []
        for fmt in formats:
            format_id = str(fmt.get("format_id", ""))
            if (not _FORMAT_ID.fullmatch(format_id) or fmt.get("vcodec") in {None, "none"}
                    or not fmt.get("url") or fmt.get("protocol") in {"mhtml", "images"}):
                continue
            has_audio = fmt.get("acodec") not in {None, "none"}
            if not has_audio and not audio:
                continue  # Never advertise silent tracks as complete video downloads.
            selector = format_id if has_audio else f"{format_id}+bestaudio[ext=m4a]/{format_id}+bestaudio"
            height = fmt.get("height")
            label = f"{height}p" if height else str(fmt.get("format_note") or format_id)
            label += f" · {fmt.get('ext') or 'video'} · {format_id}"
            result.append(MediaResource(
                key=f"youtube:{video_id}:{format_id}", group_key=f"video|youtube|{video_id}",
                title=info.get("title") or video_id, url=fmt["url"], label=label,
                width=fmt.get("width"), height=height,
                metadata={"backend": "extractor", "adapter": self.name, "source_url": page_url,
                          "selector": selector, "format_id": format_id, "video_id": video_id,
                          "ext": "mp4", "file_preview": True, "thumbnail": info.get("thumbnail")},
            ))
        if not result:
            raise ValueError("没有可下载的完整音视频格式，请确认视频可播放并重新打开页面")
        return result
