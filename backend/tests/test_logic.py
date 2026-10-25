from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import DB
from app.hub import Hub
from app.resources.classify import classify
from app.resources.normalize import identity_for, normalize_stem
from app.resources.playlist import parse_m3u8, parse_mpd
from app.resources.store import Store
from app.security.ssrf import SSRFError, assert_safe, clear_cache, ip_allowed
import ipaddress


class NormalizeTests(unittest.TestCase):
    def test_resolution_suffixes_collapse(self) -> None:
        stem, label, width, height = normalize_stem("coast_1280x720")
        self.assertEqual(stem, "coast")
        self.assertEqual(label, "1280x720")
        self.assertEqual((width, height), (1280, 720))
        stem_p, label_p, _w, height_p = normalize_stem("coast-720p")
        self.assertEqual((stem_p, label_p, height_p), ("coast", "720p", 720))
        stem_h, label_h, _w, height_h = normalize_stem("photo_1080")
        self.assertEqual((stem_h, label_h, height_h), ("photo", "1080p", 1080))

    def test_uncertain_names_stay_apart(self) -> None:
        stem, label, _w, _h = normalize_stem("report_2024")
        self.assertEqual(stem, "report_2024")
        self.assertIsNone(label)
        stem_v, label_v, _w, _h = normalize_stem("photo_v2")
        self.assertEqual(stem_v, "photo_v2")
        self.assertIsNone(label_v)

    def test_directory_is_part_of_identity(self) -> None:
        left = identity_for("http://127.0.0.1:8080/demo/media/a/cover.jpg", "image")
        right = identity_for("http://127.0.0.1:8080/demo/media/b/cover.jpg", "image")
        self.assertNotEqual(left[0], right[0])
        coast_a = identity_for("http://127.0.0.1:8080/demo/media/coast_640x360.jpg", "image")
        coast_b = identity_for("http://127.0.0.1:8080/demo/media/coast_1920x1080.jpg", "image")
        self.assertEqual(coast_a[0], coast_b[0])


class ClassifyTests(unittest.TestCase):
    def test_segments_are_not_resources(self) -> None:
        self.assertEqual(classify("http://cdn.example/a/seg_000.ts", "video/mp2t"), "segment")
        self.assertEqual(classify("http://cdn.example/a/chunk.m4s", "video/iso.segment"), "segment")
        self.assertEqual(classify("http://cdn.example/a/master.m3u8", "application/vnd.apple.mpegurl"), "playlist")
        self.assertEqual(classify("http://cdn.example/a/manifest.mpd", "application/dash+xml"), "playlist")
        self.assertEqual(classify("http://cdn.example/a/logo.png?r=1", "image/png"), "image")
        self.assertEqual(classify("http://cdn.example/favicon.ico", "image/x-icon"), "ignore")


class PlaylistTests(unittest.TestCase):
    def test_master_and_media(self) -> None:
        text = "\n".join(
            [
                "#EXTM3U",
                "#EXT-X-STREAM-INF:BANDWIDTH=800000,RESOLUTION=640x360",
                "360p/index.m3u8",
                "#EXT-X-STREAM-INF:BANDWIDTH=1400000,RESOLUTION=1280x720",
                "720p/index.m3u8",
            ]
        )
        parsed = parse_m3u8("http://127.0.0.1:8080/demo/media/hls/master.m3u8", text)
        self.assertEqual(parsed["kind"], "master")
        self.assertEqual(parsed["variants"][1]["label"], "720p")
        self.assertTrue(parsed["variants"][0]["url"].endswith("/hls/360p/index.m3u8"))
        media = parse_m3u8(
            "http://127.0.0.1:8080/demo/media/hls/360p/index.m3u8",
            "#EXTM3U\n#EXTINF:1.0,\nseg_000.ts\n#EXT-X-ENDLIST\n",
        )
        self.assertEqual(media["kind"], "media")
        self.assertTrue(media["segments"][0].endswith("/hls/360p/seg_000.ts"))

    def test_mpd_representations(self) -> None:
        text = """<?xml version="1.0"?>
<MPD xmlns="urn:mpeg:dash:schema:mpd:2011">
  <Period>
    <AdaptationSet>
      <Representation id="360" bandwidth="400000" width="640" height="360">
        <BaseURL>coast_360.mp4</BaseURL>
      </Representation>
      <Representation id="720" bandwidth="900000" width="1280" height="720">
        <BaseURL>coast_720.mp4</BaseURL>
      </Representation>
    </AdaptationSet>
  </Period>
</MPD>
"""
        parsed = parse_mpd("http://127.0.0.1:8080/demo/media/dash/manifest.mpd", text)
        self.assertEqual(len(parsed["renditions"]), 2)
        self.assertEqual(len(parsed["variants"]), 2)
        self.assertEqual(parsed["variants"][0]["label"], "360p")


