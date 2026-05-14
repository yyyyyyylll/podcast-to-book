"""
name_book — 为 workbench 多集合书生成整书书名。

输入：
    storage/workbench/jobs/<job>/show.json
    storage/workbench/jobs/<job>/episodes/epNN/chapter_for_book.json
    storage/workbench/jobs/<job>/merged/book_front_matter.json（可选，用于参考序言）

输出：
    storage/workbench/jobs/<job>/merged/book_title_candidates.json
    默认写回推荐候选到 merged/book_front_matter.json 的 book_title；
    若加 --apply-pick 则写回指定候选；若加 --no-apply 则只生成候选不写回。

用法：
    cd backend
    python -m workbench.runners.name_book --job possibility --premium
    python -m workbench.runners.name_book --job possibility --premium --apply-pick 1
    python -m workbench.runners.name_book --job possibility --premium --no-apply
    python -m workbench.runners.name_book --job possibility --title "在褶皱里重建生活"
"""
from __future__ import annotations

from multi.env import setup
setup()

import argparse
import asyncio
import logging
import sys
import time
from datetime import datetime
from typing import Any

from multi.io import JobPaths, read_json, write_json

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("name_book")

DEFAULT_CANDIDATES_NAME = "book_title_candidates.json"
FRONT_MATTER_NAME = "book_front_matter.json"


def _compact(text: str, limit: int) -> str:
    text = " ".join(str(text or "").split())
    return text[:limit]


def _chapter_context(chapter: dict[str, Any]) -> dict[str, Any]:
    sections = chapter.get("sections") or []
    section_titles = [
        (s.get("section_title") or s.get("title") or "").strip()
        for s in sections
        if (s.get("section_title") or s.get("title") or "").strip()
    ]
    return {
        "index": int(chapter.get("chapter_index") or 0),
        "chapter_title": (chapter.get("chapter_title") or "").strip(),
        "raw_title": (chapter.get("chapter_title_raw") or "").strip(),
        "core_theme": (chapter.get("core_theme") or "").strip(),
        "keywords": list(chapter.get("theme_keywords") or [])[:8],
        "section_titles": section_titles[:8],
    }


def collect_book_inputs(paths: JobPaths) -> dict[str, Any]:
    show = read_json(paths.show_json) if paths.show_json.exists() else {}
    chapters: list[dict[str, Any]] = []
    if paths.episodes_index_json.exists():
        index = read_json(paths.episodes_index_json)
        for ep in index.get("episodes", []):
            idx = int(ep.get("index"))
            chapter_path = paths.episode_dir(idx) / "chapter_for_book.json"
            if chapter_path.exists():
                chapters.append(_chapter_context(read_json(chapter_path)))

    front_matter_path = paths.merged_dir / FRONT_MATTER_NAME
    front_matter = read_json(front_matter_path) if front_matter_path.exists() else {}
    return {"show": show, "chapters": chapters, "front_matter": front_matter}


