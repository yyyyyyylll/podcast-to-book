"""
run_pipeline — 单集走完 transcription → compose → annotation，输出 chapter_for_book.json

B 端"一期 = 一章"流水线，复用 C 端节点：

    transcription_node  ASR cache 命中 → LLM 清洗、说话人识别、语义分段
            ↓
    compose_node        生成 composed_content.chapters（B 端语义上是"小节列表"）
            ↓
    annotation_node     在小节正文中插入脚注标记 + footnotes 定义
            ↓
    workbench 重映射    chapters → sections，输出 chapter_for_book.json

跳过 C 端的 extraction（金句）/ editor_preface（单集编者序）/ illustration（插图）/ typeset。

输入：
    episodes/epNN/meta.json            （来自 fetch_show + enrich_llm_metadata）
    episodes/epNN/transcript_raw.json  （来自 run_transcription，含 ASR cache）

输出：
    episodes/epNN/state_after_transcription.json   （含 transcription 字段）
    episodes/epNN/state_after_compose.json         （含 composed_content）
    episodes/epNN/state_after_annotation.json      （含 annotated_content）
    episodes/epNN/chapter_for_book.json            （★ B 端最终产出：单章可入书）

每一步支持断点续跑：默认从最早未完成的步骤开始；--force 重跑全部。

用法：

    cd backend
    # ep11 走完整条流水线
    python -m workbench.runners.run_pipeline --job possibility --only 11

    # 只重跑 compose（保留 transcription 结果）
    python -m workbench.runners.run_pipeline --job possibility --only 11 --from compose

    # 全部重跑
    python -m workbench.runners.run_pipeline --job possibility --only 11 --force

    # 批量 13 集
    python -m workbench.runners.run_pipeline --job possibility

    # 用 deepseek-v4-flash 跑 ep11 的 A/B 变体（不污染 baseline）
    python -m workbench.runners.run_pipeline --job possibility --only 11 \
        --model deepseek-v4-flash --variant deepseek-v4-flash
"""
from __future__ import annotations

from multi.env import setup
setup()

import argparse
import asyncio
import logging
import re
import sys
import time
import traceback
from datetime import datetime
from typing import Any

from multi.io import JobPaths, read_json, write_json

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("pipeline")

STEPS = ("transcribe", "compose", "annotate")

# B 端硬上限：整集（= 一章）保留的脚注总数
# 语义"一期 = 一章"，可 0 条，常规约 2 条，超长最多 5 条。
PER_EPISODE_FOOTNOTE_CAP = 5
BUSINESS_FOOTNOTE_CAP = 8


