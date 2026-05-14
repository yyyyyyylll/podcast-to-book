"""
测试 compose 节点：使用 Insta360 刘靖康×罗永浩 LLM 清洗后的转写结果
支持通过命令行参数指定写作风格: methodology / narrative (默认自动判断)

运行：
  cd backend && python3 tests/test_compose_insta360.py              # 自动判断风格
  cd backend && python3 tests/test_compose_insta360.py methodology  # 强制方法论
  cd backend && python3 tests/test_compose_insta360.py narrative    # 强制叙事
"""
import asyncio
import json
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

TRANSCRIPTION_FILE = Path(__file__).parent / "artifacts" / "transcription_insta360_llm_20260228_183541.json"
ARTIFACTS_DIR = Path(__file__).parent / "artifacts"

FORCE_STYLE = sys.argv[1] if len(sys.argv) > 1 else None


def _log(msg: str):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[compose-insta360 {ts}] {msg}", flush=True)


async def main():
    from core.workflow.state import create_initial_state
    from core.workflow.nodes.compose import Composer, compose_node
    from core.workflow.nodes.transcription import build_metadata_context

    _log("=" * 60)
    _log("Compose 节点测试: 影石Insta360 刘靖康×罗永浩")
    if FORCE_STYLE:
        _log(f"强制写作风格: {FORCE_STYLE}")
    _log("=" * 60)

    # 加载转写结果
    _log(f"加载转写结果: {TRANSCRIPTION_FILE.name}")
    data = json.loads(TRANSCRIPTION_FILE.read_text(encoding="utf-8"))
    transcription = data["transcription"]
    segments = transcription.get("segments", [])
    _log(f"  片段数: {len(segments)}")

    speaker_stats = {}
    for seg in segments:
        speaker = seg.get("speaker", "未知")
        speaker_stats[speaker] = speaker_stats.get(speaker, 0) + len(seg.get("text", ""))
    for speaker, chars in sorted(speaker_stats.items(), key=lambda x: -x[1]):
        _log(f"  {speaker}: {chars} 字")

    # 构建 state
    state = create_initial_state(
        task_id="insta360-compose",
        audio_path="(from-cache)",
        title="影石Insta360 创始人刘靖康×罗永浩！比生存更重要的是那些微小的念头",
        author="罗永浩",
        description="《罗永浩的十字路口》第十五期，我们和 Insta360 的创始人刘靖康聊聊他的创业和他的创造。",
        host_name="罗永浩",
        guest_names=["刘靖康"],
        company_names=["影石", "Insta360"],
        proper_nouns=["VR", "AR", "全景相机", "专利流氓", "GoPro"],
    )
    state["transcription"] = transcription

    # 运行 compose
    _log("开始 compose...")
    t0 = time.perf_counter()

    if FORCE_STYLE:
        metadata_context = build_metadata_context(state)
        composer = Composer(metadata_context=metadata_context)
        generator = composer.generators[FORCE_STYLE]
        raw = await generator.generate(segments, metadata_context)
        composed = composer._build_output(raw, segments, FORCE_STYLE, time.perf_counter() - t0)
    else:
        output = await compose_node(state)
        state.update(output)
        composed = state["composed_content"]

    elapsed = time.perf_counter() - t0

    chapters = composed.get("chapters", [])
    style = composed.get("writing_style", "?")
    total_words = sum(len(ch.get("content", "")) for ch in chapters)

    _log(f"compose 完成 ({elapsed:.1f}s)")
    _log(f"  写作风格: {style}")
    _log(f"  章节数: {len(chapters)}")
    _log(f"  总字数: {total_words}")
    for i, ch in enumerate(chapters, 1):
        _log(f"  {i}. {ch.get('title', '?')} ({len(ch.get('content', ''))}字)")

    # 保存 JSON
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_path = ARTIFACTS_DIR / f"compose_insta360_{ts}.json"
    json_path.write_text(
        json.dumps(composed, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    _log(f"已保存: {json_path.name}")

    # 生成 Markdown 审查报告
    md_lines = [
        "# 成稿审查报告 — 影石Insta360 刘靖康×罗永浩",
        "",
        f"- **写作风格**: {style}",
        f"- **核心主题**: {composed.get('core_theme', '?')}",
        f"- **关键词**: {'、'.join(composed.get('theme_keywords', []))}",
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

    md_path = ARTIFACTS_DIR / f"compose_review_insta360_{ts}.md"
    md_path.write_text("\n".join(md_lines), encoding="utf-8")
    _log(f"审查报告: {md_path.name}")

    _log("=" * 60)
    _log(f"完成！总耗时: {elapsed:.1f}s ({elapsed/60:.1f}min)")
    _log("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
