"""
测试 compose 节点：使用 narrative（叙事传记体）风格

使用已有的转写结果，强制使用叙事风格生成成稿。
运行：cd backend && source venv/bin/activate && python tests/test_compose_narrative.py
"""
import asyncio
import json
import sys
import os
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

ARTIFACTS_DIR = Path(__file__).resolve().parent / "artifacts"
TRANSCRIPTION_FILE = ARTIFACTS_DIR / "e2e_transcription_lovart_20260228_144153.json"


def _log(msg: str):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[narrative {ts}] {msg}", flush=True)


def _save(name: str, data):
    path = ARTIFACTS_DIR / name
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    _log(f"已保存: {path.name}")


async def main():
    from core.workflow.state import create_initial_state
    from core.workflow.nodes.transcription import build_metadata_context
    from core.workflow.nodes.compose import (
        ComposeGenerator,
        NARRATIVE_SYSTEM_PROMPT,
        NARRATIVE_PROMPT_TEMPLATE,
    )

    _log("=" * 60)
    _log("测试 Compose 节点：叙事传记体风格")
    _log("=" * 60)

    # 加载已有的转写结果
    _log(f"加载转写结果: {TRANSCRIPTION_FILE.name}")
    with open(TRANSCRIPTION_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)

    transcription = data["transcription"]
    segments = transcription["segments"]
    initial_state = data.get("initial_state", {})

    _log(f"片段数: {len(segments)}")
    _log(f"说话人: {set(seg['speaker'] for seg in segments)}")

    # 构建 state
    state = create_initial_state(
        task_id=initial_state.get("task_id", "narrative-test"),
        audio_path=initial_state.get("audio_path", ""),
        title=initial_state.get("title", "罗永浩 × 陈冕"),
        author=initial_state.get("author", "罗永浩的十字路口"),
        description=initial_state.get("description", ""),
        host_name=initial_state.get("host_name", "罗永浩"),
        guest_names=initial_state.get("guest_names", ["陈冕"]),
        company_names=initial_state.get("company_names", ["Lovart", "字节"]),
        podcast_intro=initial_state.get("podcast_intro", ""),
        proper_nouns=initial_state.get("proper_nouns", ["AI", "AI Agent"]),
    )

    metadata_context = build_metadata_context(state)

    # 直接使用 narrative 风格的 ComposeGenerator
    _log("使用 narrative（叙事传记体）风格生成...")
    generator = ComposeGenerator(
        system_prompt=NARRATIVE_SYSTEM_PROMPT,
        prompt_template=NARRATIVE_PROMPT_TEMPLATE,
    )

    t0 = time.perf_counter()
    raw = await generator.generate(segments, metadata_context)
    elapsed = time.perf_counter() - t0

    # 构建输出
    sections = raw.get("sections", [])
    chapters = []
    for sec in sections:
        source_ids = sec.get("source_segment_ids", [])
        source_segs = [
            segments[sid] for sid in source_ids
            if 0 <= sid < len(segments)
        ]
        start_t = source_segs[0].get("start_time", 0) if source_segs else 0
        end_t = source_segs[-1].get("end_time", 0) if source_segs else 0

        chapters.append({
            "title": sec.get("title", ""),
            "content": sec.get("content", ""),
            "key_points": sec.get("key_points", []),
            "section_type": sec.get("section_type", ""),
            "source_segment_ids": source_ids,
            "time_range": [start_t, end_t],
        })

    composed_content = {
        "content_type": raw.get("content_type", "叙事传记"),
        "core_theme": raw.get("core_theme", ""),
        "theme_keywords": raw.get("theme_keywords", []),
        "speakers": raw.get("speakers", []),
        "structure_rationale": raw.get("structure_rationale", ""),
        "chapters": chapters,
        "writing_style": "narrative",
        "plan_variant": "compose",
        "elapsed_seconds": elapsed,
    }

    total_words = sum(len(ch.get("content", "")) for ch in chapters)

    _log(f"生成完成 ({elapsed:.1f}s)")
    _log(f"  写作风格: narrative")
    _log(f"  核心主题: {composed_content.get('core_theme', '?')}")
    _log(f"  章节数: {len(chapters)}")
    _log(f"  总字数: {total_words}")
    for i, ch in enumerate(chapters, 1):
        _log(f"  {i}. {ch.get('title', '?')} ({len(ch.get('content', ''))}字)")

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")

    # 保存 JSON
    _save(f"compose_narrative_lovart_{ts}.json", {
        "step": "compose_narrative",
        "elapsed_seconds": elapsed,
        "composed_content": composed_content,
    })

    # 生成可读的 Markdown 审查报告
    md_lines = [
        "# 成稿审查报告 — 罗永浩 × 陈冕（叙事传记体）",
        "",
        f"- **写作风格**: narrative（叙事传记体）",
        f"- **核心主题**: {composed_content.get('core_theme', '?')}",
        f"- **关键词**: {'、'.join(composed_content.get('theme_keywords', []))}",
        f"- **章节数**: {len(chapters)}",
        f"- **总字数**: {total_words}",
        f"- **耗时**: {elapsed:.1f}s",
        "",
        "---",
        "",
    ]
    for i, ch in enumerate(chapters, 1):
        content = ch.get("content", "")
        md_lines += [
            f"## {i}. {ch.get('title', f'第{i}章')}",
            f"*字数: {len(content)}*",
            "",
            content,
            "",
            "---",
            "",
        ]

    md_path = ARTIFACTS_DIR / f"compose_narrative_review_lovart_{ts}.md"
    md_path.write_text("\n".join(md_lines), encoding="utf-8")
    _log(f"审查报告: {md_path.name}")

    _log("=" * 60)
    _log(f"完成！总耗时: {elapsed:.1f}s")
    _log("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
