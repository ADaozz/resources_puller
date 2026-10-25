"""Download dispatch is independent of site identity.

Adapters choose a backend in metadata; registering another site does not require
editing the download manager or the HTTP routes.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Awaitable, Callable


@dataclass(frozen=True)
class DownloadPlan:
    backend: str
    url: str
    metadata: dict = field(default_factory=dict)


class BackendRegistry:
    def __init__(self):
        self._handlers: dict[str, Callable[[dict, DownloadPlan], Awaitable[None]]] = {}

    def register(self, name: str, handler: Callable[[dict, DownloadPlan], Awaitable[None]]) -> None:
        if name in self._handlers:
            raise ValueError(f"重复的下载后端：{name}")
        self._handlers[name] = handler

    async def execute(self, job: dict, plan: DownloadPlan) -> None:
        try:
            handler = self._handlers[plan.backend]
        except KeyError:
            raise ValueError(f"未知的下载后端：{plan.backend}") from None
        await handler(job, plan)


def plan_for(variant: dict) -> DownloadPlan:
    metadata = dict(variant.get("metadata") or {})
    generic = "playlist" if variant.get("is_playlist") else "http"
    return DownloadPlan(metadata.get("backend", generic), metadata.get("source_url") or variant["url"], metadata)
