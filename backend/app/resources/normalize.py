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


_PROCESS = re.compile(
    r"(?i)^(?P<w>\d{2,5})w_(?P<h>\d{2,5})h"
    r"|^(?P<wx>\d{2,5})[xX](?P<hx>\d{2,5})"
    r"|^(?P<scale>[1-4])x(?:[_\-.]|$)"
)


def peel_processor(filename: str) -> tuple[str, str | None, int | None, int | None]:
    """把 name.jpg@3840w_360h_1c.avif 还原成 name.jpg，尺寸记成变体。

    @ 后面不是分辨率时保持原名，避免把不确定的名字并到一起。
    """
    if "@" not in filename:
        return filename, None, None, None
    base, _, tail = filename.partition("@")
    if not base:
        return filename, None, None, None
    matched = _PROCESS.match(tail)
    if not matched:
        return filename, None, None, None
    if matched.group("w"):
        width = int(matched.group("w"))
        height = int(matched.group("h"))
        return base, f"{width}x{height}", width, height
    if matched.group("wx"):
        width = int(matched.group("wx"))
        height = int(matched.group("hx"))
        return base, f"{width}x{height}", width, height
    return base, f"{matched.group('scale')}x", None, None


def split_url(url: str) -> tuple[str, str, str, str, str | None, int | None, int | None]:
    parts = urlparse(url)
    host = (parts.hostname or "").lower()
    path = unquote(parts.path or "")
    if path.endswith("/"):
        path = path[:-1]
    directory, _, filename = path.rpartition("/")
    directory = directory or "/"
    base, proc_label, proc_w, proc_h = peel_processor(filename)
    if "." in base:
        stem, ext = base.rsplit(".", 1)
        ext = "." + ext.lower()
    else:
        stem, ext = base, ""
    return host, directory, stem, ext, proc_label, proc_w, proc_h


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
    host, directory, stem, ext, proc_label, proc_w, proc_h = split_url(url)
    normalized, label, width, height = normalize_stem(stem)
    if proc_label:
        label = proc_label
        width = proc_w if proc_w is not None else width
        height = proc_h if proc_h is not None else height
    generic = {"master", "index", "playlist", "manifest", "video", "media"}
    title = normalized if normalized not in generic else directory.rstrip("/").rsplit("/", 1)[-1] or normalized
    # 扩展名用处理后缀之前的原始后缀，这样 jpg 再转成 webp/avif 仍是同一组。
    # 主机和目录留在键里：不同位置的同名文件不确定是不是同一资源，分开。
    key = f"{kind}|{host}|{directory}|{normalized}|{ext}"
    return key, title or normalized or "resource", label, width, height
