"""
host pipeline 装配阶段的离线测试：跳过 ASR / compose（重外部依赖），
直接构造 ComposedChapter 输入，验证 assemble_book_md + render_docx 端到端。
"""
from __future__ import annotations

import shutil
import zipfile
from pathlib import Path

import pytest

from single.host.services.book_md import ComposedChapter, assemble_book_md
from single.host.services.docx_renderer import render_docx

pandoc_available = shutil.which("pandoc") is not None


def _fake_chapters() -> list[ComposedChapter]:
    return [
        ComposedChapter(
            chapter_title="如何与时间和解",
            sections=[
                {
                    "section_title": "时间的两种用法",
                    "content": "正文段落 A，应当应用 Normal 样式（首行缩进 2 字符、行距 1.5）。",
                },
                {
                    "section_title": "把焦虑还给焦虑",
                    "content": "正文段落 B，含中英文混排 Mixed text 验证标点挤压。",
                },
            ],
            source_episode_title="第 12 期 时间的褶皱",
        ),
        ComposedChapter(
            chapter_title="独处的能力",
            sections=[
                {"section_title": "独处不是孤独", "content": "正文段落 C。"},
            ],
        ),
    ]


def test_assemble_book_md_structure():
    md = assemble_book_md(
        book_title="测试书名",
        book_author="测试作者",
        chapters=_fake_chapters(),
    )
    assert md.startswith("% 测试书名")
    assert "% 测试作者" in md.splitlines()[1]
    assert "# 第一章" in md and "如何与时间和解" in md
    assert "## 时间的两种用法" in md
    assert "# 第二章" in md and "独处的能力" in md


def test_assemble_book_md_empty_chapters_raises():
    with pytest.raises(ValueError):
        assemble_book_md(book_title="x", book_author="y", chapters=[])


@pytest.mark.skipif(not pandoc_available, reason="本机未装 pandoc")
def test_book_md_to_docx_end_to_end(tmp_path: Path):
    md_text = assemble_book_md(
        book_title="测试书名",
        book_author="测试作者",
        chapters=_fake_chapters(),
    )
    md_path = tmp_path / "book.md"
    md_path.write_text(md_text, encoding="utf-8")

    out = tmp_path / "book.docx"
    render_docx(md_path, out)
    assert out.exists() and out.stat().st_size > 1500

    with zipfile.ZipFile(out) as zf:
        body = zf.read("word/document.xml").decode("utf-8", errors="ignore")
        # 章/节文字应在文档体里出现
        for needle in ("如何与时间", "独处的能力", "时间的两种用法"):
            assert needle in body, f"docx 中找不到：{needle}"
