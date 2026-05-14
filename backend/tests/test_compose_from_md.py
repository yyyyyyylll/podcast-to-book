"""
从 markdown 转写报告中加载数据，单独测试 compose 节点。

运行方式：
cd backend
python -m pytest tests/test_compose_from_md.py -v -s
"""
import json
import re
import time
from datetime import datetime
from pathlib import Path

import pytest
from core.workflow.state import create_initial_state


ARTIFACTS_DIR = Path(__file__).resolve().parent / "artifacts"
MD_FILE = ARTIFACTS_DIR / "解析-llm分段-不改人名-1.md"


def _log(msg: str) -> None:
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[compose-md-test {ts}] {msg}", flush=True)


def _extract_final_state_json(md_path: Path) -> dict:
    """从 markdown 报告的「最终状态」代码块中提取 JSON。"""
    text = md_path.read_text(encoding="utf-8")
    match = re.search(
        r"## 最终状态\s*```json\s*(\{.*?\})\s*```",
        text,
        re.DOTALL,
    )
    if not match:
        raise ValueError(f"在 {md_path.name} 中未找到「最终状态」JSON 块")
    return json.loads(match.group(1))


def _dump(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


def _build_report(composed: dict) -> str:
    lines = ["# Compose 测试报告（从 MD 加载）", ""]
    chapters = composed.get("chapters", [])
    lines += [
        "## 概览",
        "",
        f"- **内容类型**: {composed.get('content_type', '?')}",
        f"- **核心主题**: {composed.get('core_theme', '?')}",
        f"- **关键词**: {'、'.join(composed.get('theme_keywords', []))}",
        f"- **板块数**: {len(chapters)}",
        f"- **说话人**: {'、'.join(s.get('name', '') for s in composed.get('speakers', []))}",
        f"- **prompt版本**: v{composed.get('prompt_version', 0)}",
        f"- **耗时**: {composed.get('elapsed_seconds', 0):.1f}s",
        f"- **结构说明**: {composed.get('structure_rationale', '')}",
        "",
    ]

    lines += ["---", "", "## 成稿内容", ""]
    total_words = 0
    for i, ch in enumerate(chapters, 1):
        title = ch.get("title", f"第{i}章")
        content = ch.get("content", "")
        key_points = ch.get("key_points", [])
        wc = len(content)
        total_words += wc
        lines += [
            f"### {i}. {title}",
            f"*字数: {wc} | 片段: {ch.get('source_segment_ids', [])}*",
            "",
            content,
            "",
        ]
        if key_points:
            lines += [f"**要点**: {' / '.join(key_points)}", ""]
        lines.append("")

    lines += [f"**总字数**: {total_words}", ""]
    return "\n".join(lines)


@pytest.fixture
def state_from_md():
    if not MD_FILE.exists():
        pytest.skip(f"测试文件不存在: {MD_FILE.name}")

    data = _extract_final_state_json(MD_FILE)
    transcription = data.get("transcription")
    if not transcription or not transcription.get("segments"):
        pytest.skip("markdown 中未找到有效的 transcription segments")

    state = create_initial_state(
        task_id=data.get("task_id", "compose-md-test"),
        audio_path=data.get("audio_path", ""),
        title=data.get("title", "测试"),
        author=data.get("author", ""),
        host_name=data.get("host_name", ""),
        guest_names=data.get("guest_names", []),
        company_names=data.get("company_names", []),
        podcast_intro=data.get("podcast_intro", ""),
        proper_nouns=data.get("proper_nouns", []),
    )
    state["transcription"] = transcription
    return state, transcription


@pytest.mark.asyncio
async def test_compose_from_md(state_from_md):
    """从 MD 文件加载转写数据，测试新版 compose prompt。"""
    state, transcription = state_from_md
    segments = transcription["segments"]
    seg_count = len(segments)

    source_char_count = sum(len(s.get("text", "")) for s in segments)
    _log(f"输入: {seg_count} 个片段, 原始文本共 {source_char_count} 字")

    from core.workflow.nodes.compose import compose_node

    t0 = time.perf_counter()
    output = await compose_node(state)
    elapsed = time.perf_counter() - t0

    composed = output.get("composed_content", {})
    chapters = composed.get("chapters", [])
    total_words = sum(len(ch.get("content", "")) for ch in chapters)
    ratio = total_words / source_char_count if source_char_count else 0

    _log(f"完成: {len(chapters)} 个板块, 总字数 {total_words}, "
         f"覆盖率 {ratio:.1%} (目标≥70%), 耗时 {elapsed:.1f}s")

    assert composed, "composed_content 不应为空"
    assert chapters, "章节列表不应为空"
    assert len(chapters) >= 4, f"板块数过少: {len(chapters)}，信息可能被大量丢弃"

    for ch in chapters:
        assert ch.get("title"), "章节标题不应为空"
        assert len(ch.get("content", "")) >= 200, f"章节「{ch.get('title')}」内容过短"

    if ratio < 0.70:
        _log(f"⚠️ 覆盖率 {ratio:.1%} 低于 70% 目标，但不阻断测试")

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)

    json_path = ARTIFACTS_DIR / f"compose_md_machine_{ts}.json"
    json_path.write_text(
        json.dumps({
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "source_file": MD_FILE.name,
            "source_char_count": source_char_count,
            "output_char_count": total_words,
            "coverage_ratio": round(ratio, 4),
            "composed_content": composed,
        }, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )

    report = _build_report(composed)
    report += f"\n---\n\n## 覆盖率统计\n\n"
    report += f"- 原始转写字数: {source_char_count}\n"
    report += f"- 成稿总字数: {total_words}\n"
    report += f"- 覆盖率: {ratio:.1%}\n"

    _dump(ARTIFACTS_DIR / f"compose_md_review_{ts}.md", report)
    _log(f"artifacts 已保存到 {ARTIFACTS_DIR.name}/")
