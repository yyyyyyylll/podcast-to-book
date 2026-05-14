"""
端到端全流程测试：从播客链接到 PDF，一次性跑完所有节点。

每个节点的输出保存为可读文件，并统计每个节点、每个模型的 token 消耗。

用法：
  cd backend
  python -m tests.run_full_pipeline [播客URL]

  不传 URL 时使用默认链接。
"""
import asyncio
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

DEFAULT_URL = "https://www.xiaoyuzhoufm.com/episode/69b121eb9b893f69c72bffcb"
PODCAST_URL = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_URL
_episode_hash = PODCAST_URL.rstrip("/").rsplit("/", 1)[-1][:8]
TS = datetime.now().strftime("%Y%m%d_%H%M%S") + f"_{_episode_hash}"
OUTPUT_DIR = Path(__file__).resolve().parent / "artifacts" / f"full_pipeline_{TS}"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

STATE_FILE = OUTPUT_DIR / "state.json"

NODE_TIMINGS: dict[str, float] = {}


def _log(msg: str):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[pipeline {ts}] {msg}", flush=True)


def _save(name: str, data):
    path = OUTPUT_DIR / name
    if isinstance(data, str):
        path.write_text(data, encoding="utf-8")
    else:
        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
    _log(f"  已保存: {path.name}")


