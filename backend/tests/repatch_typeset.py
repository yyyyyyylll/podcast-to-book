"""
快速验证排版改动：拿已有的 .typ 文件（包含完整书稿内容），
把前言部分替换成当前 typeset.py 里的 TYPST_PREAMBLE，重新编译。

用法：
  cd backend
  python -m tests.repatch_typeset <task_storage_dir>
  # 例：python -m tests.repatch_typeset storage/b5df08ed-7925-4938-b6b2-22d3d0306528
"""
import os
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.workflow.nodes.typeset import TYPST_PREAMBLE, _escape_typst_string


def main() -> None:
    if len(sys.argv) < 2:
        print("用法: python -m tests.repatch_typeset <task_storage_dir>")
        sys.exit(1)

    task_dir = Path(sys.argv[1]).resolve()
    if not task_dir.is_dir():
        print(f"目录不存在: {task_dir}")
        sys.exit(1)

    typ_files = sorted(task_dir.glob("book_*.typ"))
    if not typ_files:
        print(f"未找到 book_*.typ in {task_dir}")
        sys.exit(1)
    latest_typ = typ_files[-1]
    print(f"[repatch] 使用源文件: {latest_typ.name}")

    original = latest_typ.read_text(encoding="utf-8")

    # 提取原来的 book-title / podcast-name
    book_title_match = re.search(r'#let book-title = "([^"]*)"', original)
    podcast_name_match = re.search(r'#let podcast-name = "([^"]*)"', original)
    book_title = book_title_match.group(1) if book_title_match else "播客书稿"
    podcast_name = podcast_name_match.group(1) if podcast_name_match else ""
    print(f"[repatch] book_title = {book_title!r}")
    print(f"[repatch] podcast_name = {podcast_name!r}")

    # 定位 preamble 结束位置：以 `#let chapter-end = {...}` 的闭合 `}` 为界
    # chapter-end 函数是 TYPST_PREAMBLE 的最后一个定义
    m = re.search(
        r'#let chapter-end\(\) = \{\s*\n\s*v\(2em\)\s*\n\}\s*\n',
        original,
    )
    if not m:
        print("无法定位 preamble 结束位置（chapter-end 函数），请检查源文件")
        sys.exit(1)
    body_start = m.end()
    body = original[body_start:]

    # 用最新的 TYPST_PREAMBLE 替换，并注入 book_title/podcast_name
    new_preamble = TYPST_PREAMBLE.replace(
        "BOOK_TITLE_PLACEHOLDER", _escape_typst_string(book_title)
    ).replace(
        "PODCAST_NAME_PLACEHOLDER", _escape_typst_string(podcast_name)
    ).replace(
        "PAPER_SIZE_PLACEHOLDER", "iso-b5"
    )

    # 把"前置页标题块"（目录/编者序/精华提要）统一替换成与章节标题一致的新样式
    # 支持两种历史格式：
    #   旧版 A（最初）：18pt bold clr-accent + clr-gold 横线
    #   旧版 B（含英文 tag）：CONTENTS/PREFACE/HIGHLIGHTS + 中文标题
    # 目标：只保留单行中文标题（Hiragino Sans GB ExtraBold 13.5pt）
    _ALLOWED_LABELS = {"目 录", "编者序", "精华提要"}
    _HEADING_FONT = (
        '("Hiragino Sans GB", "PingFang SC", "Noto Sans CJK SC", "Noto Sans SC")'
    )

    def _new_title_line(label: str) -> str:
        # 前置页标题比章节标题（13.5pt）略大一点
        return (
            f'#text(font: {_HEADING_FONT}, size: 16pt, '
            f'weight: "extrabold", fill: black)[{label}]'
        )

    # 旧版 A：clr-accent + clr-gold 线
    def _replace_old_a(match: re.Match) -> str:
        label = match.group(1)
        if label not in _ALLOWED_LABELS:
            return match.group(0)
        return _new_title_line(label)

    body = re.sub(
        r'#text\(size: 18pt, weight: "bold", fill: clr-accent\)\[([^\]]+)\]\s*\n'
        r'\s*#v\(-0\.1cm\)\s*\n'
        r'\s*#line\(length: 10%, stroke: 1pt \+ clr-gold\)',
        _replace_old_a,
        body,
    )

    # 旧版 B：CONTENTS/PREFACE/HIGHLIGHTS 英文 tag + 中文标题
    def _replace_old_b(match: re.Match) -> str:
        label = match.group(1)
        if label not in _ALLOWED_LABELS:
            return match.group(0)
        return _new_title_line(label)

    body = re.sub(
        r'#\{\s*\n'
        r'\s*set text\(fill: black, font: \([^)]+\), size: 12pt, weight: "bold"\)\s*\n'
        r'\s*text\(tracking: 0\.32em\)\[(?:CONTENTS|PREFACE|HIGHLIGHTS)\]\s*\n'
        r'\s*\}\s*\n'
        r'\s*#v\(0\.12cm\)\s*\n'
        r'\s*#text\(font: \([^)]+\), size: 13\.5pt, weight: "extrabold", fill: black\)\[([^\]]+)\]',
        _replace_old_b,
        body,
    )

    # 旧版 C：仅有一行中文标题（没有英文 tag）但字号仍是 13.5pt，需要升级到 16pt
    def _replace_old_c(match: re.Match) -> str:
        label = match.group(1)
        if label not in _ALLOWED_LABELS:
            return match.group(0)
        return _new_title_line(label)

    body = re.sub(
        r'#text\(font: \([^)]+\), size: 13\.5pt, weight: "extrabold", fill: black\)\[([^\]]+)\]',
        _replace_old_c,
        body,
    )

    # 参考来源区块：标题 / 分隔线 / 链接颜色都改为 black
    body = body.replace(
        '#line(length: 100%, stroke: 0.5pt + clr-gold)',
        '#line(length: 100%, stroke: 0.5pt + black)',
    )
    body = body.replace(
        '#text(size: 12pt, weight: "bold", fill: clr-accent)[参考来源]',
        '#text(size: 12pt, weight: "bold", fill: black)[参考来源]',
    )
    body = re.sub(
        r'收听链接：#text\(fill: clr-accent\)\[#link',
        '收听链接：#text(fill: black)[#link',
        body,
    )

    # 题记页文字（包括署名）颜色：clr-accent → black
    # 仅替换"FandolKai 手写字体 + 16pt"这种题记专用样式行，避免误伤其他用到 clr-accent 的地方
    body = re.sub(
        r'(font: \("FandolKai"[^)]*\), size: 16pt, fill: )clr-accent',
        r'\1black',
        body,
    )

    # 剥离题记页及其背面的"纸张纹理"背景，保持与正文一致的纯底。
    # 题记页：`..., background: image("paper_texture.jpg", ...)` → `..., fill: none`
    body = re.sub(
        r',\s*background: image\("paper_texture\.jpg",[^)]*\)',
        ', fill: none',
        body,
    )
    # 题记背面的空白页：`#page(numbering: none, header: none, footer: none, background: image("paper_texture.jpg", ...))[]`
    # → 去掉 background 参数
    body = re.sub(
        r'#page\(numbering: none, header: none, footer: none, fill: none\)\[\]',
        '#page(numbering: none, header: none, footer: none)[]',
        body,
    )

    # 给存量 .typ 文件里的"目 录"页补一个隐藏 heading，让页脚 query 能正确拿到本页标题
    # （否则会把上一页的"精华提要"粘到目录页脚上）。
    # 只在当前 TOC 入口前没有 heading 时插入，避免重复注入。
    _toc_entry_pat = (
        r'(#pagebreak\(to: "odd"\)\s*\n)'
        r'(?!\s*#heading\(level: 1)'
        r'(\s*#set par\(first-line-indent: 0em\)\s*\n'
        r'\s*#align\(center\)\[\s*\n'
        r'\s*#text\(font: \([^)]+\), size: 16pt, weight: "extrabold", fill: black\)\[目 录\])'
    )
    body = re.sub(
        _toc_entry_pat,
        r'\1#heading(level: 1, numbering: none, outlined: false)[目 录]\n\2',
        body,
    )

    # 压缩前置页标题上下的垂直间距：
    #   - 标题前 `#v(0.01cm)` 去掉（几乎无感，作用仅为占位），
    #   - 标题后 `#v(0.4cm)` → `#v(0.2cm)`
    # 仅限紧邻"目录/编者序/精华提要"新样式标题行的 v 指令。
    _label_alt = "|".join(re.escape(l) for l in _ALLOWED_LABELS)

    # 1) 标题前的 #v(0.01cm) 或 #v(0.15cm)
    body = re.sub(
        r'#v\(0\.(?:01|15)cm\)\s*\n(\s*#align\(center\)\[\s*\n\s*'
        r'#?text\(font: \([^)]+\), size: 16pt, weight: "extrabold", fill: black\)\[(?:'
        + _label_alt + r')\])',
        r'\1',
        body,
    )

    # 2) 标题后的 #v(0.4cm) → #v(0.2cm)
    body = re.sub(
        r'(fill: black\)\[(?:' + _label_alt + r')\]\s*\n\s*\]\s*\n\s*)#v\(0\.4cm\)',
        r'\1#v(0.2cm)',
        body,
    )

    # 字体链升级（仅 body，preamble 由 TYPST_PREAMBLE 整体替换自动覆盖）：
    #   1) 章节 / 前置页标题链：Hiragino 前缀 → Source Han Sans 打头（需要 Bold 字重）
    body = body.replace(
        '("Hiragino Sans GB", "PingFang SC", "Noto Sans CJK SC", "Noto Sans SC")',
        '("Source Han Sans SC", "Hiragino Sans GB", "PingFang SC", '
        '"Noto Sans CJK SC", "Noto Sans SC")',
    )
    #   2) 说话人标签：回退到纯 Noto 链（需要 Regular 字重；bundle 的 SHS 只有 Bold，
    #      若留在链首 Typst 会把 Bold 当成"最接近"字重用掉）。
    #      同时去掉旧版的 `weight: 700`。
    body = body.replace(
        '("Source Han Sans SC", "Noto Sans CJK SC", "Noto Sans SC")',
        '("Noto Sans CJK SC", "Noto Sans SC")',
    )
    body = body.replace(
        '#text(font: ("Noto Sans CJK SC", "Noto Sans SC"), weight: 700)',
        '#text(font: ("Noto Sans CJK SC", "Noto Sans SC"))',
    )

    # 用 is-pad-page state 包裹 `#pagebreak(to: "odd"[, weak: true])`，
    # 使自动插入的 padding 空白页不带页脚（避免 "viii | 精华提要" 残留）。
    # 只匹配"尚未被 update(true) 前置"的 pagebreak，避免重复包裹。
    body = re.sub(
        r'(?<!is-pad-page\.update\(true\)\n)'
        r'(#pagebreak\(to: "odd"(?:, weak: true)?\))',
        r'#is-pad-page.update(true)\n\1\n#is-pad-page.update(false)',
        body,
    )

    new_source = new_preamble + "\n" + body

    # 输出到同目录下的 repatch 文件
    ts = time.strftime("%Y%m%d_%H%M%S")
    out_typ = task_dir / f"book_repatch_{ts}.typ"
    out_pdf = task_dir / f"book_repatch_{ts}.pdf"
    out_typ.write_text(new_source, encoding="utf-8")
    print(f"[repatch] 新 Typst 源码: {out_typ}")

    # 调用 typst 库直接编译（绕过异步路径）
    import typst as typst_lib
    from core.config import settings

    font_paths = []
    fonts_dir = Path(__file__).resolve().parent.parent / "app" / "workflow" / "knowledge" / "assets" / "fonts"
    if fonts_dir.exists():
        font_paths.append(str(fonts_dir))
    if settings.TYPESET_FONT_PATHS:
        font_paths.extend(
            p.strip() for p in settings.TYPESET_FONT_PATHS.split(",") if p.strip()
        )

    t0 = time.perf_counter()
    typst_lib.compile(str(out_typ), output=str(out_pdf), font_paths=font_paths)
    elapsed = time.perf_counter() - t0
    size_kb = out_pdf.stat().st_size / 1024
    print(f"[repatch] PDF 已编译: {out_pdf} ({size_kb:.0f} KB, {elapsed:.1f}s)")


if __name__ == "__main__":
    main()
