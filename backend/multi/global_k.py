"""
B 端"全局取 K"内核（annotations / highlights / illustrations 共用）

核心思路：
- 把整章 N 个小节的正文一次性喂给 LLM
- LLM 直接产出最多 K 个对象（带 section_index 定位）
- prompt 里写硬约束："K 个对象必须落在 K 个不同的小节"
- LLM 端自己做"小节多样性优化"，避免代码事后矫正造成分布不均

token 兜底：
- 估算 input tokens（中文 1 字 ≈ 1.5 token）
- 阈值 80K（DeepSeek 128K 上限的 60%，留出 prompt 模板和输出空间）
- 超阈值时分两批选 2K，再 LLM rerank 到 K（同样保留分散约束）

实测：ep11 全章 1.2 万字 ≈ 1.8 万 tokens，离阈值 4 倍距离，正常播客根本走不到
fallback 分支。
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger("global_k")


# 中文 1 字 ≈ 1.5 token（保守上界）
TOKEN_PER_CHAR = 1.5
# 单次 LLM 输入 token 阈值（超过则启动分批 fallback）
MAX_INPUT_TOKENS = 80_000


def content_char_count(sections: list[dict]) -> int:
    """Return total body characters across all sections."""
    total = 0
    for s in sections:
        content = s.get("content") or ""
        if isinstance(content, list):
            content = "\n".join(str(x) for x in content)
        total += len(str(content))
    return total


def dynamic_workbench_budget(
    sections: list[dict],
    *,
    kind: str,
    cap_override: int | None = None,
) -> dict[str, int]:
    """
    Compute a whole-episode budget for scarce book enrichments.

    The workbench treats one episode as one book chapter. Most chapters should
    sit around 2 items; only unusually long, dense chapters can approach 5.
    """
    chars = content_char_count(sections)
    if chars <= 6_000:
        target = 1
    elif chars <= 14_000:
        target = 2
    elif chars <= 22_000:
        target = 3
    elif chars <= 32_000:
        target = 4
    else:
        target = 5

    hard_max = 5
    if cap_override is not None and cap_override > 0:
        hard_max = cap_override
    target = min(target, hard_max)

    if kind == "annotation":
        return {
            "min": 0,
            "target": target,
            "max": hard_max,
            "chars": chars,
        }
    if kind == "illustration":
        return {
            "min": 1,
            "target": max(1, target),
            "max": hard_max,
            "chars": chars,
        }
    if kind == "highlight":
        highlight_max = min(3, hard_max)
        if chars <= 6_000:
            highlight_target = 1
        elif chars <= 18_000:
            highlight_target = 2
        else:
            highlight_target = 3
        return {
            "min": 0,
            "target": min(highlight_target, highlight_max),
            "max": highlight_max,
            "chars": chars,
        }
    raise ValueError(f"unknown budget kind: {kind}")


def _estimate_tokens(text: str) -> int:
    """粗估 token 数。中文为主时 1 字 ≈ 1.5 token，留 buffer。"""
    return int(len(text) * TOKEN_PER_CHAR)


def _build_full_text(sections: list[dict]) -> str:
    """
    把 N 个小节拼成"分节标记"的全文，方便 LLM 用 section_index 定位。

    输出格式：
        ### 第 1 节 · 标题A
        正文…

        ### 第 2 节 · 标题B
        正文…
    """
    lines: list[str] = []
    for i, s in enumerate(sections, start=1):
        title = (s.get("title") or "").strip()
        content = s.get("content") or ""
        if isinstance(content, list):
            content = "\n".join(str(x) for x in content)
        lines.append(f"### 第 {i} 节 · {title}\n\n{content}")
    return "\n\n".join(lines)


def _normalize_items(items: list[dict], sections: list[dict]) -> list[dict]:
    """
    标准化 LLM 输出 items（select_global_k 返回前必跑）。

    背景：`_build_full_text` 把每节标题渲染成 `### 第 N 节 · 标题` 喂给 LLM。
    部分模型（如 Gemini-3-flash）会把这整段前缀照抄到响应的 `chapter_title` 字段，
    导致下游按 section_title 精确匹配时全部 miss，金句/插图全部漏渲染。

    修复：信任 `section_index`（SSOT），把 `chapter_title` 强制覆盖成 sections[i-1].title。
    同时过滤非法 section_index。
    """
    out: list[dict] = []
    n = len(sections)
    for it in items:
        if not isinstance(it, dict):
            continue
        sec = it.get("section_index")
        if not isinstance(sec, int) or sec < 1 or sec > n:
            logger.warning("[global_k] 丢弃非法 section_index=%r 的 item", sec)
            continue
        clean_title = (sections[sec - 1].get("title") or "").strip()
        out.append({**it, "section_index": sec, "chapter_title": clean_title})
    return out


def _build_full_text_numbered_paragraphs(sections: list[dict]) -> str:
    """
    把 N 个小节拼成"分节 + 段内编号"的全文，给金句/插图定位用。

    输出格式：
        ### 第 1 节 · 标题A
        [1] 第一段…
        [2] 第二段…
        …

        ### 第 2 节 · 标题B
        [1] …
    """
    lines: list[str] = []
    for i, s in enumerate(sections, start=1):
        title = (s.get("title") or "").strip()
        content = s.get("content") or ""
        if isinstance(content, list):
            content = "\n".join(str(x) for x in content)
        # 段落分隔：双换行；过滤空段
        paras = [p.strip() for p in str(content).split("\n\n") if p.strip()]
        numbered = "\n\n".join(f"[{j+1}] {p}" for j, p in enumerate(paras))
        lines.append(f"### 第 {i} 节 · {title}\n\n{numbered}")
    return "\n\n".join(lines)


async def select_global_k(
    *,
    llm,
    sections: list[dict],
    k: int,
    system_prompt: str,
    user_prompt_template: str,
    full_text_builder=_build_full_text,
    extra_format_kwargs: dict[str, Any] | None = None,
    response_key: str,
    label: str,
    timeout: int = 120,
    max_tokens: int = 32768,
) -> list[dict]:
    """
    通用"全局取 K"调用器。

    流程：
      1. 估 token；< 阈值 → 一次性喂全文调 LLM
      2. ≥ 阈值 → 分两批选 2K，再 LLM rerank 到 K（保留分散约束）

    返回 LLM 输出 JSON 中 `response_key` 字段（list[dict]），按 LLM 给出的顺序。
    每项至少含 section_index 字段。
    """
    full_text = full_text_builder(sections)
    est_tokens = _estimate_tokens(full_text)
    extra_format_kwargs = extra_format_kwargs or {}

    if est_tokens < MAX_INPUT_TOKENS:
        logger.info(
            "[global_k] %s 单次调用 · 全章 %d 节 · 约 %d tokens · K=%d",
            label, len(sections), est_tokens, k,
        )
        items = await _call_once(
            llm=llm,
            full_text=full_text,
            n_sections=len(sections),
            k=k,
            system_prompt=system_prompt,
            user_prompt_template=user_prompt_template,
            extra_format_kwargs=extra_format_kwargs,
            response_key=response_key,
            label=label,
            timeout=timeout,
            max_tokens=max_tokens,
        )
        return _normalize_items(items, sections)

    logger.warning(
        "[global_k] %s 全章约 %d tokens 超阈值 %d，启动分批 fallback",
        label, est_tokens, MAX_INPUT_TOKENS,
    )
    items = await _call_batched_then_rerank(
        llm=llm,
        sections=sections,
        k=k,
        system_prompt=system_prompt,
        user_prompt_template=user_prompt_template,
        full_text_builder=full_text_builder,
        extra_format_kwargs=extra_format_kwargs,
        response_key=response_key,
        label=label,
        timeout=timeout,
        max_tokens=max_tokens,
    )
    return _normalize_items(items, sections)


async def _call_once(
    *,
    llm,
    full_text: str,
    n_sections: int,
    k: int,
    system_prompt: str,
    user_prompt_template: str,
    extra_format_kwargs: dict[str, Any],
    response_key: str,
    label: str,
    timeout: int,
    max_tokens: int,
) -> list[dict]:
    """一次性喂全章给 LLM，让它直接产出 K 个分散的对象。"""
    user_prompt = user_prompt_template.format(
        full_text=full_text,
        n_sections=n_sections,
        k=k,
        **extra_format_kwargs,
    )
    try:
        result = await llm.generate_json(
            prompt=user_prompt,
            system_prompt=system_prompt,
            max_tokens=max_tokens,
            thinking_budget=0,
            timeout=timeout,
            label=label,
        )
    except Exception as e:
        logger.error("[global_k] %s LLM 调用失败：%s", label, e)
        return []

    items = result.get(response_key) or []
    if not isinstance(items, list):
        logger.warning("[global_k] %s 响应字段 %s 不是 list：%r", label, response_key, items)
        return []
    return items


async def _call_batched_then_rerank(
    *,
    llm,
    sections: list[dict],
    k: int,
    system_prompt: str,
    user_prompt_template: str,
    full_text_builder,
    extra_format_kwargs: dict[str, Any],
    response_key: str,
    label: str,
    timeout: int,
    max_tokens: int,
) -> list[dict]:
    """
    Fallback：分两批各选 2K，再 LLM rerank 到 K。

    - 第 1 批：前 N/2 节
    - 第 2 批：后 N/2 节（section_index 仍按原始全章下标）
    - 候选合并后再让 LLM 二次裁剪到 K（继续要求分散）
    """
    half = max(1, len(sections) // 2)
    sections_a = sections[:half]
    sections_b = sections[half:]

    # 让 batch_b 的 section_index 偏移 half，使全章下标一致
    items_a = await _call_once(
        llm=llm,
        full_text=full_text_builder(sections_a),
        n_sections=len(sections_a),
        k=2 * k,
        system_prompt=system_prompt,
        user_prompt_template=user_prompt_template,
        extra_format_kwargs=extra_format_kwargs,
        response_key=response_key,
        label=f"{label}/batch_a",
        timeout=timeout,
        max_tokens=max_tokens,
    )

    items_b = await _call_once(
        llm=llm,
        full_text=full_text_builder(sections_b),
        n_sections=len(sections_b),
        k=2 * k,
        system_prompt=system_prompt,
        user_prompt_template=user_prompt_template,
        extra_format_kwargs=extra_format_kwargs,
        response_key=response_key,
        label=f"{label}/batch_b",
        timeout=timeout,
        max_tokens=max_tokens,
    )
    # 偏移 batch_b 的 section_index 到全章坐标
    for it in items_b:
        if "section_index" in it and isinstance(it["section_index"], int):
            it["section_index"] = it["section_index"] + half

    candidates = list(items_a) + list(items_b)
    if len(candidates) <= k:
        return candidates

    # 简单回退：按 section 多样性 + 出现顺序裁剪到 K
    # （LLM rerank 在小数据量上 ROI 不高，直接代码裁剪）
    seen_sections: set[int] = set()
    kept: list[dict] = []
    for it in candidates:
        sec = it.get("section_index")
        if not isinstance(sec, int):
            continue
        if sec in seen_sections:
            continue
        seen_sections.add(sec)
        kept.append(it)
        if len(kept) >= k:
            break
    return kept
