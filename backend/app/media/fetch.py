from __future__ import annotations

import httpx

from app.security.ssrf import assert_safe

_TIMEOUT = httpx.Timeout(connect=8.0, read=120.0, write=20.0, pool=8.0)
_REDIRECTS = {301, 302, 303, 307, 308}
# 不少公开 CDN 会拒绝 python-httpx 这类默认标识，服务端预览和下载改用浏览器标识。
_DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
    ),
    "Accept": "*/*",
}


def _merge_headers(headers: dict[str, str] | None) -> dict[str, str]:
    merged = dict(_DEFAULT_HEADERS)
    if headers:
        merged.update(headers)
    return merged


async def open_response(
    url: str,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
) -> tuple[httpx.AsyncClient, httpx.Response, str]:
    """手动跟随重定向，每一跳都重新做 SSRF 检查。调用方负责关闭 client。"""
    client = httpx.AsyncClient(follow_redirects=False, timeout=_TIMEOUT)
    current = url
    request_headers = _merge_headers(headers)
    try:
        for _ in range(6):
            assert_safe(current)
            request = client.build_request(method, current, headers=request_headers)
            response = await client.send(request, stream=True)
            if response.status_code in _REDIRECTS:
                location = response.headers.get("location")
                await response.aclose()
                if not location:
                    raise RuntimeError("重定向缺少 Location")
                current = str(httpx.URL(current).join(location))
                continue
            return client, response, current
        raise RuntimeError("重定向次数过多")
    except Exception:
        await client.aclose()
        raise


async def read_text(url: str, limit: int = 2_000_000) -> tuple[str, str]:
    client, response, final = await open_response(url)
    try:
        if response.status_code >= 400:
            body = (await response.aread())[:300]
            raise RuntimeError(
                f"HTTP {response.status_code} {body.decode('utf-8', errors='replace')}"
            )
        data = await response.aread()
        if len(data) > limit:
            data = data[:limit]
        mime = response.headers.get("content-type", "")
        return data.decode("utf-8", errors="replace"), mime
    finally:
        await response.aclose()
        await client.aclose()