def _save_state(state: dict):
    STATE_FILE.write_text(
        json.dumps(state, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )


def _tracker_snapshot(tracker) -> dict:
    """拍摄当前 tracker 快照（不清空）"""
    summary = tracker.summarize()
    return {
        "stages": summary["stages"],
        "totals": summary["totals"],
        "record_count": len(summary["records"]),
    }


async def run_pipeline():
    from core.services.podcast_service import PodcastService
    from core.services.llm_service import (
        get_llm_service, _current_tracker, UsageTracker, bind_task_id,
    )
    from core.workflow.state import create_initial_state
    from core.workflow.nodes.transcription import transcription_node
    from core.workflow.nodes.compose import compose_node
    from core.workflow.nodes.extraction import extraction_node
    from core.workflow.nodes.annotation import annotation_node
    from core.workflow.nodes.editor_preface import editor_preface_node
    from core.workflow.nodes.illustration import illustration_node
    from core.workflow.nodes.typeset import typeset_node

    pipeline_start = time.perf_counter()
    _log("=" * 70)
    _log("EchoPress 全流程端到端测试")
    _log(f"播客链接: {PODCAST_URL}")
    _log(f"输出目录: {OUTPUT_DIR}")
    _log("=" * 70)

    # ── 初始化 token 追踪 ──
    llm = get_llm_service()
    tracker = UsageTracker()
    _current_tracker.set(tracker)

    # ============================================================
    # Step 0: 解析播客元数据
    # ============================================================
    _log("\n" + "=" * 60)
    _log("Step 0/7: 解析播客 URL + 提取元数据")
    _log("=" * 60)

    tracker.set_stage("metadata_extract")
    svc = PodcastService()
    t0 = time.perf_counter()
    episode = await svc.extract(PODCAST_URL)
    elapsed = time.perf_counter() - t0
    NODE_TIMINGS["0_metadata"] = elapsed

    _log(f"解析完成 ({elapsed:.1f}s)")
    _log(f"  播客名: {episode.podcast_name}")
    _log(f"  标题:   {episode.title}")
    _log(f"  时长:   {episode.duration:.0f}s ({episode.duration/60:.1f}min)")
    _log(f"  主持人: {episode.host_name}")
    _log(f"  嘉宾:   {'、'.join(episode.guest_names)}")
    _log(f"  公司:   {'、'.join(episode.company_names)}")
    _log(f"  专有名词: {'、'.join(episode.proper_nouns)}")

    task_id = episode.episode_id
    bind_task_id(task_id)

    state = dict(create_initial_state(
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
        cover_style="classic",
        podcast_name=episode.podcast_name,
        podcast_url=PODCAST_URL,
        publish_date=episode.publish_date,
    ))

    _save("00_metadata.json", {
        "podcast_name": episode.podcast_name,
        "title": episode.title,
        "duration": episode.duration,
        "host_name": episode.host_name,
        "guest_names": episode.guest_names,
        "company_names": episode.company_names,
        "proper_nouns": episode.proper_nouns,
        "cover_url": episode.cover_url,
        "publish_date": episode.publish_date,
        "audio_url": episode.audio_url[:120] + "...",
        "elapsed_seconds": elapsed,
    })

    # ============================================================
    # Step 1: ASR 转写
    # ============================================================
    _log("\n" + "=" * 60)
    _log("Step 1/7: ASR 转写 + LLM 口语清理")
    _log("=" * 60)

    tracker.set_stage("transcription")
    t0 = time.perf_counter()
    output = await transcription_node(state)
    elapsed = time.perf_counter() - t0
    state.update(output)
    NODE_TIMINGS["1_transcription"] = elapsed

    transcription = state["transcription"]
    segments = transcription.get("segments", [])
    _log(f"转写完成 ({elapsed:.1f}s)")
    _log(f"  片段数: {len(segments)}")
    _log(f"  说话人: {transcription.get('speaker_count', 0)} 人")
    _log(f"  音频时长: {transcription.get('duration', 0):.0f}s")

    _save("01_transcription.json", transcription)
    # 可读文本版
    lines = []
    for seg in segments:
        lines.append(f"[{seg['start_time']:.0f}s] 【{seg['speaker']}】{seg['text']}")
    _save("01_transcription_readable.txt", "\n\n".join(lines))
    _save_state(state)

    # ============================================================
    # Step 2: 访谈体成稿
    # ============================================================
    _log("\n" + "=" * 60)
    _log("Step 2/7: 通用成稿引擎（compose）")
    _log("=" * 60)

    tracker.set_stage("compose")
    t0 = time.perf_counter()
    output = await compose_node(state)
    elapsed = time.perf_counter() - t0
    state.update(output)
    NODE_TIMINGS["2_compose"] = elapsed

    composed = state["composed_content"]
    chapters = composed.get("chapters", [])
    total_words = sum(len(ch.get("content", "")) for ch in chapters)
    _log(f"成稿完成 ({elapsed:.1f}s)")
    _log(f"  章节数: {len(chapters)}, 总字数: {total_words}")

    _save("02_composed.json", composed)
    md = [f"# {composed.get('title', episode.title)}\n"]
    preamble = composed.get("preamble", {})
    if preamble.get("lead_paragraph"):
        md.append(f"*{preamble['lead_paragraph']}*\n")
    for i, ch in enumerate(chapters, 1):
        md.append(f"\n## {i}. {ch.get('title', f'第{i}章')}\n")
        md.append(ch.get("content", ""))
    _save("02_composed_readable.md", "\n".join(md))
    _save_state(state)

    # ============================================================
    # Step 3: 精华提炼（extraction）
    # ============================================================
    _log("\n" + "=" * 60)
    _log("Step 3/7: 精华提炼（extraction）")
    _log("=" * 60)

    tracker.set_stage("extraction")
    t0 = time.perf_counter()
    output = await extraction_node(state)
    elapsed = time.perf_counter() - t0
    state.update(output)
    NODE_TIMINGS["3_extraction"] = elapsed

    highlights = state["highlights"]
    quotes = highlights.get("quotes", [])
    _log(f"提炼完成 ({elapsed:.1f}s), 金句: {len(quotes)} 条")

    _save("03_extraction.json", highlights)
    md = ["# 金句提炼\n"]
    for i, q in enumerate(quotes, 1):
        md.append(f"{i}. **[{q.get('placement','')}]** 「{q.get('text','')}」")
        md.append(f"   章节: {q.get('chapter_title','')}\n")
    _save("03_extraction_readable.md", "\n".join(md))
    _save_state(state)

    # ============================================================
    # Step 4: 内容注释（annotation）
    # ============================================================
    _log("\n" + "=" * 60)
    _log("Step 4/7: 内容注释（annotation）")
    _log("=" * 60)

    tracker.set_stage("annotation")
    t0 = time.perf_counter()
    output = await annotation_node(state)
    elapsed = time.perf_counter() - t0
    state.update(output)
    NODE_TIMINGS["4_annotation"] = elapsed

    annotated = state["annotated_content"]
    total_fn = annotated.get("total_footnotes", 0)
    _log(f"注释完成 ({elapsed:.1f}s), 脚注: {total_fn}")

    _save("04_annotation.json", annotated)
    md = ["# 带注释正文\n"]
    for i, ch in enumerate(annotated.get("chapters", []), 1):
        md.append(f"\n## {i}. {ch.get('title', '')}\n")
        md.append(ch.get("content", ""))
    _save("04_annotation_readable.md", "\n".join(md))
    _save_state(state)

    # ============================================================
    # Step 5: 编者序（editor_preface，联网搜索）
    # ============================================================
    _log("\n" + "=" * 60)
    _log("Step 5/7: 编者序（editor_preface，联网搜索增强）")
    _log("=" * 60)

    tracker.set_stage("editor_preface")
    t0 = time.perf_counter()
    output = await editor_preface_node(state)
    elapsed = time.perf_counter() - t0
    state.update(output)
    NODE_TIMINGS["5_editor_preface"] = elapsed

    preface = state.get("editor_preface_content", "")
    _log(f"编者序完成 ({elapsed:.1f}s), {len(preface)} 字")

    _save("05_editor_preface.md", f"# 编者序\n\n{preface}")
    _save_state(state)

    # ============================================================
    # Step 6: 正文插图（illustration）
    # ============================================================
    _log("\n" + "=" * 60)
    _log("Step 6/7: 正文插图（illustration）")
    _log("=" * 60)

    tracker.set_stage("illustration")
    t0 = time.perf_counter()
    output = await illustration_node(state)
    elapsed = time.perf_counter() - t0
    state.update(output)
    NODE_TIMINGS["6_illustration"] = elapsed

    illustrations = state.get("illustrations", {})
    images = illustrations.get("images", [])
    _log(f"插图完成 ({elapsed:.1f}s)")
    _log(f"  通过: {illustrations.get('total_count', 0)}, "
         f"生成: {illustrations.get('generated_count', 0)}, "
         f"拒绝: {illustrations.get('rejected_count', 0)}")

    _save("06_illustration.json", illustrations)
    md = ["# 插图审查\n"]
    for i, img in enumerate(images, 1):
        md.append(f"## {i}. {img.get('caption', '')}")
        md.append(f"- 章节: {img.get('chapter_title', '')}")
        md.append(f"- 文件: {img.get('filename', '')}")
        md.append(f"- 审核: {img.get('review_score', 0)}/25 — {img.get('review_reason', '')}\n")
    _save("06_illustration_readable.md", "\n".join(md))
    _save_state(state)

    # ============================================================
    # Step 7: 排版 PDF（typeset）
    # ============================================================
    _log("\n" + "=" * 60)
    _log("Step 7/7: 排版 PDF（typeset）")
    _log("=" * 60)

    tracker.set_stage("typeset")
    t0 = time.perf_counter()
    output = await typeset_node(state)
    elapsed = time.perf_counter() - t0
    state.update(output)
    NODE_TIMINGS["7_typeset"] = elapsed

    pdf_path = state.get("pdf_path", "")
    typst_source = state.get("typst_source", "")
    _log(f"排版完成 ({elapsed:.1f}s)")
    if pdf_path and Path(pdf_path).exists():
        size_kb = Path(pdf_path).stat().st_size / 1024
        _log(f"  PDF: {pdf_path} ({size_kb:.0f} KB)")
    _log(f"  Typst 源码: {len(typst_source)} 字符")

    _save("07_typst_source.typ", typst_source)
    _save_state(state)

    # ============================================================
    # 汇总：Token 消耗统计
    # ============================================================
    pipeline_elapsed = time.perf_counter() - pipeline_start

    _log("\n" + "=" * 70)
    _log("全流程完成！汇总统计")
    _log("=" * 70)

    from core.config import settings
    from core.workflow.graph import _build_usage_stats

    usage_summary = tracker.summarize()
    usage_stats = _build_usage_stats(usage_summary, state)

    # ── 按节点统计 ──
    _log(f"\n{'─' * 60}")
    _log("📊 按节点 Token 消耗统计")
    _log(f"{'─' * 60}")
    _log(f"{'节点':<20} {'调用次数':>8} {'输入Token':>12} {'输出Token':>12} {'总Token':>12} {'费用(元)':>10}")
    _log(f"{'─' * 20} {'─' * 8} {'─' * 12} {'─' * 12} {'─' * 12} {'─' * 10}")

    stage_data = usage_stats.get("llm", {}).get("stages", {})
    for stage_name, data in stage_data.items():
        _log(
            f"{stage_name:<20} {data.get('calls', 0):>8} "
            f"{data.get('prompt_tokens', 0):>12,} "
            f"{data.get('completion_tokens', 0):>12,} "
            f"{data.get('total_tokens', 0):>12,} "
            f"{data.get('cost', 0):>10.4f}"
        )

    totals = usage_stats.get("llm", {}).get("totals", {})
    _log(f"{'─' * 20} {'─' * 8} {'─' * 12} {'─' * 12} {'─' * 12} {'─' * 10}")
    _log(
        f"{'合计':<20} {totals.get('calls', 0):>8} "
        f"{totals.get('prompt_tokens', 0):>12,} "
        f"{totals.get('completion_tokens', 0):>12,} "
        f"{totals.get('total_tokens', 0):>12,} "
        f"{totals.get('cost', 0):>10.4f}"
    )

    # ── 按模型统计 ──
    model_stats: dict[str, dict] = {}
    for rec in usage_summary.get("records", []):
        model = rec.get("model", "unknown")
        if model not in model_stats:
            model_stats[model] = {
                "calls": 0, "prompt_tokens": 0,
                "completion_tokens": 0, "total_tokens": 0,
                "stages": set(),
            }
        m = model_stats[model]
        m["calls"] += 1
        m["prompt_tokens"] += rec.get("prompt_tokens", 0)
        m["completion_tokens"] += rec.get("completion_tokens", 0)
        m["total_tokens"] += rec.get("prompt_tokens", 0) + rec.get("completion_tokens", 0)
        m["stages"].add(rec.get("stage", ""))

    _log(f"\n{'─' * 60}")
    _log("📊 按模型 Token 消耗统计")
    _log(f"{'─' * 60}")
    _log(f"{'模型':<25} {'调用次数':>8} {'输入Token':>12} {'输出Token':>12} {'总Token':>12}")
    _log(f"{'─' * 25} {'─' * 8} {'─' * 12} {'─' * 12} {'─' * 12}")

    for model_name, data in model_stats.items():
        _log(
            f"{model_name:<25} {data['calls']:>8} "
            f"{data['prompt_tokens']:>12,} "
            f"{data['completion_tokens']:>12,} "
            f"{data['total_tokens']:>12,}"
        )
        _log(f"  使用阶段: {', '.join(sorted(data['stages']))}")

    # ── 耗时统计 ──
    _log(f"\n{'─' * 60}")
    _log("⏱  各节点耗时")
    _log(f"{'─' * 60}")
    for step_name, t in NODE_TIMINGS.items():
        _log(f"  {step_name:<25} {t:>8.1f}s")
    _log(f"  {'─' * 25} {'─' * 8}")
    _log(f"  {'总计':<25} {pipeline_elapsed:>8.1f}s ({pipeline_elapsed/60:.1f}min)")

    # ── ASR 费用 ──
    asr = usage_stats.get("asr", {})
    _log(f"\n{'─' * 60}")
    _log("💰 费用汇总")
    _log(f"{'─' * 60}")
    _log(f"  ASR 时长: {asr.get('duration_seconds', 0):.0f}s, 费用: ¥{asr.get('cost', 0):.4f}")
    _log(f"  LLM 费用: ¥{totals.get('cost', 0):.4f}")
    _log(f"  总费用: ¥{usage_stats.get('total_cost', 0):.4f}")

    # ── 保存完整报告 ──
    # 将 model_stats 中的 set 转为 list 用于 JSON 序列化
    model_stats_serializable = {}
    for k, v in model_stats.items():
        model_stats_serializable[k] = {**v, "stages": sorted(v["stages"])}

    report = {
        "podcast_url": PODCAST_URL,
        "task_id": task_id,
        "title": episode.title,
        "pipeline_elapsed_seconds": round(pipeline_elapsed, 1),
        "node_timings": NODE_TIMINGS,
        "usage_stats": usage_stats,
        "model_stats": model_stats_serializable,
        "pdf_path": pdf_path,
        "output_dir": str(OUTPUT_DIR),
    }
    _save("99_pipeline_report.json", report)

    # Markdown 汇总报告
    report_md = [
        f"# 全流程运行报告",
        f"",
        f"- **播客**: {episode.podcast_name} — {episode.title}",
        f"- **链接**: {PODCAST_URL}",
        f"- **总耗时**: {pipeline_elapsed:.1f}s ({pipeline_elapsed/60:.1f}min)",
        f"- **PDF**: {pdf_path}",
        f"",
        f"## 各节点耗时",
        f"",
        f"| 节点 | 耗时 |",
        f"|------|------|",
    ]
    for step_name, t in NODE_TIMINGS.items():
        report_md.append(f"| {step_name} | {t:.1f}s |")
    report_md.append(f"| **总计** | **{pipeline_elapsed:.1f}s** |")

    report_md += [
        f"",
        f"## Token 消耗（按节点）",
        f"",
        f"| 节点 | 调用 | 输入Token | 输出Token | 总Token | 费用 |",
        f"|------|------|-----------|-----------|---------|------|",
    ]
    for stage_name, data in stage_data.items():
        report_md.append(
            f"| {stage_name} | {data.get('calls', 0)} "
            f"| {data.get('prompt_tokens', 0):,} "
            f"| {data.get('completion_tokens', 0):,} "
            f"| {data.get('total_tokens', 0):,} "
            f"| ¥{data.get('cost', 0):.4f} |"
        )
    report_md.append(
        f"| **合计** | **{totals.get('calls', 0)}** "
        f"| **{totals.get('prompt_tokens', 0):,}** "
        f"| **{totals.get('completion_tokens', 0):,}** "
        f"| **{totals.get('total_tokens', 0):,}** "
        f"| **¥{totals.get('cost', 0):.4f}** |"
    )

    report_md += [
        f"",
        f"## Token 消耗（按模型）",
        f"",
        f"| 模型 | 调用 | 输入Token | 输出Token | 总Token | 使用阶段 |",
        f"|------|------|-----------|-----------|---------|----------|",
    ]
    for model_name, data in model_stats.items():
        report_md.append(
            f"| {model_name} | {data['calls']} "
            f"| {data['prompt_tokens']:,} "
            f"| {data['completion_tokens']:,} "
            f"| {data['total_tokens']:,} "
            f"| {', '.join(sorted(data['stages']))} |"
        )

    report_md += [
        f"",
        f"## 费用",
        f"",
        f"| 项目 | 费用 |",
        f"|------|------|",
        f"| ASR ({asr.get('duration_seconds', 0):.0f}s) | ¥{asr.get('cost', 0):.4f} |",
        f"| LLM | ¥{totals.get('cost', 0):.4f} |",
        f"| **总计** | **¥{usage_stats.get('total_cost', 0):.4f}** |",
    ]
    _save("99_pipeline_report.md", "\n".join(report_md))

    _log(f"\n{'=' * 70}")
    _log(f"全流程完成！所有输出文件在: {OUTPUT_DIR}")
    _log(f"{'=' * 70}")


if __name__ == "__main__":
    asyncio.run(run_pipeline())
