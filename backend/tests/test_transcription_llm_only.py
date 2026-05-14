"""
单独测试 transcription 节点的 LLM 清理部分（跳过 ASR）

使用已有的 ASR 结果，只重新运行 LLM 清理 + 合并逻辑。

运行：cd backend && python tests/test_transcription_llm_only.py
"""
import asyncio
import json
import sys
import os
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# ASR 原始数据文件路径（如果有缓存的话）
ASR_CACHE_FILE = Path(__file__).resolve().parent / "artifacts" / "asr_raw_minimax.json"
ARTIFACTS_DIR = Path(__file__).resolve().parent / "artifacts"
ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)

# 播客元数据
PODCAST_METADATA = {
    "task_id": "69392768281939cce65925d3",
    "audio_url": "https://media.xyzcdn.net/68981df29e7bcd326eb91d88/lhQbAp06A7_S4DXOSNV6gDX1ZESx.m4a",
    "title": "MiniMax 创始人闫俊杰×罗永浩！大山并非无法翻越",
    "podcast_name": "罗永浩的十字路口",
    "host_name": "罗永浩",
    "guest_names": ["闫俊杰"],
    "company_names": ["MiniMax", "商汤科技"],
    "proper_nouns": ["AGI", "scaling", "ChatGPT", "GPT-4o", "agent"],
    "duration": 13859,
}


def _log(msg: str):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[llm-test {ts}] {msg}", flush=True)


def _save(name: str, data):
    path = ARTIFACTS_DIR / name
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    _log(f"已保存: {path.name}")


async def get_asr_segments():
    """获取 ASR 原始片段（优先用缓存）"""
    
    # 尝试加载缓存
    if ASR_CACHE_FILE.exists():
        _log(f"加载 ASR 缓存: {ASR_CACHE_FILE.name}")
        data = json.loads(ASR_CACHE_FILE.read_text(encoding="utf-8"))
        return data["segments"], data.get("duration", PODCAST_METADATA["duration"])
    
    # 没有缓存，需要重新调用 ASR
    _log("没有 ASR 缓存，重新调用 ASR 服务...")
    from core.services.asr_service import get_asr_service
    
    asr_service = get_asr_service()
    t0 = time.perf_counter()
    raw_transcription = await asr_service.transcribe(
        PODCAST_METADATA["audio_url"], 
        hotwords=PODCAST_METADATA["proper_nouns"]
    )
    elapsed = time.perf_counter() - t0
    
    segments = raw_transcription.get("segments", [])
    duration = raw_transcription.get("duration", 0)
    
    _log(f"ASR 完成 ({elapsed:.1f}s): {len(segments)} 个原始片段")
    
    # 保存缓存
    _save("asr_raw_minimax.json", {
        "segments": segments,
        "duration": duration,
        "audio_url": PODCAST_METADATA["audio_url"],
        "cached_at": datetime.now().isoformat(),
    })
    
    return segments, duration


