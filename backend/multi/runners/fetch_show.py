"""
fetch_show — 抓取小宇宙栏目下所有单集元数据

用法：

    cd backend
    python -m workbench.runners.fetch_show \\
        --url https://www.xiaoyuzhoufm.com/podcast/6807993539793c80b08070f0 \\
        --job possibility

输出：

    storage/workbench/jobs/<job_id>/
      show.json
      episodes_index.json
      episodes/ep01/meta.json
      episodes/ep02/meta.json
      ...

设计：默认串行 + 集间随机间隔 1-2.5s，避免触发小宇宙反爬。
LLM 元信息提取（嘉宾/公司/专有名词）和 C 端单集解析共用同一份服务，
所以每集会调用一次 LLM，13 集大约 5-8 分钟跑完。
"""
from __future__ import annotations

# 必须最先 setup，覆盖 .env 防止误连生产
from multi.env import setup
setup()

import argparse
import asyncio
import logging
import random
import sys
import traceback
from dataclasses import asdict
from datetime import datetime
from typing import Any

from multi.io import JobPaths, write_json, slugify
from multi.parsers.xiaoyuzhou_show import parse_show

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("fetch_show")


def _episode_to_dict(ep) -> dict[str, Any]:
    """PodcastEpisode dataclass → 可序列化 dict"""
    return {
        "episode_id": ep.episode_id,
        "title": ep.title,
        "podcast_name": ep.podcast_name,
        "audio_url": ep.audio_url,
        "duration_seconds": ep.duration,
        "duration_minutes": round(ep.duration / 60, 1) if ep.duration else 0.0,
        "publish_date": ep.publish_date,
        "cover_url": ep.cover_url,
        "host_name": ep.host_name,
        "guest_names": ep.guest_names,
        "name_aliases": ep.name_aliases,
        "company_names": ep.company_names,
        "proper_nouns": ep.proper_nouns,
        "description": ep.description,
        "shownotes_text": ep.shownotes_text,
    }


async def _fetch_one_episode(
    *,
    semaphore: asyncio.Semaphore,
    idx: int,
    eid: str,
    url: str,
    paths: JobPaths,
    skip_llm: bool,
    skip_existing: bool,
) -> dict[str, Any]:
    """抓单集：返回精简 index 条目（含成功/失败状态）"""
    ep_dir = paths.episode_dir(idx)
    meta_path = ep_dir / "meta.json"

    if skip_existing and meta_path.exists():
        logger.info("[ep%02d] 已存在，跳过：%s", idx, meta_path)
        try:
            from multi.io import read_json
            existing = read_json(meta_path)
            return {
                "index": idx,
                "episode_id": eid,
                "url": url,
                "status": "skipped_existing",
                "title": existing.get("title", ""),
                "audio_url": existing.get("audio_url", ""),
            }
        except Exception:
            pass

    async with semaphore:
        # 如果是非首个请求，先随机等 1.0-2.5s
        await asyncio.sleep(random.uniform(1.0, 2.5))

        from core.services.podcast_service import get_podcast_service
        service = get_podcast_service()

        if skip_llm:
            import core.services.podcast_service as ps_module
            async def _noop(*args, **kwargs):
                return {
                    "host_name": "", "guest_names": [], "name_aliases": {},
                    "company_names": [], "proper_nouns": [],
                }
            ps_module._llm_extract_metadata = _noop  # type: ignore[assignment]

        try:
            logger.info("[ep%02d] 解析 %s", idx, url)
            ep = await service._extract_xiaoyuzhou(url)
            data = _episode_to_dict(ep)
            data["_fetched_at"] = datetime.now().isoformat(timespec="seconds")
            write_json(meta_path, data)
            logger.info(
                "[ep%02d] ✅ %s · %.1f分钟 · %s",
                idx, data["title"][:40], data["duration_minutes"], data["audio_url"][:60],
            )
            return {
                "index": idx,
                "episode_id": eid,
                "url": url,
                "status": "ok",
                "title": data["title"],
                "audio_url": data["audio_url"],
                "duration_minutes": data["duration_minutes"],
                "publish_date": data["publish_date"],
            }
        except Exception as e:
            logger.error("[ep%02d] ❌ 解析失败：%s", idx, e)
            traceback.print_exc()
            err_path = ep_dir / "error.json"
            write_json(err_path, {
                "url": url,
                "error": str(e),
                "type": type(e).__name__,
                "_failed_at": datetime.now().isoformat(timespec="seconds"),
            })
            return {
                "index": idx,
                "episode_id": eid,
                "url": url,
                "status": "error",
                "error": str(e),
            }


