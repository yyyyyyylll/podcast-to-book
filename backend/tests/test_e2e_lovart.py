"""
端到端测试：罗永浩 × 陈冕（Lovart）
播客链接: https://www.xiaoyuzhoufm.com/episode/69608f978f388c61e1fa0ad0

运行：cd backend && python tests/test_e2e_lovart.py
"""
import asyncio
import json
import sys
import os
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

PODCAST_URL = "https://www.xiaoyuzhoufm.com/episode/69608f978f388c61e1fa0ad0"
ARTIFACTS_DIR = Path(__file__).resolve().parent / "artifacts"
ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)


def _log(msg: str):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[e2e {ts}] {msg}", flush=True)


def _save(name: str, data):
    path = ARTIFACTS_DIR / name
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    _log(f"已保存: {path.name}")


async def step1_parse():
    """步骤1：解析播客 URL（提取音频地址和元数据）"""
    from core.services.podcast_service import PodcastService

    _log(f"=== 步骤1: 解析播客 URL ===")
    _log(f"链接: {PODCAST_URL}")

    svc = PodcastService()
    t0 = time.perf_counter()
    episode = await svc.extract(PODCAST_URL)
    elapsed = time.perf_counter() - t0

    _log(f"解析完成 ({elapsed:.1f}s)")
    _log(f"  播客名:  {episode.podcast_name}")
    _log(f"  标题:    {episode.title}")
    _log(f"  时长:    {episode.duration:.0f}s ({episode.duration/60:.1f}min)")
    _log(f"  主持人:  {episode.host_name}")
    _log(f"  嘉宾:    {'、'.join(episode.guest_names)}")
    _log(f"  公司:    {'、'.join(episode.company_names)}")
    _log(f"  专有名词: {'、'.join(episode.proper_nouns)}")
    _log(f"  音频URL: {episode.audio_url[:80]}...")

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    _save(f"e2e_parse_lovart_{ts}.json", {
        "step": "parse",
        "url": PODCAST_URL,
        "elapsed_seconds": elapsed,
        "episode_id": episode.episode_id,
        "audio_url": episode.audio_url,
        "title": episode.title,
        "podcast_name": episode.podcast_name,
        "duration": episode.duration,
        "host_name": episode.host_name,
        "guest_names": episode.guest_names,
        "company_names": episode.company_names,
        "proper_nouns": episode.proper_nouns,
        "description": episode.description,
    })

    return episode


async def step2_transcribe(episode):
    """步骤2：ASR 转写"""
    from core.workflow.state import create_initial_state
    from core.workflow.nodes.transcription import transcription_node

    _log(f"=== 步骤2: ASR 转写 ===")
    _log(f"音频时长: {episode.duration/60:.1f} 分钟，预计需要较长时间...")

    state = create_initial_state(
        task_id=episode.episode_id,
        audio_path=episode.audio_url,
        title=episode.title,
        author=episode.host_name or episode.podcast_name,
        description=episode.description,
        host_name=episode.host_name,
        guest_names=episode.guest_names,
        company_names=episode.company_names,
        podcast_intro=episode.shownotes_text[:1000] if hasattr(episode, 'shownotes_text') else "",
        proper_nouns=episode.proper_nouns,
    )

    t0 = time.perf_counter()
    output = await transcription_node(state)
    elapsed = time.perf_counter() - t0
    state.update(output)

    transcription = state["transcription"]
    segments = transcription.get("segments", [])
    _log(f"转写完成 ({elapsed:.1f}s): {len(segments)} 个片段")

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    _save(f"e2e_transcription_lovart_{ts}.json", {
        "step": "transcription",
        "elapsed_seconds": elapsed,
        "segment_count": len(segments),
        "transcription": transcription,
        "initial_state": {
            "task_id": state["task_id"],
            "audio_path": state["audio_path"],
            "title": state["title"],
            "author": state["author"],
            "host_name": state["host_name"],
            "guest_names": state["guest_names"],
            "company_names": state["company_names"],
            "podcast_intro": state.get("podcast_intro", ""),
            "proper_nouns": state["proper_nouns"],
        },
    })

    return state


async def step3_compose(state):
    """步骤3：内容成稿（Compose）"""
    from core.workflow.nodes.compose import compose_node

    transcription = state["transcription"]
    segments = transcription.get("segments", [])
    _log(f"=== 步骤3: 内容成稿 ===")
    _log(f"输入: {len(segments)} 个片段")

    t0 = time.perf_counter()
    output = await compose_node(state)
    elapsed = time.perf_counter() - t0
    state.update(output)

    composed = state["composed_content"]
    chapters = composed.get("chapters", [])
    style = composed.get("writing_style", "?")
    total_words = sum(len(ch.get("content", "")) for ch in chapters)

    _log(f"成稿完成 ({elapsed:.1f}s)")
    _log(f"  写作风格: {style}")
    _log(f"  章节数: {len(chapters)}")
    _log(f"  总字数: {total_words}")
    for i, ch in enumerate(chapters, 1):
        _log(f"  {i}. {ch.get('title', '?')} ({len(ch.get('content', ''))}字)")

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    _save(f"e2e_compose_lovart_{ts}.json", {
        "step": "compose",
        "elapsed_seconds": elapsed,
        "composed_content": composed,
    })

    # 生成可读的 Markdown 审查报告
    md_lines = [
        "# 成稿审查报告 — 罗永浩 × 陈冕（Lovart）",
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

    md_path = ARTIFACTS_DIR / f"e2e_compose_review_lovart_{ts}.md"
    md_path.write_text("\n".join(md_lines), encoding="utf-8")
    _log(f"审查报告: {md_path.name}")

    return state


async def main():
    _log("=" * 60)
    _log("端到端测试: 罗永浩 × 陈冕（Lovart）")
    _log("=" * 60)

    total_t0 = time.perf_counter()

    # 步骤1: 解析
    episode = await step1_parse()

    # 步骤2: 转写
    state = await step2_transcribe(episode)

    # 步骤3: 成稿
    state = await step3_compose(state)

    total_elapsed = time.perf_counter() - total_t0
    _log("=" * 60)
    _log(f"全部完成！总耗时: {total_elapsed:.1f}s ({total_elapsed/60:.1f}min)")
    _log("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
