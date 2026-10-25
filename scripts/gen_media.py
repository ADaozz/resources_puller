#!/usr/bin/env python3
"""用 ffmpeg 生成测试页用的图片、渐进视频、HLS 和 DASH。"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path


def run(cmd: list[str]) -> None:
    print("+", " ".join(cmd), flush=True)
    subprocess.check_call(cmd)


def image(path: Path, color: str, size: str, label: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    font = "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"
    draw = []
    if Path(font).exists():
        draw = [
            "-vf",
            f"drawtext=fontfile={font}:text='{label}':fontcolor=white:fontsize=36:x=24:y=24",
        ]
    try:
        run(
            [
                "ffmpeg",
                "-y",
                "-f",
                "lavfi",
                "-i",
                f"color=c={color}:s={size}",
                *draw,
                "-frames:v",
                "1",
                str(path),
            ]
        )
    except subprocess.CalledProcessError:
        run(
            [
                "ffmpeg",
                "-y",
                "-f",
                "lavfi",
                "-i",
                f"color=c={color}:s={size}",
                "-frames:v",
                "1",
                str(path),
            ]
        )


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("用法: gen_media.py <输出目录>")
    root = Path(sys.argv[1])
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True)
    image(root / "logo.png", "0xF5D76E", "320x180", "logo")
    image(root / "coast_640x360.jpg", "0x2255AA", "640x360", "640x360")
    image(root / "coast_1280x720.jpg", "0x2277CC", "1280x720", "1280x720")
    image(root / "coast_1920x1080.jpg", "0x2299EE", "1920x1080", "1920x1080")
    image(root / "a" / "cover.jpg", "0xEE6644", "200x200", "dir-a")
    image(root / "b" / "cover.jpg", "0x44AA66", "200x200", "dir-b")
    image(root / "lazy.jpg", "0x9B59B6", "300x200", "lazy")
    image(root / "clicked.png", "0xE74C3C", "400x300", "clicked")
    image(root / "signed_photo.jpg", "0x1ABC9C", "360x240", "signed")
    image(root / "banner.jpg", "0x3D8BFF", "960x160", "banner")

    clip = root / "clip.mp4"
    run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=640x360:rate=30:duration=2",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=44100:duration=2",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            str(clip),
        ]
    )
    shutil.copyfile(clip, root / "slow_src.mp4")

    for name, scale in (("360p", "640:360"), ("720p", "1280:720")):
        folder = root / "hls" / name
        folder.mkdir(parents=True)
        run(
            [
                "ffmpeg",
                "-y",
                "-i",
                str(clip),
                "-vf",
                f"scale={scale}",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-b:a",
                "64k",
                "-hls_time",
                "1",
                "-hls_playlist_type",
                "vod",
                "-hls_segment_filename",
                str(folder / "seg_%03d.ts"),
                str(folder / "index.m3u8"),
            ]
        )
    master = root / "hls" / "master.m3u8"
    master.write_text(
        "\n".join(
            [
                "#EXTM3U",
                "#EXT-X-VERSION:3",
                "#EXT-X-STREAM-INF:BANDWIDTH=800000,RESOLUTION=640x360",
                "360p/index.m3u8",
                "#EXT-X-STREAM-INF:BANDWIDTH=2000000,RESOLUTION=1280x720",
                "720p/index.m3u8",
                "",
            ]
        ),
        encoding="utf-8",
    )

    dash = root / "dash"
    dash.mkdir()
    dash_ok = False
    try:
        run(
            [
                "ffmpeg",
                "-y",
                "-i",
                str(clip),
                "-map",
                "0:v:0",
                "-map",
                "0:a:0",
                "-map",
                "0:v:0",
                "-map",
                "0:a:0",
                "-b:v:0",
                "400k",
                "-s:v:0",
                "640x360",
                "-b:v:1",
                "900k",
                "-s:v:1",
                "1280x720",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-use_timeline",
                "1",
                "-use_template",
                "1",
                "-seg_duration",
                "1",
                "-adaptation_sets",
                "id=0,streams=v id=1,streams=a",
                "-f",
                "dash",
                str(dash / "manifest.mpd"),
            ]
        )
        dash_ok = (dash / "manifest.mpd").exists()
    except subprocess.CalledProcessError:
        dash_ok = False
    if not dash_ok:
        for name, scale in (("coast_360.mp4", "640:360"), ("coast_720.mp4", "1280:720")):
            run(
                [
                    "ffmpeg",
                    "-y",
                    "-i",
                    str(clip),
                    "-vf",
                    f"scale={scale}",
                    "-an",
                    "-c:v",
                    "libx264",
                    "-pix_fmt",
                    "yuv420p",
                    str(dash / name),
                ]
            )
        (dash / "manifest.mpd").write_text(
            """<?xml version="1.0" encoding="UTF-8"?>
<MPD xmlns="urn:mpeg:dash:schema:mpd:2011" profiles="urn:mpeg:dash:profile:isoff-on-demand:2011" type="static" mediaPresentationDuration="PT2S" minBufferTime="PT1S">
  <Period>
    <AdaptationSet mimeType="video/mp4" segmentAlignment="true">
      <Representation id="360" bandwidth="400000" width="640" height="360">
        <BaseURL>coast_360.mp4</BaseURL>
      </Representation>
      <Representation id="720" bandwidth="900000" width="1280" height="720">
        <BaseURL>coast_720.mp4</BaseURL>
      </Representation>
    </AdaptationSet>
  </Period>
</MPD>
""",
            encoding="utf-8",
        )
    print("媒体已生成:", root)


if __name__ == "__main__":
    main()
