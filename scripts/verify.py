#!/usr/bin/env python3
"""对已启动的服务做阶段 0–5 真机验收。请在容器内执行。"""

from __future__ import annotations

import asyncio
import json
import os
import socket
import struct
import subprocess
import time
import traceback
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

BASE = os.environ.get("BASE", "http://127.0.0.1:8080")
PROOFS = Path(os.environ.get("PROOFS", "/data/proofs"))
RESULTS: list[dict] = []


def record(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append({"name": name, "ok": ok, "detail": detail})
    print(("PASS " if ok else "FAIL ") + name + (f" — {detail}" if detail else ""), flush=True)


def api(method: str, path: str, body: dict | None = None, timeout: int = 60) -> tuple[int, dict | list | str]:
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(BASE + path, data=data, method=method)
    if data is not None:
        request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
            if not raw:
                return response.status, {}
            return response.status, json.loads(raw)
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            payload = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            payload = raw.decode("utf-8", errors="replace")
        return exc.code, payload


def detail_of(payload: object) -> str:
    if isinstance(payload, dict):
        value = payload.get("detail", payload)
        return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    return str(payload)


def wait_for(predicate, timeout: float, label: str):
    last = None
    deadline = time.time() + timeout
    while time.time() < deadline:
        last = predicate()
        if last:
            return last
        time.sleep(0.4)
    raise AssertionError(f"{label} 超时，最后状态：{json.dumps(last, ensure_ascii=False)[:1500]}")


def groups() -> list[dict]:
    code, payload = api("GET", f"/api/sessions/{STATE['sid']}/resources")
    if code != 200 or not isinstance(payload, dict):
        return []
    return payload.get("groups") or []


def downloads() -> list[dict]:
    code, payload = api("GET", f"/api/sessions/{STATE['sid']}/downloads")
    if code != 200 or not isinstance(payload, dict):
        return []
    return payload.get("downloads") or []


def sniff(data: bytes) -> str:
    if data.startswith(b"\xff\xd8"):
        return "jpeg"
    if data.startswith(b"\x89PNG"):
        return "png"
    if len(data) >= 12 and data[4:8] == b"ftyp":
        return "mp4"
    return data[:20].hex()


def fetch_bytes(path: str) -> bytes:
    with urllib.request.urlopen(BASE + path, timeout=90) as response:
        return response.read()


def shot(name: str) -> None:
    PROOFS.mkdir(parents=True, exist_ok=True)
    target = PROOFS / name
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "x11grab",
            "-video_size",
            "1440x900",
            "-i",
            ":99.0",
            "-frames:v",
            "1",
            str(target),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )


def rec(sock: socket.socket, size: int) -> bytes:
    buf = b""
    while len(buf) < size:
        chunk = sock.recv(size - len(buf))
        if not chunk:
            raise ConnectionError("VNC 连接中断")
        buf += chunk
    return buf


def vnc_click(x: int, y: int) -> tuple[int, int]:
    sock = socket.create_connection(("127.0.0.1", 5900), timeout=5)
    sock.settimeout(5)
    version = rec(sock, 12)
    sock.sendall(version)
    if version.startswith(b"RFB 003.003"):
        security = struct.unpack(">I", rec(sock, 4))[0]
        if security != 1:
            raise RuntimeError(f"VNC 安全类型 {security}")
    else:
        count = rec(sock, 1)[0]
        kinds = list(rec(sock, count)) if count else []
        if 1 not in kinds:
            raise RuntimeError(f"VNC 不提供空密码认证：{kinds}")
        sock.sendall(b"\x01")
        result = rec(sock, 4)
        if result != b"\x00\x00\x00\x00":
            raise RuntimeError(f"VNC 认证失败：{result!r}")
    sock.sendall(b"\x01")
    header = rec(sock, 24)
    width, height = struct.unpack(">HH", header[:4])
    name_len = struct.unpack(">I", header[20:24])[0]
    if name_len:
        rec(sock, name_len)

    def pointer(mask: int) -> None:
        sock.sendall(struct.pack(">BBHH", 5, mask, max(0, x), max(0, y)))

    # 不要在点击前请求整帧画面，否则服务端发送缓冲区塞满后会丢掉指针事件。
    pointer(0)
    time.sleep(0.1)
    pointer(1)
    time.sleep(0.15)
    pointer(0)
    time.sleep(0.3)
    sock.close()
    return width, height


