"""Static HTML report renderer for podcast direction diagnosis."""
from __future__ import annotations

import argparse
import html
import json
from pathlib import Path
from typing import Any

from multi.podcast_direction.value_labels import public_editing_value


def _esc(value: Any) -> str:
    return html.escape(str(value or ""), quote=True)


def _list(items: list[Any], *, empty: str = "—") -> str:
    if not items:
        return f"<span class=\"muted\">{_esc(empty)}</span>"
    return "".join(f"<li>{_esc(item)}</li>" for item in items)


def _title_items(items: list[dict[str, Any]]) -> str:
    if not items:
        return "<p class=\"muted\">暂无</p>"
    return "".join(
        "<article class=\"title-row\">"
        f"<strong>{_esc(item.get('title'))}</strong>"
        f"<span>{_esc(item.get('reason'))}</span>"
        "</article>"
        for item in items
    )


def _direction_cards(directions: list[dict[str, Any]]) -> str:
    if not directions:
        return "<p class=\"muted\">暂无方向结构</p>"
    cards: list[str] = []
    for item in directions:
        evidence = "".join(
            f"<span class=\"evidence\">{_esc(title)}</span>"
            for title in (item.get("episode_evidence") or [])[:8]
        )
        topics = "".join(
            f"<li>{_esc(topic)}</li>" for topic in (item.get("sub_topics") or [])
        )
        cards.append(
            "<article class=\"direction-card\">"
            "<div class=\"direction-head\">"
            f"<h3>{_esc(item.get('direction_name'))}</h3>"
            f"<span class=\"value value-{_esc(public_editing_value(item.get('editing_value')))}\">{_esc(public_editing_value(item.get('editing_value')))}</span>"
            "</div>"
            f"<p>{_esc(item.get('direction_description'))}</p>"
            f"<ul class=\"compact-list\">{topics}</ul>"
            f"<div class=\"evidence-row\">{evidence}</div>"
            "</article>"
        )
    return "".join(cards)


def _topic_cards(topics: list[dict[str, Any]]) -> str:
    if not topics:
        return "<p class=\"muted\">未识别到主播显式专题链接。</p>"
    cards: list[str] = []
    for topic in topics:
        episodes = "".join(
            f"<li>{_esc(title)}</li>" for title in (topic.get("episode_titles") or [])
        )
        cards.append(
            "<article class=\"topic-card\">"
            "<div>"
            f"<a href=\"{_esc(topic.get('url'))}\" target=\"_blank\" rel=\"noreferrer\">{_esc(topic.get('topic_title'))}</a>"
            f"<span>{int(topic.get('episode_count') or 0)} 集</span>"
            "</div>"
            f"<ol>{episodes}</ol>"
            "</article>"
        )
    return "".join(cards)


