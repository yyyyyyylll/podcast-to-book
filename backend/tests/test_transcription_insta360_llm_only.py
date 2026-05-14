"""
从 ASR 缓存加载结果，只跑 LLM 清洗 + 合并（跳过 ASR 调用）
影石Insta360 创始人刘靖康×罗永浩

运行：cd backend && python3 tests/test_transcription_insta360_llm_only.py
"""
import asyncio
import json
import sys
import os
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

ASR_CACHE_FILE = Path(__file__).resolve().parent.parent / "storage" / "asr_cache" / "4692259261204b13.json"
ARTIFACTS_DIR = Path(__file__).resolve().parent / "artifacts"
ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)

EPISODE_META = {
    "episode_id": "694cb5e1946dca09253c1b2e",
    "title": "影石Insta360 创始人刘靖康×罗永浩！比生存更重要的是那些微小的念头",
    "podcast_name": "罗永浩的十字路口",
    "duration": 15877.0,
    "host_name": "罗永浩",
    "guest_names": ["刘靖康"],
    "company_names": ["影石", "Insta360"],
    "proper_nouns": ["VR", "AR", "全景相机", "专利流氓"],
    "description": "《十字路口》播客节目简介……",
}


def _log(msg: str):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[llm-only {ts}] {msg}", flush=True)


def _save(name: str, data):
    path = ARTIFACTS_DIR / name
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    _log(f"已保存: {path.name}")


async def main():
    from core.workflow.state import create_initial_state
    from core.workflow.nodes.transcription import (
        llm_clean_and_segment,
        merge_consecutive_same_speaker,
        fix_unmapped_speakers,
    )

    _log("=" * 60)
    _log("LLM-only 清洗: 影石Insta360 刘靖康×罗永浩")
    _log("=" * 60)

    # 加载 ASR 缓存
    _log(f"加载 ASR 缓存: {ASR_CACHE_FILE.name}")
    raw_transcription = json.loads(ASR_CACHE_FILE.read_text(encoding="utf-8"))
    raw_segments = raw_transcription.get("segments", [])
    _log(f"ASR 原始片段: {len(raw_segments)}")

    # 构建 state
    state = create_initial_state(
        task_id=EPISODE_META["episode_id"],
        audio_path="(from-cache)",
        title=EPISODE_META["title"],
        author=EPISODE_META["host_name"],
        description=EPISODE_META["description"],
        host_name=EPISODE_META["host_name"],
        guest_names=EPISODE_META["guest_names"],
        company_names=EPISODE_META["company_names"],
        proper_nouns=EPISODE_META["proper_nouns"],
    )

    # LLM 清洗 + 语义分段
    _log("开始 LLM 清洗 + 语义分段...")
    t0 = time.perf_counter()
    cleaned_segments = await llm_clean_and_segment(raw_segments, state)
    llm_elapsed = time.perf_counter() - t0
    _log(f"LLM 清洗完成 ({llm_elapsed:.1f}s / {llm_elapsed/60:.1f}min)")
    _log(f"  清洗后片段: {len(cleaned_segments)}")

    # 过滤空片段
    non_empty = [seg for seg in cleaned_segments if seg["text"].strip()]

    # 修复未映射的说话人
    non_empty = fix_unmapped_speakers(non_empty)

    # 合并同一说话人的连续发言
    final_segments = merge_consecutive_same_speaker(non_empty)
    _log(f"  合并后: {len(non_empty)} → {len(final_segments)} 个片段")

    # 统计
    speaker_stats = {}
    for seg in final_segments:
        speaker = seg.get("speaker", "未知")
        speaker_stats[speaker] = speaker_stats.get(speaker, 0) + len(seg.get("text", ""))

    speaker_corrections = sum(1 for seg in final_segments if seg.get("speaker_corrected"))

    _log(f"  说话人数: {len(speaker_stats)}")
    _log(f"  说话人修正: {speaker_corrections} 处")
    _log(f"  说话人统计:")
    for speaker, chars in sorted(speaker_stats.items(), key=lambda x: -x[1]):
        _log(f"    - {speaker}: {chars} 字")

    # 保存 JSON
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    _save(f"transcription_insta360_llm_{ts}.json", {
        "step": "llm_only",
        "llm_elapsed_seconds": llm_elapsed,
        "segment_count": len(final_segments),
        "speaker_corrections": speaker_corrections,
        "speaker_stats": speaker_stats,
        "transcription": {
            "segments": final_segments,
            "duration": raw_transcription.get("duration", 0),
            "speaker_count": len(speaker_stats),
            "segment_count": len(final_segments),
            "raw_segment_count": len(raw_segments),
            "speaker_corrections": speaker_corrections,
        },
    })

    # 生成 Markdown 审查报告
    md_lines = [
        "# Transcription 审查报告 — 影石Insta360 刘靖康×罗永浩 (LLM-only)",
        "",
        f"- **播客**: {EPISODE_META['podcast_name']}",
        f"- **标题**: {EPISODE_META['title']}",
        f"- **时长**: {EPISODE_META['duration']/60:.1f} 分钟",
        f"- **主持人**: {EPISODE_META['host_name']}",
        f"- **嘉宾**: {'、'.join(EPISODE_META['guest_names'])}",
        f"- **原始片段数**: {len(raw_segments)}",
        f"- **最终片段数**: {len(final_segments)}",
        f"- **说话人修正**: {speaker_corrections} 处",
        f"- **LLM处理耗时**: {llm_elapsed:.1f}s ({llm_elapsed/60:.1f}min)",
        "",
        "## 说话人统计",
        "",
    ]
    for speaker, chars in sorted(speaker_stats.items(), key=lambda x: -x[1]):
        md_lines.append(f"- **{speaker}**: {chars} 字")

    md_lines += ["", "---", "", "## 对话内容", ""]

    for seg in final_segments:
        speaker = seg.get("speaker", "未知")
        text = seg.get("text", "")
        corrected = seg.get("speaker_corrected", False)
        original_speaker = seg.get("original_speaker", "")

        if corrected and original_speaker:
            md_lines.append(f"**【{speaker}】** ⚠️ *（原标记: {original_speaker}）*")
        else:
            md_lines.append(f"**【{speaker}】**")
        md_lines.append(text)
        md_lines.append("")

    md_path = ARTIFACTS_DIR / f"transcription_review_insta360_llm_{ts}.md"
    md_path.write_text("\n".join(md_lines), encoding="utf-8")
    _log(f"审查报告: {md_path.name}")

    total_elapsed = time.perf_counter() - t0
    _log("=" * 60)
    _log(f"完成！总耗时: {total_elapsed:.1f}s ({total_elapsed/60:.1f}min)")
    _log("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
