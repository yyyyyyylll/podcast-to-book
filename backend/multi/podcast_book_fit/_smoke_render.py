"""Smoke test for book-fit HTML rendering."""
from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path

from multi.podcast_book_fit.render import render_book_fit_html
from multi.podcast_book_fit.render import render_book_fit_pdf


def main() -> None:
    test_pdf_render_ignores_stale_playwright_browser_path()
    html = render_book_fit_html(
        {
            "podcast_name": "夜夜页页",
            "fit_level": "非常值得继续沟通",
            "book_fit_summary": "这档播客已经形成稳定表达，适合先从已有连续栏目切入沟通成书可能。",
            "host_profile_tags": ["经典细读", "知识漫游"],
            "existing_series_clues": [
                {"title": "一千零一夜·系列合集", "episode_count": 10}
            ],
            "host_topic_references": [
                {"topic_title": "一千零一夜·系列合集", "episode_count": 10}
            ],
            "possible_book_directions": [
                {
                    "direction": "经典细读精选",
                    "suggested_title": "《把经典讲给今天的人听》",
                    "reason": "连续性强，适合先试整理。",
                    "evidence_mode": "from_series",
                    "evidence_title": "可先参考「一千零一夜·系列合集」",
                    "episode_titles": [
                        "Vol.001 一千零一夜开篇",
                        "Vol.002 山鲁佐德",
                    ],
                    "topic_sections": [
                        {
                            "section_title": "故事入口",
                            "section_reason": "先用最容易进入的故事建立阅读兴趣。",
                            "episode_titles": [
                                "Vol.001 一千零一夜开篇",
                                "Vol.002 山鲁佐德",
                                "Vol.003 第一夜",
                            ],
                        },
                        {
                            "section_title": "人物与命运",
                            "section_reason": "把人物关系整理成可读路径。",
                            "episode_titles": [
                                "Vol.004 国王",
                                "Vol.005 商人与魔鬼",
                                "Vol.006 渔夫",
                            ],
                        },
                        {
                            "section_title": "文本源流",
                            "section_reason": "补足经典作品的背景。",
                            "episode_titles": [
                                "Vol.007 版本",
                                "Vol.008 翻译",
                                "Vol.009 流传",
                            ],
                        },
                    ],
                }
            ],
            "recommended_sample_episode": {
                "episode_title": "Vol.002 山鲁佐德",
                "source_direction": "经典细读精选",
                "why_this_episode": "这一期人物关系清楚，适合先整理成一篇可阅读的样章。",
            },
            "client_next_step": "建议先从一组成熟栏目中选择 6-10 期做样章。",
            "metadata": {"episode_count": 70, "source_url": "https://example.com"},
        }
    )
    assert "<!doctype html>" in html.lower()
    assert "播客成书建议" in html
    assert "初步建议" in html
    assert "非常值得继续沟通" not in html
    assert "值得继续沟通" not in html
    assert "沟通判断" not in html
    assert "metric-grid" not in html
    assert "hero" not in html
    assert "70 期播客" in html
    assert "70 集资料" not in html
    assert "已有专题 / 栏目线索" not in html
    assert "已有专题 / 栏目线索" not in html
    assert "screenshot-sheet" in html
    assert "compact-grid" in html
    assert "letter-spacing:0.03em" in html
    assert "推荐样章节目" in html
    assert "Vol.002 山鲁佐德" in html
    assert "建议先用这一期做样章，方便快速看到单集改写后的成稿质感。" in html
    assert "只建议先用这一期" not in html
    assert "推荐样张线" not in html
    assert "故事入口" in html
    assert "人物与命运" in html
    assert "文本源流" in html
    assert "整书标题候选：" in html
    assert "directions-section" in html
    assert "break-after:avoid" in html
    assert "主题方向：" in html
    assert "建议理由：" in html
    assert "可参考板块" in html
    assert "《把经典讲给今天的人听》" in html
    assert "可先参考「一千零一夜·系列合集」" in html
    assert "Vol.001 一千零一夜开篇" in html
    assert "下一步整理建议" not in html
    assert "建议先从一组成熟栏目" not in html
    assert "后台" not in html
    assert "运营" not in html
    assert "生成其他播客建议" in html
    assert "导出 PDF" in html
    assert "window.print()" not in html
    assert "download" in html
    assert "http://127.0.0.1:8765/" in html
    test_short_podcast_renders_chapter_framing()
    test_hide_unreliable_series_section()
    print("[OK] book-fit HTML renders customer-facing advice sections")


def test_pdf_render_ignores_stale_playwright_browser_path() -> None:
    previous = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = "/Users/jh/Library/Caches/ms-playwright"
    try:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            html_path = root / "book_fit_judgment.html"
            pdf_path = root / "book_fit_judgment.pdf"
            html_path.write_text("<!doctype html><html><body>PDF ok</body></html>", encoding="utf-8")
            assert asyncio.run(render_book_fit_pdf(html_path, pdf_path))
            assert pdf_path.exists()
            assert pdf_path.stat().st_size > 1000
    finally:
        if previous is None:
            os.environ.pop("PLAYWRIGHT_BROWSERS_PATH", None)
        else:
            os.environ["PLAYWRIGHT_BROWSERS_PATH"] = previous


def test_hide_unreliable_series_section() -> None:
    html = render_book_fit_html(
        {
            "podcast_name": "测试播客",
            "book_fit_summary": "这档播客适合先从节目标题连续性判断。",
            "host_profile_tags": [],
            "existing_series_clues": [{"title": "零散线索", "episode_count": 1}],
            "host_topic_references": [{"topic_title": "零散线索", "episode_count": 1}],
            "possible_book_directions": [],
            "client_next_step": "",
            "metadata": {"episode_count": 3},
        }
    )
    assert "已有专题 / 栏目线索" not in html
    assert "零散线索" not in html


def test_short_podcast_renders_chapter_framing() -> None:
    html = render_book_fit_html(
        {
            "podcast_name": "可能性褶皱",
            "book_fit_summary": "这档播客适合先整理成同一本书里的章节样张。",
            "host_profile_tags": [],
            "possible_book_directions": [
                {
                    "direction": "个人内核稳定",
                    "suggested_title": "《个人内核稳定》",
                    "reason": "适合作为大章节。",
                    "episode_titles": ["EP01 找回掌控感"],
                    "evidence_title": "可先参考这 1 期节目",
                }
            ],
            "client_next_step": "",
            "metadata": {"episode_count": 13, "book_unit": "chapter"},
        }
    )
    assert "章节标题候选：" in html
    assert "可参考的大章节 / 样章方向" in html
    assert "整书标题候选：" not in html


if __name__ == "__main__":
    main()