def render_report_html(final_json: dict[str, Any]) -> str:
    overall = final_json.get("overall_diagnosis") or {}
    structure = final_json.get("direction_structure") or {}
    titles = final_json.get("title_suggestions") or {}
    customer = final_json.get("customer_diagnosis") or {}
    qc = final_json.get("quality_check") or {}
    metadata = final_json.get("metadata") or {}
    topics = final_json.get("host_topic_references") or []

    podcast_name = overall.get("podcast_name") or "播客诊断"
    episode_count = metadata.get("_input_episode_count") or 0
    sampled = metadata.get("_input_sampled")
    episode_label = f"完整 {episode_count} 集" if episode_count else "整档"
    if sampled:
        episode_label = f"采样 {episode_count} 集"
    warnings = qc.get("warnings") or []

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{_esc(podcast_name)} · 播客方向诊断</title>
  <style>
    :root {{
      --paper: #f7f2e8;
      --ink: #1e2424;
      --muted: #68716e;
      --line: #d9d0bf;
      --panel: #fffaf0;
      --teal: #126f68;
      --rust: #a7472c;
      --violet: #655184;
      --gold: #bd8b2a;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      color: var(--ink);
      background: var(--paper);
      font-family: "Aptos", "PingFang SC", "Microsoft YaHei", sans-serif;
      line-height: 1.65;
    }}
    body::before {{
      content: "";
      position: fixed;
      inset: 0;
      pointer-events: none;
      background-image: linear-gradient(rgba(30,36,36,.045) 1px, transparent 1px);
      background-size: 100% 9px;
      mix-blend-mode: multiply;
    }}
    .layout {{ display: grid; grid-template-columns: 260px minmax(0, 1fr); min-height: 100vh; }}
    aside {{ position: sticky; top: 0; height: 100vh; padding: 28px 22px; border-right: 1px solid var(--line); background: #eee6d7; }}
    aside h1 {{ margin: 0 0 8px; font-size: 28px; line-height: 1.1; letter-spacing: 0; }}
    .meta {{ color: var(--muted); font-size: 13px; }}
    nav {{ margin-top: 30px; display: grid; gap: 8px; }}
    nav a {{ color: var(--ink); text-decoration: none; padding: 8px 0; border-bottom: 1px solid rgba(30,36,36,.12); }}
    main {{ padding: 36px min(6vw, 72px) 64px; }}
    .hero {{ display: grid; grid-template-columns: minmax(0, 1.3fr) minmax(260px, .7fr); gap: 28px; align-items: end; border-bottom: 2px solid var(--ink); padding-bottom: 24px; }}
    .kicker {{ color: var(--rust); font-size: 13px; font-weight: 700; letter-spacing: 0; }}
    h2 {{ margin: 34px 0 14px; font-size: 22px; letter-spacing: 0; }}
    h3 {{ margin: 0; font-size: 18px; letter-spacing: 0; }}
    .hero-title {{ margin: 8px 0 14px; font-size: clamp(36px, 6vw, 74px); line-height: .96; letter-spacing: 0; }}
    .summary {{ font-size: 17px; max-width: 780px; }}
    .metrics {{ display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }}
    .metric {{ border: 1px solid var(--line); background: var(--panel); padding: 14px; min-height: 92px; }}
    .metric strong {{ display: block; font-size: 24px; line-height: 1.2; color: var(--teal); }}
    .metric span {{ color: var(--muted); font-size: 13px; }}
    .section {{ padding-top: 8px; }}
    .direction-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 14px; }}
    .direction-card, .topic-card, .copy-box, .title-row, .warning-row {{ border: 1px solid var(--line); background: var(--panel); padding: 16px; }}
    .direction-head {{ display: flex; gap: 12px; justify-content: space-between; align-items: center; }}
    .value {{ min-width: 34px; height: 34px; display: inline-grid; place-items: center; border-radius: 50%; color: white; background: var(--violet); font-weight: 700; }}
    .value-高 {{ background: var(--rust); }}
    .value-高 {{ background: var(--gold); }}
    .compact-list {{ padding-left: 20px; margin: 10px 0; }}
    .evidence-row {{ display: flex; flex-wrap: wrap; gap: 8px; margin-top: 12px; }}
    .evidence {{ border: 1px solid rgba(18,111,104,.28); color: var(--teal); padding: 4px 8px; font-size: 13px; background: #eef6f2; }}
    .topic-list {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); gap: 14px; }}
    .topic-card div {{ display: flex; justify-content: space-between; gap: 12px; border-bottom: 1px solid var(--line); padding-bottom: 8px; }}
    .topic-card a {{ color: var(--teal); font-weight: 800; text-decoration-thickness: 1px; }}
    .topic-card ol {{ margin: 12px 0 0; padding-left: 20px; }}
    .title-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 14px; }}
    .title-row {{ margin-bottom: 10px; }}
    .title-row strong {{ display: block; color: var(--rust); }}
    .copy-box {{ font-size: 17px; border-left: 5px solid var(--teal); }}
    .warning-row {{ border-left: 5px solid var(--rust); margin-bottom: 10px; }}
    .muted {{ color: var(--muted); }}
    @media (max-width: 840px) {{
      .layout {{ display: block; }}
      aside {{ position: static; height: auto; border-right: 0; border-bottom: 1px solid var(--line); }}
      nav {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
      main {{ padding: 24px 18px 44px; }}
      .hero {{ grid-template-columns: 1fr; }}
      .metrics {{ grid-template-columns: 1fr 1fr; }}
    }}
  </style>
</head>
<body>
  <div class="layout">
    <aside>
      <div class="kicker">EchoPress Workbench</div>
      <h1>{_esc(podcast_name)}</h1>
      <div class="meta">{_esc(episode_label)} · 方向诊断看板</div>
      <nav>
        <a href="#summary">总览</a>
        <a href="#topics">主播专题</a>
        <a href="#directions">方向结构</a>
        <a href="#titles">标题建议</a>
        <a href="#customer">客户文案</a>
        <a href="#quality">质检</a>
      </nav>
    </aside>
    <main>
      <section class="hero" id="summary">
        <div>
          <div class="kicker">内容方向诊断</div>
          <div class="hero-title">{_esc(podcast_name)}</div>
          <p class="summary">{_esc(overall.get('overall_content_judgment'))}</p>
        </div>
        <div class="metrics">
          <div class="metric"><strong>{_esc(episode_label)}</strong><span>输入覆盖</span></div>
          <div class="metric"><strong>{_esc(overall.get('content_structure_type'))}</strong><span>结构判断</span></div>
          <div class="metric"><strong>{_esc(structure.get('priority_direction'))}</strong><span>优先方向</span></div>
          <div class="metric"><strong>{_esc(qc.get('confidence_level') or '—')}</strong><span>质检信心</span></div>
        </div>
      </section>

      <section class="section" id="topics">
        <h2>主播已有专题</h2>
        <div class="topic-list">{_topic_cards(topics)}</div>
      </section>

      <section class="section" id="directions">
        <h2>方向结构</h2>
        <div class="direction-grid">{_direction_cards(structure.get('directions') or [])}</div>
      </section>

      <section class="section" id="titles">
        <h2>标题建议</h2>
        <div class="title-grid">
          <div><h3>克制专业型</h3>{_title_items(titles.get('professional_titles') or [])}</div>
          <div><h3>文艺表达型</h3>{_title_items(titles.get('literary_titles') or [])}</div>
          <div><h3>清晰说明型</h3>{_title_items(titles.get('clear_titles') or [])}</div>
        </div>
      </section>

      <section class="section" id="customer">
        <h2>客户可见诊断</h2>
        <div class="copy-box">{_esc(customer.get('diagnosis_text'))}</div>
      </section>

      <section class="section" id="quality">
        <h2>质检</h2>
        <p class="muted">AI 复核：{_esc(qc.get('ai_review_summary') or '—')}</p>
        {"".join(f'<div class="warning-row">{_esc(w)}</div>' for w in warnings) or '<p class="muted">暂无质检警告。</p>'}
      </section>
    </main>
  </div>
</body>
</html>
"""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", required=True, help="diagnosis JSON path")
    parser.add_argument("--out", required=True, help="HTML output path")
    args = parser.parse_args()

    source = Path(args.json).expanduser().resolve()
    out = Path(args.out).expanduser().resolve()
    final_json = json.loads(source.read_text(encoding="utf-8"))
    out.write_text(render_report_html(final_json), encoding="utf-8")
    print(f"[OK] wrote {out}")


if __name__ == "__main__":
    main()