def click_point() -> tuple[int, int]:
    code, payload = api("GET", f"/api/sessions/{STATE['sid']}/debug")
    if code != 200 or not isinstance(payload, dict):
        raise AssertionError(detail_of(payload))
    metrics = payload.get("metrics") or {}
    box = payload.get("box")
    if not box:
        raise AssertionError(f"找不到测试按钮：{metrics}")
    ratio = float(metrics.get("devicePixelRatio") or 1)
    extra_w = float(metrics["outerWidth"]) - float(metrics["innerWidth"])
    extra_h = float(metrics["outerHeight"]) - float(metrics["innerHeight"])
    # 宽度差很大时，多出来的是视口右侧空白，不是左右对称的窗口边框。
    if extra_w > 48:
        border_x = 0.0
        border_top = max(0.0, extra_h)
    else:
        border_x = max(0.0, extra_w / 2)
        border_top = max(0.0, extra_h - border_x)
    x = float(metrics["screenX"]) + border_x * ratio + (box["x"] + box["width"] / 2) * ratio
    y = float(metrics["screenY"]) + border_top * ratio + (box["y"] + box["height"] / 2) * ratio
    return int(x), int(y)


def query_value(url: str, key: str) -> str:
    return (parse_qs(urlsplit(url).query).get(key) or [""])[0]


def find_variant(predicate) -> dict | None:
    for group in groups():
        for variant in group["variants"]:
            if predicate(group, variant):
                return variant
    return None


STATE: dict = {}


def step_health() -> None:
    def ready():
        code, payload = api("GET", "/api/health")
        if code == 200 and isinstance(payload, dict) and payload.get("ok") and payload.get("chromium_in_tree"):
            return payload
        return None

    payload = wait_for(ready, 90, "浏览器就绪")
    if payload.get("launch_count") != 1:
        raise AssertionError(payload)
    STATE["pid"] = payload["browser_pid"]
    record("浏览器进程", True, f"pid={payload['browser_pid']} launch_count=1")


def step_session() -> None:
    code, payload = api("POST", "/api/sessions")
    if code != 200 or not isinstance(payload, dict):
        raise AssertionError(detail_of(payload))
    STATE["sid"] = payload["id"]
    if payload.get("browser_pid") != STATE["pid"]:
        raise AssertionError(f"会话浏览器 pid 不一致：{payload}")
    record("创建会话", True, payload["id"])


def step_open_demo() -> None:
    code, payload = api(
        "POST",
        f"/api/sessions/{STATE['sid']}/navigate",
        {"url": "http://127.0.0.1:8080/demo/"},
        timeout=60,
    )
    if code != 200:
        raise AssertionError(detail_of(payload))

    def ready():
        found = groups()
        hls = [group for group in found if any("/hls/" in variant["url"] for variant in group["variants"])]
        dash = [group for group in found if any("/dash/" in variant["url"] for variant in group["variants"])]
        images = [group for group in found if group["kind"] == "image"]
        coast = [group for group in images if group["title"] == "coast"]
        covers = [group for group in images if group["title"] == "cover"]
        logo = [group for group in images if group["title"] == "logo"]
        urls = [variant["url"] for group in found for variant in group["variants"]]
        if not (
            len(hls) == 1
            and len(dash) == 1
            and len(coast) == 1
            and len(coast[0]["variants"]) >= 3
            and len(covers) == 2
            and len(logo) == 1
            and logo[0]["variants"][0]["occurrence_count"] >= 2
            and len(images) >= 5
            and not any(url.split("?", 1)[0].endswith((".ts", ".m4s")) for url in urls)
        ):
            return None
        return found

    ready_groups = wait_for(ready, 30, "资源分组")
    record(
        "实时捕获与去重",
        True,
        f"组 {len(ready_groups)}，coast 变体 {next(g['variants'].__len__() for g in ready_groups if g['title']=='coast')}",
    )


