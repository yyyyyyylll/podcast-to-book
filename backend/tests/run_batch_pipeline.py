"""
批量端到端全流程测试
====================

并行运行 14 个播客的完整流水线，每个播客使用所有封面模板排版。

- 默认封面模板 (classic) 用于正式 PDF
- 所有 9 个封面模板都渲染为 PNG 图片保存
- 每个节点的中间产物和最终结果均保存到 artifacts 目录

用法：
  cd backend
  python -m tests.run_batch_pipeline

  # 可选：指定并发数（默认 3）
  python -m tests.run_batch_pipeline --concurrency 4
"""

import asyncio
import json
import os
import re
import sys
import time
import shutil
import traceback as tb_mod
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List, Optional

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# ================================================================
# 14 个测试播客 URL
# ================================================================

PODCAST_URLS: List[tuple] = [
    # ── 商业科技 ──
    ("商业-事件-圆桌",   "https://www.xiaoyuzhoufm.com/episode/69adf2bdc8cdeb38c28a1a81"),
    ("商业-事件-访谈",   "https://www.xiaoyuzhoufm.com/episode/698563a188663289fe80769a"),
    ("商业-嘉宾-访谈",   "https://www.xiaoyuzhoufm.com/episode/695331cb2db086f897b50ea9"),
    ("商业-嘉宾-圆桌",   "https://www.xiaoyuzhoufm.com/episode/685f6dc8bef90978ec4c1e60"),
    # ── 自我成长 ──
    ("成长-嘉宾-访谈",   "https://www.xiaoyuzhoufm.com/episode/663ac13e13426298925c9853"),
    ("成长-嘉宾-圆桌A",  "https://www.xiaoyuzhoufm.com/episode/65e75676d15a20dbcab730ca"),
    ("成长-嘉宾-圆桌B",  "https://www.xiaoyuzhoufm.com/episode/699c3925de29766da93f2f74"),
    # ── 社会人文 ──
    ("人文-事件-圆桌A",  "https://www.xiaoyuzhoufm.com/episode/689dbb17759c1ff652eb6795"),
    ("人文-事件-圆桌B",  "https://www.xiaoyuzhoufm.com/episode/6948c33c262481ac732bbd0b"),
    ("人文-事件-圆桌C",  "https://www.xiaoyuzhoufm.com/episode/68d101ed2c82c9dcca9c4bbc"),
    ("人文-事件-圆桌D",  "https://www.xiaoyuzhoufm.com/episode/6963c010e235ea65bc5023a0"),
    ("人文-事件-访谈",   "https://www.xiaoyuzhoufm.com/episode/696cd009109824f9e125ad1c"),
    ("人文-嘉宾-访谈",   "https://www.xiaoyuzhoufm.com/episode/6826c6a55ccf03732b24d5a3"),
    ("人文-嘉宾-圆桌",   "https://www.xiaoyuzhoufm.com/episode/67c66c0bb0167b8db954b848"),
]

DEFAULT_COVER_STYLE = "classic"

# 解析 --concurrency N
_conc_idx = sys.argv.index("--concurrency") if "--concurrency" in sys.argv else -1
MAX_CONCURRENT = int(sys.argv[_conc_idx + 1]) if _conc_idx >= 0 else 3

TS = datetime.now().strftime("%Y%m%d_%H%M%S")
BATCH_DIR = Path(__file__).resolve().parent / "artifacts" / f"batch_{TS}"
BATCH_DIR.mkdir(parents=True, exist_ok=True)


# ================================================================
# 工具函数
# ================================================================

def _log(idx: int, msg: str):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[#{idx:02d} {ts}] {msg}", flush=True)


def _save(path: Path, data):
    if isinstance(data, str):
        path.write_text(data, encoding="utf-8")
    else:
        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )


def _compute_cover_variables(state: Dict, annotated: Dict) -> Dict[str, Any]:
    """从 state 和 annotated_content 提取封面渲染变量（与 typeset_node 逻辑一致）。"""
    raw_title = annotated.get("title", "")
    if not raw_title:
        raw_title = annotated.get("core_theme", "播客书稿")

    split_match = re.split(r'[：:——––—\-]{1,2}', raw_title, maxsplit=1)
    if len(split_match) > 1 and split_match[0].strip() and split_match[1].strip():
        cover_title = split_match[0].strip()
        cover_subtitle = split_match[1].strip()
    else:
        cover_title = raw_title.strip() or "播客书稿"
        cover_subtitle = ""

    from core.workflow.nodes.typeset import _clean_speakers

    speakers_raw = list(annotated.get("speakers") or [])
    host = state.get("host_name", "")
    state_guests = state.get("guest_names") or []

    if not speakers_raw:
        speakers_raw = []
        if host:
            speakers_raw.append({"name": host, "role": "主持人"})
        for g in state_guests:
            if g:
                speakers_raw.append({"name": g, "role": "嘉宾"})

    transcription_segs = (state.get("transcription") or {}).get("segments", [])
    speakers_list = _clean_speakers(speakers_raw, transcription_segs)

    hosts, guests, others = [], [], []
    for s in speakers_list:
        name = s.get("name")
        if not name or re.match(r'^说话人\d*$', name):
            continue
        role = s.get("role", "")
        if "主持" in role:
            hosts.append(name)
        elif "嘉宾" in role:
            guests.append(name)
        else:
            others.append(name)

    author_line_1, author_line_2 = "", ""
    if guests:
        author_line_1 = f'嘉宾 / {"、".join(guests)}'
    if hosts:
        host_text = f'主持 / {"、".join(hosts)}'
        if author_line_1:
            author_line_2 = host_text
        else:
            author_line_1 = host_text
    if others:
        other_text = "、".join(others)
        if not author_line_1:
            author_line_1 = other_text
        elif not author_line_2:
            author_line_2 = other_text

    podcast_name_disp = state.get("podcast_name", "")
    series_label = (
        f"{podcast_name_disp} · 典藏系列" if podcast_name_disp else "播客书稿 · 典藏系列"
    )

    from core.config import settings
    task_dir = Path(settings.STORAGE_DIR) / state.get("task_id", "")
    cover_image_path = ""
    for ext in (".jpg", ".png", ".webp"):
        candidate = task_dir / f"cover{ext}"
        if candidate.exists():
            cover_image_path = str(candidate)
            break

    return {
        "title": cover_title,
        "subtitle": cover_subtitle,
        "author_line_1": author_line_1,
        "author_line_2": author_line_2,
        "series_label": series_label,
        "cover_image": cover_image_path,
    }


# ================================================================
# 单个播客流水线
# ================================================================

async def run_single_podcast(
    idx: int,
    label: str,
    url: str,
    semaphore: asyncio.Semaphore,
) -> Dict[str, Any]:
    """在信号量控制下运行单个播客的完整流水线。"""
    async with semaphore:
        return await _pipeline_impl(idx, label, url)


