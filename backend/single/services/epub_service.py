"""
EPUB 导出服务

从工作流结构化产物生成 EPUB 3.0 电子书。
"""

import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ebooklib import epub


def _normalize_lang(lang: str) -> str:
    return "en" if lang.lower().startswith("en") else "zh"


def _ui_strings(output_language: str) -> Dict[str, str]:
    if _normalize_lang(output_language) == "en":
        return {
            "preface": "Editor's Note",
            "summary": "Highlights",
            "epigraph_title": "Epigraph",
            "refs_title": "Source",
            "refs_podcast": "This book is adapted from the podcast",
            "refs_episode": "Episode",
            "refs_date": "Published",
            "refs_listen": "Listen",
            "fallback_title": "Podcast Book",
        }
    return {
        "preface": "编者序",
        "summary": "精华提要",
        "epigraph_title": "题记",
        "refs_title": "参考来源",
        "refs_podcast": "本书内容整理自播客节目",
        "refs_episode": "单集",
        "refs_date": "发布日期",
        "refs_listen": "收听链接",
        "fallback_title": "播客书稿",
    }


EPUB_CSS = """\
body {
  font-family: "Noto Serif SC", "Songti SC", "STSong", "Source Han Serif SC",
               "Georgia", "Times New Roman", serif;
  line-height: 1.8;
  color: #2D2D2D;
  margin: 0;
  padding: 0;
}
h1, h2, h3 {
  text-align: center;
  color: #6B3A2A;
  margin-top: 2em;
  margin-bottom: 1em;
}
h1 { font-size: 1.6em; }
h2 { font-size: 1.3em; }
p {
  text-indent: 0;
  margin: 0.6em 0;
}
p.no-indent {
  text-indent: 0;
}
p.speaker {
  text-indent: 0;
  margin-top: 1.2em;
}
p.speaker strong {
  font-family: "Noto Sans SC", "Noto Sans CJK SC", "PingFang SC", "Heiti SC",
               "Source Han Sans SC", sans-serif;
  color: #6B3A2A;
}
.pullquote {
  margin: 1.5em 1em;
  padding: 0.8em 1em;
  border-left: 3px solid #C4A882;
  font-style: italic;
  color: #6B3A2A;
  text-indent: 0;
}
.pullquote .attribution {
  display: block;
  text-align: right;
  font-style: normal;
  color: #999;
  margin-top: 0.5em;
}
.epigraph {
  margin: 3em 1.5em;
  text-align: center;
}
.epigraph .quote-text {
  font-size: 1.2em;
  font-style: italic;
  color: #6B3A2A;
  line-height: 2;
}
.epigraph .attribution {
  margin-top: 1.5em;
  color: #999;
}
figure {
  margin: 1.5em auto;
  text-align: center;
  text-indent: 0;
  page-break-inside: avoid;
}
figure img {
  display: block;
  max-width: 100%;
  height: auto;
  margin: 0 auto;
}
figcaption {
  display: block;
  font-size: 0.85em;
  color: #999;
  margin-top: 0.5em;
  text-indent: 0;
}
.footnote-ref {
  font-size: 0.75em;
  vertical-align: super;
  color: #6B3A2A;
  text-decoration: none;
}
.footnotes {
  margin-top: 2em;
  border-top: 1px solid #C4A882;
  padding-top: 0.8em;
  font-size: 0.85em;
  color: #666;
}
.footnotes p {
  text-indent: 0;
  margin: 0.3em 0;
}
.refs-section {
  margin-top: 2em;
  border-top: 1px solid #C4A882;
  padding-top: 1em;
}
.refs-section h2 {
  text-align: left;
  font-size: 1.1em;
}
.refs-section p {
  text-indent: 0;
  font-size: 0.9em;
  color: #333;
}
.summary-item {
  text-indent: 0;
  margin: 0.5em 0 0.5em 1em;
}
.summary-item strong {
  color: #6B3A2A;
}
.cover-page {
  text-align: center;
  padding: 0;
  margin: 0;
}
.cover-page img {
  max-width: 100%;
  max-height: 100%;
}
"""