def _truncate_footnotes_per_episode(
    annotated_chapters: list[dict],
    cap: int = PER_EPISODE_FOOTNOTE_CAP,
) -> tuple[list[dict], int]:
    """
    全集统一截断脚注到 cap 条以内（"一期 = 一章"语义）。

    保留策略：每节最多 1 条 + 全集总数 ≤ cap，按章节顺序优先。

    实现要点：
      1. 重写每章 content：删除被丢弃的 [^N] marker，重写末尾 footnote section
      2. 全集统一从 1 开始重编号
      3. 用占位 token 避免编号串扰
    """
    if not annotated_chapters:
        return annotated_chapters, 0

    # Phase 1：决定保留谁
    keep_per_chapter: list[list[dict]] = []
    drop_per_chapter: list[list[dict]] = []
    total_kept = 0
    for ch in annotated_chapters:
        fns = list(ch.get("footnotes") or [])
        keep_here: list[dict] = []
        drop_here: list[dict] = []
        for fn in fns:
            if len(keep_here) < 1 and total_kept < cap:
                keep_here.append(fn)
                total_kept += 1
            else:
                drop_here.append(fn)
        keep_per_chapter.append(keep_here)
        drop_per_chapter.append(drop_here)

    # Phase 2：按全集顺序重编号
    new_num = 1
    renumber: list[dict[int, int]] = []  # 每章 old_num -> new_num
    for keep_here in keep_per_chapter:
        local_map: dict[int, int] = {}
        for fn in keep_here:
            old = int(fn.get("number", 0))
            local_map[old] = new_num
            fn["_new_number"] = new_num
            new_num += 1
        renumber.append(local_map)

    # Phase 3：重写每章 content
    new_chapters: list[dict] = []
    for ci, ch in enumerate(annotated_chapters):
        keep = keep_per_chapter[ci]
        drop = drop_per_chapter[ci]
        content = ch.get("content", "") or ""

        # 砍掉末尾 footnote section（最后一个 \n\n---\n\n[^N]: ...）
        matches = list(re.finditer(r"\n\n---\n\n(?=\[\^\d+\]:)", content))
        if matches:
            content = content[: matches[-1].start()]

        # 删丢弃 marker
        for fn in drop:
            old_num = int(fn.get("number", 0))
            content = content.replace(f"[^{old_num}]", "")
        # 清掉因 marker 删除留下的多余空格（不动换行）
        content = re.sub(r"[ \t]+", " ", content)
        content = re.sub(r"[ \t]+\n", "\n", content)
        # 中文字符之间的残留空格删除（marker 删除后常见）
        content = re.sub(
            r"(?<=[\u4e00-\u9fff”\"])\s+(?=[\u4e00-\u9fff“\"])",
            "",
            content,
        )

        # 保留 marker：先 → 占位，再 → 新编号（避免链式覆盖）
        for fn in keep:
            old_num = int(fn.get("number", 0))
            content = content.replace(f"[^{old_num}]", f"<<KEEP_{old_num}>>")
        for fn in keep:
            old_num = int(fn.get("number", 0))
            new_n = renumber[ci][old_num]
            content = content.replace(f"<<KEEP_{old_num}>>", f"[^{new_n}]")

        # 追加新 footnote section
        if keep:
            lines = []
            for fn in keep:
                old_num = int(fn.get("number", 0))
                new_n = renumber[ci][old_num]
                text = fn.get("text", "")
                lines.append(f"[^{new_n}]: {text}")
            new_section = "\n\n---\n\n" + "\n".join(lines)
            content = content.rstrip() + new_section
        else:
            content = content.rstrip()

        # 把保留的 footnote 用新编号写回 chapter.footnotes
        new_fns = []
        for fn in keep:
            old_num = int(fn.get("number", 0))
            new_fn = {**fn, "number": renumber[ci][old_num]}
            new_fn.pop("_new_number", None)
            new_fns.append(new_fn)

        new_chapters.append({**ch, "content": content, "footnotes": new_fns})

    return new_chapters, total_kept


def _strip_episode_prefix(title: str) -> str:
    """
    去掉单集标题中的"序号. "前缀，以及"播客名｜"前缀。
    "13. 如何成为一个内核稳定的人？" → "如何成为一个内核稳定的人？"
    "D101个朋友｜飞天魔女 Cherry「…」" → "飞天魔女 Cherry「…」"
    """
    t = re.sub(r"^\s*\d+[\.\、\:\：]\s*", "", title or "").strip()
    if "｜" in t:
        t = t.split("｜", 1)[1].strip()
    return t


def _build_initial_state(meta: dict[str, Any], show: dict[str, Any], transcript_raw: dict[str, Any] | None) -> dict[str, Any]:
    """
    用 meta.json + show.json 组装 PodBookState。

    audio_path：优先用 transcript_raw._workbench.submitted_audio（保证 ASR cache 命中），
    否则用 audio_url。
    """
    if transcript_raw and isinstance(transcript_raw, dict):
        submitted = (transcript_raw.get("_workbench") or {}).get("submitted_audio", "")
        audio_path = submitted or meta.get("audio_url", "")
    else:
        audio_path = meta.get("audio_url", "")

    title = meta.get("title", "")
    episode_title = title

    from core.workflow.state import create_initial_state

    state = create_initial_state(
        task_id=meta.get("episode_id") or f"workbench-{meta.get('index', 'x')}",
        audio_path=audio_path,
        title=title,
        author=meta.get("host_name") or meta.get("podcast_name", ""),
        description=meta.get("description") or meta.get("shownotes_text", ""),
        host_name=meta.get("host_name", ""),
        guest_names=list(meta.get("guest_names") or []),
        company_names=list(meta.get("company_names") or []),
        podcast_intro=show.get("description", "") or show.get("shownotes_text", ""),
        proper_nouns=list(meta.get("proper_nouns") or []),
        name_aliases=dict(meta.get("name_aliases") or {}),
        cover_url=meta.get("cover_url", ""),
        cover_style="swiss",
        podcast_name=meta.get("podcast_name", ""),
        podcast_url="",
        publish_date=meta.get("publish_date", ""),
        episode_title=episode_title,
    )
    return dict(state)