async def _pipeline_impl(idx: int, label: str, url: str) -> Dict[str, Any]:
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
    from core.services.cover_service import render_cover, get_available_styles
    from core.workflow.graph import _build_usage_stats

    episode_hash = url.rstrip("/").rsplit("/", 1)[-1][:8]
    out = BATCH_DIR / f"{idx:02d}_{label}_{episode_hash}"
    out.mkdir(parents=True, exist_ok=True)

    result: Dict[str, Any] = {
        "idx": idx,
        "label": label,
        "url": url,
        "output_dir": str(out),
        "status": "running",
        "error": None,
        "timings": {},
        "title": "",
    }

    pipeline_start = time.perf_counter()
    _log(idx, f"开始 [{label}]")

    try:
        # ── Token tracker（ContextVar，asyncio Task 间隔离） ──
        get_llm_service()
        tracker = UsageTracker()
        _current_tracker.set(tracker)

        # ============================
        # Step 0: 元数据
        # ============================
        _log(idx, "Step 0/7 · 元数据")
        tracker.set_stage("metadata_extract")
        t0 = time.perf_counter()
        svc = PodcastService()
        episode = await svc.extract(url)
        elapsed = time.perf_counter() - t0
        result["timings"]["0_metadata"] = round(elapsed, 1)
        result["title"] = episode.title
        _log(idx, f"  → {episode.title} ({elapsed:.0f}s)")

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
            cover_style=DEFAULT_COVER_STYLE,
            podcast_name=episode.podcast_name,
            podcast_url=url,
            publish_date=episode.publish_date,
        ))

        _save(out / "00_metadata.json", {
            "podcast_name": episode.podcast_name,
            "title": episode.title,
            "duration": episode.duration,
            "host_name": episode.host_name,
            "guest_names": episode.guest_names,
            "company_names": episode.company_names,
            "proper_nouns": episode.proper_nouns,
            "cover_url": episode.cover_url,
            "publish_date": episode.publish_date,
            "elapsed": elapsed,
        })

        # ============================
        # Step 1: ASR 转写
        # ============================
        _log(idx, "Step 1/7 · ASR 转写")
        tracker.set_stage("transcription")
        t0 = time.perf_counter()
        output = await transcription_node(state)
        elapsed = time.perf_counter() - t0
        state.update(output)
        result["timings"]["1_transcription"] = round(elapsed, 1)
        segs = state["transcription"].get("segments", [])
        _log(idx, f"  → {len(segs)} 段 ({elapsed:.0f}s)")

        _save(out / "01_transcription.json", state["transcription"])
        lines = [f"[{s['start_time']:.0f}s] 【{s['speaker']}】{s['text']}" for s in segs]
        _save(out / "01_transcription_readable.txt", "\n\n".join(lines))

        # ============================
        # Step 2: 成稿
        # ============================
        _log(idx, "Step 2/7 · 成稿")
        tracker.set_stage("compose")
        t0 = time.perf_counter()
        output = await compose_node(state)
        elapsed = time.perf_counter() - t0
        state.update(output)
        result["timings"]["2_compose"] = round(elapsed, 1)

        composed = state["composed_content"]
        chapters = composed.get("chapters", [])
        total_words = sum(len(ch.get("content", "")) for ch in chapters)
        _log(idx, f"  → {len(chapters)} 章, {total_words} 字 ({elapsed:.0f}s)")

        _save(out / "02_composed.json", composed)
        md = [f"# {composed.get('title', episode.title)}\n"]
        preamble = composed.get("preamble", {})
        if preamble.get("lead_paragraph"):
            md.append(f"*{preamble['lead_paragraph']}*\n")
        for i, ch in enumerate(chapters, 1):
            md.append(f"\n## {i}. {ch.get('title', f'第{i}章')}\n")
            md.append(ch.get("content", ""))
        _save(out / "02_composed_readable.md", "\n".join(md))

        # ============================
        # Step 3: 精华提炼
        # ============================
        _log(idx, "Step 3/7 · 精华提炼")
        tracker.set_stage("extraction")
        t0 = time.perf_counter()
        output = await extraction_node(state)
        elapsed = time.perf_counter() - t0
        state.update(output)
        result["timings"]["3_extraction"] = round(elapsed, 1)

        quotes = state["highlights"].get("quotes", [])
        _log(idx, f"  → {len(quotes)} 条金句 ({elapsed:.0f}s)")

        _save(out / "03_extraction.json", state["highlights"])
        eq = ["# 金句提炼\n"]
        for i, q in enumerate(quotes, 1):
            eq.append(f"{i}. **[{q.get('placement','')}]** 「{q.get('text','')}」\n")
        _save(out / "03_extraction_readable.md", "\n".join(eq))

        # ============================
        # Step 4: 注释
        # ============================
        _log(idx, "Step 4/7 · 内容注释")
        tracker.set_stage("annotation")
        t0 = time.perf_counter()
        output = await annotation_node(state)
        elapsed = time.perf_counter() - t0
        state.update(output)
        result["timings"]["4_annotation"] = round(elapsed, 1)

        ann = state["annotated_content"]
        _log(idx, f"  → {ann.get('total_footnotes', 0)} 脚注 ({elapsed:.0f}s)")

        _save(out / "04_annotation.json", ann)
        amd = ["# 带注释正文\n"]
        for i, ch in enumerate(ann.get("chapters", []), 1):
            amd.append(f"\n## {i}. {ch.get('title', '')}\n")
            amd.append(ch.get("content", ""))
        _save(out / "04_annotation_readable.md", "\n".join(amd))

        # ============================
        # Step 5: 编者序
        # ============================
        _log(idx, "Step 5/7 · 编者序")
        tracker.set_stage("editor_preface")
        t0 = time.perf_counter()
        output = await editor_preface_node(state)
        elapsed = time.perf_counter() - t0
        state.update(output)
        result["timings"]["5_editor_preface"] = round(elapsed, 1)

        preface = state.get("editor_preface_content", "")
        _log(idx, f"  → {len(preface)} 字 ({elapsed:.0f}s)")
        _save(out / "05_editor_preface.md", f"# 编者序\n\n{preface}")

        # ============================
        # Step 6: 插图
        # ============================
        _log(idx, "Step 6/7 · 插图")
        tracker.set_stage("illustration")
        t0 = time.perf_counter()
        output = await illustration_node(state)
        elapsed = time.perf_counter() - t0
        state.update(output)
        result["timings"]["6_illustration"] = round(elapsed, 1)

        ills = state.get("illustrations", {})
        _log(idx, f"  → 生成 {ills.get('generated_count', 0)}, "
             f"拒绝 {ills.get('rejected_count', 0)} ({elapsed:.0f}s)")
        _save(out / "06_illustration.json", ills)

        # ============================
        # Step 7: 排版 PDF（默认 classic 封面）
        # ============================
        _log(idx, "Step 7/7 · 排版 PDF")
        tracker.set_stage("typeset")
        t0 = time.perf_counter()
        output = await typeset_node(state)
        elapsed = time.perf_counter() - t0
        state.update(output)
        result["timings"]["7_typeset"] = round(elapsed, 1)

        pdf_path = state.get("pdf_path", "")
        _log(idx, f"  → PDF {elapsed:.0f}s")
        _save(out / "07_typst_source.typ", state.get("typst_source", ""))

        if pdf_path and Path(pdf_path).exists():
            dst = out / Path(pdf_path).name
            shutil.copy2(pdf_path, dst)
            result["pdf_path"] = str(dst)
            result["pdf_size_kb"] = round(Path(pdf_path).stat().st_size / 1024, 1)

        # ============================
        # Step 8: 全模板封面渲染
        # ============================
        _log(idx, "Step 8 · 渲染所有封面模板")
        t0 = time.perf_counter()
        covers_dir = out / "covers"
        covers_dir.mkdir(exist_ok=True)

        annotated_for_cover = state.get("annotated_content") or state.get("composed_content") or {}
        cover_vars = _compute_cover_variables(state, annotated_for_cover)

        styles = get_available_styles()
        cover_results = []
        for style_info in styles:
            sid = style_info["id"]
            cover_out = str(covers_dir / f"{sid}.png")
            try:
                await render_cover(style=sid, variables=cover_vars, output_path=cover_out)
                cover_results.append({"style": sid, "status": "ok"})
                _log(idx, f"  封面 {sid} OK")
            except Exception as e:
                cover_results.append({"style": sid, "status": "error", "error": str(e)})
                _log(idx, f"  封面 {sid} FAIL: {e}")

        elapsed_covers = time.perf_counter() - t0
        result["timings"]["8_covers"] = round(elapsed_covers, 1)
        result["cover_results"] = cover_results
        _log(idx, f"  → 封面渲染 {elapsed_covers:.0f}s")

        # ── 保存 state ──
        _save(out / "state.json", state)

        # ── Token / 费用统计 ──
        usage_summary = tracker.summarize()
        usage_stats = _build_usage_stats(usage_summary, state)
        result["usage_stats"] = usage_stats

        pipeline_elapsed = time.perf_counter() - pipeline_start
        result["timings"]["total"] = round(pipeline_elapsed, 1)
        result["status"] = "success"
        _save(out / "99_report.json", result)

        # ── Markdown 报告 ──
        _build_single_report(result, out, usage_stats, usage_summary)

        _log(idx, f"完成 [{label}] {pipeline_elapsed:.0f}s "
             f"({pipeline_elapsed / 60:.1f}min) "
             f"¥{usage_stats.get('total_cost', 0):.4f}")

    except Exception as e:
        result["status"] = "error"
        result["error"] = str(e)
        result["traceback"] = tb_mod.format_exc()
        result["timings"]["total"] = round(time.perf_counter() - pipeline_start, 1)
        _save(out / "99_report.json", result)
        _save(out / "99_error.txt", tb_mod.format_exc())
        _log(idx, f"FAIL [{label}]: {e}")

    return result


