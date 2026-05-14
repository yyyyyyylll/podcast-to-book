"""Smoke test for book-fit prompt wording."""
from __future__ import annotations

from multi.podcast_book_fit.prompt import build_book_fit_user


def main() -> None:
    prompt = build_book_fit_user(
        podcast_name="夜夜页页",
        podcast_intro="借读书之名闲聊。",
        episode_list=[
            {"title": "Vol.001 一千零一夜", "intro": "开篇"},
            {"title": "Vol.002 西游记", "intro": "细读"},
        ],
        host_topic_references=[
            {"topic_title": "一千零一夜·系列合集", "episode_count": 10}
        ],
    )
    assert "200-300 字" in prompt
    assert "鼓励主播成书" in prompt
    assert "不需要确定最终书名" in prompt
    assert "值得继续沟通" in prompt
    assert "只推荐 1 期节目" in prompt
    assert "必须等于第一个主题方向、第一个板块下的第一期" in prompt
    assert "recommended_sample_episode" in prompt
    assert "topic_sections" in prompt
    assert "3 个板块" in prompt
    assert "一千零一夜·系列合集" in prompt
    print("[OK] book-fit prompt keeps lightweight encouraging scope")


if __name__ == "__main__":
    main()