class SsrfTests(unittest.TestCase):
    def setUp(self) -> None:
        clear_cache()

    def test_blocks_private_and_allows_self(self) -> None:
        self.assertFalse(ip_allowed(ipaddress.ip_address("169.254.169.254"), 80))
        self.assertFalse(ip_allowed(ipaddress.ip_address("10.1.2.3"), 80))
        self.assertFalse(ip_allowed(ipaddress.ip_address("192.168.0.8"), 80))
        self.assertFalse(ip_allowed(ipaddress.ip_address("127.0.0.1"), 22))
        self.assertTrue(ip_allowed(ipaddress.ip_address("127.0.0.1"), 8080))
        assert_safe("http://127.0.0.1:8080/demo/")
        assert_safe("http://127.1:8080/demo/")
        with self.assertRaises(SSRFError):
            assert_safe("http://127.0.0.1:22/")
        with self.assertRaises(SSRFError):
            assert_safe("http://169.254.169.254/latest/meta-data/")
        with self.assertRaises(SSRFError):
            assert_safe("http://10.0.0.1/")
        with self.assertRaises(SSRFError):
            assert_safe("file:///etc/passwd")
        with self.assertRaises(SSRFError):
            assert_safe("http://2130706433/")


class StoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = Store(DB(":memory:"), Hub())
        self.store.create_session("s1")

    def test_groups_resolutions_and_ignores_segments(self) -> None:
        base = "http://127.0.0.1:8080/demo/media"
        for name in ("coast_640x360.jpg", "coast_1280x720.jpg", "coast_1920x1080.jpg"):
            self.store.ingest(
                "s1",
                url=f"{base}/{name}",
                mime="image/jpeg",
                status=200,
                via="network",
            )
        self.store.ingest("s1", url=f"{base}/logo.png", mime="image/png", status=200, via="network")
        self.store.ingest("s1", url=f"{base}/logo.png", mime="image/png", status=200, via="network")
        self.store.ingest("s1", url=f"{base}/a/cover.jpg", mime="image/jpeg", status=200, via="network")
        self.store.ingest("s1", url=f"{base}/b/cover.jpg", mime="image/jpeg", status=200, via="network")
        master = "\n".join(
            [
                "#EXTM3U",
                "#EXT-X-STREAM-INF:BANDWIDTH=800000,RESOLUTION=640x360",
                "360p/index.m3u8",
                "#EXT-X-STREAM-INF:BANDWIDTH=2000000,RESOLUTION=1280x720",
                "720p/index.m3u8",
            ]
        )
        self.store.ingest(
            "s1",
            url=f"{base}/hls/master.m3u8",
            mime="application/vnd.apple.mpegurl",
            status=200,
            via="network",
            body=master,
        )
        self.store.ingest(
            "s1",
            url=f"{base}/hls/360p/index.m3u8",
            mime="application/vnd.apple.mpegurl",
            status=200,
            via="network",
            body="#EXTM3U\n#EXTINF:1,\nseg_000.ts\n",
        )
        self.store.ingest(
            "s1",
            url=f"{base}/hls/360p/seg_000.ts",
            mime="video/mp2t",
            status=200,
            via="network",
        )
        groups = self.store.list_groups("s1")
        images = [group for group in groups if group["kind"] == "image"]
        videos = [group for group in groups if group["kind"] == "video"]
        self.assertEqual(len(images), 4)
        coast = next(group for group in images if group["title"] == "coast")
        self.assertEqual(len(coast["variants"]), 3)
        logo = next(group for group in images if group["title"] == "logo")
        self.assertEqual(len(logo["variants"]), 1)
        self.assertGreaterEqual(logo["variants"][0]["occurrence_count"], 2)
        covers = [group for group in images if group["title"] == "cover"]
        self.assertEqual(len(covers), 2)
        self.assertEqual(len(videos), 1)
        labels = {variant["label"] for variant in videos[0]["variants"]}
        self.assertIn("360p", labels)
        self.assertIn("720p", labels)
        urls = [variant["url"] for group in groups for variant in group["variants"]]
        self.assertFalse(any(url.endswith(".ts") for url in urls))

    def test_late_master_absorbs_media_playlist(self) -> None:
        base = "http://127.0.0.1:8080/demo/media/hls"
        self.store.ingest(
            "s1",
            url=f"{base}/360p/index.m3u8",
            mime="application/vnd.apple.mpegurl",
            status=200,
            via="network",
            body="#EXTM3U\n#EXTINF:1,\nseg_000.ts\n",
        )
        self.store.ingest(
            "s1",
            url=f"{base}/master.m3u8",
            mime="application/vnd.apple.mpegurl",
            status=200,
            via="network",
            body="#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=800000,RESOLUTION=640x360\n360p/index.m3u8\n",
        )
        videos = [group for group in self.store.list_groups("s1") if group["kind"] == "video"]
        self.assertEqual(len(videos), 1)
        self.assertTrue(any("360p/index.m3u8" in variant["url"] for variant in videos[0]["variants"]))


if __name__ == "__main__":
    unittest.main()
