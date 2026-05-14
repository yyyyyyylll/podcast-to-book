"""
逐节点测试：从播客链接到 PDF，每步独立运行。

用法：
  cd backend
  python tests/test_step_by_step.py --step 0   # 解析播客元数据
  python tests/test_step_by_step.py --step 1   # 转写（耗时长，可复用缓存）
  python tests/test_step_by_step.py --step 2   # 成稿（compose_interview）
  python tests/test_step_by_step.py --step 3   # 精华提炼（extraction，仅金句）
  python tests/test_step_by_step.py --step 4   # 内容注释（annotation）
  python tests/test_step_by_step.py --step 5   # 编者序（editor_preface，联网搜索增强）
  python tests/test_step_by_step.py --step 6   # 排版 PDF（typeset）
  python tests/test_step_by_step.py --step 7   # 正文插图（illustration，搜图+AI生图+VLM审核）
"""
import asyncio
import json
import sys
import os
import time
import argparse
from datetime import datetime
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

PODCAST_URL = "https://www.xiaoyuzhoufm.com/episode/69608f978f388c61e1fa0ad0"
STATE_DIR = Path(__file__).resolve().parent / "artifacts" / "step_by_step"
STATE_DIR.mkdir(parents=True, exist_ok=True)

STATE_FILE = STATE_DIR / "state.json"
TS = datetime.now().strftime("%Y%m%d_%H%M%S")


def _log(msg: str):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[step {ts}] {msg}", flush=True)


