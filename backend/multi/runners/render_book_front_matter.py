"""
render_book_front_matter — 为 workbench 多集合书生成整书级序言和目录。

这是 B 端合书层工作流的一部分，不影响 C 端 typeset。

输入：
    storage/workbench/jobs/<job>/show.json
    storage/workbench/jobs/<job>/episodes/epNN/chapter_for_book.json
    merge_book_pdf 传入的章节页码范围

输出：
    storage/workbench/jobs/<job>/merged/book_front_matter.json
    storage/workbench/jobs/<job>/merged/book_front_matter.typ
    storage/workbench/jobs/<job>/merged/book_front_matter.pdf
"""
from __future__ import annotations

from multi.env import setup
setup()

import argparse
import asyncio
import logging
import re
from pathlib import Path
from typing import Any

import fitz
import typst as typst_lib

from core.config import settings
from core.services.llm_service import get_llm_service
from core.workflow.nodes.typeset import FONTS_DIR, _escape_typst, _escape_typst_string
from multi.io import JobPaths, read_json, write_json

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("render_book_front_matter")

DEFAULT_JSON_NAME = "book_front_matter.json"
DEFAULT_TYP_NAME = "book_front_matter.typ"
DEFAULT_PDF_NAME = "book_front_matter.pdf"


FRONT_MATTER_PREAMBLE = r"""
// PodBook 工作台 — 整书前置页（序言 + 目录）

#let clr-body = rgb("#2D2D2D")
#let clr-light = rgb("#999999")
#let clr-accent = rgb("#6B3A2A")

#let book-title = "BOOK_TITLE_PLACEHOLDER"
#let book-subtitle = "BOOK_SUBTITLE_PLACEHOLDER"
#let podcast-name = "PODCAST_NAME_PLACEHOLDER"
#let book-author = "AUTHOR_PLACEHOLDER"
#let book-series = "SERIES_PLACEHOLDER"

#let trim-width = 148mm
#let trim-height = 210mm
#let bleed = 3mm
#let print-margin-top = bleed + 26mm
#let print-margin-bottom = bleed + 28mm
#let print-margin-outside = bleed + 20mm
#let print-margin-inside = bleed + 23mm

#set page(
  width: 154mm,
  height: 216mm,
  margin: (
    inside: print-margin-inside,
    outside: print-margin-outside,
    top: print-margin-top,
    bottom: print-margin-bottom,
  ),
  numbering: "i",
  header: none,
  footer-descent: 10mm,
  footer: context {
    let phys = here().page()
    if phys <= 2 { return }  // 扉页和扉页背面：算入页码但不印刷
    set text(fill: black)
    show linebreak: none
    let pagenum = text(
      font: ("EB Garamond", "Palatino", "Baskerville", "Times New Roman", "Noto Serif CJK SC", "Noto Serif SC"),
      size: 10pt,
      style: "italic",
    )[#counter(page).display()]
    let title-text = text(font: ("Noto Sans CJK SC", "Noto Sans SC"), size: 8.5pt)[#podcast-name]
    let sep = text(font: ("Noto Sans CJK SC", "Noto Sans SC"), size: 8.5pt)[#h(0.6em)|#h(0.6em)]
    let hang = 5mm
    if calc.even(phys) {
      h(-hang)
      [#pagenum#sep#title-text]
      h(1fr)
    } else {
      h(1fr)
      [#title-text#sep#pagenum]
      h(-hang)
    }
  },
)

#set text(
  font: ("Noto Serif CJK SC", "Noto Serif SC"),
  size: 10.5pt,
  lang: "zh",
  region: "cn",
  fill: clr-body,
)
#set par(leading: 1.45em, spacing: 1.6em, first-line-indent: 2em, justify: true)

#let front-title(t) = {
  set par(first-line-indent: 0em)
  align(center)[
    #text(
      font: ("Source Han Sans SC", "Hiragino Sans GB", "PingFang SC", "Noto Sans CJK SC", "Noto Sans SC"),
      size: 16pt,
      weight: "extrabold",
      fill: black,
      tracking: 0.18em,
    )[#t]
  ]
}

#let title-page() = {
  set par(first-line-indent: 0em)
  v(0.85fr)
  align(center)[
    #text(
      font: ("Source Han Serif SC", "Songti SC", "Noto Serif CJK SC", "Noto Serif SC"),
      size: 31pt,
      weight: "semibold",
      tracking: 0.1em,
      fill: black,
    )[#book-title]
    #if book-subtitle != "" [
      #v(1.1em)
      #text(
        font: ("Source Han Serif SC", "Songti SC", "Noto Serif CJK SC", "Noto Serif SC"),
        size: 14pt,
        weight: "regular",
        fill: clr-body,
        tracking: 0.04em,
      )[#book-subtitle]
    ]
    #v(2.8em)
    #text(
      font: ("Source Han Serif SC", "Songti SC", "Noto Serif CJK SC", "Noto Serif SC"),
      size: 13pt,
      weight: "regular",
      fill: clr-body,
    )[#book-author]
  ]
  v(0.9fr)
  align(center)[
    #line(length: 38mm, stroke: 0.45pt + clr-light)
    #v(0.8em)
    #text(
      font: ("Source Han Sans SC", "Hiragino Sans GB", "PingFang SC", "Noto Sans CJK SC", "Noto Sans SC"),
      size: 10pt,
      fill: clr-light,
      tracking: 0.06em,
    )[#book-series]
  ]
  v(1.8cm)
}

#let toc-title() = {
  set par(first-line-indent: 0em)
  align(right)[
    #text(
      font: ("Source Han Sans SC", "Hiragino Sans GB", "PingFang SC", "Noto Sans CJK SC", "Noto Sans SC"),
      size: 16pt,
      weight: "extrabold",
      tracking: 0.18em,
      fill: black,
    )[目 录]
  ]
}

#let toc-block(body) = {
  set par(first-line-indent: 0em, leading: 1.4em, spacing: 0.86em)
  block(width: 90%, inset: (left: 8mm))[#body]
}

#let toc-chapter(num, title) = {
  block(width: 100%, above: 3.6em, below: 1.8em)[
    #grid(
      columns: (5.2em, 1fr),
      column-gutter: 0.25em,
      align: top,
    )[
      #text(
        font: ("Source Han Serif SC", "Songti SC", "Noto Serif CJK SC", "Noto Serif SC"),
        size: 12pt,
        weight: "semibold",
        fill: black,
      )[#num]
    ][
      #text(
        font: ("Source Han Serif SC", "Songti SC", "Noto Serif CJK SC", "Noto Serif SC"),
        size: 12pt,
        weight: "semibold",
        fill: black,
      )[#title]
    ]
  ]
}

#let toc-section(title, page) = {
  block(width: 100%, below: 1.25em)[
    #grid(
      columns: (5.2em, 1fr),
      column-gutter: 0.25em,
      align: bottom,
    )[
      #none
    ][
      #text(
        font: ("Source Han Serif SC", "Songti SC", "Noto Serif CJK SC", "Noto Serif SC"),
        size: 10.5pt,
        weight: "regular",
        fill: black,
      )[#title]
      #h(0.8em)
      #text(
        font: ("EB Garamond", "Times New Roman", "Noto Serif CJK SC", "Noto Serif SC"),
        size: 10.5pt,
        fill: black,
      )[#page]
    ]
  ]
}
"""


