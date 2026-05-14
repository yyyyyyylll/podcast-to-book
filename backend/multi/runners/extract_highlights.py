"""
extract_highlights — B 端单集金句提取器（"全局取 K"版本）

策略：
- 一次 LLM 调用看到全章 N 节带段落编号的正文
- 直接产出最多 K 条带定位（section_index + after_paragraph）的金句卡片
- 先按整章价值排序挑选，不按小节配额取前 K 个

替代旧的 C 端 extraction_node 三阶段流程（抽取 → 审核 → 定位）+ 后置截断。

输入：
    episodes/epNN/state_after_compose.json   （含 transcription + composed_content）
输出：
    episodes/epNN/highlights.json            （B 端最终产物，喂给 chapter_for_book）

用法：
    cd backend
    python -m workbench.runners.extract_highlights --job possibility --only 11
    python -m workbench.runners.extract_highlights --job possibility --only 11 --force
    python -m workbench.runners.extract_highlights --job possibility            # 跑所有已 compose 的单集
"""
from __future__ import annotations

from multi.env import setup
setup()

import argparse
import asyncio
import logging
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
logger = logging.getLogger("highlights")

# B 端硬上限：每集（= 一章）总金句条数。
# 可 0 条，常规 1-2 条，最多 3 条。
PER_EPISODE_CAP = 3


