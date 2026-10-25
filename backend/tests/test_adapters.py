from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.adapters.base import AdapterRegistry
from app.adapters.youtube import YouTubeAdapter
from app.db import DB
from app.hub import Hub
from app.media.backends import BackendRegistry, DownloadPlan, plan_for
from app.media.extractor import Extractor, cookie_file
from app.media.pipeline import DownloadManager
from app.resources.store import Store

PAGE = "https://www.youtube.com/watch?v=jNQXAC9IVRw"


def video_info(video_id="jNQXAC9IVRw", suffix="first"):
    return {"id": video_id, "title": "Test video", "formats": [
        {"format_id": "137", "height": 1080, "width": 1920, "ext": "mp4",
         "vcodec": "avc1", "acodec": "none", "url": f"https://cdn.example/video?token={suffix}"},
        {"format_id": "140", "ext": "m4a", "vcodec": "none", "acodec": "mp4a",
         "url": "https://cdn.example/audio"},
        {"format_id": "18", "height": 360, "ext": "mp4", "vcodec": "avc1", "acodec": "mp4a",
         "url": "https://cdn.example/muxed"},
        {"format_id": "drm", "has_drm": True, "vcodec": "avc1", "acodec": "mp4a", "url": "https://cdn.example/drm"},
    ]}


class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.adapter = YouTubeAdapter(Extractor())
        self.store = Store(DB(":memory:"), Hub())
        self.store.create_session("s1")

    def test_urls_and_host_boundaries(self):
        for url in [PAGE, "https://youtu.be/jNQXAC9IVRw?t=10",
                    "https://m.youtube.com/shorts/jNQXAC9IVRw", "https://www.youtube-nocookie.com/embed/jNQXAC9IVRw"]:
            self.assertEqual(self.adapter.page_key(url), "jNQXAC9IVRw")
        for url in ["https://youtube.com.evil.example/watch?v=jNQXAC9IVRw",
                    "https://www.youtube.com/playlist?list=123", "https://youtu.be/invalid", "file://youtube.com/watch?v=jNQXAC9IVRw"]:
            self.assertIsNone(self.adapter.page_key(url))
        registry = AdapterRegistry((self.adapter,))
        self.assertIs(registry.for_page(PAGE), self.adapter)
        self.assertIsNone(registry.for_page("https://example.com/video"))
        self.assertTrue(registry.owns_media("https://r1.googlevideo.com/videoplayback?range=0-100", PAGE))
        self.assertFalse(registry.owns_media("https://googlevideo.com.evil.example/videoplayback", PAGE))

    def test_audio_pairing_drm_and_silent_tracks(self):
        resources = self.adapter.resources(video_info(), PAGE, "jNQXAC9IVRw")
        self.assertEqual(len(resources), 2)
        self.assertEqual(resources[0].metadata["selector"], "137+bestaudio[ext=m4a]/137+bestaudio")
        self.assertEqual(resources[1].metadata["selector"], "18")
        no_audio = video_info()
        no_audio["formats"] = no_audio["formats"][:1]
        with self.assertRaisesRegex(ValueError, "完整音视频"):
            self.adapter.resources(no_audio, PAGE, "jNQXAC9IVRw")
        with self.assertRaisesRegex(ValueError, "直播"):
            self.adapter.resources({**video_info(), "is_live": True}, PAGE, "jNQXAC9IVRw")

    def test_stable_renditions_and_separate_videos_sessions(self):
        self.store.ingest_resolved("s1", self.adapter.resources(video_info(), PAGE, "jNQXAC9IVRw"))
        original = self.store.list_groups("s1")[0]
        self.store.ingest_resolved("s1", self.adapter.resources(video_info(suffix="renewed"), PAGE, "jNQXAC9IVRw"))
        refreshed = self.store.list_groups("s1")[0]
        self.assertEqual(original["id"], refreshed["id"])
        self.assertEqual([v["id"] for v in original["variants"]], [v["id"] for v in refreshed["variants"]])
        self.assertIn("renewed", refreshed["variants"][0]["url"])
        self.assertEqual(len(refreshed["variants"]), 2)
        other = "aqz-KE-bpKQ"
        self.store.ingest_resolved("s1", self.adapter.resources(video_info(other), PAGE.replace("jNQXAC9IVRw", other), other))
        self.assertEqual(len(self.store.list_groups("s1")), 2)
        self.store.create_session("s2")
        self.assertIsNone(self.store.get_variant("s2", refreshed["variants"][0]["id"]))
        self.store.delete_session("s1")
        self.store.ingest_resolved("s1", self.adapter.resources(video_info(), PAGE, "jNQXAC9IVRw"))
        self.assertEqual(self.store.list_groups("s1"), [])

    def test_plan_keeps_selected_format_and_refresh_source(self):
        resource = self.adapter.resources(video_info(), PAGE, "jNQXAC9IVRw")[0]
        plan = plan_for({"url": resource.url, "metadata": resource.metadata})
        self.assertEqual(plan.backend, "extractor")
        self.assertEqual(plan.url, PAGE)
        self.assertNotIn("token", plan.url)
        self.assertEqual(plan_for({"url": "https://cdn.example/image.jpg"}).backend, "http")
        self.assertEqual(plan_for({"url": "https://cdn.example/dash", "is_playlist": True}).backend, "playlist")


class AsyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_browser_adapter_status_refresh_and_session_cancellation(self):
        from app.browser.manager import BrowserService
        from types import SimpleNamespace
        hub = Hub()
        store = Store(DB(":memory:"), hub)
        store.create_session("s1")
        manager = DownloadManager(store, hub)
        service = BrowserService(store, hub, manager)
        extractor = SimpleNamespace(inspect=AsyncMock(return_value=video_info()))
        service.adapters = AdapterRegistry((YouTubeAdapter(extractor),))
        context = SimpleNamespace(cookies=AsyncMock(return_value=[]), close=AsyncMock())
        service.session = SimpleNamespace(id="s1", current_url=PAGE, context=context)
        service._schedule_capture("s1", PAGE)
        await service._capture_task
        self.assertEqual(service.snapshot("s1")["adapter_status"]["state"], "ready")
        self.assertEqual(len(store.list_groups("s1")), 1)
        self.assertEqual(context.cookies.await_args.args[0], ["https://www.youtube.com/"])
        extractor.inspect.side_effect = RuntimeError("上游拒绝访问（403）")
        service._schedule_capture("s1", PAGE, refresh=True)
        await service._capture_task
        self.assertEqual(service.adapter_status["state"], "error")
        service._schedule_capture("s1", PAGE, refresh=True)
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {"DATA_DIR": temp}):
            await service.close_session("s1")
        self.assertFalse(service._capture_tasks)
        self.assertIsNone(service.adapter_status)
        self.assertFalse(store.session_exists("s1"))
        context.close.assert_awaited_once()

    async def test_backend_registration_without_site_branches(self):
        registry = BackendRegistry()
        handler = AsyncMock()
        registry.register("new-site-engine", handler)
        plan = DownloadPlan("new-site-engine", PAGE)
        await registry.execute({"id": "job"}, plan)
        handler.assert_awaited_once_with({"id": "job"}, plan)
        with self.assertRaises(ValueError):
            registry.register("new-site-engine", handler)

    async def test_cookie_export_and_cleanup_on_failure(self):
        class FailingExtractor(Extractor):
            async def run(self, args, **kwargs):
                path = Path(args[args.index("--cookies") + 1])
                self.cookie_path = path
                assert path.stat().st_mode & 0o777 == 0o600
                assert "session-value" in path.read_text()
                raise RuntimeError("fixture failure")
        extractor = FailingExtractor()
        with patch("app.media.extractor.assert_safe"):
            with self.assertRaisesRegex(RuntimeError, "fixture failure"):
                await extractor.inspect(PAGE, [{"domain": ".youtube.com", "name": "SID", "value": "session-value"}])
        self.assertFalse(extractor.cookie_path.exists())
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "cookies"
            cookie_file(path, [{"domain": ".youtube.com", "name": "evil", "value": "x\ninjected"}])
            self.assertNotIn("injected", path.read_text())

    async def test_cancellation_kills_extractor_and_child(self):
        with tempfile.TemporaryDirectory() as temp:
            pid_file = Path(temp) / "pids"
            script = ("import os,subprocess,time,pathlib; "
                      "p=subprocess.Popen(['sleep','120']); "
                      f"pathlib.Path({str(pid_file)!r}).write_text(str(os.getpid())+' '+str(p.pid)); "
                      "time.sleep(120)")
            task = asyncio.create_task(Extractor().run([sys.executable, "-c", script], timeout=10))
            for _ in range(100):
                if pid_file.exists():
                    break
                await asyncio.sleep(0.02)
            self.assertTrue(pid_file.exists())
            pids = pid_file.read_text().split()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            for pid in pids:
                stat = Path(f"/proc/{pid}/stat")
                self.assertTrue(not stat.exists() or stat.read_text().split()[2] == "Z")

    async def test_cancelled_download_removes_cookies_and_partial_tracks(self):
        started = asyncio.Event()
        class SlowExtractor(Extractor):
            async def run(self, args, **kwargs):
                cookie_path = Path(args[args.index("--cookies") + 1])
                (cookie_path.parent / "media.f137.mp4.part").write_bytes(b"partial")
                started.set()
                await asyncio.Event().wait()
        with tempfile.TemporaryDirectory() as temp, patch("app.media.extractor.assert_safe"):
            root = Path(temp)
            task = asyncio.create_task(SlowExtractor().download(PAGE, "137+140", root / "complete.mp4", [], lambda value: None))
            await started.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.assertEqual(list(root.iterdir()), [])

    async def test_cancelled_preview_returns_an_error_and_can_retry(self):
        started = asyncio.Event()
        store = Store(DB(":memory:"), Hub())
        store.create_session("s1")
        resources = YouTubeAdapter(Extractor()).resources(video_info(), PAGE, "jNQXAC9IVRw")
        store.ingest_resolved("s1", resources)
        variant = store.get_variant("s1", store.list_groups("s1")[0]["variants"][0]["id"])
        manager = DownloadManager(store, Hub())
        async def slow_download(*args):
            started.set()
            await asyncio.Event().wait()
        manager.extractor.download = slow_download
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {"DATA_DIR": temp}), patch("app.media.pipeline.assert_safe"):
            preview = asyncio.create_task(manager.preview_file("s1", variant))
            await started.wait()
            first = store.list_downloads("s1")[0]
            await manager.cancel("s1", first["id"])
            with self.assertRaisesRegex(RuntimeError, "已取消"):
                await preview
            retry = manager.enqueue("s1", variant)
            self.assertNotEqual(retry["id"], first["id"])
            await manager.cancel_session("s1")


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "requires FFmpeg")
class MediaIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_split_download_mux_preview_range_and_cleanup(self):
        """Real yt-dlp + FFmpeg, with local media replacing the blocked YouTube origin."""
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                            "color=c=blue:s=160x90:d=1", "-an", "-c:v", "libx264", str(root / "video.mp4")], check=True)
            subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                            "sine=frequency=440:duration=1", "-vn", "-c:a", "aac", str(root / "audio.m4a")], check=True)
            class QuietHandler(SimpleHTTPRequestHandler):
                def log_message(self, *_):
                    pass
            server = ThreadingHTTPServer(("127.0.0.1", 0), partial(QuietHandler, directory=temp))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            port = server.server_port
            base = f"http://127.0.0.1:{port}"
            info = video_info()
            info.update({"extractor": "youtube", "extractor_key": "Youtube", "webpage_url": PAGE})
            info["formats"] = info["formats"][:2]
            for fmt, filename in zip(info["formats"], ["video.mp4", "audio.m4a"]):
                fmt["url"] = base + "/" + filename
                fmt["protocol"] = "http"
            info_file = root / "fixture.json"
            info_file.write_text(json.dumps(info))
            class FixtureExtractor(Extractor):
                percentages = []
                async def run(self, args, **kwargs):
                    # Only replace metadata acquisition. All selection, media
                    # downloads, merge/recode flags and process handling are real.
                    args = args[:-2] + ["--load-info-json", str(info_file)]
                    original = kwargs["progress"]
                    def progress(value):
                        self.percentages.append(value)
                        original(value)
                    kwargs["progress"] = progress
                    return await super().run(args, **kwargs)
            try:
                with patch.dict(os.environ, {"DATA_DIR": str(root / "data"), "SELF_PORTS": str(port),
                                              "NO_PROXY": "127.0.0.1,localhost"}):
                    store = Store(DB(":memory:"), Hub())
                    store.create_session("s1")
                    adapter = YouTubeAdapter(Extractor())
                    store.ingest_resolved("s1", adapter.resources(info, PAGE, "jNQXAC9IVRw"))
                    variant = store.get_variant("s1", store.list_groups("s1")[0]["variants"][0]["id"])
                    manager = DownloadManager(store, Hub())
                    manager.extractor = FixtureExtractor()
                    # PAGE is the canonical source; DNS/SSRF are tested separately.
                    with patch("app.media.pipeline.assert_safe"), patch("app.media.extractor.assert_safe"):
                        first, second = await asyncio.gather(manager.preview_file("s1", variant), manager.preview_file("s1", variant))
                    self.assertEqual(first, second)
                    self.assertEqual(len(store.list_downloads("s1")), 1)
                    self.assertTrue(manager.extractor.percentages)
                    self.assertEqual(manager.enqueue("s1", variant)["id"], store.list_downloads("s1")[0]["id"])
                    probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type",
                                            "-of", "json", str(first)], capture_output=True, check=True)
                    self.assertEqual({s["codec_type"] for s in json.loads(probe.stdout)["streams"]}, {"video", "audio"})
                    job = store.list_downloads("s1")[0]
                    self.assertEqual(job["state"], "COMPLETED")
                    self.assertTrue(job["filename"].endswith(".mp4"))
                    # Serve the actual prepared file through the production API.
                    from fastapi import FastAPI
                    from app.api.routes import router
                    from types import SimpleNamespace
                    import httpx
                    app = FastAPI()
                    app.include_router(router)
                    app.state.browser = SimpleNamespace(session=SimpleNamespace(id="s1"))
                    app.state.store = store
                    app.state.downloads = manager
                    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                        response = await client.get(f"/api/sessions/s1/variants/{variant['id']}/preview", headers={"Range": "bytes=0-31"})
                    self.assertEqual(response.status_code, 206)
                    self.assertEqual(len(response.content), 32)
                    self.assertIn(b"ftyp", response.content)
                    # A generic direct download still goes through the original backend.
                    generic = {"id": "generic", "url": base + "/video.mp4", "group_title": "direct"}
                    direct = manager.enqueue("s1", generic)
                    await manager._tasks[direct["id"]]
                    self.assertEqual(store.get_download("s1", direct["id"])["state"], "COMPLETED")
                    await manager.cancel_session("s1")
                    self.assertFalse(manager._preview_jobs)
            finally:
                server.shutdown()
                server.server_close()
                thread.join()


if __name__ == "__main__":
    unittest.main()
