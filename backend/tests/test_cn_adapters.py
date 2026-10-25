from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.adapters import default_registry
from app.adapters.bilibili import BilibiliAdapter
from app.adapters.douyin import DouyinAdapter
from app.db import DB
from app.hub import Hub
from app.media.extractor import Extractor, public_error
from app.resources.store import Store

BILI = "https://www.bilibili.com/video/BV1GJ411x7h7"
DOUYIN = "https://www.douyin.com/video/6961737553342991651"


def bilibili_info():
    return {"id": "BV1GJ411x7h7_p2", "title": "视频 P2", "formats": [
        {"format_id": "100026", "url": "https://a.bilivideo.com/video.m4s", "ext": "mp4",
         "height": 1080, "width": 1920, "vcodec": "avc1", "acodec": "none"},
        {"format_id": "30280", "url": "https://a.bilivideo.com/audio.m4s", "ext": "m4a",
         "vcodec": "none", "acodec": "mp4a"},
        {"format_id": "16", "url": "https://a.bilivideo.com/progressive.mp4", "ext": "mp4",
         "vcodec": None, "acodec": None, "height": 360},
    ]}


def douyin_info():
    return {"id": "6961737553342991651", "title": "抖音样例", "thumbnails": [{"url": "https://img.example/cover.jpg"}],
            "formats": [
                {"format_id": "play_addr_h264", "url": "https://v1.douyinvod.com/video/abc", "ext": "mp4",
                 "width": 1080, "height": 1920, "vcodec": "h264", "acodec": "aac"},
                {"format_id": "download_addr-0", "url": "https://v1.zjcdn.com/video/abc", "ext": "mp4",
                 "vcodec": "h264", "acodec": "aac"},
                {"format_id": "vvc", "url": "https://v1.zjcdn.com/video/vvc", "ext": "mp4",
                 "vcodec": "bytevc2", "acodec": "aac"},
            ]}


class ChineseAdapterTests(unittest.TestCase):
    def test_bilibili_video_alias_embed_and_part_identity(self):
        adapter = BilibiliAdapter(Extractor())
        for url in [BILI, "https://m.bilibili.com/video/BV1GJ411x7h7?spm_id_from=333"]:
            self.assertEqual(adapter.page_key(url), "BV1GJ411x7h7:p1")
        self.assertEqual(adapter.page_key(BILI + "?p=2"), "BV1GJ411x7h7:p2")
        self.assertEqual(adapter.page_key("https://player.bilibili.com/player.html?bvid=BV1GJ411x7h7&page=2"), "BV1GJ411x7h7:p2")
        self.assertEqual(adapter.page_key("https://www.bilibili.com/video/av170001"), "av170001:p1")
        for url in [BILI + "?p=0", BILI + "?p=-1", BILI + "?p=two",
                    "https://bilibili.com.evil.example/video/BV1GJ411x7h7",
                    "https://live.bilibili.com/1234", "file://www.bilibili.com/video/BV1GJ411x7h7"]:
            self.assertIsNone(adapter.page_key(url))

    def test_douyin_watch_share_and_modal(self):
        adapter = DouyinAdapter(Extractor())
        for url in [DOUYIN, "https://www.iesdouyin.com/share/video/6961737553342991651/",
                    "https://www.douyin.com/jingxuan?modal_id=6961737553342991651"]:
            self.assertEqual(adapter.page_key(url), "6961737553342991651")
        for url in ["https://www.douyin.com/jingxuan", "https://www.douyin.com/video/bad",
                    "https://douyin.com.evil.example/video/6961737553342991651", "https://live.douyin.com/123"]:
            self.assertIsNone(adapter.page_key(url))

    def test_registered_hosts_and_cdn_ownership(self):
        registry = default_registry(Extractor())
        self.assertEqual(registry.for_page(BILI).name, "bilibili")
        self.assertEqual(registry.for_page(DOUYIN).name, "douyin")
        self.assertEqual(registry.for_page("https://youtu.be/jNQXAC9IVRw").name, "youtube")
        self.assertTrue(registry.owns_media("https://a.bilivideo.com/a.m4s", BILI))
        self.assertTrue(registry.owns_media("https://v1.douyinvod.com/video/a", DOUYIN))
        self.assertTrue(registry.owns_media("https://v5.zjcdn.com/a/media-video-avc1", DOUYIN))
        self.assertFalse(registry.owns_media("https://bilivideo.com.evil.example/a.m4s", BILI))
        self.assertFalse(registry.owns_media("https://img.hdslb.com/banner.jpg", BILI))
        # A feed without a concrete video keeps the existing generic capture path.
        self.assertFalse(registry.owns_media("https://v1.douyinvod.com/video/a", "https://www.douyin.com/jingxuan"))

    def test_bilibili_dash_audio_and_legacy_progressive(self):
        adapter = BilibiliAdapter(Extractor())
        resources = adapter.resources(bilibili_info(), BILI + "?p=2", "BV1GJ411x7h7:p2")
        self.assertEqual(len(resources), 2)
        self.assertEqual(resources[0].metadata["selector"], "100026+bestaudio[ext=m4a]/100026+bestaudio")
        self.assertEqual(resources[1].metadata["selector"], "16")
        with self.assertRaisesRegex(ValueError, "具体分 P"):
            adapter.resources({"_type": "multi_video", "entries": []}, BILI, "id")
        with self.assertRaisesRegex(ValueError, "直播"):
            adapter.resources({"is_live": True}, BILI, "id")

    def test_douyin_muxed_formats_and_unsupported_codec(self):
        adapter = DouyinAdapter(Extractor())
        resources = adapter.resources(douyin_info(), DOUYIN, "6961737553342991651")
        self.assertEqual(len(resources), 2)
        self.assertEqual(resources[0].metadata["selector"], "play_addr_h264")
        self.assertTrue(resources[0].metadata["file_preview"])
        self.assertEqual(resources[0].metadata["thumbnail"], "https://img.example/cover.jpg")
        self.assertEqual(resources[0].group_key, "video|douyin|6961737553342991651")

    def test_parts_stay_separate_and_expiring_links_keep_ids(self):
        adapter = BilibiliAdapter(Extractor())
        store = Store(DB(":memory:"), Hub())
        store.create_session("s1")
        for part in (1, 2):
            store.ingest_resolved("s1", adapter.resources(bilibili_info(), BILI + f"?p={part}", f"BV1GJ411x7h7:p{part}"))
        groups = store.list_groups("s1")
        self.assertEqual(len(groups), 2)
        original = groups[1]["variants"][0]["id"]
        renewed = bilibili_info()
        renewed["formats"][0]["url"] += "?deadline=new"
        store.ingest_resolved("s1", adapter.resources(renewed, BILI + "?p=2", "BV1GJ411x7h7:p2"))
        self.assertEqual(store.list_groups("s1")[1]["variants"][0]["id"], original)

    def test_user_errors_do_not_expose_signed_links(self):
        for error, expected in [("Fresh cookies needed", "Cookie"), ("HTTP Error 412", "412"),
                                ("please login", "登录"), ("rate limit exceeded", "频繁"),
                                ("certificate verify failed", "证书")]:
            message = public_error(error + " https://cdn.example/video?secret=signed")
            self.assertIn(expected, message)
            self.assertNotIn("signed", message)