async def _process_one(
    *,
    idx: int,
    paths: JobPaths,
    force: bool,
    refresh: bool = False,  # 保留参数兼容旧 CLI；global_k 模式下 refresh 等同于 force
) -> dict[str, Any]:
    ep_dir = paths.episode_dir(idx)
    state_after_compose = ep_dir / "state_after_compose.json"
    if not state_after_compose.exists():
        logger.warning("[ep%02d] state_after_compose.json 不存在，跳过", idx)
        return {"index": idx, "status": "no_compose"}

    highlights_path = ep_dir / "highlights.json"

    if not force and not refresh and highlights_path.exists():
        logger.info("[ep%02d] 已有 highlights.json，复用（--force 强制重跑）", idx)
        existing = read_json(highlights_path)
        return {
            "index": idx,
            "status": "skip",
            "quotes": len(existing.get("quotes", [])),
        }

    state = read_json(state_after_compose)
    composed = state.get("composed_content") or {}
    chapters = composed.get("chapters") or []
    if not chapters:
        logger.warning("[ep%02d] composed_content.chapters 为空，跳过", idx)
        return {"index": idx, "status": "no_chapters"}

    logger.info("[ep%02d] 启动金句提取（global_k）· %d 个小节", idx, len(chapters))

    from core.services.llm_service import get_llm_service
    from multi.prompts import get_workbench_prompts
    from multi.global_k import (
        dynamic_workbench_budget,
        select_global_k,
        _build_full_text_numbered_paragraphs,
    )

    content_type = state.get("content_type", "self_growth")
    prompts = get_workbench_prompts("highlight_global_k", content_type)
    if not prompts:
        return {
            "index": idx,
            "status": "error",
            "error": f"missing workbench/prompts/highlight_global_k/{content_type}.py",
        }

    llm = get_llm_service()
    budget = dynamic_workbench_budget(
        chapters,
        kind="highlight",
        cap_override=PER_EPISODE_CAP,
    )
    k = min(budget["max"], max(budget["target"], budget["min"]), len(chapters))

    t0 = time.monotonic()
    try:
        items = await select_global_k(
            llm=llm,
            sections=chapters,
            k=k,
            system_prompt=prompts["system"],
            user_prompt_template=prompts["user"],
            full_text_builder=_build_full_text_numbered_paragraphs,
            extra_format_kwargs={
                "min_items": budget["min"],
                "target_items": budget["target"],
                "max_items": budget["max"],
                "content_chars": budget["chars"],
            },
            response_key="quotes",
            label="highlight_global_k",
            timeout=120,
            max_tokens=8192,
        )
    except Exception as e:
        logger.error("[ep%02d] ❌ highlight_global_k 失败：%s", idx, e)
        traceback.print_exc()
        write_json(ep_dir / "error_highlights.json", {
            "stage": "highlight_global_k",
            "error": str(e),
            "type": type(e).__name__,
            "_failed_at": datetime.now().isoformat(timespec="seconds"),
        })
        return {"index": idx, "status": "error", "error": str(e)}

    elapsed = round(time.monotonic() - t0, 1)

    quotes: list[dict] = []
    seen_sections: set[int] = set()
    for q in items:
        sec = q.get("section_index")
        if not isinstance(sec, int) or sec < 1 or sec > len(chapters):
            logger.warning("[ep%02d] 跳过非法 section_index=%r", idx, sec)
            continue
        if sec in seen_sections:
            logger.info("[ep%02d] 跳过同小节重复金句：§%d", idx, sec)
            continue
        seen_sections.add(sec)
        ch_title = (chapters[sec - 1].get("title") or "").strip()
        quotes.append({
            "section_index": sec,
            "chapter_title": ch_title,  # 兼容 assembler / typeset 可能查找该字段
            "after_paragraph": q.get("after_paragraph"),
            "text": q.get("text", ""),
            "speaker": q.get("speaker", ""),
            "placement": q.get("placement", "inline_card"),
        })

    payload = {
        "quotes": quotes,
        "_workbench": {
            "strategy": "global_k",
            "k": k,
            "budget": budget,
            "kept_count": len(quotes),
            "per_episode_cap": PER_EPISODE_CAP,
            "elapsed_seconds": elapsed,
            "_built_at": datetime.now().isoformat(timespec="seconds"),
        },
    }
    write_json(highlights_path, payload)
    logger.info(
        "[ep%02d] ✅ 金句完成（global_k）· %d 条（落点：%s）· %.0fs",
        idx, len(quotes),
        ", ".join(f"§{q['section_index']}" for q in quotes) or "—",
        elapsed,
    )

    chapter_for_book = ep_dir / "chapter_for_book.json"
    if chapter_for_book.exists():
        try:
            from multi.assembler import write_chapter_for_book
            write_chapter_for_book(idx, ep_dir)
            logger.info("[ep%02d] 已同步更新 chapter_for_book.json（嵌入金句）", idx)
        except Exception as e:
            logger.warning("[ep%02d] 重组 chapter_for_book.json 失败：%s", idx, e)

    return {
        "index": idx,
        "status": "ok",
        "kept_quotes": len(quotes),
        "elapsed": elapsed,
    }


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
    if not paths.episodes_index_json.exists():
        raise SystemExit(f"未找到 episodes_index.json：{paths.root}")

    index = read_json(paths.episodes_index_json)
    only = _parse_only(args.only)

    logger.info("作业目录：%s", paths.root)
    if only is not None:
        logger.info("只处理：%s", sorted(only))

    results: list[dict] = []
    for ep in index.get("episodes", []):
        idx = ep.get("index")
        if only is not None and idx not in only:
            continue
        result = await _process_one(idx=idx, paths=paths, force=args.force, refresh=args.refresh)
        results.append(result)

    ok = sum(1 for r in results if r.get("status") == "ok")
    err = sum(1 for r in results if r.get("status") == "error")
    skip = sum(1 for r in results if r.get("status") == "skip")
    logger.info("=" * 50)
    logger.info("完成：成功 %d · 失败 %d · 跳过 %d · 总 %d", ok, err, skip, len(results))
    return 1 if err else 0


def main() -> None:
    p = argparse.ArgumentParser(description="B 端单集金句提取（每章 0-3 条，常规 1-2 条）")
    p.add_argument("--job", required=True)
    p.add_argument("--only", default="", help="例：11 或 1,5,11 或 1-3")
    p.add_argument("--force", action="store_true", help="强制重跑（含 LLM），覆盖已有 highlights.json")
    p.add_argument("--refresh", action="store_true", help="仅基于已有 state_after_highlights.json 重新截断（不跑 LLM）")
    args = p.parse_args()

    try:
        rc = asyncio.run(main_async(args))
    except KeyboardInterrupt:
        logger.warning("用户中断")
        rc = 130
    sys.exit(rc)


if __name__ == "__main__":
    main()