async def main_async(args: argparse.Namespace) -> int:
    if args.concurrency > 3:
        raise SystemExit("--concurrency 不允许超过 3，避免被小宇宙封")

    logger.info("解析栏目页：%s", args.url)
    parse_result = await parse_show(args.url)
    show = parse_result.show

    job_hint = args.job or slugify(show.title or show.pid, max_len=30)
    paths = JobPaths.create(job_id=args.job, hint=job_hint)
    logger.info("作业目录：%s", paths.root)

    show_payload = {
        **asdict(show),
        "show_url": show.show_url,
        "_fetched_at": datetime.now().isoformat(timespec="seconds"),
    }
    show_payload.pop("raw", None)
    write_json(paths.show_json, show_payload)
    logger.info(
        "节目元数据 ✅ %s · 主播 %s · %d 集 · 封面 %s",
        show.title,
        "、".join(show.podcaster_names) or show.author_label or "(空)",
        show.episode_count,
        show.cover_url[:60],
    )
    logger.info("发现 %d 集（按页面顺序：最新→最旧）", len(parse_result.episode_ids))

    # 写一次粗略 index（先记录 eid 列表，后续补充每集状态）
    index_payload = {
        "show_pid": show.pid,
        "show_title": show.title,
        "total": len(parse_result.episode_ids),
        "order": "page_order_newest_first",
        "episodes": [
            {"index": i + 1, "episode_id": eid, "url": url, "status": "pending"}
            for i, (eid, url) in enumerate(zip(parse_result.episode_ids, parse_result.episode_urls))
        ],
    }
    write_json(paths.episodes_index_json, index_payload)

    sem = asyncio.Semaphore(args.concurrency)
    tasks = [
        _fetch_one_episode(
            semaphore=sem,
            idx=i + 1,
            eid=eid,
            url=url,
            paths=paths,
            skip_llm=args.no_llm,
            skip_existing=args.resume,
        )
        for i, (eid, url) in enumerate(zip(parse_result.episode_ids, parse_result.episode_urls))
    ]
    results = await asyncio.gather(*tasks)

    # 用 result 回填 episodes_index
    by_idx = {r["index"]: r for r in results}
    for ep_entry in index_payload["episodes"]:
        r = by_idx.get(ep_entry["index"], {})
        ep_entry.update({k: v for k, v in r.items() if k not in ("index",)})
    write_json(paths.episodes_index_json, index_payload)

    ok = sum(1 for r in results if r.get("status") == "ok")
    err = sum(1 for r in results if r.get("status") == "error")
    skipped = sum(1 for r in results if r.get("status") == "skipped_existing")
    logger.info("=" * 50)
    logger.info("完成：成功 %d · 跳过 %d · 失败 %d · 总 %d", ok, skipped, err, len(results))
    logger.info("作业目录：%s", paths.root)

    return 1 if err else 0


def main() -> None:
    p = argparse.ArgumentParser(
        description="抓取小宇宙栏目下所有单集元数据",
    )
    p.add_argument("--url", required=True, help="小宇宙栏目链接 https://www.xiaoyuzhoufm.com/podcast/<pid>")
    p.add_argument("--job", default="", help="作业 ID（默认根据节目名 + 时间戳生成）")
    p.add_argument(
        "--concurrency", type=int, default=1,
        help="单集并发数，默认 1（串行）。最大 3，避免触发反爬。",
    )
    p.add_argument(
        "--no-llm", action="store_true",
        help="跳过 LLM 元信息提取（嘉宾/公司/专有名词），加速 + 不消耗 LLM 额度",
    )
    p.add_argument(
        "--resume", action="store_true",
        help="跳过已存在 meta.json 的单集（断点续抓）",
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
