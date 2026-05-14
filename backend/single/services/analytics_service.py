"""开源版：埋点已禁用，所有 track 调用都是 no-op。"""
from __future__ import annotations


async def track(event: str, session=None, **kwargs) -> None:
    return None
