from __future__ import annotations

import re
from urllib.parse import parse_qs, urlsplit

from app.adapters.base import MediaResource
from app.adapters.extracted import ExtractedVideoAdapter, host_matches

_VIDEO = re.compile(r"(?:BV[A-Za-z0-9]{10}|av[1-9][0-9]*)", re.IGNORECASE)
_HOSTS = {"www.bilibili.com", "bilibili.com", "m.bilibili.com", "player.bilibili.com"}


class BilibiliAdapter(ExtractedVideoAdapter):
    name = "bilibili"
    display_name = "B站"
    # The legacy progressive MP4 extractor omits both codec fields.
    allow_unknown_codecs = True

    def target(self, url: str) -> tuple[str, int] | None:
        parts = urlsplit(url)
        if parts.scheme not in {"http", "https"} or parts.hostname not in _HOSTS:
            return None
        query = parse_qs(parts.query)
        segments = parts.path.strip("/").split("/")
        candidates = {
            "video": lambda: segments[1] if len(segments) > 1 else "",
            "player.html": lambda: query.get("bvid", [""])[0] or "av" + query.get("aid", [""])[0],
        }
        video_id = candidates.get(segments[0], lambda: "")()
        raw_part = query.get("p", query.get("page", ["1"]))[0]
        if not _VIDEO.fullmatch(video_id) or not re.fullmatch(r"[1-9][0-9]{0,5}", raw_part):
            return None
        prefix = "BV" if video_id[:2].lower() == "bv" else "av"
        return prefix + video_id[2:], int(raw_part)

    def page_key(self, url: str) -> str | None:
        target = self.target(url)
        return f"{target[0]}:p{target[1]}" if target else None

    def cookie_urls(self, url: str) -> list[str]:
        return ["https://www.bilibili.com/", "https://api.bilibili.com/"]

    def owns_media(self, url: str, page_url: str) -> bool:
        return bool(self.target(page_url)) and host_matches(
            urlsplit(url).hostname or "", ("bilivideo.com", "bilivideo.cn", "biliapi.net"),
        )

    async def resolve(self, url: str, cookies: list[dict]) -> list[MediaResource]:
        target = self.target(url)
        if not target:
            return []
        video_id, part = target
        canonical = f"https://www.bilibili.com/video/{video_id}?p={part}"
        info = await self.extractor.inspect(canonical, cookies)
        # AV and BV aliases resolve to the same BV identity; keep P separate.
        resolved_id = str(info.get("id", "")).split("_p", 1)[0]
        if _VIDEO.fullmatch(resolved_id):
            video_id = resolved_id
            canonical = f"https://www.bilibili.com/video/{video_id}?p={part}"
        return self.resources(info, canonical, f"{video_id}:p{part}")