def step_vnc_click() -> None:
    x, y = click_point()
    width, height = vnc_click(x, y)
    time.sleep(0.6)
    code, payload = api("GET", f"/api/sessions/{STATE['sid']}/debug")
    clicked = isinstance(payload, dict) and (payload.get("metrics") or {}).get("clicked")
    if not clicked:
        vnc_click(x, y)
        time.sleep(0.8)
        code, payload = api("GET", f"/api/sessions/{STATE['sid']}/debug")
        clicked = isinstance(payload, dict) and (payload.get("metrics") or {}).get("clicked")
    shot("browser.png")
    if not clicked:
        raise AssertionError(f"VNC 点击未进入页面 x={x} y={y} screen={width}x{height} debug={payload}")

    def ready():
        urls = [variant["url"] for group in groups() for variant in group["variants"]]
        if any(url.endswith("clicked.png") for url in urls) and any(url.endswith("lazy.jpg") for url in urls) and any(
            url.endswith("clip.mp4") for url in urls
        ):
            return True
        return None

    wait_for(ready, 20, "点击后的新媒体")
    record("noVNC 同会话点击", True, f"坐标 {x},{y}，画面 {width}x{height}")


def step_preview() -> None:
    logo = find_variant(lambda _group, variant: variant["url"].split("?", 1)[0].endswith("/logo.png"))
    coast = find_variant(lambda _group, variant: "coast_1920x1080" in variant["url"])
    signed = find_variant(
        lambda _group, variant: "signed_photo.jpg" in variant["url"] and query_value(variant["url"], "exp") != "1"
    )
    if not logo or not coast or not signed:
        raise AssertionError("缺少可预览资源")
    logo_kind = sniff(fetch_bytes(f"/api/sessions/{STATE['sid']}/variants/{logo['id']}/preview"))
    coast_kind = sniff(fetch_bytes(f"/api/sessions/{STATE['sid']}/variants/{coast['id']}/preview"))
    signed_kind = sniff(fetch_bytes(f"/api/sessions/{STATE['sid']}/variants/{signed['id']}/preview"))
    if logo_kind != "png" or coast_kind != "jpeg" or signed_kind != "jpeg":
        raise AssertionError(f"预览类型 logo={logo_kind} coast={coast_kind} signed={signed_kind}")
    record("预览", True, "png/jpeg 可读取")


def step_ui() -> None:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, args=["--no-sandbox", "--disable-gpu"])
        page = browser.new_page(viewport={"width": 1400, "height": 900})
        page.goto(BASE + "/", wait_until="domcontentloaded")
        page.wait_for_function("() => document.body.dataset.ws === 'open'", timeout=20000)
        page.wait_for_function(
            """() => {
                const node = document.querySelector('[data-testid=deck-image] [data-testid=deck-count]');
                return node && Number(node.textContent) >= 5;
            }""",
            timeout=20000,
        )
        plates = page.locator("[data-testid=deck-image] .plate").count()
        badge = page.locator("[data-testid=deck-image] [data-testid=badge]").inner_text()
        if plates > 4 or not badge:
            raise AssertionError(f"堆叠数量异常 plates={plates} badge={badge}")
        pid_text = page.get_by_test_id("pid").inner_text()
        if str(STATE["pid"]) not in pid_text:
            raise AssertionError(pid_text)
        src = page.get_by_test_id("viewport").get_attribute("src") or ""
        if "novnc" not in src:
            raise AssertionError(src)
        page.locator("[data-testid=deck-image] .stack").click()
        page.get_by_test_id("modal").wait_for()
        if page.get_by_test_id("viewport").count() != 1:
            raise AssertionError("打开弹窗后浏览器画面被卸载")
        checkbox = page.locator("[data-testid=group-check]").last
        group_id = checkbox.get_attribute("data-group-id")
        checkbox.check()
        page.evaluate("() => window.__rpDebug.closeWs()")
        page.wait_for_function("() => document.body.dataset.ws === 'closed'", timeout=10000)
        page.wait_for_function("() => document.body.dataset.ws === 'open'", timeout=10000)
        again = page.locator(f'[data-testid=group-check][data-group-id="{group_id}"]')
        if not again.is_checked():
            raise AssertionError("重连后选择状态丢失")
        page.get_by_test_id("copy").first.click()
        copied = page.get_by_test_id("copied").inner_text()
        if "已复制" not in copied or not page.get_by_test_id("copied").get_attribute("data-last-copied"):
            raise AssertionError(copied)
        page.get_by_test_id("select-all").click()
        page.get_by_test_id("download-batch").click()
        page.screenshot(path=str(PROOFS / "ui.png"), full_page=True)
        browser.close()
    record("堆叠仓、弹窗、选择、复制、重连", True, f"封面 {plates} 张，总数 {badge}")

    def enough():
        done = [job for job in downloads() if job["state"] == "COMPLETED"]
        return done if len(done) >= 2 else None

    done = wait_for(enough, 60, "批量下载")
    record("批量下载", True, f"完成 {len(done)} 个")


