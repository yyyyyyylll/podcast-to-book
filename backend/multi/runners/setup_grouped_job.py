"""
setup_grouped_job — 为"分组单集"范式准备作业目录。

读取 book_config.json，从 source_job 中按配置复制各单集数据到本作业：
  - meta.json（更新 audio_url 为本地绝对路径）
  - article_text.md, article_parsed.json, media_manifest.json
  - images/ 目录（全部文件）
  - audio/ 目录（全部文件；多段自动用 ffmpeg 合并）

同时生成：
  - show.json（从源单集 meta 组装）
  - episodes_index.json

支持断点续跑（已存在则跳过，--force 强制覆盖）。

用法：
    cd backend
    python -m workbench.runners.setup_grouped_job --job dart__jh__book1__2026-05-07
    python -m workbench.runners.setup_grouped_job --job dart__jh__book1__2026-05-07 --force
"""
from __future__ import annotations

from multi.env import setup
setup()

import argparse
import logging
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from multi.io import JobPaths, read_json, write_json, workbench_root

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("setup_grouped_job")


def _copy_file_if_needed(src: Path, dst: Path, force: bool) -> bool:
    """复制单文件，已存在且非 force 则跳过。返回是否实际复制。"""
    if dst.exists() and not force:
        return False
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    return True


def _copy_dir_if_needed(src: Path, dst: Path, force: bool) -> int:
    """复制整个目录，已存在且非 force 则跳过各文件。返回复制文件数。"""
    if not src.exists():
        return 0
    dst.mkdir(parents=True, exist_ok=True)
    count = 0
    for f in src.iterdir():
        if f.is_file():
            if _copy_file_if_needed(f, dst / f.name, force):
                count += 1
    return count


def _concat_audio_ffmpeg(audio_files: list[Path], output: Path) -> bool:
    """
    用 ffmpeg concat 过滤器合并多个 MP3 文件为单一 combined.mp3。
    返回是否成功。
    """
    if len(audio_files) == 1:
        shutil.copy2(audio_files[0], output)
        return True

    # 构造 ffmpeg 命令：-i a1 -i a2 ... -filter_complex concat=n=N:v=0:a=1
    n = len(audio_files)
    cmd = []
    for af in audio_files:
        cmd += ["-i", str(af)]
    cmd += [
        "-filter_complex", f"concat=n={n}:v=0:a=1[out]",
        "-map", "[out]",
        "-y",
        str(output),
    ]
    full_cmd = ["ffmpeg"] + cmd
    logger.info("合并音频：%s", " ".join(full_cmd))
    try:
        result = subprocess.run(full_cmd, capture_output=True, text=True, timeout=300)
        if result.returncode != 0:
            logger.error("ffmpeg 失败：%s", result.stderr[-500:])
            return False
        return True
    except FileNotFoundError:
        logger.error("ffmpeg 未找到，请确保 ffmpeg 已安装并在 PATH 中")
        return False
    except subprocess.TimeoutExpired:
        logger.error("ffmpeg 超时")
        return False


