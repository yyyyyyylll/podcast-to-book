"""
排版节点（Typeset）测试

使用已有的 full_pipeline / compose_full 缓存数据测试 PDF 生成。

运行方式：
cd backend
pytest tests/test_typeset.py -v -s
pytest tests/test_typeset.py::test_typeset_from_cache -v -s        # 从缓存生成 PDF
pytest tests/test_typeset.py::test_build_typst_source_only -v -s   # 只生成 Typst 源码（不编译）
"""

import json
import time
from datetime import datetime
from pathlib import Path

import pytest
from core.workflow.state import create_initial_state


def _log(message: str) -> None:
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[typeset-test {ts}] {message}", flush=True)


def test_editor_preface_user_input_preserves_paragraph_indentation():
    """用户输入的编者序应按段落拆分，逐段生成带首行缩进的 #par。"""
    from core.workflow.nodes.typeset import _build_editors_preface

    source = _build_editors_preface("第一段首句。\n第二段首句。\n\n第三段首句。")

    assert source.count('#par(first-line-indent: 0em)[#h(2em)') == 3
    assert '  #par(first-line-indent: 0em)[#h(2em)第一段首句。]' in source
    assert '  #par(first-line-indent: 0em)[#h(2em)第二段首句。]' in source
    assert '  #par(first-line-indent: 0em)[#h(2em)第三段首句。]' in source


def test_epub_preface_user_input_preserves_paragraphs():
    """EPUB 里的编者序也应和 PDF 一样按用户段落输出。"""
    from single.services.epub_service import _build_preface_xhtml

    html = _build_preface_xhtml(
        "第一段首句。\r\n第二段首句。\r\n\r\n第三段首句。",
        {"preface": "编者序"},
    )

    assert html.count("<p>") == 3
    assert "<p>第一段首句。</p>" in html
    assert "<p>第二段首句。</p>" in html
    assert "<p>第三段首句。</p>" in html


@pytest.fixture
def cached_pipeline_data():
    """从 artifacts 加载已缓存的 annotated_content + highlights。"""
    artifacts_dir = Path(__file__).resolve().parent / "artifacts"

    candidates = sorted(
        list(artifacts_dir.glob("full_pipeline_machine_*.json"))
        + list(artifacts_dir.glob("compose_full_machine_*.json")),
        reverse=True,
    )

    for candidate in candidates:
        try:
            data = json.loads(candidate.read_text(encoding="utf-8"))
            annotated = data.get("annotated_content")
            highlights = data.get("highlights")
            composed = data.get("composed_content")

            if annotated and annotated.get("chapters"):
                _log(f"缓存: {candidate.name}, "
                     f"{len(annotated['chapters'])} 个注释章节")
                return {
                    "annotated_content": annotated,
                    "highlights": highlights or {"quotes": [], "methodologies": []},
                    "composed_content": composed,
                    "source_file": candidate.name,
                }
        except Exception:
            continue

    pytest.skip("未找到可用的流水线缓存，请先运行 test_compose.py::test_full_pipeline")


def test_build_typst_source_only(cached_pipeline_data):
    """只测试 Typst 源码生成（不需要 typst 库）。"""
    from core.workflow.nodes.typeset import build_typst_source

    data = cached_pipeline_data
    _log(f"数据来源: {data['source_file']}")

    t0 = time.perf_counter()
    source = build_typst_source(
        data["annotated_content"],
        data["highlights"],
    )
    elapsed = time.perf_counter() - t0

    _log(f"Typst 源码生成完成: {len(source)} 字符, 耗时 {elapsed:.3f}s")

    assert source, "Typst 源码不应为空"
    assert "= " in source, "源码应包含章节标题"
    assert "#set page" in source, "源码应包含页面设置"

    # 保存 .typ 源码用于检查
    artifacts_dir = Path(__file__).resolve().parent / "artifacts"
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    typ_path = artifacts_dir / f"typeset_source_{ts}.typ"
    typ_path.write_text(source, encoding="utf-8")
    _log(f"Typst 源码已保存: {typ_path.name}")

    # 打印摘要
    lines = source.split("\n")
    headings = [l for l in lines if l.startswith("= ")]
    pull_quotes = source.count("#pull-quote")
    step_cards = source.count("#step-card")
    footnotes = source.count("#footnote")
    print(f"\n{'='*60}")
    print(f"章节: {len(headings)}")
    for h in headings:
        print(f"  {h}")
    print(f"金句卡片: {pull_quotes}")
    print(f"步骤卡: {step_cards}")
    print(f"脚注: {footnotes}")
    print(f"总行数: {len(lines)}")
    print(f"{'='*60}")


