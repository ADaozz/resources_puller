"""Shared rendition mapping for sites backed by the local extractor process."""
from __future__ import annotations

import re

from app.adapters.base import MediaResource
from app.media.extractor import Extractor

_FORMAT_ID = re.compile(r"^[A-Za-z0-9_-]+$")


def host_matches(host: str, domains: tuple[str, ...]) -> bool:
    return any(host == domain or host.endswith("." + domain) for domain in domains)


class ExtractedVideoAdapter:
    name: str
    display_name: str
    allow_unknown_codecs = False

    def __init__(self, extractor: Extractor):
        self.extractor = extractor

    def resources(self, info: dict, page_url: str, video_id: str) -> list[MediaResource]:
        if info.get("is_live") or info.get("live_status") in {"is_live", "is_upcoming"}:
            raise ValueError(f"暂不支持录制{self.display_name}直播，请选择已结束的视频")
        if info.get("_type") in {"playlist", "multi_video"}:
            raise ValueError("请选择单个视频或具体分 P；暂不支持整合集和旧版多段 FLV")
        formats = [fmt for fmt in info.get("formats", [])
                   if not fmt.get("has_drm") and fmt.get("vcodec") != "bytevc2"]
        audio = [fmt for fmt in formats if fmt.get("vcodec") == "none" and fmt.get("acodec") not in {None, "none"}]
        result = []
        for fmt in formats:
            format_id = str(fmt.get("format_id", ""))
            if (not _FORMAT_ID.fullmatch(format_id) or fmt.get("vcodec") == "none"
                    or (not fmt.get("vcodec") and not self.allow_unknown_codecs)
                    or not fmt.get("url") or fmt.get("protocol") in {"mhtml", "images"}):
                continue
            has_audio = fmt.get("acodec") not in {None, "none"} or (
                self.allow_unknown_codecs and fmt.get("acodec") is None
                and fmt.get("vcodec") is None and fmt.get("ext") in {"mp4", "flv"})
            if not has_audio and not audio:
                continue  # Never advertise silent tracks as complete video downloads.
            selector = format_id if has_audio else f"{format_id}+bestaudio[ext=m4a]/{format_id}+bestaudio"
            height = fmt.get("height")
            label = f"{height}p" if height else str(fmt.get("format_note") or format_id)
            codec = (fmt.get("vcodec") or "video").split(".", 1)[0]
            label += f" · {codec} · {fmt.get('ext') or 'video'} · {format_id}"
            thumbnail = info.get("thumbnail") or next(
                (item.get("url") for item in reversed(info.get("thumbnails") or []) if item.get("url")), None,
            )
            result.append(MediaResource(
                key=f"{self.name}:{video_id}:{format_id}", group_key=f"video|{self.name}|{video_id}",
                title=info.get("title") or video_id, url=fmt["url"], label=label,
                width=fmt.get("width"), height=height,
                metadata={"backend": "extractor", "adapter": self.name, "source_url": page_url,
                          "selector": selector, "format_id": format_id, "video_id": video_id,
                          "ext": "mp4", "file_preview": True, "thumbnail": thumbnail},
            ))
        if not result:
            raise ValueError("没有可下载的完整音视频格式，请确认视频可播放并重新打开页面")
        return result