def _build_prompt(inputs: dict[str, Any], n: int) -> str:
    show = inputs.get("show") or {}
    front_matter = inputs.get("front_matter") or {}
    chapters = inputs.get("chapters") or []
    chapter_lines = []
    for ch in chapters:
        sections = "、".join(ch.get("section_titles") or [])[:180]
        keywords = "、".join(ch.get("keywords") or [])
        chapter_lines.append(
            f"第{ch.get('index'):02d}章：{ch.get('chapter_title')}\n"
            f"原始标题：{ch.get('raw_title')}\n"
            f"主题/关键词：{ch.get('core_theme')}；{keywords}\n"
            f"小节：{sections}"
        )

    preface = "\n".join(front_matter.get("preface_paragraphs") or [])
    return f"""
你是一位资深中文图书编辑，要为一本由播客多期内容整理成的非虚构合集命名。

栏目源信息：
- 栏目名：{show.get('title') or ''}
- 栏目简介：{show.get('brief') or ''}
- 栏目描述：{_compact(show.get('description_clean') or show.get('description') or '', 700)}
- 主播/作者：{'、'.join(show.get('podcaster_names') or [])}

已有序言摘要：
{_compact(preface, 900)}

章节结构：
{chr(10).join(chapter_lines)}

命名目标：
1. 书名必须是“这本合集自己的名字”，不能只是照抄栏目名。
2. 但要能自然承接栏目气质：褶皱、可能性、读书、向内探索、清醒、温柔而有力量。
3. 避免课程名、营销感、自媒体爆款感、方法论工具书感。
4. 不要使用“指南”“心法”“攻略”“法则”“训练营”“从 A 到 B”。
5. 候选之间要明显不同：有的可偏文学，有的偏非虚构，有的偏清醒克制。
6. 中文书名 4-12 个汉字最佳，最多 14 个汉字；可有副标题，但主标题必须单独成立。
7. 不要英文，不要编号，不要照搬单章标题。

严格返回 JSON：
{{
  "candidates": [
    {{
      "book_title": "书名",
      "subtitle": "可为空",
      "char_count": 实际汉字数,
      "rationale": "40 字内说明命名思路"
    }}
  ],
  "recommended_pick": 1,
  "editor_note": "80 字内说明整体判断"
}}

请给出 {n} 个候选。
""".strip()


async def _call_llm(prompt: str, *, premium: bool) -> dict[str, Any]:
    from core.config import settings
    from core.services.llm_service import get_llm_service

    llm = get_llm_service()
    model = settings.LLM_PREMIUM_MODEL if premium else settings.LLM_MODEL
    return await llm.generate_json(
        prompt=prompt,
        system_prompt="你是一位资深中文图书编辑，擅长给非虚构合集起克制、准确、有书卷气的书名。",
        model=model,
        temperature=0.65,
        max_tokens=4096,
        thinking_budget=0,
        timeout=240,
        label="name_book",
    )


def normalize_candidates(result: dict[str, Any]) -> list[dict[str, Any]]:
    raw = result.get("candidates") or []
    candidates: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        title = (item.get("book_title") or item.get("title") or "").strip()
        if not title:
            continue
        candidates.append({
            "book_title": title,
            "subtitle": (item.get("subtitle") or "").strip(),
            "char_count": item.get("char_count"),
            "rationale": (item.get("rationale") or "").strip(),
        })
    return candidates


async def generate_book_title_candidates(
    *,
    paths: JobPaths,
    n: int,
    premium: bool,
) -> dict[str, Any]:
    inputs = collect_book_inputs(paths)
    prompt = _build_prompt(inputs, n=n)
    started = time.monotonic()
    result = await _call_llm(prompt, premium=premium)
    candidates = normalize_candidates(result)
    out = {
        "current_show_title": (inputs.get("show") or {}).get("title", ""),
        "candidates": candidates,
        "recommended_pick": result.get("recommended_pick") or 1,
        "editor_note": (result.get("editor_note") or "").strip(),
        "_workbench": {
            "model_tier": "premium" if premium else "standard",
            "chapter_count": len(inputs.get("chapters") or []),
            "elapsed_seconds": round(time.monotonic() - started, 1),
            "built_at": datetime.now().isoformat(timespec="seconds"),
        },
    }
    write_json(paths.merged_dir / DEFAULT_CANDIDATES_NAME, out)
    return out


def apply_book_title(
    *,
    paths: JobPaths,
    title: str,
    source: str,
    rationale: str = "",
    subtitle: str = "",
) -> dict[str, Any]:
    title = (title or "").strip()
    if not title:
        raise ValueError("book title 不能为空")

    front_matter_path = paths.merged_dir / FRONT_MATTER_NAME
    front_matter = read_json(front_matter_path) if front_matter_path.exists() else {}
    front_matter["book_title"] = title
    if subtitle.strip():
        front_matter["book_subtitle"] = subtitle.strip()
    front_matter["_workbench"] = front_matter.get("_workbench") or {}
    front_matter["_workbench"].update({
        "book_title_source": source,
        "book_title_rationale": rationale,
        "book_title_applied_at": datetime.now().isoformat(timespec="seconds"),
    })
    write_json(front_matter_path, front_matter)
    return front_matter


