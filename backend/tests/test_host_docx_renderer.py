"""docx_renderer 烟雾测试：markdown → .docx 端到端。"""
from __future__ import annotations

import shutil
import zipfile
from pathlib import Path

import pytest

from single.host.services.docx_renderer import (
    REFERENCE_DOCX,
    PandocNotInstalled,
    render_docx,
)

pandoc_available = shutil.which("pandoc") is not None


@pytest.mark.skipif(not pandoc_available, reason="本机未装 pandoc")
def test_render_docx_smoke(tmp_path: Path):
    assert REFERENCE_DOCX.exists(), "reference.docx 缺失，请先跑 build_reference_docx"

    book_md = tmp_path / "book.md"
    book_md.write_text(
        "% 测试书名\n"
        "% 测试作者\n"
        "\n"
        "# 第一章 开篇\n"
        "\n"
        "这是第一段正文，应当应用 Normal 样式：12pt、行距 1.5、首行缩进 2 字符。\n"
        "\n"
        "## 第一节 引言\n"
        "\n"
        "节标题下的段落。\n"
        "\n"
        "> 这是一段引文。\n"
        "\n"
        "# 第二章\n"
        "\n"
        "第二章应当从新的一页开始（Heading 1 段前分页）。\n",
        encoding="utf-8",
    )
    out = tmp_path / "book.docx"
    render_docx(book_md, out)
    assert out.exists() and out.stat().st_size > 1000

    # 校验产出确为合法 docx（zip 结构 + 含 word/document.xml）
    with zipfile.ZipFile(out) as zf:
        names = zf.namelist()
        assert "word/document.xml" in names
        body = zf.read("word/document.xml").decode("utf-8", errors="ignore")
        # pandoc 会按空格 / 字体切分 run，所以章标题文本可能分散在多个 <w:t>
        assert "第一章" in body and "开篇" in body
        assert "第二章" in body


def test_pandoc_missing_raises(monkeypatch, tmp_path: Path):
    """模拟 pandoc 不在 PATH 时应抛 PandocNotInstalled。"""
    monkeypatch.setattr("app.host.services.docx_renderer.shutil.which", lambda _: None)
    md = tmp_path / "x.md"
    md.write_text("# x\n", encoding="utf-8")
    with pytest.raises(PandocNotInstalled):
        render_docx(md, tmp_path / "x.docx")
