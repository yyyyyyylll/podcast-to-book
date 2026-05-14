"""
单独测试 transcription 节点：MiniMax 创始人闫俊杰×罗永浩
播客链接: https://www.xiaoyuzhoufm.com/episode/69392768281939cce65925d3

运行：cd backend && python tests/test_transcription_minimax.py
"""
import asyncio
import json
import sys
import os
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

PODCAST_URL = "https://www.xiaoyuzhoufm.com/episode/69392768281939cce65925d3"
ARTIFACTS_DIR = Path(__file__).resolve().parent / "artifacts"
ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)


def _log(msg: str):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[transcription {ts}] {msg}", flush=True)


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
    _save(f"transcription_parse_minimax_{ts}.json", {
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
    """步骤2：ASR 转写 + LLM 清理（含说话人修正）"""
    from core.workflow.state import create_initial_state
    from core.workflow.nodes.transcription import transcription_node

    _log(f"=== 步骤2: ASR 转写 + LLM 清理 ===")
    _log(f"音频时长: {episode.duration/60:.1f} 分钟")
    _log(f"注意: 这是一个 {episode.duration/60:.0f} 分钟的长播客，预计需要较长时间...")

    state = create_initial_state(
        task_id=episode.episode_id,
        audio_path=episode.audio_url,
        title=episode.title,
        author=episode.host_name or episode.podcast_name,
        description=episode.description,
        host_name=episode.host_name,
        guest_names=episode.guest_names,
        company_names=episode.company_names,
        podcast_intro=episode.shownotes_text[:1000] if hasattr(episode, 'shownotes_text') and episode.shownotes_text else "",
        proper_nouns=episode.proper_nouns,
    )

    t0 = time.perf_counter()
    output = await transcription_node(state)
    elapsed = time.perf_counter() - t0
    state.update(output)

    transcription = state["transcription"]
    segments = transcription.get("segments", [])
    speaker_corrections = transcription.get("speaker_corrections", 0)
    
    _log(f"转写完成 ({elapsed:.1f}s / {elapsed/60:.1f}min)")
    _log(f"  原始片段: {transcription.get('raw_segment_count', '?')}")
    _log(f"  最终片段: {len(segments)}")
    _log(f"  说话人数: {transcription.get('speaker_count', '?')}")
    _log(f"  说话人修正: {speaker_corrections} 处")
    _log(f"  总时长: {transcription.get('duration', 0)/60:.1f}min")

    # 统计各说话人的发言量
    speaker_stats = {}
    for seg in segments:
        speaker = seg.get("speaker", "未知")
        speaker_stats[speaker] = speaker_stats.get(speaker, 0) + len(seg.get("text", ""))
    
    _log(f"  说话人统计:")
    for speaker, chars in sorted(speaker_stats.items(), key=lambda x: -x[1]):
        _log(f"    - {speaker}: {chars} 字")

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    _save(f"transcription_minimax_{ts}.json", {
        "step": "transcription",
        "elapsed_seconds": elapsed,
        "segment_count": len(segments),
        "speaker_corrections": speaker_corrections,
        "speaker_stats": speaker_stats,
        "transcription": transcription,
        "initial_state": {
            "task_id": state["task_id"],
            "audio_path": state["audio_path"],
            "title": state["title"],
            "author": state["author"],
            "host_name": state["host_name"],
            "guest_names": state["guest_names"],
            "company_names": state["company_names"],
            "proper_nouns": state["proper_nouns"],
        },
    })

    # 生成可读的 Markdown 审查报告
    md_lines = [
        "# Transcription 审查报告 — MiniMax 闫俊杰×罗永浩",
        "",
        f"- **播客**: {episode.podcast_name}",
        f"- **标题**: {episode.title}",
        f"- **时长**: {episode.duration/60:.1f} 分钟",
        f"- **主持人**: {episode.host_name}",
        f"- **嘉宾**: {'、'.join(episode.guest_names)}",
        f"- **原始片段数**: {transcription.get('raw_segment_count', '?')}",
        f"- **最终片段数**: {len(segments)}",
        f"- **说话人修正**: {speaker_corrections} 处",
        f"- **处理耗时**: {elapsed:.1f}s ({elapsed/60:.1f}min)",
        "",
        "## 说话人统计",
        "",
    ]
    for speaker, chars in sorted(speaker_stats.items(), key=lambda x: -x[1]):
        md_lines.append(f"- **{speaker}**: {chars} 字")
    
    md_lines += [
        "",
        "---",
        "",
        "## 对话内容",
        "",
    ]

    # 标记被修正的片段
    for i, seg in enumerate(segments):
        speaker = seg.get("speaker", "未知")
        text = seg.get("text", "")
        corrected = seg.get("speaker_corrected", False)
        original_speaker = seg.get("original_speaker", "")
        
        if corrected and original_speaker:
            md_lines.append(f"**【{speaker}】** ⚠️ *（原标记: {original_speaker}）*")
        else:
            md_lines.append(f"**【{speaker}】**")
        md_lines.append(f"{text}")
        md_lines.append("")

    md_path = ARTIFACTS_DIR / f"transcription_review_minimax_{ts}.md"
    md_path.write_text("\n".join(md_lines), encoding="utf-8")
    _log(f"审查报告: {md_path.name}")

    return state


async def main():
    _log("=" * 60)
    _log("Transcription 节点测试: MiniMax 闫俊杰×罗永浩")
    _log("=" * 60)

    total_t0 = time.perf_counter()

    # 步骤1: 解析
    episode = await step1_parse()

    # 步骤2: 转写
    state = await step2_transcribe(episode)

    total_elapsed = time.perf_counter() - total_t0
    _log("=" * 60)
    _log(f"测试完成！总耗时: {total_elapsed:.1f}s ({total_elapsed/60:.1f}min)")
    _log("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
