"""开源版：积分系统已禁用，所有操作都是 no-op。"""
from __future__ import annotations


async def check_balance(session, user_id: str, amount: int) -> bool:
    return True


async def deduct(session, user_id: str, amount: int, **kwargs) -> None:
    return None


async def refund(session, user_id: str, amount: int, **kwargs) -> None:
    return None


async def get_balance(session, user_id: str) -> int:
    return 999_999
