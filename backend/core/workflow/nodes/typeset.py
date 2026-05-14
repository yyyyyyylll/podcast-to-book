"""
节点：排版（Typeset）

将注释后的书稿 + 精华提炼渲染为可打印的 PDF 文件。
使用 Typst 排版引擎生成适配印厂参数的 A5 成品 PDF（含出血）。

内容转换流程：
  annotated_content.chapters → 解析脚注 → 转义 → 插入 pull-quote / 插图 → Typst 源码
  highlights.quotes[epigraph] → 题记页
  highlights.quotes[inline_card] → 正文内拉引式金句
  illustrations.images → 正文内插图

输入：annotated_content, highlights, illustrations
输出：pdf_path, typst_source
"""

import re
import tempfile
from pathlib import Path
from typing import Dict, Any, List, Tuple, Optional

import httpx

from core.workflow.state import PodBookState, WorkflowStage
from core.config import settings

FONTS_DIR = str(Path(__file__).resolve().parent.parent / "knowledge" / "assets" / "fonts")


# ================================================================
# Typst 排版模板
# ================================================================

TYPST_PREAMBLE = r"""
// PodBook 自动排版 — 由 typeset_node 生成

// ── 调色板 ──────────────────────────────
#let clr-accent  = rgb("#6B3A2A")   // 棕色 — 标题、金句
#let clr-gold    = rgb("#C4A882")   // 暖金 — 装饰线
#let clr-deep-gold = rgb("#B8860B") // 深金 — 章节英文
#let clr-body    = rgb("#2D2D2D")   // 深灰 — 正文（比纯黑更柔和）
#let clr-light   = rgb("#999999")   // 浅灰 — 页码、署名
#let clr-bg      = rgb("#FBF7F2")   // 暖白 — 卡片底色
#let clr-cover   = rgb("#5C1A1B")   // 暗红 — 封面背景
#let clr-cover-gold = rgb("#D4AF37") // 封面金 — 封面标题

// ── 书名 / 栏目名变量（用于页眉） ───────────
#let book-title = "BOOK_TITLE_PLACEHOLDER"
#let podcast-name = "PODCAST_NAME_PLACEHOLDER"

// ── 前置/正文 状态 ───────────────────────
#let is-front-matter = state("front-matter", true)

// 跨节强制对齐到奇数页时，Typst 会自动插入一张空白 padding 页；
// 这张页会继承当前的 footer 设置，从而在视觉上变成"几乎空白但带页脚"。
// 用这个 state 包裹 `pagebreak(to: "odd")`，让 footer 在 padding 页里直接 return，
// 达到印刷惯例上的"完全空白"效果。
#let is-pad-page = state("pad-page", false)

// ── 印刷安全区（148×210mm 成品 + 四边 3mm 出血） ──
// 版心距裁切线：天头 26mm、地脚 28mm、切口 20mm、订口 23mm。
// 参照主流出版社 A5 图书惯例：地脚 ≥ 天头（视觉重心偏上不坠落，
// 因页码在角落不在居中，天地可接近等高），订口 > 切口（补偿胶装约
// 2–3mm 装订损失，装订后视觉对称）。版心 105×156mm 约 1:1.49。
#let trim-width = 148mm
#let trim-height = 210mm
#let bleed = 3mm
#let safe-top = 20mm
#let safe-bottom = 20mm
#let safe-outside = 18mm
#let safe-inside = 20mm
#let print-margin-top = bleed + 26mm
#let print-margin-bottom = bleed + 28mm
#let print-margin-outside = bleed + 20mm
#let print-margin-inside = bleed + 23mm

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
  // 页脚位置计算（A5 + 3mm 出血 = 154×216mm，地脚 31mm = 3mm 出血 + 28mm）：
  //   正文底边 → 裁切线：28mm
  //   footer-descent: 10mm → 页脚基线 = 正文底 + 10mm → 基线距裁切线 18mm
  //   italic descender ≈ 3mm → 最低像素距裁切线 ≥15mm
  //   远大于印厂 5mm 安全阈值，符合主流书籍页码 12–15mm 的视觉惯例。
  footer-descent: 10mm,
  numbering: "i",
  header: none,  // 顶部不设页眉，页码与章名合并到页脚切口角
  footer: context {
    // 页脚显示策略：
    //   - 封面 / 题记页 / 空白页：在各自的 `#page(footer: none)` 中已显式关掉，这里不会被调用；
    //   - 编者序 / 精华提要 / 目录：保留页脚，显示罗马小写页码与本页标题；
    //   - 正文：显示阿拉伯页码与最近的章节名。
    // 因此不再使用 `is-front-matter.get()` 提前 return 的分支。

    // 跨节强制奇数页时自动插入的 padding 空白页：保持完全空白，不打页脚。
    if is-pad-page.get() { return }

    // 对齐必须看物理页（here().page()）而不是逻辑页码（counter），
    // 因为 counter(page).update(1) 会重置逻辑计数器，但物理左右页是由装订决定的：
    // 奇数物理页 = 右页（recto），切口在右 → 页脚靠右
    // 偶数物理页 = 左页（verso），切口在左 → 页脚靠左
    let phys = here().page()
    let chapters = query(selector(heading.where(level: 1)).before(here()))
    let show-title = chapters.len() > 0

    set text(fill: black)
    show linebreak: none

    // 页码：衬线、Regular、斜体。字体优先级：
    //   EB Garamond（bundle 里的开源字体，在 Linux 和 Mac 上都可用，有真 italic 字形）→
    //   macOS 系统字体（本地备用）→ Noto Serif CJK SC（最终 fallback，但无真 italic）。
    let pagenum = text(
      font: ("EB Garamond", "Palatino", "Baskerville", "Times New Roman", "Noto Serif CJK SC", "Noto Serif SC"),
      size: 10pt,
      style: "italic",
    )[#counter(page).display()]

    // 章名：非衬线、Regular 字重。
    //   注意链首放 Noto Sans CJK SC 而不是 Source Han Sans SC：bundle 里 SHS
    //   只有 Bold，若放在链首 Typst 会把它当成"最接近"的字重用掉，结果仍是粗体。
    let title-text = text(
      font: ("Noto Sans CJK SC", "Noto Sans SC"),
      size: 8.5pt,
    )[#chapters.last().body]

    // 分隔符：与页脚整体同字体同字重
    let sep = text(font: ("Noto Sans CJK SC", "Noto Sans SC"), size: 8.5pt)[#h(0.6em)|#h(0.6em)]

    // 页脚悬挂：向外切口延伸 5mm，比正文切口边距明显靠外。
    // 用 h(1fr) 把内容推到另一端，再用负向 h(-hang) 把内容拽出文本区边界，
    // 直接挤到切口角。这样比 move + align 更可靠，不受容器宽度推断影响。
    let hang = 5mm

    if calc.even(phys) {
      // 偶数页（verso / 左页）：页码在左下角切口，整段向左悬挂
      h(-hang)
      if show-title {
        [#pagenum#sep#title-text]
      } else {
        pagenum
      }
      h(1fr)
    } else {
      // 奇数页（recto / 右页）：页码在右下角切口，整段向右悬挂
      h(1fr)
      if show-title {
        [#title-text#sep#pagenum]
      } else {
        pagenum
      }
      h(-hang)
    }
  },
)

// ── 字体：中文书籍版式 ─────────────────────
#set text(
  font: ("Noto Serif CJK SC", "Noto Serif SC"),
  size: 10.5pt,
  lang: "zh",
  region: "cn",
  fill: clr-body,
)

#set par(leading: 1.4em, spacing: 1.8em, first-line-indent: 2em, justify: true)
#let cn-chapter-nums = ("一", "二", "三", "四", "五", "六", "七", "八", "九", "十", "十一", "十二", "十三", "十四", "十五", "十六", "十七", "十八", "十九", "二十")
#set heading(numbering: (..nums) => {
  let n = nums.pos().first()
  box(width: 4em)[第#cn-chapter-nums.at(n - 1)章]
})

// ── 脚注样式 ────────────────────────────
// 脚注序号用圆圈数字，脚注文本顶格、紧凑。
#set footnote(numbering: "①")
#set footnote.entry(indent: 0em)
#show footnote.entry: it => {
  set text(size: 8.5pt, fill: black)
  set par(leading: 0.9em, spacing: 0.8em, first-line-indent: 0em, hanging-indent: 0em)
  show super: it => text(baseline: 0em, size: 1em)[#it.body]
  it
}

// ── 章节标题 ────────────────────────────
// 经典书籍章节扉页：紧凑型古典居中
#show heading.where(level: 1): it => {
  context {
    if is-front-matter.get() {
      // 前置页 heading 仅用于出现在目录中，不渲染
    } else {
      pagebreak(weak: true)
      v(0.01cm)
      align(center)[
        #set par(first-line-indent: 0em)
        
        // "CHAPTER – I"（同一行；与中文章节标题使用同一字体，统一字重避免 glyph 回退造成视觉差异）
        #{
          set text(
            fill: black,
            font: ("Source Han Sans SC", "Hiragino Sans GB", "PingFang SC", "Noto Sans CJK SC", "Noto Sans SC"),
            size: 12pt,
            weight: "bold",
          )
          text(tracking: 0.32em)[CHAPTER]
          h(0.5em)
          text[–]
          h(0.5em)
          text[#counter(heading).display("I")]
        }

        #v(0.12cm)

        // 章节标题（Hiragino Sans GB ExtraBold、黑色）
        #text(font: ("Source Han Sans SC", "Hiragino Sans GB", "PingFang SC", "Noto Sans CJK SC", "Noto Sans SC"), size: 13.5pt, weight: "extrabold", fill: black)[#it.body]
        
      ]
      v(0.12cm)
    }
  }
}

// ── 拉引式金句 (引用块竖线风格) ──────────────
#let pull-quote(body, speaker: none) = {
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

// ── 编号步骤卡 ──────────────────────────
#let step-card(title, steps) = {
  v(1.2em)
  block(
    width: 100%,
    breakable: false,
    inset: (x: 1.5em, y: 1.2em),
    radius: 6pt,
    fill: clr-bg,
    stroke: (left: 3pt + clr-accent, rest: 0.5pt + clr-gold),
  )[
    #set par(first-line-indent: 0em)
    #text(weight: "bold", size: 11pt, fill: clr-accent)[#title]
    #v(0.6em)
    #for (i, step) in steps.enumerate() [
      #text(weight: "bold", fill: clr-accent)[#str(i + 1).] #step \
    ]
  ]
  v(1.2em)
}

// ── 正文插图 ────────────────────────────
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

// ── 章节尾花 ────────────────────────────
#let chapter-end() = {
  v(2em)
}

"""


