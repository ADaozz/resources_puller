from __future__ import annotations

import logging
import shutil
import traceback
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.routes import router as api_router
from app.browser.manager import BrowserService
from app.config import data_dir
from app.db import DB
from app.demo_site import router as demo_router
from app.hub import Hub
from app.media.pipeline import DownloadManager
from app.resources.store import Store

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger("resources_puller")


@asynccontextmanager
async def lifespan(app: FastAPI):
    root = data_dir()
    if root.exists():
        shutil.rmtree(root, ignore_errors=True)
    root.mkdir(parents=True, exist_ok=True)
    db = DB(str(root / "app.db"))
    hub = Hub()
    store = Store(db, hub)
    downloads = DownloadManager(store, hub)
    browser = BrowserService(store, hub, downloads)
    app.state.browser = browser
    app.state.store = store
    app.state.downloads = downloads
    app.state.hub = hub
    try:
        await browser.start()
    except Exception:
        browser.start_error = traceback.format_exc()
        log.exception("浏览器启动失败")
    try:
        yield
    finally:
        try:
            await browser.stop()
        except Exception:
            log.exception("浏览器关闭失败")


app = FastAPI(title="网页媒体资源解析器", lifespan=lifespan)
app.include_router(api_router)
app.include_router(demo_router)
