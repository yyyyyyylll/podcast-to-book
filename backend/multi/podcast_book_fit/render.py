"""HTML renderer for lightweight podcast-to-book fit judgment."""
from __future__ import annotations

import argparse
import asyncio
import html
import json
import os
from pathlib import Path
from typing import Any


def _esc(value: Any) -> str:
    return html.escape(str(value or ""), quote=True)


def _tags(items: list[str]) -> str:
    return "".join(f"<span>{_esc(item)}</span>" for item in items)


def public_series_clues(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return only reliable host-owned series clues.

    Customer screenshot pages currently hide this section entirely; keep this
    helper for data policy tests and future non-screenshot variants.
    """
    visible: list[dict[str, Any]] = []
    for item in items or []:
        title = item.get("topic_title") or item.get("title") or ""
        count = int(item.get("episode_count") or 0)
        if title and count >= 2:
            visible.append(item)
    return visible


def _series(items: list[dict[str, Any]]) -> str:
    if not items:
        return "<p class=\"muted\">暂未识别到明确专题栏目，可从标题连续性继续判断。</p>"
    return "".join(
        "<article class=\"line-item\">"
        f"<strong>{_esc(item.get('title') or item.get('topic_title'))}</strong>"
        f"<em>{int(item.get('episode_count') or 0)} 集</em>"
        f"<p>{_esc(item.get('why_it_matters') or '')}</p>"
        "</article>"
        for item in items
    )


def _directions(items: list[dict[str, Any]], *, book_unit: str = "book") -> str:
    cards: list[str] = []
    title_label = "章节标题候选：" if book_unit == "chapter" else "整书标题候选："
    for item in items:
        cards.append(
            "<article class=\"direction\">"
            f"<div class=\"field-label\">{title_label}</div>"
            f"<h3>{_esc(item.get('suggested_title') or item.get('direction'))}</h3>"
            f"<p><strong>主题方向：</strong>{_esc(item.get('direction'))}</p>"
            f"<p><strong>建议理由：</strong>{_esc(item.get('reason'))}</p>"
            f"<p><strong>内容依据：</strong>{_esc(item.get('evidence_title') or '可参考的节目内容')}</p>"
            f"{_topic_sections(item.get('topic_sections') or [])}"
            "</article>"
        )
    return "".join(cards) or "<p class=\"muted\">暂无可探索方向。</p>"


def _topic_sections(items: list[dict[str, Any]]) -> str:
    if not items:
        return ""
    blocks = []
    for item in items[:3]:
        episode_list = "".join(
            f"<li>{_esc(title)}</li>"
            for title in list(item.get("episode_titles") or [])[:4]
        )
        blocks.append(
            "<article class=\"topic-section\">"
            f"<h4>{_esc(item.get('section_title') or '可先收录的节目')}</h4>"
            f"<p>{_esc(item.get('section_reason'))}</p>"
            f"<ol>{episode_list}</ol>"
            "</article>"
        )
    return (
        "<div class=\"section-pack\">"
        "<div class=\"field-label\">可参考板块</div>"
        "<div class=\"section-grid\">"
        + "".join(blocks)
        + "</div></div>"
    )


def _sample_episode(item: dict[str, Any]) -> str:
    if not item:
        return ""
    sample_reason = "建议先用这一期做样章，方便快速看到单集改写后的成稿质感。"
    return (
        "<section class=\"sample-line\">"
        "<div class=\"sample-copy\">"
        "<div class=\"kicker\">推荐样章节目</div>"
        f"<h2>{_esc(item.get('episode_title'))}</h2>"
        f"<p><strong>所属方向：</strong>{_esc(item.get('source_direction'))}</p>"
        f"<p>{_esc(sample_reason)}</p>"
        "</div>"
        "</section>"
    )


def render_book_fit_html(result: dict[str, Any]) -> str:
    metadata = result.get("metadata") or {}
    book_unit = metadata.get("book_unit") or "book"
    name = result.get("podcast_name") or "播客成书初判"
    episode_count = metadata.get("episode_count") or metadata.get("rss_episode_count") or ""
    coverage = f"{episode_count} 期播客" if episode_count else "整档播客"
    sample = result.get("recommended_sample_episode") or {}
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{_esc(name)} · 播客成书建议</title>
  <style>
    :root {{
      --paper:#f7f2e8; --ink:#1e2424; --muted:#68716e; --line:#d9d0bf;
      --panel:#fffaf0; --teal:#126f68; --rust:#a7472c; --gold:#bd8b2a; --wash:#eee6d7;
    }}
    * {{ box-sizing:border-box; }}
    body {{
      margin:0; background:var(--paper); color:var(--ink);
      font-family:"Aptos","PingFang SC","Microsoft YaHei",sans-serif;
      line-height:1.62; letter-spacing:0.03em;
    }}
    body::before {{
      content:""; position:fixed; inset:0; pointer-events:none;
      background-image:linear-gradient(rgba(30,36,36,.045) 1px, transparent 1px);
      background-size:100% 9px; mix-blend-mode:multiply;
    }}
    .shell {{ max-width:1180px; margin:0 auto; padding:18px 20px 34px; }}
    .screenshot-sheet {{ min-height:calc(100vh - 38px); }}
    .client-note {{ display:flex; justify-content:space-between; gap:12px; align-items:center; border-bottom:2px solid var(--ink); padding-bottom:8px; margin-bottom:10px; color:var(--muted); font-size:12px; }}
    .client-note a {{ color:var(--teal); text-decoration:none; font-weight:800; white-space:nowrap; }}
    .client-note a:hover {{ text-decoration:underline; }}
    .title-band {{ display:grid; grid-template-columns:minmax(0,1fr) auto; gap:12px; align-items:end; margin-bottom:10px; }}
    .kicker {{ color:var(--rust); font-size:11px; font-weight:800; }}
    h1 {{ margin:3px 0 0; font-size:clamp(28px,4vw,44px); line-height:1; letter-spacing:0; }}
    section {{ margin:0 0 34px; }}
    h2 {{ margin:0 0 12px; font-size:18px; letter-spacing:0.02em; }}
    h3 {{ margin:0 0 4px; font-size:15px; letter-spacing:0.02em; }}
    h4 {{ margin:0 0 4px; font-size:13px; color:var(--teal); }}
    .doc-meta {{ border:1px solid var(--line); background:var(--panel); padding:7px 9px; color:var(--muted); min-width:128px; text-align:right; font-size:12px; }}
    .summary {{ border:1px solid var(--line); background:var(--panel); padding:15px 17px; font-size:15px; border-left:5px solid var(--teal); }}
    .muted {{ color:var(--muted); }}
    .tags {{ display:flex; flex-wrap:wrap; gap:6px; margin-top:8px; }}
    .tags span {{ border:1px solid rgba(18,111,104,.26); background:#eef6f2; color:var(--teal); padding:3px 8px; font-size:12px; }}
    .grid {{ display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:18px; }}
    .compact-grid {{ align-items:start; }}
    .directions-section h2 {{ break-after:avoid; page-break-after:avoid; }}
    .panel,.line-item,.direction {{ border:1px solid var(--line); background:var(--panel); padding:14px; }}
    .line-item {{ margin-bottom:10px; }}
    .line-item strong {{ display:block; color:var(--teal); }}
    .line-item em {{ font-style:normal; color:var(--rust); font-weight:700; }}
    .field-label {{ color:var(--muted); font-size:11px; font-weight:800; margin-bottom:4px; }}
    .direction h3 {{ color:var(--rust); }}
    .direction p {{ margin:4px 0 0; font-size:13px; }}
    .sample-line {{ background:var(--ink); color:var(--paper); padding:22px; margin-top:0; }}
    .sample-line .kicker {{ color:#f0c48e; }}
    .sample-line h2 {{ margin:5px 0 9px; font-size:26px; color:var(--paper); }}
    .sample-line p {{ margin:6px 0; font-size:14px; }}
    .sample-line span {{ display:block; margin-top:9px; color:#f3d5a6; font-size:13px; }}
    .sample-line .field-label {{ color:#f3d5a6; }}
    .section-pack {{ border-top:1px solid var(--line); margin-top:14px; padding-top:12px; }}
    .sample-line .section-pack {{ border-top:0; margin-top:0; padding-top:0; }}
    .section-grid {{ display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:10px; }}
    .topic-section {{ background:#fffaf0; color:var(--ink); border:1px solid var(--line); padding:10px; }}
    .topic-section p {{ margin:0 0 5px; color:var(--muted); font-size:12px; line-height:1.45; }}
    .topic-section ol {{ margin:5px 0 0; padding-left:18px; }}
    .topic-section li {{ margin:2px 0; font-size:12px; line-height:1.38; }}
    .actions {{ display:flex; gap:8px; justify-content:flex-end; margin-top:10px; }}
    .actions a {{ border:1px solid var(--teal); background:#eef6f2; color:var(--teal); padding:6px 10px; font-size:12px; font-weight:800; text-decoration:none; }}
    @media(max-width:760px) {{
      .title-band {{ grid-template-columns:1fr; }}
      .doc-meta {{ text-align:left; }}
      .section-grid,.grid {{ grid-template-columns:1fr; }}
    }}
    @page {{
      size:A4;
      margin:15mm 14mm;
    }}
    @media print {{
      body {{ background:#fffaf0; }}
      body::before,.client-note a,.actions {{ display:none; }}
      .shell {{ max-width:none; padding:0; }}
      .screenshot-sheet {{ min-height:0; }}
      section {{ margin-bottom:28px; }}
      .direction,.panel,.summary,.sample-line {{ break-inside:avoid; page-break-inside:avoid; }}
      .direction {{ break-inside:avoid-page; page-break-inside:avoid; }}
      .directions-section h2 {{ break-after:avoid; page-break-after:avoid; }}
      .directions-section .compact-grid {{ break-before:avoid; page-break-before:avoid; }}
    }}
  </style>
</head>
<body>
  <main class="shell screenshot-sheet">
    <div class="client-note">
      <strong>EchoPress 播客成书建议</strong>
      <span>{_esc(name)} · {_esc(coverage)} · <a href="http://127.0.0.1:8765/" target="_blank" rel="noopener">生成其他播客建议</a></span>
    </div>
    <div class="actions"><a href="book_fit_judgment.pdf" download>导出 PDF</a></div>
    <section class="title-band">
      <div>
        <div class="kicker">Podcast Book Proposal</div>
        <h1>{_esc(name)}成书建议</h1>
      </div>
      <div class="doc-meta">
        <strong>{_esc(coverage)}</strong><br>
        <span>{len(result.get('possible_book_directions') or [])} 个方向</span>
      </div>
    </section>

    <section>
      <h2>初步建议</h2>
      <div class="summary">{_esc(result.get('book_fit_summary'))}</div>
      <div class="tags">{_tags(result.get('host_profile_tags') or [])}</div>
    </section>

    {_sample_episode(sample)}

    <section class="directions-section">
      <h2>{"可参考的大章节 / 样章方向" if book_unit == "chapter" else "可参考的主题 / 标题方向"}</h2>
      <div class="grid compact-grid">{_directions(result.get('possible_book_directions') or [], book_unit=book_unit)}</div>
    </section>
  </main>
</body>
</html>
"""


async def render_book_fit_pdf(html_path: Path, pdf_path: Path) -> bool:
    """Render the customer-facing HTML report to PDF with Playwright."""
    browser_path = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    if browser_path and not Path(browser_path).expanduser().exists():
        os.environ.pop("PLAYWRIGHT_BROWSERS_PATH", None)
    try:
        from playwright.async_api import async_playwright
    except Exception:
        return False

    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page(viewport={"width": 1240, "height": 1754})
        await page.goto(html_path.resolve().as_uri(), wait_until="networkidle")
        await page.pdf(
            path=str(pdf_path),
            format="A4",
            print_background=True,
            margin={"top": "15mm", "right": "14mm", "bottom": "15mm", "left": "14mm"},
        )
        await browser.close()
    return pdf_path.exists() and pdf_path.stat().st_size > 0


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    source = Path(args.json).expanduser().resolve()
    out = Path(args.out).expanduser().resolve()
    out.write_text(render_book_fit_html(json.loads(source.read_text(encoding="utf-8"))), encoding="utf-8")
    pdf = out.with_suffix(".pdf")
    try:
        asyncio.run(render_book_fit_pdf(out, pdf))
    except Exception:
        pass
    print(f"[OK] wrote {out}")


if __name__ == "__main__":
    main()