def _candidate_by_pick(payload: dict[str, Any], pick: int) -> dict[str, Any]:
    candidates = payload.get("candidates") or []
    if not (1 <= pick <= len(candidates)):
        raise ValueError(f"--apply-pick {pick} 越界（候选数 {len(candidates)}）")
    return candidates[pick - 1]


def resolve_apply_pick(payload: dict[str, Any], *, explicit_pick: int, no_apply: bool) -> int:
    if no_apply:
        return 0
    if explicit_pick:
        return explicit_pick
    try:
        pick = int(payload.get("recommended_pick") or 1)
    except (TypeError, ValueError):
        pick = 1
    candidates = payload.get("candidates") or []
    if not candidates:
        return 0
    if not (1 <= pick <= len(candidates)):
        return 1
    return pick


async def main_async(args: argparse.Namespace) -> int:
    paths = JobPaths.open(args.job)
    paths.merged_dir.mkdir(parents=True, exist_ok=True)

    if args.title:
        updated = apply_book_title(
            paths=paths,
            title=args.title,
            subtitle=args.subtitle,
            source="name_book:manual",
            rationale=args.rationale,
        )
        logger.info("✅ 已手动写回整书书名：《%s》", updated["book_title"])
        return 0

    candidates_path = paths.merged_dir / DEFAULT_CANDIDATES_NAME
    if candidates_path.exists() and not args.force:
        payload = read_json(candidates_path)
        logger.info("复用已有候选：%s（用 --force 重跑 LLM）", candidates_path)
    else:
        logger.info("生成整书书名候选：job=%s · n=%d · premium=%s", args.job, args.n, args.premium)
        payload = await generate_book_title_candidates(
            paths=paths,
            n=args.n,
            premium=args.premium,
        )
        logger.info("候选已写入：%s", candidates_path)

    for i, cand in enumerate(payload.get("candidates") or [], start=1):
        subtitle = f"：{cand['subtitle']}" if cand.get("subtitle") else ""
        logger.info(
            "#%d 《%s%s》 · %s",
            i,
            cand.get("book_title", ""),
            subtitle,
            cand.get("rationale", ""),
        )

    apply_pick = resolve_apply_pick(
        payload,
        explicit_pick=args.apply_pick,
        no_apply=args.no_apply,
    )
    if apply_pick:
        cand = _candidate_by_pick(payload, apply_pick)
        updated = apply_book_title(
            paths=paths,
            title=cand["book_title"],
            subtitle=cand.get("subtitle", ""),
            source=f"name_book#{apply_pick}",
            rationale=cand.get("rationale", ""),
        )
        logger.info("✅ 已应用候选 #%d → 整书书名《%s》", apply_pick, updated["book_title"])

    return 0


def main() -> None:
    p = argparse.ArgumentParser(description="为 workbench 多集合书生成整书书名")
    p.add_argument("--job", required=True)
    p.add_argument("--n", type=int, default=8, help="候选数量，默认 8")
    p.add_argument("--premium", action="store_true", help="使用 premium 模型")
    p.add_argument("--force", action="store_true", help="忽略已有候选，重跑 LLM")
    p.add_argument(
        "--apply-pick",
        type=int,
        default=0,
        help="应用第几个候选到 book_front_matter.json；默认应用 recommended_pick",
    )
    p.add_argument("--no-apply", action="store_true", help="只生成候选，不写回默认书名")
    p.add_argument("--title", default="", help="手动指定书名并写回，跳过 LLM")
    p.add_argument("--subtitle", default="", help="--title 手动写回时可带副标题")
    p.add_argument("--rationale", default="", help="--title 手动写回时记录命名说明")
    args = p.parse_args()

    try:
        rc = asyncio.run(main_async(args))
    except KeyboardInterrupt:
        logger.warning("用户中断")
        rc = 130
    sys.exit(rc)


if __name__ == "__main__":
    main()
