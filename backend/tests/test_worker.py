#!/usr/bin/env python3
"""
进程隔离测试 Worker

由 test_runner.py 以独立子进程方式启动。
Python 在启动时将所有模块加载到 sys.modules，之后对 .py 源码的修改
不会影响本进程中已加载的代码，从而实现「边测试边改代码」。

用法（通常由 test_runner 自动调用）：
  cd backend
  python -m tests.test_worker <run_id>
"""
import asyncio
import json
import os
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

ALL_NODES = [
    "metadata", "transcription", "compose",
    "extraction", "annotation", "editor_preface", "illustration",
    "typeset",
]
ENRICH_NODES = {"extraction", "annotation", "editor_preface", "illustration"}

ARTIFACTS_DIR = Path(__file__).resolve().parent / "artifacts"


# ──────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────

def _log(msg: str):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[worker {ts}] {msg}", flush=True)


def _save(output_dir: Path, name: str, data):
    path = output_dir / name
    if isinstance(data, str):
        path.write_text(data, encoding="utf-8")
    else:
        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
    _log(f"  已保存: {path.name}")


def _save_state(output_dir: Path, state: dict):
    (output_dir / "state.json").write_text(
        json.dumps(state, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )


def _update_status(output_dir: Path, **kwargs):
    """原子更新 status.json（写临时文件再 rename，防止读到半写状态）"""
    status_file = output_dir / "status.json"
    status = {}
    if status_file.exists():
        try:
            status = json.loads(status_file.read_text())
        except Exception:
            pass
    status.update(kwargs)
    status["updated_at"] = datetime.now().isoformat()
    tmp = output_dir / ".status.tmp"
    tmp.write_text(json.dumps(status, ensure_ascii=False, indent=2, default=str))
    tmp.rename(status_file)


def _determine_steps(config: dict) -> list[str]:
    """根据 config 计算本次需要运行的节点列表"""
    only_nodes = config.get("only_nodes")
    if only_nodes:
        ordered = [n for n in ALL_NODES if n in only_nodes]
        return ordered

    start_from = config.get("start_from", "metadata")
    if start_from == "enrich":
        start_from = "extraction"
    if start_from not in ALL_NODES:
        _log(f"未知的 start_from: {start_from}，从 metadata 开始")
        start_from = "metadata"
    idx = ALL_NODES.index(start_from)
    return ALL_NODES[idx:]


# ──────────────────────────────────────────────────────────────
# Node runners
# ──────────────────────────────────────────────────────────────

async def _run_metadata(state, config, tracker, output_dir, timings):
    from core.services.podcast_service import PodcastService
    from core.services.llm_service import bind_task_id
    from core.workflow.state import create_initial_state

    _update_status(output_dir, current_stage="metadata")
    _log("\n" + "=" * 60)
    _log("Step: 解析播客 URL + 提取元数据")
    _log("=" * 60)

    tracker.set_stage("metadata_extract")
    svc = PodcastService()
    t0 = time.perf_counter()
    episode = await svc.extract(config["podcast_url"])
    elapsed = time.perf_counter() - t0
    timings["metadata"] = elapsed

    _log(f"解析完成 ({elapsed:.1f}s)")
    _log(f"  播客名: {episode.podcast_name}")
    _log(f"  标题:   {episode.title}")
    _log(f"  时长:   {episode.duration:.0f}s ({episode.duration/60:.1f}min)")
    _log(f"  主持人: {episode.host_name}")
    _log(f"  嘉宾:   {'、'.join(episode.guest_names)}")

    task_id = episode.episode_id
    bind_task_id(task_id)

    new_state = dict(create_initial_state(
        task_id=task_id,
        audio_path=episode.audio_url,
        title=episode.title,
        author=episode.host_name or episode.podcast_name,
        description=episode.description,
        host_name=episode.host_name,
        guest_names=episode.guest_names,
        company_names=episode.company_names,
        podcast_intro=episode.shownotes_text,
        proper_nouns=episode.proper_nouns,
        cover_url=episode.cover_url,
        podcast_name=episode.podcast_name,
        podcast_url=config["podcast_url"],
        publish_date=episode.publish_date,
    ))
    state.update(new_state)

    _save(output_dir, "00_metadata.json", {
        "podcast_name": episode.podcast_name,
        "title": episode.title,
        "duration": episode.duration,
        "host_name": episode.host_name,
        "guest_names": episode.guest_names,
        "company_names": episode.company_names,
        "proper_nouns": episode.proper_nouns,
        "cover_url": episode.cover_url,
        "audio_url": episode.audio_url[:120] + "...",
        "elapsed_seconds": elapsed,
    })
    _save_state(output_dir, state)
    return task_id


async def _run_transcription(state, tracker, output_dir, timings):
    from core.workflow.nodes.transcription import transcription_node

    _update_status(output_dir, current_stage="transcription")
    _log("\n" + "=" * 60)
    _log("Step: ASR 转写 + LLM 口语清理")
    _log("=" * 60)

    tracker.set_stage("transcription")
    t0 = time.perf_counter()
    output = await transcription_node(state)
    elapsed = time.perf_counter() - t0
    state.update(output)
    timings["transcription"] = elapsed

    transcription = state["transcription"]
    segments = transcription.get("segments", [])
    _log(f"转写完成 ({elapsed:.1f}s)")
    _log(f"  片段数: {len(segments)}, 说话人: {transcription.get('speaker_count', 0)}")

    _save(output_dir, "01_transcription.json", transcription)
    lines = []
    for seg in segments:
        lines.append(f"[{seg['start_time']:.0f}s] 【{seg['speaker']}】{seg['text']}")
    _save(output_dir, "01_transcription_readable.txt", "\n\n".join(lines))
    _save_state(output_dir, state)


async def _run_compose(state, tracker, output_dir, timings):
    from core.workflow.nodes.compose_interview import compose_interview_node

    _update_status(output_dir, current_stage="compose")
    _log("\n" + "=" * 60)
    _log("Step: 访谈体成稿")
    _log("=" * 60)

    tracker.set_stage("compose")
    t0 = time.perf_counter()
    output = await compose_interview_node(state)
    elapsed = time.perf_counter() - t0
    state.update(output)
    timings["compose"] = elapsed

    composed = state["composed_content"]
    chapters = composed.get("chapters", [])
    total_words = sum(len(ch.get("content", "")) for ch in chapters)
    _log(f"成稿完成 ({elapsed:.1f}s), 章节数: {len(chapters)}, 总字数: {total_words}")

    _save(output_dir, "02_composed.json", composed)
    md = [f"# {composed.get('title', state.get('title', ''))}\n"]
    preamble = composed.get("preamble", {})
    if preamble.get("lead_paragraph"):
        md.append(f"*{preamble['lead_paragraph']}*\n")
    for i, ch in enumerate(chapters, 1):
        md.append(f"\n## {i}. {ch.get('title', f'第{i}章')}\n")
        md.append(ch.get("content", ""))
    _save(output_dir, "02_composed_readable.md", "\n".join(md))
    _save_state(output_dir, state)


async def _run_enrich_batch(nodes_to_run: list[str], state, parent_tracker, output_dir, timings):
    """并行运行多个 enrich 子节点，每个用独立 UsageTracker 避免归属混乱"""
    from core.services.llm_service import _current_tracker, UsageTracker
    from core.workflow.nodes.extraction import extraction_node
    from core.workflow.nodes.annotation import annotation_node
    from core.workflow.nodes.editor_preface import editor_preface_node
    from core.workflow.nodes.illustration import illustration_node

    NODE_FNS = {
        "extraction": extraction_node,
        "annotation": annotation_node,
        "editor_preface": editor_preface_node,
        "illustration": illustration_node,
    }

    stage_label = ", ".join(nodes_to_run)
    _update_status(output_dir, current_stage=f"enrich({stage_label})")
    _log("\n" + "=" * 60)
    _log(f"Step: Enrich 并行 [{stage_label}]")
    _log("=" * 60)

    async def _run_one(name: str):
        branch_tracker = UsageTracker()
        branch_tracker.set_stage(name)
        _current_tracker.set(branch_tracker)
        _log(f"  → {name} 开始")
        t0 = time.perf_counter()
        result = await NODE_FNS[name](state)
        elapsed = time.perf_counter() - t0
        _log(f"  ← {name} 完成 ({elapsed:.1f}s)")
        return name, result, elapsed, branch_tracker

    results = await asyncio.gather(
        *[_run_one(n) for n in nodes_to_run],
        return_exceptions=True,
    )

    errors = []
    for item in results:
        if isinstance(item, BaseException):
            errors.append(item)
            _log(f"  ✗ enrich 子节点异常: {item}")
            continue
        name, result, elapsed, branch_tracker = item
        timings[name] = elapsed
        if isinstance(result, dict):
            state.update(result)
        for rec in branch_tracker._usage_records:
            parent_tracker._usage_records.append(rec)

    _current_tracker.set(parent_tracker)

    # 保存各子节点输出
    if "extraction" in nodes_to_run and "highlights" in state:
        highlights = state["highlights"]
        _save(output_dir, "03_extraction.json", highlights)
        quotes = highlights.get("quotes", [])
        md = ["# 金句提炼\n"]
        for i, q in enumerate(quotes, 1):
            md.append(f"{i}. **[{q.get('placement', '')}]** 「{q.get('text', '')}」")
            md.append(f"   章节: {q.get('chapter_title', '')}\n")
        _save(output_dir, "03_extraction_readable.md", "\n".join(md))

    if "annotation" in nodes_to_run and "annotated_content" in state:
        annotated = state["annotated_content"]
        _save(output_dir, "04_annotation.json", annotated)
        _log(f"  注释脚注: {annotated.get('total_footnotes', 0)}")

    if "editor_preface" in nodes_to_run and state.get("editor_preface_content"):
        preface = state["editor_preface_content"]
        _save(output_dir, "05_editor_preface.md", f"# 编者序\n\n{preface}")
        _log(f"  编者序: {len(preface)} 字")

    if "illustration" in nodes_to_run and "illustrations" in state:
        illustrations = state["illustrations"]
        _save(output_dir, "06_illustration.json", illustrations)
        images = illustrations.get("images", [])
        _log(f"  插图: 通过 {illustrations.get('total_count', 0)}, "
             f"生成 {illustrations.get('generated_count', 0)}, "
             f"拒绝 {illustrations.get('rejected_count', 0)}")
        md = ["# 插图\n"]
        for i, img in enumerate(images, 1):
            md.append(f"## {i}. {img.get('caption', '')}")
            md.append(f"- 章节: {img.get('chapter_title', '')}")
            md.append(f"- 文件: {img.get('filename', '')}")
            md.append(f"- 审核: {img.get('review_score', 0)}/25 — {img.get('review_reason', '')}\n")
        _save(output_dir, "06_illustration_readable.md", "\n".join(md))

    _save_state(output_dir, state)

    if errors:
        raise errors[0]


async def _run_typeset(state, tracker, output_dir, timings):
    from core.workflow.nodes.typeset import typeset_node

    _update_status(output_dir, current_stage="typeset")
    _log("\n" + "=" * 60)
    _log("Step: 排版 PDF")
    _log("=" * 60)

    tracker.set_stage("typeset")
    t0 = time.perf_counter()
    output = await typeset_node(state)
    elapsed = time.perf_counter() - t0
    state.update(output)
    timings["typeset"] = elapsed

    pdf_path = state.get("pdf_path", "")
    typst_source = state.get("typst_source", "")
    _log(f"排版完成 ({elapsed:.1f}s)")
    if pdf_path and Path(pdf_path).exists():
        size_kb = Path(pdf_path).stat().st_size / 1024
        _log(f"  PDF: {pdf_path} ({size_kb:.0f} KB)")
    _log(f"  Typst 源码: {len(typst_source)} 字符")

    _save(output_dir, "07_typst_source.typ", typst_source)
    _save_state(output_dir, state)


# ──────────────────────────────────────────────────────────────
# Final report
# ──────────────────────────────────────────────────────────────

def _generate_report(tracker, state, timings, pipeline_elapsed, config, output_dir):
    from core.workflow.graph import _build_usage_stats

    usage_summary = tracker.summarize()
    usage_stats = _build_usage_stats(usage_summary, state)

    stage_data = usage_stats.get("llm", {}).get("stages", {})
    totals = usage_stats.get("llm", {}).get("totals", {})
    asr = usage_stats.get("asr", {})

    _log(f"\n{'=' * 70}")
    _log("测试完成！汇总统计")
    _log(f"{'=' * 70}")

    _log(f"\n{'─' * 60}")
    _log("各节点耗时")
    _log(f"{'─' * 60}")
    for step_name, t in timings.items():
        _log(f"  {step_name:<25} {t:>8.1f}s")
    _log(f"  {'─' * 25} {'─' * 8}")
    _log(f"  {'总计':<25} {pipeline_elapsed:>8.1f}s ({pipeline_elapsed/60:.1f}min)")

    _log(f"\n{'─' * 60}")
    _log("Token 消耗")
    _log(f"{'─' * 60}")
    _log(f"{'节点':<20} {'调用':>6} {'输入Token':>12} {'输出Token':>12} {'费用(元)':>10}")
    for stage_name, data in stage_data.items():
        _log(
            f"{stage_name:<20} {data.get('calls', 0):>6} "
            f"{data.get('prompt_tokens', 0):>12,} "
            f"{data.get('completion_tokens', 0):>12,} "
            f"{data.get('cost', 0):>10.4f}"
        )
    _log(f"{'─' * 60}")
    _log(
        f"{'合计':<20} {totals.get('calls', 0):>6} "
        f"{totals.get('prompt_tokens', 0):>12,} "
        f"{totals.get('completion_tokens', 0):>12,} "
        f"{totals.get('cost', 0):>10.4f}"
    )
    _log(f"  ASR: ¥{asr.get('cost', 0):.4f}, LLM: ¥{totals.get('cost', 0):.4f}, "
         f"总计: ¥{usage_stats.get('total_cost', 0):.4f}")

    report = {
        "run_id": config["run_id"],
        "podcast_url": config.get("podcast_url"),
        "title": state.get("title"),
        "pipeline_elapsed_seconds": round(pipeline_elapsed, 1),
        "timings": timings,
        "usage_stats": usage_stats,
        "pdf_path": state.get("pdf_path"),
    }
    _save(output_dir, "99_report.json", report)

    report_md = [
        f"# 测试报告 — {config['run_id']}",
        f"",
        f"- **标题**: {state.get('title', '')}",
        f"- **链接**: {config.get('podcast_url', '(from state)')}",
        f"- **总耗时**: {pipeline_elapsed:.1f}s ({pipeline_elapsed/60:.1f}min)",
        f"- **PDF**: {state.get('pdf_path', 'N/A')}",
        f"",
        f"## 各节点耗时",
        f"| 节点 | 耗时 |",
        f"|------|------|",
    ]
    for step_name, t in timings.items():
        report_md.append(f"| {step_name} | {t:.1f}s |")
    report_md.append(f"| **总计** | **{pipeline_elapsed:.1f}s** |")
    report_md += [
        f"",
        f"## Token 消耗",
        f"| 节点 | 调用 | 输入Token | 输出Token | 费用 |",
        f"|------|------|-----------|-----------|------|",
    ]
    for stage_name, data in stage_data.items():
        report_md.append(
            f"| {stage_name} | {data.get('calls', 0)} "
            f"| {data.get('prompt_tokens', 0):,} "
            f"| {data.get('completion_tokens', 0):,} "
            f"| ¥{data.get('cost', 0):.4f} |"
        )
    report_md += [
        f"",
        f"## 费用",
        f"| 项目 | 费用 |",
        f"|------|------|",
        f"| ASR | ¥{asr.get('cost', 0):.4f} |",
        f"| LLM | ¥{totals.get('cost', 0):.4f} |",
        f"| **总计** | **¥{usage_stats.get('total_cost', 0):.4f}** |",
    ]
    _save(output_dir, "99_report.md", "\n".join(report_md))


# ──────────────────────────────────────────────────────────────
# Main pipeline
# ──────────────────────────────────────────────────────────────

async def run_pipeline(config: dict):
    from core.services.llm_service import (
        get_llm_service, _current_tracker, UsageTracker, bind_task_id,
    )

    output_dir = Path(config["output_dir"])
    from_state_path = config.get("from_state")
    steps = _determine_steps(config)

    pipeline_start = time.perf_counter()
    timings: dict[str, float] = {}

    llm = get_llm_service()
    tracker = UsageTracker()
    _current_tracker.set(tracker)

    _log("=" * 70)
    _log("EchoPress 进程隔离测试")
    _log(f"  Run ID:  {config['run_id']}")
    _log(f"  PID:     {os.getpid()}")
    _log(f"  步骤:    {' → '.join(steps)}")
    if from_state_path:
        _log(f"  恢复自:  {from_state_path}")
    if config.get("podcast_url"):
        _log(f"  播客:    {config['podcast_url']}")
    _log("=" * 70)
    _log("代码已全部加载到内存，后续源码修改不影响本次测试。")

    _update_status(output_dir, status="running", steps=steps)

    # 加载或初始化 state
    if from_state_path:
        state = json.loads(Path(from_state_path).read_text("utf-8"))
        task_id = state.get("task_id", config["run_id"])
        bind_task_id(task_id)
        _log(f"已加载状态, task_id: {task_id}")
    else:
        state = {}

    # 逐步执行，enrich 子节点自动分组并行
    i = 0
    while i < len(steps):
        step = steps[i]

        if step == "metadata":
            task_id = await _run_metadata(state, config, tracker, output_dir, timings)
            i += 1

        elif step == "transcription":
            await _run_transcription(state, tracker, output_dir, timings)
            i += 1

        elif step == "compose":
            await _run_compose(state, tracker, output_dir, timings)
            i += 1

        elif step in ENRICH_NODES:
            batch = []
            while i < len(steps) and steps[i] in ENRICH_NODES:
                batch.append(steps[i])
                i += 1
            await _run_enrich_batch(batch, state, tracker, output_dir, timings)

        elif step == "typeset":
            await _run_typeset(state, tracker, output_dir, timings)
            i += 1

        else:
            _log(f"未知步骤: {step}，跳过")
            i += 1

    # 生成报告
    pipeline_elapsed = time.perf_counter() - pipeline_start
    _generate_report(tracker, state, timings, pipeline_elapsed, config, output_dir)

    _update_status(
        output_dir,
        status="completed",
        current_stage="completed",
        completed_at=datetime.now().isoformat(),
        timings=timings,
        total_elapsed=round(pipeline_elapsed, 1),
        pdf_path=state.get("pdf_path"),
    )

    _log(f"\n{'=' * 70}")
    _log(f"全部完成！总耗时: {pipeline_elapsed:.1f}s ({pipeline_elapsed/60:.1f}min)")
    _log(f"输出目录: {output_dir}")
    _log(f"{'=' * 70}")


# ──────────────────────────────────────────────────────────────
# Entry point
# ──────────────────────────────────────────────────────────────

def main():
    if len(sys.argv) < 2:
        print("Usage: python -m tests.test_worker <run_id>", flush=True)
        sys.exit(1)

    run_id = sys.argv[1]
    run_dir = ARTIFACTS_DIR / f"run_{run_id}"
    config_file = run_dir / "config.json"

    if not config_file.exists():
        print(f"Config not found: {config_file}", flush=True)
        sys.exit(1)

    config = json.loads(config_file.read_text())

    try:
        asyncio.run(run_pipeline(config))
    except KeyboardInterrupt:
        _log("收到中断信号，退出")
        _update_status(run_dir, status="killed", completed_at=datetime.now().isoformat())
        sys.exit(130)
    except Exception as e:
        traceback.print_exc()
        _update_status(
            run_dir,
            status="failed",
            error=str(e)[:2000],
            completed_at=datetime.now().isoformat(),
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
