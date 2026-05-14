"""开源版的 deps：单用户模型，所有"当前用户"都返回 `_LocalUser`，没有 JWT 校验。

如果你需要给开源版加多用户/登录，可以替换这个文件 + 加 single/api/auth.py + 在 main.py 注册。
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Optional

from fastapi import Depends, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from single.models.user import User

_bearer = HTTPBearer(auto_error=False)


def _local_user() -> User:
    return User(id="local", phone="", status="active", invited_by=None)


# ── 用户身份相关 ─────────────────────────────────────────────────────
async def get_current_user(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> User:
    return _local_user()


async def get_optional_user(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> Optional[User]:
    return _local_user()


async def resolve_download_user(
    request: Request,
    kind: str,
    creds: HTTPAuthorizationCredentials | None,
) -> User:
    return _local_user()


async def get_user_for_pdf_download(
    request: Request,
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> User:
    return _local_user()


async def get_user_for_epub_download(
    request: Request,
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> User:
    return _local_user()


# ── 兼容签发接口（不再使用，但部分代码可能 import 它们）─────────────
def create_access_token(user_id: str, phone: str = "") -> str:
    return "local-no-auth"


def create_refresh_token(user_id: str, phone: str = "") -> str:
    return "local-no-auth"


def create_download_token(user_id: str, task_id: str, kind: str, ttl_seconds: int = 600) -> str:
    return "local-no-auth"


def decode_download_token(token: str, task_id: str, kind: str) -> str:
    return "local"


def decode_token(token: str, expected_type: str = "access") -> dict:
    return {"sub": "local", "type": expected_type}
