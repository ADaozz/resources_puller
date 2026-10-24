from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from urllib.parse import urljoin

_ATTR = re.compile(r"""([A-Z0-9-]+)=("[^"]*"|[^,]*)""")
_MEDIA_EXT = (".mp4", ".m4v", ".webm", ".mkv", ".mov", ".m3u8", ".mpd")
_SEGMENT_TAGS = {"SegmentTemplate", "SegmentList"}


def _attrs(text: str) -> dict[str, str]:
    found: dict[str, str] = {}
    for key, value in _ATTR.findall(text):
        found[key.upper()] = value.strip().strip('"')
    return found


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def parse_m3u8(base_url: str, text: str) -> dict:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    is_master = any(line.startswith("#EXT-X-STREAM-INF") for line in lines)
    if is_master:
        variants: list[dict] = []
        pending: dict[str, str] | None = None
        for line in lines:
            if line.startswith("#EXT-X-STREAM-INF"):
                pending = _attrs(line.split(":", 1)[1] if ":" in line else "")
                continue
            if line.startswith("#") or pending is None:
                continue
            url = urljoin(base_url, line)
            width = height = None
            resolution = pending.get("RESOLUTION", "")
            if "x" in resolution.lower():
                left, right = re.split(r"[xX]", resolution, maxsplit=1)
                if left.isdigit() and right.isdigit():
                    width, height = int(left), int(right)
            bandwidth = pending.get("BANDWIDTH", "")
            if height:
                label = f"{height}p"
            elif bandwidth.isdigit():
                label = f"{max(int(bandwidth) // 1000, 1)}k"
            else:
                label = "variant"
            variants.append(
                {
                    "url": url,
                    "width": width,
                    "height": height,
                    "label": label,
                    "is_playlist": True,
                }
            )
            pending = None
        return {"kind": "master", "variants": variants, "segments": []}

    segments: list[str] = []
    for line in lines:
        if line.startswith("#EXT-X-MAP"):
            matched = re.search(r'URI="([^"]+)"', line)
            if matched:
                segments.append(urljoin(base_url, matched.group(1)))
            continue
        if line.startswith("#"):
            continue
        segments.append(urljoin(base_url, line))
    return {"kind": "media", "variants": [], "segments": segments}


def _looks_like_file(url: str) -> bool:
    path = url.split("?", 1)[0].lower()
    return path.endswith(_MEDIA_EXT)


def parse_mpd(base_url: str, text: str) -> dict:
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return {"kind": "mpd", "variants": [], "renditions": [], "segments": []}

    variants: list[dict] = []
    renditions: list[dict] = []

    def walk(el: ET.Element, inherited: str, segmented: bool) -> None:
        local_base = inherited
        now_segmented = segmented
        for child in list(el):
            name = _local(child.tag)
            if name == "BaseURL" and (child.text or "").strip():
                local_base = urljoin(inherited, child.text.strip())
            if name in _SEGMENT_TAGS:
                now_segmented = True
        if _local(el.tag) == "Representation":
            width = _int(el.attrib.get("width"))
            height = _int(el.attrib.get("height"))
            bandwidth = _int(el.attrib.get("bandwidth"))
            label = f"{height}p" if height else (el.attrib.get("id") or "dash")
            renditions.append(
                {
                    "label": label,
                    "width": width,
                    "height": height,
                    "bandwidth": bandwidth,
                }
            )
            rep_base = local_base
            rep_segmented = now_segmented
            for child in list(el):
                name = _local(child.tag)
                if name == "BaseURL" and (child.text or "").strip():
                    rep_base = urljoin(local_base, child.text.strip())
                if name in _SEGMENT_TAGS:
                    rep_segmented = True
            if (
                not rep_segmented
                and rep_base
                and rep_base != base_url
                and _looks_like_file(rep_base)
            ):
                variants.append(
                    {
                        "url": rep_base,
                        "width": width,
                        "height": height,
                        "label": label,
                        "is_playlist": rep_base.split("?", 1)[0].lower().endswith((".m3u8", ".mpd")),
                    }
                )
        for child in list(el):
            if _local(child.tag) != "BaseURL":
                walk(child, local_base, now_segmented)

    walk(root, base_url, False)
    return {"kind": "mpd", "variants": variants, "renditions": renditions, "segments": []}


def _int(value: str | None) -> int | None:
    if value and value.isdigit():
        return int(value)
    return None