async def run_llm_clean(raw_segments, duration):
    """运行 LLM 清理 + 合并"""
    from core.workflow.state import create_initial_state
    from core.workflow.nodes.transcription import (
        llm_clean_and_segment, 
        merge_consecutive_same_speaker
    )
    
    _log(f"=== LLM 清理测试 ===")
    _log(f"输入: {len(raw_segments)} 个原始 ASR 片段")
    
    # 创建 state
    state = create_initial_state(
        task_id=PODCAST_METADATA["task_id"],
        audio_path=PODCAST_METADATA["audio_url"],
        title=PODCAST_METADATA["title"],
        author=PODCAST_METADATA["host_name"],
        description="",
        host_name=PODCAST_METADATA["host_name"],
        guest_names=PODCAST_METADATA["guest_names"],
        company_names=PODCAST_METADATA["company_names"],
        podcast_intro="",
        proper_nouns=PODCAST_METADATA["proper_nouns"],
    )
    
    # Step 1: LLM 清理
    _log("Step 1: LLM 智能清理 + 语义分段 + 说话人修正...")
    t0 = time.perf_counter()
    cleaned_segments = await llm_clean_and_segment(raw_segments, state)
    llm_elapsed = time.perf_counter() - t0
    
    non_empty = [seg for seg in cleaned_segments if seg["text"].strip()]
    _log(f"LLM 清理完成 ({llm_elapsed:.1f}s): {len(non_empty)} 个片段")
    
    # Step 2: 合并同一说话人连续发言
    _log("Step 2: 合并同一说话人连续发言...")
    final_segments = merge_consecutive_same_speaker(non_empty)
    _log(f"合并完成: {len(non_empty)} → {len(final_segments)} 个片段")
    
    total_elapsed = time.perf_counter() - t0
    
    # 统计
    speaker_stats = {}
    speaker_corrections = 0
    for seg in final_segments:
        speaker = seg.get("speaker", "未知")
        speaker_stats[speaker] = speaker_stats.get(speaker, 0) + len(seg.get("text", ""))
        if seg.get("speaker_corrected"):
            speaker_corrections += 1
    
    _log(f"=== 结果统计 ===")
    _log(f"  处理耗时: {total_elapsed:.1f}s ({total_elapsed/60:.1f}min)")
    _log(f"  原始片段: {len(raw_segments)}")
    _log(f"  最终片段: {len(final_segments)}")
    _log(f"  说话人修正: {speaker_corrections} 处")
    _log(f"  说话人统计:")
    for speaker, chars in sorted(speaker_stats.items(), key=lambda x: -x[1]):
        _log(f"    - {speaker}: {chars} 字")
    
    # 构建完整 transcription
    transcription = {
        "segments": final_segments,
        "full_text": "\n".join([
            f"【{seg['speaker']}】{seg['text']}"
            for seg in final_segments
        ]),
        "duration": duration,
        "speaker_count": len(speaker_stats),
        "segment_count": len(final_segments),
        "raw_segment_count": len(raw_segments),
        "speaker_corrections": speaker_corrections,
    }
    
    # 保存结果
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    _save(f"transcription_llm_minimax_{ts}.json", {
        "step": "llm_clean_only",
        "elapsed_seconds": total_elapsed,
        "llm_elapsed_seconds": llm_elapsed,
        "segment_count": len(final_segments),
        "speaker_corrections": speaker_corrections,
        "speaker_stats": speaker_stats,
        "transcription": transcription,
    })
    
    # 生成 Markdown 审查报告
    md_lines = [
        "# Transcription 审查报告 — MiniMax 闫俊杰×罗永浩",
        "",
        f"- **测试类型**: LLM 清理测试（复用 ASR 缓存）",
        f"- **播客**: {PODCAST_METADATA['podcast_name']}",
        f"- **标题**: {PODCAST_METADATA['title']}",
        f"- **时长**: {duration/60:.1f} 分钟",
        f"- **主持人**: {PODCAST_METADATA['host_name']}",
        f"- **嘉宾**: {'、'.join(PODCAST_METADATA['guest_names'])}",
        f"- **原始片段数**: {len(raw_segments)}",
        f"- **最终片段数**: {len(final_segments)}",
        f"- **说话人修正**: {speaker_corrections} 处",
        f"- **处理耗时**: {total_elapsed:.1f}s ({total_elapsed/60:.1f}min)",
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

    for i, seg in enumerate(final_segments):
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

    md_path = ARTIFACTS_DIR / f"transcription_llm_review_minimax_{ts}.md"
    md_path.write_text("\n".join(md_lines), encoding="utf-8")
    _log(f"审查报告: {md_path.name}")
    
    return transcription


async def main():
    _log("=" * 60)
    _log("LLM 清理测试: MiniMax 闫俊杰×罗永浩")
    _log("=" * 60)
    
    total_t0 = time.perf_counter()
    
    # 获取 ASR 原始片段
    raw_segments, duration = await get_asr_segments()
    
    # 运行 LLM 清理
    transcription = await run_llm_clean(raw_segments, duration)
    
    total_elapsed = time.perf_counter() - total_t0
    _log("=" * 60)
    _log(f"测试完成！总耗时: {total_elapsed:.1f}s ({total_elapsed/60:.1f}min)")
    _log("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