# ================================================================
# 文本转换工具
# ================================================================

def _escape_typst(text: str) -> str:
    """转义 Typst content mode 中的特殊字符。"""
    text = text.replace("\\", "\\\\")
    text = text.replace("#", "\\#")
    text = text.replace("$", "\\$")
    text = text.replace("@", "\\@")
    text = text.replace("<", "\\<")
    text = text.replace(">", "\\>")
    text = text.replace("~", "\\~")
    return text


def _escape_typst_string(text: str) -> str:
    """转义 Typst 字符串字面量 ("...") 中的特殊字符。"""
    text = text.replace("\\", "\\\\")
    text = text.replace('"', '\\"')
    return text


def _md_bold_to_typst(text: str) -> str:
    """将 Markdown **bold** 转为 Typst *bold*。"""
    return re.sub(r'\*\*(.+?)\*\*', r'*\1*', text)


def _strip_md_emphasis(text: str) -> str:
    """移除常见 Markdown 强调标记（粗体/斜体），保留纯文本。"""
    text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)
    text = re.sub(r'__(.+?)__', r'\1', text)
    text = re.sub(r'(?<!\*)\*([^*\n]+)\*(?!\*)', r'\1', text)
    text = re.sub(r'(?<!_)_([^_\n]+)_(?!_)', r'\1', text)
    return text


def _split_footnotes(content: str) -> Tuple[str, Dict[int, str]]:
    """将章节内容拆分为正文 + 脚注定义字典。"""
    match = re.search(r'\n\n---\n+(\[\^\d+\]:.+)$', content, re.DOTALL)
    if match:
        body = content[:match.start()]
        fn_section = match.group(1)
    else:
        body = content
        fn_section = ""

    footnotes: Dict[int, str] = {}
    for fn_match in re.finditer(
        r'\[\^(\d+)\]:\s*(.+?)(?=\n\[\^|\Z)', fn_section, re.DOTALL
    ):
        num = int(fn_match.group(1))
        text = fn_match.group(2).strip()
        footnotes[num] = text

    return body, footnotes


_FOOTNOTE_PREFIX_RE = re.compile(
    r"^\s*(\*\*)?(编者注|编者按|译者注|译注|按|注)(\*\*)?\s*[：:]\s*",
)


def _strip_footnote_prefix(text: str) -> str:
    """Remove redundant editor-note prefixes before rendering footnotes."""
    if not text:
        return text
    return _FOOTNOTE_PREFIX_RE.sub("", text, count=1).lstrip()


def _parse_quote_attribution(text: str) -> Tuple[str, str]:
    """从金句文本中分离引文和说话人。

    输入: '「不要在海面上修灯塔」 —— 张帆'
    输出: ('不要在海面上修灯塔', '张帆')

    当 —— 后面的部分过长（>15字）时视为正文内容而非署名。
    """
    text = text.strip().strip("「」""")
    parts = re.split(r'\s*——\s*|\s*--\s*', text, maxsplit=1)
    quote = parts[0].strip().strip("「」""")
    candidate = parts[1].strip() if len(parts) > 1 else ""
    if candidate and len(candidate) <= 15:
        return quote, candidate
    if candidate:
        quote = text
    return quote, ""