async def _run_transcription(state: dict[str, Any]) -> dict[str, Any]:
    from core.workflow.nodes.transcription import transcription_node
    update = await transcription_node(state)
    if not isinstance(update, dict):
        raise RuntimeError(f"transcription_node 返回非 dict: {type(update)}")
    return {**state, **update}


async def _run_compose(state: dict[str, Any]) -> dict[str, Any]:
    from core.workflow.nodes.compose import compose_node
    update = await compose_node(state)
    if not isinstance(update, dict):
        raise RuntimeError(f"compose_node 返回非 dict: {type(update)}")
    return {**state, **update}


async def _run_annotation(state: dict[str, Any]) -> dict[str, Any]:
    """
    B 端注释步骤（"全局取 K"版本）：

    一次 LLM 调用看到全章 N 节正文，直接产出最多 K 条带定位 + 详注的脚注。
    硬约束 "K 条必须落在 K 个不同的小节"，避免分布不均。

    替代原来的逐节循环 `_annotate_chapter` + 后置 `_truncate_footnotes_per_episode`，
    旧的 `_truncate_footnotes_per_episode` 仍保留（不再被注释路径调用），可作为
    后续历史产物的修复工具。
    """
    from core.workflow.state import WorkflowStage
    from core.services.llm_service import get_llm_service
    from multi.prompts import get_workbench_prompts
    from multi.global_k import dynamic_workbench_budget, select_global_k, _build_full_text

    composed = state.get("composed_content") or {}
    chapters = composed.get("chapters") or []
    if not chapters:
        raise RuntimeError("composed_content.chapters 缺失，无法注释")

    content_type = state.get("content_type", "self_growth")
    prompts = get_workbench_prompts("annotation_global_k", content_type)
    if not prompts:
        raise RuntimeError(
            f"未找到 workbench/prompts/annotation_global_k/{content_type}.py，无法走全局取 K 注释"
        )

    llm = get_llm_service()
    footnote_cap = BUSINESS_FOOTNOTE_CAP if content_type == "business" else PER_EPISODE_FOOTNOTE_CAP
    budget = dynamic_workbench_budget(
        chapters,
        kind="annotation",
        cap_override=footnote_cap,
    )
    if content_type == "business":
        budget["target"] = min(budget["max"], max(budget["target"], 6))
    k = min(budget["max"], max(budget["target"], budget["min"]), len(chapters))

    items = await select_global_k(
        llm=llm,
        sections=chapters,
        k=k,
        system_prompt=prompts["system"],
        user_prompt_template=prompts["user"],
        full_text_builder=_build_full_text,
        extra_format_kwargs={
            "min_items": budget["min"],
            "target_items": budget["target"],
            "max_items": budget["max"],
            "content_chars": budget["chars"],
        },
        response_key="footnotes",
        label="annotation_global_k",
        timeout=180,
        max_tokens=32768,
    )

    # 应用脚注：在对应 section 正文中插入 [^N] 标记 + 节末追加 footnote 定义
    annotated_chapters, total_footnotes = _apply_global_footnotes(chapters, items)

    annotated_content = {
        **composed,
        "chapters": annotated_chapters,
        "total_footnotes": total_footnotes,
        "_workbench": {
            "strategy": "global_k",
            "k": k,
            "budget": budget,
            "kept_footnote_count": total_footnotes,
            "per_episode_footnote_cap": footnote_cap,
        },
    }

    update = {
        "annotated_content": annotated_content,
        "current_stage": WorkflowStage.ANNOTATION.value,
    }
    return {**state, **update}


