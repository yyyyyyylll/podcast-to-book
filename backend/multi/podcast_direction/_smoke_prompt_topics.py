"""Smoke test for diagnosis prompt topic-reference injection."""
from __future__ import annotations

from multi.podcast_direction.prompt import build_diagnosis_user


def main() -> None:
    prompt = build_diagnosis_user(
        podcast_name="夜夜页页",
        podcast_intro="借读书之名闲聊。",
        episode_list=[{"title": "Vol.001", "intro": "简介"}],
        host_topic_references=[
            {
                "topic_title": "一千零一夜·系列合集",
                "episode_count": 2,
                "episode_titles": ["Vol.001", "Vol.002"],
            }
        ],
    )
    assert "主播已有专题/系列线索" in prompt
    assert "一千零一夜·系列合集" in prompt
    assert "不要直接照搬" in prompt
    print("[OK] prompt includes host topic references as guarded evidence")


if __name__ == "__main__":
    main()