def _setup_episode(
    *,
    book_ep_index: int,
    source_ep_id: str,
    has_audio: bool,
    audio_parts: int,
    source_episodes_dir: Path,
    target_paths: JobPaths,
    force: bool,
) -> dict[str, Any]:
    """
    复制单集数据。返回结果摘要 dict。
    """
    src_ep_dir = source_episodes_dir / source_ep_id
    if not src_ep_dir.exists():
        logger.warning("[ep%02d] 源目录不存在，跳过：%s", book_ep_index, src_ep_dir)
        return {"index": book_ep_index, "status": "no_source"}

    dst_ep_dir = target_paths.episode_dir(book_ep_index)
    label = f"ep{book_ep_index:02d}←{source_ep_id}"

    # --- 复制静态文件 ---
    files_copied = 0
    for fname in ("article_text.md", "article_parsed.json", "media_manifest.json"):
        src_f = src_ep_dir / fname
        if src_f.exists():
            if _copy_file_if_needed(src_f, dst_ep_dir / fname, force):
                files_copied += 1

    # --- 复制 images/ ---
    img_count = _copy_dir_if_needed(src_ep_dir / "images", dst_ep_dir / "images", force)
    logger.info("[%s] 图片已复制 %d 张", label, img_count)

    # --- 处理音频 ---
    audio_url = ""
    if has_audio:
        src_audio_dir = src_ep_dir / "audio"
        dst_audio_dir = dst_ep_dir / "audio"
        dst_audio_dir.mkdir(parents=True, exist_ok=True)

        # 找到所有源音频文件（排序）
        src_audio_files = sorted(src_audio_dir.glob("audi_*.mp3")) if src_audio_dir.exists() else []
        actual_parts = len(src_audio_files)

        if actual_parts == 0:
            logger.warning("[%s] has_audio=true 但未找到 audio 文件", label)
        elif actual_parts == 1 or audio_parts <= 1:
            # 单文件：直接复制，audio_url 指向它
            src_file = src_audio_files[0]
            dst_file = dst_audio_dir / src_file.name
            if _copy_file_if_needed(src_file, dst_file, force):
                logger.info("[%s] 音频已复制：%s", label, dst_file.name)
            audio_url = str(dst_file.resolve())
        else:
            # 多文件：先复制所有，再合并
            for af in src_audio_files:
                _copy_file_if_needed(af, dst_audio_dir / af.name, force)

            combined = dst_audio_dir / "combined.mp3"
            if combined.exists() and not force:
                logger.info("[%s] combined.mp3 已存在，跳过合并", label)
            else:
                # 用已复制的本地文件进行合并
                dst_audio_files = sorted(dst_audio_dir.glob("audi_*.mp3"))
                logger.info("[%s] 合并 %d 段音频 → combined.mp3", label, len(dst_audio_files))
                ok = _concat_audio_ffmpeg(dst_audio_files, combined)
                if not ok:
                    logger.error("[%s] 音频合并失败，fallback 到第一段", label)
                    combined = dst_audio_dir / sorted(dst_audio_dir.glob("audi_*.mp3"))[0].name

            audio_url = str(combined.resolve())

    # --- 复制并更新 meta.json ---
    src_meta_path = src_ep_dir / "meta.json"
    dst_meta_path = dst_ep_dir / "meta.json"

    if dst_meta_path.exists() and not force:
        logger.info("[%s] meta.json 已存在，跳过", label)
    elif src_meta_path.exists():
        meta = read_json(src_meta_path)
        # 更新 audio_url 为本地绝对路径
        meta["audio_url"] = audio_url
        meta["_workbench"] = {
            "source_job": source_ep_id,
            "book_ep_index": book_ep_index,
            "has_audio": has_audio,
            "audio_parts": audio_parts,
            "_setup_at": datetime.now().isoformat(timespec="seconds"),
        }
        write_json(dst_meta_path, meta)
        files_copied += 1
        logger.info("[%s] meta.json 已写入（audio_url=%s）", label, audio_url[:60] if audio_url else "")
    else:
        logger.warning("[%s] 源 meta.json 不存在", label)

    return {
        "index": book_ep_index,
        "source_ep_id": source_ep_id,
        "status": "ok",
        "files_copied": files_copied,
        "images": img_count,
        "audio_url": audio_url,
    }


def _build_show_json(book_config: dict[str, Any], source_episodes_dir: Path) -> dict[str, Any]:
    """从 book_config 和第一个有效 meta.json 组装 show.json。"""
    # 取第一集的 meta 作为节目基础信息
    first_ep_id = None
    for chapter in book_config.get("chapters", []):
        eps = chapter.get("episodes", [])
        if eps:
            first_ep_id = eps[0].get("source_ep_id")
            break

    base_meta: dict[str, Any] = {}
    if first_ep_id:
        meta_path = source_episodes_dir / first_ep_id / "meta.json"
        if meta_path.exists():
            base_meta = read_json(meta_path)

    return {
        "title": book_config.get("book_title", ""),
        "subtitle": book_config.get("book_subtitle", ""),
        "podcast_name": base_meta.get("podcast_name", "燃点艺术 DART"),
        "paradigm": book_config.get("paradigm", "grouped_episodes"),
        "source_job": book_config.get("source_job", ""),
        "host_name": base_meta.get("host_name", ""),
        "podcaster_names": [base_meta.get("host_name", "")] if base_meta.get("host_name") else [],
        "description": book_config.get("book_subtitle", ""),
        "brief": book_config.get("book_subtitle", ""),
        "_generated_at": datetime.now().isoformat(timespec="seconds"),
    }