def _split_footnotes(content: str) -> Tuple[str, Dict[int, str]]:
    match = re.search(r"\n\n---\n+(\[\^\d+\]:.+)$", content, re.DOTALL)
    if match:
        body = content[:match.start()]
        fn_section = match.group(1)
    else:
        body = content
        fn_section = ""

    footnotes: Dict[int, str] = {}
    for fn_match in re.finditer(r"\[\^(\d+)\]:\s*(.+?)(?=\n\[\^|\Z)", fn_section, re.DOTALL):
        num = int(fn_match.group(1))
        text = re.sub(r"\*\*(.+?)\*\*", r"\1", fn_match.group(2).strip())
        footnotes[num] = text

    return body, footnotes


def _extract_speaker(text: str) -> Tuple[Optional[str], str]:
    stripped = text.strip()
    for pattern in (
        r"^\*\*(.+?)[：:]\*\*\s*(.*)$",
        r"^\*\*(.+?)\*\*[：:]\s*(.*)$",
        r"^([^:\n：]{1,40})[：:]\s+(.*)$",
    ):
        match = re.match(pattern, stripped)
        if not match:
            continue
        name = match.group(1).strip()
        rest = match.group(2).strip()
        if re.match(r"^(说话人\d*|Speaker\s*\d*)$", name, re.IGNORECASE):
            return None, text
        if rest.lower().startswith(name.lower()):
            rest = rest[len(name):].lstrip(" ,.:;：")
        return name, rest
    return None, text


def _md_bold_to_html(text: str) -> str:
    return re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)


def _strip_md_emphasis(text: str) -> str:
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"__(.+?)__", r"\1", text)
    text = re.sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)", r"\1", text)
    text = re.sub(r"(?<!_)_([^_\n]+)_(?!_)", r"\1", text)
    return text


def _parse_quote_attribution(text: str) -> Tuple[str, str]:
    def _trim_quote_marks(raw: str) -> str:
        raw = raw.strip()
        raw = re.sub(r"^[\u300c\u300e\u201c\"'`｢\u2018]+", "", raw)
        raw = re.sub(r"[\u300d\u300f\u201d\"'`｣\u2019]+$", "", raw)
        return raw.strip()

    text = _trim_quote_marks(text)
    parts = re.split(r"\s*(?:\u2014\u2014|\u2014|--)\s*", text, maxsplit=1)
    quote = _trim_quote_marks(parts[0])
    candidate = parts[1].strip() if len(parts) > 1 else ""
    if candidate and len(candidate) <= 15:
        return quote, candidate
    if candidate:
        return quote, ""
    return quote, ""


def _parse_para_pos(raw: Any) -> Optional[int]:
    if raw is None:
        return None
    if isinstance(raw, int):
        return raw
    value = str(raw).strip()
    if value.upper().startswith("P"):
        value = value[1:]
    try:
        return int(value)
    except ValueError:
        return None


