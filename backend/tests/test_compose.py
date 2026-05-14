"""
成稿节点（Compose）测试

使用已有的转写结果作为输入，测试 compose_node 和 annotation_node。

运行方式：
cd backend
pytest tests/test_compose.py -v -s
pytest tests/test_compose.py::test_compose_only -v -s   # 只测成稿
pytest tests/test_compose.py::test_full_pipeline -v -s  # 成稿+提炼+注释
"""
import json
import time
from datetime import datetime
from pathlib import Path

import pytest
from core.workflow.state import create_initial_state


def _log(message: str) -> None:
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[compose-test {ts}] {message}", flush=True)


def _dump_json(path: Path, data) -> None:
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )


def _dump_md(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


def _build_compose_report(composed: dict, highlights: dict | None, annotated: dict | None) -> str:
    """生成可读的 Markdown 审查报告"""
    lines = ["# 成稿审查报告", ""]

    # 概览
    chapters = composed.get("chapters", [])
    lines += [
        "## 概览",
        "",
        f"- **内容类型**: {composed.get('content_type', '?')}",
        f"- **核心主题**: {composed.get('core_theme', '?')}",
        f"- **关键词**: {'、'.join(composed.get('theme_keywords', []))}",
        f"- **板块数**: {len(chapters)}",
        f"- **说话人**: {'、'.join(s.get('name', '') for s in composed.get('speakers', []))}",
        f"- **prompt版本**: v{composed.get('prompt_version', 0)}",
        f"- **耗时**: {composed.get('elapsed_seconds', 0):.1f}s",
        f"- **结构说明**: {composed.get('structure_rationale', '')}",
        "",
    ]

    # 各章节
    lines += ["---", "", "## 成稿内容", ""]
    total_words = 0
    for i, ch in enumerate(chapters, 1):
        title = ch.get("title", f"第{i}章")
        content = ch.get("content", "")
        key_points = ch.get("key_points", [])
        word_count = len(content)
        total_words += word_count

        lines += [
            f"### {i}. {title}",
            f"*字数: {word_count} | 片段: {ch.get('source_segment_ids', [])}*",
            "",
            content,
            "",
        ]
        if key_points:
            lines += [f"**要点**: {' / '.join(key_points)}", ""]
        lines.append("")

    lines += [f"**总字数**: {total_words}", ""]

    # 精华提炼
    if highlights:
        lines += ["---", "", "## 精华提炼", ""]
        quotes = highlights.get("quotes", [])
        methodologies = highlights.get("methodologies", [])

        if quotes:
            lines += ["### 金句", ""]
            for q in quotes:
                if isinstance(q, dict):
                    text = q.get("text", "")
                    placement = q.get("placement", "")
                    chapter = q.get("chapter_title", "")
                    after_p = q.get("after_paragraph")
                    label = f" `[{placement}]`" if placement else ""
                    if placement == "inline_card" and after_p is not None:
                        label += f" `P{after_p}后`"
                    source = f" — {chapter}" if chapter else ""
                    lines += [f"> {text}{label}{source}", ""]
                else:
                    lines += [f"> {q}", ""]

        if methodologies:
            lines += ["### 方法论", ""]
            for m in methodologies:
                name = m.get("name", "")
                steps = m.get("steps", [])
                visual = m.get("visual_type", "")
                chapter = m.get("chapter_title", "")
                after_p = m.get("after_paragraph")
                header = f"**{name}**"
                if visual:
                    header += f" `[{visual}]`"
                if after_p is not None:
                    header += f" `P{after_p}后`"
                if chapter:
                    header += f" — {chapter}"
                lines += [header]
                for s in steps:
                    lines += [f"- {s}"]
                lines += [""]

    # 注释版成稿
    if annotated:
        ann_chapters = annotated.get("chapters", [])
        total_fn = annotated.get("total_footnotes", 0)
        lines += [
            "---",
            "",
            f"## 注释版成稿（共 {total_fn} 个脚注）",
            "",
        ]
        for i, ch in enumerate(ann_chapters, 1):
            title = ch.get("title", f"第{i}章")
            content = ch.get("content", "")
            lines += [f"### {i}. {title}", "", content, ""]

    return "\n".join(lines)


# ============ 测试用例 ============

@pytest.fixture
def transcription_from_cache():
    """从 artifacts 目录加载已有的转写结果"""
    artifacts_dir = Path(__file__).resolve().parent / "artifacts"

    # 优先找带 segments 数据的 workflow 机器输出
    candidates = sorted(artifacts_dir.glob("workflow_machine_*.json"), reverse=True)
    for candidate in candidates:
        try:
            data = json.loads(candidate.read_text(encoding="utf-8"))
            # 找到 transcription stage 的输出
            for stage_result in data.get("stage_results", []):
                if stage_result.get("stage") == "transcription":
                    transcription = stage_result["output"].get("transcription")
                    if transcription and transcription.get("segments"):
                        _log(f"从缓存加载转写数据: {candidate.name}, "
                             f"片段数: {len(transcription['segments'])}")
                        return transcription, data.get("initial_state", {})
        except Exception:
            continue

    pytest.skip("未找到可用的转写缓存，请先运行 test_workflow.py 中的转写测试")


@pytest.mark.asyncio
async def test_compose_only(transcription_from_cache):
    """测试成稿节点（compose）"""
    transcription, initial_state_data = transcription_from_cache

    state = create_initial_state(
        task_id=initial_state_data.get("task_id", "compose-test"),
        audio_path=initial_state_data.get("audio_path", ""),
        title=initial_state_data.get("title", "测试"),
        author=initial_state_data.get("author", ""),
        host_name=initial_state_data.get("host_name", ""),
        guest_names=initial_state_data.get("guest_names", []),
        company_names=initial_state_data.get("company_names", []),
        podcast_intro=initial_state_data.get("podcast_intro", ""),
        proper_nouns=initial_state_data.get("proper_nouns", []),
    )
    state["transcription"] = transcription

    from core.workflow.nodes.compose import compose_node

    _log(f"开始 compose 测试，输入 {len(transcription['segments'])} 个片段")
    t0 = time.perf_counter()
    output = await compose_node(state)
    elapsed = time.perf_counter() - t0

    composed = output.get("composed_content", {})
    chapters = composed.get("chapters", [])

    _log(f"完成: {len(chapters)} 个板块, 耗时 {elapsed:.1f}s")

    assert composed, "composed_content 不应为空"
    assert chapters, "章节列表不应为空"
    assert 3 <= len(chapters) <= 10, f"章节数量异常: {len(chapters)}"
    for ch in chapters:
        assert ch.get("title"), "章节标题不应为空"
        assert len(ch.get("content", "")) >= 200, f"章节「{ch.get('title')}」内容过短"

    # 输出 artifacts
    artifacts_dir = Path(__file__).resolve().parent / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    task_id = state["task_id"]

    _dump_json(artifacts_dir / f"compose_machine_{task_id}_{ts}.json", {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "task_id": task_id,
        "composed_content": composed,
    })
    _dump_md(
        artifacts_dir / f"compose_review_{task_id}_{ts}.md",
        _build_compose_report(composed, None, None),
    )
    _log("artifacts 已保存")


@pytest.mark.asyncio
async def test_full_pipeline(transcription_from_cache):
    """测试完整新流水线：compose → extraction → annotation"""
    transcription, initial_state_data = transcription_from_cache

    state = create_initial_state(
        task_id=initial_state_data.get("task_id", "compose-full-test"),
        audio_path=initial_state_data.get("audio_path", ""),
        title=initial_state_data.get("title", "测试"),
        author=initial_state_data.get("author", ""),
        host_name=initial_state_data.get("host_name", ""),
        guest_names=initial_state_data.get("guest_names", []),
        company_names=initial_state_data.get("company_names", []),
        podcast_intro=initial_state_data.get("podcast_intro", ""),
        proper_nouns=initial_state_data.get("proper_nouns", []),
    )
    state["transcription"] = transcription

    from core.workflow.nodes.compose import compose_node
    from core.workflow.nodes.extraction import extraction_node
    from core.workflow.nodes.annotation import annotation_node

    _log(f"开始完整流水线测试，输入 {len(transcription['segments'])} 个片段")

    # 1. Compose
    _log("节点1: compose...")
    t0 = time.perf_counter()
    output = await compose_node(state)
    state.update(output)
    _log(f"compose 完成: {len(state['composed_content']['chapters'])} 个板块, "
         f"耗时 {time.perf_counter() - t0:.1f}s")

    # 2. Extraction
    _log("节点2: extraction...")
    t0 = time.perf_counter()
    output = await extraction_node(state)
    state.update(output)
    highlights = state.get("highlights", {})
    _log(f"extraction 完成: {len(highlights.get('quotes', []))} 条金句, "
         f"{len(highlights.get('methodologies', []))} 个方法论, "
         f"耗时 {time.perf_counter() - t0:.1f}s")

    # 3. Annotation
    _log("节点3: annotation...")
    t0 = time.perf_counter()
    output = await annotation_node(state)
    state.update(output)
    annotated = state.get("annotated_content", {})
    _log(f"annotation 完成: {annotated.get('total_footnotes', 0)} 个脚注, "
         f"耗时 {time.perf_counter() - t0:.1f}s")

    assert state.get("composed_content"), "composed_content 不应为空"
    assert state.get("highlights"), "highlights 不应为空"
    assert state.get("annotated_content"), "annotated_content 不应为空"

    # 输出 artifacts
    artifacts_dir = Path(__file__).resolve().parent / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    task_id = state["task_id"]

    _dump_json(artifacts_dir / f"compose_full_machine_{task_id}_{ts}.json", {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "task_id": task_id,
        "composed_content": state["composed_content"],
        "highlights": highlights,
        "annotated_content": annotated,
    })
    _dump_md(
        artifacts_dir / f"compose_full_review_{task_id}_{ts}.md",
        _build_compose_report(
            state["composed_content"],
            highlights,
            annotated,
        ),
    )
    _log("artifacts 已保存")


@pytest.fixture
def cached_transcription_and_composed():
    """从 artifacts 加载已缓存的 transcription + composed_content"""
    artifacts_dir = Path(__file__).resolve().parent / "artifacts"

    # 加载 transcription（优先真实音频数据）
    transcription = None
    initial_state_data = {}
    candidates = sorted(
        artifacts_dir.glob("workflow_machine_real_audio_*.json"), reverse=True
    )
    if not candidates:
        candidates = sorted(artifacts_dir.glob("workflow_machine_*.json"), reverse=True)
    for candidate in candidates:
        try:
            data = json.loads(candidate.read_text(encoding="utf-8"))
            for stage_result in data.get("stage_results", []):
                if stage_result.get("stage") == "transcription":
                    t = stage_result["output"].get("transcription")
                    if t and t.get("segments"):
                        transcription = t
                        initial_state_data = data.get("initial_state", {})
                        _log(f"转写缓存: {candidate.name}, "
                             f"{len(t['segments'])} 个片段")
                        break
            if transcription:
                break
        except Exception:
            continue

    if not transcription:
        pytest.skip("未找到转写缓存")

    # 加载 composed_content
    composed_content = None
    compose_candidates = sorted(
        list(artifacts_dir.glob("full_pipeline_machine_*.json"))
        + list(artifacts_dir.glob("compose_full_machine_*.json")),
        reverse=True,
    )
    for candidate in compose_candidates:
        try:
            data = json.loads(candidate.read_text(encoding="utf-8"))
            c = data.get("composed_content")
            if c and c.get("chapters"):
                composed_content = c
                _log(f"成稿缓存: {candidate.name}, "
                     f"{len(c['chapters'])} 个章节")
                break
        except Exception:
            continue

    if not composed_content:
        pytest.skip("未找到成稿缓存")

    return transcription, composed_content, initial_state_data


@pytest.mark.asyncio
async def test_extraction_only(cached_transcription_and_composed):
    """
    单独测试 extraction 节点（两阶段流水线）。

    运行：pytest tests/test_compose.py::test_extraction_only -v -s
    """
    transcription, composed_content, initial_state_data = cached_transcription_and_composed

    state = create_initial_state(
        task_id=initial_state_data.get("task_id", "extraction-test"),
        audio_path=initial_state_data.get("audio_path", ""),
        title=initial_state_data.get("title", "测试"),
        author=initial_state_data.get("author", ""),
        host_name=initial_state_data.get("host_name", ""),
        guest_names=initial_state_data.get("guest_names", []),
        company_names=initial_state_data.get("company_names", []),
        podcast_intro=initial_state_data.get("podcast_intro", ""),
        proper_nouns=initial_state_data.get("proper_nouns", []),
    )
    state["transcription"] = transcription
    state["composed_content"] = composed_content

    from core.workflow.nodes.extraction import extraction_node

    _log(f"开始 extraction 测试: "
         f"{len(transcription['segments'])} 个片段, "
         f"{len(composed_content['chapters'])} 个章节")

    t0 = time.perf_counter()
    output = await extraction_node(state)
    elapsed = time.perf_counter() - t0
    state.update(output)

    highlights = state.get("highlights", {})
    quotes = highlights.get("quotes", [])
    methods = highlights.get("methodologies", [])

    _log(f"extraction 完成: {len(quotes)} 条金句, {len(methods)} 个方法论, "
         f"耗时 {elapsed:.1f}s")

    assert highlights, "highlights 不应为空"
    assert quotes, "金句列表不应为空"

    # 打印详细结果
    print("\n" + "=" * 60)
    print("金句:")
    for i, q in enumerate(quotes, 1):
        p = q.get("placement", "?")
        ch = q.get("chapter_title", "")
        ap = q.get("after_paragraph")
        pos = f"P{ap}后" if ap is not None else ""
        print(f"  {i}. [{p}]{' ' + pos if pos else ''} {q['text']}")
        if ch:
            print(f"     └─ {ch}")

    if methods:
        print("\n方法论:")
        for i, m in enumerate(methods, 1):
            ch = m.get("chapter_title", "")
            vt = m.get("visual_type", "")
            ap = m.get("after_paragraph")
            print(f"  {i}. {m['name']} [{vt}]"
                  + (f" P{ap}后" if ap is not None else ""))
            for s in m.get("steps", []):
                print(f"     - {s}")
            if ch:
                print(f"     └─ {ch}")
    print("=" * 60)

    # 保存 artifacts
    artifacts_dir = Path(__file__).resolve().parent / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")

    _dump_json(artifacts_dir / f"extraction_machine_{ts}.json", {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "highlights": highlights,
        "elapsed_seconds": elapsed,
    })

    report_lines = ["# Extraction 测试报告", ""]
    report_lines += [f"- 耗时: {elapsed:.1f}s"]
    report_lines += [f"- 金句: {len(quotes)} 条"]
    report_lines += [f"- 方法论: {len(methods)} 个", ""]

    report_lines += ["---", "", "## 金句", ""]
    for q in quotes:
        p = q.get("placement", "")
        ch = q.get("chapter_title", "")
        ap = q.get("after_paragraph")
        label = f" `[{p}]`" if p else ""
        if p == "inline_card" and ap is not None:
            label += f" `P{ap}后`"
        source = f" — {ch}" if ch else ""
        report_lines += [f"> {q['text']}{label}{source}", ""]

    if methods:
        report_lines += ["---", "", "## 方法论", ""]
        for m in methods:
            vt = m.get("visual_type", "")
            ch = m.get("chapter_title", "")
            ap = m.get("after_paragraph")
            header = f"**{m['name']}**"
            if vt:
                header += f" `[{vt}]`"
            if ap is not None:
                header += f" `P{ap}后`"
            if ch:
                header += f" — {ch}"
            report_lines += [header]
            for s in m.get("steps", []):
                report_lines += [f"- {s}"]
            report_lines += [""]

    _dump_md(
        artifacts_dir / f"extraction_review_{ts}.md",
        "\n".join(report_lines),
    )
    _log("artifacts 已保存")
