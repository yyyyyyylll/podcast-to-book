"""Static HTML judgment page renderer for podcast direction diagnosis."""
from __future__ import annotations

import argparse
import html
import json
from pathlib import Path
from typing import Any

from multi.podcast_direction.value_labels import public_editing_value


def _esc(value: Any) -> str:
    return html.escape(str(value or ""), quote=True)


def _decision_label(final_json: dict[str, Any]) -> tuple[str, str]:
    overall = final_json.get("overall_diagnosis") or {}
    qc = final_json.get("quality_check") or {}
    metadata = final_json.get("metadata") or {}
    structure_type = overall.get("content_structure_type") or ""
    confidence = qc.get("confidence_level") or ""
    sampled = bool(metadata.get("_input_sampled"))
    if "方向较散" in structure_type or confidence == "低" or sampled:
        return "谨慎推进", "需要先补材料或人工复核后再进入整理"
    if qc.get("manual_review_needed"):
        return "可以继续整理", "方向成立，但建议带着质检风险人工复核"
    return "可以继续整理", "方向清晰，适合进入后续内容整理"


def _direction_score(item: dict[str, Any], priority: str, not_priority: str) -> str:
    value = public_editing_value(item.get("editing_value"))
    evidence_count = len(item.get("episode_evidence") or [])
    topic_count = len(item.get("sub_topics") or [])
    flags: list[str] = [f"价值 {value}", f"证据 {evidence_count}", f"子题 {topic_count}"]
    name = item.get("direction_name") or ""
    if name == priority:
        flags.insert(0, "优先")
    if name == not_priority:
        flags.insert(0, "不优先")
    return "".join(f"<span>{_esc(flag)}</span>" for flag in flags)


def _direction_rows(directions: list[dict[str, Any]], priority: str, not_priority: str) -> str:
    if not directions:
        return "<p class=\"muted\">暂无方向判断。</p>"
    rows: list[str] = []
    for item in directions:
        topics = "、".join((item.get("sub_topics") or [])[:4])
        evidence = " / ".join((item.get("episode_evidence") or [])[:3])
        rows.append(
            "<article class=\"judge-row\">"
            "<div>"
            f"<h3>{_esc(item.get('direction_name'))}</h3>"
            f"<p>{_esc(topics)}</p>"
            f"<small>{_esc(evidence)}</small>"
            "</div>"
            f"<div class=\"chips\">{_direction_score(item, priority, not_priority)}</div>"
            "</article>"
        )
    return "".join(rows)


def _topic_rows(topics: list[dict[str, Any]]) -> str:
    if not topics:
        return "<p class=\"muted\">未识别到主播显式专题。</p>"
    return "".join(
        "<div class=\"topic-line\">"
        f"<strong>{_esc(topic.get('topic_title'))}</strong>"
        f"<span>{int(topic.get('episode_count') or 0)} 集</span>"
        "</div>"
        for topic in topics
    )


def _warning_rows(warnings: list[str]) -> str:
    if not warnings:
        return "<p class=\"muted\">暂无质检警告。</p>"
    return "".join(f"<li>{_esc(item)}</li>" for item in warnings)


