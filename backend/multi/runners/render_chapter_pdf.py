"""
render_chapter_pdf — 把 chapter_for_book.json 渲染成单章 A5 PDF。

排版语义（B 端「一期 = 一章」）：
- chapter_title（整集名）= 真正的「章节扉页」：整页只有标题，居中、无衬线大字，
  扉页结束后 pagebreak，正文从下一页开始。
- section_title（每节小标题）= 居中、无衬线、中等字号、**不**独立成页。
- 正文 / 脚注 / 金句卡片 / 插图 / 页脚：复用 C 端 Typst 风格（Noto Serif CJK 衬线、
  首行缩进 2em、leading 1.4em、A5 + 3mm 出血、页脚阿拉伯页码 + 章名）。

输入：
    episodes/epNN/chapter_for_book.json
    （隐式依赖）episodes/epNN/illustrations/*.{jpg,png,webp}

输出：
    episodes/epNN/chapter_preview.pdf
    episodes/epNN/chapter_preview.typ      （Typst 源，方便排查）

用法：
    cd backend
    python -m workbench.runners.render_chapter_pdf --job possibility --only 11
    python -m workbench.runners.render_chapter_pdf --job possibility --only 11 --out /tmp/ep11.pdf
    python -m workbench.runners.render_chapter_pdf --job possibility --merge-book --copy-book-to-desktop
"""
from __future__ import annotations

from multi.env import setup
setup()

import argparse
import logging
import re
import sys
from pathlib import Path
from typing import Any

import typst as typst_lib

from multi.io import JobPaths, read_json