def _build_single_report(result: Dict, out: Path, usage_stats: Dict, usage_summary: Dict):
    """为单个播客生成 Markdown 报告。"""
    lines = [
        f"# 流水线报告 — {result.get('title', '')}\n",
        f"- **标签**: {result['label']}",
        f"- **URL**: {result['url']}",
        f"- **总耗时**: {result['timings'].get('total', 0)}s\n",
        "## 各节点耗时\n",
        "| 节点 | 耗时(s) |",
        "|------|---------|",
    ]
    for k, v in result["timings"].items():
        if k != "total":
            lines.append(f"| {k} | {v} |")
    lines.append(f"| **总计** | **{result['timings'].get('total', 0)}** |")

    stage_data = usage_stats.get("llm", {}).get("stages", {})
    totals = usage_stats.get("llm", {}).get("totals", {})
    lines += [
        "\n## Token 消耗\n",
        "| 节点 | 调用 | 输入 | 输出 | 费用 |",
        "|------|------|------|------|------|",
    ]
    for sn, sd in stage_data.items():
        lines.append(
            f"| {sn} | {sd.get('calls', 0)} "
            f"| {sd.get('prompt_tokens', 0):,} "
            f"| {sd.get('completion_tokens', 0):,} "
            f"| ¥{sd.get('cost', 0):.4f} |"
        )
    lines.append(
        f"| **合计** | **{totals.get('calls', 0)}** "
        f"| **{totals.get('prompt_tokens', 0):,}** "
        f"| **{totals.get('completion_tokens', 0):,}** "
        f"| **¥{totals.get('cost', 0):.4f}** |"
    )

    asr = usage_stats.get("asr", {})
    lines += [
        f"\n## 费用汇总",
        f"- ASR ({asr.get('duration_seconds', 0):.0f}s): ¥{asr.get('cost', 0):.4f}",
        f"- LLM: ¥{totals.get('cost', 0):.4f}",
        f"- **总计: ¥{usage_stats.get('total_cost', 0):.4f}**",
    ]

    covers = result.get("cover_results", [])
    if covers:
        lines += ["\n## 封面渲染\n", "| 模板 | 状态 |", "|------|------|"]
        for c in covers:
            s = "OK" if c["status"] == "ok" else f"FAIL: {c.get('error', '')}"
            lines.append(f"| {c['style']} | {s} |")

    _save(out / "99_report.md", "\n".join(lines))