def _apply_global_footnotes(
    chapters: list[dict],
    items: list[dict],
) -> tuple[list[dict], int]:
    """
    把 LLM 全局选出的 footnotes 应用到章节里：

    - 每条 footnote 的 section_index（1-based）→ 找到对应章节
    - 在该章节正文中找 target_word，紧跟其后插入 [^N]
    - 在该章节末尾以 `\n\n---\n\n[^N]: ...` 形式追加 footnote 定义
    - 全章脚注按选中顺序从 1 开始编号
    - target_word 不在正文时跳过该条（log warning）
    """
    out_chapters: list[dict] = [dict(ch) for ch in chapters]
    # 给每章准备 footnotes 容器
    for ch in out_chapters:
        ch.setdefault("footnotes", [])

    counter = 0
    seen_sections: set[int] = set()
    for it in items:
        sec_idx_1b = it.get("section_index")
        target = (it.get("target_word") or "").strip()
        text = (it.get("text") or "").strip()
        ftype = it.get("type") or "editor_note"
        if not isinstance(sec_idx_1b, int) or sec_idx_1b < 1 or sec_idx_1b > len(out_chapters):
            logger.warning("[annotation] 跳过非法 section_index=%r", sec_idx_1b)
            continue
        if sec_idx_1b in seen_sections:
            logger.info("[annotation] 跳过同小节重复注释：§%d", sec_idx_1b)
            continue
        if not target or not text:
            logger.warning("[annotation] 跳过空 target_word/text 项：%r", it)
            continue

        ch = out_chapters[sec_idx_1b - 1]
        content = ch.get("content", "") or ""
        pos = content.find(target)
        if pos < 0:
            logger.warning(
                "[annotation] 第 %d 节正文中未找到 target_word=%r，跳过",
                sec_idx_1b, target,
            )
            continue

        counter += 1
        seen_sections.add(sec_idx_1b)
        marker = f"[^{counter}]"
        end = pos + len(target)
        new_content = content[:end] + marker + content[end:]
        formatted_text = _format_footnote_text(target, text)
        ch["content"] = new_content
        ch["footnotes"].append({
            "number": counter,
            "type": ftype,
            "text": formatted_text,
            "target_word": target,
        })

    # 把 footnote 定义追加到节末
    for ch in out_chapters:
        fns = ch.get("footnotes") or []
        if not fns:
            continue
        lines = [f"[^{fn['number']}]: {fn['text']}" for fn in fns]
        ch["content"] = ch["content"].rstrip() + "\n\n---\n\n" + "\n".join(lines)

    return out_chapters, counter


def _format_footnote_text(target: str, text: str) -> str:
    """Ensure book footnotes name the annotated term before explaining it."""
    target = (target or "").strip()
    text = (text or "").strip()
    if not target or not text:
        return text
    head = text[: max(len(target) + 8, 24)]
    if target in head:
        return text
    return f"{target}：{text}"


