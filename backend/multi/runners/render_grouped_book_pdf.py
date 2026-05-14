"""
render_grouped_book_pdf — 把"分组单集"范式的书渲染成 PDF。

结构（与标准范式不同）：
  大章节（level 1 扉页） → 内容概览 → 每集（level 2 标题 → 导语 → 正文小节）

输入：
    book_config.json
    merged/chapter_{N}_overview.json
    episodes/epNN/episode_intro.json
    episodes/epNN/chapter_for_book.json
    episodes/epNN/images/（WeChat 原图）

输出：
    merged/chapter_{N}_preview.pdf
    merged/chapter_{N}_preview.typ（Typst 源）

用法：
    cd backend
    python -m workbench.runners.render_grouped_book_pdf --job dart__jh__book1__2026-05-07 --chapter 1
    python -m workbench.runners.render_grouped_book_pdf --job dart__jh__book1__2026-05-07
"""
from __future__ import annotations

from multi.env import setup
setup()

import argparse
import logging
import sys
from pathlib import Path
from typing import Any

import fitz as _fitz
import typst as typst_lib

from multi.io import JobPaths, read_json, write_json
from core.workflow.nodes.typeset import (
    FONTS_DIR,
    _escape_typst,
    _escape_typst_string,
    _split_footnotes,
    _process_inline,
    _process_blockquote,
    _extract_speaker,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("render_grouped_book_pdf")

# ================================================================
# Typst 模板（分组书版 — 大章节 + 集内小节）
# ================================================================

TYPST_PREAMBLE = r"""
// PodBook 工作台 — 分组书 PDF（render_grouped_book_pdf 生成）

#let clr-accent  = rgb("#6B3A2A")
#let clr-gold    = rgb("#C4A882")
#let clr-body    = rgb("#2D2D2D")
#let clr-light   = rgb("#999999")

#let book-name = "BOOK_NAME_PLACEHOLDER"
#let chapter-name = "CHAPTER_NAME_PLACEHOLDER"
#let chapter-num-padded = "CHAPTER_NUM_PADDED_PLACEHOLDER"
#let chapter-page-start = CHAPTER_PAGE_START_PLACEHOLDER

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
  header-ascent: 8mm,
  footer-descent: 10mm,
  numbering: "1",
  header: context {
    counter(footnote).update(0)
  },
  footer: context {
    let phys = here().page()
    set text(fill: black)
    show linebreak: none
    let pagenum = text(
      font: ("EB Garamond", "Palatino", "Baskerville", "Times New Roman", "Noto Serif CJK SC", "Noto Serif SC"),
      size: 10pt,
      style: "italic",
    )[#counter(page).display()]
    let sep = text(font: ("Noto Sans CJK SC", "Noto Sans SC"), size: 8.5pt)[#h(0.6em)|#h(0.6em)]
    let hang = 5mm
    if calc.even(phys) {
      // 偶数页：大章节标题
      let ch-text = text(font: ("Noto Sans CJK SC", "Noto Sans SC"), size: 8.5pt)[#chapter-name]
      h(-hang)
      [#pagenum#sep#ch-text]
      h(1fr)
    } else {
      // 奇数页：当前集标题（取最近的 level 2 heading）
      let ep2 = query(heading.where(level: 2).before(here()))
      let ep-text = if ep2.len() > 0 {
        text(font: ("Noto Sans CJK SC", "Noto Sans SC"), size: 8.5pt)[#ep2.last().body]
      } else {
        text(font: ("Noto Sans CJK SC", "Noto Sans SC"), size: 8.5pt)[#chapter-name]
      }
      h(1fr)
      [#ep-text#sep#pagenum]
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
#set par(leading: 1.4em, spacing: 1.8em, first-line-indent: (amount: 2em, all: true), justify: true)

#set footnote(numbering: "①")
#set footnote.entry(indent: 0em)
#show footnote.entry: it => {
  set text(size: 8.5pt, fill: black)
  set par(leading: 0.9em, spacing: 0.8em, first-line-indent: 0em, hanging-indent: 0em)
  show super: it => text(baseline: 0em, size: 1em)[#it.body]
  it
}

// ── 大章节扉页（level 1） ──
#show heading.where(level: 1): it => {
  pagebreak(weak: true)
  set page(footer: none, header: none)
  set par(first-line-indent: 0em, leading: 1.2em)
  place(top + left, dy: 18em)[
    #block(width: 95%)[
      #it.body
    ]
  ]
  v(1pt)
  pagebreak(weak: false)
}

// ── 每集标题（level 2） — 不独立成页，有装饰线，奇偶页不同对齐 ──
#show heading.where(level: 2): it => {
  pagebreak(weak: true)
  set par(first-line-indent: 0em)
  v(1.2em)
  context {
    let aln = if calc.even(here().page()) { left } else { right }
    block(width: 100%, above: 0em, below: 0.6em)[
      #align(aln)[
        #text(
          font: ("Source Han Serif SC", "Songti SC", "Noto Serif CJK SC", "Noto Serif SC"),
          size: 15pt,
          weight: "semibold",
          fill: black,
          tracking: 0.04em,
        )[#it.body]
      ]
    ]
  }
}

// ── 每集主播嘉宾（紧跟 level 2 标题下方） ──
#let episode-speakers(body) = {
  set par(first-line-indent: 0em)
  v(0.6em)
  context {
    let aln = if calc.even(here().page()) { left } else { right }
    align(aln)[
      #text(
        font: ("Noto Serif CJK SC", "Noto Serif SC", "Source Han Serif SC", "STSong"),
        size: 8.5pt,
        fill: clr-body,
        tracking: 0.05em,
      )[#body]
    ]
  }
  v(1.2em)
}

// ── 小节标题（level 3） — 居中无衬线 ──
#show heading.where(level: 3): it => {
  set par(first-line-indent: 0em)
  block(width: 100%, above: 2.4em, below: 0.4em)[
    #align(center)[
      #text(
        font: ("Source Han Sans SC", "Hiragino Sans GB", "PingFang SC", "Noto Sans CJK SC", "Noto Sans SC"),
        size: 12pt,
        weight: "bold",
        tracking: 0.05em,
        fill: black,
      )[#it.body]
    ]
  ]
}

// ── 导语（楷体，浅灰圆角底图 + 左侧大装饰引号） ──
// 不足一页：单独占页（强制换页），超过一页：正文紧跟。
#let episode-intro(body) = {
  set par(first-line-indent: 0em)
  block(
    width: 100%,
    inset: (x: 2.4em, y: 2.0em),
    fill: rgb("#F2F2F2"),
    radius: 6pt,
  )[
    #grid(
      columns: (2.6em, 1fr),
      column-gutter: 0.4em,
      align: (top, top),
      text(
        size: 6.5em,
        fill: rgb("#D0D0D0"),
        font: ("Georgia", "Times New Roman", "Noto Serif CJK SC"),
      )[\u{201C}],
      {
        set text(font: ("FandolKai", "STKaiti", "Kaiti SC", "Noto Serif CJK SC", "Noto Serif SC"))
        set par(leading: 1.0em, first-line-indent: 0em)
        body
      },
    )
  ]
  // 测量导言高度，不足一页则独占该页。
  // 实际文字列宽 = 版心(105mm) - block inset(2×8.9mm) - 引号列(2.6em≈9.6mm) - 栏间距(0.4em≈1.5mm) ≈ 76mm。
  // 阈值 100mm：导语自身超过 100mm 时一定会随标题+对谈人行溢出页面，不强制换页。
  context {
    let m = measure(block(width: 76mm, inset: (y: 2.0em))[
      #set text(font: ("FandolKai", "STKaiti", "Kaiti SC", "Noto Serif CJK SC", "Noto Serif SC"), size: 10.5pt)
      #set par(leading: 1.0em, first-line-indent: 0em)
      #body
    ])
    if m.height < 100mm {
      pagebreak(weak: false)
    } else {
      v(1.2em)
    }
  }
}

// ── 结语（同导语样式） ──
#let episode-epilogue(body) = {
  set par(first-line-indent: 0em)
  v(1.0em)
  block(
    width: 100%,
    inset: (x: 2.4em, y: 2.0em),
    fill: rgb("#F2F2F2"),
    radius: 6pt,
  )[
    #grid(
      columns: (1fr, 2.6em),
      column-gutter: 0.4em,
      align: (top, top),
      {
        set text(font: ("FandolKai", "STKaiti", "Kaiti SC", "Noto Serif CJK SC", "Noto Serif SC"))
        set par(leading: 1.4em, first-line-indent: 0em)
        body
      },
      text(
        size: 6.5em,
        fill: rgb("#D0D0D0"),
        font: ("Georgia", "Times New Roman", "Noto Serif CJK SC"),
      )[\u{201D}],
    )
  ]
  v(1.2em)
}

// ── 内容概览（章节开头成段文字） ──
#let chapter-overview(body) = {
  set page(footer: none, header: none)
  set par(first-line-indent: 0em)
  block(width: 100%, below: 2em)[
    #set text(size: 10.5pt, fill: clr-body)
    #set par(leading: 1.5em, spacing: 1.6em, first-line-indent: (amount: 2em, all: true), justify: true)
    #par[]
    #body
  ]
  pagebreak(weak: true)
}

// ── 正文插图 ──
#let max-illustration-height = 6.0cm

#let book-illustration(path, caption: none, img-width: 85%, max-height: max-illustration-height) = {
  v(1.5em)
  align(center)[
    #set par(first-line-indent: 0em)
    #block(width: img-width, breakable: false)[
      #layout(size => {
        let full-img = image(path, width: size.width)
        let m = measure(full-img)
        if m.height > max-height {
          image(path, height: max-height)
        } else {
          full-img
        }
      })
      #if caption != none {
        v(0.22em)
        text(size: 8.5pt, fill: clr-light)[#caption]
      }
    ]
  ]
  v(1.5em)
}

#let chapter-end() = { v(2em) }
"""

_CN_CHAPTER_NUMS = ["零", "一", "二", "三", "四", "五", "六", "七", "八", "九", "十"]


def _chapter_label_vertical_typst(label: str) -> str:
    """生成竖排章节标签 Typst 片段（左右各一条竖线）。"""
    chars = list(label)
    n = len(chars)
    # 每字约 14pt，spacing 0.4em≈5.6pt，加 2pt 余量
    height_pt = int(n * 18 + max(0, n - 1) * 7.2 + 2)
    char_items = "\n      ".join(f"[{c}]," for c in chars)
    return (
        f"grid(\n"
        f"    columns: (1.5pt, auto, 1.5pt),\n"
        f"    align: center + horizon,\n"
        f"    column-gutter: 0.8em,\n"
        f"    block(width: 1.5pt, height: {height_pt}pt, fill: black),\n"
        f"    stack(\n"
        f"      dir: ttb,\n"
        f"      spacing: 0.4em,\n"
        f"      {char_items}\n"
        f"    ),\n"
        f"    block(width: 1.5pt, height: {height_pt}pt, fill: black),\n"
        f"  )"
    )


def _chapter_label_cn(index: int) -> str:
    if 0 <= index <= 10:
        return f"第{_CN_CHAPTER_NUMS[index]}章"
    tens, ones = divmod(index, 10)
    suffix = _CN_CHAPTER_NUMS[ones] if ones else ""
    return f"第{_CN_CHAPTER_NUMS[tens]}十{suffix}章"


def _strip_footnote_prefix(text: str) -> str:
    import re
    pat = re.compile(r"^\s*(\*\*)?(编者注|编者按|译者注|译注|按|注)(\*\*)?\s*[：:]\s*")
    return pat.sub("", text, count=1).lstrip() if text else text


def _is_md_hr(p: str) -> bool:
    s = p.strip()
    return len(s) >= 3 and all(c == s[0] for c in s) and s[0] in {"-", "*", "_"}


def _apply_name_aliases(text: str, aliases: dict[str, str]) -> str:
    for old, new in aliases.items():
        text = text.replace(old, new)
    return text


def _avg_hash(path: Path, size: int = 8) -> tuple[int, ...]:
    """Average perceptual hash for near-duplicate detection."""
    from PIL import Image
    with Image.open(path) as im:
        im = im.convert("L").resize((size, size), Image.LANCZOS)
        pixels = list(im.tobytes())
        avg = sum(pixels) / len(pixels)
        return tuple(1 if p >= avg else 0 for p in pixels)


def _dedup_images(imgs: list[Path], threshold: int = 10) -> list[Path]:
    """Return unique images, dropping near-duplicates (e.g. watermarked clones)."""
    unique: list[Path] = []
    hashes: list[tuple[int, ...]] = []
    for img in imgs:
        try:
            h = _avg_hash(img)
        except Exception:
            unique.append(img)
            continue
        if all(sum(a != b for a, b in zip(h, eh)) > threshold for eh in hashes):
            unique.append(img)
            hashes.append(h)
    return unique


def _build_section_typst(
    section: dict[str, Any],
    *,
    emitted_footnotes: set[int],
    emitted_footnote_texts: set[str],
    section_images: list[str] | None = None,
    name_aliases: dict[str, str] | None = None,
    guest_photo_path: str | None = None,
    guest_photo_caption: str = "",
    guest_name: str = "",
    guest_photo_width: str = "85%",
    guest_photo_max_height: str | None = None,
) -> str:
    """渲染单个小节为 Typst 片段（level 3 标题 + 段落 + 图片）。"""
    title = (section.get("section_title") or "").strip()
    raw_content = section.get("content") or ""
    if name_aliases:
        raw_content = _apply_name_aliases(raw_content, name_aliases)

    body, content_footnotes = _split_footnotes(raw_content)
    content_footnotes = {
        num: _strip_footnote_prefix(text)
        for num, text in content_footnotes.items()
    }
    structured = section.get("footnotes") or []
    for fn in structured:
        num = fn.get("number") or fn.get("id")
        text = _strip_footnote_prefix((fn.get("text") or "").strip())
        if num and text and num not in content_footnotes:
            content_footnotes[int(num)] = text

    paragraphs = [
        p.strip() for p in body.split("\n\n")
        if p.strip() and not _is_md_hr(p.strip())
    ]

    processed: list[str] = []
    para_speakers: list[str | None] = []
    for para in paragraphs:
        if para.startswith(">"):
            processed.append(_process_blockquote(para, content_footnotes))
            para_speakers.append(None)
        else:
            sp, _rest = _extract_speaker(para)
            para_speakers.append(sp)
            processed.append(_process_inline(
                para, content_footnotes, emitted_footnotes, emitted_footnote_texts,
            ))

    # 嘉宾照：插在第一个嘉宾发言段之后
    guest_photo_after: int | None = None
    if guest_photo_path and guest_name:
        for i, sp in enumerate(para_speakers):
            if sp and guest_name.lower() in sp.lower():
                guest_photo_after = i
                break

    # 其他图片均匀分布在段落之间
    imgs = section_images or []
    n_paras = len(processed)
    img_after: dict[int, list[str]] = {}
    if imgs and n_paras > 0:
        for j, img_path in enumerate(imgs):
            insert_after = min(
                round((j + 1) * n_paras / len(imgs)) - 1,
                n_paras - 1,
            )
            img_after.setdefault(insert_after, []).append(img_path)

    parts: list[str] = []
    if title:
        parts.append(f"=== {_escape_typst(title)}\n")
    parts.append("#par[]")

    SPEAKER_SWITCH_GAP = "#v(0.6em)"
    last_speaker: str | None = None

    for i, para in enumerate(processed):
        if para:
            cur_speaker = para_speakers[i] if i < len(para_speakers) else None
            if cur_speaker and last_speaker and cur_speaker != last_speaker:
                parts.append(SPEAKER_SWITCH_GAP)
            if cur_speaker:
                last_speaker = cur_speaker
            parts.append(f"#par()[{para}]")
        # 嘉宾照
        if i == guest_photo_after and guest_photo_path:
            caption_param = f', caption: "{_escape_typst_string(guest_photo_caption)}"' if guest_photo_caption else ""
            max_height_param = f", max-height: {guest_photo_max_height}" if guest_photo_max_height else ""
            parts.append(f'#book-illustration("{guest_photo_path}"{caption_param}, img-width: {guest_photo_width}{max_height_param})')
        # 其他图片
        for img_path in img_after.get(i, []):
            parts.append(f'#book-illustration("{img_path}")')

    return "\n\n".join(parts)


def _build_speakers_line(ep_dir: Path, name_aliases: dict[str, str] | None = None) -> str:
    """从 meta.json（+ state_after_transcription.json 兜底）构建「主播 · 嘉宾」文字。"""
    meta_path = ep_dir / "meta.json"
    if not meta_path.exists():
        return ""
    meta = read_json(meta_path)
    host = (meta.get("host_name") or "").strip()
    guests = [g.strip() for g in (meta.get("guest_names") or []) if g.strip()]

    # meta.json 里 guest_names 有时为空，从 state_after_transcription 兜底
    if not guests:
        trans_path = ep_dir / "state_after_transcription.json"
        if trans_path.exists():
            trans = read_json(trans_path)
            guests = [g.strip() for g in (trans.get("guest_names") or []) if g.strip()]

    if name_aliases:
        host = _apply_name_aliases(host, name_aliases)
        guests = [_apply_name_aliases(g, name_aliases) for g in guests]

    all_names = guests + ([host] if host else [])
    if not all_names:
        return ""
    return _escape_typst(f"对谈人 / {'、'.join(all_names)}")


import os as _os
import re as _re
import random as _random


def _extract_guest_info(article_text: str) -> tuple[str, str]:
    """从文章中提取嘉宾姓名和简介，用于嘉宾照图注。"""
    m = _re.search(r'本期嘉宾\s+(.+?)[\n\r]+([^\n\r]{4,80})', article_text)
    if m:
        return m.group(1).strip(), m.group(2).strip()
    return "", ""


def _load_guest_photo(ep_dir: Path, merged_dir: Path) -> tuple[str | None, str, str, str, str | None]:
    """
    识别嘉宾照并构建图注。
    返回 (merged_dir 的相对路径, 图注文字, 嘉宾姓名, img_width, max_height)。
    嘉宾照判定：优先使用 manifest 中 is_guest_photo=true 的条目；
    否则取第一张长宽比在 0.85-2.5 之间的图（排除 QR 码等超长图）。
    img_width 从 manifest 的 book_img_width 字段读取，默认 "85%"。
    max_height 从 manifest 的 book_max_height 字段读取，默认 None（使用模板默认值）。
    """
    manifest_path = ep_dir / "media_manifest.json"
    if not manifest_path.exists():
        return None, "", "", "85%", None

    manifest = read_json(manifest_path)
    article_path = ep_dir / "article_text.md"
    guest_name, guest_desc = "", ""
    if article_path.exists():
        guest_name, guest_desc = _extract_guest_info(article_path.read_text(encoding="utf-8"))

    def caption_str(img: dict | None = None):
        if img and img.get("book_caption_suppress"):
            return ""
        if img and img.get("book_caption"):
            return img["book_caption"].rstrip("，、：；,:")
        raw = f"{guest_name} · {guest_desc}" if guest_desc else guest_name
        return raw.rstrip("，、：；,:")

    # 优先：显式标记的嘉宾照（book_exclude=true 则跳过）
    for img in manifest.get("images", []):
        if not img.get("is_guest_photo"):
            continue
        if img.get("book_exclude"):
            continue
        dl = img.get("download") or {}
        if dl.get("status") != "ok" or not dl.get("local_path"):
            continue
        full = ep_dir / dl["local_path"]
        rel = _os.path.relpath(str(full.resolve()), str(merged_dir.resolve()))
        width = img.get("book_img_width") or "85%"
        max_height = img.get("book_max_height") or None
        return rel, caption_str(img), guest_name, width, max_height

    # 兜底：比例在 0.85-2.5 之间的第一张
    for img in manifest.get("images", []):
        if img.get("book_exclude"):
            continue
        ratio = float((img.get("raw") or {}).get("data-ratio", 0))
        dl = img.get("download") or {}
        if dl.get("status") != "ok" or not dl.get("local_path"):
            continue
        if 0.85 <= ratio <= 2.5:
            full = ep_dir / dl["local_path"]
            rel = _os.path.relpath(str(full.resolve()), str(merged_dir.resolve()))
            width = img.get("book_img_width") or "85%"
            max_height = img.get("book_max_height") or None
            return rel, caption_str(img), guest_name, width, max_height

    return None, "", "", "85%", None


def _build_episode_typst(
    ep_cfg: dict[str, Any],
    ep_dir: Path,
    merged_dir: Path,
    name_aliases: dict[str, str] | None = None,
) -> str:
    """渲染单集（level 2 标题 + 主播嘉宾 + 导语 + 图片 + 小节）。"""
    chapter_path = ep_dir / "chapter_for_book.json"
    if not chapter_path.exists():
        return f"// ep{ep_cfg['book_ep_index']:02d} 内容尚未生成"

    chapter = read_json(chapter_path)
    ep_title = (chapter.get("chapter_title") or "").strip() or "（未命名）"

    parts: list[str] = []

    # 集标题（level 2）
    parts.append(f"== {_escape_typst(ep_title)}\n")

    # 主播 · 嘉宾（紧跟标题）
    speakers_line = _build_speakers_line(ep_dir, name_aliases=name_aliases)
    if speakers_line:
        parts.append(f"#episode-speakers[{speakers_line}]")

    # 导语
    intro_path = ep_dir / "episode_intro.json"
    if intro_path.exists():
        intro_data = read_json(intro_path)
        intro_text = (intro_data.get("intro") or "").strip()
        intro_attribution = (intro_data.get("intro_attribution") or "").strip()
        if intro_text:
            inner = _escape_typst(intro_text)
            if intro_attribution:
                inner += f"\n\n#align(right)[{_escape_typst(intro_attribution)}]"
            parts.append(f"#episode-intro[{inner}]")

    # 嘉宾照识别
    guest_photo_rel, guest_caption, guest_name, guest_photo_width, guest_photo_max_height = _load_guest_photo(ep_dir, merged_dir)
    guest_photo_filename = _os.path.basename(guest_photo_rel) if guest_photo_rel else ""

    # 其余图片去重，排除嘉宾照和 book_exclude 标记的图
    manifest_excludes: set[str] = set()
    _mf_path = ep_dir / "media_manifest.json"
    if _mf_path.exists():
        for _img in read_json(_mf_path).get("images", []):
            if _img.get("book_exclude"):
                lp = (_img.get("download") or {}).get("local_path", "")
                if lp:
                    manifest_excludes.add(_os.path.basename(lp))

    images_dir = ep_dir / "images"
    other_imgs: list[str] = []
    if images_dir.exists():
        raw_imgs = sorted(
            f for f in images_dir.iterdir()
            if f.is_file() and f.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp")
        )
        for img in _dedup_images(raw_imgs):
            if img.name == guest_photo_filename:
                continue
            if img.name in manifest_excludes:
                continue
            rel = _os.path.relpath(str(img.resolve()), str(merged_dir.resolve()))
            other_imgs.append(rel)

    # 其他图片均匀分布到所有小节，每节最多 1 张
    sections = chapter.get("sections") or []
    emitted_footnotes: set[int] = set()
    emitted_footnote_texts: set[str] = set()
    n_sections = len(sections)

    no_image_sections: set[int] = set(chapter.get("no_image_sections") or [])
    section_imgs_map: dict[int, list[str]] = {i: [] for i in range(n_sections)}
    pinned = chapter.get("pinned_section_images")
    if pinned is not None:
        # 使用已固定的图片分配，不再 shuffle
        for k, v in pinned.items():
            idx = int(k)
            if idx < n_sections:
                section_imgs_map[idx] = list(v)
    elif other_imgs and n_sections >= 1:
        rng = _random.Random(ep_cfg.get("book_ep_index", 0))
        shuffled = list(other_imgs)
        rng.shuffle(shuffled)
        # 每节最多放 1 张，超出的丢弃；no_image_sections 的节不参与分配
        slots = [i for i in range(n_sections) if i not in no_image_sections]
        rng.shuffle(slots)
        for img, slot in zip(shuffled, slots):
            section_imgs_map[slot].append(img)
        # 首次分配后写入，后续渲染直接复用
        chapter["pinned_section_images"] = {str(k): v for k, v in section_imgs_map.items()}
        write_json(chapter_path, chapter)

    for i, section in enumerate(sections):
        # 嘉宾照只传给第一节（第一节通常含嘉宾首段发言）
        sec_typst = _build_section_typst(
            section,
            emitted_footnotes=emitted_footnotes,
            emitted_footnote_texts=emitted_footnote_texts,
            section_images=section_imgs_map.get(i, []),
            name_aliases=name_aliases,
            guest_photo_path=guest_photo_rel if i == 0 else None,
            guest_photo_caption=guest_caption if i == 0 else "",
            guest_name=guest_name,
            guest_photo_width=guest_photo_width,
            guest_photo_max_height=guest_photo_max_height if i == 0 else None,
        )
        parts.append(sec_typst)

    # 结语
    epilogue_path = ep_dir / "episode_epilogue.json"
    if epilogue_path.exists():
        epilogue_data = read_json(epilogue_path)
        epilogue_text = (epilogue_data.get("epilogue") or "").strip()
        epilogue_attribution = (epilogue_data.get("epilogue_attribution") or "").strip()
        if epilogue_text:
            inner = _escape_typst(epilogue_text)
            if epilogue_attribution:
                inner += f"\n\n#align(right)[{_escape_typst(epilogue_attribution)}]"
            parts.append(f"#episode-epilogue[{inner}]")

    parts.append("#chapter-end()")
    return "\n\n".join(parts)


def build_grouped_chapter_typst(
    chapter_config: dict[str, Any],
    book_config: dict[str, Any],
    paths: JobPaths,
    *,
    page_start: int = 1,
) -> str:
    """生成单个大章节的完整 Typst 源。"""
    chapter_index = chapter_config["chapter_index"]
    chapter_title = chapter_config["chapter_title"]
    book_title = book_config.get("book_title", "")
    label = _chapter_label_cn(chapter_index)
    chapter_num_padded = f"{chapter_index:02d}"

    preamble = (
        TYPST_PREAMBLE
        .replace("BOOK_NAME_PLACEHOLDER", _escape_typst_string(book_title))
        .replace("CHAPTER_NAME_PLACEHOLDER", _escape_typst_string(f"{label} {chapter_title}"))
        .replace("CHAPTER_NUM_PADDED_PLACEHOLDER", chapter_num_padded)
        .replace("CHAPTER_PAGE_START_PLACEHOLDER", str(max(1, page_start)))
        .replace("CHAPTER_LABEL_VERT_PLACEHOLDER", _chapter_label_vertical_typst(label))
    )

    blocks: list[str] = [preamble]
    blocks.append(f"#counter(page).update({max(1, page_start)})")

    # 章节扉页（大章节标题，level 1）
    _ch_text = (
        f'font: ("Source Han Sans SC", "Hiragino Sans GB", "PingFang SC", '
        f'"Noto Sans CJK SC", "Noto Sans SC"), size: 28pt, weight: "extrabold", '
        f'fill: black, tracking: 0.12em'
    )
    ch_title_display = (
        f'#stack(dir: ttb, spacing: 2.8em,'
        f' text({_ch_text})[{_escape_typst(label)}],'
        f' text({_ch_text})[{_escape_typst(chapter_title)}])'
    )
    blocks.append(f"#heading(level: 1)[{ch_title_display}]\n")

    # 内容概览
    overview_path = paths.merged_dir / f"chapter_{chapter_index}_overview.json"
    if overview_path.exists():
        overview_data = read_json(overview_path)
        paras = [p.strip() for p in (overview_data.get("overview_paragraphs") or []) if p.strip()]
        if paras:
            overview_body = "\n\n".join(
                f"#par()[{_escape_typst(p)}]"
                for p in paras
            )
            blocks.append(f"#chapter-overview[\n{overview_body}\n]")

    # 各集内容
    name_aliases: dict[str, str] = book_config.get("name_aliases") or {}
    merged_dir = paths.merged_dir
    for ep_cfg in chapter_config.get("episodes", []):
        ep_idx = ep_cfg["book_ep_index"]
        ep_dir = paths.episode_dir(ep_idx)
        ep_typst = _build_episode_typst(ep_cfg, ep_dir, merged_dir, name_aliases=name_aliases or None)
        blocks.append(ep_typst)

    return "\n\n".join(blocks)


def render_grouped_chapter_pdf(
    chapter_config: dict[str, Any],
    book_config: dict[str, Any],
    paths: JobPaths,
    *,
    page_start: int = 1,
) -> Path:
    chapter_index = chapter_config["chapter_index"]
    typst_source = build_grouped_chapter_typst(
        chapter_config, book_config, paths, page_start=page_start,
    )

    typ_path = paths.merged_dir / f"chapter_{chapter_index}_preview.typ"
    pdf_path = paths.merged_dir / f"chapter_{chapter_index}_preview.pdf"

    typ_path.write_text(typst_source, encoding="utf-8")
    logger.info("[章%d] 编译 Typst → PDF ...", chapter_index)
    # root 设为 job 根目录，让 Typst 沙盒能访问 episodes/ 下的图片
    typst_lib.compile(
        str(typ_path),
        output=str(pdf_path),
        font_paths=[FONTS_DIR],
        root=str(paths.root.resolve()),
    )
    logger.info("[章%d] PDF 已生成 → %s", chapter_index, pdf_path)
    return pdf_path


def main() -> None:
    p = argparse.ArgumentParser(description="把分组书章节渲染为 PDF")
    p.add_argument("--job", required=True, help="作业 ID")
    p.add_argument("--chapter", type=int, default=None, help="渲染指定章节，不指定则全部")
    args = p.parse_args()

    paths = JobPaths.open(args.job)
    config_path = paths.root / "book_config.json"
    if not config_path.exists():
        raise SystemExit(f"未找到 book_config.json：{paths.root}")

    book_config = read_json(config_path)
    all_chapters = book_config.get("chapters", [])

    if args.chapter is not None:
        chapters_to_render = [c for c in all_chapters if c.get("chapter_index") == args.chapter]
        if not chapters_to_render:
            raise SystemExit(f"未找到 chapter_index={args.chapter} 的章节配置")
    else:
        chapters_to_render = all_chapters

    paths.merged_dir.mkdir(parents=True, exist_ok=True)

    def _pdf_pages(chapter_index: int) -> int:
        p = paths.merged_dir / f"chapter_{chapter_index}_preview.pdf"
        if not p.exists():
            return 0
        try:
            doc = _fitz.open(str(p))
            n = doc.page_count
            doc.close()
            return n
        except Exception:
            return 0

    # 计算连续页码起始值：累加所有在目标章之前的章节页数（含章末空白页）
    page_start = 1
    for ch in all_chapters:
        if ch in chapters_to_render:
            break
        pages = _pdf_pages(ch["chapter_index"])
        page_start += pages + (1 if pages % 2 == 1 else 0)

    for ch in chapters_to_render:
        pdf = render_grouped_chapter_pdf(ch, book_config, paths, page_start=page_start)
        pages = _pdf_pages(ch["chapter_index"])
        page_start += pages + (1 if pages % 2 == 1 else 0)
        logger.info("✅ 章%d PDF → %s", ch["chapter_index"], pdf)


if __name__ == "__main__":
    main()
