"""Smoke test for the static judgment-focused diagnosis page."""
from __future__ import annotations

from multi.podcast_direction.judgment_page import render_judgment_html


def main() -> None:
    html = render_judgment_html(
        {
            "overall_diagnosis": {
                "podcast_name": "夜夜页页",
                "content_structure_type": "主方向清晰但有若干分支型",
                "main_directions": ["经典叙事细读", "趣味知识简史"],
                "overall_content_judgment": "这是一档以阅读为入口的节目。",
            },
            "direction_structure": {
                "directions": [
                    {
                        "direction_name": "经典叙事细读",
                        "editing_value": "高",
                        "sub_topics": ["西游记", "一千零一夜", "文本源流"],
                        "episode_evidence": ["西游记第001回", "一千零一夜开篇"],
                    },
                    {
                        "direction_name": "闲聊自述",
                        "editing_value": "低",
                        "sub_topics": ["幕后"],
                        "episode_evidence": ["年度总结"],
                    },
                ],
                "priority_direction": "经典叙事细读",
                "priority_reason": "连续性最强。",
                "not_priority_direction": "闲聊自述",
                "not_priority_reason": "证据较散。",
            },
            "customer_diagnosis": {
                "main_risk": "不要把所有分支平均展开。",
                "recommended_sub_topics": ["西游记", "一千零一夜"],
            },
            "host_topic_references": [
                {"topic_title": "一千零一夜·系列合集", "episode_count": 10}
            ],
            "quality_check": {
                "confidence_level": "中",
                "manual_review_needed": True,
                "warnings": ["需人工复核"],
            },
            "metadata": {"_input_episode_count": 70, "_input_sampled": False},
        }
    )
    assert "<!doctype html>" in html.lower()
    assert "判断页" in html
    assert "可以继续整理" in html
    assert "经典叙事细读" in html
    assert "一千零一夜·系列合集" in html
    assert "需人工复核" in html
    print("[OK] judgment page renders decision-focused sections")


if __name__ == "__main__":
    main()
