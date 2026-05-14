"""
diagnose_podcast_direction — EchoPress 整档播客内容方向诊断（独立工具）

针对一档播客的"全集文字资料"（节目名 + 节目简介 + 全部节目标题/简介），
做整档内容方向诊断 + 客户可见诊断 + 标题建议。

工作流（DESIGN.md §五 / §八）：
  1. 主诊断 LLM 调用：合并工作流 1+2+3，一次产出统一 JSON（diagnosis）
  2. QC 自检 LLM 调用：工作流 4 的 AI 部分，找过度拔高 / 凭空 / 一致性问题
  3. 硬规则校验：parser.run_rule_checks(diagnosis) → (errors, warnings)
  4. 合并：parser.merge_qc(rule_errors, rule_warnings, ai_qc) → 最终 quality_check
  5. 渲染：parser.render_customer_markdown(final_json) → 5 张卡片 markdown

用法：

    cd backend
    # 模式 A：复用 workbench fetch_show 已抓的 job 产出
    python -m workbench.podcast_direction.runner --job possibility

    # 模式 B：外部 JSON（结构见 examples/input_example.json）
    python -m workbench.podcast_direction.runner \
        --input ./my_pod.json --out ./diagnosis.json

    # 可选 flag
    [--premium]              # 主诊断用 settings.LLM_PREMIUM_MODEL
    [--qc-premium]           # QC 自检用 settings.LLM_PREMIUM_MODEL
    [--no-qc]                # 跳过 QC 自检（只用硬规则降噪）
    [--note "..."]           # 写进 metadata._note，便于复盘对比
    [--max-runs 5]           # 历史 runs 保留多少次（默认 5；0=不归档）
    [--no-runs-archive]      # 等价于 --max-runs 0
    [--no-validate]          # 跳过硬规则 + QC（仅调试用）

输入：
  --job 模式：storage/workbench/jobs/<job_id>/show.json + episodes/epNN/meta.json
  --input 模式：自定义 JSON（podcast_name + podcast_intro + episode_list[{title,intro}]）

输出：
  --job 模式：
    storage/workbench/jobs/<job_id>/direction_diagnosis.json
    storage/workbench/jobs/<job_id>/direction_diagnosis.md
    storage/workbench/jobs/<job_id>/podcast_direction_runs/<run_id>/...
  --input 模式：
    --out 指定路径(.json) + 同名 .md
    + <--out 同目录>/.podcast_direction_runs/<run_id>/...
"""
from __future__ import annotations

# 必须最先 setup，覆盖 .env 防止误连生产
from multi.env import setup
setup()

import argparse
import asyncio
import json
import logging
import shutil
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any

from multi.io import JobPaths, read_json, write_json
from multi.podcast_direction.contact import extract_contact_info
from multi.podcast_direction.parser import (
    merge_qc,
    render_customer_markdown,
    run_rule_checks,
)
from multi.podcast_direction.prompt import PROMPTS
from multi.podcast_direction.topics import extract_host_topic_references

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("podcast_direction")


TOTAL_INPUT_CHAR_LIMIT = 8000
PER_EPISODE_MIN_CAP = 80
LARGE_SHOW_PER_EPISODE_MIN_CAP = 20
SAMPLE_NEWEST = 30
SAMPLE_OLDEST = 10
SAMPLE_TRIGGER_EPISODE_COUNT = 50

PER_EPISODE_CAP_BY_COUNT = (
    (10, 400),
    (20, 300),
    (50, 200),
    (10**9, 120),
)

DIAGNOSIS_TEMPERATURE = 0.4
QC_TEMPERATURE = 0.3


def _per_episode_cap(episode_count: int) -> int:
    for threshold, cap in PER_EPISODE_CAP_BY_COUNT:
        if episode_count <= threshold:
            return cap
    return PER_EPISODE_CAP_BY_COUNT[-1][1]


def _truncate(text: str, max_chars: int) -> str:
    text = text.strip()
    if len(text) <= max_chars:
        return text
    return text[: max(max_chars - 1, 1)].rstrip() + "…"