def step_merge() -> None:
    hls = find_variant(lambda _group, variant: variant["url"].endswith("720p/index.m3u8"))
    dash = find_variant(lambda _group, variant: variant["url"].endswith("manifest.mpd"))
    if not hls or not dash:
        raise AssertionError(f"缺少播放列表 hls={bool(hls)} dash={bool(dash)}")
    code, payload = api(
        "POST",
        f"/api/sessions/{STATE['sid']}/downloads",
        {"items": [{"variant_id": hls["id"]}, {"variant_id": dash["id"]}]},
    )
    if code != 200 or not isinstance(payload, dict):
        raise AssertionError(detail_of(payload))
    ids = [item["id"] for item in payload["downloads"]]

    def ready():
        found = [job for job in downloads() if job["id"] in ids]
        if len(found) < 2:
            return None
        if any(job["state"] == "FAILED" for job in found):
            raise AssertionError(json.dumps(found, ensure_ascii=False)[:1500])
        if all(job["state"] == "COMPLETED" for job in found):
            return found
        return None

    found = wait_for(ready, 120, "HLS/DASH 合并")
    for job in found:
        data = fetch_bytes(f"/api/sessions/{STATE['sid']}/downloads/{job['id']}/file")
        if sniff(data) != "mp4" or len(data) < 1000:
            raise AssertionError(f"{job['filename']} {sniff(data)} {len(data)}")
        if not job.get("path") or not Path(job["path"]).is_file():
            raise AssertionError("合并文件不在磁盘上")
    STATE["files"] = [job["path"] for job in found]
    record("HLS/DASH 合并", True, "两个 mp4")


def step_cancel_and_error() -> None:
    code, _payload = api(
        "POST",
        f"/api/sessions/{STATE['sid']}/navigate",
        {"url": "http://127.0.0.1:8080/demo/slow-page"},
        timeout=60,
    )
    if code != 200:
        raise AssertionError(detail_of(_payload))

    def slow():
        return find_variant(lambda _group, variant: "/demo/slow/clip.mp4" in variant["url"])

    variant = wait_for(slow, 20, "慢速视频")
    code, payload = api(
        "POST",
        f"/api/sessions/{STATE['sid']}/downloads",
        {"items": [{"variant_id": variant["id"]}]},
    )
    job_id = payload["downloads"][0]["id"]

    def running():
        job = next(item for item in downloads() if item["id"] == job_id)
        if job["state"] in {"DOWNLOADING", "MERGING", "PROBING"} and job["progress"] >= 0:
            return job
        if job["state"] in {"FAILED", "CANCELLED", "COMPLETED"}:
            raise AssertionError(json.dumps(job, ensure_ascii=False))
        return None

    wait_for(running, 20, "下载进度")
    code, cancelled = api("POST", f"/api/sessions/{STATE['sid']}/downloads/{job_id}/cancel")
    if code != 200 or not isinstance(cancelled, dict) or cancelled.get("state") != "CANCELLED":
        raise AssertionError(detail_of(cancelled))
    if cancelled.get("path") and Path(cancelled["path"]).exists():
        raise AssertionError("取消后仍留下部分文件")

    expired = find_variant(
        lambda _group, variant: "signed_photo.jpg" in variant["url"] and query_value(variant["url"], "exp") == "1"
    )
    if not expired:
        raise AssertionError("没有过期签名资源")
    code, payload = api(
        "POST",
        f"/api/sessions/{STATE['sid']}/downloads",
        {"items": [{"variant_id": expired["id"]}]},
    )
    expired_id = payload["downloads"][0]["id"]

    def failed():
        job = next(item for item in downloads() if item["id"] == expired_id)
        if job["state"] == "FAILED":
            return job
        if job["state"] == "COMPLETED":
            raise AssertionError(job)
        return None

    failed_job = wait_for(failed, 30, "签名失效")
    if "403" not in (failed_job.get("error") or "") and "签名" not in (failed_job.get("error") or ""):
        raise AssertionError(failed_job)
    record("进度、取消、签名错误", True, failed_job["error"][:120])


