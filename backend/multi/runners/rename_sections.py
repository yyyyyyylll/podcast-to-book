"""
rename_sections — 一次性给整章的全部小节统一重命名（B 端定制）

为什么需要这个 runner？
   compose Phase 1 的 plan_user 在 C 端是为「独立成书的章节」设计的，
   到 B 端「一期=一章」之后，原本的 chapter title 退化成了 section title。
   它没有「章内多样化、节奏感、跟章名拉开层级」的硬约束，于是经常出现
   6 个小节里 4 个用同一个主词打头（如全是「内阻力 X」），章内节奏垮掉。

这个 runner 把「整章的全部 section_title」一次性交给 LLM 重命名：
- 一次 LLM 调用，全局视角
- 强制章内词汇多样化（同名词最多 2 次）
- 强制章内节奏（现象→机制→陷阱→破局→进阶→收束 这种叙事弧）
- 跟章名拉开层级（不重复章名核心词）

用法：

    cd backend
    # 默认 2 种风格（parallel + narrative）各出一组候选
    python -m workbench.runners.rename_sections --job possibility --only 11

    # 只跑某种风格
    python -m workbench.runners.rename_sections --job possibility --only 11 --style parallel
    python -m workbench.runners.rename_sections --job possibility --only 11 --style narrative

    # 选定风格后写回 chapter_for_book.json（覆盖各 section.section_title）
    python -m workbench.runners.rename_sections --job possibility --only 11 --style parallel --apply

输入：episodes/epNN/chapter_for_book.json
输出：episodes/epNN/section_titles_candidates.json
        若加 --apply，则同时把所选风格的全部小节标题写回 chapter_for_book.json
"""
from __future__ import annotations

from multi.env import setup
setup()

import argparse
import asyncio
import json
import logging
import sys
import time
from datetime import datetime
from typing import Any