# ================================================================
# 批量汇总
# ================================================================

def _build_batch_summary(final_results: List[Dict], batch_elapsed: float):
    """生成批量测试汇总报告。"""
    from core.services.cover_service import get_available_styles
    styles = get_available_styles()
    style_ids = [s["id"] for s in styles]

    success = sum(1 for r in final_results if r.get("status") == "success")
    failed = len(final_results) - success

    lines = [
        "# 批量全流程测试报告\n",
        f"- **时间**: {TS}",
        f"- **播客数**: {len(PODCAST_URLS)}",
        f"- **并发数**: {MAX_CONCURRENT}",
        f"- **总耗时**: {batch_elapsed:.0f}s ({batch_elapsed / 60:.1f}min)",
        f"- **成功/失败**: {success}/{failed}\n",
        "## 各播客结果\n",
        "| # | 类型 | 标题 | 状态 | 耗时(s) | PDF(KB) | 费用 |",
        "|---|------|------|------|---------|---------|------|",
    ]
    total_cost = 0.0
    for r in final_results:
        ok = r.get("status") == "success"
        title = (r.get("title", "-") or "-")[:25]
        t = r.get("timings", {}).get("total", "-")
        pdf = r.get("pdf_size_kb", "-")
        cost = r.get("usage_stats", {}).get("total_cost", 0) if ok else 0
        total_cost += cost
        mark = "OK" if ok else "FAIL"
        lines.append(f"| {r.get('idx', '?')} | {r.get('label', '')} | {title} | {mark} | {t} | {pdf} | ¥{cost:.3f} |")

    # 封面模板矩阵
    lines += [
        "\n## 封面模板渲染矩阵\n",
        "| 播客 | " + " | ".join(style_ids) + " |",
        "|------" + "|------" * len(style_ids) + "|",
    ]
    for r in final_results:
        covers = r.get("cover_results", [])
        cmap = {c["style"]: c["status"] for c in covers}
        cells = ["OK" if cmap.get(sid) == "ok" else ("FAIL" if sid in cmap else "-") for sid in style_ids]
        lines.append(f"| #{r.get('idx', '?')} {r.get('label', '')} | " + " | ".join(cells) + " |")

    # 各节点平均耗时
    step_totals: Dict[str, List[float]] = {}
    for r in final_results:
        if r.get("status") != "success":
            continue
        for k, v in r.get("timings", {}).items():
            if k == "total":
                continue
            step_totals.setdefault(k, []).append(v)

    if step_totals:
        lines += [
            "\n## 各节点平均耗时\n",
            "| 节点 | 平均(s) | 最短(s) | 最长(s) |",
            "|------|---------|---------|---------|",
        ]
        for k in sorted(step_totals.keys()):
            vals = step_totals[k]
            lines.append(f"| {k} | {sum(vals)/len(vals):.0f} | {min(vals):.0f} | {max(vals):.0f} |")

    lines.append(f"\n## 总费用: ¥{total_cost:.4f}")

    # 失败详情
    failures = [r for r in final_results if r.get("status") != "success"]
    if failures:
        lines.append("\n## 失败详情\n")
        for r in failures:
            lines.append(f"### #{r.get('idx')} {r.get('label')}")
            lines.append(f"- URL: {r.get('url')}")
            lines.append(f"- 错误: {r.get('error', 'unknown')}\n")

    _save(BATCH_DIR / "99_batch_summary.md", "\n".join(lines))
    _save(BATCH_DIR / "99_batch_results.json", final_results)


