from __future__ import annotations

from urllib.parse import unquote, urlparse

IMAGE_EXT = {
    ".jpg",
    ".jpeg",
    ".png",
    ".gif",
    ".webp",
    ".avif",
    ".bmp",
    ".svg",
    ".apng",
}
VIDEO_EXT = {".mp4", ".webm", ".mkv", ".mov", ".m4v", ".ogv", ".avi"}
PLAYLIST_EXT = {".m3u8", ".m3u", ".mpd"}
SEGMENT_EXT = {".ts", ".m4s", ".cmfv", ".cmfa"}


def extension_of(url: str) -> str:
    path = unquote(urlparse(url).path).lower()
    name = path.rsplit("/", 1)[-1]
    if "." not in name:
        return ""
    return "." + name.rsplit(".", 1)[-1]


def _mime(mime: str | None) -> str:
    return (mime or "").split(";", 1)[0].strip().lower()


def classify(url: str, mime: str | None = None, hint: str | None = None) -> str:
    """返回 image / video / playlist / segment / ignore。"""
    lowered = url.lower()
    if lowered.startswith(("data:", "blob:", "about:", "chrome:", "devtools:", "javascript:")):
        return "ignore"
    path = unquote(urlparse(url).path).lower()
    if path.endswith("/favicon.ico"):
        return "ignore"
    ext = extension_of(url)
    media_type = _mime(mime)
    if ext in SEGMENT_EXT or media_type in {"video/mp2t", "video/iso.segment"}:
        return "segment"
    if ext in PLAYLIST_EXT or media_type in {
        "application/vnd.apple.mpegurl",
        "application/x-mpegurl",
        "audio/mpegurl",
        "application/dash+xml",
    }:
        return "playlist"
    if media_type.startswith("image/") or ext in IMAGE_EXT:
        if ext == ".ico":
            return "ignore"
        return "image"
    if media_type.startswith("video/") or ext in VIDEO_EXT:
        return "video"
    if hint == "image":
        return "image"
    if hint == "video":
        return "video"
    return "ignore"
