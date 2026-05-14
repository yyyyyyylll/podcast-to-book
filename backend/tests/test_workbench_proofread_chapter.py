from multi.runners.proofread_chapter import (
    apply_high_confidence_issues,
    apply_replacement_issues,
    build_section_source_text,
    mark_source_only_issues,
    normalize_proofread_issues,
    render_human_report_html,
    render_human_report_markdown,
    replacement_required,
    should_surface_for_human_review,
)


def test_build_section_source_text_uses_one_based_segment_ids():
    segments = [
        {"speaker": "fofo", "text": "开场白"},
        {"speaker": "哈梨", "text": "主体内容"},
        {"speaker": "fofo", "text": "回应内容"},
    ]
    section = {"source_segment_ids": [2, 3]}

    source = build_section_source_text(section, segments)

    assert "【哈梨】主体内容" in source
    assert "【fofo】回应内容" in source
    assert "开场白" not in source


def test_build_section_source_text_can_include_neighbor_context():
    segments = [
        {"speaker": "fofo", "text": "前文"},
        {"speaker": "哈梨", "text": "主体内容"},
        {"speaker": "fofo", "text": "后文"},
    ]
    section = {"source_segment_ids": [2]}

    source = build_section_source_text(section, segments, context_window=1)

    assert "【fofo】前文" in source
    assert "【哈梨】主体内容" in source
    assert "【fofo】后文" in source


def test_build_section_source_text_falls_back_to_time_range_when_ids_missing():
    segments = [
        {"speaker": "fofo", "text": "太早", "start_time": 0, "end_time": 5},
        {"speaker": "哈梨", "text": "命中", "start_time": 10, "end_time": 20},
        {"speaker": "fofo", "text": "太晚", "start_time": 40, "end_time": 50},
    ]
    section = {"time_range": [8, 25]}

    source = build_section_source_text(section, segments)

    assert "【哈梨】命中" in source
    assert "太早" not in source
    assert "太晚" not in source


def test_normalize_proofread_issues_keeps_expected_schema_and_defaults():
    raw = {
        "issues": [
            {
                "type": "meaning_drift",
                "severity": "high",
                "location": "第 1 段",
                "current_text": "最终稿",
                "replacement_text": "修订稿",
                "source_evidence": "ASR 证据",
                "suggestion": "建议",
            },
            {"type": "typo", "current_text": "错字"},
        ],
        "section_summary": "整体可用",
    }

    normalized = normalize_proofread_issues(raw)

    assert normalized["section_summary"] == "整体可用"
    assert normalized["issues"][0]["type"] == "meaning_drift"
    assert normalized["issues"][0]["severity"] == "high"
    assert normalized["issues"][0]["replacement_text"] == "修订稿"
    assert normalized["issues"][1]["type"] == "typo"
    assert normalized["issues"][1]["severity"] == "medium"


def test_apply_high_confidence_issues_only_applies_exact_safe_replacements():
    chapter = {
        "sections": [
            {
                "section_index": 1,
                "content": "这种丢脸和受挫感，本质上是因为混淆了了他人的课题和自己的课题。",
            }
        ]
    }
    report = {
        "sections": [
            {
                "section_index": 1,
                "issues": [
                    {
                        "type": "typo",
                        "severity": "low",
                        "confidence": "high",
                        "needs_human_review": False,
                        "current_text": "混淆了了他人的课题",
                        "replacement_text": "混淆了他人的课题",
                    },
                    {
                        "type": "meaning_drift",
                        "severity": "medium",
                        "confidence": "high",
                        "needs_human_review": True,
                        "current_text": "自己的课题",
                        "replacement_text": "自己的任务",
                    },
                ],
            }
        ]
    }

    updated, applications = apply_high_confidence_issues(chapter, report)

    assert "混淆了他人的课题" in updated["sections"][0]["content"]
    assert "混淆了了他人的课题" not in updated["sections"][0]["content"]
    assert "自己的课题" in updated["sections"][0]["content"]
    applied = [a for a in applications if a["status"] == "applied"]
    skipped = [a for a in applications if a["status"] == "skipped"]
    assert len(applied) == 1
    assert applied[0]["status"] == "applied"
    assert len(skipped) == 1


def test_apply_high_confidence_issues_skips_noop_replacements():
    chapter = {"sections": [{"section_index": 1, "content": "原文"}]}
    report = {
        "sections": [
            {
                "section_index": 1,
                "issues": [
                    {
                        "type": "typo",
                        "confidence": "high",
                        "needs_human_review": False,
                        "current_text": "原文",
                        "replacement_text": "原文",
                    }
                ],
            }
        ]
    }

    updated, applications = apply_high_confidence_issues(chapter, report)

    assert updated["sections"][0]["content"] == "原文"
    assert applications[0]["status"] == "skipped"
    assert applications[0]["reason"] == "replacement_same_as_current"