class ChineseResolutionTests(unittest.IsolatedAsyncioTestCase):
    async def test_failed_adapter_preserves_browser_media_and_success_deduplicates(self):
        from app.browser.manager import BrowserService
        from app.media.pipeline import DownloadManager
        store = Store(DB(":memory:"), Hub())
        store.create_session("s1")
        service = BrowserService(store, Hub(), DownloadManager(store, Hub()))
        service.session = SimpleNamespace(id="s1", current_url=DOUYIN)
        service._set_adapter_status("s1", {"state": "loading"})
        url = "https://v1.douyinvod.com/video/captured.mp4"
        service._ingest_capture("s1", url=url, mime="video/mp4", status=206, via="network")
        self.assertEqual(store.list_groups("s1"), [])
        service._set_adapter_status("s1", {"state": "error"})
        self.assertEqual(len(store.list_groups("s1")), 1)
        self.assertFalse(service._pending_media)
        service._set_adapter_status("s1", {"state": "loading"})
        service._ingest_capture("s1", url=url + "?range=100-200", mime="video/mp4", status=206, via="network")
        service._set_adapter_status("s1", {"state": "ready"})
        self.assertFalse(service._pending_media)
        service._ingest_capture("s1", url=url + "?range=200-300", mime="video/mp4", status=206, via="network")
        self.assertEqual(len(store.list_groups("s1")[0]["variants"]), 1)

    async def test_bilibili_alias_and_part_are_preserved_for_download(self):
        extractor = SimpleNamespace(inspect=AsyncMock(return_value=bilibili_info()))
        resources = await BilibiliAdapter(extractor).resolve("https://www.bilibili.com/video/av170001?p=2&tracking=1", [])
        extractor.inspect.assert_awaited_once_with("https://www.bilibili.com/video/av170001?p=2", [])
        self.assertEqual(resources[0].metadata["source_url"], BILI + "?p=2")
        self.assertEqual(resources[0].metadata["video_id"], "BV1GJ411x7h7:p2")

    async def test_share_and_modal_urls_use_same_canonical_video(self):
        extractor = SimpleNamespace(inspect=AsyncMock(return_value=douyin_info()))
        adapter = DouyinAdapter(extractor)
        first = await adapter.resolve("https://www.iesdouyin.com/share/video/6961737553342991651/", [])
        second = await adapter.resolve("https://www.douyin.com/jingxuan?modal_id=6961737553342991651", [])
        self.assertEqual(first[0].key, second[0].key)
        self.assertEqual(first[0].metadata["source_url"], DOUYIN)
        self.assertEqual(extractor.inspect.await_args.args[0], DOUYIN)
        self.assertEqual(adapter.cookie_urls(DOUYIN), ["https://www.douyin.com/", "https://www.iesdouyin.com/"])
