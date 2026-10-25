from __future__ import annotations

import re
from urllib.parse import quote, urljoin


def proxy_url(session_id: str, target: str) -> str:
    return f"/api/sessions/{session_id}/media-proxy?url={quote(target, safe='')}"


def rewrite_m3u8(text: str, base_url: str, session_id: str) -> str:
    rewritten: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            rewritten.append(line)
            continue
        if stripped.startswith("#"):
            rewritten.append(
                re.sub(
                    r'URI="([^"]+)"',
                    lambda match: f'URI="{proxy_url(session_id, urljoin(base_url, match.group(1)))}"',
                    line,
                )
            )
            continue
        rewritten.append(proxy_url(session_id, urljoin(base_url, stripped)))
    return "\n".join(rewritten) + "\n"