@pytest.mark.asyncio
async def test_typeset_from_cache(cached_pipeline_data):
    """完整测试：从缓存数据生成 PDF。"""
    data = cached_pipeline_data
    _log(f"数据来源: {data['source_file']}")

    state = create_initial_state(
        task_id="typeset-test",
        audio_path="",
        title="排版测试",
        author="test",
    )
    state["annotated_content"] = data["annotated_content"]
    state["highlights"] = data["highlights"]

    from core.workflow.nodes.typeset import typeset_node

    _log("开始排版...")
    t0 = time.perf_counter()
    output = await typeset_node(state)
    elapsed = time.perf_counter() - t0

    pdf_path = output.get("pdf_path", "")
    typst_source = output.get("typst_source", "")

    _log(f"排版完成: 耗时 {elapsed:.2f}s")
    _log(f"PDF: {pdf_path}")
    _log(f"Typst 源码: {len(typst_source)} 字符")

    assert pdf_path, "pdf_path 不应为空"
    assert Path(pdf_path).exists(), f"PDF 文件不存在: {pdf_path}"
    assert Path(pdf_path).stat().st_size > 1000, "PDF 文件过小，可能生成失败"

    # 复制 PDF 到 artifacts 便于查看
    artifacts_dir = Path(__file__).resolve().parent / "artifacts"
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    import shutil
    dest = artifacts_dir / f"typeset_book_{ts}.pdf"
    shutil.copy2(pdf_path, dest)
    _log(f"PDF 已复制到: {dest.name}")

    typ_dest = artifacts_dir / f"typeset_source_{ts}.typ"
    typ_dest.write_text(typst_source, encoding="utf-8")
    _log(f"Typst 源码已保存: {typ_dest.name}")


@pytest.mark.asyncio
async def test_typeset_with_cover_image(cached_pipeline_data):
    """测试带封面图的 PDF 生成。"""
    data = cached_pipeline_data
    _log(f"数据来源: {data['source_file']}")

    cover_url = "https://image.xyzcdn.net/FsdAUJWHwlC6vIt2S-poUdrZs5K6.png"

    state = create_initial_state(
        task_id="typeset-cover-test",
        audio_path="",
        title="排版测试（含封面）",
        author="test",
        cover_url=cover_url,
    )
    state["annotated_content"] = data["annotated_content"]
    state["highlights"] = data["highlights"]

    from core.workflow.nodes.typeset import typeset_node

    _log("开始排版（含封面图）...")
    t0 = time.perf_counter()
    output = await typeset_node(state)
    elapsed = time.perf_counter() - t0

    pdf_path = output.get("pdf_path", "")
    typst_source = output.get("typst_source", "")

    _log(f"排版完成: 耗时 {elapsed:.2f}s")
    _log(f"PDF: {pdf_path}")

    assert pdf_path, "pdf_path 不应为空"
    assert Path(pdf_path).exists(), f"PDF 文件不存在: {pdf_path}"
    assert "#image" in typst_source, "Typst 源码应包含 #image 指令"

    artifacts_dir = Path(__file__).resolve().parent / "artifacts"
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    import shutil
    dest = artifacts_dir / f"typeset_book_cover_{ts}.pdf"
    shutil.copy2(pdf_path, dest)
    _log(f"PDF 已复制到: {dest.name}")