def step_ssrf() -> None:
    targets = [
        "http://169.254.169.254/latest/meta-data/",
        "http://127.0.0.1:22/",
        "http://10.1.2.3/",
        "file:///etc/passwd",
    ]
    for target in targets:
        code, payload = api("POST", f"/api/sessions/{STATE['sid']}/navigate", {"url": target})
        if code != 400:
            raise AssertionError(f"{target} -> {code} {detail_of(payload)}")
    code, payload = api("GET", f"/api/sessions/{STATE['sid']}/debug")
    blocked = payload.get("blocked") if isinstance(payload, dict) else []
    urls = " ".join(item.get("url", "") for item in blocked)
    if "127.0.0.1:22" not in urls or "169.254.169.254" not in urls:
        raise AssertionError(urls)
    record("SSRF 防护", True, "导航拒绝且页面内请求被拦截")


async def _ws_once() -> int:
    import websockets

    async with websockets.connect(f"ws://127.0.0.1:8080/api/sessions/{STATE['sid']}/events") as socket:
        raw = await asyncio.wait_for(socket.recv(), 8)
        payload = json.loads(raw)
        if payload.get("type") != "snapshot":
            raise AssertionError(payload)
        return len(payload.get("groups") or [])


def step_ws() -> None:
    first = asyncio.run(_ws_once())
    second = asyncio.run(_ws_once())
    if first < 1 or second < 1:
        raise AssertionError(f"{first}/{second}")
    record("WebSocket 快照重连", True, f"两组连接分别看到 {first} 和 {second} 个资源组")


def step_cleanup() -> None:
    files = STATE.get("files") or []
    code, _payload = api("DELETE", f"/api/sessions/{STATE['sid']}")
    if code != 200:
        raise AssertionError(detail_of(_payload))
    code, payload = api("GET", f"/api/sessions/{STATE['sid']}/resources")
    if code != 404:
        raise AssertionError(payload)
    for path in files:
        if Path(path).exists():
            raise AssertionError(f"会话清理后文件仍在 {path}")
    code, payload = api("POST", "/api/sessions")
    if code != 200 or not isinstance(payload, dict):
        raise AssertionError(detail_of(payload))
    if payload.get("launch_count") != 1 or payload.get("browser_pid") != STATE["pid"]:
        raise AssertionError(payload)
    new_id = payload["id"]
    code, resources = api("GET", f"/api/sessions/{new_id}/resources")
    if code != 200 or resources.get("groups"):
        raise AssertionError(resources)
    api("DELETE", f"/api/sessions/{new_id}")
    record("会话清理", True, "旧文件删除，浏览器进程仍是原来的一个")


def main() -> None:
    PROOFS.mkdir(parents=True, exist_ok=True)
    steps = [
        step_health,
        step_session,
        step_open_demo,
        step_vnc_click,
        step_preview,
        step_ui,
        step_merge,
        step_cancel_and_error,
        step_ssrf,
        step_ws,
        step_cleanup,
    ]
    for action in steps:
        try:
            action()
        except Exception as exc:
            record(action.__name__, False, str(exc))
            traceback.print_exc()
            shot("failure.png")
            break
    report = PROOFS / "report.json"
    report.write_text(json.dumps(RESULTS, ensure_ascii=False, indent=2), encoding="utf-8")
    failed = [item for item in RESULTS if not item["ok"]]
    print(json.dumps(RESULTS, ensure_ascii=False, indent=2))
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
