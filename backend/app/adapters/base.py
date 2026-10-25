from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class MediaResource:
    """A stable rendition. Download metadata must not contain credentials."""

    key: str
    group_key: str
    title: str
    url: str
    label: str
    width: int | None = None
    height: int | None = None
    mime: str = "video/mp4"
    metadata: dict = field(default_factory=dict)


class SiteAdapter(Protocol):
    name: str

    def page_key(self, url: str) -> str | None: ...

    def cookie_urls(self, url: str) -> list[str]: ...

    def owns_media(self, url: str, page_url: str) -> bool: ...

    async def resolve(self, url: str, cookies: list[dict]) -> list[MediaResource]: ...


class AdapterRegistry:
    def __init__(self, adapters: tuple[SiteAdapter, ...] = ()):
        self._adapters = adapters

    def for_page(self, url: str) -> SiteAdapter | None:
        return next((adapter for adapter in self._adapters if adapter.page_key(url)), None)

    def owns_media(self, url: str, page_url: str) -> bool:
        return any(adapter.owns_media(url, page_url) for adapter in self._adapters)
