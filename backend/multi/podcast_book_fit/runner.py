"""
podcast_book_fit.runner — 输入播客链接，生成主播成书初判

用法：
    cd backend
    python -m workbench.podcast_book_fit.runner \
        --url https://www.xiaoyuzhoufm.com/podcast/<pid> \
        --job xyxxxx__codex__bookfit__2026-04-30 --premium

输出：
    storage/workbench/jobs/<job_id>/
      book_fit_input.json
      book_fit_judgment.json
      book_fit_judgment.html
      error.json（失败时）
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

from multi.io import JobPaths, slugify, write_json
from multi.podcast_book_fit.input_source import build_input_from_url
from multi.podcast_book_fit.prompt import PROMPTS
from multi.podcast_book_fit.render import render_book_fit_html, render_book_fit_pdf
from multi.podcast_direction.topics import extract_host_topic_references

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("podcast_book_fit")


FIT_LEVELS = {"值得继续沟通", "非常值得继续沟通"}


def _clip_text(text: str, max_chars: int) -> str:
    text = (text or "").strip()
    if len(text) <= max_chars:
        return text
    return text[: max(max_chars - 1, 1)].rstrip("，。；、 ") + "…"


def _normalize_public_copy(text: str, *, episode_count: int | None = None) -> str:
    text = (text or "").strip()
    if episode_count:
        text = text.replace(f"{episode_count}集资料", f"{episode_count} 期播客")
        text = text.replace(f"{episode_count} 集资料", f"{episode_count} 期播客")
        text = text.replace(f"{episode_count}集", f"{episode_count} 期播客")
        text = text.replace(f"{episode_count} 集", f"{episode_count} 期播客")
    return text


def _clean_display_title(value: str, fallback: str) -> str:
    raw = (value or fallback or "").strip()
    raw = raw.strip("《》\"'“”")
    for mark in ("：", ":"):
        if mark in raw:
            raw = raw.split(mark, 1)[0].strip()
    raw = raw.strip("《》\"'“”")
    if not raw:
        raw = "可先整理的章节"
    if not raw.startswith("《"):
        raw = f"《{raw}》"
    return raw


def _episode_titles_by_referenced_numbers(
    text: str,
    episode_titles: list[str],
) -> list[str]:
    selected: list[str] = []
    seen: set[int] = set()
    prefix_map: dict[int, str] = {}
    for position, title in enumerate(episode_titles, 1):
        prefix = re.match(r"\s*(?:EP|Ep|ep)?\s*([0-9０-９]{1,3})\s*[.．、:：-]", title)
        if prefix:
            try:
                prefix_idx = int(prefix.group(1).translate(str.maketrans("０１２３４５６７８９", "0123456789")))
                prefix_map[prefix_idx] = title
            except ValueError:
                pass
        prefix_map.setdefault(position, title)
    for match in re.finditer(r"第\s*([0-9０-９]{1,3}(?:\s*[、,，]\s*[0-9０-９]{1,3})*)\s*[集期]", text or ""):
        for raw in re.split(r"[、,，]\s*", match.group(1)):
            if not raw:
                continue
            try:
                idx = int(raw.translate(str.maketrans("０１２３４５６７８９", "0123456789")))
            except ValueError:
                continue
            if idx in prefix_map and idx not in seen:
                selected.append(prefix_map[idx])
                seen.add(idx)
    return selected


def _normalize_topic_sections(
    sections: list[dict[str, Any]] | None,
    episode_titles: list[str],
) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for item in list(sections or [])[:3]:
        section = dict(item or {})
        titles = [
            str(title).strip()
            for title in list(section.get("episode_titles") or [])[:4]
            if str(title).strip()
        ]
        if not titles:
            continue
        normalized.append(
            {
                "section_title": (section.get("section_title") or "可先收录的节目").strip(),
                "section_reason": (section.get("section_reason") or "").strip(),
                "episode_titles": titles,
            }
        )

    if normalized:
        return normalized[:3]

    titles = [title for title in episode_titles if title][:12]
    if not titles:
        return []
    if len(titles) < 6:
        return [
            {
                "section_title": "可先收录的节目",
                "section_reason": "这些节目可以作为主题样张的第一批内容依据。",
                "episode_titles": titles[:4],
            }
        ]

    chunks = [titles[0:4], titles[4:8], titles[8:12]]
    fallback_names = ["主题入口", "核心讨论", "延展材料"]
    return [
        {
            "section_title": fallback_names[idx],
            "section_reason": "这一组节目可以合并整理成一个相对完整的阅读板块。",
            "episode_titles": chunk[:4],
        }
        for idx, chunk in enumerate(chunks)
        if len(chunk) >= 2
    ][:3]


def _normalize_sample_episode(
    value: dict[str, Any] | None,
    directions: list[dict[str, Any]],
    fallback_episode_titles: list[str],
) -> dict[str, str]:
    reason = "建议先用这一期做样章，方便快速看到单集改写后的成稿质感。"

    all_titles: list[tuple[str, str]] = []
    for item in directions:
        item_direction = str(item.get("direction") or "").strip()
        for section in item.get("topic_sections") or []:
            for episode_title in section.get("episode_titles") or []:
                clean_title = str(episode_title or "").strip()
                if clean_title:
                    all_titles.append((clean_title, item_direction))
        for episode_title in item.get("episode_titles") or []:
            clean_title = str(episode_title or "").strip()
            if clean_title:
                all_titles.append((clean_title, item_direction))

    title = ""
    direction = ""
    if all_titles:
        title, direction = all_titles[0]

    if not title and fallback_episode_titles:
        title = fallback_episode_titles[0]
    if not direction and directions:
        direction = str(directions[0].get("direction") or "").strip()

    return {
        "episode_title": title,
        "source_direction": direction,
        "why_this_episode": reason,
    }


def normalize_book_fit_result(
    result: dict[str, Any],
    *,
    podcast_name: str,
    metadata: dict[str, Any],
    episode_list: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    out = dict(result or {})
    out["podcast_name"] = (out.get("podcast_name") or podcast_name or "").strip()
    if out.get("fit_level") not in FIT_LEVELS:
        out["fit_level"] = "值得继续沟通"
    out["host_profile_tags"] = list(out.get("host_profile_tags") or [])[:5]
    out["existing_series_clues"] = list(out.get("existing_series_clues") or [])[:6]
    episode_count = metadata.get("episode_count") or metadata.get("rss_episode_count")
    book_unit = "chapter" if isinstance(episode_count, int) and episode_count <= 20 else "book"
    fallback_episode_titles = [
        (ep.get("title") or "").strip()
        for ep in (episode_list or [])
        if (ep.get("title") or "").strip()
    ][:15]
    directions = []
    for item in list(out.get("possible_book_directions") or [])[:3]:
        direction = dict(item or {})
        direction["suggested_title"] = _clean_display_title(
            direction.get("suggested_title") or "",
            direction.get("direction") or "",
        )
        direction["episode_titles"] = list(direction.get("episode_titles") or [])[:15]
        if not direction["episode_titles"] and fallback_episode_titles:
            direction["episode_titles"] = _episode_titles_by_referenced_numbers(
                " ".join(
                    [
                        str(direction.get("direction") or ""),
                        str(direction.get("reason") or ""),
                        str(direction.get("evidence_title") or ""),
                    ]
                ),
                fallback_episode_titles,
            )
        if not direction["episode_titles"] and fallback_episode_titles:
            direction["episode_titles"] = fallback_episode_titles
        direction["evidence_mode"] = (
            direction.get("evidence_mode")
            if direction.get("evidence_mode") in {"from_series", "selected_episodes"}
            else "selected_episodes"
        )
        if not direction.get("evidence_title"):
            if direction["episode_titles"]:
                direction["evidence_title"] = f"可先参考这 {len(direction['episode_titles'])} 期节目"
            else:
                direction["evidence_title"] = "可先参考的节目内容"
        direction["topic_sections"] = _normalize_topic_sections(
            list(direction.get("topic_sections") or []),
            direction["episode_titles"],
        )
        directions.append(direction)
    out["possible_book_directions"] = directions
    out["recommended_sample_episode"] = _normalize_sample_episode(
        out.get("recommended_sample_episode") if isinstance(out.get("recommended_sample_episode"), dict) else None,
        directions,
        fallback_episode_titles,
    )
    out.pop("recommended_sample_line", None)
    out.pop("client_next_step", None)
    out.pop("outreach_angle", None)
    out["book_fit_summary"] = _normalize_public_copy(
        out.get("book_fit_summary") or "",
        episode_count=episode_count if isinstance(episode_count, int) else None,
    )
    out["metadata"] = {
        **metadata,
        "book_unit": book_unit,
        "summary_chars": len(out["book_fit_summary"]),
        "fit_level_normalized": out["fit_level"],
    }
    return out


async def call_book_fit_llm(
    *,
    input_payload: dict[str, Any],
    host_topic_references: list[dict[str, Any]],
    premium: bool,
) -> tuple[dict[str, Any], dict[str, Any], str]:
    from core.config import settings
    from core.services.llm_service import get_llm_service

    llm = get_llm_service()
    model = settings.LLM_PREMIUM_MODEL if premium else settings.LLM_MODEL
    prompt = PROMPTS["build_user"](
        podcast_name=input_payload["podcast_name"],
        podcast_intro=input_payload["podcast_intro"],
        episode_list=input_payload["episode_list"],
        host_topic_references=host_topic_references,
    )
    started = time.monotonic()
    result = await llm.generate_json(
        prompt=prompt,
        system_prompt=PROMPTS["system"],
        model=model,
        temperature=0.35,
        label="podcast_book_fit_judgment",
    )
    stats = {
        "llm_model": model,
        "llm_premium": premium,
        "llm_elapsed_seconds": round(time.monotonic() - started, 1),
    }
    return result, stats, prompt


async def run_book_fit(
    *,
    url: str,
    paths: JobPaths,
    premium: bool,
) -> dict[str, Any]:
    logger.info("读取播客资料：%s", url)
    input_payload, source_metadata = await build_input_from_url(url)
    host_topics = extract_host_topic_references(input_payload.get("episode_list") or [])
    source_metadata["host_topic_count"] = len(host_topics)
    source_metadata["episode_count"] = len(input_payload.get("episode_list") or [])
    input_payload["host_topic_references"] = host_topics
    write_json(paths.root / "book_fit_input.json", input_payload)
    logger.info(
        "资料完成：%s · %d 集 · 专题 %d 个",
        input_payload.get("podcast_name"),
        source_metadata["episode_count"],
        len(host_topics),
    )

    raw_result, llm_stats, prompt = await call_book_fit_llm(
        input_payload=input_payload,
        host_topic_references=host_topics,
        premium=premium,
    )
    metadata = {
        **source_metadata,
        **llm_stats,
        "source_url": url,
        "built_at": datetime.now().isoformat(timespec="seconds"),
    }
    result = normalize_book_fit_result(
        raw_result if isinstance(raw_result, dict) else {},
        podcast_name=input_payload.get("podcast_name", ""),
        metadata=metadata,
        episode_list=input_payload.get("episode_list") or [],
    )
    result["host_topic_references"] = host_topics
    write_json(paths.root / "book_fit_prompt.json", {"prompt": prompt})
    write_json(paths.root / "book_fit_judgment.json", result)
    html_path = paths.root / "book_fit_judgment.html"
    pdf_path = paths.root / "book_fit_judgment.pdf"
    html_path.write_text(
        render_book_fit_html(result),
        encoding="utf-8",
    )
    try:
        if await render_book_fit_pdf(html_path, pdf_path):
            result.setdefault("metadata", {})["pdf_path"] = str(pdf_path)
            write_json(paths.root / "book_fit_judgment.json", result)
    except Exception as exc:
        logger.warning("PDF 生成失败，可先使用 HTML 页面里的浏览器导出：%s", exc)
    logger.info("落盘：%s", html_path)
    return result


async def main_async(args: argparse.Namespace) -> int:
    hint = slugify(args.url.rsplit("/", 1)[-1] or "podcast", max_len=18)
    paths = JobPaths.create(job_id=args.job, hint=f"{hint}__codex__bookfit")
    try:
        result = await run_book_fit(url=args.url, paths=paths, premium=args.premium)
        print("=" * 60)
        print(f"播客成书初判：{result.get('podcast_name')}")
        print(f"判断：{result.get('fit_level')}")
        print(f"页面：{paths.root / 'book_fit_judgment.html'}")
        print("=" * 60)
        return 0
    except Exception as exc:
        logger.error("失败：%s", exc)
        traceback.print_exc()
        write_json(
            paths.root / "error.json",
            {
                "runner": "podcast_book_fit.runner",
                "url": args.url,
                "error": str(exc),
                "type": type(exc).__name__,
                "timestamp": datetime.now().isoformat(timespec="seconds"),
            },
        )
        return 1


def main() -> None:
    parser = argparse.ArgumentParser(description="播客成书初判 runner")
    parser.add_argument("--url", required=True, help="小宇宙播客栏目链接")
    parser.add_argument("--job", default="", help="job id，不填则自动生成")
    parser.add_argument("--premium", action="store_true", help="使用 premium LLM")
    args = parser.parse_args()
    try:
        rc = asyncio.run(main_async(args))
    except KeyboardInterrupt:
        rc = 130
    sys.exit(rc)


if __name__ == "__main__":
    main()
