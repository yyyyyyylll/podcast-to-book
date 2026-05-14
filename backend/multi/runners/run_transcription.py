"""
run_transcription — 调用腾讯云 ASR 转写单集，输出原始 transcript.json

输入：episodes/epNN/meta.json （audio_url + proper_nouns + host_name + name_aliases）
输出：episodes/epNN/transcript_raw.json （腾讯云 ASR 原始结果，带 segments / duration / 说话人标签）

设计：
- 默认走 URL 模式：直接把小宇宙 audio_url 提交给腾讯云，腾讯云去拉，**不消耗本地带宽**
- 走 URL 失败时（罕见）才回落到本地音频（episodes/epNN/audio.m4a），但 .env.workbench
  的 COS 留空，本地文件 > 5MB 时会触发 ffmpeg 分片转写（C 端 fallback 链路）
- 热词来源：proper_nouns + 主播名 + name_aliases + 节目名
- 单集 ASR ~1-2 分钟（36 分钟音频）

用法：

    cd backend
    # 跑 ep11 试水（约 36 分钟音频，预计 1-2 分钟出结果）
    python -m workbench.runners.run_transcription --job possibility --only 11

    # 批量
    python -m workbench.runners.run_transcription --job possibility

    # 强制重跑（绕过缓存）
    python -m workbench.runners.run_transcription --job possibility --only 11 --force

    # 用本地音频文件代替 URL 模式（调试用）
    python -m workbench.runners.run_transcription --job possibility --only 11 --use-local
"""
from __future__ import annotations

from multi.env import setup
setup()

import argparse
import asyncio
import logging
import re
import sys
import time
import traceback
from datetime import datetime
from typing import Any

from multi.io import JobPaths, read_json, write_json

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("transcribe")


def _build_hotwords(meta: dict[str, Any], show_title: str) -> list[str]:
    """组装热词表。腾讯云 ASR 上限 300 个，单集远不会触顶。"""
    words: list[str] = []
    seen: set[str] = set()

    def add(items):
        for w in items if isinstance(items, list) else [items]:
            w = (w or "").strip()
            if w and w not in seen and len(w) <= 30:
                seen.add(w)
                words.append(w)

    if show_title:
        add(show_title)

    host_name = meta.get("host_name", "") or ""
    if host_name:
        for n in re.split(r"[、，,\s]+", host_name):
            add(n)

    add(meta.get("guest_names") or [])

    aliases = meta.get("name_aliases") or {}
    if isinstance(aliases, dict):
        for k, vs in aliases.items():
            add(k)
            if isinstance(vs, list):
                add(vs)

    add(meta.get("company_names") or [])
    add(meta.get("proper_nouns") or [])

    return words