def _escape_html(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


_META_SECTION_RE = re.compile(
    r"^(?:#{1,6}\s*)?(?:key takeaways?|highlights?|summary|overview|discussion points?|"
    r"核心要点|要点总结|精华提要|内容提要|总结|概述)\s*$",
    re.IGNORECASE,
)


def _filter_body_paragraphs(paragraphs: List[str], chapter_title: str) -> List[str]:
    result: List[str] = []
    skipping_summary = False
    for para in paragraphs:
        stripped = para.strip()
        if not stripped:
            continue
        heading_text = re.sub(r"^#{1,6}\s*", "", stripped).strip()
        if _META_SECTION_RE.match(stripped) or _META_SECTION_RE.match(heading_text):
            skipping_summary = True
            continue
        if heading_text.casefold() == chapter_title.strip().casefold():
            continue
        if skipping_summary:
            if re.match(r"^\*\*.+?:\*\*", stripped):
                skipping_summary = False
            elif re.match(r"^[-*•]\s+", stripped) or re.match(r"^\d+[.)]\s+", stripped):
                continue
            else:
                skipping_summary = False
        if re.match(r"^#{1,6}\s+", stripped):
            continue
        result.append(stripped)
    return result


def _looks_like_inline_heading(para: str, chapter_title: str = "") -> bool:
    p = para.strip()
    if not p or "\n" in p or len(p) > 100:
        return False
    if chapter_title and p.casefold() == chapter_title.strip().casefold():
        return False
    if re.search(r"[.!?。！？]$", p):
        return False
    if p.startswith(("**", "#", ">", "-", "* ")):
        return False
    words = re.findall(r"[A-Za-z][A-Za-z'’-]*", p)
    titled = [word for word in words if word[:1].isupper()]
    has_colon = ":" in p or "：" in p
    return (has_colon and len(words) >= 3) or (len(words) >= 4 and len(titled) / max(len(words), 1) >= 0.6)


def _map_highlights_to_chapters(
    chapters: List[Dict[str, Any]],
    highlights: Dict[str, Any],
    illustrations: Optional[Dict[str, Any]] = None,
) -> Tuple[Optional[Dict[str, Any]], Dict[str, List[Dict[str, Any]]], Dict[str, List[Dict[str, Any]]]]:
    epigraph: Optional[Dict[str, Any]] = None
    quotes_by_title: Dict[str, List[Dict[str, Any]]] = {chapter["title"]: [] for chapter in chapters if chapter.get("title")}
    illustrations_by_title: Dict[str, List[Dict[str, Any]]] = {
        chapter["title"]: [] for chapter in chapters if chapter.get("title")
    }

    first_string_quote: Optional[str] = None
    for quote in highlights.get("quotes", []):
        if isinstance(quote, str):
            if first_string_quote is None:
                first_string_quote = quote
            continue
        if quote.get("placement") == "epigraph":
            epigraph = quote
        elif quote.get("chapter_title") in quotes_by_title:
            quotes_by_title[quote["chapter_title"]].append(quote)

    if not epigraph and first_string_quote:
        epigraph = {"text": first_string_quote, "placement": "epigraph"}

    if illustrations:
        for image in illustrations.get("images", []):
            chapter_title = image.get("chapter_title", "")
            if chapter_title in illustrations_by_title:
                illustrations_by_title[chapter_title].append(image)

    return epigraph, quotes_by_title, illustrations_by_title


_PH_PREFIX = "\x00FN"
_PH_SUFFIX = "\x00"


def _build_paragraph_html(
    text: str,
    footnotes: Dict[int, str],
    chapter_fn_list: List[Tuple[int, str]],
    emitted_fn: set,
) -> str:
    speaker, rest = _extract_speaker(text)
    placeholders: Dict[str, str] = {}

    def _replace_fn(match: re.Match[str]) -> str:
        num = int(match.group(1))
        key = f"{_PH_PREFIX}{num}{_PH_SUFFIX}"
        if num in footnotes and num not in emitted_fn:
            emitted_fn.add(num)
            chapter_fn_list.append((num, footnotes[num]))
            placeholders[key] = (
                f'<a class="footnote-ref" epub:type="noteref" href="#fn{num}">[{len(chapter_fn_list)}]</a>'
            )
        else:
            placeholders[key] = ""
        return key

    body = re.sub(r"\[\^(\d+)\]", _replace_fn, rest if speaker else text)
    body = _strip_md_emphasis(_escape_html(body))
    for key, html_frag in placeholders.items():
        body = body.replace(key, html_frag)

    if speaker:
        return f'<p class="speaker"><strong>{_escape_html(speaker)}：</strong>{body}</p>'
    return f"<p>{body}</p>"


def _build_pullquote_html(quote: Dict[str, Any]) -> str:
    text, speaker = _parse_quote_attribution(quote.get("text", ""))
    if not text:
        return ""
    html = f'<div class="pullquote">{_escape_html(text)}'
    if speaker:
        html += f'<span class="attribution">—— {_escape_html(speaker)}</span>'
    html += "</div>"
    return html


def _build_illustration_html(illustration: Dict[str, Any], image_map: Dict[str, str]) -> str:
    filename = illustration.get("filename", "")
    epub_image_name = image_map.get(filename, "")
    if not epub_image_name:
        return ""
    caption = illustration.get("caption", "")
    html = f'<figure><img src="{epub_image_name}" alt="{_escape_html(caption)}" />'
    if caption:
        html += f"<figcaption>{_escape_html(caption)}</figcaption>"
    html += "</figure>"
    return html


def _build_footnotes_html(fn_list: List[Tuple[int, str]]) -> str:
    if not fn_list:
        return ""
    html = '<div class="footnotes">'
    for idx, (num, text) in enumerate(fn_list, 1):
        html += f'<p id="fn{num}">[{idx}] {_escape_html(text)}</p>'
    html += "</div>"
    return html


def _build_chapter_xhtml(
    chapter: Dict[str, Any],
    quotes: List[Dict[str, Any]],
    illustrations: List[Dict[str, Any]],
    image_map: Dict[str, str],
    chapter_index: int,
) -> str:
    title = chapter.get("title", f"Chapter {chapter_index + 1}")
    body, footnotes = _split_footnotes(chapter.get("content", ""))
    body = re.sub(r"【章节标题】\s*\n.*?\n", "", body, count=1)
    body = re.sub(r"【章节内容】\s*\n?", "", body, count=1)
    paragraphs = _filter_body_paragraphs([p.strip() for p in body.split("\n\n") if p.strip()], chapter.get("title", ""))
    if len(paragraphs) >= 2 and _looks_like_inline_heading(paragraphs[0], chapter.get("title", "")):
        paragraphs = paragraphs[1:]

    insertions: Dict[int, List[Tuple[str, Dict[str, Any]]]] = {}
    for quote in quotes:
        if quote.get("placement") != "inline_card":
            continue
        pos = _parse_para_pos(quote.get("after_paragraph"))
        if pos is not None:
            insertions.setdefault(pos, []).append(("quote", quote))

    for illustration in illustrations:
        pos = _parse_para_pos(illustration.get("after_paragraph"))
        if pos is not None:
            insertions.setdefault(pos, []).append(("illustration", illustration))

    chapter_fn_list: List[Tuple[int, str]] = []
    emitted_fn: set = set()
    parts: List[str] = [f"<h1>{_escape_html(title)}</h1>"]

    for idx, para in enumerate(paragraphs):
        if para.startswith("> ") or para.startswith(">"):
            quote_text = re.sub(r"^>\s?", "", para, flags=re.MULTILINE).strip()
            if quote_text:
                parts.append(
                    f'<blockquote><p class="no-indent">{_strip_md_emphasis(_escape_html(quote_text))}</p></blockquote>'
                )
        else:
            parts.append(_build_paragraph_html(para, footnotes, chapter_fn_list, emitted_fn))

        for insert_type, insert_data in insertions.get(idx, []):
            if insert_type == "quote":
                parts.append(_build_pullquote_html(insert_data))
            else:
                parts.append(_build_illustration_html(insert_data, image_map))

    parts.append(_build_footnotes_html(chapter_fn_list))
    return "\n".join(parts)


def _build_preface_xhtml(text: str, ui: Dict[str, str]) -> str:
    if not text or not text.strip():
        return ""
    parts = [f'<h1>{_escape_html(ui["preface"])}</h1>']
    normalized = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    paragraphs = [p.strip() for p in re.split(r"\n+", normalized) if p.strip()]
    for para in paragraphs:
        if not re.match(r"^#{1,3}\s", para):
            parts.append(f"<p>{_md_bold_to_html(_escape_html(para))}</p>")
    return "\n".join(parts)


def _build_summary_xhtml(bullets: List[str], ui: Dict[str, str]) -> str:
    if not bullets:
        return ""
    parts = [f'<h1>{_escape_html(ui["summary"])}</h1>']
    for bullet in bullets:
        parts.append(f'<p class="summary-item">{_md_bold_to_html(_escape_html(bullet))}</p>')
    return "\n".join(parts)


def _build_references_xhtml(source_meta: Dict[str, str], ui: Dict[str, str]) -> str:
    podcast_name = source_meta.get("podcast_name", "")
    title = source_meta.get("title", "")
    publish_date = source_meta.get("publish_date", "")
    podcast_url = source_meta.get("podcast_url", "")
    if not podcast_name and not title and not podcast_url:
        return ""

    parts = ['<div class="refs-section">', f'<h2>{_escape_html(ui["refs_title"])}</h2>']
    if podcast_name:
        parts.append(f'<p>{ui["refs_podcast"]}「{_escape_html(podcast_name)}」</p>')
    if title:
        parts.append(f'<p>{ui["refs_episode"]}: {_escape_html(title)}</p>')
    if publish_date:
        parts.append(f'<p>{ui["refs_date"]}: {_escape_html(publish_date)}</p>')
    if podcast_url:
        parts.append(f'<p>{ui["refs_listen"]}: <a href="{_escape_html(podcast_url)}">{_escape_html(podcast_url)}</a></p>')
    parts.append("</div>")
    return "\n".join(parts)


def _wrap_xhtml(body: str, title: str = "") -> str:
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        "<!DOCTYPE html>\n"
        '<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">\n'
        "<head>\n"
        f"  <title>{_escape_html(title)}</title>\n"
        '  <link rel="stylesheet" type="text/css" href="style/default.css" />\n'
        "</head>\n"
        "<body>\n"
        f"{body}\n"
        "</body>\n"
        "</html>"
    )


