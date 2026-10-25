from __future__ import annotations

import re
from urllib.parse import parse_qs, urlsplit

from app.adapters.base import MediaResource
from app.adapters.extracted import ExtractedVideoAdapter, host_matches

_ID = re.compile(r"[0-9]{10,25}")
_HOSTS = {"www.douyin.com", "douyin.com", "www.iesdouyin.com", "iesdouyin.com"}


class DouyinAdapter(ExtractedVideoAdapter):
    name = "douyin"
    display_name = "抖音"

    def page_key(self, url: str) -> str | None:
        parts = urlsplit(url)
        if parts.scheme not in {"http", "https"} or parts.hostname not in _HOSTS:
            return None
        segments = parts.path.strip("/").split("/")
        paths = {
            "video": lambda: segments[1] if len(segments) > 1 else "",
            "share": lambda: segments[2] if len(segments) > 2 and segments[1] == "video" else "",
        }
        video_id = paths.get(segments[0], lambda: "")()
        video_id = video_id or parse_qs(parts.query).get("modal_id", [""])[0]
        return video_id if _ID.fullmatch(video_id) else None

    def cookie_urls(self, url: str) -> list[str]:
        return ["https://www.douyin.com/", "https://www.iesdouyin.com/"]

    def owns_media(self, url: str, page_url: str) -> bool:
        parts = urlsplit(url)
        return bool(self.page_key(page_url)) and (
            host_matches(parts.hostname or "", ("douyinvod.com", "zjcdn.com"))
            or (host_matches(parts.hostname or "", ("douyin.com", "iesdouyin.com", "amemv.com"))
                and parts.path.startswith("/aweme/v1/play"))
        )

    async def resolve(self, url: str, cookies: list[dict]) -> list[MediaResource]:
        video_id = self.page_key(url)
        if not video_id:
            return []
        canonical = f"https://www.douyin.com/video/{video_id}"
        info = await self.extractor.inspect(canonical, cookies)
        return self.resources(info, canonical, video_id)