async def _transcribe_one(
    *,
    idx: int,
    paths: JobPaths,
    show_title: str,
    use_local: bool,
    force: bool,
) -> dict[str, Any]:
    ep_dir = paths.episode_dir(idx)
    meta_path = ep_dir / "meta.json"
    transcript_path = ep_dir / "transcript_raw.json"

    if not meta_path.exists():
        logger.warning("[ep%02d] meta.json 不存在，跳过", idx)
        return {"index": idx, "status": "no_meta"}

    if not force and transcript_path.exists():
        try:
            existing = read_json(transcript_path)
            seg_count = len(existing.get("segments", []))
            duration = existing.get("duration", 0)
            if seg_count >= 10 and duration >= 60:
                logger.info(
                    "[ep%02d] transcript_raw.json 已存在（%d 段 · %.1f 分钟），跳过",
                    idx, seg_count, duration / 60,
                )
                return {
                    "index": idx,
                    "status": "skipped_existing",
                    "segments": seg_count,
                    "duration_minutes": round(duration / 60, 1),
                }
        except Exception:
            logger.warning("[ep%02d] 已有 transcript_raw.json 但解析失败，重新转写", idx)

    meta = read_json(meta_path)
    audio_url = meta.get("audio_url", "")
    audio_local = ep_dir / "audio.m4a"

    if use_local:
        if not audio_local.exists():
            logger.error("[ep%02d] --use-local 但本地音频不存在：%s", idx, audio_local)
            return {"index": idx, "status": "error", "error": "local audio missing"}
        audio_path = str(audio_local)
        logger.info("[ep%02d] 使用本地音频：%s", idx, audio_local.name)
    else:
        if not audio_url:
            logger.error("[ep%02d] audio_url 为空，无法走 URL 模式", idx)
            return {"index": idx, "status": "error", "error": "audio_url missing"}
        audio_path = audio_url
        logger.info("[ep%02d] 使用 URL 模式：%s...", idx, audio_url[:60])

    hotwords = _build_hotwords(meta, show_title)
    logger.info("[ep%02d] 热词 %d 个：%s", idx, len(hotwords), "、".join(hotwords[:8]) + ("..." if len(hotwords) > 8 else ""))

    duration_seconds = meta.get("duration_seconds") or 0
    title = meta.get("title", "")[:40]
    logger.info("[ep%02d] 标题：%s · 时长 %.1f 分钟 · 提交 ASR...", idx, title, duration_seconds / 60)

    from core.services.asr_service import get_asr_service
    asr = get_asr_service()

    started = time.monotonic()
    try:
        result = await asr.transcribe(
            audio_path=audio_path,
            hotwords=hotwords or None,
            audio_duration=duration_seconds or None,
        )
    except Exception as e:
        logger.error("[ep%02d] ❌ ASR 失败：%s", idx, e)
        traceback.print_exc()
        write_json(ep_dir / "error.json", {
            "stage": "transcribe",
            "audio": audio_path,
            "error": str(e),
            "type": type(e).__name__,
            "_failed_at": datetime.now().isoformat(timespec="seconds"),
        })
        return {"index": idx, "status": "error", "error": str(e)}

    elapsed = time.monotonic() - started

    enriched = {
        **result,
        "_workbench": {
            "submitted_audio": audio_path,
            "audio_url": audio_url,
            "hotwords": hotwords,
            "asr_seconds": round(elapsed, 1),
            "_transcribed_at": datetime.now().isoformat(timespec="seconds"),
        },
    }
    write_json(transcript_path, enriched)

    try:
        from core.workflow.nodes.transcription import save_asr_cache
        save_asr_cache(audio_path, result)
        logger.info("[ep%02d] 已灌入 ASR cache（下游 transcription_node 将命中）", idx)
    except Exception as e:
        logger.warning("[ep%02d] 灌 ASR cache 失败（不影响主流程）: %s", idx, e)

    seg_count = len(result.get("segments", []))
    duration = result.get("duration", 0)
    # 腾讯云返回的字段是 "speaker"（如"说话人1"），不是 "speaker_id"
    speakers = {seg.get("speaker") for seg in result.get("segments", []) if seg.get("speaker")}
    speaker_count = result.get("speaker_count") or len(speakers)

    logger.info(
        "[ep%02d] ✅ %d 段 · %.1f 分钟 · %d 说话人 · ASR 耗时 %.0fs",
        idx, seg_count, duration / 60, speaker_count, elapsed,
    )
    return {
        "index": idx,
        "status": "ok",
        "segments": seg_count,
        "duration_minutes": round(duration / 60, 1),
        "speakers": speaker_count,
        "asr_seconds": round(elapsed, 1),
    }


def _parse_only(only: str) -> set[int] | None:
    if not only:
        return None
    out: set[int] = set()
    for part in only.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            out.update(range(int(a), int(b) + 1))
        else:
            out.add(int(part))
    return out


async def main_async(args: argparse.Namespace) -> int:
    paths = JobPaths.open(args.job)
    if not paths.show_json.exists():
        raise SystemExit(f"未找到 show.json：{paths.root}")

    show = read_json(paths.show_json)
    index = read_json(paths.episodes_index_json)
    show_title = show.get("title", "")
    only = _parse_only(args.only)

    logger.info("作业目录：%s", paths.root)
    logger.info("节目：%s · 单集 %d", show_title, index.get("total", 0))
    if only is not None:
        logger.info("只处理：%s", sorted(only))

    results: list[dict[str, Any]] = []
    total_started = time.monotonic()

    for ep in index.get("episodes", []):
        idx = ep.get("index")
        if only is not None and idx not in only:
            continue
        result = await _transcribe_one(
            idx=idx,
            paths=paths,
            show_title=show_title,
            use_local=args.use_local,
            force=args.force,
        )
        results.append(result)

    elapsed = time.monotonic() - total_started
    ok = sum(1 for r in results if r.get("status") == "ok")
    skipped = sum(1 for r in results if r.get("status") == "skipped_existing")
    err = sum(1 for r in results if r.get("status") == "error")
    other = sum(1 for r in results if r.get("status") in ("no_meta",))

    logger.info("=" * 50)
    logger.info(
        "完成：成功 %d · 跳过 %d · 失败 %d · 缺料 %d · 总 %d · 耗时 %.0fs",
        ok, skipped, err, other, len(results), elapsed,
    )
    return 1 if err else 0


def main() -> None:
    p = argparse.ArgumentParser(description="批量调用腾讯云 ASR 转写单集")
    p.add_argument("--job", required=True, help="作业 ID")
    p.add_argument("--only", default="", help="只跑指定单集，例：1,5,11 或 1-5")
    p.add_argument("--force", action="store_true", help="覆盖已存在的 transcript_raw.json")
    p.add_argument(
        "--use-local", action="store_true",
        help="使用本地 audio.m4a 代替 URL 模式（调试用，会触发 base64/分片或 COS 上传）",
    )
    args = p.parse_args()

    try:
        rc = asyncio.run(main_async(args))
    except KeyboardInterrupt:
        logger.warning("用户中断")
        rc = 130
    sys.exit(rc)


if __name__ == "__main__":
    main()
