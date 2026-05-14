"""开源版：邀请奖励已禁用，所有调用都是 no-op。"""
from __future__ import annotations


async def reward_inviter_on_first_book(session, user) -> None:
    return None
