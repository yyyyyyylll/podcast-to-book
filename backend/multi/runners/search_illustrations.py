"""
search_illustrations — B 端单集插图搜索器（只搜不生）

完全复用 C 端 illustration_node 的 search 分支三阶段：
    _phase1_search          LLM 逐节识别"具体实体"（人/作品/地标/产品）
    _phase2_search          Serper.dev (Google Images) 搜图 + 多候选下载
    _phase3_review_searched VLM 逐张审核，取第一张通过的

跳过 C 端的 concept 分支（_phase1_concepts → _phase2_generate → _phase3_review_generated），
即"AI 生图"完全不调用，符合 B 端"只搜图、不 AI 生成"的需求。

B 端额外的硬约束（"一期 = 一章"语义）：
- phase1 先从整章全局排序挑选搜图实体，不按小节配额截断
- 常规章节目标约 2 张，短章至少 1 张，超长/高密度章节最多 5 张

C 端 settings 维持不变（避免污染 C 端业务）：
    ILLUSTRATION_SEARCH_MAX_RESULTS     = 10    每个实体下载 10 张候选给 VLM 审
    ILLUSTRATION_SEARCH_MIN_WIDTH/HEIGHT = 800/600

输入：
    episodes/epNN/state_after_compose.json
输出：
    episodes/epNN/illustrations/                各张图片
    episodes/epNN/illustrations.json            B 端图片清单（喂给 chapter_for_book）

用法：
    cd backend
    python -m workbench.runners.search_illustrations --job possibility --only 11
    python -m workbench.runners.search_illustrations --job possibility --only 11 --force
    python -m workbench.runners.search_illustrations --job possibility
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
from pathlib import Path
from typing import Any

from multi.io import JobPaths, read_json, write_json

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("illustrations")

# B 端硬上限：每集最多保留几个搜图实体（= 通过 VLM 后最多几张图）
# 语义"一期 = 一章"，至少 1 张，常规约 2 张，超长最多 5 张。
PER_EPISODE_ENTITY_CAP = 5


def _trim_solid_image_margins(path: Path, *, pad_px: int = 12) -> bool:
    """
    Trim large solid-color margins from downloaded search images.

    Some book-cover results, especially Amazon thumbnails, are placed on a wide
    white canvas. If we typeset that raw file, the caption appears far below the
    visible cover. This keeps a small padding and overwrites the local file.
    """
    try:
        from PIL import Image, ImageChops
    except ImportError:
        return False

    if not path.exists():
        return False

    try:
        im = Image.open(path).convert("RGB")
    except Exception:
        return False

    bg = Image.new("RGB", im.size, im.getpixel((0, 0)))
    diff = ImageChops.difference(im, bg)
    bbox = diff.getbbox()
    if not bbox:
        return False

    left, top, right, bottom = bbox
    left = max(0, left - pad_px)
    top = max(0, top - pad_px)
    right = min(im.size[0], right + pad_px)
    bottom = min(im.size[1], bottom + pad_px)

    new_w = right - left
    new_h = bottom - top
    if new_w <= 0 or new_h <= 0:
        return False

    removed_w = im.size[0] - new_w
    removed_h = im.size[1] - new_h
    # Avoid touching normal photos with only tiny borders.
    if removed_w < im.size[0] * 0.12 and removed_h < im.size[1] * 0.12:
        return False

    cropped = im.crop((left, top, right, bottom))
    try:
        cropped.save(path, quality=95)
    except Exception:
        return False
    logger.info(
        "裁掉图片白边：%s %sx%s → %sx%s",
        path.name, im.size[0], im.size[1], new_w, new_h,
    )
    return True


async def _process_one(
    *,
    idx: int,
    paths: JobPaths,
    force: bool,
    cap: int = PER_EPISODE_ENTITY_CAP,
    refresh: bool = False,
) -> dict[str, Any]:
    ep_dir = paths.episode_dir(idx)
    state_after_compose = ep_dir / "state_after_compose.json"
    if not state_after_compose.exists():
        logger.warning("[ep%02d] state_after_compose.json 不存在，跳过", idx)
        return {"index": idx, "status": "no_compose"}

    illustrations_dir = ep_dir / "illustrations"
    illustrations_json = ep_dir / "illustrations.json"

    # --refresh：不跑 LLM/Serper/VLM，仅基于已有 illustrations.json 重新按 cap 截断
    if refresh and illustrations_json.exists():
        existing = read_json(illustrations_json)
        old_images = list(existing.get("images") or [])
        # 刷新模式无法重新全章排序，只能在已有结果中应用当前硬上限；
        # 真正的全局择优请使用 --force 重新跑 LLM。
        seen_sections: set[str] = set()
        kept: list[dict] = []
        for im in old_images:
            sec = (im.get("chapter_title") or im.get("section_title") or "").strip()
            if sec and sec in seen_sections:
                continue
            seen_sections.add(sec)
            kept.append(im)
            if len(kept) >= cap:
                break
        existing["images"] = kept
        existing["total_count"] = len(kept)
        wb = dict(existing.get("_workbench") or {})
        wb["per_episode_entity_cap"] = cap
        wb["refreshed"] = True
        wb["_built_at"] = datetime.now().isoformat(timespec="seconds")
        existing["_workbench"] = wb
        write_json(illustrations_json, existing)
        logger.info(
            "[ep%02d] ✅ 仅刷新截断 · 原始 %d → 截断 %d 张",
            idx, len(old_images), len(kept),
        )
        chapter_for_book = ep_dir / "chapter_for_book.json"
        if chapter_for_book.exists():
            from multi.assembler import write_chapter_for_book
            write_chapter_for_book(idx, ep_dir)
            logger.info("[ep%02d] 已同步更新 chapter_for_book.json", idx)
        return {"index": idx, "status": "refresh", "approved": len(kept)}

    if not force and illustrations_json.exists():
        existing = read_json(illustrations_json)
        logger.info(
            "[ep%02d] 已有 illustrations.json（%d 张通过），复用（--force 强制重跑）",
            idx, len(existing.get("images", [])),
        )
        return {"index": idx, "status": "skip", "approved": len(existing.get("images", []))}

    illustrations_dir.mkdir(parents=True, exist_ok=True)

    state = read_json(state_after_compose)
    composed = state.get("composed_content") or {}
    if not composed.get("chapters"):
        logger.warning("[ep%02d] composed_content.chapters 为空，跳过", idx)
        return {"index": idx, "status": "no_chapters"}

    from core.config import settings
    if not settings.SERPER_API_KEY:
        raise SystemExit("SERPER_API_KEY 未配置（.env 或 .env.workbench），无法搜图")

    from core.services.llm_service import get_llm_service
    from core.workflow.nodes.illustration import (
        _phase2_search,
        _phase3_review_searched,
        _format_search_image,
    )
    from multi.prompts import get_workbench_prompts
    from multi.global_k import (
        dynamic_workbench_budget,
        select_global_k,
        _build_full_text_numbered_paragraphs,
    )

    llm = get_llm_service()
    content_type = state.get("content_type", "self_growth")

    chapters = composed.get("chapters") or []
    prompts = get_workbench_prompts("illustration_global_k", content_type)
    if not prompts:
        raise SystemExit(
            f"未找到 workbench/prompts/illustration_global_k/{content_type}.py"
        )

    t0 = time.monotonic()

    try:
        # Phase 1：global_k 一次性从整章识别 ≤ K 个最高价值搜图实体。
        budget = dynamic_workbench_budget(chapters, kind="illustration", cap_override=cap)
        k = min(budget["max"], max(budget["target"], budget["min"]), len(chapters))
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
            response_key="items",
            label="illustration_global_k",
            timeout=120,
            max_tokens=8192,
        )
        # 兼容 phase2/phase3：补全 chapter_title 字段（C 端代码里以此组织 caption）
        search_items: list[dict] = []
        seen_sections: set[int] = set()
        for it in items:
            sec = it.get("section_index")
            if not isinstance(sec, int) or sec < 1 or sec > len(chapters):
                logger.warning("[ep%02d] 插图项跳过非法 section_index=%r", idx, sec)
                continue
            if sec in seen_sections:
                logger.info("[ep%02d] 跳过同小节重复插图候选：§%d", idx, sec)
                continue
            seen_sections.add(sec)
            ch_title = (chapters[sec - 1].get("title") or "").strip()
            search_items.append({
                **it,
                "chapter_title": it.get("chapter_title") or ch_title,
            })
        search_items_raw = list(search_items)
        logger.info(
            "[ep%02d] Phase 1（global_k）完成 · 正文 %d 字 · 目标 %d 张 · 上限 %d · 产出 %d 个实体",
            idx, budget["chars"], budget["target"], budget["max"], len(search_items),
        )
        for it in search_items:
            logger.info(
                "[ep%02d]   · §%s 「%s」 → %s",
                idx, it.get("section_index"), it.get("entity_name", "?"),
                it.get("search_query", ""),
            )

        if not search_items:
            payload = {
                "images": [],
                "total_count": 0,
                "rejected_count": 0,
                "_workbench": {
                    "phase": "no_search_items",
                    "_built_at": datetime.now().isoformat(timespec="seconds"),
                },
            }
            write_json(illustrations_json, payload)
            return {"index": idx, "status": "ok", "approved": 0, "rejected": 0}

        # Phase 2b：Serper 搜图 + 下载多候选
        candidate_groups = await _phase2_search(search_items, illustrations_dir)
        cand_total = sum(len(g) for g in candidate_groups)
        logger.info("[ep%02d] Phase 2b 完成 · 下载 %d 张候选", idx, cand_total)

        # Phase 3b：VLM 审核（逐组取第一张通过）
        approved, rejected = await _phase3_review_searched(llm, candidate_groups, composed)
        logger.info("[ep%02d] Phase 3b 完成 · 通过 %d 张 · 拒绝 %d 张", idx, len(approved), rejected)

    except Exception as e:
        logger.error("[ep%02d] ❌ 搜图失败：%s", idx, e)
        traceback.print_exc()
        write_json(ep_dir / "error_illustrations.json", {
            "stage": "illustration_search",
            "error": str(e),
            "type": type(e).__name__,
            "_failed_at": datetime.now().isoformat(timespec="seconds"),
        })
        return {"index": idx, "status": "error", "error": str(e)}

    elapsed = round(time.monotonic() - t0, 1)

    images = []
    for c in approved:
        item = _format_search_image(c)
        # C 端 formatter only keeps chapter_title. In workbench, section titles
        # can be edited later, so section_index is the stable anchor for render.
        item["section_index"] = c.get("section_index")
        # local_path 改成相对 ep_dir 的路径，方便 markdown 引用
        local_abs = Path(c.get("local_path", ""))
        if local_abs.exists():
            _trim_solid_image_margins(local_abs)
        if local_abs.exists():
            try:
                rel = local_abs.relative_to(ep_dir)
                item["local_path_rel"] = str(rel)
            except ValueError:
                item["local_path_rel"] = local_abs.name
        else:
            item["local_path_rel"] = ""
        images.append(item)

    payload = {
        "images": images,
        "total_count": len(images),
        "rejected_count": rejected,
        "_workbench": {
            "candidate_total": cand_total,
            "search_items_count": len(search_items),
            "search_items_raw_count": len(search_items_raw),
            "per_episode_entity_cap": cap,
            "budget": budget,
            "elapsed_seconds": elapsed,
            "_built_at": datetime.now().isoformat(timespec="seconds"),
        },
    }
    write_json(illustrations_json, payload)
    logger.info(
        "[ep%02d] ✅ 搜图完成 · 候选 %d → 通过 %d 张（拒 %d）· %.0fs",
        idx, cand_total, len(images), rejected, elapsed,
    )

    # 同步重组 chapter_for_book.json（若已存在）
    chapter_for_book = ep_dir / "chapter_for_book.json"
    if chapter_for_book.exists():
        try:
            from multi.assembler import write_chapter_for_book
            write_chapter_for_book(idx, ep_dir)
            logger.info("[ep%02d] 已同步更新 chapter_for_book.json（嵌入插图）", idx)
        except Exception as e:
            logger.warning("[ep%02d] 重组 chapter_for_book.json 失败：%s", idx, e)
    return {
        "index": idx,
        "status": "ok",
        "approved": len(images),
        "rejected": rejected,
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
        result = await _process_one(
            idx=idx, paths=paths, force=args.force, cap=args.cap, refresh=args.refresh,
        )
        results.append(result)

    ok = sum(1 for r in results if r.get("status") == "ok")
    err = sum(1 for r in results if r.get("status") == "error")
    skip = sum(1 for r in results if r.get("status") == "skip")
    logger.info("=" * 50)
    logger.info("完成：成功 %d · 失败 %d · 跳过 %d · 总 %d", ok, err, skip, len(results))
    return 1 if err else 0


def main() -> None:
    p = argparse.ArgumentParser(description="B 端单集插图搜索（只搜不生，整章 1-5 张，常规约 2 张）")
    p.add_argument("--job", required=True)
    p.add_argument("--only", default="", help="例：11 或 1,5,11 或 1-3")
    p.add_argument("--force", action="store_true", help="强制重跑（含 LLM/搜图/VLM），覆盖已有 illustrations.json")
    p.add_argument("--refresh", action="store_true", help="仅基于已有 illustrations.json 重新按 cap 截断（不跑 LLM/搜图/VLM）")
    p.add_argument("--cap", type=int, default=PER_EPISODE_ENTITY_CAP,
                   help=f"整集保留实体数硬上限（默认 {PER_EPISODE_ENTITY_CAP}，绝不超过 5）")
    args = p.parse_args()

    try:
        rc = asyncio.run(main_async(args))
    except KeyboardInterrupt:
        logger.warning("用户中断")
        rc = 130
    sys.exit(rc)


if __name__ == "__main__":
    main()