def _save_state(state: dict):
    STATE_FILE.write_text(
        json.dumps(state, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    _log(f"状态已保存: {STATE_FILE}")


def _load_state() -> dict:
    if not STATE_FILE.exists():
        raise FileNotFoundError(f"找不到状态文件: {STATE_FILE}\n请先运行 --step 0")
    return json.loads(STATE_FILE.read_text(encoding="utf-8"))


def _save_artifact(name: str, data):
    path = STATE_DIR / name
    if isinstance(data, str):
        path.write_text(data, encoding="utf-8")
    else:
        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
    _log(f"已保存: {path.name}")


# ============================================================
# Step 0: 解析播客元数据
# ============================================================
async def step0_parse():
    from core.services.podcast_service import PodcastService
    from core.workflow.state import create_initial_state

    _log("=" * 60)
    _log("Step 0: 解析播客 URL + 提取元数据")
    _log(f"链接: {PODCAST_URL}")
    _log("=" * 60)

    svc = PodcastService()
    t0 = time.perf_counter()
    episode = await svc.extract(PODCAST_URL)
    elapsed = time.perf_counter() - t0

    _log(f"解析完成 ({elapsed:.1f}s)")
    _log(f"  播客名:    {episode.podcast_name}")
    _log(f"  标题:      {episode.title}")
    _log(f"  时长:      {episode.duration:.0f}s ({episode.duration/60:.1f}min)")
    _log(f"  主持人:    {episode.host_name}")
    _log(f"  嘉宾:      {'、'.join(episode.guest_names)}")
    _log(f"  公司:      {'、'.join(episode.company_names)}")
    _log(f"  专有名词:  {'、'.join(episode.proper_nouns)}")
    _log(f"  封面URL:   {episode.cover_url[:80]}...")
    _log(f"  发布日期:  {episode.publish_date}")
    _log(f"  音频URL:   {episode.audio_url[:80]}...")

    state = dict(create_initial_state(
        task_id=episode.episode_id,
        audio_path=episode.audio_url,
        title=episode.title,
        author=episode.host_name or episode.podcast_name,
        description=episode.description,
        host_name=episode.host_name,
        guest_names=episode.guest_names,
        company_names=episode.company_names,
        podcast_intro=episode.shownotes_text,
        proper_nouns=episode.proper_nouns,
        cover_url=episode.cover_url,
        podcast_name=episode.podcast_name,
        podcast_url=PODCAST_URL,
        publish_date=episode.publish_date,
    ))

    _save_state(state)
    _save_artifact(f"step0_parse_{TS}.json", {
        "step": "parse",
        "elapsed_seconds": elapsed,
        "podcast_name": episode.podcast_name,
        "title": episode.title,
        "host_name": episode.host_name,
        "guest_names": episode.guest_names,
        "company_names": episode.company_names,
        "proper_nouns": episode.proper_nouns,
        "publish_date": episode.publish_date,
        "podcast_url": PODCAST_URL,
        "cover_url": episode.cover_url,
    })

    _log("Step 0 完成。检查以上元数据，确认后运行 --step 1")


# ============================================================
# Step 1: 转写
# ============================================================
async def step1_transcribe():
    from core.workflow.nodes.transcription import transcription_node

    state = _load_state()
    _log("=" * 60)
    _log("Step 1: ASR 转写 + LLM 口语清理")
    _log(f"音频时长: ~{13435/60:.0f} 分钟，预计耗时较长...")
    _log("=" * 60)

    t0 = time.perf_counter()
    output = await transcription_node(state)
    elapsed = time.perf_counter() - t0
    state.update(output)

    transcription = state["transcription"]
    segments = transcription.get("segments", [])

    _log(f"转写完成 ({elapsed:.1f}s)")
    _log(f"  片段数: {len(segments)}")
    _log(f"  说话人数: {transcription.get('speaker_count', 0)}")
    _log(f"  总时长: {transcription.get('duration', 0):.0f}s")

    _log(f"\n前 5 个片段预览:")
    for seg in segments[:5]:
        _log(f"  [{seg['start_time']:.0f}s] 【{seg['speaker']}】{seg['text'][:60]}...")

    _save_state(state)
    _save_artifact(f"step1_transcription_{TS}.json", {
        "step": "transcription",
        "elapsed_seconds": elapsed,
        "segment_count": len(segments),
    })

    _log("Step 1 完成。检查后运行 --step 2")


# ============================================================
# Step 2: 成稿（compose_interview）
# ============================================================
async def step2_compose():
    from core.workflow.nodes.compose_interview import compose_interview_node

    state = _load_state()
    segments = state.get("transcription", {}).get("segments", [])
    _log("=" * 60)
    _log("Step 2: 访谈体成稿（compose_interview）")
    _log(f"输入: {len(segments)} 个片段")
    _log("=" * 60)

    t0 = time.perf_counter()
    output = await compose_interview_node(state)
    elapsed = time.perf_counter() - t0
    state.update(output)

    composed = state["composed_content"]
    chapters = composed.get("chapters", [])
    preamble = composed.get("preamble", {})
    total_words = sum(len(ch.get("content", "")) for ch in chapters)

    _log(f"成稿完成 ({elapsed:.1f}s)")
    _log(f"  标题:     {composed.get('title', '?')}")
    _log(f"  核心主题: {composed.get('core_theme', '?')}")
    _log(f"  关键词:   {'、'.join(composed.get('theme_keywords', []))}")
    _log(f"  章节数:   {len(chapters)}")
    _log(f"  总字数:   {total_words}")

    _log(f"\n编者序（lead_paragraph）:")
    lead = preamble.get("lead_paragraph", "")
    _log(f"  {lead[:200]}{'...' if len(lead) > 200 else ''}")

    _log(f"\n内容提要（summary_bullets）:")
    for b in preamble.get("summary_bullets", []):
        _log(f"  • {b[:80]}{'...' if len(b) > 80 else ''}")

    _log(f"\n章节列表:")
    for i, ch in enumerate(chapters, 1):
        _log(f"  {i}. {ch.get('title', '?')} ({len(ch.get('content', ''))}字)")

    _save_state(state)

    # 生成可读 Markdown 审查报告
    md_lines = [
        f"# 成稿审查报告 — {composed.get('title', '')}",
        "",
        f"- **核心主题**: {composed.get('core_theme', '')}",
        f"- **关键词**: {'、'.join(composed.get('theme_keywords', []))}",
        f"- **章节数**: {len(chapters)}",
        f"- **总字数**: {total_words}",
        f"- **耗时**: {elapsed:.1f}s",
        "",
        "---",
        "",
        "## 编者序",
        "",
        preamble.get("lead_paragraph", "（无）"),
        "",
        "## 内容提要",
        "",
    ]
    for b in preamble.get("summary_bullets", []):
        md_lines.append(f"- {b}")
    md_lines += ["", "---", ""]

    for i, ch in enumerate(chapters, 1):
        md_lines += [
            f"## {i}. {ch.get('title', f'第{i}章')}",
            f"*字数: {len(ch.get('content', ''))}*",
            "",
            ch.get("content", ""),
            "",
            "---",
            "",
        ]

    _save_artifact(f"step2_compose_review_{TS}.md", "\n".join(md_lines))
    _save_artifact(f"step2_compose_{TS}.json", {
        "step": "compose",
        "elapsed_seconds": elapsed,
        "composed_content": composed,
    })

    _log("Step 2 完成。审查报告已生成。检查后运行 --step 3")


# ============================================================
# Step 3: 精华提炼（extraction）
# ============================================================
async def step3_extraction():
    from core.workflow.nodes.extraction import extraction_node

    state = _load_state()
    _log("=" * 60)
    _log("Step 3: 精华提炼（extraction）")
    _log("=" * 60)

    t0 = time.perf_counter()
    output = await extraction_node(state)
    elapsed = time.perf_counter() - t0
    state.update(output)

    highlights = state["highlights"]
    quotes = highlights.get("quotes", [])

    _log(f"提炼完成 ({elapsed:.1f}s)")
    _log(f"  金句: {len(quotes)} 条")

    _log(f"\n金句列表:")
    for i, q in enumerate(quotes, 1):
        placement = q.get("placement", "?")
        chapter = q.get("chapter_title", "?")
        _log(f"  {i}. [{placement}] 「{q.get('text', '')[:50]}...」 → {chapter}")

    _save_state(state)
    _save_artifact(f"step3_extraction_{TS}.json", {
        "step": "extraction",
        "elapsed_seconds": elapsed,
        "highlights": highlights,
    })

    # Markdown 审查
    md_lines = ["# 精华提炼审查报告", "", f"- 金句: {len(quotes)} 条", "", "---", "", "## 金句", ""]
    for i, q in enumerate(quotes, 1):
        md_lines.append(f"{i}. **[{q.get('placement','')}]** 「{q.get('text','')}」")
        md_lines.append(f"   - 章节: {q.get('chapter_title','')}, after_paragraph: {q.get('after_paragraph','')}")
        md_lines.append("")
    _save_artifact(f"step3_extraction_review_{TS}.md", "\n".join(md_lines))
    _log("Step 3 完成。检查后运行 --step 4")


# ============================================================
# Step 4: 内容注释（annotation）
# ============================================================
async def step4_annotation():
    from core.workflow.nodes.annotation import annotation_node

    state = _load_state()
    _log("=" * 60)
    _log("Step 4: 内容注释（annotation）")
    _log("=" * 60)

    t0 = time.perf_counter()
    output = await annotation_node(state)
    elapsed = time.perf_counter() - t0
    state.update(output)

    annotated = state["annotated_content"]
    total_fn = annotated.get("total_footnotes", 0)
    chapters = annotated.get("chapters", [])

    _log(f"注释完成 ({elapsed:.1f}s)")
    _log(f"  总脚注数: {total_fn}")
    _log(f"  章节数: {len(chapters)}")

    for i, ch in enumerate(chapters, 1):
        content = ch.get("content", "")
        fn_count = content.count("[^")
        _log(f"  {i}. {ch.get('title', '?')} — {fn_count} 个脚注")

    _save_state(state)
    _save_artifact(f"step4_annotation_{TS}.json", {
        "step": "annotation",
        "elapsed_seconds": elapsed,
        "total_footnotes": total_fn,
    })

    # Markdown 审查：展示带脚注的完整正文
    md_lines = ["# 注释审查报告", "", f"总脚注: {total_fn}", "", "---", ""]
    for i, ch in enumerate(chapters, 1):
        md_lines += [
            f"## {i}. {ch.get('title', '')}",
            "",
            ch.get("content", ""),
            "",
            "---",
            "",
        ]
    _save_artifact(f"step4_annotation_review_{TS}.md", "\n".join(md_lines))
    _log("Step 4 完成。检查后运行 --step 5")


# ============================================================
# Step 5: 编者序（editor_preface，联网搜索增强）
# ============================================================
async def step5_editor_preface():
    from core.workflow.nodes.editor_preface import editor_preface_node

    state = _load_state()
    _log("=" * 60)
    _log("Step 5: 编者序生成（editor_preface）")
    _log("  使用 OpenAI Responses API + web_search 搜索背景信息")
    _log("=" * 60)

    t0 = time.perf_counter()
    output = await editor_preface_node(state)
    elapsed = time.perf_counter() - t0
    state.update(output)

    preface = state.get("editor_preface_content", "")

    _log(f"编者序生成完成 ({elapsed:.1f}s)")
    _log(f"  长度: {len(preface)} 字")
    _log(f"\n编者序全文:")
    _log("-" * 40)
    _log(preface)
    _log("-" * 40)

    # 与 compose 阶段的 lead_paragraph 对比
    composed = state.get("composed_content") or state.get("annotated_content") or {}
    old_lead = composed.get("preamble", {}).get("lead_paragraph", "")
    if old_lead:
        _log(f"\n[对比] compose 阶段的 lead_paragraph ({len(old_lead)} 字):")
        _log(f"  {old_lead[:200]}{'...' if len(old_lead) > 200 else ''}")

    _save_state(state)
    _save_artifact(f"step5_editor_preface_{TS}.md", "\n".join([
        "# 编者序审查报告",
        "",
        f"- 耗时: {elapsed:.1f}s",
        f"- 长度: {len(preface)} 字",
        "",
        "---",
        "",
        "## 编者序（搜索增强版）",
        "",
        preface,
        "",
        "---",
        "",
        "## 对比：compose 阶段 lead_paragraph（原始版）",
        "",
        old_lead or "（无）",
    ]))

    _log("Step 5 完成。检查后运行 --step 6")


# ============================================================
# Step 6: 排版 PDF（typeset）
# ============================================================
async def step6_typeset():
    from core.workflow.nodes.typeset import typeset_node

    state = _load_state()
    _log("=" * 60)
    _log("Step 6: 排版 PDF（typeset）")
    _log("=" * 60)

    t0 = time.perf_counter()
    output = await typeset_node(state)
    elapsed = time.perf_counter() - t0
    state.update(output)

    pdf_path = state.get("pdf_path", "")
    typst_source = state.get("typst_source", "")

    _log(f"排版完成 ({elapsed:.1f}s)")
    _log(f"  PDF: {pdf_path}")
    _log(f"  Typst 源码长度: {len(typst_source)} 字符")

    if pdf_path and Path(pdf_path).exists():
        size_kb = Path(pdf_path).stat().st_size / 1024
        _log(f"  PDF 大小: {size_kb:.0f} KB")

    _save_state(state)
    _save_artifact(f"step6_typst_source_{TS}.typ", typst_source)
    _log(f"Step 6 完成。PDF 已生成: {pdf_path}")


# ============================================================
# Step 7: 正文插图（illustration）
# ============================================================
async def step7_illustration():
    from core.workflow.nodes.illustration import illustration_node

    state = _load_state()
    _log("=" * 60)
    _log("Step 7: 正文插图（illustration）")
    _log("  Phase 1: 逐章识别方法论/框架配图")
    _log("  Phase 2: AI 生图（nano-banana-pro）")
    _log("  Phase 3: VLM 审核（qwen-vl-max）")
    _log("=" * 60)

    composed = state.get("composed_content")
    if not composed:
        _log("ERROR: state 中没有 composed_content，请先运行 --step 2")
        return

    chapters = composed.get("chapters", [])
    _log(f"输入: {len(chapters)} 个章节, "
         f"主题: {composed.get('core_theme', '')[:60]}")

    t0 = time.perf_counter()
    output = await illustration_node(state)
    elapsed = time.perf_counter() - t0
    state.update(output)

    illustrations = state.get("illustrations", {})
    images = illustrations.get("images", [])

    _log(f"\n插图生成完成 ({elapsed:.1f}s)")
    _log(f"  通过审核: {illustrations.get('total_count', 0)} 张")
    _log(f"  AI 生图: {illustrations.get('generated_count', 0)} 张")
    _log(f"  被拒绝: {illustrations.get('rejected_count', 0)} 张")

    _log(f"\n插图列表:")
    for i, img in enumerate(images, 1):
        img_type = img.get("type", "?")
        chapter = img.get("chapter_title", "?")
        caption = img.get("caption", "")
        score = img.get("review_score", 0)
        source = img.get("source", "")
        _log(f"  {i}. [{img_type}] {caption[:40]} → {chapter} "
             f"(score={score}/25, source={source})")

    _save_state(state)
    _save_artifact(f"step7_illustration_{TS}.json", {
        "step": "illustration",
        "elapsed_seconds": elapsed,
        "illustrations": illustrations,
    })

    # Markdown 审查报告
    md_lines = [
        "# 正文插图审查报告",
        "",
        f"- 耗时: {elapsed:.1f}s",
        f"- 通过审核: {illustrations.get('total_count', 0)} 张",
        f"- AI 生图: {illustrations.get('generated_count', 0)} 张",
        f"- 被拒绝: {illustrations.get('rejected_count', 0)} 张",
        "",
        "---",
        "",
    ]
    for i, img in enumerate(images, 1):
        md_lines += [
            f"## {i}. {img.get('caption', '无标题')}",
            "",
            f"- **类型**: {img.get('type', '?')}",
            f"- **章节**: {img.get('chapter_title', '?')}",
            f"- **段落位置**: P{img.get('after_paragraph', '?')}",
            f"- **文件**: {img.get('filename', '?')}",
            f"- **来源**: {img.get('source', '?')}",
            f"- **审核得分**: {img.get('review_score', 0)}/25",
            f"- **审核理由**: {img.get('review_reason', '')}",
            "",
            "---",
            "",
        ]
    _save_artifact(f"step7_illustration_review_{TS}.md", "\n".join(md_lines))

    _log("Step 7 完成。审查报告已生成。")


# ============================================================
# Main
# ============================================================
STEPS = {
    0: ("解析播客元数据", step0_parse),
    1: ("ASR 转写", step1_transcribe),
    2: ("访谈体成稿", step2_compose),
    3: ("精华提炼", step3_extraction),
    4: ("内容注释", step4_annotation),
    5: ("编者序", step5_editor_preface),
    6: ("排版 PDF", step6_typeset),
    7: ("正文插图", step7_illustration),
}


async def main():
    parser = argparse.ArgumentParser(description="逐节点测试")
    parser.add_argument("--step", type=int, required=True, choices=STEPS.keys(),
                        help="要执行的步骤编号")
    args = parser.parse_args()

    name, func = STEPS[args.step]
    _log(f"执行 Step {args.step}: {name}")
    await func()


if __name__ == "__main__":
    asyncio.run(main())
