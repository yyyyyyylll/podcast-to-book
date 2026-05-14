"""
chapter_for_book 组装器 —— B 端单集"打包入书"的最终产物拼装。

输入（按需读取，全部可选）：
    episodes/epNN/state_after_annotation.json   注释后的章节正文
    episodes/epNN/state_after_compose.json      编排后的章节（无注释，作 fallback）
    episodes/epNN/meta.json                     单集元数据
    episodes/epNN/highlights.json               B 端金句产物
    episodes/epNN/illustrations.json            B 端插图产物
    episodes/epNN/chapter_title_candidates.json 章名候选（如有）

输出：
    episodes/epNN/chapter_for_book.json          B 端单章交付物

设计：
- 这是**纯组装**逻辑，不调任何 LLM、不发任何网络请求。
- run_pipeline 跑完 annotation 后调它做最终打包；
  extract_highlights / search_illustrations 完成后再调一次，把 highlights/illustrations 注入。
"""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any

from multi.io import read_json, write_json


def _strip_episode_prefix(title: str) -> str:
    """去掉单集标题中的"序号. "前缀。"""
    return re.sub(r"^\s*\d+[\.\、\:\：]\s*", "", title or "").strip()


def assemble_chapter(
    *,
    idx: int,
    ep_dir: Path,
    meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    从 ep_dir 下的产物组装出 chapter_for_book dict（不写盘）。

    优先级：annotated_content > composed_content > meta-only fallback
    旁路注入：highlights.json + illustrations.json（若存在）
    """
    if meta is None:
        meta_path = ep_dir / "meta.json"
        if not meta_path.exists():
            raise FileNotFoundError(f"缺少 meta.json：{ep_dir}")
        meta = read_json(meta_path)

    # 选择正文源：优先 annotated，其次 composed
    state_after_annotation = ep_dir / "state_after_annotation.json"
    state_after_compose = ep_dir / "state_after_compose.json"

    annotated = {}
    composed = {}
    if state_after_annotation.exists():
        s = read_json(state_after_annotation)
        annotated = s.get("annotated_content") or {}
        composed = s.get("composed_content") or composed
    if state_after_compose.exists():
        s = read_json(state_after_compose)
        composed = s.get("composed_content") or composed

    src = annotated if annotated.get("chapters") else composed

    raw_sections = src.get("chapters") or []
    sections: list[dict[str, Any]] = []
    aggregated_footnotes: list[dict[str, Any]] = []
    for i, ch in enumerate(raw_sections, start=1):
        section_footnotes = list(ch.get("footnotes") or [])
        sections.append({
            "section_index": i,
            "section_title": ch.get("title", "").strip() or f"第 {i} 节",
            "content": ch.get("content", ""),
            "key_points": list(ch.get("key_points") or []),
            "section_type": ch.get("section_type", ""),
            "time_range": ch.get("time_range", []),
            "source_segment_ids": list(ch.get("source_segment_ids") or []),
            "footnotes": section_footnotes,
        })
        for fn in section_footnotes:
            aggregated_footnotes.append({**fn, "section_index": i})

    footnotes_total = annotated.get("total_footnotes", 0) if annotated else 0

    raw_episode_title = meta.get("title", "")
    fallback_title = _strip_episode_prefix(raw_episode_title)
    composed_title = (composed.get("title") or "").strip()

    # 章名优先级：
    #   1. 已有 chapter_for_book.json 中由 name_chapter 写入的 title（chapter_title_source 以 "name_chapter" 开头）
    #   2. composed_content.title（compose Phase 3 LLM 生成）
    #   3. episode title 剥序号前缀
    existing_book_path = ep_dir / "chapter_for_book.json"
    name_chapter_title = ""
    name_chapter_source = ""
    if existing_book_path.exists():
        try:
            existing = read_json(existing_book_path)
            src_tag = (existing.get("chapter_title_source") or "")
            if src_tag.startswith("name_chapter"):
                name_chapter_title = (existing.get("chapter_title") or "").strip()
                name_chapter_source = src_tag
        except Exception:
            pass

    if name_chapter_title:
        final_title = name_chapter_title
        title_source = name_chapter_source
    elif composed_title:
        final_title = composed_title
        title_source = "composed_content.title"
    else:
        final_title = fallback_title
        title_source = "episode_title_stripped"

    # 旁路加载：金句 / 插图
    highlights_payload: list[dict] = []
    illustrations_payload: list[dict] = []
    side_meta: dict[str, Any] = {}

    h_path = ep_dir / "highlights.json"
    if h_path.exists():
        hl = read_json(h_path)
        highlights_payload = list(hl.get("quotes") or [])
        side_meta["highlights"] = {
            "count": len(highlights_payload),
            "raw_count": (hl.get("_workbench") or {}).get("raw_count"),
            "per_chapter_cap": (hl.get("_workbench") or {}).get("per_chapter_cap"),
        }

    i_path = ep_dir / "illustrations.json"
    if i_path.exists():
        il = read_json(i_path)
        illustrations_payload = list(il.get("images") or [])
        side_meta["illustrations"] = {
            "count": len(illustrations_payload),
            "rejected": il.get("rejected_count", 0),
        }

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
        "footnotes": aggregated_footnotes,
        "highlights": highlights_payload,
        "illustrations": illustrations_payload,
        "_pipeline": {
            "source": "annotated_content" if src is annotated and annotated else "composed_content",
            "annotated": bool(annotated.get("chapters")),
            "side_meta": side_meta,
            "_built_at": datetime.now().isoformat(timespec="seconds"),
        },
    }


def write_chapter_for_book(idx: int, ep_dir: Path) -> dict[str, Any]:
    """组装并写入 chapter_for_book.json，返回组装后的 dict。"""
    chapter = assemble_chapter(idx=idx, ep_dir=ep_dir)
    write_json(ep_dir / "chapter_for_book.json", chapter)
    return chapter
