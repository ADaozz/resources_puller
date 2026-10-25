"""导航、预览和下载共用的 SSRF 检查。

回环地址只放行本服务端口（默认 8080/8000），用来打开内置测试页。
其余私网、链路本地、元数据地址一律拒绝。解析失败时拒绝。
"""

from __future__ import annotations

import ipaddress
import socket
import time
from urllib.parse import urlsplit

from app.config import self_ports

_CACHE: dict[tuple[str, int], tuple[float, bool, str]] = {}
_TTL = 10.0


class SSRFError(ValueError):
    pass


def ip_allowed(ip: ipaddress.IPv4Address | ipaddress.IPv6Address, port: int) -> bool:
    if ip.is_loopback:
        return port in self_ports()
    if (
        ip.is_private
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    ):
        return False
    return True


def _parse_literal(host: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        return ipaddress.ip_address(host)
    except ValueError:
        pass
    if host.isdigit():
        try:
            return ipaddress.ip_address(int(host))
        except ValueError:
            return None
    try:
        packed = socket.inet_aton(host)
    except OSError:
        return None
    try:
        return ipaddress.ip_address(socket.inet_ntoa(packed))
    except ValueError:
        return None


def _resolve(host: str, port: int) -> list[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    literal = _parse_literal(host)
    if literal is not None:
        return [literal]
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise SSRFError(f"无法解析主机：{host}") from exc
    found: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = []
    for info in infos:
        raw = info[4][0].split("%", 1)[0]
        try:
            found.append(ipaddress.ip_address(raw))
        except ValueError:
            continue
    if not found:
        raise SSRFError(f"无法解析主机：{host}")
    return found


def assert_safe(url: str) -> None:
    parts = urlsplit(url.strip())
    if parts.scheme not in {"http", "https"}:
        raise SSRFError("只允许 http 和 https")
    host = parts.hostname
    if not host:
        raise SSRFError("缺少主机名")
    port = parts.port or (443 if parts.scheme == "https" else 80)
    if port < 1 or port > 65535:
        raise SSRFError("端口无效")
    key = (host.lower(), port)
    now = time.monotonic()
    cached = _CACHE.get(key)
    if cached and now - cached[0] < _TTL:
        if not cached[1]:
            raise SSRFError(cached[2])
        return
    try:
        ips = _resolve(host, port)
    except SSRFError as exc:
        _CACHE[key] = (now, False, str(exc))
        raise
    for ip in ips:
        if not ip_allowed(ip, port):
            reason = f"拒绝访问非公网地址 {ip}（端口 {port}）"
            _CACHE[key] = (now, False, reason)
            raise SSRFError(reason)
    _CACHE[key] = (now, True, "")


def clear_cache() -> None:
    _CACHE.clear()
