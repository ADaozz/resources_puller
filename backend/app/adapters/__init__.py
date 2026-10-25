from app.adapters.base import AdapterRegistry
from app.adapters.bilibili import BilibiliAdapter
from app.adapters.douyin import DouyinAdapter
from app.adapters.youtube import YouTubeAdapter
from app.media.extractor import Extractor


def default_registry(extractor: Extractor) -> AdapterRegistry:
    return AdapterRegistry((YouTubeAdapter(extractor), BilibiliAdapter(extractor), DouyinAdapter(extractor)))