# ================================================================
# 段落处理
# ================================================================

_FN_PLACEHOLDER_PREFIX = "\x00FN"
_FN_PLACEHOLDER_SUFFIX = "\x00"


def _extract_speaker(text: str) -> Tuple[Optional[str], str]:
    """从段落开头提取说话人名。

    兼容 `**Speaker:**`、`**Speaker**:`、`Speaker:` 等格式。
    对 `Speaker Name:` 这类占位标签，尝试从正文开头恢复真实姓名。
    """
    stripped = text.strip()
    for pattern in (
        r'^\*\*(.+?)[：:]\*\*\s*(.*)$',
        r'^\*\*(.+?)\*\*[：:]\s*(.*)$',
        r'^([^:\n：]{1,40})[：:]\s+(.*)$',
    ):
        m = re.match(pattern, stripped)
        if not m:
            continue
        name = m.group(1).strip()
        rest = m.group(2).strip()
        if re.match(r'^(说话人\d*|Speaker\s*\d*)$', name, re.IGNORECASE):
            return None, text
        if re.match(r'^(Speaker\s*Name|说话人名字|Speaker)$', name, re.IGNORECASE):
            actual = re.match(
                r'^([A-Z][A-Za-z\'’.\-]+(?:\s+[A-Z][A-Za-z\'’.\-]+){0,3}|[\u4e00-\u9fff]{2,8})\s+(.*)$',
                rest,
            )
            if actual:
                return actual.group(1).strip(), actual.group(2).strip()
            return None, rest or text
        if rest.lower().startswith(name.lower()):
            rest = rest[len(name):].lstrip(" ,.:;：")
        return name, rest
    return None, text


_NON_PERSON_SPEAKERS = re.compile(
    r'^(观众|听众|现场观众|观众提问|听众提问|audience|unknown|未知)$',
    re.IGNORECASE,
)


def _clean_speakers(
    speakers: List[Dict],
    transcription_segments: Optional[List[Dict]] = None,
) -> List[Dict]:
    """清洗 speakers 列表：过滤非人名条目、去除重复名称变体。

    处理三类问题：
    1. 非真实人名（如"观众""听众"）被当作嘉宾
    2. 同一人以不同名称出现（如"杨远骋"和"杨远骋Koji"）
    3. 播客片头/片尾等微量说话人（如"声音碎片"）被当作嘉宾
    """
    if not speakers:
        return speakers

    # 0) 如果有转写数据，计算各说话人内容占比，标记微量说话人
    trivial_from_transcript: set = set()
    if transcription_segments:
        total_chars = sum(len(seg.get("text", "")) for seg in transcription_segments)
        if total_chars > 0:
            speaker_chars: Dict[str, int] = {}
            for seg in transcription_segments:
                sp = seg.get("speaker", "")
                speaker_chars[sp] = speaker_chars.get(sp, 0) + len(seg.get("text", ""))
            trivial_from_transcript = {
                sp for sp, chars in speaker_chars.items()
                if chars / total_chars < 0.03
            }

    # 1) 过滤非人名、泛称标签、微量说话人
    filtered = []
    for s in speakers:
        name = (s.get("name") or "").strip()
        if not name:
            continue
        if re.match(r'^说话人\d*$', name):
            continue
        if _NON_PERSON_SPEAKERS.match(name):
            continue
        if name in trivial_from_transcript:
            continue
        filtered.append(s)

    # 2) 去除重复名称变体：如果 A 是 B 的子串（且 A 长度 >= 2），
    #    视为同一人，保留较长的完整名称
    to_remove: set = set()
    names = [(i, (filtered[i].get("name") or "")) for i in range(len(filtered))]

    for i, name_i in names:
        if i in to_remove:
            continue
        for j, name_j in names:
            if j <= i or j in to_remove:
                continue
            if name_i == name_j:
                to_remove.add(j)
                continue
            short, long = (name_i, name_j) if len(name_i) <= len(name_j) else (name_j, name_i)
            short_idx = i if len(name_i) <= len(name_j) else j
            if len(short) >= 2 and short in long:
                to_remove.add(short_idx)

    result = [s for i, s in enumerate(filtered) if i not in to_remove]
    if len(result) < len(speakers):
        removed_names = set(s.get("name") for s in speakers) - set(s.get("name") for s in result)
        if removed_names:
            print(f"[typeset] speakers 清洗: 移除 {removed_names}")
    return result


def _process_inline(
    text: str,
    footnotes: Dict[int, str],
    emitted_footnotes: set,
    emitted_footnote_texts: set,
) -> str:
    """处理段落内的行内元素：脚注标记 → Typst #footnote[...]，文本转义。"""
    placeholders: Dict[str, str] = {}

    def _replace_marker(m: re.Match) -> str:
        num = int(m.group(1))
        key = f"{_FN_PLACEHOLDER_PREFIX}{num}{_FN_PLACEHOLDER_SUFFIX}"
        if num in footnotes and num not in emitted_footnotes:
            raw_text = footnotes[num]
            fn_text = re.sub(r'\*\*(.+?)\*\*', r'\1', raw_text)
            if fn_text in emitted_footnote_texts:
                placeholders[key] = ""
            else:
                fn_text_escaped = _escape_typst(fn_text)
                placeholders[key] = f"#footnote[{fn_text_escaped}]"
                emitted_footnote_texts.add(fn_text)
            emitted_footnotes.add(num)
        else:
            placeholders[key] = ""
        return key

    speaker, rest = _extract_speaker(text)
    if speaker:
        text = rest

    text = re.sub(r'\[\^(\d+)\]', _replace_marker, text)
    text = _strip_md_emphasis(text)
    text = re.sub(r'^(\s*)\*(\s)', r'\1-\2', text, flags=re.MULTILINE)
    text = _escape_typst(text)

    for key, typst_code in placeholders.items():
        text = text.replace(key, typst_code)

    if speaker:
        speaker_escaped = _escape_typst(speaker)
        text = (
            '#text(font: ("Noto Sans CJK SC", "Noto Sans SC"))'
            f'[{speaker_escaped}：]{text}'
        )

    return text


def _process_blockquote(para: str, footnotes: Dict[int, str]) -> str:
    """处理引用块 (> text)。

    根据用户需求，直接移除引用块内容。
    为了保持段落索引一致（用于金句插入定位），返回空字符串。
    """
    return ""


# ================================================================
# 页面构建
# ================================================================

_BREAK_CHARS = set("，。、；！？·,;!? ")