def _aggregate_from_job(paths: JobPaths) -> tuple[dict[str, Any], dict[str, Any]]:
    """读 show.json + episodes/epNN/meta.json，聚合为标准输入。

    Returns:
        (input_payload, contact_raw)
        - input_payload: {podcast_name, podcast_intro, episode_list} —— 喂给 LLM 的诊断输入
        - contact_raw: 用来跑 contact.extract_contact_info 的原始字段（subscription_count /
          contacts / podcaster_names / description-完整版）。从 show.json 直接转交。
    """
    if not paths.show_json.exists():
        raise FileNotFoundError(
            f"show.json 不存在：{paths.show_json}（请先跑 fetch_show）"
        )
    if not paths.episodes_index_json.exists():
        raise FileNotFoundError(
            f"episodes_index.json 不存在：{paths.episodes_index_json}"
        )

    show = read_json(paths.show_json)
    index = read_json(paths.episodes_index_json)

    podcast_name = (show.get("title") or "").strip()
    podcast_intro = (
        show.get("description_clean")
        or show.get("description")
        or ""
    ).strip()

    episode_list: list[dict[str, str]] = []
    for ep in index.get("episodes", []):
        idx = ep.get("index")
        if idx is None:
            continue
        meta_path = paths.episode_dir(idx) / "meta.json"
        if not meta_path.exists():
            logger.warning("[ep%02d] meta.json 不存在，跳过", idx)
            continue
        meta = read_json(meta_path)
        title = (meta.get("title") or "").strip()
        intro = (
            meta.get("description")
            or meta.get("shownotes_text")
            or ""
        ).strip()
        if not title:
            continue
        episode_list.append({"title": title, "intro": intro})

    input_payload = {
        "podcast_name": podcast_name,
        "podcast_intro": podcast_intro,
        "episode_list": episode_list,
    }
    contact_raw = {
        "podcast_name": podcast_name,
        "podcaster_names": list(show.get("podcaster_names") or []),
        "contacts": list(show.get("contacts") or []),
        "description": (show.get("description") or "").strip(),
        "subscription_count": show.get("subscription_count"),
    }
    return input_payload, contact_raw