def _to_chapter_for_book_legacy(
    *,
    idx: int,
    meta: dict[str, Any],
    state: dict[str, Any],
    ep_dir: Any = None,
) -> dict[str, Any]:
    """
    workbench 语义重映射：把 C 端 composed/annotated 的"chapters"重命名为"sections"，
    整集打包成单章 chapter_for_book。

    章名优先级：
      1. composed_content.title（compose Phase 3 LLM 生成的简洁书章名，13 字左右）
      2. episode.title 剥序号前缀（fallback；原文常带营销文案，可能较长）

    正文优先用 annotated_content（含脚注），否则退回 composed_content。
    """
    annotated = state.get("annotated_content") or {}
    composed = state.get("composed_content") or {}
    src = annotated if annotated.get("chapters") else composed

    raw_sections = src.get("chapters") or []
    sections: list[dict[str, Any]] = []
    for i, ch in enumerate(raw_sections, start=1):
        sections.append({
            "section_index": i,
            "section_title": ch.get("title", "").strip() or f"第 {i} 节",
            "content": ch.get("content", ""),
            "key_points": list(ch.get("key_points") or []),
            "section_type": ch.get("section_type", ""),
            "time_range": ch.get("time_range", []),
            "source_segment_ids": list(ch.get("source_segment_ids") or []),
        })

    footnotes_total = annotated.get("total_footnotes", 0) if annotated else 0

    raw_episode_title = meta.get("title", "")
    fallback_title = _strip_episode_prefix(raw_episode_title)
    composed_title = (composed.get("title") or "").strip()
    final_title = fallback_title or composed_title
    title_source = "episode_title_stripped" if fallback_title else "composed_content.title"

    # 旁路加载：金句 / 插图（若存在则嵌入）
    highlights_payload: list[dict] = []
    illustrations_payload: list[dict] = []
    side_artifacts: dict[str, Any] = {}
    if ep_dir is not None:
        from pathlib import Path as _Path
        h_path = _Path(ep_dir) / "highlights.json"
        if h_path.exists():
            try:
                hl = read_json(h_path)
                highlights_payload = list(hl.get("quotes") or [])
                side_artifacts["highlights"] = {
                    "count": len(highlights_payload),
                    "raw_count": (hl.get("_workbench") or {}).get("raw_count"),
                    "per_chapter_cap": (hl.get("_workbench") or {}).get("per_chapter_cap"),
                }
            except Exception as e:
                side_artifacts["highlights_error"] = str(e)

        i_path = _Path(ep_dir) / "illustrations.json"
        if i_path.exists():
            try:
                il = read_json(i_path)
                illustrations_payload = list(il.get("images") or [])
                side_artifacts["illustrations"] = {
                    "count": len(illustrations_payload),
                    "rejected": il.get("rejected_count", 0),
                }
            except Exception as e:
                side_artifacts["illustrations_error"] = str(e)

    return {
        "chapter_index": idx,
        "chapter_title": final_title,
        "chapter_title_raw": raw_episode_title,
        "chapter_title_from_compose": composed_title,
        "chapter_title_from_episode": fallback_title,
        "chapter_title_source": title_source,
        "episode_id": meta.get("episode_id", ""),
        "podcast_name": meta.get("podcast_name", ""),
        "host_name": meta.get("host_name", ""),
        "guest_names": list(meta.get("guest_names") or []),
        "publish_date": meta.get("publish_date", ""),
        "duration_minutes": meta.get("duration_minutes", 0),
        "core_theme": src.get("core_theme", ""),
        "theme_keywords": list(src.get("theme_keywords") or []),
        "speakers": list(src.get("speakers") or []),
        "sections": sections,
        "footnotes_total": footnotes_total,
        "highlights": highlights_payload,
        "illustrations": illustrations_payload,
        "_pipeline": {
            "source": "annotated_content" if src is annotated else "composed_content",
            "annotated": bool(annotated),
            "side_artifacts": side_artifacts,
            "_built_at": datetime.now().isoformat(timespec="seconds"),
        },
    }