def _build_episodes_index(book_config: dict[str, Any]) -> dict[str, Any]:
    """从 book_config 生成 episodes_index.json。"""
    episodes = []
    for chapter in book_config.get("chapters", []):
        for ep_cfg in chapter.get("episodes", []):
            idx = ep_cfg["book_ep_index"]
            episodes.append({
                "index": idx,
                "source_ep_id": ep_cfg.get("source_ep_id", ""),
                "chapter_index": chapter["chapter_index"],
                "chapter_title": chapter["chapter_title"],
            })
    return {
        "total": len(episodes),
        "paradigm": book_config.get("paradigm", "grouped_episodes"),
        "episodes": episodes,
        "_generated_at": datetime.now().isoformat(timespec="seconds"),
    }


def main() -> None:
    p = argparse.ArgumentParser(description="为分组单集书范式准备作业目录")
    p.add_argument("--job", required=True, help="目标作业 ID（需包含 book_config.json）")
    p.add_argument("--force", action="store_true", help="强制覆盖已有文件")
    args = p.parse_args()

    # 打开目标作业目录（book_config.json 已在那里）
    target_paths = JobPaths.open(args.job)
    config_path = target_paths.root / "book_config.json"
    if not config_path.exists():
        raise SystemExit(f"未找到 book_config.json：{config_path}")

    book_config = read_json(config_path)
    source_job = book_config.get("source_job")
    if not source_job:
        raise SystemExit("book_config.json 缺少 source_job 字段")

    source_root = workbench_root() / "jobs" / source_job
    if not source_root.exists():
        raise SystemExit(f"源作业目录不存在：{source_root}")

    source_episodes_dir = source_root / "episodes"
    logger.info("目标作业：%s", target_paths.root)
    logger.info("源作业：%s", source_root)

    # 生成/更新 show.json
    show_path = target_paths.show_json
    if show_path.exists() and not args.force:
        logger.info("show.json 已存在，跳过")
    else:
        show = _build_show_json(book_config, source_episodes_dir)
        write_json(show_path, show)
        logger.info("show.json 已生成")

    # 生成/更新 episodes_index.json
    index_path = target_paths.episodes_index_json
    if index_path.exists() and not args.force:
        logger.info("episodes_index.json 已存在，跳过")
    else:
        index = _build_episodes_index(book_config)
        write_json(index_path, index)
        logger.info("episodes_index.json 已生成（%d 集）", index["total"])

    # 逐集复制数据
    results = []
    for chapter in book_config.get("chapters", []):
        chapter_idx = chapter["chapter_index"]
        logger.info("--- 第%d章：%s ---", chapter_idx, chapter["chapter_title"])
        for ep_cfg in chapter.get("episodes", []):
            result = _setup_episode(
                book_ep_index=ep_cfg["book_ep_index"],
                source_ep_id=ep_cfg["source_ep_id"],
                has_audio=ep_cfg.get("has_audio", True),
                audio_parts=ep_cfg.get("audio_parts", 1),
                source_episodes_dir=source_episodes_dir,
                target_paths=target_paths,
                force=args.force,
            )
            results.append(result)

    ok = sum(1 for r in results if r.get("status") == "ok")
    err = sum(1 for r in results if r.get("status") != "ok")
    logger.info("完成：成功 %d · 失败/跳过 %d", ok, err)
    sys.exit(1 if err else 0)


if __name__ == "__main__":
    main()