def _aggregate_from_file(path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    """--input 模式聚合。

    必填：podcast_name / podcast_intro / episode_list
    可选（用于联系方式提取）：podcaster_names / contacts / description / subscription_count
    都没有时，contact_raw 会保留空字段，最终 contact_info 里会带 warning 但不报错。
    """
    raw = read_json(path)
    if not isinstance(raw, dict):
        raise ValueError(f"--input 文件顶层必须是 JSON 对象：{path}")
    podcast_name = (raw.get("podcast_name") or "").strip()
    podcast_intro = (raw.get("podcast_intro") or "").strip()
    episode_list_raw = raw.get("episode_list") or []
    if not podcast_name:
        raise ValueError("--input 缺少 podcast_name")
    if not isinstance(episode_list_raw, list) or not episode_list_raw:
        raise ValueError("--input 的 episode_list 必须是非空数组")
    episode_list = []
    for i, ep in enumerate(episode_list_raw, 1):
        if not isinstance(ep, dict):
            raise ValueError(f"--input.episode_list[{i}] 不是对象")
        title = (ep.get("title") or "").strip()
        intro = (ep.get("intro") or "").strip()
        if not title:
            raise ValueError(f"--input.episode_list[{i}] 缺少 title")
        episode_list.append({"title": title, "intro": intro})

    input_payload = {
        "podcast_name": podcast_name,
        "podcast_intro": podcast_intro,
        "episode_list": episode_list,
    }
    contact_raw = {
        "podcast_name": podcast_name,
        "podcaster_names": list(raw.get("podcaster_names") or []),
        "contacts": list(raw.get("contacts") or []),
        "description": (raw.get("description") or podcast_intro).strip(),
        "subscription_count": raw.get("subscription_count"),
    }
    return input_payload, contact_raw


def _trim_input(raw: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """按 plan §十节策略对输入做自适应裁剪。返回 (trimmed_input, trim_stats)。"""
    episode_list = list(raw.get("episode_list") or [])
    original_count = len(episode_list)

    cap = _per_episode_cap(original_count)
    podcast_intro = (raw.get("podcast_intro") or "").strip()

    def _trimmed_with_cap(
        source_episodes: list[dict[str, Any]],
        per_cap: int,
    ) -> tuple[list[dict[str, str]], int]:
        out: list[dict[str, str]] = []
        total = len(podcast_intro)
        for ep in source_episodes:
            intro = _truncate(ep.get("intro") or "", per_cap)
            total += len(intro) + len(ep.get("title") or "")
            out.append({"title": ep.get("title", ""), "intro": intro})
        return out, total

    sampled = False
    trimmed, total_chars = _trimmed_with_cap(episode_list, cap)

    if total_chars > TOTAL_INPUT_CHAR_LIMIT and episode_list:
        min_cap = (
            LARGE_SHOW_PER_EPISODE_MIN_CAP
            if original_count > SAMPLE_TRIGGER_EPISODE_COUNT
            else PER_EPISODE_MIN_CAP
        )
        while total_chars > TOTAL_INPUT_CHAR_LIMIT and cap > min_cap:
            scale = TOTAL_INPUT_CHAR_LIMIT / max(total_chars, 1)
            adjusted_cap = max(int(cap * scale), min_cap)
            if adjusted_cap >= cap:
                adjusted_cap = cap - 1
            trimmed, total_chars = _trimmed_with_cap(episode_list, adjusted_cap)
            cap = adjusted_cap

    if total_chars > TOTAL_INPUT_CHAR_LIMIT and len(episode_list) > 20:
        sampled_episodes = episode_list[: SAMPLE_NEWEST] + episode_list[-SAMPLE_OLDEST:]
        sampled = True
        trimmed, total_chars = _trimmed_with_cap(sampled_episodes, cap)

    out = {
        "podcast_name": raw.get("podcast_name", ""),
        "podcast_intro": podcast_intro,
        "episode_list": trimmed,
    }
    stats = {
        "_input_episode_count": len(trimmed),
        "_input_total_chars": total_chars,
        "_input_sampled": sampled,
        "_input_per_episode_cap": cap,
    }
    if sampled:
        stats["_input_total_episode_count"] = original_count
    return out, stats


async def _call_diagnosis(
    *,
    input_payload: dict[str, Any],
    host_topic_references: list[dict[str, Any]] | None = None,
    premium: bool,
    temperature: float = DIAGNOSIS_TEMPERATURE,
) -> tuple[dict[str, Any], dict[str, Any], str]:
    """主诊断 LLM 调用。返回 (diagnosis_json, llm_stats, user_prompt)。"""
    from core.config import settings
    from core.services.llm_service import get_llm_service

    llm = get_llm_service()
    model = settings.LLM_PREMIUM_MODEL if premium else settings.LLM_MODEL
    user_prompt = PROMPTS["diagnosis"]["build_user"](
        podcast_name=input_payload["podcast_name"],
        podcast_intro=input_payload["podcast_intro"],
        episode_list=input_payload["episode_list"],
        host_topic_references=host_topic_references,
    )
    started = time.monotonic()
    diagnosis = await llm.generate_json(
        prompt=user_prompt,
        system_prompt=PROMPTS["diagnosis"]["system"],
        model=model,
        temperature=temperature,
        label="podcast_direction_diagnosis",
    )
    elapsed = round(time.monotonic() - started, 1)
    stats = {
        "_llm_diagnosis_model": model,
        "_llm_diagnosis_premium": premium,
        "_llm_diagnosis_temperature": temperature,
        "_llm_diagnosis_elapsed_seconds": elapsed,
    }
    return diagnosis, stats, user_prompt


async def _call_qc(
    *,
    input_payload: dict[str, Any],
    diagnosis: dict[str, Any],
    premium: bool,
    temperature: float = QC_TEMPERATURE,
) -> tuple[dict[str, Any], dict[str, Any], str]:
    """QC 自检 LLM 调用。返回 (ai_qc_json, llm_stats, user_prompt)。"""
    from core.config import settings
    from core.services.llm_service import get_llm_service

    llm = get_llm_service()
    model = settings.LLM_PREMIUM_MODEL if premium else settings.LLM_MODEL
    user_prompt = PROMPTS["qc"]["build_user"](
        input_payload=input_payload,
        diagnosis=diagnosis,
    )
    started = time.monotonic()
    ai_qc = await llm.generate_json(
        prompt=user_prompt,
        system_prompt=PROMPTS["qc"]["system"],
        model=model,
        temperature=temperature,
        label="podcast_direction_qc",
    )
    elapsed = round(time.monotonic() - started, 1)
    stats = {
        "_llm_qc_model": model,
        "_llm_qc_premium": premium,
        "_llm_qc_temperature": temperature,
        "_llm_qc_elapsed_seconds": elapsed,
    }
    return ai_qc, stats, user_prompt


def _build_metadata(
    *,
    run_id: str,
    trim_stats: dict[str, Any],
    diagnosis_stats: dict[str, Any],
    qc_stats: dict[str, Any] | None,
    qc_skipped: bool,
    note: str,
) -> dict[str, Any]:
    md: dict[str, Any] = {
        "tool_name": "EchoPress 整档播客内容方向诊断工具",
        "version": "v1.0",
        "input_type": "podcast_text_material",
        "_run_id": run_id,
        "_built_at": datetime.now().isoformat(timespec="seconds"),
        **diagnosis_stats,
        **trim_stats,
        "_qc_skipped": qc_skipped,
        "_warnings_count": 0,
        "_note": note or "",
    }
    if qc_stats:
        md.update(qc_stats)
    return md


def _archive_run(
    *,
    runs_dir: Path,
    run_id: str,
    trimmed_input: dict[str, Any],
    diagnosis_system: str,
    diagnosis_user: str,
    diagnosis_response: dict[str, Any],
    qc_system: str | None,
    qc_user: str | None,
    qc_response: dict[str, Any] | None,
    normalized: dict[str, Any],
    customer_markdown: str,
    run_log: dict[str, Any],
    max_runs: int,
) -> Path:
    """落归档目录 podcast_direction_runs/<run_id>/，并清理超出 max_runs 的旧目录。"""
    run_dir = runs_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    write_json(run_dir / "input.json", trimmed_input)
    (run_dir / "prompt_diagnosis.txt").write_text(
        f"### SYSTEM ###\n\n{diagnosis_system}\n\n### USER ###\n\n{diagnosis_user}\n",
        encoding="utf-8",
    )
    write_json(run_dir / "response_diagnosis.json", diagnosis_response)

    if qc_user is not None and qc_system is not None:
        (run_dir / "prompt_qc.txt").write_text(
            f"### SYSTEM ###\n\n{qc_system}\n\n### USER ###\n\n{qc_user}\n",
            encoding="utf-8",
        )
    if qc_response is not None:
        write_json(run_dir / "response_qc.json", qc_response)

    write_json(run_dir / "normalized.json", normalized)
    (run_dir / "customer_markdown.md").write_text(customer_markdown, encoding="utf-8")
    (run_dir / "run.log").write_text(
        json.dumps(run_log, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    _prune_runs(runs_dir, max_runs=max_runs)
    return run_dir


def _prune_runs(runs_dir: Path, *, max_runs: int) -> None:
    if max_runs <= 0:
        return
    if not runs_dir.exists():
        return
    children = sorted(
        [d for d in runs_dir.iterdir() if d.is_dir()],
        key=lambda p: p.name,
        reverse=True,
    )
    for old in children[max_runs:]:
        try:
            shutil.rmtree(old)
            logger.info("清理历史 run：%s", old.name)
        except Exception as e:
            logger.warning("清理历史 run 失败 %s：%s", old, e)


def _print_summary(payload: dict[str, Any]) -> None:
    overall = payload.get("overall_diagnosis") or {}
    direction = payload.get("direction_structure") or {}
    titles = (payload.get("title_suggestions") or {}).get("professional_titles") or []
    quality = payload.get("quality_check") or {}
    metadata = payload.get("metadata") or {}
    contact = payload.get("contact_info") or {}

    podcast_name = overall.get("podcast_name") or "(未知节目)"
    ep_count = metadata.get("_input_episode_count", "?")
    structure_type = overall.get("content_structure_type") or "?"
    main_directions = overall.get("main_directions") or []
    priority = direction.get("priority_direction") or "?"
    first_title = titles[0].get("title") if titles else "?"

    confidence = quality.get("confidence_level", "?")
    rule_errors = quality.get("rule_errors_count", 0)
    rule_warns = quality.get("rule_warnings_count", 0)
    ai_warns = quality.get("ai_warnings_count", 0)

    diag_model = metadata.get("_llm_diagnosis_model", "?")
    diag_elapsed = metadata.get("_llm_diagnosis_elapsed_seconds", "?")
    qc_skipped = metadata.get("_qc_skipped", False)
    qc_elapsed = metadata.get("_llm_qc_elapsed_seconds")
    run_id = metadata.get("_run_id", "?")

    primary_host = contact.get("primary_host") or "(空)"
    sub_count = contact.get("subscription_count")
    sub_count_label = f"{sub_count:,}" if isinstance(sub_count, int) else "(空)"
    wechat_id = contact.get("wechat_personal_id") or "(未识别)"
    wechat_oa = contact.get("wechat_official_account") or "(空)"

    bar = "=" * 60
    lines = [
        bar,
        f"EchoPress 整档诊断 · 节目「{podcast_name}」({ep_count} 期)",
        bar,
        f"内容结构类型：{structure_type}",
        f"主要方向：{' / '.join(main_directions) if main_directions else '?'}",
        f"推荐优先整理：{priority}",
        f"建议标题（克制专业型 #1）：{first_title}",
        "",
        f"主播：{primary_host} · 订阅数：{sub_count_label} · 微信号：{wechat_id} · 公众号：{wechat_oa}",
        (
            f"置信度：{confidence} · 规则：{rule_errors} errors / {rule_warns} warns · "
            f"AI 自检：{'skipped' if qc_skipped else f'{ai_warns} warns'}"
        ),
        (
            f"主诊断：{diag_model} · {diag_elapsed}s"
            + (f" · QC：{qc_elapsed}s" if qc_elapsed is not None else "")
            + f" · run_id {run_id}"
        ),
        bar,
    ]
    print("\n".join(lines))

    if quality.get("manual_review_needed"):
        logger.warning(
            "⚠ 建议人工复核：见 quality_check.warnings (%d 条)",
            len(quality.get("warnings") or []),
        )


def _failure_payload(
    *, run_id: str, stage: str, exc: BaseException
) -> dict[str, Any]:
    return {
        "tool": "podcast_direction",
        "run_id": run_id,
        "stage": stage,
        "error_type": type(exc).__name__,
        "error_message": str(exc),
        "_failed_at": datetime.now().isoformat(timespec="seconds"),
    }


async def main_async(args: argparse.Namespace) -> int:
    run_id = datetime.now().strftime("%Y%m%d-%H%M%S")
    note = (args.note or "").strip()
    max_runs = 0 if args.no_runs_archive else max(int(args.max_runs), 0)

    if args.job:
        paths = JobPaths.open(args.job)
        out_path = paths.root / "direction_diagnosis.json"
        md_path = paths.root / "direction_diagnosis.md"
        runs_dir = paths.root / "podcast_direction_runs"
        error_path = paths.root / "podcast_direction_error.json"
        logger.info("作业目录：%s", paths.root)
    else:
        input_path = Path(args.input).expanduser().resolve()
        if not input_path.exists():
            raise FileNotFoundError(f"--input 文件不存在：{input_path}")
        if args.out:
            out_path = Path(args.out).expanduser().resolve()
        else:
            out_path = input_path.with_name("direction_diagnosis.json")
        md_path = out_path.with_suffix(".md")
        runs_dir = out_path.parent / ".podcast_direction_runs"
        error_path = out_path.with_name("podcast_direction_error.json")
        logger.info("输入文件：%s", input_path)

    stage = "aggregate"
    diagnosis_user_prompt = ""
    qc_user_prompt: str | None = None
    diagnosis_response: dict[str, Any] = {}
    qc_response: dict[str, Any] | None = None

    try:
        if args.job:
            raw, contact_raw = _aggregate_from_job(JobPaths.open(args.job))
        else:
            raw, contact_raw = _aggregate_from_file(
                Path(args.input).expanduser().resolve()
            )

        stage = "extract_contact"
        contact_info = extract_contact_info(
            podcast_name=contact_raw.get("podcast_name", ""),
            podcaster_names=contact_raw.get("podcaster_names"),
            contacts=contact_raw.get("contacts"),
            description=contact_raw.get("description", ""),
            subscription_count=contact_raw.get("subscription_count"),
        )
        logger.info(
            "联系方式：主播=%s · 订阅数=%s · 微信号=%s · 公众号=%s",
            contact_info.get("primary_host") or "(空)",
            contact_info.get("subscription_count"),
            contact_info.get("wechat_personal_id") or "(未识别)",
            contact_info.get("wechat_official_account") or "(空)",
        )
        for w in contact_info.get("extraction_warnings") or []:
            logger.info("  contact: %s", w)

        stage = "extract_topics"
        host_topic_references = extract_host_topic_references(raw.get("episode_list") or [])
        logger.info("主播专题：识别到 %d 个显式专题链接分组", len(host_topic_references))
        for topic in host_topic_references[:8]:
            logger.info(
                "  topic: %s · %s 集",
                topic.get("topic_title") or topic.get("topic_id"),
                topic.get("episode_count"),
            )

        stage = "trim"
        trimmed, trim_stats = _trim_input(raw)
        if not trimmed["episode_list"]:
            raise ValueError("聚合后的 episode_list 为空，无法做整档诊断")
        logger.info(
            "输入聚合：节目「%s」 · %d 期 · 总输入 %d 字 · 单期上限 %d 字 · 采样=%s",
            trimmed["podcast_name"],
            trim_stats["_input_episode_count"],
            trim_stats["_input_total_chars"],
            trim_stats["_input_per_episode_cap"],
            trim_stats["_input_sampled"],
        )

        stage = "llm_diagnosis"
        logger.info(
            "[1/2] 主诊断 LLM 调用 (premium=%s, temperature=%s)...",
            args.premium, DIAGNOSIS_TEMPERATURE,
        )
        diagnosis, diag_stats, diagnosis_user_prompt = await _call_diagnosis(
            input_payload=trimmed,
            host_topic_references=host_topic_references,
            premium=args.premium,
        )
        diagnosis_response = diagnosis if isinstance(diagnosis, dict) else {}
        if not isinstance(diagnosis, dict):
            raise ValueError(
                f"主诊断 LLM 返回不是 JSON 对象，实际是 {type(diagnosis).__name__}"
            )
        logger.info(
            "[1/2] ✅ 主诊断完成 · %ss · 结构=%s",
            diag_stats["_llm_diagnosis_elapsed_seconds"],
            (diagnosis.get("overall_diagnosis") or {}).get("content_structure_type", "?"),
        )

        ai_qc: dict[str, Any] | None = None
        qc_stats: dict[str, Any] | None = None
        qc_skipped = bool(args.no_qc) or bool(args.no_validate)

        if not qc_skipped:
            stage = "llm_qc"
            logger.info(
                "[2/2] QC 自检 LLM 调用 (premium=%s, temperature=%s)...",
                args.qc_premium, QC_TEMPERATURE,
            )
            ai_qc, qc_stats, qc_user_prompt = await _call_qc(
                input_payload=trimmed,
                diagnosis=diagnosis,
                premium=args.qc_premium,
            )
            qc_response = ai_qc if isinstance(ai_qc, dict) else {}
            if not isinstance(ai_qc, dict):
                raise ValueError(
                    f"QC 自检 LLM 返回不是 JSON 对象，实际是 {type(ai_qc).__name__}"
                )
            logger.info(
                "[2/2] ✅ QC 完成 · %ss · ai_warnings=%d · ai_confidence=%s",
                qc_stats["_llm_qc_elapsed_seconds"],
                len(ai_qc.get("ai_warnings") or []),
                ai_qc.get("ai_confidence", "?"),
            )
        else:
            logger.info("[2/2] 跳过 QC 自检（--no-qc 或 --no-validate）")

        stage = "rule_check"
        if args.no_validate:
            rule_errors: list[str] = []
            rule_warnings: list[str] = []
            logger.warning("--no-validate 已开启，跳过硬规则校验")
        else:
            rule_errors, rule_warnings = run_rule_checks(diagnosis)
            logger.info(
                "硬规则：%d errors / %d warnings",
                len(rule_errors), len(rule_warnings),
            )
            for err in rule_errors:
                logger.error("  · %s", err)
            for warn in rule_warnings:
                logger.warning("  · %s", warn)

        stage = "merge_qc"
        final_qc = merge_qc(rule_errors, rule_warnings, ai_qc)

        stage = "compose_final"
        final_json = dict(diagnosis)
        final_json["metadata"] = _build_metadata(
            run_id=run_id,
            trim_stats=trim_stats,
            diagnosis_stats=diag_stats,
            qc_stats=qc_stats,
            qc_skipped=qc_skipped,
            note=note,
        )
        final_json["quality_check"] = final_qc
        final_json["contact_info"] = contact_info
        final_json["host_topic_references"] = host_topic_references
        final_json["metadata"]["_warnings_count"] = len(final_qc.get("warnings") or [])
        final_json["metadata"]["_contact_warnings_count"] = len(
            contact_info.get("extraction_warnings") or []
        )
        final_json["metadata"]["_host_topic_count"] = len(host_topic_references)
        overall = final_json.setdefault("overall_diagnosis", {})
        if not overall.get("podcast_name"):
            overall["podcast_name"] = trimmed["podcast_name"]

        stage = "render_md"
        customer_md = render_customer_markdown(final_json)

        stage = "archive"
        write_json(out_path, final_json)
        md_path.write_text(customer_md, encoding="utf-8")
        logger.info("✅ 落盘：%s", out_path)
        logger.info("✅ 落盘：%s", md_path)

        if max_runs > 0:
            run_log = {
                **diag_stats,
                **(qc_stats or {}),
                "qc_skipped": qc_skipped,
                "rule_errors_count": len(rule_errors),
                "rule_warnings_count": len(rule_warnings),
                "ai_warnings_count": len((ai_qc or {}).get("ai_warnings") or []),
                "confidence_level": final_qc.get("confidence_level"),
                "manual_review_needed": final_qc.get("manual_review_needed"),
                "note": note,
                "run_id": run_id,
                "_built_at": final_json["metadata"]["_built_at"],
            }
            _archive_run(
                runs_dir=runs_dir,
                run_id=run_id,
                trimmed_input=trimmed,
                diagnosis_system=PROMPTS["diagnosis"]["system"],
                diagnosis_user=diagnosis_user_prompt,
                diagnosis_response=diagnosis_response,
                qc_system=PROMPTS["qc"]["system"] if not qc_skipped else None,
                qc_user=qc_user_prompt,
                qc_response=qc_response,
                normalized=final_json,
                customer_markdown=customer_md,
                run_log=run_log,
                max_runs=max_runs,
            )
            logger.info("📁 归档：%s/%s/", runs_dir, run_id)

        _print_summary(final_json)
        return 0

    except Exception as e:
        logger.error("❌ 失败 stage=%s：%s", stage, e)
        traceback.print_exc()
        try:
            write_json(error_path, _failure_payload(run_id=run_id, stage=stage, exc=e))
            logger.info("已写入错误日志：%s", error_path)
        except Exception as e2:
            logger.error("写入 error.json 也失败：%s", e2)
        return 1


def main() -> None:
    p = argparse.ArgumentParser(
        description="EchoPress 整档播客内容方向诊断工具（主诊断 LLM + QC 自检 LLM + 规则降噪）",
    )
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--job", help="复用 fetch_show 已抓的 job 产出")
    src.add_argument("--input", help="外部 JSON 文件路径")

    p.add_argument(
        "--out", default="",
        help="--input 模式下的输出 JSON 路径（默认与输入文件同目录；同名 .md 同时写出）",
    )
    p.add_argument(
        "--premium", action="store_true",
        help="主诊断使用 settings.LLM_PREMIUM_MODEL（默认普通模型）",
    )
    p.add_argument(
        "--qc-premium", action="store_true",
        help="QC 自检使用 settings.LLM_PREMIUM_MODEL（默认普通模型）",
    )
    p.add_argument(
        "--no-qc", action="store_true",
        help="跳过 QC 自检 LLM 调用，仅靠硬规则降噪",
    )
    p.add_argument(
        "--note", default="",
        help="本次跑的目的，写进 metadata._note 便于复盘对比",
    )
    p.add_argument(
        "--max-runs", type=int, default=5,
        help="历史 runs 保留多少次（默认 5；0=不归档）",
    )
    p.add_argument(
        "--no-runs-archive", action="store_true",
        help="不写 runs 子目录（等价于 --max-runs 0）",
    )
    p.add_argument(
        "--no-validate", action="store_true",
        help="跳过硬规则 + QC（仅调试用）",
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