async def _process_one(
    *,
    idx: int,
    paths: JobPaths,
    show: dict[str, Any],
    from_step: str,
    force: bool,
    variant: str = "",
    compose_style: str = "",
) -> dict[str, Any]:
    ep_dir = paths.episode_dir(idx)
    meta_path = ep_dir / "meta.json"
    if not meta_path.exists():
        logger.warning("[ep%02d] meta.json 不存在，跳过", idx)
        return {"index": idx, "status": "no_meta"}

    meta = read_json(meta_path)
    transcript_raw_path = ep_dir / "transcript_raw.json"
    transcript_raw = read_json(transcript_raw_path) if transcript_raw_path.exists() else None

    # transcription 永远共用 baseline 的产物（ASR 缓存 + LLM 清洗结果一致）
    state_after_transcription = ep_dir / "state_after_transcription.json"

    if variant:
        # variant 模式：compose / annotation 产物写到 ep_dir/variants/<variant>/
        # 不写 chapter_for_book.json（避免覆盖 baseline 章），留给报告 runner 直接对比 state
        variant_dir = ep_dir / "variants" / variant
        variant_dir.mkdir(parents=True, exist_ok=True)
        state_after_compose = variant_dir / "state_after_compose.json"
        state_after_annotation = variant_dir / "state_after_annotation.json"
    else:
        state_after_compose = ep_dir / "state_after_compose.json"
        state_after_annotation = ep_dir / "state_after_annotation.json"
    chapter_for_book = ep_dir / "chapter_for_book.json"

    title = meta.get("title", "")[:40]
    duration_seconds = meta.get("duration_seconds") or 0
    logger.info(
        "[ep%02d] 处理：%s · %.1f 分钟",
        idx, title, duration_seconds / 60,
    )

    step_order = list(STEPS)
    if from_step not in step_order:
        raise SystemExit(f"--from 取值非法：{from_step}（应为 {step_order}）")
    start_at = step_order.index(from_step)

    # 注：force 只决定"产物已存在时是否复用",不再强制 start_at=0。
    # 这样 `--from annotate --force` 才能正确地"从 annotate 开始 + 强制重跑 annotate"。
    # 想全部重跑可用 `--force`(默认 from=transcribe)。

    # 安全护栏：variant 模式严禁重跑 transcribe（会写回 baseline 的 state_after_transcription.json）
    if variant and start_at < step_order.index("compose"):
        logger.warning(
            "[ep%02d] variant=%s 模式禁止重跑 transcribe，已强制 start_at=compose（保护 baseline）",
            idx, variant,
        )
        start_at = step_order.index("compose")

    state: dict[str, Any] | None = None
    timing: dict[str, float] = {}

    if start_at <= step_order.index("transcribe"):
        if not force and state_after_transcription.exists():
            logger.info("[ep%02d] 复用已有 state_after_transcription.json", idx)
            state = read_json(state_after_transcription)
        else:
            logger.info("[ep%02d] Step 1/3 transcription_node 启动...", idx)
            state = _build_initial_state(meta, show, transcript_raw)
            t0 = time.monotonic()
            try:
                state = await _run_transcription(state)
            except Exception as e:
                logger.error("[ep%02d] ❌ transcription 失败：%s", idx, e)
                traceback.print_exc()
                write_json(ep_dir / "error_pipeline.json", {
                    "stage": "transcription",
                    "error": str(e),
                    "type": type(e).__name__,
                    "_failed_at": datetime.now().isoformat(timespec="seconds"),
                })
                return {"index": idx, "status": "error", "stage": "transcription", "error": str(e)}
            timing["transcribe"] = round(time.monotonic() - t0, 1)
            write_json(state_after_transcription, _state_persist_safe(state))
            seg_count = len(((state.get("transcription") or {}).get("segments") or []))
            logger.info("[ep%02d] ✅ transcription 完成 · %d 段 · %.0fs", idx, seg_count, timing["transcribe"])
    else:
        if not state_after_transcription.exists():
            raise SystemExit(f"[ep{idx:02d}] --from {from_step} 但缺少 state_after_transcription.json")
        state = read_json(state_after_transcription)
        logger.info("[ep%02d] 跳过 transcription（--from=%s）", idx, from_step)

    if compose_style and state is not None:
        state["narrative_type"] = compose_style
        logger.info("[ep%02d] compose_style=%s → narrative_type 覆写为 %s", idx, compose_style, compose_style)

    if start_at <= step_order.index("compose"):
        if not force and state_after_compose.exists():
            logger.info("[ep%02d] 复用已有 state_after_compose.json", idx)
            state = read_json(state_after_compose)
        else:
            logger.info("[ep%02d] Step 2/3 compose_node 启动...", idx)
            t0 = time.monotonic()
            try:
                state = await _run_compose(state)
            except Exception as e:
                logger.error("[ep%02d] ❌ compose 失败：%s", idx, e)
                traceback.print_exc()
                write_json(ep_dir / "error_pipeline.json", {
                    "stage": "compose",
                    "error": str(e),
                    "type": type(e).__name__,
                    "_failed_at": datetime.now().isoformat(timespec="seconds"),
                })
                return {"index": idx, "status": "error", "stage": "compose", "error": str(e)}
            timing["compose"] = round(time.monotonic() - t0, 1)
            write_json(state_after_compose, _state_persist_safe(state))
            ch_count = len(((state.get("composed_content") or {}).get("chapters") or []))
            logger.info("[ep%02d] ✅ compose 完成 · %d 个小节 · %.0fs", idx, ch_count, timing["compose"])
    else:
        if not state_after_compose.exists():
            raise SystemExit(f"[ep{idx:02d}] --from {from_step} 但缺少 state_after_compose.json")
        state = read_json(state_after_compose)
        logger.info("[ep%02d] 跳过 compose（--from=%s）", idx, from_step)

    if start_at <= step_order.index("annotate"):
        if not force and state_after_annotation.exists():
            logger.info("[ep%02d] 复用已有 state_after_annotation.json", idx)
            state = read_json(state_after_annotation)
        else:
            logger.info("[ep%02d] Step 3/3 annotation_node 启动...", idx)
            t0 = time.monotonic()
            try:
                state = await _run_annotation(state)
            except Exception as e:
                logger.error("[ep%02d] ❌ annotation 失败：%s", idx, e)
                traceback.print_exc()
                write_json(ep_dir / "error_pipeline.json", {
                    "stage": "annotation",
                    "error": str(e),
                    "type": type(e).__name__,
                    "_failed_at": datetime.now().isoformat(timespec="seconds"),
                })
                return {"index": idx, "status": "error", "stage": "annotation", "error": str(e)}
            timing["annotate"] = round(time.monotonic() - t0, 1)
            write_json(state_after_annotation, _state_persist_safe(state))
            fn_count = (state.get("annotated_content") or {}).get("total_footnotes", 0)
            logger.info("[ep%02d] ✅ annotation 完成 · %d 条脚注 · %.0fs", idx, fn_count, timing["annotate"])

    if variant:
        # variant 模式：不重写 baseline 的 chapter_for_book.json
        annotated = (state.get("annotated_content") or {})
        ch_count = len(annotated.get("chapters") or [])
        fn_count = annotated.get("total_footnotes", 0)
        logger.info(
            "[ep%02d] ✅ variant=%s 完成 · %d 节 · %d 脚注（产物在 variants/%s/）",
            idx, variant, ch_count, fn_count, variant,
        )
        return {
            "index": idx,
            "status": "ok",
            "variant": variant,
            "sections": ch_count,
            "footnotes": fn_count,
            "timing": timing,
        }

    from multi.assembler import write_chapter_for_book
    chapter = write_chapter_for_book(idx, ep_dir)
    logger.info(
        "[ep%02d] 📖 chapter_for_book.json 已生成 · 章名《%s》· %d 节 · %d 脚注 · %d 金句 · %d 插图",
        idx, chapter["chapter_title"], len(chapter["sections"]),
        chapter["footnotes_total"], len(chapter.get("highlights") or []),
        len(chapter.get("illustrations") or []),
    )

    return {
        "index": idx,
        "status": "ok",
        "chapter_title": chapter["chapter_title"],
        "sections": len(chapter["sections"]),
        "footnotes": chapter["footnotes_total"],
        "timing": timing,
    }