def test_apply_replacement_issues_can_apply_all_exact_replacements_even_if_review_needed():
    chapter = {"sections": [{"section_index": 1, "content": "原句A。原句B。"}]}
    report = {
        "sections": [
            {
                "section_index": 1,
                "issues": [
                    {
                        "type": "meaning_drift",
                        "confidence": "medium",
                        "needs_human_review": True,
                        "current_text": "原句A",
                        "replacement_text": "改句A",
                    },
                    {
                        "type": "typo",
                        "confidence": "low",
                        "needs_human_review": False,
                        "current_text": "原句B",
                        "replacement_text": "改句B",
                    },
                ],
            }
        ]
    }

    updated, applications = apply_replacement_issues(chapter, report, require_high_confidence=False)

    assert updated["sections"][0]["content"] == "改句A。改句B。"
    assert len([a for a in applications if a["status"] == "applied"]) == 2


def test_render_human_report_markdown_prioritizes_review_items():
    report = {
        "chapter_title": "第一章",
        "issue_count": 2,
        "severity_counts": {"high": 0, "medium": 1, "low": 1},
        "auto_apply": {"applied_count": 1, "applications": [{"status": "applied", "current_text": "错", "replacement_text": "对"}]},
        "sections": [
            {
                "section_index": 1,
                "section_title": "小节",
                "issues": [
                    {
                        "type": "meaning_drift",
                        "severity": "medium",
                        "confidence": "medium",
                        "needs_human_review": True,
                        "current_text": "问题文本",
                        "source_evidence": "源证据",
                        "suggestion": "处理建议",
                        "reason": "原因",
                    },
                    {
                        "type": "typo",
                        "severity": "low",
                        "confidence": "high",
                        "needs_human_review": False,
                        "current_text": "错",
                        "replacement_text": "对",
                    },
                ],
            }
        ],
    }

    md = render_human_report_markdown(report)

    assert "# 校对报告：第一章" in md
    assert "## 需要人工审核" in md
    assert "问题文本" in md
    assert "源证据" in md
    assert "## 已自动应用" in md
    assert "`错` → `对`" in md


def test_should_surface_for_human_review_only_keeps_true_uncertainty_and_speaker_errors():
    assert should_surface_for_human_review({
        "type": "speaker_mismatch",
        "severity": "medium",
        "needs_human_review": False,
    })
    assert should_surface_for_human_review({
        "type": "meaning_drift",
        "severity": "high",
        "needs_human_review": False,
    })
    assert should_surface_for_human_review({
        "type": "unsupported_inference",
        "severity": "medium",
        "needs_human_review": True,
    })
    assert not should_surface_for_human_review({
        "type": "unsupported_inference",
        "severity": "medium",
        "needs_human_review": False,
    })
    assert not should_surface_for_human_review({
        "type": "asr_uncertain",
        "severity": "low",
        "needs_human_review": True,
        "source_only": True,
    })


def test_replacement_required_for_editable_issues_but_not_uncertainty():
    assert replacement_required({"type": "typo"})
    assert replacement_required({"type": "awkward_sentence"})
    assert replacement_required({"type": "meaning_drift", "needs_human_review": False})
    assert not replacement_required({"type": "asr_uncertain"})
    assert not replacement_required({"type": "speaker_mismatch", "needs_human_review": True})


def test_mark_source_only_issues_removes_non_body_asr_uncertain_from_review():
    section = {"content": "正文里没有开场白。"}
    normalized = {
        "issues": [
            {
                "type": "asr_uncertain",
                "severity": "low",
                "confidence": "high",
                "needs_human_review": True,
                "current_text": "【fofo】大家好，欢迎收听我们的读书分享节目《可能性褶皱》。我是fofo。",
            }
        ]
    }

    marked = mark_source_only_issues(normalized, section)

    assert marked["issues"][0]["source_only"] is True
    assert marked["issues"][0]["needs_human_review"] is False
    assert "source_only_not_in_body" in marked["issues"][0]["reason"]


def test_render_human_report_html_contains_review_ui():
    report = {
        "chapter_title": "第一章",
        "issue_count": 2,
        "severity_counts": {"high": 0, "medium": 1, "low": 1},
        "auto_apply": {"applied_count": 1, "applications": [{"status": "applied", "section_index": 1, "current_text": "错", "replacement_text": "对"}]},
        "sections": [
            {
                "section_index": 1,
                "section_title": "小节",
                "issues": [
                    {
                        "type": "meaning_drift",
                        "severity": "medium",
                        "confidence": "medium",
                        "needs_human_review": True,
                        "current_text": "问题文本",
                        "source_evidence": "源证据",
                        "suggestion": "处理建议",
                        "reason": "原因",
                    }
                ],
            }
        ],
    }

    html = render_human_report_html(report)

    assert "<!doctype html>" in html
    assert "校对审阅台" in html
    assert "data-filter=\"review\"" in html
    assert "问题文本" in html
    assert "源证据" in html
    assert "已自动应用" in html
    assert "function setFilter" in html
