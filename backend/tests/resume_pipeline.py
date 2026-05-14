"""
从中断处续跑全流程：加载已有 state，从 Step 6（illustration）继续。
"""
import asyncio
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

PODCAST_URL = "https://www.xiaoyuzhoufm.com/episode/69855b8fc78b823892e62849"
OUTPUT_DIR = Path(__file__).resolve().parent / "artifacts" / "full_pipeline_20260312_101024"
STATE_FILE = OUTPUT_DIR / "state.json"

NODE_TIMINGS: dict[str, float] = {
    "0_metadata": 5.7,
    "1_transcription": 804.8,
    "2_compose": 336.1,
    "3_extraction": 99.0,
    "4_annotation": 660.8,
    "5_editor_preface": 105.0,
}
PIPELINE_ELAPSED_BEFORE = sum(NODE_TIMINGS.values())


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


async def resume():
    from core.services.llm_service import (
        get_llm_service, _current_tracker, UsageTracker, bind_task_id,
    )
    from core.workflow.nodes.illustration import illustration_node
    from core.workflow.nodes.typeset import typeset_node

    state = json.loads(STATE_FILE.read_text("utf-8"))
    task_id = state["task_id"]
    bind_task_id(task_id)

    llm = get_llm_service()
    tracker = UsageTracker()
    _current_tracker.set(tracker)

    resume_start = time.perf_counter()
    _log("=" * 70)
    _log("续跑: 从 Step 6（插图）继续")
    _log(f"task_id: {task_id}")
    _log("=" * 70)

    # ============================================================
    # Step 6: 正文插图
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
    # Step 7: 排版 PDF
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
    # 汇总统计
    # ============================================================
    resume_elapsed = time.perf_counter() - resume_start
    total_pipeline = PIPELINE_ELAPSED_BEFORE + resume_elapsed

    _log("\n" + "=" * 70)
    _log("全流程完成！汇总统计")
    _log("=" * 70)

    from core.config import settings
    from core.workflow.graph import _build_usage_stats

    usage_summary = tracker.summarize()
    usage_stats = _build_usage_stats(usage_summary, state)

    # 按节点统计 (这里只有续跑部分的 token)
    _log(f"\n{'─' * 60}")
    _log("按节点 Token 消耗统计（续跑部分: illustration + typeset）")
    _log(f"{'─' * 60}")
    _log(f"{'节点':<20} {'调用次数':>8} {'输入Token':>12} {'输出Token':>12} {'总Token':>12}")
    _log(f"{'─' * 20} {'─' * 8} {'─' * 12} {'─' * 12} {'─' * 12}")

    stage_data = usage_stats.get("llm", {}).get("stages", {})
    for stage_name, data in stage_data.items():
        _log(
            f"{stage_name:<20} {data.get('calls', 0):>8} "
            f"{data.get('prompt_tokens', 0):>12,} "
            f"{data.get('completion_tokens', 0):>12,} "
            f"{data.get('total_tokens', 0):>12,}"
        )

    totals = usage_stats.get("llm", {}).get("totals", {})
    _log(f"{'─' * 20} {'─' * 8} {'─' * 12} {'─' * 12} {'─' * 12}")
    _log(
        f"{'合计':<20} {totals.get('calls', 0):>8} "
        f"{totals.get('prompt_tokens', 0):>12,} "
        f"{totals.get('completion_tokens', 0):>12,} "
        f"{totals.get('total_tokens', 0):>12,}"
    )

    # 按模型
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
    _log("按模型 Token 消耗统计（续跑部分）")
    _log(f"{'─' * 60}")
    for model_name, data in model_stats.items():
        _log(
            f"  {model_name}: {data['calls']} 调用, "
            f"prompt={data['prompt_tokens']:,}, completion={data['completion_tokens']:,}, "
            f"total={data['total_tokens']:,}"
        )
        _log(f"    阶段: {', '.join(sorted(data['stages']))}")

    # 耗时
    _log(f"\n{'─' * 60}")
    _log("各节点耗时")
    _log(f"{'─' * 60}")
    for step_name, t in NODE_TIMINGS.items():
        _log(f"  {step_name:<25} {t:>8.1f}s")
    _log(f"  {'─' * 25} {'─' * 8}")
    _log(f"  {'总计':<25} {total_pipeline:>8.1f}s ({total_pipeline/60:.1f}min)")

    # 保存
    model_stats_s = {k: {**v, "stages": sorted(v["stages"])} for k, v in model_stats.items()}
    _save("99_resume_report.json", {
        "node_timings": NODE_TIMINGS,
        "resume_usage_stats": usage_stats,
        "resume_model_stats": model_stats_s,
        "pdf_path": pdf_path,
        "total_pipeline_seconds": total_pipeline,
    })

    report_md = [
        f"# 续跑报告（Step 6-7）",
        f"",
        f"## 各节点耗时",
        f"",
        f"| 节点 | 耗时 |",
        f"|------|------|",
    ]
    for step_name, t in NODE_TIMINGS.items():
        report_md.append(f"| {step_name} | {t:.1f}s |")
    report_md.append(f"| **总计** | **{total_pipeline:.1f}s ({total_pipeline/60:.1f}min)** |")
    report_md += [
        f"",
        f"## Token 消耗（续跑部分: illustration + typeset）",
        f"",
        f"| 节点 | 调用 | 输入Token | 输出Token | 总Token |",
        f"|------|------|-----------|-----------|---------|",
    ]
    for stage_name, data in stage_data.items():
        report_md.append(
            f"| {stage_name} | {data.get('calls', 0)} "
            f"| {data.get('prompt_tokens', 0):,} "
            f"| {data.get('completion_tokens', 0):,} "
            f"| {data.get('total_tokens', 0):,} |"
        )
    report_md += [
        f"",
        f"## 按模型统计（续跑部分）",
        f"",
    ]
    for model_name, data in model_stats.items():
        report_md.append(f"- **{model_name}**: {data['calls']} 调用, {data['total_tokens']:,} token")
    report_md += [
        f"",
        f"## PDF",
        f"",
        f"路径: `{pdf_path}`",
    ]
    _save("99_resume_report.md", "\n".join(report_md))

    _log(f"\n全流程完成！PDF: {pdf_path}")
    _log(f"输出目录: {OUTPUT_DIR}")


if __name__ == "__main__":
    asyncio.run(resume())