def _state_persist_safe(state: dict[str, Any]) -> dict[str, Any]:
    """state 持久化前剥掉不可序列化字段（如 callable）。当前结构都是 dict/list/str/num，直接返回。"""
    return dict(state)


def _parse_only(only: str) -> set[int] | None:
    if not only:
        return None
    out: set[int] = set()
    for part in only.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            out.update(range(int(a), int(b) + 1))
        else:
            out.add(int(part))
    return out


async def main_async(args: argparse.Namespace) -> int:
    paths = JobPaths.open(args.job)
    if not paths.show_json.exists():
        raise SystemExit(f"未找到 show.json：{paths.root}")

    show = read_json(paths.show_json)
    index = read_json(paths.episodes_index_json)
    only = _parse_only(args.only)

    # 若存在 book_config.json，读取 compose_style（优先 CLI --compose-style 覆写）
    book_config_path = paths.root / "book_config.json"
    compose_style = args.compose_style or ""
    if not compose_style and book_config_path.exists():
        book_cfg = read_json(book_config_path)
        compose_style = book_cfg.get("compose_style", "")

    logger.info("作业目录：%s", paths.root)
    logger.info("节目：%s · 单集 %d", show.get("title", ""), index.get("total", 0))
    if only is not None:
        logger.info("只处理：%s", sorted(only))
    logger.info("--from=%s · --force=%s", args.from_step, args.force)
    if compose_style:
        logger.info("compose_style=%s（来自 %s）", compose_style,
                    "CLI" if args.compose_style else "book_config.json")

    # 模型覆写：用于 A/B 模型对比（如 deepseek-v4-flash vs gemini-3-flash-preview）
    if args.model:
        from core.services.llm_service import set_model_override
        set_model_override(args.model)
        logger.info("🔁 LLM 模型覆写：所有 LLM 调用强制使用 %s", args.model)

    if args.variant:
        logger.info("🧪 variant=%s · compose/annotation 产物写入 variants/%s/", args.variant, args.variant)
        if args.from_step == "transcribe":
            # variant 模式默认从 compose 开始，避免重跑 ASR / 清洗
            args.from_step = "compose"
            logger.info("🧪 variant 模式自动 --from=compose（共用 baseline transcription）")

    results: list[dict[str, Any]] = []
    total_started = time.monotonic()

    episodes_to_run = [
        ep for ep in index.get("episodes", [])
        if only is None or ep.get("index") in only
    ]

    if args.parallel and len(episodes_to_run) > 1:
        logger.info("🚀 并行模式：同时处理 %d 集", len(episodes_to_run))
        tasks = [
            _process_one(
                idx=ep.get("index"),
                paths=paths,
                show=show,
                from_step=args.from_step,
                force=args.force,
                variant=args.variant,
                compose_style=compose_style,
            )
            for ep in episodes_to_run
        ]
        results = list(await asyncio.gather(*tasks, return_exceptions=False))
    else:
        for ep in episodes_to_run:
            idx = ep.get("index")
            result = await _process_one(
                idx=idx,
                paths=paths,
                show=show,
                from_step=args.from_step,
                force=args.force,
                variant=args.variant,
                compose_style=compose_style,
            )
            results.append(result)

    elapsed = time.monotonic() - total_started
    ok = sum(1 for r in results if r.get("status") == "ok")
    err = sum(1 for r in results if r.get("status") == "error")
    other = sum(1 for r in results if r.get("status") in ("no_meta",))

    logger.info("=" * 50)
    logger.info(
        "完成：成功 %d · 失败 %d · 缺料 %d · 总 %d · 耗时 %.0fs",
        ok, err, other, len(results), elapsed,
    )
    return 1 if err else 0


