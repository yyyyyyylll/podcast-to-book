"""Smoke test for book-fit runner helper validation."""
from __future__ import annotations

from multi.podcast_book_fit.runner import normalize_book_fit_result


def main() -> None:
    test_normalize_long_podcast_copy()
    test_short_podcast_falls_back_to_chapter_titles_and_episode_list()
    test_normalize_creates_single_sample_episode_and_topic_sections()
    print("[OK] runner helper normalizes book-fit result")


def test_normalize_long_podcast_copy() -> None:
    result = normalize_book_fit_result(
        {
            "podcast_name": "夜夜页页",
            "fit_level": "很强",
            "book_fit_summary": "这档播客以阅读和叙事为主要入口，70集已经形成稳定表达，也有连续栏目积累，适合继续沟通成书可能。后续可以先从已有专题切入，观察哪些内容最能代表主播的表达气质，再进一步判断整理路径。",
            "host_profile_tags": ["经典细读"],
            "existing_series_clues": [{"title": "一千零一夜·系列合集", "episode_count": 10}],
            "possible_book_directions": [{"direction": "经典细读精选", "reason": "连续性强。"}],
            "client_next_step": "建议先从已有栏目切入。" * 20,
        },
        podcast_name="夜夜页页",
        metadata={"episode_count": 70},
    )
    assert result["fit_level"] in {"值得继续沟通", "非常值得继续沟通"}
    assert result["metadata"]["episode_count"] == 70
    assert result["podcast_name"] == "夜夜页页"
    assert "client_next_step" not in result
    assert "episode_titles" in result["possible_book_directions"][0]
    assert "70集" not in result["book_fit_summary"]
    assert "70 期播客" in result["book_fit_summary"]


def test_short_podcast_falls_back_to_chapter_titles_and_episode_list() -> None:
    result = normalize_book_fit_result(
        {
            "podcast_name": "可能性褶皱",
            "fit_level": "值得继续沟通",
            "book_fit_summary": "这档播客已有 13集内容，适合从章节角度先整理。",
            "possible_book_directions": [
                {
                    "direction": "个人“内核稳定”训练：从课题分离、底层自信到情绪免疫",
                    "reason": "第03、01集适合作为同一本书里的大章节。",
                }
            ],
            "client_next_step": "建议先整理一个章节样张。",
        },
        podcast_name="可能性褶皱",
        metadata={"episode_count": 13},
        episode_list=[
            {"title": "03. 建立自己的行动系统"},
            {"title": "02. 如何克服拖延"},
            {"title": "01. 下行时代，如何重新找回掌控感"},
        ],
    )
    direction = result["possible_book_directions"][0]
    assert result["metadata"]["book_unit"] == "chapter"
    assert direction["suggested_title"] == "《个人“内核稳定”训练》"
    assert "：" not in direction["suggested_title"]
    assert direction["evidence_title"] == "可先参考这 2 期节目"
    assert direction["episode_titles"] == [
        "03. 建立自己的行动系统",
        "01. 下行时代，如何重新找回掌控感",
    ]


def test_normalize_creates_single_sample_episode_and_topic_sections() -> None:
    result = normalize_book_fit_result(
        {
            "podcast_name": "关系练习",
            "fit_level": "值得继续沟通",
            "book_fit_summary": "这档播客已有 36 集内容，适合先从一条成熟主题线做样张。",
            "recommended_sample_episode": {
                "episode_title": "EP06 冲突",
                "why_this_episode": "这一期有明确问题、个人经验和可展开结构，适合先做单篇样章。",
                "source_direction": "在关系里重新理解自己",
            },
            "possible_book_directions": [
                {
                    "direction": "在关系里重新理解自己",
                    "suggested_title": "《在关系里重新理解自己》",
                    "reason": "这条线有足够节目积累，适合先做样张。",
                    "topic_sections": [
                        {
                            "section_title": "自我边界",
                            "section_reason": "围绕边界与自我感展开。",
                            "episode_titles": ["EP01 边界", "EP02 拒绝", "EP03 选择", "EP04 独处"],
                        },
                        {
                            "section_title": "亲密关系",
                            "episode_titles": ["EP05 亲密", "EP06 冲突", "EP07 和解"],
                        },
                        {
                            "section_title": "职业选择",
                            "episode_titles": ["EP08 转向", "EP09 稳定", "EP10 重新开始"],
                        },
                    ],
                },
                {
                    "direction": "日常生活观察",
                    "suggested_title": "《把日子说清楚》",
                    "reason": "可作为后续备选。",
                    "episode_titles": ["EP11 日常", "EP12 城市", "EP13 家务"],
                },
            ],
            "client_next_step": "建议先做一条主题样张。",
        },
        podcast_name="关系练习",
        metadata={"episode_count": 36},
    )
    assert result["recommended_sample_episode"]["episode_title"] == "EP01 边界"
    assert result["recommended_sample_episode"]["source_direction"] == "在关系里重新理解自己"
    assert (
        result["recommended_sample_episode"]["why_this_episode"]
        == "建议先用这一期做样章，方便快速看到单集改写后的成稿质感。"
    )
    assert "recommended_sample_line" not in result
    assert len(result["possible_book_directions"][0]["topic_sections"]) == 3
    assert all(
        3 <= len(section["episode_titles"]) <= 4
        for section in result["possible_book_directions"][0]["topic_sections"]
    )
    assert result["possible_book_directions"][1]["topic_sections"][0]["section_title"] == "可先收录的节目"


if __name__ == "__main__":
    main()
