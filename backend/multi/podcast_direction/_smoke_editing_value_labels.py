"""Smoke test for public editing-value wording.

Run:
    cd backend
    python -m workbench.podcast_direction._smoke_editing_value_labels
"""
from __future__ import annotations

from multi.podcast_direction.parser import render_customer_markdown
from multi.podcast_direction.prompt import build_diagnosis_user
from multi.podcast_direction.report_page import render_report_html
from multi.podcast_direction.judgment_page import render_judgment_html


def _sample() -> dict:
    return {
        "overall_diagnosis": {
            "podcast_name": "测试节目",
            "overall_content_judgment": "整体判断。",
            "content_structure_type": "主方向清晰但有若干分支型",
            "main_directions": ["主线", "分支"],
        },
        "direction_structure": {
            "directions": [
                {
                    "direction_name": "主线",
                    "direction_description": "描述",
                    "sub_topics": ["A", "B", "C"],
                    "episode_evidence": ["第一集", "第二集"],
                    "editing_value": "高",
                },
                {
                    "direction_name": "分支",
                    "direction_description": "描述",
                    "sub_topics": ["D", "E", "F"],
                    "episode_evidence": ["第三集", "第四集"],
                    "editing_value": "中",
                },
                {
                    "direction_name": "边角",
                    "direction_description": "描述",
                    "sub_topics": ["G", "H", "I"],
                    "episode_evidence": ["第五集", "第六集"],
                    "editing_value": "低",
                },
            ],
            "priority_direction": "主线",
            "not_priority_direction": "边角",
        },
        "title_suggestions": {},
        "customer_diagnosis": {"diagnosis_text": "客户诊断。"},
        "quality_check": {"confidence_level": "中", "warnings": []},
        "metadata": {"_input_episode_count": 3, "_input_sampled": False},
    }


def main() -> None:
    final_json = _sample()
    combined = "\n".join(
        [
            render_customer_markdown(final_json),
            render_report_html(final_json),
            render_judgment_html(final_json),
        ]
    )
    assert "整理价值：中" not in combined
    assert "整理价值：低" not in combined
    assert "价值 中" not in combined
    assert "价值 低" not in combined
    assert "整理价值：极高" in combined
    assert "整理价值：高" in combined

    prompt = build_diagnosis_user(
        podcast_name="测试节目",
        podcast_intro="简介",
        episode_list=[{"title": "第一集", "intro": "简介"}],
    )
    assert "高 / 中 / 低" not in prompt
    assert "极高 / 高" in prompt
    print("[OK] editing-value labels avoid medium/low in public wording")


if __name__ == "__main__":
    main()