def main() -> None:
    p = argparse.ArgumentParser(description="单集走完 transcription→compose→annotation 流水线")
    p.add_argument("--job", required=True, help="作业 ID")
    p.add_argument("--only", default="", help="只跑指定单集，例：1,5,11 或 1-5")
    p.add_argument(
        "--from", dest="from_step", default="transcribe",
        choices=list(STEPS),
        help="从指定步骤开始（默认 transcribe；前置步骤已完成时会自动复用结果）",
    )
    p.add_argument("--force", action="store_true", help="强制重跑全部步骤，覆盖已有 state_after_*.json")
    p.add_argument(
        "--model", default="",
        help="可选：用 set_model_override 强制所有 LLM 调用走指定模型（如 deepseek-v4-flash），用于 A/B 对比",
    )
    p.add_argument(
        "--variant", default="",
        help="可选：变体名称（如 deepseek-v4-flash）。指定后 compose/annotation 产物写入 variants/<name>/，不污染 baseline；通常配 --model 一起用",
    )
    p.add_argument(
        "--parallel", action="store_true",
        help="并行处理多集（asyncio.gather），适合多期同时转写/编排，各集独立目录不冲突",
    )
    p.add_argument(
        "--compose-style", dest="compose_style", default="",
        help="覆写 compose 风格（如 conversational）；若未指定则从 book_config.json 的 compose_style 字段读取",
    )
    args = p.parse_args()

    try:
        rc = asyncio.run(main_async(args))
    except KeyboardInterrupt:
        logger.warning("用户中断")
        rc = 130
    sys.exit(rc)


if __name__ == "__main__":
    main()