def _chapter_context(chapter: dict[str, Any]) -> dict[str, Any]:
    sections = chapter.get("sections") or []
    section_titles = [
        (s.get("section_title") or s.get("title") or "").strip()
        for s in sections
        if (s.get("section_title") or s.get("title") or "").strip()
    ]
    excerpts: list[str] = []
    for s in sections[:3]:
        text = (s.get("content") or "").strip().replace("\n", " ")
        if text:
            excerpts.append(text[:260])
    return {
        "index": int(chapter.get("chapter_index") or 0),
        "chapter_title": (chapter.get("chapter_title") or "").strip(),
        "section_titles": section_titles,
        "char_count": sum(len(s.get("content") or "") for s in sections),
        "excerpt": "\n".join(excerpts),
    }


def collect_book_inputs(paths: JobPaths, chapters: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    show = read_json(paths.show_json) if paths.show_json.exists() else {}
    if chapters is None:
        index = read_json(paths.episodes_index_json)
        chapters = []
        for ep in index.get("episodes", []):
            idx = int(ep.get("index"))
            chapter_path = paths.episode_dir(idx) / "chapter_for_book.json"
            if chapter_path.exists():
                chapters.append(read_json(chapter_path))
    return {
        "show": show,
        "chapters": [_chapter_context(c) for c in chapters],
    }


def _build_preface_prompt(inputs: dict[str, Any]) -> str:
    show = inputs.get("show") or {}
    chapters = inputs.get("chapters") or []
    chapter_lines = []
    for ch in chapters:
        sections = "、".join(ch.get("section_titles") or [])[:260]
        excerpt = (ch.get("excerpt") or "")[:420]
        chapter_lines.append(
            f"第{ch.get('index'):02d}章：{ch.get('chapter_title')}\n"
            f"小节：{sections}\n"
            f"片段：{excerpt}"
        )
    return f"""
你要为一本由播客栏目多期内容汇编而成的 B 端定制书写整书序言。

栏目源信息：
- 栏目名：{show.get('title') or ''}
- 栏目简介：{show.get('brief') or ''}
- 栏目描述：{show.get('description_clean') or show.get('description') or ''}
- 主播/作者：{'、'.join(show.get('podcaster_names') or [])}

本书由以下章节组成：

{chr(10).join(chapter_lines)}

写作要求：
1. 写成“序”，不是营销文案，也不是功能说明。
2. 优先尊重主播自述里的精神气质。栏目描述里关于“人生褶皱”“向内探索”“书籍”“清醒”“温柔而有力量”的表达，是序言的底色。
3. 需要综合栏目气质与 13 章内容，说明这本书如何从读书、心理学、身体系统、行动框架、人际边界、线下生活、下行周期与未来布局，汇成一条“在褶皱中重建生活可能性”的线索。
4. 语气克制、清醒、有温度，像一本正式非虚构图书的编者序，但不要站得太远。要让读者感觉这篇序是在陪人走近自己的生活，而不是第三者在分析一个栏目。
5. 可以适度使用“我们”“你”，但不要鸡汤，不要喊口号；少用“这一栏目”“这本汇编”这类外部观察词。
6. 不要过度使用双引号；除书名外，普通概念尽量不用引号。
7. 不要写“本书将带你”“你将学会”这类课程销售腔。
8. 全文冒号最多 1 个，不要用冒号承担主要转折；优先用自然的承接句、因果句和递进句推进。
9. 段落之间要有连续的思想坡度，避免从主题清单突然跳到另一个主题清单。
10. 句子长短要错落。可以有短句，但不要连续使用同一种句式；避免每句都像“主题，是……”。
11. 控制在 500-800 个中文字符，分 3-5 段；目标排版长度是 1-2 页，宁可短，不要铺陈。

严格返回 JSON：
{{
  "preface_title": "序言",
  "preface_paragraphs": ["段落1", "段落2"]
}}
""".strip()


def _fallback_preface(inputs: dict[str, Any]) -> dict[str, Any]:
    show = inputs.get("show") or {}
    title = show.get("title") or "这档播客"
    return {
        "preface_title": "序言",
        "preface_paragraphs": [
            f"{title}的出发点，是在人生褶皱里看见更多可能性。所谓褶皱，并不是生活的瑕疵，而是那些不够平整、不够顺利、也不容易被一句正确答案抹平的地方。",
            "这本书把十三期播客整理成十三章。从主体性、认知觉醒、内阻力与自律，到营养、睡眠、行动、底层自信、人际边界、线下生活、支持系统、未来布局和内核稳定，它们共同指向同一个问题。外部世界不断制造噪音，一个人还能不能重新组织自己的生活秩序。",
            "读书在这里不是为了获得漂亮观点，而是为了让观点进入日常。愿这本书陪你把模糊的感受慢慢辨认出来，在喧嚣世界里保持清醒，也温柔而有力量地生活。",
        ],
    }


async def generate_preface(inputs: dict[str, Any]) -> dict[str, Any]:
    llm = get_llm_service()
    prompt = _build_preface_prompt(inputs)
    try:
        result = await llm.generate_json(
            prompt=prompt,
            system_prompt="你是一位资深中文图书编辑，擅长为非虚构合集写克制、清醒、有温度的序言。你会尊重原栏目自述里的精神气质，让文字贴近读者的生活经验，而不是站在远处做摘要。",
            model=settings.LLM_PREMIUM_MODEL,
            temperature=0.55,
            max_tokens=2048,
            thinking_budget=0,
            timeout=240,
            label="book_front_matter_preface",
        )
    except Exception as e:
        logger.warning("序言生成失败，使用 fallback：%s", e)
        return _fallback_preface(inputs)
    paragraphs = result.get("preface_paragraphs") or []
    paragraphs = [str(p).strip() for p in paragraphs if str(p).strip()]
    if not paragraphs:
        return _fallback_preface(inputs)
    return {
        "preface_title": (result.get("preface_title") or "序言").strip(),
        "preface_paragraphs": paragraphs,
    }


def _front_page_title(title: str) -> str:
    return f'#front-title("{_escape_typst_string(title)}")'


def _format_toc_page(page: Any) -> str:
    try:
        return f"/{int(page):03d}"
    except (TypeError, ValueError):
        return ""


_CN_NUMS = "零一二三四五六七八九"


def _chapter_label(index: int) -> str:
    if index <= 0:
        return "第零章"
    if index < 10:
        return f"第{_CN_NUMS[index]}章"
    if index == 10:
        return "第十章"
    if index < 20:
        return f"第十{_CN_NUMS[index - 10]}章"
    tens, ones = divmod(index, 10)
    suffix = _CN_NUMS[ones] if ones else ""
    return f"第{_CN_NUMS[tens]}十{suffix}章"


def _chapter_json_path(chapter: dict[str, Any]) -> Path | None:
    episode_dir = chapter.get("episode_dir")
    if episode_dir:
        path = Path(str(episode_dir)) / "chapter_for_book.json"
        if path.exists():
            return path
        backend_path = Path("backend") / path
        if backend_path.exists():
            return backend_path
    return None


def _normalize_for_search(text: str) -> str:
    return "".join(str(text).split())


def _find_section_page(chapter: dict[str, Any], section_title: str) -> int | None:
    pdf_path_raw = chapter.get("pdf_path")
    if not pdf_path_raw or not section_title:
        return None
    pdf_path = Path(str(pdf_path_raw))
    if not pdf_path.exists():
        backend_path = Path("backend") / pdf_path
        if backend_path.exists():
            pdf_path = backend_path
        else:
            return None

    needle = _normalize_for_search(section_title)
    if not needle:
        return None
    page_start = int(chapter.get("page_start") or 1)
    try:
        doc = fitz.open(pdf_path)
    except Exception:
        return None
    try:
        for page_index, page in enumerate(doc):
            haystack = _normalize_for_search(page.get_text())
            if needle in haystack:
                return page_start + page_index
    finally:
        doc.close()
    return None


def _toc_sections(chapter: dict[str, Any]) -> list[dict[str, Any]]:
    chapter_path = _chapter_json_path(chapter)
    if not chapter_path:
        return []
    try:
        source = read_json(chapter_path)
    except Exception:
        return []
    sections = []
    for section in source.get("sections") or []:
        title = (section.get("section_title") or section.get("title") or "").strip()
        if not title:
            continue
        page = _find_section_page(chapter, title) or chapter.get("page_start")
        sections.append({"title": title, "page": page})
    return sections


def _split_author_names(value: Any) -> list[str]:
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            parts.extend(_split_author_names(item))
        return parts
    text = str(value or "").strip()
    if not text:
        return []
    text = text.replace("和", "、").replace("&", "、").replace("/", "、")
    return [p.strip() for p in re.split(r"[、，,\s]+", text) if p.strip()]


def _normalize_author_name(name: str) -> str:
    name = (name or "").strip()
    lowered = name.lower()
    if "fofo" in lowered or lowered == "fo":
        return "fofo"
    if "哈梨" in name:
        return "哈梨"
    return name


def resolve_book_authors(paths: JobPaths, front_matter: dict[str, Any]) -> list[str]:
    show = read_json(paths.show_json) if paths.show_json.exists() else {}
    raw_names: list[str] = []
    raw_names.extend(_split_author_names(front_matter.get("authors") or []))
    raw_names.extend(_split_author_names(show.get("podcaster_names") or []))

    chapter_paths: list[Path] = []
    if paths.episodes_index_json.exists():
        try:
            index = read_json(paths.episodes_index_json)
        except Exception:
            index = {}
        for ep in index.get("episodes", []):
            try:
                idx = int(ep.get("index"))
            except (TypeError, ValueError):
                continue
            chapter_path = paths.episode_dir(idx) / "chapter_for_book.json"
            if not chapter_path.exists():
                continue
            chapter_paths.append(chapter_path)
    else:
        chapter_paths = sorted(paths.episodes_dir.glob("ep*/chapter_for_book.json"))

    for chapter_path in chapter_paths:
        try:
            chapter = read_json(chapter_path)
        except Exception:
            continue
        raw_names.extend(_split_author_names(chapter.get("host_name") or ""))

    normalized: list[str] = []
    seen: set[str] = set()
    for raw in raw_names:
        name = _normalize_author_name(raw)
        if not name or name in seen:
            continue
        seen.add(name)
        normalized.append(name)

    preferred = ["哈梨", "fofo"]
    preferred_present = [name for name in preferred if name in seen]
    rest = [name for name in normalized if name not in preferred]
    return preferred_present + rest


def build_front_matter_typst(
    *,
    paths: JobPaths,
    front_matter: dict[str, Any],
    chapters: list[dict[str, Any]],
) -> str:
    show = read_json(paths.show_json) if paths.show_json.exists() else {}
    book_title = show.get("title") or paths.job_id
    # 书名：优先读 front_matter 里的自定义书名，否则回落到播客名
    book_display_title = (front_matter.get("book_title") or "").strip() or book_title
    book_subtitle = (front_matter.get("book_subtitle") or "").strip()
    author_list = resolve_book_authors(paths, front_matter)
    author_names = "、".join(str(a).strip() for a in author_list if str(a).strip())
    author_str = f"{author_names} 著" if author_names else ""
    # 系列行始终使用播客名（区别于书名）
    series_str = f"《{book_title}》· 典藏系列"
    preamble = (
        FRONT_MATTER_PREAMBLE
        .replace("BOOK_TITLE_PLACEHOLDER", _escape_typst_string(book_display_title))
        .replace("BOOK_SUBTITLE_PLACEHOLDER", _escape_typst_string(book_subtitle))
        .replace("PODCAST_NAME_PLACEHOLDER", _escape_typst_string(book_title))
        .replace("AUTHOR_PLACEHOLDER", _escape_typst_string(author_str))
        .replace("SERIES_PLACEHOLDER", _escape_typst_string(series_str))
    )
    lines: list[str] = [preamble, "#counter(page).update(1)"]

    # 扉页（title page），奇数页起，之后另起奇数页进入序言
    lines.append("#title-page()")
    lines.append('#pagebreak(to: "odd")')

    lines.append(_front_page_title(front_matter.get("preface_title") or "序言"))
    lines.append("#v(0.4cm)")
    for para in front_matter.get("preface_paragraphs") or []:
        lines.append(f"#par[{_escape_typst(str(para).strip())}]")

    # 目录另起奇数页；前置页使用罗马页码，正文单章 PDF 自己从阿拉伯 1 开始。
    lines.append('#pagebreak(to: "odd")')
    lines.append("#v(2.1cm)")
    lines.append("#toc-title()")
    lines.append("#v(2.4cm)")
    lines.append("#toc-block[")
    for ch in chapters:
        idx = int(ch.get("index"))
        title = ch.get("chapter_title") or ""
        if not title and ch.get("episode_dir"):
            chapter_path = Path(str(ch["episode_dir"])) / "chapter_for_book.json"
            if chapter_path.exists():
                try:
                    title = (read_json(chapter_path).get("chapter_title") or "").strip()
                except Exception:
                    title = ""
        title = title or ch.get("title") or f"第 {idx:02d} 章"
        chapter_label = _chapter_label(idx)
        lines.append(f'#toc-chapter[{_escape_typst(chapter_label)}][{_escape_typst(title)}]')
        sections = _toc_sections(ch)
        if not sections:
            page_start = _format_toc_page(ch.get("page_start"))
            lines.append(f'#toc-section[{_escape_typst(title)}][{_escape_typst(page_start)}]')
        for section in sections:
            page = _format_toc_page(section.get("page"))
            lines.append(f'#toc-section[{_escape_typst(section["title"])}][{_escape_typst(page)}]')
    lines.append("]")
    return "\n\n".join(lines)


def _append_blank_page_if_odd(pdf_path: Path) -> None:
    doc = fitz.open(pdf_path)
    try:
        if doc.page_count % 2 == 0:
            return
        rect = doc[0].rect
        doc.new_page(width=rect.width, height=rect.height)
        tmp_path = pdf_path.with_suffix(".even.pdf")
        doc.save(tmp_path)
    finally:
        doc.close()
    tmp_path.replace(pdf_path)


def render_book_front_matter(
    *,
    paths: JobPaths,
    chapters: list[dict[str, Any]],
    force: bool = False,
) -> Path:
    paths.merged_dir.mkdir(parents=True, exist_ok=True)
    json_path = paths.merged_dir / DEFAULT_JSON_NAME
    if json_path.exists() and not force:
        front_matter = read_json(json_path)
    else:
        inputs = collect_book_inputs(paths)
        front_matter = asyncio.run(generate_preface(inputs))
        write_json(json_path, front_matter)
        logger.info("✅ 整书序言已生成 → %s", json_path)

    typst_source = build_front_matter_typst(
        paths=paths,
        front_matter=front_matter,
        chapters=chapters,
    )
    typ_path = paths.merged_dir / DEFAULT_TYP_NAME
    pdf_path = paths.merged_dir / DEFAULT_PDF_NAME
    typ_path.write_text(typst_source, encoding="utf-8")
    typst_lib.compile(str(typ_path), output=str(pdf_path), font_paths=[FONTS_DIR])
    _append_blank_page_if_odd(pdf_path)
    logger.info("✅ 前置页 PDF 已渲染 → %s", pdf_path)
    return pdf_path


def main() -> None:
    p = argparse.ArgumentParser(description="生成 workbench 整书前置页：序言 + 目录")
    p.add_argument("--job", required=True)
    p.add_argument("--force", action="store_true", help="强制重新生成序言文本")
    args = p.parse_args()

    paths = JobPaths.open(args.job)
    index = read_json(paths.episodes_index_json)
    chapters = []
    page_start = 1
    for ep in index.get("episodes", []):
        idx = int(ep.get("index"))
        chapter_path = paths.episode_dir(idx) / "chapter_for_book.json"
        if not chapter_path.exists():
            continue
        chapter = read_json(chapter_path)
        chapters.append({
            "index": idx,
            "chapter_title": chapter.get("chapter_title") or ep.get("title") or "",
            "page_start": page_start,
        })
    render_book_front_matter(paths=paths, chapters=chapters, force=args.force)


if __name__ == "__main__":
    main()
