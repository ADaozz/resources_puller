from __future__ import annotations

import re
from urllib.parse import unquote, urlparse

_RES = re.compile(
    r"(?i)(?:[_\-.@](?P<w>\d{2,5})[xX](?P<h>\d{2,5})"
    r"|[_\-.](?P<p>\d{3,4})p"
    r"|[@_\-.](?P<scale>[1-4])x"
    r"|[_\-.](?P<word>hd|sd|fhd|uhd|qhd|4k|8k))"
)
_TRAIL_H = re.compile(
    r"(?i)[_\-.](?P<h>144|240|270|360|480|540|576|720|1080|1440|2160|4320)$"
)


def split_url(url: str) -> tuple[str, str, str, str]:
    parts = urlparse(url)
    host = (parts.hostname or "").lower()
    path = unquote(parts.path or "")
    if path.endswith("/"):
        path = path[:-1]
    directory, _, filename = path.rpartition("/")
    directory = directory or "/"
    if "." in filename:
        stem, ext = filename.rsplit(".", 1)
        ext = "." + ext.lower()
    else:
        stem, ext = filename, ""
    return host, directory, stem, ext


def normalize_stem(stem: str) -> tuple[str, str | None, int | None, int | None]:
    """去掉分辨率后缀。去完后名称为空则保持原名且不标记分辨率（不确定就分开）。"""
    label: str | None = None
    width: int | None = None
    height: int | None = None
    found = False

    def repl(match: re.Match[str]) -> str:
        nonlocal label, width, height, found
        found = True
        if match.group("w"):
            width = int(match.group("w"))
            height = int(match.group("h"))
            label = f"{width}x{height}"
        elif match.group("p"):
            height = int(match.group("p"))
            label = f"{height}p"
        elif match.group("scale"):
            label = f"{match.group('scale')}x"
        else:
            label = (match.group("word") or "").lower()
        return " "

    cleaned = _RES.sub(repl, stem)
    if not found:
        trail = _TRAIL_H.search(stem)
        if trail:
            found = True
            height = int(trail.group("h"))
            label = f"{height}p"
            cleaned = stem[: trail.start()]
    normalized = re.sub(r"[\s_\-.@]+", "_", cleaned).strip("_").lower()
    if not normalized:
        normalized = re.sub(r"[\s_\-.@]+", "_", stem).strip("_").lower() or "resource"
        return normalized, None, None, None
    if not found:
        label = None
        width = None
        height = None
    return normalized, label, width, height


def identity_for(url: str, kind: str) -> tuple[str, str, str | None, int | None, int | None]:
    host, directory, stem, ext = split_url(url)
    normalized, label, width, height = normalize_stem(stem)
    generic = {"master", "index", "playlist", "manifest", "video", "media"}
    title = normalized if normalized not in generic else directory.rstrip("/").rsplit("/", 1)[-1] or normalized
    key = f"{kind}|{host}|{directory}|{normalized}|{ext}"
    return key, title or normalized or "resource", label, width, height
