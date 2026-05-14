import hashlib
import json


def compute_content_hash(
    episode_id: str,
    user_title: str = "",
    user_host_name: str = "",
    user_guest_names: list[str] | None = None,
    editor_preface: str = "",
) -> str:
    """根据决定书籍内容的输入参数计算哈希（不含 cover_style）。

    相同 hash 表示生成的文字内容完全一致，
    只有封面模板不同时仍可复用中间结果。
    """
    data = {
        "episode_id": episode_id,
        "user_title": (user_title or "").strip(),
        "user_host_name": (user_host_name or "").strip(),
        "user_guest_names": sorted(
            [g.strip() for g in (user_guest_names or []) if g.strip()]
        ),
        "editor_preface": (editor_preface or "").strip(),
    }
    return hashlib.sha256(
        json.dumps(data, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()[:32]
