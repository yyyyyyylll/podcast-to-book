"""Smoke test for host-owned podcast topic extraction.

Run:
    cd backend
    python -m workbench.podcast_direction._smoke_topics
"""
from __future__ import annotations

from multi.podcast_direction.topics import extract_host_topic_references


def main() -> None:
    episodes = [
        {
            "title": "Vol.001 《一千零一夜》开篇",
            "intro": '<p><a href="https://www.xiaoyuzhoufm.com/podcast-topic/abc123">一千零一夜·系列合集</a></p>',
        },
        {
            "title": "Vol.002 《一千零一夜》继续",
            "intro": '<p><a href="https://www.xiaoyuzhoufm.com/podcast-topic/abc123?utm=x">一千零一夜·系列合集</a><a href="https://www.xiaoyuzhoufm.com/podcast-topic/abc123">一千零一夜·系列合集</a></p>',
        },
        {
            "title": "Vol.003 另一条线",
            "intro": '<p><a href="https://www.xiaoyuzhoufm.com/podcast-topic/def456">疯人传</a></p>',
        },
        {"title": "Vol.004 无专题", "intro": "<p>普通简介</p>"},
    ]

    topics = extract_host_topic_references(episodes)
    assert len(topics) == 2, topics
    assert topics[0]["topic_title"] == "一千零一夜·系列合集", topics
    assert topics[0]["episode_count"] == 2, topics
    assert topics[0]["episode_titles"] == [
        "Vol.001 《一千零一夜》开篇",
        "Vol.002 《一千零一夜》继续",
    ], topics
    assert topics[0]["topic_id"] == "abc123", topics
    assert topics[1]["topic_title"] == "疯人传", topics
    print("[OK] host topic references extracted and grouped")


if __name__ == "__main__":
    main()