# 复用 C 端 typeset 节点的成熟工具函数（不重复造轮子）
from core.workflow.nodes.typeset import (
    FONTS_DIR,
    _escape_typst,
    _escape_typst_string,
    _split_footnotes,
    _process_inline,
    _process_blockquote,
    _parse_quote_attribution,
    _redistribute_illustrations,
    _extract_speaker,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("render_chapter_pdf")


# ================================================================
# Typst 模板（B 端单章版 — 与 C 端 typeset.py 同源但简化）
# ================================================================

# 关键差异（vs C 端）：
#  1. level-1 heading 改为「真正的扉页」：整页留白、垂直居中放章名，扉页结束后 pagebreak。
#     C 端是「换页 + 标题在新页顶 + 正文紧跟」，对 B 端单章太简陋。
#  2. 新增 level-2 heading：节小标题，居中 + 无衬线 + 中等字号 + 不独立成页。
#  3. 移除 cover / toc / epigraph / preface / summary 等整书级页面，
#     B 端单章直接「扉页 → 正文」两段式。
#  4. 页脚永远显示阿拉伯页码 + 章名（B 端没有「前置页」概念）。
TYPST_PREAMBLE = r"""
// PodBook 工作台 — 单章 PDF（B 端 render_chapter_pdf 生成）

// ── 调色板（与 C 端保持一致，便于审校风格统一） ──
#let clr-accent  = rgb("#6B3A2A")   // 棕色 — 标题、金句
#let clr-gold    = rgb("#C4A882")   // 暖金 — 装饰线
#let clr-body    = rgb("#2D2D2D")   // 深灰 — 正文（比纯黑更柔和）
#let clr-light   = rgb("#999999")   // 浅灰 — 页码、署名
#let clr-bg      = rgb("#FBF7F2")   // 暖白 — 卡片底色

// ── 印刷安全区（A5 成品 148×210mm + 3mm 四边出血） ──
#let trim-width = 148mm
#let trim-height = 210mm
#let bleed = 3mm
#let print-margin-top = bleed + 26mm
#let print-margin-bottom = bleed + 28mm
#let print-margin-outside = bleed + 20mm
#let print-margin-inside = bleed + 23mm

// ── 章名 / 章节编号（用于页脚和扉页） ─────────────────────
#let chapter-name = "CHAPTER_NAME_PLACEHOLDER"
#let chapter-num-padded = "CHAPTER_NUM_PADDED_PLACEHOLDER"
#let chapter-page-start = CHAPTER_PAGE_START_PLACEHOLDER

// ── 页面设置 ────────────────────────────
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
  // 脚注按页重新计数：Typst 官方建议在每页 header 中重置 footnote counter。
  // 这里不渲染任何可见页眉，只把 header 当作每页开始时的计数器钩子。
  header: context {
    counter(footnote).update(0)
  },
  footer: context {
    let phys = here().page()
    set text(fill: black)
    show linebreak: none

    // 页码：衬线 italic，与 C 端一致
    let pagenum = text(
      font: ("EB Garamond", "Palatino", "Baskerville", "Times New Roman", "Noto Serif CJK SC", "Noto Serif SC"),
      size: 10pt,
      style: "italic",
    )[#counter(page).display()]

    // 章名：无衬线 Regular
    let title-text = text(
      font: ("Noto Sans CJK SC", "Noto Sans SC"),
      size: 8.5pt,
    )[#chapter-name]

    let sep = text(font: ("Noto Sans CJK SC", "Noto Sans SC"), size: 8.5pt)[#h(0.6em)|#h(0.6em)]

    // 切口角悬挂 5mm，奇数页右下、偶数页左下（与 C 端逻辑一致）
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

// ── 字体：中文书籍版式（衬线正文） ─────────
#set text(
  font: ("Noto Serif CJK SC", "Noto Serif SC"),
  size: 10.5pt,
  lang: "zh",
  region: "cn",
  fill: clr-body,
)

#set par(leading: 1.4em, spacing: 1.8em, first-line-indent: 2em, justify: true)

// ── 脚注样式（黑色、紧凑） ──────────────
// 脚注序号用圆圈数字 ① ② ③(Unicode U+2460-U+2473,最多支持到 ⑳,即 20 条)。
// 当前一期 ≤ 3 条,完全够用。
#set footnote(numbering: "①")
// 脚注顶格:set footnote.entry(indent: 0em) 控制脚注序号前的整体左缩进(默认 1em);
// par 的 first-line-indent / hanging-indent 共同确保后续行也不缩进。
#set footnote.entry(indent: 0em)
#show footnote.entry: it => {
  set text(size: 8.5pt, fill: black)
  set par(leading: 0.9em, spacing: 0.8em, first-line-indent: 0em, hanging-indent: 0em)
  // 脚注 entry 默认把序号渲染为 super(上标小字号),圆圈数字本身已有视觉锚定不需要再上标;
  // 取 super.body 把它展平为 baseline 大字号,让 ① ② ③ 显眼一些。
  // 注意:`show super: text.with(...)` 只是把 super 包了一层 text,super 还在 → 仍上标。
  show super: it => text(baseline: 0em, size: 1em)[#it.body]
  it
}

// ── 章节扉页（level 1）──────────────────
// 极简版式：右上大数字 + 左侧章名，整页留白。
//   · 右上：大号黑色阿拉伯数字（无装饰文字）
//   · 左侧：章名左对齐、无衬线 ExtraBold，固定上边界，不固定下边界
//   · 用 place() 把两个元素分别绝对定位，保持空旷
#show heading.where(level: 1): it => {
  pagebreak(weak: true)
  set page(footer: none, header: none)
  set par(first-line-indent: 0em, leading: 1.2em)

  // —— 右上：大数字 ——
  place(
    top + right,
    dy: 2em,
  )[
    #text(
      font: ("Inter", "Noto Sans CJK SC"),
      size: 108pt,
      weight: "regular",
      fill: black,
      tracking: 0.24em,
    )[#chapter-num-padded]
  ]

  // —— 左侧：章名 ——
  // 固定上边界，而不是固定底边。标题变成两行或三行时只向下生长，
  // 不会把第一行顶到不可预期的位置；下方仍有充足印刷安全区。
  place(
    top + left,
    dy: 18em,
  )[
    // 右边界略收,与上方章节数字的视觉右边界对齐。
    #block(width: 95%)[
      #it.body
      CHAPTER_CAST_BLOCK_PLACEHOLDER
    ]
  ]

  // 占位让本页有内容（pagebreak 之前需要至少一个 block，否则空 heading 会被吞掉）
  v(1pt)

  // 章节扉页后加一页全空白页，让标题页和空白页占据一张纸的正反面。
  // 正文从下一张纸开始，方便后续装订和人工精修。
  pagebreak(weak: false)
  set page(footer: none, header: none)
  v(1pt)

  pagebreak(weak: false)
}

// ── 节小标题（level 2）──────────────────
// 居中 + 无衬线 + 中等字号 + 不独立成页。
// above/below 显式覆盖 par.spacing(1.8em),否则下间距实际是 par.spacing 而非 v() 的值。
#show heading.where(level: 2): it => {
  set par(first-line-indent: 0em)
  block(width: 100%, above: 2.6em, below: 0.4em)[
    #align(center)[
      #text(
        font: ("Source Han Sans SC", "Hiragino Sans GB", "PingFang SC", "Noto Sans CJK SC", "Noto Sans SC"),
        size: 13pt,
        weight: "bold",
        tracking: 0.05em,
        fill: black,
      )[#it.body]
    ]
  ]
}

// ── 拉引式金句（与 C 端一致） ──────────
#let pull-quote(body) = {
  v(1.5em)
  block(
    width: 100%,
    inset: (left: 1.5em, right: 1em, y: 0.8em),
    stroke: (left: 3pt + clr-gold),
  )[
    #set text(font: ("EB Garamond", "FandolKai"), size: 11.5pt, fill: clr-accent)
    #set par(first-line-indent: 0em, leading: 1.5em)
    
    #body
  ]
  v(1.5em)
}

// ── 正文插图（与 C 端一致） ────────────
#let max-illustration-height = 6.0cm  // ≈正文可用高度 15.6cm 的 38%

#let book-illustration(path, caption: none, img-width: 85%) = {
  v(1.5em)
  align(center)[
    #set par(first-line-indent: 0em)
    #block(
      width: img-width,
      breakable: false,
    )[
      #layout(size => {
        let full-img = image(path, width: size.width)
        let m = measure(full-img)
        if m.height > max-illustration-height {
          image(path, height: max-illustration-height)
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

// ── 章末花（与 C 端一致） ──────────────
#let chapter-end() = {
  v(2em)
}
"""


# ================================================================
# 工具：把 highlights / illustrations 按 section 分组
# ================================================================

_SECTION_PREFIX_RE = re.compile(r"^第\s*\d+\s*节\s*[·:：\-—]?\s*")


def _group_by_section(items: list[dict], sections: list[dict]) -> dict[int, list[dict]]:
    """把 highlights/illustrations 按归属小节分组。

    匹配优先级（容错三层，应对不同 LLM 输出习惯）：
        1. `it["section_index"]`（SSOT，由 global_k._normalize_items 写入）
        2. `it["chapter_title"]` 完全等于某 section_title
        3. `it["chapter_title"]` 去掉 "第 N 节 · " 前缀后等于某 section_title
           （兜底 LLM 复读 _build_full_text 的标记前缀）
    """
    by_idx: dict[int, list[dict]] = {}
    valid_indices: set[int] = {s.get("section_index", 0) for s in sections}
    title_to_idx: dict[str, int] = {
        (s.get("section_title") or "").strip(): s.get("section_index", 0)
        for s in sections
    }
    for it in items:
        sec_idx = it.get("section_index")
        if not (isinstance(sec_idx, int) and sec_idx in valid_indices):
            ch_title = (it.get("chapter_title") or "").strip()
            sec_idx = title_to_idx.get(ch_title)
            if sec_idx is None and ch_title:
                stripped = _SECTION_PREFIX_RE.sub("", ch_title).strip()
                sec_idx = title_to_idx.get(stripped)
        if sec_idx is None:
            continue
        by_idx.setdefault(sec_idx, []).append(it)
    return by_idx


def _parse_para_pos(raw: Any) -> int | None:
    """容忍 'P3' / '3' / 3 三种 after_paragraph 写法。"""
    if raw is None:
        return None
    if isinstance(raw, int):
        return raw
    s = str(raw).strip()
    if s.upper().startswith("P"):
        s = s[1:]
    try:
        return int(s)
    except ValueError:
        return None


# ================================================================
# 单节 → Typst 片段
# ================================================================

_SIZE_TO_WIDTH = {"small": "20%", "medium": "35%", "large": "50%"}

# 剥脚注开头的"编者注:""注:""按:"等前缀(支持 **xxx**: 和 xxx: 两种写法)。
# 这种前缀对客观事实型脚注是冗余的,统一删掉,保持脚注像百科条目而非编者评论。
_FOOTNOTE_PREFIX_RE = re.compile(
    r"^\s*(\*\*)?(编者注|编者按|译者注|译注|按|注)(\*\*)?\s*[：:]\s*",
)


def _strip_footnote_prefix(text: str) -> str:
    """把脚注开头的'编者注:'类前缀剥掉,首字母还原为大写(英文情况)。"""
    if not text:
        return text
    return _FOOTNOTE_PREFIX_RE.sub("", text, count=1).lstrip()

# 图注长度由 prompt(workbench/prompts/illustration_global_k/self_growth.py)约束 ≤18 字保证单行,
# render 阶段不做硬截断:保留完整意思优先于"严格不换行"。如果未来 LLM 不听话,
# 应当在产出阶段加二次 LLM 重写(保意思的同时压缩字数),而不是这里强行砍。


def _build_section(
    section: dict[str, Any],
    quotes: list[dict],
    illustrations: list[dict],
    *,
    emitted_footnotes: set[int],
    emitted_footnote_texts: set[str],
    illustrations_dir_rel: str,
) -> str:
    """渲染单个 section 为 Typst 源（== 小标题 + 段落 + 节末插入物）。"""
    title = (section.get("section_title") or "").strip()
    raw_content = section.get("content") or ""

    body, content_footnotes = _split_footnotes(raw_content)

    # 兜底剥脚注前缀:既然 prompt 已规定脚注必须是客观事实,"**编者注**:"/"编者注:"/"注:"
    # 这种前缀是冗余的,统一删掉。LLM 偶尔不听话也保证视觉一致。
    content_footnotes = {
        num: _strip_footnote_prefix(text)
        for num, text in content_footnotes.items()
    }

    # 合并 section.footnotes（结构化）到 content_footnotes（行内）
    # B 端 assembler 既保留行内 [^N] 又有结构化 footnotes 数组；优先使用结构化版本，
    # 因为 DeepSeek 偶尔会把 footnote 文本和正文挤在一起，结构化版本更干净。
    structured_footnotes = section.get("footnotes") or []
    for fn in structured_footnotes:
        num = fn.get("number") or fn.get("id")
        text = _strip_footnote_prefix((fn.get("text") or "").strip())
        if num and text and num not in content_footnotes:
            content_footnotes[int(num)] = text

    # 切段并丢掉 markdown 水平分隔线段（独立行的 ---、***、___ ——
    # LLM 在 compose 阶段偶尔会用它做小话题分隔，但 Typst 会把 `---` 当
    # em-dash 连字渲染成一条长横线，造成视觉噪音）。
    def _is_md_hr(p: str) -> bool:
        s = p.strip()
        if len(s) < 3:
            return False
        return all(c == s[0] for c in s) and s[0] in {"-", "*", "_"}

    paragraphs = [
        p.strip() for p in body.split("\n\n")
        if p.strip() and not _is_md_hr(p.strip())
    ]

    processed: list[str] = []
    # 平行数组：从原始段落提取的说话人(用于"切换说话人加大段间距")。
    # _process_inline 已把 speaker 渲染为 #text(font:...)[Speaker：],但提取一次原文更稳。
    para_speakers: list[str | None] = []
    for para in paragraphs:
        if para.startswith(">"):
            processed.append(_process_blockquote(para, content_footnotes))
            para_speakers.append(None)
        else:
            sp, _rest = _extract_speaker(para)
            para_speakers.append(sp)
            processed.append(_process_inline(
                para,
                content_footnotes,
                emitted_footnotes,
                emitted_footnote_texts,
            ))

    # 节内 highlights / illustrations 按 after_paragraph 插入。
    # Prompt 约定 after_paragraph 是 1-based："插在第 K 段之后"；
    # 这里的 processed 循环是 0-based，所以入表前要转成 K - 1。
    insertions: dict[int, list[str]] = {}
    for q in quotes:
        if q.get("placement") not in (None, "", "inline_card"):
            continue
        pos = _parse_para_pos(q.get("after_paragraph"))
        if pos is None:
            continue
        pos = max(0, pos - 1)
        q_text, _q_speaker = _parse_quote_attribution(q.get("text", ""))
        if not q_text:
            continue
        insertions.setdefault(pos, []).append(
            f'#pull-quote[{_escape_typst(q_text)}]'
        )

    for ill in illustrations:
        pos = _parse_para_pos(ill.get("after_paragraph"))
        if pos is None:
            continue
        pos = max(0, pos - 1)
        filename = ill.get("filename") or ""
        if not filename:
            continue
        # B 端 illustrations.json 中 filename 已经是相对 ep_dir 的路径
        # （如 "illustrations/search_0_0.jpg"），不要重复拼前缀。
        # 容错：如果是裸文件名，自动补 illustrations/ 前缀。
        if "/" in filename or filename.startswith(illustrations_dir_rel):
            rel_path = filename
        else:
            rel_path = f"{illustrations_dir_rel}/{filename}"
        # 图注末尾标点剥离:caption 风格是"短标识、无句号",
        # LLM 偶尔会带 。!?.;,即使带了也统一剥掉,保持一致风格。
        caption = (ill.get("caption", "") or "").strip().rstrip("。．.；;！!？?，,、 ")
        size = ill.get("size", "")
        width_param = _SIZE_TO_WIDTH.get(size, "")
        parts = [f'"{rel_path}"']
        if caption:
            parts.append(f'caption: "{_escape_typst_string(caption)}"')
        if width_param:
            parts.append(f"img-width: {width_param}")
        insertions.setdefault(pos, []).append(
            f'#book-illustration({", ".join(parts)})'
        )

    _redistribute_illustrations(insertions, len(processed))

    after_insertion: set = set()
    for pos in insertions:
        after_insertion.add(pos + 1)

    parts: list[str] = []
    title_escaped = _escape_typst(title) if title else "（未命名）"
    parts.append(f"== {title_escaped}\n")
    parts.append("#par[]")  # 让首段缩进设置生效

    # 说话人切换时给段前加额外垂直间距(par.spacing 默认 1.8em,加 0.6em 让对话感更清晰)。
    # 规则:仅当"上一段有 speaker A、当前段有 speaker B、A != B"时插入;
    #     连续相同 speaker 的多段、或中间穿插 None(叙述段)都不插入。
    SPEAKER_SWITCH_GAP = "#v(0.6em)"
    last_speaker: str | None = None

    for i, para in enumerate(processed):
        if para:
            cur_speaker = para_speakers[i] if i < len(para_speakers) else None
            if (
                cur_speaker
                and last_speaker
                and cur_speaker != last_speaker
            ):
                parts.append(SPEAKER_SWITCH_GAP)
            if cur_speaker:
                last_speaker = cur_speaker

            if i in after_insertion:
                parts.append(f"#par(first-line-indent: 0em)[#h(2em){para}]")
            else:
                parts.append(f"#par(first-line-indent: 2em)[{para}]")
        for ins in insertions.get(i, []):
            parts.append(ins)

    return "\n\n".join(parts)


# ================================================================
# 单章 → Typst 源
# ================================================================

# 中英文冒号、破折号(双/单)。用 unicode escape 显式列出,避免 IDE/编辑器
# 误把全角和半角折叠为同一字符。
# 注意：逗号不作为章扉页硬分行点；逗号常用于一句内部并列动作,
# 例如"交还课题，拿回解释权"，硬切会破坏标题节奏。
_CHAPTER_TITLE_SEPARATORS = (
    "\uFF1A",   # ：fullwidth colon
    ":",        # ASCII colon
    "\u2014\u2014",  # —— double em-dash
    "\u2014",   # — single em-dash
)
_CHAPTER_TITLE_SEPARATOR_RE = re.compile(
    r"(\u2014\u2014|[\uFF1A:\u2014])"
)


def _format_chapter_title_for_display(title: str) -> str:
    """把章名按分隔符排成等字号 Typst markup。

    分隔符**保留**在它前一行的末尾,例如 "读《一生之敌》:内阻力与心流之间"
    会被切成两行: "读《一生之敌》:" 和 "内阻力与心流之间"。
    三行可接受；如果人工分行后很可能超过三行,则取消人工分行,
    交给 Typst 自然换行,通常会比硬切更少行。
    """
    if not title:
        return _escape_typst(title)

    tokens = _CHAPTER_TITLE_SEPARATOR_RE.split(title)
    seps = set(_CHAPTER_TITLE_SEPARATORS)
    lines: list[str] = []
    cur = ""
    for tok in tokens:
        if not tok:
            continue
        cur += tok
        if tok in seps:
            lines.append(cur.strip())
            cur = ""
    if cur.strip():
        lines.append(cur.strip())

    font_chain = (
        '"Source Han Sans SC", "Hiragino Sans GB", "PingFang SC", '
        '"Noto Sans CJK SC", "Noto Sans SC"'
    )

    display_lines = lines if len(lines) > 1 else [title]
    # A5 版心 + 28pt 粗体下,每行大约容纳 10-11 个中文字符。
    # 如果人工分行后预计超过三行,不要硬按冒号/破折号切分。
    approx_lines = sum(max(1, (len(line) + 9) // 10) for line in display_lines)
    if approx_lines > 3:
        display_lines = [title]
    rendered = [
        (
            f'#text(font: ({font_chain}), size: 28pt, weight: "extrabold", '
            f'fill: black, tracking: 0.02em)[{_escape_typst(line)}]'
        )
        for line in display_lines
    ]
    return " \\\n".join(rendered)


def _normalize_name_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [part.strip() for part in re.split(r"[、,，/]+", value) if part.strip()]
    if isinstance(value, list):
        out: list[str] = []
        for item in value:
            if isinstance(item, str):
                out.extend(_normalize_name_list(item))
        return out
    return []


def _format_chapter_cast_for_display(chapter: dict[str, Any]) -> str:
    """章节扉页的主播/嘉宾信息，左对齐显示在章名下方。"""
    hosts = _normalize_name_list(chapter.get("host_name"))
    guests = _normalize_name_list(chapter.get("guest_names"))
    if not hosts and not guests:
        return ""

    font_chain = '"Noto Serif CJK SC", "Noto Serif SC", "Source Han Serif SC", "Songti SC"'
    lines: list[str] = []
    if hosts:
        lines.append(f"主播 / {'、'.join(hosts)}")
    if guests:
        lines.append(f"嘉宾 / {'、'.join(guests)}")

    rendered_lines = []
    for i, line in enumerate(lines):
        if i:
            rendered_lines.append("#v(0.35em)")
        rendered_lines.append(
            f'#text(font: ({font_chain}), size: 12.5pt, fill: black, '
            f'tracking: 0.03em)[{_escape_typst(line)}]'
        )
    return "\n#v(1.35em)\n" + "\n".join(rendered_lines)


def build_chapter_typst(
    chapter: dict[str, Any],
    *,
    illustrations_dir_rel: str = "illustrations",
    page_start: int = 1,
) -> str:
    chapter_title = (chapter.get("chapter_title") or "").strip() or "未命名章节"
    sections = chapter.get("sections") or []
    highlights = chapter.get("highlights") or []
    illustrations = chapter.get("illustrations") or []

    chapter_index = int(chapter.get("chapter_index") or 1)
    chapter_num_padded = f"{chapter_index:02d}"

    # 排除 epigraph 类型（B 端约定题记由 book-level 处理，不进单章）
    inline_quotes = [q for q in highlights if q.get("placement") != "epigraph"]

    quotes_by_section = _group_by_section(inline_quotes, sections)
    images_by_section = _group_by_section(illustrations, sections)

    preamble = (
        TYPST_PREAMBLE
        .replace("CHAPTER_NAME_PLACEHOLDER", _escape_typst_string(chapter_title))
        .replace("CHAPTER_NUM_PADDED_PLACEHOLDER", chapter_num_padded)
        .replace("CHAPTER_PAGE_START_PLACEHOLDER", str(max(1, int(page_start))))
        .replace("CHAPTER_CAST_BLOCK_PLACEHOLDER", _format_chapter_cast_for_display(chapter))
    )

    blocks: list[str] = [preamble]
    blocks.append("#counter(page).update(chapter-page-start)")

    # 章节扉页（level 1）—— 整页只有这个标题
    # 章名按符号(冒号/逗号/破折号)分行,每行作为独立显示行。
    # 用函数式 #heading(...) 写法（而不是 `= ...`）才能接受多行 content body：
    # `= title` 语法是单行的，遇到 markup linebreak (\\) 会提前结束 heading，
    # 第二行被当成普通正文渲染到下一页。
    chapter_title_display = _format_chapter_title_for_display(chapter_title)
    blocks.append(f"#heading(level: 1)[{chapter_title_display}]\n")

    # 跨节共享的 footnote 状态，避免同一脚注被多次渲染
    emitted_footnotes: set[int] = set()
    emitted_footnote_texts: set[str] = set()

    for s in sections:
        idx = s.get("section_index", 0)
        section_typst = _build_section(
            s,
            quotes_by_section.get(idx, []),
            images_by_section.get(idx, []),
            emitted_footnotes=emitted_footnotes,
            emitted_footnote_texts=emitted_footnote_texts,
            illustrations_dir_rel=illustrations_dir_rel,
        )
        blocks.append(section_typst)

    blocks.append("#chapter-end()")

    return "\n\n".join(blocks)


# ================================================================
# 编译
# ================================================================

def render_chapter_pdf(
    idx: int,
    ep_dir: Path,
    *,
    out_pdf: Path | None = None,
    page_start: int = 1,
) -> Path:
    """编译单集 PDF。返回 PDF 路径。"""
    chapter_path = ep_dir / "chapter_for_book.json"
    if not chapter_path.exists():
        raise FileNotFoundError(
            f"未找到 chapter_for_book.json：{chapter_path}\n"
            f"请先跑 run_pipeline / extract_highlights / search_illustrations。"
        )
    chapter = read_json(chapter_path)

    typst_source = build_chapter_typst(chapter, page_start=page_start)

    typ_path = ep_dir / "chapter_preview.typ"
    typ_path.write_text(typst_source, encoding="utf-8")

    if out_pdf is None:
        out_pdf = ep_dir / "chapter_preview.pdf"
    out_pdf.parent.mkdir(parents=True, exist_ok=True)

    typst_lib.compile(
        str(typ_path),
        output=str(out_pdf),
        font_paths=[FONTS_DIR],
    )

    return out_pdf


# ================================================================
# CLI
# ================================================================

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


def main() -> None:
    p = argparse.ArgumentParser(description="渲染单章 A5 PDF（B 端 chapter_for_book → PDF）")
    p.add_argument("--job", required=True)
    p.add_argument(
        "--only",
        default="",
        help="只渲染指定单集，例：11 或 1,5,11；省略则渲染所有有 chapter_for_book.json 的单集",
    )
    p.add_argument("--out", default="", help="输出 PDF 路径（仅 --only 为单集时生效）")
    p.add_argument("--page-start", type=int, default=1, help="该单章 PDF 的起始物理页码（整书连续页码用）")
    p.add_argument("--merge-book", action="store_true", help="渲染完成后合并整书 PDF（要求所有单章 PDF 已齐）")
    p.add_argument("--copy-book-to-desktop", action="store_true", help="合并整书后同时复制到桌面")
    args = p.parse_args()

    paths = JobPaths.open(args.job)
    if not paths.episodes_index_json.exists():
        raise SystemExit(f"未找到 episodes_index.json：{paths.root}")

    index = read_json(paths.episodes_index_json)
    only = _parse_only(args.only)

    rendered = 0
    for ep in index.get("episodes", []):
        idx = ep.get("index")
        if only is not None and idx not in only:
            continue
        ep_dir = paths.episode_dir(idx)
        chapter_path = ep_dir / "chapter_for_book.json"
        if not chapter_path.exists():
            logger.info("[ep%02d] 跳过（无 chapter_for_book.json）", idx)
            continue

        if args.out and only is not None and len(only) == 1:
            out_pdf = Path(args.out).expanduser().resolve()
        else:
            out_pdf = ep_dir / "chapter_preview.pdf"

        try:
            pdf_path = render_chapter_pdf(idx, ep_dir, out_pdf=out_pdf, page_start=args.page_start)
        except Exception as e:
            logger.error("[ep%02d] ❌ 渲染失败: %s", idx, e)
            raise

        size_kb = pdf_path.stat().st_size / 1024
        logger.info("[ep%02d] ✅ PDF 渲染完成 (%.0f KB) → %s", idx, size_kb, pdf_path)
        rendered += 1

    logger.info("完成：渲染 %d 集 PDF", rendered)

    if args.merge_book:
        from multi.runners.merge_book_pdf import merge_book_pdf
        merge_book_pdf(
            paths=paths,
            copy_desktop=args.copy_book_to_desktop,
        )


if __name__ == "__main__":
    main()
