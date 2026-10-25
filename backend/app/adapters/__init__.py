from app.adapters.base import AdapterRegistry
from app.adapters.youtube import YouTubeAdapter
from app.media.extractor import Extractor


def default_registry(extractor: Extractor) -> AdapterRegistry:
    return AdapterRegistry((YouTubeAdapter(extractor),))
