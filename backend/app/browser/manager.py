"""同一进程会话。

Playwright 以 headed 方式把 Chromium 启动在 Xvfb 的 DISPLAY 上。
x11vnc 抓的是这块屏幕，noVNC 只是它的画面和键鼠通道。
因此用户在网页里点击、滚动时，命中的就是 Playwright 正在监听的那个浏览器。
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from playwright.async_api import Browser, BrowserContext, Page, Playwright, async_playwright

from app.config import SCREEN_H, SCREEN_W
from app.hub import Hub
from app.media.pipeline import DownloadManager
from app.resources.classify import classify
from app.resources.store import Store
from app.security.ssrf import SSRFError, assert_safe

log = logging.getLogger(__name__)


def main_chromium_pids() -> list[int]:
    found: list[int] = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            raw = (entry / "cmdline").read_bytes()
        except OSError:
            continue
        command = raw.replace(b"\x00", b" ").decode("utf-8", "replace")
        if "chrome" not in command and "chromium" not in command:
            continue
        if "--type=" in command or "crashpad" in command:
            continue
        if "--remote-debugging-pipe" in command or "--remote-debugging-port" in command:
            found.append(int(entry.name))
    return found


def _json_ready(value):
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, dict):
        return {str(key): _json_ready(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_ready(item) for item in value]
    return str(value)


def descendant_has_chromium(pid: int | None) -> bool:
    if not pid:
        return False
    stack = [pid]
    seen: set[int] = set()
    while stack:
        current = stack.pop()
        if current in seen:
            continue
        seen.add(current)
        children_path = Path(f"/proc/{current}/task/{current}/children")
        cmdline_path = Path(f"/proc/{current}/cmdline")
        try:
            cmdline = cmdline_path.read_bytes().replace(b"\x00", b" ").decode("utf-8", errors="replace").lower()
        except OSError:
            cmdline = ""
        if current != pid and ("chrom" in cmdline):
            return True
        try:
            raw = children_path.read_text()
        except OSError:
            continue
        for part in raw.split():
            if part.isdigit():
                stack.append(int(part))
    return False

NOVNC_URL = (
    "/novnc/vnc.html?autoconnect=1&resize=scale&reconnect=1&reconnect_delay=1000"
    "&show_dot=1&path=websockify"
)

DOM_SCRIPT = r"""
(() => {
  if (window.__rpDomInstalled) return;
  window.__rpDomInstalled = true;
  const seen = new Set();
  const abs = (value) => {
    if (!value || typeof value !== "string") return null;
    if (value.startsWith("data:") || value.startsWith("blob:")) return null;
    try { return new URL(value, location.href).href; } catch { return null; }
  };
  const report = (value, tag) => {
    const url = abs(value);
    if (!url || seen.has(url + "|" + tag)) return;
    seen.add(url + "|" + tag);
    if (typeof window.rpReport === "function") {
      window.rpReport({ url, tag });
    }
  };
  const scan = (root) => {
    if (!root || !root.querySelectorAll) return;
    root.querySelectorAll("img,video,source,audio").forEach((el) => {
      ["currentSrc", "src", "poster"].forEach((key) => {
        if (el[key]) report(el[key], el.tagName || "MEDIA");
      });
      if (el.srcset) {
        el.srcset.split(",").forEach((part) => {
          const candidate = part.trim().split(/\s+/)[0];
          if (candidate) report(candidate, "SRCSET");
        });
      }
    });
    root.querySelectorAll("[style*='url(']").forEach((el) => {
      const style = el.getAttribute("style") || "";
      const matched = style.match(/url\((['"]?)(.*?)\1\)/g) || [];
      matched.forEach((item) => {
        const inner = item.replace(/^url\((['"]?)/, "").replace(/(['"]?)\)$/, "");
        report(inner, "CSS");
      });
    });
  };
  const start = () => {
    scan(document);
    const observer = new MutationObserver((records) => {
      records.forEach((record) => {
        record.addedNodes.forEach((node) => {
          if (node.nodeType === 1) scan(node);
        });
        if (record.type === "attributes" && record.target && record.target.nodeType === 1) {
          scan(record.target);
        }
      });
    });
    observer.observe(document.documentElement, {
      childList: true,
      subtree: true,
      attributes: true,
      attributeFilter: ["src", "srcset", "poster", "style"],
    });
  };
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start, { once: true });
  } else {
    start();
  }
})();
"""

CHROMIUM_ARGS = [
    "--no-sandbox",
    "--disable-dev-shm-usage",
    "--disable-gpu",
    "--ozone-platform=x11",
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-sync",
    "--disable-translate",
    "--disable-extensions",
    "--disable-background-networking",
    "--disable-component-update",
    "--disable-features=Translate,MediaRouter,InterestFeedContentSuggestions",
    "--autoplay-policy=no-user-gesture-required",
    "--window-position=0,0",
    f"--window-size={SCREEN_W},{SCREEN_H}",
    "--start-maximized",
    "--force-device-scale-factor=1",
    "--lang=zh-CN",
]


@dataclass
class LiveSession:
    id: str
    context: BrowserContext
    page: Page
    current_url: str = "about:blank"


class BrowserService:
    def __init__(self, store: Store, hub: Hub, downloads: DownloadManager) -> None:
        self.store = store
        self.hub = hub
        self.downloads = downloads
        self.playwright: Playwright | None = None
        self.browser: Browser | None = None
        self.session: LiveSession | None = None
        self.start_error: str | None = None
        self.launch_count = 0
        self._chromium_pid: int | None = None
        self.blocked: list[dict] = []

    @property
    def browser_pid(self) -> int | None:
        return self._chromium_pid

    async def start(self) -> None:
        os.environ.setdefault("DISPLAY", ":99")
        self.playwright = await async_playwright().start()
        before = set(main_chromium_pids())
        self.browser = await self.playwright.chromium.launch(
            headless=False,
            args=CHROMIUM_ARGS,
        )
        fresh = [pid for pid in main_chromium_pids() if pid not in before]
        self._chromium_pid = fresh[0] if fresh else (main_chromium_pids()[:1] or [None])[0]
        self.launch_count += 1
        log.info("headed Chromium 已启动 pid=%s display=%s", self.browser_pid, os.environ.get("DISPLAY"))

    async def stop(self) -> None:
        if self.session:
            await self.close_session(self.session.id)
        if self.browser:
            await self.browser.close()
            self.browser = None
        if self.playwright:
            await self.playwright.stop()
            self.playwright = None

    def snapshot(self, session_id: str | None = None) -> dict:
        groups = self.store.list_groups(session_id) if session_id else []
        downloads = self.store.list_downloads(session_id) if session_id else []
        return {
            "type": "snapshot",
            "groups": groups,
            "downloads": downloads,
            "current_url": self.session.current_url if self.session and self.session.id == session_id else "",
            "browser_pid": self.browser_pid,
        }

    async def create_session(self) -> dict:
        if not self.browser:
            raise RuntimeError(self.start_error or "浏览器尚未启动")
        if self.session:
            return self.describe()
        context = await self.browser.new_context(
            no_viewport=True,
            locale="zh-CN",
            accept_downloads=False,
        )
        session_id = uuid.uuid4().hex
        self.store.create_session(session_id)

        async def on_dom(_source, payload):
            await self._on_dom(session_id, payload)

        await context.expose_binding("rpReport", on_dom)
        await context.add_init_script(DOM_SCRIPT)

        async def on_response(response):
            await self._on_response(session_id, response)

        async def guard(route):
            await self._guard(session_id, route)

        context.on("response", on_response)
        await context.route("**/*", guard)
        page = await context.new_page()
        page.on(
            "framenavigated",
            lambda frame, sid=session_id: self._on_navigated(sid, frame),
        )
        self.session = LiveSession(id=session_id, context=context, page=page)
        await self._place_window(page)
        return self.describe()

    def describe(self) -> dict:
        if not self.session:
            raise KeyError("没有活动会话")
        return {
            "id": self.session.id,
            "novnc_url": NOVNC_URL,
            "current_url": self.session.current_url,
            "browser_pid": self.browser_pid,
            "launch_count": self.launch_count,
            "display": os.environ.get("DISPLAY", ":99"),
        }

    async def close_session(self, session_id: str) -> None:
        if not self.session or self.session.id != session_id:
            raise KeyError(session_id)
        await self.downloads.cancel_session(session_id)
        markers = []
        try:
            markers = self.store.delete_session(session_id)
        finally:
            context = self.session.context
            self.session = None
            await context.close()
        self._delete_marker_files(session_id, markers)

    async def navigate(self, session_id: str, url: str) -> str:
        page = self._page(session_id)
        assert_safe(url)
        await page.goto(url, wait_until="domcontentloaded", timeout=45000)
        self.session.current_url = page.url  # type: ignore[union-attr]
        return page.url

    async def back(self, session_id: str) -> str:
        page = self._page(session_id)
        await page.go_back(wait_until="domcontentloaded", timeout=30000)
        self.session.current_url = page.url  # type: ignore[union-attr]
        return page.url

    async def forward(self, session_id: str) -> str:
        page = self._page(session_id)
        await page.go_forward(wait_until="domcontentloaded", timeout=30000)
        self.session.current_url = page.url  # type: ignore[union-attr]
        return page.url

    async def reload(self, session_id: str) -> str:
        page = self._page(session_id)
        await page.reload(wait_until="domcontentloaded", timeout=45000)
        self.session.current_url = page.url  # type: ignore[union-attr]
        return page.url

    async def debug(self, session_id: str) -> dict:
        page = self._page(session_id)
        metrics = await page.evaluate(
            """() => ({
                href: location.href,
                clicked: !!window.__rpClicked,
                screenX: window.screenX,
                screenY: window.screenY,
                outerWidth: window.outerWidth,
                outerHeight: window.outerHeight,
                innerWidth: window.innerWidth,
                innerHeight: window.innerHeight,
                devicePixelRatio: window.devicePixelRatio || 1
            })"""
        )
        box = None
        try:
            box = await page.locator("#load-hidden").bounding_box()
        except Exception:
            box = None
        return _json_ready(
            {
                "session_id": session_id,
                "browser_pid": self.browser_pid,
                "launch_count": self.launch_count,
                "display": os.environ.get("DISPLAY"),
                "metrics": metrics,
                "box": box,
                "blocked": self.blocked[-30:],
            }
        )

    def _page(self, session_id: str) -> Page:
        if not self.session or self.session.id != session_id:
            raise KeyError(session_id)
        return self.session.page

    async def _place_window(self, page: Page) -> None:
        try:
            cdp = await page.context.new_cdp_session(page)
            info = await cdp.send("Browser.getWindowForTarget")
            await cdp.send(
                "Browser.setWindowBounds",
                {
                    "windowId": info["windowId"],
                    "bounds": {
                        "left": 0,
                        "top": 0,
                        "width": SCREEN_W,
                        "height": SCREEN_H,
                        "windowState": "normal",
                    },
                },
            )
            await page.bring_to_front()
        except Exception:
            log.exception("调整窗口位置失败，继续使用默认窗口")

    def _on_navigated(self, session_id: str, frame) -> None:
        if not self.session or self.session.id != session_id:
            return
        if frame != self.session.page.main_frame:
            return
        self.session.current_url = frame.url
        self.hub.publish(session_id, {"type": "page.url", "url": frame.url})

    async def _on_dom(self, session_id: str, payload) -> None:
        if not isinstance(payload, dict):
            return
        url = payload.get("url")
        if not isinstance(url, str):
            return
        tag = str(payload.get("tag") or "")
        if tag in {"IMG", "SRCSET", "CSS"}:
            hint = "image"
        elif tag in {"VIDEO", "SOURCE", "AUDIO"}:
            hint = "video"
        else:
            hint = None
        try:
            self.store.ingest(session_id, url=url, mime=None, status=None, via="dom", hint=hint)
        except Exception:
            log.exception("DOM 资源入库失败")

    async def _on_response(self, session_id: str, response) -> None:
        try:
            url = response.url
            headers = response.headers
            mime = headers.get("content-type", "")
            kind = classify(url, mime)
            if kind in {"ignore", "segment"}:
                return
            body = None
            if kind == "playlist":
                try:
                    body = await response.text()
                except Exception:
                    body = ""
            self.store.ingest(
                session_id,
                url=url,
                mime=mime,
                status=response.status,
                via="network",
                body=body,
            )
        except Exception:
            log.exception("网络响应入库失败")

    async def _guard(self, session_id: str, route) -> None:
        url = route.request.url
        if url.startswith(("data:", "blob:", "about:", "chrome-error:")):
            await route.continue_()
            return
        try:
            await asyncio.to_thread(assert_safe, url)
        except SSRFError as exc:
            self.blocked.append({"url": url, "reason": str(exc), "at": time.time(), "session_id": session_id})
            self.blocked = self.blocked[-100:]
            log.info("已拦截 %s：%s", url, exc)
            await route.abort("blockedbyclient")
            return
        try:
            await route.continue_()
        except Exception:
            log.exception("放行请求失败")

    def _delete_marker_files(self, session_id: str, markers: list[str]) -> None:
        from pathlib import Path

        from app.config import data_dir

        download_dir = data_dir() / "downloads" / session_id
        if download_dir.exists():
            for child in download_dir.iterdir():
                child.unlink(missing_ok=True)
            download_dir.rmdir()
        posters = data_dir() / "posters"
        cache = data_dir() / "cache"
        for marker in markers:
            if marker.startswith("poster:"):
                variant_id = marker.split(":", 1)[1]
                (posters / f"{variant_id}.jpg").unlink(missing_ok=True)
                (cache / f"{variant_id}.mp4").unlink(missing_ok=True)
            else:
                Path(marker).unlink(missing_ok=True)