# ================================================================
# 入口
# ================================================================

async def main():
    print(f"{'=' * 70}")
    print(f"EchoPress 批量全流程测试")
    print(f"{'=' * 70}")
    print(f"  播客数量:  {len(PODCAST_URLS)}")
    print(f"  并发数:    {MAX_CONCURRENT}")
    print(f"  输出目录:  {BATCH_DIR}")
    print(f"  封面模板:  {DEFAULT_COVER_STYLE} (PDF默认) + 全部模板 PNG")
    print(f"{'=' * 70}\n")

    semaphore = asyncio.Semaphore(MAX_CONCURRENT)
    tasks = [
        run_single_podcast(i + 1, label, url, semaphore)
        for i, (label, url) in enumerate(PODCAST_URLS)
    ]

    batch_start = time.perf_counter()
    results = await asyncio.gather(*tasks, return_exceptions=True)
    batch_elapsed = time.perf_counter() - batch_start

    final_results = []
    for i, r in enumerate(results):
        if isinstance(r, BaseException):
            final_results.append({
                "idx": i + 1,
                "label": PODCAST_URLS[i][0],
                "url": PODCAST_URLS[i][1],
                "status": "exception",
                "error": str(r),
                "timings": {},
            })
        else:
            final_results.append(r)

    _build_batch_summary(final_results, batch_elapsed)

    success = sum(1 for r in final_results if r.get("status") == "success")
    failed = len(final_results) - success
    print(f"\n{'=' * 70}")
    print(f"批量测试完成")
    print(f"{'=' * 70}")
    print(f"  成功: {success}/{len(PODCAST_URLS)}")
    print(f"  失败: {failed}/{len(PODCAST_URLS)}")
    print(f"  总耗时: {batch_elapsed:.0f}s ({batch_elapsed / 60:.1f}min)")
    print(f"  报告: {BATCH_DIR / '99_batch_summary.md'}")
    print(f"{'=' * 70}")


if __name__ == "__main__":
    asyncio.run(main())
