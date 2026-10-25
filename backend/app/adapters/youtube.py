from __future__ import annotations

import re
from urllib.parse import parse_qs, urlsplit

from app.adapters.base import MediaResource
from app.adapters.extracted import ExtractedVideoAdapter

_VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com", "www.youtube-nocookie.com", "youtube-nocookie.com"}


class YouTubeAdapter(ExtractedVideoAdapter):
    name = "youtube"
    display_name = "YouTube"

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