def render_judgment_html(final_json: dict[str, Any]) -> str:
    overall = final_json.get("overall_diagnosis") or {}
    structure = final_json.get("direction_structure") or {}
    customer = final_json.get("customer_diagnosis") or {}
    qc = final_json.get("quality_check") or {}
    metadata = final_json.get("metadata") or {}
    topics = final_json.get("host_topic_references") or []

    podcast_name = overall.get("podcast_name") or "播客诊断"
    directions = structure.get("directions") or []
    priority = structure.get("priority_direction") or ""
    not_priority = structure.get("not_priority_direction") or ""
    decision, decision_note = _decision_label(final_json)
    episode_count = metadata.get("_input_episode_count") or 0
    sampled = metadata.get("_input_sampled")
    coverage = f"完整 {episode_count} 集" if episode_count else "整档"
    if sampled:
        coverage = f"采样 {episode_count} 集"
    warnings = qc.get("warnings") or []

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{_esc(podcast_name)} · 判断页</title>
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
      --wash: #eee6d7;
    }}
    * {{ box-sizing: border-box; }}
    html {{ scroll-behavior: smooth; }}
    body {{
      margin: 0;
      color: var(--ink);
      background: var(--paper);
      font-family: "Aptos", "PingFang SC", "Microsoft YaHei", sans-serif;
      line-height: 1.55;
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
    aside {{
      position: sticky;
      top: 0;
      height: 100vh;
      padding: 28px 22px;
      border-right: 1px solid var(--line);
      background: var(--wash);
    }}
    .kicker {{ color: var(--rust); font-size: 13px; font-weight: 800; letter-spacing: 0; }}
    aside h1 {{ margin: 8px 0 10px; font-size: 28px; line-height: 1.12; letter-spacing: 0; }}
    .meta {{ color: var(--muted); font-size: 13px; }}
    nav {{ margin-top: 30px; display: grid; gap: 8px; }}
    nav a {{ color: var(--ink); text-decoration: none; padding: 8px 0; border-bottom: 1px solid rgba(30,36,36,.12); }}
    main {{ padding: 34px min(6vw, 72px) 60px; }}
    .verdict {{
      display: grid;
      grid-template-columns: minmax(0, 1.1fr) minmax(280px, .9fr);
      gap: 18px;
      align-items: stretch;
      border-bottom: 2px solid var(--ink);
      padding-bottom: 22px;
    }}
    .stamp {{
      background: var(--ink);
      color: var(--paper);
      min-height: 280px;
      padding: 24px;
      display: flex;
      flex-direction: column;
      justify-content: space-between;
    }}
    .stamp h2 {{ margin: 8px 0; font-size: clamp(42px, 7vw, 86px); line-height: .9; letter-spacing: 0; }}
    .stamp p {{ margin: 0; color: #e8ddca; font-size: 17px; }}
    .decision-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }}
    .tile {{
      border: 1px solid var(--line);
      background: var(--panel);
      padding: 15px;
      min-height: 96px;
    }}
    .tile strong {{ display: block; color: var(--teal); font-size: 22px; line-height: 1.18; }}
    .tile span {{ color: var(--muted); font-size: 13px; }}
    h2 {{ margin: 32px 0 14px; font-size: 22px; letter-spacing: 0; }}
    h3 {{ margin: 0 0 6px; font-size: 18px; letter-spacing: 0; }}
    .section {{ padding-top: 8px; }}
    .judge-row {{
      display: grid;
      grid-template-columns: minmax(0, 1fr) minmax(210px, .32fr);
      gap: 16px;
      align-items: start;
      border: 1px solid var(--line);
      background: var(--panel);
      padding: 16px;
      margin-bottom: 10px;
    }}
    .judge-row p {{ margin: 0 0 8px; }}
    .judge-row small {{ color: var(--muted); display: block; }}
    .chips {{ display: flex; flex-wrap: wrap; gap: 7px; justify-content: flex-end; }}
    .chips span {{
      border: 1px solid rgba(18,111,104,.24);
      background: #eef6f2;
      color: var(--teal);
      padding: 4px 8px;
      font-size: 13px;
      white-space: nowrap;
    }}
    .panel-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 14px; }}
    .panel {{ border: 1px solid var(--line); background: var(--panel); padding: 16px; }}
    .topic-line {{ display: flex; justify-content: space-between; gap: 12px; border-bottom: 1px solid var(--line); padding: 10px 0; }}
    .topic-line:first-child {{ padding-top: 0; }}
    .topic-line:last-child {{ border-bottom: 0; padding-bottom: 0; }}
    .risk {{ border-left: 5px solid var(--rust); }}
    .risk ul {{ margin: 8px 0 0; padding-left: 20px; }}
    .muted {{ color: var(--muted); }}
    @media (max-width: 860px) {{
      .layout {{ display: block; }}
      aside {{ position: static; height: auto; border-right: 0; border-bottom: 1px solid var(--line); }}
      nav {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
      main {{ padding: 22px 16px 44px; }}
      .verdict {{ grid-template-columns: 1fr; }}
      .decision-grid {{ grid-template-columns: 1fr 1fr; }}
      .judge-row {{ grid-template-columns: 1fr; }}
      .chips {{ justify-content: flex-start; }}
    }}
    @media (max-width: 520px) {{
      .decision-grid {{ grid-template-columns: 1fr; }}
    }}
  </style>
</head>
<body>
  <div class="layout">
    <aside>
      <div class="kicker">EchoPress Judgment</div>
      <h1>{_esc(podcast_name)}</h1>
      <div class="meta">{_esc(coverage)} · 判断页</div>
      <nav>
        <a href="#verdict">判断结论</a>
        <a href="#basis">判断依据</a>
        <a href="#directions">方向取舍</a>
        <a href="#topics">主播专题</a>
        <a href="#risk">复核风险</a>
      </nav>
    </aside>
    <main>
      <section class="verdict" id="verdict">
        <div class="stamp">
          <div>
            <div class="kicker">判断结论</div>
            <h2>{_esc(decision)}</h2>
          </div>
          <p>{_esc(decision_note)}</p>
        </div>
        <div class="decision-grid">
          <div class="tile"><strong>{_esc(coverage)}</strong><span>输入覆盖</span></div>
          <div class="tile"><strong>{_esc(overall.get('content_structure_type'))}</strong><span>结构类型</span></div>
          <div class="tile"><strong>{_esc(priority)}</strong><span>优先方向</span></div>
          <div class="tile"><strong>{_esc(qc.get('confidence_level') or '—')}</strong><span>质检信心</span></div>
          <div class="tile"><strong>{len(topics)}</strong><span>主播专题线索</span></div>
          <div class="tile"><strong>{len(warnings)}</strong><span>复核提醒</span></div>
        </div>
      </section>

      <section class="section" id="basis">
        <h2>判断依据</h2>
        <div class="panel-grid">
          <article class="panel">
            <h3>整体判断</h3>
            <p>{_esc(overall.get('overall_content_judgment'))}</p>
          </article>
          <article class="panel">
            <h3>优先原因</h3>
            <p>{_esc(structure.get('priority_reason'))}</p>
          </article>
          <article class="panel">
            <h3>暂不优先</h3>
            <p><strong>{_esc(not_priority)}</strong></p>
            <p>{_esc(structure.get('not_priority_reason'))}</p>
          </article>
        </div>
      </section>

      <section class="section" id="directions">
        <h2>方向取舍</h2>
        {_direction_rows(directions, priority, not_priority)}
      </section>

      <section class="section" id="topics">
        <h2>主播专题是否支持判断</h2>
        <div class="panel">{_topic_rows(topics)}</div>
      </section>

      <section class="section" id="risk">
        <h2>复核风险</h2>
        <div class="panel risk">
          <h3>{_esc(customer.get('main_risk') or '主要风险')}</h3>
          <ul>{_warning_rows(warnings)}</ul>
        </div>
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
    out.write_text(render_judgment_html(final_json), encoding="utf-8")
    print(f"[OK] wrote {out}")


if __name__ == "__main__":
    main()