from multi.io import JobPaths, read_json, write_json
from multi.podcast_timeline import (
    TimelineEntry,
    format_timeline_for_prompt,
    parse_timeline,
    subset_by_range,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("rename_sections")


STYLE_SPECS = {
    "parallel": {
        "label": "并列短语派",
        "guidance": (
            "**风格定位：并列短语派**\n"
            "- 全章小节标题保持**同一种语法形态**（要么全是名词短语，要么全是动宾短语，"
            "要么全是「X 的 Y」结构），整章内部互相对仗、像一组并列分镜\n"
            "- 长度严格控制在 **4-10 字**，且 6 个标题彼此长度差 ≤ 4 字\n"
            "- 节奏短促有力，像目录页的并列条目\n"
            "- 想象优秀非虚构合集（如《人类简史》《被讨厌的勇气》《思考，快与慢》）"
            "里的小节命名方式：克制、对仗、信息密度高"
        ),
    },
    "narrative": {
        "label": "叙事弧派",
        "guidance": (
            "**风格定位：叙事弧派**\n"
            "- 6 个小节标题**连起来读就是一个故事线**——从「问题登场」走到「机制揭示」"
            "再到「陷阱与误区」再到「破局之道」再到「进阶心法」最后「落地行动」\n"
            "- 每个标题不必用同一种语法形态，但要随章节内容**有内在的递进感**\n"
            "- 长度可在 **4-12 字** 之间略微浮动，承载叙事节奏\n"
            "- 想象一本好的非虚构在做章内分节时的笔法：每个小节既是落点，也是下一节的引子"
        ),
    },
    "concrete": {
        "label": "内容感优先派",
        "guidance": (
            "**风格定位：内容感优先派 / 反 AI 模板腔**\n"
            "- 想象这是一本由真正的作者 / 编辑写出的书，每个小节标题都被一个挑剔的编辑\n"
            "  审过：标题不准确、不具体、信息密度低，立刻打回去重写\n"
            "\n"
            "## 优先级（按重要性从高到低）\n"
            "\n"
            "### A. 准确（最高优先级）\n"
            "- 每个标题里的所有概念、人物、比喻、数字、意象，**必须能在该节正文里找到原文依据**。\n"
            "  反例（视为「瞎想」，一律重拟）：\n"
            "    · 「合理化大师的**临终狙击**」——「临终」并未在正文出现，瞎想\n"
            "    · 「在汗水中召唤**缪斯**降临」——「缪斯/希腊神话」与正文无关\n"
            "    · 「建立客观的**上帝视角**」——「上帝视角」过载\n"
            "    · 「**三本书**交汇的行动终点」——只有一本书《一生之敌》\n"
            "    · 「**普莱斯菲尔德的博弈**」——读者不认识这个外文名，且正文也没说「博弈」\n"
            "- **核心概念词必须高频出现**：本书的灵魂概念（如本章是「内阻力」），\n"
            "  全章 N 个标题里**应出现 2-3 次**（不打头，作为修饰/宾语/比喻自然嵌入即可）。\n"
            "  完全回避核心概念会让目录失去主线——这是大忌。\n"
            "\n"
            "### B. 具体（次优先）\n"
            "- 每个标题必须包含至少一项「该节独有的具体信号」：\n"
            "    · 节内出现的关键概念名（如「内阻力」「职业选手」「开机仪式」「领域导向」「心流」）；\n"
            "    · 节内出现的具体数字（如「六大特征」「27 年」「11 个州」）；\n"
            "    · 节内出现的关键比喻或意象（如「肉搏」「拳击手」「打字机」）；\n"
            "    · 节内出现的人物 / 书名引用（如「《一生之敌》」「Steven 的 27 年」）。\n"
            "  禁止抽象到只剩「认识 X」「X 的特征」「总结与行动」这种空气标题\n"
            "\n"
            "### C. 句式有变化（最低优先级，仅在 A/B 都满足后追求）\n"
            "- 标题之间应有句式差异，**避免全章统一「X 的 Y」或「X：Y」模板**。\n"
            "- 但**不要为了句式多样化去硬凑**——如果一组标题用相似句式但都准确具体，\n"
            "  那比「8 种不同句式但有 3 条瞎想」要好得多。\n"
            "\n"
            "## 铁律式禁忌（一旦命中，重拟）\n"
            "- 「认识 X」「X 的特征」「X 的陷阱」「X 与 Y」「总结」「行动」「思考」\n"
            "  这些 PPT 式空泛词\n"
            "- 「X 上瘾」「X 觉醒」「X 内核」「X 主义」「X 之道」「X 心法」这种被自媒体用滥的拼词\n"
            "- 命令式「击退 X」「征服 X」「打破 X」开头\n"
            "- 「从 A 到 B」结构（往往套路）；「A 与 B」单纯并列（信息密度低）\n"
            "- 直接把节目名「可能性褶皱」嵌进小节标题（这是节目名不是章节内容）\n"
            "- **任何与正文无依据的文学化意象**（临终/缪斯/上帝/三本书/博弈/狙击 等），\n"
            "  即便看起来「高级」，没有正文依据 = 不合格\n"
            "\n"
            "## 长度硬约束\n"
            "- **6-12 字（含中文标点）**，目标中位 8-10 字。任何超过 12 字的标题必须重写。\n"
            "- 用户明确反馈：标题应像「书的目录」——克制、紧凑，而非「公众号大标题」。\n"
            "- 副作用：超 12 字的句子常滑向「主词：长副词」模板，AI 味立刻冒头。\n"
            "\n"
            "## 优秀范本\n"
            "- 得到精读 / 三联书评的小节切分；《纽约时报》非虚构图书摘要的小节命名风格——\n"
            "  具体、有抓手、不讲套话；该用的核心概念词大方用，不为了「文采」绕弯。"
        ),
    },
}


def _truncate(s: str, n: int) -> str:
    s = (s or "").strip()
    return s if len(s) <= n else s[:n] + "…"


def _build_prompt(
    *,
    chapter: dict[str, Any],
    style_key: str,
    n: int = 1,
    timeline: list[TimelineEntry] | None = None,
) -> str:
    spec = STYLE_SPECS[style_key]

    chapter_title = chapter.get("chapter_title", "")
    raw_title = chapter.get("chapter_title_raw", "")
    core_theme = chapter.get("core_theme", "")
    theme_keywords = "、".join(chapter.get("theme_keywords") or [])
    podcast_name = chapter.get("podcast_name", "")

    section_lines: list[str] = []
    for s in chapter.get("sections", []):
        idx = s.get("section_index")
        old_title = s.get("section_title", "")
        content = s.get("content", "")
        gist = _truncate(content.replace("\n", " "), 280)
        key_points = s.get("key_points") or []
        kp_text = "；".join(key_points) if key_points else "（无）"
        # 把作者本人在该节时间窗内标的子标题作为"局部命名参考池"喂给 LLM。
        # 这是作者本意,优先级高于 LLM 原创——但只能借鉴字眼/抓手,不能照抄。
        local_tl_block = ""
        if timeline:
            tr = s.get("time_range") or [None, None]
            start_sec, end_sec = (tr + [None, None])[:2]
            local = subset_by_range(timeline, start_sec=start_sec, end_sec=end_sec)
            if local:
                local_tl_block = (
                    "\n- 作者本人在该节时间窗里写的子标题（"
                    f"共 {len(local)} 条，仅作命名素材，**禁止照抄**）：\n"
                    + format_timeline_for_prompt(local, indent="    ")
                )
        section_lines.append(
            f"## 第 {idx} 节（当前标题：{old_title}）\n"
            f"- 关键要点：{kp_text}\n"
            f"- 正文开头节选：{gist}"
            f"{local_tl_block}"
        )
    sections_block = "\n\n".join(section_lines)
    n_sections = len(chapter.get("sections", []))

    # 章级总览：作者本人写的全期时间轴（粒度比当前小节更细，仅作整章命名参考）
    chapter_timeline_block = ""
    if timeline:
        chapter_timeline_block = (
            "\n## 作者本人写的本期时间轴（{n} 条，仅供命名时借鉴具体字眼/抓手）\n\n"
            "> ⚠️ 这是作者在 shownotes 里给出的**子章节标记**，粒度比当前 {nsec} 个小节**更细**。\n"
            "> 它的作用**只是让你看到作者本人在描述这部分内容时用的具体词汇**——\n"
            "> 你可以借鉴其中具体的概念名、人物、数字、比喻，但：\n"
            "> 1. **不要照抄整句**作为小节标题（那些标题往往太长、太口语）；\n"
            "> 2. **不要因此重新切分小节**——当前 {nsec} 节的边界已经定了，不要试图改变；\n"
            "> 3. **不要直接把“内阻力特点 X”这种序号式标题搬过来**——本书的小节标题不带序号；\n"
            "> 4. 若该节落了多条作者子标题（比如七大特点都在同一节），请抽象出**一个统摄性命名**，\n"
            ">    再借用作者用过的字眼让它接地气。\n\n"
            "{lines}\n"
        ).format(
            n=len(timeline),
            nsec=n_sections,
            lines=format_timeline_for_prompt(timeline),
        )

    return f"""# 任务

你正在帮一本读书 / 观点合集的整体编辑给**单章内部的全部 {n_sections} 个小节**重新命名。

这本书的整体形态是：每一章对应一期播客谈话，章内有 {n_sections} 个小节用小标题分隔。当前的小节标题在
词汇和节奏上有问题（同一个核心词打头出现太多次、缺少章内节奏），需要统一重写。

# 这一章的素材

- **节目**：{podcast_name}
- **节目原标题**（这一期的源标题，含被讨论的书 / 关键概念，可作为命名素材）：{raw_title}
- **当前章名**：{chapter_title}
- **核心主题**：{core_theme}
- **关键词**：{theme_keywords}
{chapter_timeline_block}
## 当前 {n_sections} 个小节（旧标题 + 关键要点 + 正文开头 + 作者本意子标题）

{sections_block}

# 风格要求

{spec['guidance']}

# 不可触碰的硬约束

1. **核心概念名要在整章标题里出现 2-3 次**（如本章的「内阻力」），不要刻意回避——
   这是该书的灵魂，完全不出现会让目录失去主线。
   **但「同一个核心词打头」最多 2 次**（避免 4 个标题都用「内阻力 X」打头，章内节奏垮）。
2. **跟章名拉开层级**：小节标题不允许直接重复整个章名。可以呼应章名的关键词，但要换角度切入。
3. **每个标题里的所有意象/概念/数字必须能在该节正文里找到原文依据**——
   不能为了"显得高级"瞎想（临终、缪斯、上帝视角、三本书…），无依据 = 不合格。
4. **每个小节标题准确反映该节正文重点**——不是空泛的概括，必须能让读者从标题预判该节内容。
5. **不允许使用**：数字编号、英文、特殊符号、问号、感叹号、冒号副标题。
6. **不允许使用**：「总结与行动」「总结」「行动指南」「思考」「反思」这种 PPT 式空泛收束词。
   即便是最后一节，也要起一个有内容感的具体标题。
7. **字数硬约束**：每个标题 **6-12 字**（含中文标点）。超过 12 字一律视为不合格，必须重拟。
8. **顺序保持**：返回的 {n_sections} 个新标题，必须与原 {n_sections} 个小节**按顺序一一对应**。

# 候选组数

请一次性给出 **{n}** 套**思路明显不同**的小节标题方案——
- 每套都是一组 {n_sections} 个小节标题，对应同一章的同 {n_sections} 个小节
- 不同套之间不能只是个别字词的同义改写，要在「整体笔法 / 主导命名思路 / 引用对象」上做出明显差异
- 在风格定位允许的范围内尽量发散

# 输出格式

严格 JSON：

```json
{{
  "variants": [
    {{
      "variant_summary": "30 字内说明这一套标题的整体思路",
      "sections": [
        {{
          "section_index": 1,
          "old_title": "原标题",
          "new_title": "新标题",
          "rationale": "20 字内说明这个标题"
        }}
        // ... 共 {n_sections} 条 ...
      ]
    }}
    // ... 共 {n} 套 ...
  ]
}}
```"""


async def _call_llm(prompt: str, *, premium: bool, temperature: float = 0.5) -> dict[str, Any]:
    from core.services.llm_service import get_llm_service
    from core.config import settings
    llm = get_llm_service()
    model = settings.LLM_PREMIUM_MODEL if premium else settings.LLM_MODEL
    return await llm.generate_json(
        prompt=prompt,
        system_prompt=(
            "你是一位资深图书编辑，擅长给非虚构作品的整章做小节切分和小节命名。"
            "你尤其讲究章内节奏：5-6 个小节标题不应该词汇撞车，也不应该词性混乱，"
            "好的小节命名应该读起来像一组对仗的目录条目，或者一条连贯的叙事弧线。"
        ),
        model=model,
        temperature=temperature,
        max_tokens=12288,
        thinking_budget=0,
        timeout=300,
        label="rename_sections",
    )


async def _generate_one_style(
    *,
    chapter: dict[str, Any],
    style_key: str,
    premium: bool,
    n: int = 1,
    timeline: list[TimelineEntry] | None = None,
) -> dict[str, Any]:
    spec = STYLE_SPECS[style_key]
    prompt = _build_prompt(chapter=chapter, style_key=style_key, n=n, timeline=timeline)
    started = time.monotonic()
    try:
        result = await _call_llm(
            prompt, premium=premium,
            temperature=0.7 if n > 1 else 0.5,
        )
    except Exception as e:
        logger.error("[%s] LLM 调用失败：%s", style_key, e)
        return {
            "style": style_key,
            "label": spec["label"],
            "error": str(e),
        }
    elapsed = round(time.monotonic() - started, 1)
    raw_variants = result.get("variants")
    if not isinstance(raw_variants, list) or not raw_variants:
        # 兼容老 schema：result 直接就是 {style_summary, sections}
        if "sections" in result:
            raw_variants = [{
                "variant_summary": result.get("style_summary", ""),
                "sections": result.get("sections", []),
            }]
        else:
            raw_variants = []
    variants = []
    for v in raw_variants:
        if not isinstance(v, dict):
            continue
        variants.append({
            "variant_summary": v.get("variant_summary", "") or v.get("style_summary", ""),
            "sections": v.get("sections", []),
        })
    return {
        "style": style_key,
        "label": spec["label"],
        "variants": variants,
        "elapsed_seconds": elapsed,
    }


def _validate_variant(
    variant: dict[str, Any],
    expected_n: int,
    *,
    max_chars: int = 12,
) -> tuple[bool, str]:
    """校验单套 LLM 重命名候选是否合格。

    硬规则：
    - section 数与期望一致
    - 每条 new_title 非空
    - 每条不超过 max_chars 个字符（含标点；这是图书目录的紧凑度约束）
    - 同套内标题不能重复
    """
    sections = variant.get("sections", [])
    if len(sections) != expected_n:
        return False, f"返回 {len(sections)} 条，期望 {expected_n} 条"
    titles: list[str] = []
    too_long: list[str] = []
    for i, s in enumerate(sections):
        nt = (s.get("new_title") or "").strip()
        if not nt:
            return False, f"第 {i+1} 节 new_title 为空"
        if len(nt) > max_chars:
            too_long.append(f"§{i+1}「{nt}」({len(nt)} 字)")
        titles.append(nt)
    if too_long:
        return False, f"标题超长（>{max_chars} 字）：{', '.join(too_long)}"
    seen: dict[str, int] = {}
    for t in titles:
        seen[t] = seen.get(t, 0) + 1
    dup = [t for t, c in seen.items() if c > 1]
    if dup:
        return False, f"小节标题重复：{dup}"
    return True, ""


async def _process_one(
    *,
    idx: int,
    paths: JobPaths,
    styles: list[str],
    premium: bool,
    apply_style: str | None,
    apply_pick: int,
    n_variants: int,
    use_timeline: bool = False,
) -> dict[str, Any]:
    ep_dir = paths.episode_dir(idx)
    chapter_path = ep_dir / "chapter_for_book.json"
    if not chapter_path.exists():
        logger.warning("[ep%02d] chapter_for_book.json 不存在，跳过", idx)
        return {"index": idx, "status": "no_chapter"}

    chapter = read_json(chapter_path)
    n_sec = len(chapter.get("sections", []))

    # 默认 **不** 注入作者时间轴。
    # 经验上，作者在 shownotes 里写的子标题往往是「播客口语风」（"汗水和纪律在召唤过来的"），
    # LLM 拿到后会把这种口语语气带进结果，反而稀释了「图书编辑式克制」的命名质感。
    # 想用作者锚点时显式 --use-timeline 启用。
    timeline: list[TimelineEntry] = []
    if use_timeline:
        meta_path = ep_dir / "meta.json"
        if meta_path.exists():
            try:
                meta = read_json(meta_path)
                timeline = parse_timeline(meta.get("shownotes_text"))
            except Exception as e:
                logger.warning("[ep%02d] 解析时间轴失败：%s", idx, e)

    logger.info(
        "[ep%02d] 小节重命名（共 %d 节）：风格 %s · 每风格 %d 套候选 · 时间轴=%s",
        idx, n_sec, styles, n_variants,
        f"{len(timeline)} 条" if use_timeline else "off",
    )

    style_results: list[dict[str, Any]] = []
    for style_key in styles:
        sr = await _generate_one_style(
            chapter=chapter, style_key=style_key, premium=premium, n=n_variants,
            timeline=timeline,
        )
        if "error" in sr:
            logger.warning("[ep%02d] %s 失败：%s", idx, sr["label"], sr["error"])
            style_results.append(sr)
            continue
        validated_variants = []
        for vi, variant in enumerate(sr.get("variants", []), 1):
            ok, reason = _validate_variant(variant, expected_n=n_sec)
            variant["valid"] = ok
            variant["validation_reason"] = reason
            validated_variants.append(variant)
            if not ok:
                logger.warning(
                    "[ep%02d] %s #%d 校验失败：%s",
                    idx, sr["label"], vi, reason,
                )
                continue
            logger.info(
                "[ep%02d] %s #%d · %s",
                idx, sr["label"], vi,
                variant.get("variant_summary", ""),
            )
            for s in variant["sections"]:
                logger.info(
                    "                  § %d  %s  →  %s",
                    s.get("section_index"),
                    s.get("old_title", ""),
                    s.get("new_title", ""),
                )
        sr["variants"] = validated_variants
        style_results.append(sr)

    out = {
        "chapter_index": idx,
        "chapter_title": chapter.get("chapter_title", ""),
        "current_section_titles": [
            s.get("section_title", "") for s in chapter.get("sections", [])
        ],
        "styles": style_results,
        "_built_at": datetime.now().isoformat(timespec="seconds"),
    }
    write_json(ep_dir / "section_titles_candidates.json", out)

    if apply_style:
        sr = next((s for s in style_results if s.get("style") == apply_style), None)
        if not sr:
            logger.error("[ep%02d] --apply %s 失败：该风格未生成", idx, apply_style)
            return {"index": idx, "status": "apply_missing"}
        valid_variants = [v for v in sr.get("variants", []) if v.get("valid")]
        if not valid_variants:
            logger.error("[ep%02d] --apply %s 失败：无校验通过的候选", idx, apply_style)
            return {"index": idx, "status": "apply_missing"}
        if not (1 <= apply_pick <= len(valid_variants)):
            logger.error(
                "[ep%02d] --apply-pick %d 越界（该风格共 %d 个有效候选）",
                idx, apply_pick, len(valid_variants),
            )
            return {"index": idx, "status": "apply_pick_oob"}
        chosen = valid_variants[apply_pick - 1]
        new_titles = {s["section_index"]: s["new_title"] for s in chosen["sections"]}
        for sec in chapter.get("sections", []):
            i = sec.get("section_index")
            if i in new_titles:
                sec["section_title_before_rename"] = sec.get("section_title", "")
                sec["section_title"] = new_titles[i]
        chapter["_pipeline"] = chapter.get("_pipeline") or {}
        chapter["_pipeline"]["sections_renamed_at"] = datetime.now().isoformat(
            timespec="seconds"
        )
        chapter["_pipeline"]["sections_rename_style"] = apply_style
        chapter["_pipeline"]["sections_rename_pick"] = apply_pick
        chapter["_pipeline"]["sections_rename_summary"] = chosen.get(
            "variant_summary", ""
        )
        write_json(chapter_path, chapter)
        logger.info(
            "[ep%02d] ✅ 已应用 %s #%d · 共改写 %d 节",
            idx, apply_style, apply_pick, len(new_titles),
        )

    return {"index": idx, "status": "ok"}


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
    if not paths.episodes_index_json.exists():
        raise SystemExit(f"未找到 episodes_index.json：{paths.root}")

    index = read_json(paths.episodes_index_json)
    only = _parse_only(args.only)

    if args.style == "all":
        styles = list(STYLE_SPECS.keys())
    else:
        styles = [args.style]

    if args.apply and args.style == "all":
        raise SystemExit("--apply 必须配合具体的 --style（parallel/narrative），不能用 all")

    logger.info("作业目录：%s", paths.root)
    logger.info("风格：%s · premium=%s · apply=%s", styles, args.premium, args.apply or "(no)")

    # 模型覆写：跟 run_pipeline 保持同一接口，做 A/B 模型对比时不用改 .env
    if args.model:
        from core.services.llm_service import set_model_override
        set_model_override(args.model)
        logger.info("🔁 LLM 模型覆写：所有 LLM 调用强制使用 %s", args.model)

    results: list[dict[str, Any]] = []
    for ep in index.get("episodes", []):
        idx = ep.get("index")
        if only is not None and idx not in only:
            continue
        result = await _process_one(
            idx=idx,
            paths=paths,
            styles=styles,
            premium=args.premium,
            apply_style=args.apply,
            apply_pick=args.apply_pick,
            n_variants=args.n,
            use_timeline=args.use_timeline,
        )
        results.append(result)

    ok = sum(1 for r in results if r.get("status") == "ok")
    logger.info("=" * 50)
    logger.info("完成：成功 %d / 总 %d", ok, len(results))
    return 0


def main() -> None:
    p = argparse.ArgumentParser(description="给整章全部小节统一重命名（B 端定制）")
    p.add_argument("--job", required=True)
    p.add_argument("--only", default="", help="只跑指定单集，例：11 或 1,5,11")
    p.add_argument(
        "--style", default="all",
        choices=["all", "parallel", "narrative", "concrete"],
        help="小节标题风格：all=三种风格都生成（默认）；具体风格只生成一种",
    )
    p.add_argument(
        "--premium", action="store_true",
        help="使用 premium 模型（更贵但质量更高）",
    )
    p.add_argument(
        "--apply", default="",
        choices=["", "parallel", "narrative", "concrete"],
        help="选定一种风格后写回 chapter_for_book.json 各 section.section_title",
    )
    p.add_argument(
        "--apply-pick", type=int, default=1,
        help="--apply 时挑该风格的第几套候选（1-based，默认 1）",
    )
    p.add_argument(
        "--n", type=int, default=1,
        help="每种风格让 LLM 一次返回多少套互相方向不同的候选（默认 1）",
    )
    p.add_argument(
        "--use-timeline", action="store_true",
        help=(
            "把作者在 shownotes 里写的时间轴章节作为命名素材喂给 LLM。"
            "默认关闭——经验上作者标题往往口语化，会拉低标题克制度；"
            "只在 LLM 完全接不住、需要作者锚点时显式启用。"
        ),
    )
    p.add_argument(
        "--model", default="",
        help=(
            "可选：用 set_model_override 强制所有 LLM 调用走指定模型"
            "（如 gemini-3-flash-preview / deepseek-v4-flash）。"
            "用于 A/B 模型对比时不用改 .env.workbench。"
        ),
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