def _extract_epigraph_from_pdf(pdf_path: str, output_path: str) -> Optional[str]:
    import fitz

    doc = fitz.open(pdf_path)
    try:
        for idx in range(min(5, doc.page_count)):
            page = doc[idx]
            text = page.get_text().strip()
            if text and len(text) < 200 and ("\u2014" in text or "--" in text):
                page.get_pixmap(matrix=fitz.Matrix(2, 2)).save(output_path, jpg_quality=85)
                return output_path
    finally:
        doc.close()
    return None


def generate_epub(
    task_id: str,
    title: str,
    author: str,
    annotated_content: Dict[str, Any],
    highlights: Dict[str, Any],
    illustrations: Optional[Dict[str, Any]],
    editor_preface_content: str,
    source_meta: Dict[str, str],
    output_dir: str,
    output_language: str = "zh-CN",
    cover_image_path: Optional[str] = None,
    pdf_path: Optional[str] = None,
) -> str:
    ui = _ui_strings(output_language)
    lang = _normalize_lang(output_language)
    chapters = annotated_content.get("chapters", [])
    epigraph, quotes_map, illustrations_map = _map_highlights_to_chapters(chapters, highlights, illustrations)

    book = epub.EpubBook()
    book.set_identifier(f"echopress-{task_id}")
    book.set_title(title or ui["fallback_title"])
    book.set_language("en" if lang == "en" else "zh-CN")
    book.add_author(author or "EchoPress")

    css_item = epub.EpubItem(
        uid="style_default",
        file_name="style/default.css",
        media_type="text/css",
        content=EPUB_CSS.encode("utf-8"),
    )
    book.add_item(css_item)

    spine: List[Any] = []
    toc: List[Any] = []

    if cover_image_path and os.path.exists(cover_image_path):
        with open(cover_image_path, "rb") as file:
            cover_data = file.read()
        book.set_cover("images/cover.png", cover_data, create_page=False)
        cover = epub.EpubHtml(title="Cover", file_name="cover.xhtml", lang=book.language)
        cover.content = (
            '<?xml version="1.0" encoding="utf-8"?>\n'
            "<!DOCTYPE html>\n"
            '<html xmlns="http://www.w3.org/1999/xhtml">\n'
            "<head><title>Cover</title></head>\n"
            '<body style="margin:0;padding:0;text-align:center">\n'
            '<div class="cover-page"><img src="images/cover.png" alt="Cover" style="max-width:100%;max-height:100%" /></div>\n'
            "</body>\n"
            "</html>"
        ).encode("utf-8")
        book.add_item(cover)
        spine.append(cover)

    image_map: Dict[str, str] = {}
    if illustrations:
        for image in illustrations.get("images", []):
            filename = image.get("filename", "")
            if not filename:
                continue
            full_path = os.path.join(output_dir, filename)
            if not os.path.exists(full_path):
                continue
            with open(full_path, "rb") as file:
                image_data = file.read()
            epub_name = f"images/{filename.replace('/', '_')}"
            media_type = "image/png" if Path(filename).suffix.lower() == ".png" else "image/jpeg"
            book.add_item(epub.EpubItem(file_name=epub_name, media_type=media_type, content=image_data))
            image_map[filename] = epub_name

    def _make_chapter(chapter_title: str, file_name: str, body_html: str) -> epub.EpubHtml:
        chapter = epub.EpubHtml(title=chapter_title, file_name=file_name, lang=book.language)
        chapter.content = _wrap_xhtml(body_html, chapter_title).encode("utf-8")
        chapter.add_item(css_item)
        book.add_item(chapter)
        return chapter

    if epigraph and pdf_path and os.path.exists(pdf_path):
        epi_img_path = os.path.join(output_dir, "epigraph_page.jpg")
        if _extract_epigraph_from_pdf(pdf_path, epi_img_path):
            with open(epi_img_path, "rb") as file:
                epi_data = file.read()
            book.add_item(epub.EpubItem(file_name="images/epigraph.jpg", media_type="image/jpeg", content=epi_data))
            epigraph_page = epub.EpubHtml(title=ui["epigraph_title"], file_name="epigraph.xhtml", lang=book.language)
            epigraph_page.content = (
                '<?xml version="1.0" encoding="utf-8"?>\n'
                "<!DOCTYPE html>\n"
                '<html xmlns="http://www.w3.org/1999/xhtml">\n'
                f'<head><title>{_escape_html(ui["epigraph_title"])}</title></head>\n'
                '<body style="margin:0;padding:0;text-align:center">\n'
                '<div style="display:flex;align-items:center;justify-content:center;min-height:100vh">'
                '<img src="images/epigraph.jpg" alt="Epigraph" style="max-width:100%;max-height:100%" />'
                "</div>\n"
                "</body>\n"
                "</html>"
            ).encode("utf-8")
            book.add_item(epigraph_page)
            spine.append(epigraph_page)

    preface_text = editor_preface_content or annotated_content.get("preamble", {}).get("lead_paragraph", "")
    preface_html = _build_preface_xhtml(preface_text, ui)
    if preface_html:
        preface = _make_chapter(ui["preface"], "preface.xhtml", preface_html)
        spine.append(preface)
        toc.append(preface)

    summary_html = _build_summary_xhtml(annotated_content.get("preamble", {}).get("summary_bullets", []), ui)
    if summary_html:
        summary = _make_chapter(ui["summary"], "summary.xhtml", summary_html)
        spine.append(summary)
        toc.append(summary)

    spine.append("nav")

    for idx, chapter in enumerate(chapters):
        chapter_title = chapter.get("title", f"Chapter {idx + 1}")
        chapter_body = _build_chapter_xhtml(
            chapter,
            quotes_map.get(chapter_title, []),
            illustrations_map.get(chapter_title, []),
            image_map,
            idx,
        )
        chapter_page = _make_chapter(chapter_title, f"chapter_{idx:02d}.xhtml", chapter_body)
        spine.append(chapter_page)
        toc.append(chapter_page)

    refs_html = _build_references_xhtml(source_meta, ui)
    if refs_html:
        refs = _make_chapter(ui["refs_title"], "references.xhtml", refs_html)
        spine.append(refs)
        toc.append(refs)

    book.toc = toc
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    book.spine = spine

    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    epub_path = str(out_dir / "book.epub")
    epub.write_epub(epub_path, book, {})
    return epub_path


def invalidate_epub_cache(task_id: str) -> bool:
    """删除指定任务的 EPUB 缓存文件。

    用于编辑 / 重建 PDF 后主动失效，避免用户下载到与新 PDF 不一致的旧 EPUB。
    下次访问 `/tasks/{id}/download/epub` 时会按新内容重新生成。

    Returns:
        True 表示找到并删除了旧文件；False 表示不存在或删除失败（不抛异常）。
    """
    from core.config import settings

    epub_path = Path(settings.STORAGE_DIR) / task_id / "book.epub"
    try:
        if epub_path.exists():
            epub_path.unlink()
            return True
    except OSError:
        # 删除失败（权限 / race），下载时的 mtime 兜底会再判一次
        pass
    return False
