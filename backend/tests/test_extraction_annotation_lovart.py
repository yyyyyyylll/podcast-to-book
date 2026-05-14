"""
测试精华提炼 + 注释节点
输入：e2e_compose_lovart_20260228_142553.json
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


def _log(msg: str):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[test {ts}] {msg}", flush=True)


def _save(name: str, data):
    path = ARTIFACTS_DIR / name
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    _log(f"已保存: {path.name}")
    return path


async def main():
    from core.workflow.nodes.extraction import extraction_node
    from core.workflow.nodes.annotation import annotation_node

    compose_file = ARTIFACTS_DIR / "e2e_compose_lovart_20260228_142553.json"
    transcription_file = ARTIFACTS_DIR / "e2e_transcription_lovart_20260228_142203.json"

    _log(f"加载成稿: {compose_file.name}")
    compose_data = json.loads(compose_file.read_text(encoding="utf-8"))
    composed_content = compose_data.get("composed_content", compose_data)

    _log(f"加载转写: {transcription_file.name}")
    transcription_data = json.loads(transcription_file.read_text(encoding="utf-8"))
    transcription = transcription_data.get("transcription", transcription_data)

    _log(f"章节数: {len(composed_content.get('chapters', []))}")
    _log(f"转写片段数: {len(transcription.get('segments', []))}")

    state = {
        "task_id": "test_lovart_extraction_annotation",
        "transcription": transcription,
        "composed_content": composed_content,
    }

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    # ========== 阶段一：精华提炼 ==========
    _log("=" * 50)
    _log("阶段一：精华提炼 (extraction)")
    _log("=" * 50)

    t0 = time.perf_counter()
    extraction_result = await extraction_node(state)
    elapsed = time.perf_counter() - t0
    highlights = extraction_result.get("highlights", {})

    _log(f"精华提炼完成 ({elapsed:.1f}s)")

    _save(f"extraction_lovart_{timestamp}.json", highlights)

    review_path = ARTIFACTS_DIR / f"extraction_review_lovart_{timestamp}.md"
    with open(review_path, "w", encoding="utf-8") as f:
        quotes = highlights.get("quotes", [])
        methodologies = highlights.get("methodologies", [])
        f.write("# 精华提炼结果（Lovart）\n\n")
        f.write(f"## 金句 ({len(quotes)} 条)\n\n")
        for i, q in enumerate(quotes, 1):
            f.write(f"### 金句 {i}\n")
            f.write(f"- **原文**: {q.get('text', '')}\n")
            f.write(f"- **出处**: 「{q.get('chapter_title', '')}」\n")
            f.write(f"- **位置**: {q.get('placement', '')}\n\n")

        f.write(f"## 方法论 ({len(methodologies)} 个)\n\n")
        for i, m in enumerate(methodologies, 1):
            f.write(f"### 方法论 {i}: {m.get('name', '')}\n")
            f.write(f"- **出处**: 「{m.get('chapter_title', '')}」\n")
            steps = m.get("steps", [])
            if steps:
                f.write("- **步骤**:\n")
                for j, step in enumerate(steps, 1):
                    f.write(f"  {j}. {step}\n")
            f.write("\n")

    _log(f"可读版已保存: {review_path.name}")
    _log(f"金句: {len(quotes)} 条, 方法论: {len(methodologies)} 个")

    # ========== 阶段二：内容注释 ==========
    _log("=" * 50)
    _log("阶段二：内容注释 (annotation)")
    _log("=" * 50)

    state["highlights"] = highlights

    t0 = time.perf_counter()
    annotation_result = await annotation_node(state)
    elapsed = time.perf_counter() - t0
    annotated_content = annotation_result.get("annotated_content", {})

    _log(f"内容注释完成 ({elapsed:.1f}s)")

    _save(f"annotation_lovart_{timestamp}.json", annotated_content)

    review_path = ARTIFACTS_DIR / f"annotation_review_lovart_{timestamp}.md"
    with open(review_path, "w", encoding="utf-8") as f:
        f.write(f"# {annotated_content.get('core_theme', '播客书稿')}\n\n")
        f.write(f"**内容类型**: {annotated_content.get('content_type', '')}\n")
        f.write(f"**总脚注数**: {annotated_content.get('total_footnotes', 0)}\n\n")
        f.write("---\n\n")

        for i, ch in enumerate(annotated_content.get("chapters", []), 1):
            f.write(f"## 第 {i} 章：{ch.get('title', '')}\n\n")
            f.write(ch.get("content", ""))
            f.write("\n\n---\n\n")

    _log(f"可读版已保存: {review_path.name}")
    _log(f"总脚注数: {annotated_content.get('total_footnotes', 0)}")

    _log("=" * 50)
    _log("全部完成！")
    _log("=" * 50)


if __name__ == "__main__":
    asyncio.run(main())