def _find_title_split(title: str) -> int:
    """在标题中间附近找最佳换行位置，优先在标点后断行，否则在正中间。"""
    n = len(title)
    mid = n // 2
    max_drift = max(2, n // 5)
    for offset in range(max_drift + 1):
        for pos in (mid + offset, mid - offset):
            if 0 < pos < n and title[pos - 1] in _BREAK_CHARS:
                return pos
    return mid

def _build_cover_from_image(cover_image_filename: str) -> str:
    """使用预渲染的图片作为封面。"""
    return (
        '#page(margin: 0pt, numbering: none, header: none, footer: none)[\n'
        f'  #image("{cover_image_filename}", width: 100%, height: 100%, fit: "cover")\n'
        ']'
    )

def _build_blank_page(paper_texture_filename: str = "") -> str:
    """生成无文字、无页眉页脚的空白页，用于印刷装订留白。"""
    if paper_texture_filename:
        return f'#page(numbering: none, header: none, footer: none, background: image("{paper_texture_filename}", width: 100%, height: 100%, fit: "cover"))[]'
    return '#page(numbering: none, header: none, footer: none)[]'

def _build_toc() -> str:
    """生成目录页。"""
    return (
        # `pagebreak(to: "odd")` 在奇→奇过渡时会自动插一张空白 padding 页；
        # 用 is-pad-page state 包裹，让那张 padding 页的 footer 直接 return，
        # 避免上一节页脚（"viii | 精华提要"）残留在空白页上。
        '#is-pad-page.update(true)\n'
        '#pagebreak(to: "odd")\n'
        '#is-pad-page.update(false)\n'
        # 给目录页一个 level-1 heading（outlined: false，不出现在目录自身里），
        # 目的：让页脚里的 `query(heading.where(level:1).before(here()))` 能正确拿到"目 录"，
        # 而不会把上一页（精华提要）的标题错误地粘到目录页脚上。
        '#heading(level: 1, numbering: none, outlined: false)[目 录]\n'
        '#set par(first-line-indent: 0em)\n'
        '#align(center)[\n'
        '  #text(font: ("Source Han Sans SC", "Hiragino Sans GB", "PingFang SC", "Noto Sans CJK SC", "Noto Sans SC"), size: 16pt, weight: "extrabold", fill: black)[目 录]\n'
        ']\n'
        '#v(0.2cm)\n'
        '#pad(right: 8%)[\n'
        '#{\n'
        '  set text(fill: clr-body)\n'
        '  show linebreak: none\n'
        '  show outline.entry.where(level: 1): it => {\n'
        '    v(0.8em)\n'
        '    strong(it)\n'
        '  }\n'
        '  outline(title: none, indent: 0em)\n'
        '}\n'
        ']\n'
    )


def _build_epigraph(quote: Optional[Dict], paper_texture_filename: str = "") -> str:
    """生成题记页（全书精选 1 条金句）。加入古典花纹与典雅排版。"""
    if not quote:
        return ""
    text, speaker = _parse_quote_attribution(quote.get("text", ""))
    if not speaker:
        speaker = quote.get("speaker", "")
    if not text:
        return ""
    text_escaped = _escape_typst(text)
    speaker_escaped = _escape_typst(speaker) if speaker else ""

    # 使用内联 SVG 绘制一个典雅的古典花纹（四芒星+线条）
    flourish_svg = (
        "<svg viewBox='0 0 120 30' xmlns='http://www.w3.org/2000/svg'>"
        "<path d='M60,2 Q65,15 75,15 Q65,15 60,28 Q55,15 45,15 Q55,15 60,2 Z' fill='#C4A882'/>"
        "<line x1='15' y1='15' x2='35' y2='15' stroke='#C4A882' stroke-width='0.5'/>"
        "<circle cx='40' cy='15' r='1.5' fill='#C4A882'/>"
        "<line x1='105' y1='15' x2='85' y2='15' stroke='#C4A882' stroke-width='0.5'/>"
        "<circle cx='80' cy='15' r='1.5' fill='#C4A882'/>"
        "</svg>"
    )

    if paper_texture_filename:
        bg_code = f'background: image("{paper_texture_filename}", width: 100%, height: 100%, fit: "cover")'
    else:
        bg_code = 'fill: none'

    lines = [
        f'#page(margin: (x: 24mm, y: 24mm), numbering: none, header: none, footer: none, {bg_code})[',
        '  #counter(page).update(1)',
        '  #set par(first-line-indent: 0em)',
        '  #v(1fr)',
        '  #align(center)[',
        '    // 古典装饰花纹',
        f'    #image.decode("{flourish_svg}", width: 3.5cm)',
        '    #v(3em)',
        '    // 题记内容',
        '    #block(width: 95%)[',
        '      #set par(leading: 1.8em, justify: true)',
        '      #set align(start)',
        '      #set text(font: ("FandolKai", "Noto Serif CJK SC", "Noto Serif SC"), size: 16pt, fill: black)',
        f'      {text_escaped}',
        '    ]',
    ]
    if speaker_escaped:
        lines.append('    #v(3em)')
        lines.append('    #text(font: ("FandolKai", "Noto Serif CJK SC", "Noto Serif SC"), size: 16pt, fill: black, tracking: 0.15em)[')
        lines.append(f'      —— #h(0.5em) {speaker_escaped}')
        lines.append('    ]')
    lines += [
        '  ]',
        '  #v(1.5fr)',
        ']',
    ]
    return "\n".join(lines)


def _build_editors_preface(lead_paragraph: str) -> str:
    """生成编者序页（简介/导语）。"""
    if not lead_paragraph or not lead_paragraph.strip():
        return ""
    normalized = lead_paragraph.replace("\r\n", "\n").replace("\r", "\n").strip()
    paragraphs = [p.strip() for p in re.split(r"\n+", normalized) if p.strip()]
    body_parts = []
    for para in paragraphs:
        body_parts.append(_escape_typst(_md_bold_to_typst(para)))

    lines = [
        # 同 _build_toc：包裹 pagebreak，使自动插入的 padding 空白页不带页脚。
        '#is-pad-page.update(true)',
        '#pagebreak(to: "odd", weak: true)',
        '#is-pad-page.update(false)',
        '#page(header: none)[',
        '  #heading(level: 1, numbering: none, outlined: true)[编者序]',
        '  #set par(first-line-indent: 0em)',
        '  #align(center)[',
        '    #text(font: ("Source Han Sans SC", "Hiragino Sans GB", "PingFang SC", "Noto Sans CJK SC", "Noto Sans SC"), size: 16pt, weight: "extrabold", fill: black)[编者序]',
        '  ]',
        '  #v(0.2cm)',
        '  #set par(first-line-indent: 0em, leading: 1.4em, spacing: 1.8em)',
        '  #set text(font: ("Noto Serif CJK SC", "Noto Serif SC"), size: 11pt, fill: clr-body)',
        '  #par[]',
    ]
    for part in body_parts:
        lines.append(f'  #par(first-line-indent: 0em)[#h(2em){part}]')
    lines.append(']')
    return "\n".join(lines)


def _build_summary(bullets: List[str]) -> str:
    """生成内容提要页。"""
    if not bullets:
        return ""
    lines = [
        # 同 _build_toc：包裹 pagebreak，使自动插入的 padding 空白页不带页脚。
        '#is-pad-page.update(true)',
        '#pagebreak(to: "odd", weak: true)',
        '#is-pad-page.update(false)',
        '#page(header: none)[',
        '  #heading(level: 1, numbering: none, outlined: true)[精华提要]',
        '  #set par(first-line-indent: 0em)',
        '  #align(center)[',
        '    #text(font: ("Source Han Sans SC", "Hiragino Sans GB", "PingFang SC", "Noto Sans CJK SC", "Noto Sans SC"), size: 16pt, weight: "extrabold", fill: black)[精华提要]',
        '  ]',
        '  #v(0.2cm)',
        '  #set text(size: 10.5pt, fill: clr-body)',
    ]
    for bullet in bullets:
        if '：' in bullet:
            label, rest = bullet.split('：', 1)
            label_escaped = _escape_typst(_md_bold_to_typst(label))
            rest_escaped = _escape_typst(_md_bold_to_typst(rest))
            lines.append(f'  #block(inset: (left: 1em, bottom: 0.8em))[• #text(fill: clr-accent, weight: "bold")[{label_escaped}]：{rest_escaped}]')
        elif ':' in bullet:
            label, rest = bullet.split(':', 1)
            label_escaped = _escape_typst(_md_bold_to_typst(label))
            rest_escaped = _escape_typst(_md_bold_to_typst(rest))
            lines.append(f'  #block(inset: (left: 1em, bottom: 0.8em))[• #text(fill: clr-accent, weight: "bold")[{label_escaped}]：{rest_escaped}]')
        else:
            escaped = _escape_typst(_md_bold_to_typst(bullet))
            lines.append(f'  #block(inset: (left: 1em, bottom: 0.8em))[• {escaped}]')
    lines.append(']')
    return "\n".join(lines)


def _build_references(source_meta: Dict) -> str:
    """生成参考来源区域（正文末尾的简短元信息）。"""
    podcast_name = source_meta.get("podcast_name", "")
    title = source_meta.get("title", "")
    publish_date = source_meta.get("publish_date", "")
    podcast_url = source_meta.get("podcast_url", "")

    if not podcast_name and not title and not podcast_url:
        return ""

    lines = [
        '#v(2em)',
        '#block(breakable: false)[',
        '#line(length: 100%, stroke: 0.5pt + black)',
        '#v(1em)',
        '#set par(first-line-indent: 0em)',
        '#text(size: 12pt, weight: "bold", fill: black)[参考来源]',
        '#v(0.8em)',
        '#set text(size: 9.5pt, fill: black)',
    ]
    if podcast_name:
        lines.append(f'本书内容整理自播客节目「{_escape_typst(podcast_name)}」 \\')
    if title:
        lines.append(f'单集：《{_escape_typst(title)}》 \\')
    if publish_date:
        lines.append(f'发布日期：{_escape_typst(publish_date)} \\')
    if podcast_url:
        lines.append(f'收听链接：#text(fill: black)[#link("{_escape_typst_string(podcast_url)}")]')
    lines.append(']')
    return "\n".join(lines)


def _redistribute_illustrations(
    insertions: Dict[int, List[str]],
    total_paragraphs: int,
) -> None:
    """确保图片错落分布——同一段落后最多放 1 张图片。

    当多张图片指向同一段落时，保留第一张，将其余图片就近移到
    相邻的空闲段落（优先向后、再向前）。只处理 ``#book-illustration``，
    金句（``#pull-quote``）不受影响。就地修改 *insertions*。
    """
    if total_paragraphs <= 0:
        return

    occupied: set = set()
    overflow: List[str] = []

    for pos in sorted(insertions.keys()):
        items = insertions[pos]
        ills = [x for x in items if x.startswith("#book-illustration")]
        if not ills:
            continue
        non_ills = [x for x in items if not x.startswith("#book-illustration")]
        insertions[pos] = non_ills

        for ill in ills:
            if pos not in occupied:
                insertions[pos].append(ill)
                occupied.add(pos)
            else:
                overflow.append((pos, ill))

    for original_pos, ill in overflow:
        placed = False
        for offset in range(1, total_paragraphs):
            for candidate in (original_pos + offset, original_pos - offset):
                if 0 <= candidate < total_paragraphs and candidate not in occupied:
                    insertions.setdefault(candidate, []).append(ill)
                    occupied.add(candidate)
                    placed = True
                    break
            if placed:
                break
        if not placed:
            insertions.setdefault(original_pos, []).append(ill)


def _build_chapter(
    chapter: Dict,
    quotes: List[Dict],
    illustrations: Optional[List[Dict]] = None,
    emitted_footnotes: Optional[set] = None,
    emitted_footnote_texts: Optional[set] = None,
) -> str:
    """将单个章节转换为 Typst 源码（含标题、正文、脚注、金句卡片、插图）。"""
    if emitted_footnotes is None:
        emitted_footnotes = set()
    if emitted_footnote_texts is None:
        emitted_footnote_texts = set()
    title = _escape_typst(chapter.get("title", ""))
    raw_content = chapter.get("content", "")

    body, footnotes = _split_footnotes(raw_content)
    footnotes = {
        num: _strip_footnote_prefix(text)
        for num, text in footnotes.items()
    }
    structured_footnotes = chapter.get("footnotes") or []
    for fn in structured_footnotes:
        num = fn.get("number") or fn.get("id")
        text = _strip_footnote_prefix((fn.get("text") or "").strip())
        if num and text:
            footnotes[int(num)] = text
    # 过滤 LLM 偶尔生成的元数据标记段落
    body = re.sub(r'【章节标题】\s*\n.*?\n', '', body, count=1)
    body = re.sub(r'【章节内容】\s*\n?', '', body, count=1)
    paragraphs = [p.strip() for p in body.split("\n\n") if p.strip()]

    processed: List[str] = []
    speakers_per_para: List[Optional[str]] = []
    for para in paragraphs:
        if para.startswith("> ") or para.startswith(">"):
            processed.append(_process_blockquote(para, footnotes))
            speakers_per_para.append(None)
        else:
            speaker, _ = _extract_speaker(para)
            speakers_per_para.append(speaker)
            processed.append(_process_inline(para, footnotes, emitted_footnotes, emitted_footnote_texts))

    def _parse_para_pos(raw) -> Optional[int]:
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

    insertions: Dict[int, List[str]] = {}
    for q in quotes:
        if q.get("placement") != "inline_card":
            continue
        pos = _parse_para_pos(q.get("after_paragraph"))
        if pos is None:
            continue
        # Prompts define after_paragraph as 1-based: insert after paragraph K.
        # processed/result_parts are 0-based, so K must become K - 1 here.
        pos = max(0, pos - 1)
        q_text, q_speaker = _parse_quote_attribution(q.get("text", ""))
        if not q_text:
            continue
        typst_call = f'#pull-quote[{_escape_typst(q_text)}]'
        insertions.setdefault(pos, []).append(typst_call)

    _SIZE_TO_WIDTH = {"small": "20%", "medium": "35%", "large": "50%"}

    for ill in (illustrations or []):
        pos = _parse_para_pos(ill.get("after_paragraph"))
        if pos is None:
            continue
        # Same 1-based -> 0-based conversion as pull quotes.
        pos = max(0, pos - 1)
        ill_filename = ill.get("filename", "")
        if not ill_filename:
            continue
        caption = (ill.get("caption", "") or "").strip().rstrip("。．.；;！!？?，,、 ")
        size = ill.get("size", "")
        width_param = _SIZE_TO_WIDTH.get(size, "")

        parts = [f'"{ill_filename}"']
        if caption:
            parts.append(f'caption: "{_escape_typst_string(caption)}"')
        if width_param:
            parts.append(f"img-width: {width_param}")
        typst_call = f'#book-illustration({", ".join(parts)})'
        insertions.setdefault(pos, []).append(typst_call)

    _redistribute_illustrations(insertions, len(processed))

    max_chapter_title = 20
    if len(title) > max_chapter_title:
        sp = _find_title_split(title)
        display_title = title[:sp] + "#linebreak()" + title[sp:]
    else:
        display_title = title
    after_insertion: set = set()
    for pos in insertions:
        after_insertion.add(pos + 1)

    result_parts = [f"= {display_title}\n", "#par[]"]
    current_speaker: Optional[str] = None
    for i, para in enumerate(processed):
        if para:
            speaker = speakers_per_para[i]
            if speaker and current_speaker and current_speaker != speaker:
                result_parts.append("#v(1.7em)")
            if speaker:
                current_speaker = speaker
            if i in after_insertion:
                result_parts.append(f"#par(first-line-indent: 0em)[#h(2em){para}]")
            else:
                result_parts.append(f"#par(first-line-indent: 2em)[{para}]")
        
        for insertion in insertions.get(i, []):
            result_parts.append(insertion)

    result_parts.append("#chapter-end()")
    return "\n\n".join(result_parts)


# ================================================================
# 组装与编译
# ================================================================

def _map_highlights_to_chapters(
    chapters: List[Dict],
    highlights: Dict,
    illustrations: Optional[Dict] = None,
) -> Tuple[Optional[Dict], Dict[str, List[Dict]], Dict[str, List[Dict]]]:
    """将金句和插图按 chapter_title 映射到对应章节。

    返回 (epigraph_quote, quotes_by_title, illustrations_by_title)。
    """
    epigraph: Optional[Dict] = None
    quotes_by_title: Dict[str, List[Dict]] = {
        ch["title"]: [] for ch in chapters if ch.get("title")
    }
    illustrations_by_title: Dict[str, List[Dict]] = {
        ch["title"]: [] for ch in chapters if ch.get("title")
    }

    for q in highlights.get("quotes", []):
        if isinstance(q, str):
            continue
        if q.get("placement") == "epigraph":
            epigraph = q
        elif q.get("chapter_title") in quotes_by_title:
            quotes_by_title[q["chapter_title"]].append(q)

    if illustrations:
        for img in illustrations.get("images", []):
            if img.get("source") == "ai_generated" or img.get("type") == "concept":
                continue
            ct = img.get("chapter_title", "")
            if ct in illustrations_by_title:
                illustrations_by_title[ct].append(img)

    return epigraph, quotes_by_title, illustrations_by_title


def build_typst_source(
    annotated_content: Dict,
    highlights: Dict,
    paper_size: str = "iso-b5",
    cover_image_filename: str = "",
    source_meta: Optional[Dict] = None,
    editor_preface_content: str = "",
    paper_texture_filename: str = "",
    illustrations: Optional[Dict] = None,
    back_cover_image_filename: str = "",
) -> str:
    """从结构化数据构建完整的 Typst 源码。

    页面顺序：封面 → 题记页 → 编者序 → 内容提要 → 目录 → 正文 → 参考来源
    """
    emitted_footnotes: set = set()
    emitted_footnote_texts: set = set()
    chapters = annotated_content.get("chapters", [])
    epigraph, quotes_map, illustrations_map = _map_highlights_to_chapters(
        chapters, highlights, illustrations
    )

    # 页眉用的短标题：优先用 title，fallback 到 core_theme
    raw_title = annotated_content.get("title", "")
    if raw_title:
        book_title_short = re.split(r'[：:]', raw_title, maxsplit=1)[0].strip()
    else:
        core_theme = annotated_content.get("core_theme", "")
        book_title_short = re.split(r'[：:]', core_theme, maxsplit=1)[0].strip()
    book_title_short = book_title_short or "播客书稿"

    podcast_name = (source_meta or {}).get("podcast_name", "")
    preamble_typst = TYPST_PREAMBLE.replace("PAPER_SIZE_PLACEHOLDER", paper_size)
    preamble_typst = preamble_typst.replace(
        "BOOK_TITLE_PLACEHOLDER", _escape_typst_string(book_title_short)
    )
    preamble_typst = preamble_typst.replace(
        "PODCAST_NAME_PLACEHOLDER", _escape_typst_string(podcast_name)
    )

    content_preamble = annotated_content.get("preamble", {})

    parts: List[str] = [preamble_typst]

    # 1. 封面
    if cover_image_filename:
        parts.append(_build_cover_from_image(cover_image_filename))
    else:
        # Fallback to an empty page if no cover image
        parts.append('#page(margin: 0pt, numbering: none, header: none, footer: none)[]')
    parts.append(_build_blank_page())

    # 2. 题记页（页码重置在题记页内部）
    # 题记页及其背面：与正文一致，不使用纸张纹理背景（保持整体观感简洁）
    epigraph_page = _build_epigraph(epigraph, paper_texture_filename="")
    if epigraph_page:
        parts.append(epigraph_page)
        parts.append(_build_blank_page())

    # 3. 编者序（优先使用独立节点生成的版本）
    preface_text = editor_preface_content or content_preamble.get("lead_paragraph", "")
    editors_preface = _build_editors_preface(preface_text)
    if editors_preface:
        parts.append(editors_preface)

    # 4. 内容提要
    summary_page = _build_summary(
        content_preamble.get("summary_bullets", [])
    )
    if summary_page:
        parts.append(summary_page)

    # 5. 目录
    parts.append(_build_toc())

    # ── 正文页码：从阿拉伯数字 1 开始 ──
    # #set page 会触发换页，counter(page).update(1) 让该页为第 1 页
    parts.append('#is-front-matter.update(false)\n#set page(numbering: "1")\n#counter(page).update(1)\n#counter(heading).update(0)')

    # 6. 正文章节
    for ch in chapters:
        title = ch.get("title", "")
        parts.append(_build_chapter(
            ch,
            quotes_map.get(title, []),
            illustrations_map.get(title, []),
            emitted_footnotes,
            emitted_footnote_texts,
        ))

    # 7. 参考来源
    if source_meta:
        refs = _build_references(source_meta)
        if refs:
            parts.append(refs)

    # 8. 封底
    if back_cover_image_filename:
        parts.append(
            '#page(margin: 0pt, numbering: none, header: none, footer: none)[\n'
            f'  #image("{back_cover_image_filename}", width: 100%, height: 100%, fit: "cover")\n'
            ']'
        )
    else:
        parts.append(
            '#page(margin: 0pt, numbering: none, header: none, footer: none, fill: clr-cover)[]'
        )

    return "\n\n".join(parts)


TYPST_COMPILE_TIMEOUT = 300  # 5 minutes


def _ensure_even_pages(pdf_path: str) -> str:
    """印刷要求总页数为偶数。如果是奇数页，在封底前插入一张空白页。"""
    import fitz
    import os

    doc = fitz.open(pdf_path)
    if doc.page_count % 2 == 0:
        doc.close()
        return pdf_path

    last = doc.load_page(-1)
    w, h = last.rect.width, last.rect.height
    doc.new_page(pno=doc.page_count - 1, width=w, height=h)
    tmp_path = pdf_path + ".tmp"
    doc.save(tmp_path, deflate=True)
    new_count = doc.page_count
    doc.close()
    os.replace(tmp_path, pdf_path)
    print(f"[typeset] 总页数为奇数，已在封底前插入空白页 → {new_count} 页")
    return pdf_path


async def compile_pdf(
    typst_source: str,
    output_dir: Optional[str] = None,
    font_paths: Optional[List[str]] = None,
) -> str:
    """编译 Typst 源码为 PDF，返回 PDF 文件路径。"""
    import asyncio
    import typst as typst_lib
    from datetime import datetime

    if output_dir:
        out_dir = Path(output_dir)
    else:
        out_dir = Path(settings.STORAGE_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    typ_path = out_dir / f"book_{ts}.typ"
    pdf_path = out_dir / f"book_{ts}.pdf"

    typ_path.write_text(typst_source, encoding="utf-8")

    compile_kwargs: Dict[str, Any] = {
        "output": str(pdf_path),
    }
    if font_paths:
        compile_kwargs["font_paths"] = font_paths
    elif settings.TYPESET_FONT_PATHS:
        compile_kwargs["font_paths"] = [
            p.strip() for p in settings.TYPESET_FONT_PATHS.split(",") if p.strip()
        ]

    loop = asyncio.get_event_loop()
    await asyncio.wait_for(
        loop.run_in_executor(
            None,
            lambda: typst_lib.compile(str(typ_path), **compile_kwargs),
        ),
        timeout=TYPST_COMPILE_TIMEOUT,
    )

    _ensure_even_pages(str(pdf_path))

    print(f"[typeset] PDF 已生成: {pdf_path} ({pdf_path.stat().st_size / 1024:.0f} KB)")
    return str(pdf_path)


# ================================================================
# 节点入口
# ================================================================

async def _download_cover_image(cover_url: str, output_dir: str) -> str:
    """下载播客封面图到输出目录，返回文件名（相对路径）。"""
    if not cover_url:
        return ""
    try:
        out_dir = Path(output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)

        # 本地文件路径（自定义上传封面）
        if cover_url.startswith("/files/"):
            local_path = Path(settings.STORAGE_DIR) / cover_url.split("/files/", 1)[1]
            if local_path.exists():
                import shutil
                dest_name = f"cover{local_path.suffix or '.png'}"
                shutil.copy(local_path, out_dir / dest_name)
                print(f"[typeset] 封面图本地复制: {dest_name}")
                return dest_name
            print(f"[typeset] 本地封面文件不存在: {local_path}")
            return ""

        ext = ".jpg"
        if ".png" in cover_url.lower():
            ext = ".png"
        elif ".webp" in cover_url.lower():
            ext = ".webp"
        filename = f"cover{ext}"

        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            resp = await client.get(cover_url)
            resp.raise_for_status()
            (out_dir / filename).write_bytes(resp.content)

        print(f"[typeset] 封面图已下载: {filename} ({len(resp.content) / 1024:.0f} KB)")
        return filename
    except Exception as e:
        print(f"[typeset] 封面图下载失败，跳过: {e}")
        return ""


async def typeset_node(state: PodBookState) -> Dict[str, Any]:
    """
    排版节点：将注释后的书稿渲染为 print-ready PDF。

    输入：annotated_content, highlights
    输出：pdf_path, typst_source
    """
    task_id = state.get("task_id", "unknown")
    print(f"[typeset] 开始处理任务: {task_id}")

    annotated = state.get("annotated_content")
    if not annotated:
        annotated = state.get("composed_content")
        if not annotated:
            raise ValueError("缺少书稿内容，无法进行排版")
        print("[typeset] 未找到 annotated_content，使用 composed_content 降级")

    highlights = state.get("highlights") or {"quotes": []}

    # speakers 补全：确保 host 和 guest 都出现在 speakers 列表中
    existing_speakers = list(annotated.get("speakers") or [])
    existing_names = {s.get("name") for s in existing_speakers if s.get("name")}
    host = state.get("host_name", "")
    state_guests = state.get("guest_names") or []

    def _name_already_covered(new_name: str, known_names: set) -> bool:
        """判断 new_name 是否已被 known_names 中的某个名字覆盖（子串匹配）。"""
        if new_name in known_names:
            return True
        for k in known_names:
            if len(k) >= 2 and len(new_name) >= 2:
                if k in new_name or new_name in k:
                    return True
        return False

    if not existing_speakers:
        speakers_fallback = []
        if host:
            speakers_fallback.append({"name": host, "role": "主持人"})
        for g in state_guests:
            if g:
                speakers_fallback.append({"name": g, "role": "嘉宾"})
        if speakers_fallback:
            annotated = {**annotated, "speakers": speakers_fallback}
            print(f"[typeset] speakers 从 state 顶层字段补充: {[s['name'] for s in speakers_fallback]}")
    else:
        added = []
        if host and not _name_already_covered(host, existing_names):
            existing_speakers.insert(0, {"name": host, "role": "主持人"})
            added.append(host)
        for g in state_guests:
            if g and not _name_already_covered(g, existing_names):
                existing_speakers.append({"name": g, "role": "嘉宾"})
                added.append(g)
        if added:
            annotated = {**annotated, "speakers": existing_speakers}
            print(f"[typeset] speakers 合并补全: 新增 {added}")

    user_title = (state.get("user_title") or "").strip()
    if user_title and annotated.get("title") != user_title:
        print(f"[typeset] 使用用户指定书名: {user_title} (原: {annotated.get('title', '')})")
        annotated = {**annotated, "title": user_title}

    chapters = annotated.get("chapters", [])
    print(f"[typeset] 输入: {len(chapters)} 个章节, "
          f"{len(highlights.get('quotes', []))} 条金句")

    paper_size = settings.TYPESET_PAPER_SIZE
    output_dir = str(Path(settings.STORAGE_DIR) / task_id)

    cover_url = state.get("cover_url", "")
    cover_filename = await _download_cover_image(cover_url, output_dir)

    # 准备封面变量
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

    # 封面作者行：用户显式提供的名字优先，否则从 annotated.speakers 推导
    _user_host_cover = (state.get("user_host_name") or "").strip()
    _user_guests_cover = list(state.get("user_guest_names") or [])

    if _user_host_cover or _user_guests_cover:
        hosts = [_user_host_cover] if _user_host_cover else []
        guests = [g for g in _user_guests_cover if g]
        others = []
        print(f"[typeset] 封面作者行使用用户输入: hosts={hosts}, guests={guests}")
    else:
        transcription_segs = (state.get("transcription") or {}).get("segments", [])
        speakers = _clean_speakers(annotated.get("speakers", []), transcription_segs)
        hosts = []
        guests = []
        others = []
        for s in speakers:
            name = s.get("name")
            if not name or re.match(r'^说话人\d*$', name): continue
            role = s.get("role", "")
            if "主持" in role: hosts.append(name)
            elif "嘉宾" in role: guests.append(name)
            else: others.append(name)

    author_line_1 = ""
    author_line_2 = ""
    if guests:
        author_line_1 = f'嘉宾 / {"、".join(guests)}'
    if hosts:
        host_text = f'主播 / {"、".join(hosts)}'
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
    series_label = f"{podcast_name_disp} · 典藏系列" if podcast_name_disp else "播客书稿 · 典藏系列"

    cover_style = state.get("cover_style", "swiss")
    cover_image_full_path = str(Path(output_dir) / cover_filename) if cover_filename else ""
    
    from core.services.cover_service import render_cover, render_back_cover
    rendered_cover_filename = "cover_rendered.png"
    rendered_cover_path = str(Path(output_dir) / rendered_cover_filename)

    custom_full_cover_url = state.get("custom_full_cover_url", "")
    if custom_full_cover_url:
        full_cover_local = await _download_cover_image(custom_full_cover_url, output_dir)
        if full_cover_local:
            import shutil
            shutil.copy(Path(output_dir) / full_cover_local, rendered_cover_path)
            print(f"[typeset] 使用用户上传的整体封面: {full_cover_local}")
        else:
            print("[typeset] 整体封面下载失败，回退到模板渲染")
            await render_cover(
                style=cover_style,
                variables={
                    "title": cover_title,
                    "subtitle": cover_subtitle,
                    "author_line_1": author_line_1,
                    "author_line_2": author_line_2,
                    "series_label": series_label,
                    "cover_image": cover_image_full_path,
                },
                output_path=rendered_cover_path
            )
    else:
        await render_cover(
            style=cover_style,
            variables={
                "title": cover_title,
                "subtitle": cover_subtitle,
                "author_line_1": author_line_1,
                "author_line_2": author_line_2,
                "series_label": series_label,
                "cover_image": cover_image_full_path,
            },
            output_path=rendered_cover_path
        )

    back_cover_filename = "back_cover_rendered.png"
    back_cover_path = str(Path(output_dir) / back_cover_filename)

    custom_back_cover_url = state.get("custom_back_cover_url", "")
    if custom_back_cover_url:
        back_cover_local = await _download_cover_image(custom_back_cover_url, output_dir)
        if back_cover_local:
            import shutil
            shutil.copy(Path(output_dir) / back_cover_local, back_cover_path)
            print(f"[typeset] 使用用户上传的自定义封底: {back_cover_local}")
        else:
            print("[typeset] 自定义封底下载失败，回退到模板渲染")
            await render_back_cover(style=cover_style, output_path=back_cover_path)
    else:
        await render_back_cover(style=cover_style, output_path=back_cover_path)

    try:
        from core.services.cover_service import render_cover_previews
        await render_cover_previews(
            variables={
                "title": cover_title,
                "subtitle": cover_subtitle,
                "author_line_1": author_line_1,
                "author_line_2": author_line_2,
                "series_label": series_label,
                "cover_image": cover_image_full_path,
            },
            output_dir=output_dir,
        )
    except Exception as e:
        print(f"[typeset] 封面预览缩略图生成失败（不影响排版）: {e}")

    source_meta = {
        "podcast_name": state.get("podcast_name", ""),
        "title": state.get("episode_title") or state.get("title", ""),
        "publish_date": state.get("publish_date", ""),
        "podcast_url": state.get("podcast_url", ""),
    }

    # 复制纸张纹理用于题记页背景
    paper_texture_src = Path(__file__).resolve().parent.parent / "knowledge" / "assets" / "paper_texture.jpg"
    paper_texture_filename = ""
    if paper_texture_src.exists():
        import shutil
        shutil.copy(paper_texture_src, Path(output_dir) / "paper_texture.jpg")
        paper_texture_filename = "paper_texture.jpg"

    illustrations = state.get("illustrations") or {"images": []}
    raw_images = illustrations.get("images", [])
    valid_images = []
    for img in raw_images:
        fn = img.get("filename", "")
        if fn and not (Path(output_dir) / fn).exists():
            print(f"[typeset] WARNING: 插图文件不存在，跳过: {fn}")
            continue
        valid_images.append(img)
    if len(valid_images) < len(raw_images):
        print(f"[typeset] 过滤后插图: {len(valid_images)}/{len(raw_images)}")
    illustrations = {**illustrations, "images": valid_images}

    typst_source = build_typst_source(
        annotated, highlights, paper_size,
        cover_image_filename=rendered_cover_filename,
        source_meta=source_meta,
        editor_preface_content=state.get("editor_preface_content", ""),
        paper_texture_filename=paper_texture_filename,
        illustrations=illustrations,
        back_cover_image_filename=back_cover_filename,
    )
    print(f"[typeset] Typst 源码生成完成: {len(typst_source)} 字符")

    pdf_path = await compile_pdf(typst_source, output_dir=output_dir, font_paths=[FONTS_DIR])

    print(f"[typeset] 任务 {task_id} 排版完成")
    return {
        "pdf_path": pdf_path,
        "typst_source": typst_source,
        "current_stage": WorkflowStage.TYPESET.value,
    }
