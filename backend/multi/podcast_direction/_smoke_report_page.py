"""Smoke test for the static diagnosis report page renderer.

Run:
    cd backend
    python -m workbench.podcast_direction._smoke_report_page
"""
from __future__ import annotations

from multi.podcast_direction.report_page import render_report_html


def main() -> None:
    html = render_report_html(
        {
            "overall_diagnosis": {
                "podcast_name": "夜夜页页",
                "content_structure_type": "主方向清晰但有若干分支型",
                "main_directions": ["经典细读连载"],
                "overall_content_judgment": "这是一档以阅读为入口的节目。",
            },
            "direction_structure": {
                "directions": [
                    {
                        "direction_name": "经典细读",
                        "direction_description": "围绕原典逐回细读。",
                        "sub_topics": ["西游记", "一千零一夜", "史记"],
                        "episode_evidence": ["西游记第001回", "一千零一夜开篇"],
                        "editing_value": "高",
                    }
                ],
                "priority_direction": "经典细读",
                "not_priority_direction": "闲聊自述",
            },
            "title_suggestions": {
                "professional_titles": [{"title": "《经典细读》", "reason": "稳"}],
                "literary_titles": [],
                "clear_titles": [],
            },
            "customer_diagnosis": {
                "diagnosis_text": "客户可见诊断正文。",
                "recommended_direction": "经典细读",
                "recommended_sub_topics": ["西游记"],
                "main_risk": "不要混入过多闲聊。",
            },
            "host_topic_references": [
                {
                    "topic_title": "一千零一夜·系列合集",
                    "episode_count": 2,
                    "episode_titles": ["一千零一夜开篇", "一千零一夜继续"],
                    "url": "https://www.xiaoyuzhoufm.com/podcast-topic/abc123",
                }
            ],
            "quality_check": {"confidence_level": "中", "warnings": ["需人工复核"]},
            "metadata": {"_input_episode_count": 70, "_input_sampled": False},
        }
    )
    assert "<!doctype html>" in html.lower()
    assert "夜夜页页" in html
    assert "主播已有专题" in html
    assert "一千零一夜·系列合集" in html
    assert "完整 70 集" in html
    assert "客户可见诊断正文" in html
    print("[OK] report page renders key diagnosis sections")


if __name__ == "__main__":
    main()
