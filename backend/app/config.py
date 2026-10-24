from __future__ import annotations

import os
from pathlib import Path


def data_dir() -> Path:
    return Path(os.environ.get("DATA_DIR", "/data"))


def demo_media_dir() -> Path:
    return Path(os.environ.get("DEMO_MEDIA_DIR", "/app/demo-media"))


def self_ports() -> set[int]:
    raw = os.environ.get("SELF_PORTS", "8080,8000")
    ports: set[int] = set()
    for part in raw.split(","):
        part = part.strip()
        if part:
            ports.add(int(part))
    return ports


def sign_secret() -> bytes:
    return os.environ.get("SIGN_SECRET", "resources-puller-demo").encode()


SCREEN_W = 1440
SCREEN_H = 900
