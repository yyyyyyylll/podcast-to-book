"""开源版的 User：仅保留路由代码引用的字段，不建数据库表。

如果你要给开源版加多用户/登录，把它改成真正的 SQLAlchemy 模型即可。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class User:
    id: str = "local"
    phone: str = ""
    status: str = "active"
    invited_by: Optional[str] = None

    def __post_init__(self):
        if not self.id:
            self.id = "local"
