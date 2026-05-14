"""
name_chapter — 给单章生成"统领整章"的章名（B 端定制）

C 端 compose 的 `composed_content.title` 是"书封面书名"风格（主+副），到 B 端
"一期=一章" 后跟小节标题撞层级。这个 runner 专门生成"压得住整章、跟小节明显
拉开层级"的章名。

用法：

    cd backend
    # 单集，3 种风格各出一个候选（默认）
    python -m workbench.runners.name_chapter --job possibility --only 11

    # 单集，只跑指定风格
    python -m workbench.runners.name_chapter --job possibility --only 11 --style minimal
    python -m workbench.runners.name_chapter --job possibility --only 11 --style action
    python -m workbench.runners.name_chapter --job possibility --only 11 --style classic

    # 选定风格后写入 chapter_for_book.json（覆盖 chapter_title）
    python -m workbench.runners.name_chapter --job possibility --only 11 --style classic --apply

输入：episodes/epNN/chapter_for_book.json
输出：episodes/epNN/chapter_title_candidates.json（3 个候选 + 各自风格说明）
        若加 --apply，则同时把所选风格的 chapter_title 写回 chapter_for_book.json
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

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("name_chapter")


STYLE_SPECS = {
    "minimal": {
        "label": "极简概念派",
        "max_chars": 8,
        "guidance": (
            "**风格定位：极简概念派**\n"
            "- 必须是**纯名词或名词性短语**（如「内阻力」「内核稳定」「自我价值体系」）\n"
            "- 不带动词、不带修饰、不带冒号副标题\n"
            "- 让一个核心概念独立成章名，留白让小节标题展开\n"
            "- 想象这是经典学术著作 / 哲学随笔的章名习惯（如《论自由》《存在与时间》"
            "里章节命名的克制感）\n"
            "- 倾向于 2-5 字，最多不超过 8 字"
        ),
    },
    "action": {
        "label": "动作派",
        "max_chars": 8,
        "guidance": (
            "**风格定位：动作派**\n"
            "- 以**动词或动宾短语**为主体（如「对抗内阻力」「修炼内核稳定」「重夺线下生活」）\n"
            "- 强调读者在这一章学到 / 做到什么\n"
            "- 不带冒号副标题、不带问号、不带感叹号\n"
            "- 不能用「如何」「怎样」开头\n"
            "- 风格类似优秀自助类书籍的章名，但要克制（避免营销文案感）\n"
            "- 倾向于 4-6 字，最多不超过 8 字"
        ),
    },
    "classic": {
        "label": "书章经典派",
        "max_chars": 8,
        "guidance": (
            "**风格定位：书章经典派**\n"
            "- 文字含蓄克制、文气书卷气，像传统散文集 / 思想随笔的章名\n"
            "- 可用「论 X」「X 之道」「X 与 Y」「关于 X」「X 之书」等古典结构\n"
            "- 概念性优先于场景性\n"
            "- 不带冒号副标题、不带问号、不带感叹号\n"
            "- 想象《人类简史》《沉思录》《自卑与超越》里章名的语感\n"
            "- 倾向于 4-8 字"
        ),
    },
    "bookish": {
        "label": "读书随笔派",
        "max_chars": 22,
        "guidance": (
            "**风格定位：读书随笔派 / 专栏文章派**\n"
            "- 想象这是一篇优秀书评 / 读书随笔 / 文化专栏的标题，**有作者气、有手感**——\n"
            "  读者一眼就觉得「这是个写作者写的，不是 AI 拼的」\n"
            "- **强烈鼓励直接引用节目原标题里的元素**：被讨论的书名（用《》包裹）、\n"
            "  作者人名、原标题里的关键词组（如「重度拖延」「持续努力」「识别」「克服」），\n"
            "  让章名跟节目源头自然咬合\n"
            "- 可以使用冒号——但冒号前后必须都有实质信息（如「读《一生之敌》：把拖延当作敌人」），\n"
            "  绝对不允许「概念词：营销文案 SubTitle」那种自助书套路\n"
            "- 允许使用动词、引语、人物视角、场景钩子，但要克制不抢戏\n"
            "- **铁律式禁忌**（一旦命中，重拟）：\n"
            "    - 「X 术」「X 法」「X 学」「X 论」「X 经」「X 之道」「X 心法」「X 指南」\n"
            "      （这些都是 AI 套话工具书味）\n"
            "    - 「从 A 到 B」结构（这是节目原标题已用过的套话）\n"
            "    - 「X 上瘾」「X 觉醒」「X 内核」「X 主义」这种被自媒体用滥的拼词\n"
            "    - 任何「击退」「征服」「攻克」「突破」「打破」开头的动词命令式\n"
            "- 想象的优秀范本：刘瑜《观念的水位》里的章名、也斯专栏标题、毛姆《读书随笔》、\n"
            "  王小波随笔的标题感——克制、有人味、不喊口号\n"
            "- 长度：12-22 字最佳，宁可写到 18-20 字让标题完整、有呼吸，也不要为了短而压缩到\n"
            "  只剩骨架"
        ),
    },
}


def _build_prompt(*, chapter: dict[str, Any], style_key: str, n: int = 1) -> str:
    spec = STYLE_SPECS[style_key]
    sections_lines = []
    for s in chapter.get("sections", []):
        sections_lines.append(f"  · 第 {s['section_index']} 节：{s['section_title']}")
    sections_text = "\n".join(sections_lines)

    raw_title = chapter.get("chapter_title_raw", "")
    composed_title = chapter.get("chapter_title_from_compose", "")
    core_theme = chapter.get("core_theme", "")
    theme_keywords = "、".join(chapter.get("theme_keywords") or [])
    podcast_name = chapter.get("podcast_name", "")
    host_name = chapter.get("host_name", "")

    return f"""# 任务

你正在帮一本读书/观点合集的整体编辑给**第 {chapter.get('chapter_index')} 章**起一个章名。

这本书的整体形态是：每一章对应一期播客（约 50 分钟谈话），章内有 4-6 个小节用小标题分隔。

# 这一章的素材

- **节目**：{podcast_name}
- **主播**：{host_name}
- **节目原标题**：{raw_title}
- **C 端原 title**（"书封面"风格，过长不可直接用作章名）：{composed_title}
- **核心主题**：{core_theme}
- **关键词**：{theme_keywords}
- **本章 5 个小节标题**：

{sections_text}

# 章名要求

{spec['guidance']}

# 不可触碰的硬约束

1. 长度严格 ≤ {spec['max_chars']} 个汉字（标点不计入但要克制）
2. **绝不能跟任何一个小节标题撞**：章名要跟小节明显拉开层级或视角——
   - 短风格（minimal/action/classic）：比小节更概括、更短、更高一个抽象层级
   - 长风格（bookish）：可以比小节长，但必须在「视角 / 引文 / 人物 / 书名」上跟小节明显不同，
     不允许只是把某个小节标题改写一下当章名
3. 禁止数字（除非是书名《》内的一部分）、禁止英文（除非是书名 / 人名作为引用且必要）、禁止特殊符号
4. 不带"第 X 章""第 X 集"等编号前缀（编号由排版统一加）
5. 章名要让目录页一眼看清"这章在讲什么领域/什么核心问题/对应哪本书或哪个人"

# 候选数量

请一次性给出 **{n}** 个**思路明显不同**的章名候选——
- 不同候选之间不能只是同义改写或字词调整，要在「视角 / 切入点 / 引用对象（书名 / 人名 / 概念 / 故事钩子 / 引语）」上做出明显差异
- 即便最后落到的核心都是同一本书 / 同一个概念，也要走不同的笔法
- 在风格定位允许的范围内尽量发散

# 输出

严格 JSON：

```json
{{
  "candidates": [
    {{
      "chapter_title": "你给的章名",
      "char_count": 实际汉字数（数字）,
      "rationale": "30 字内说明这个章名的命名思路 / 切入点"
    }}
    // ... 共 {n} 条 ...
  ]
}}
```"""


async def _call_llm(prompt: str, *, premium: bool, temperature: float = 0.7) -> dict[str, Any]:
    from core.services.llm_service import get_llm_service
    from core.config import settings
    llm = get_llm_service()
    model = settings.LLM_PREMIUM_MODEL if premium else settings.LLM_MODEL
    return await llm.generate_json(
        prompt=prompt,
        system_prompt="你是一位资深图书编辑，擅长给非虚构作品起精准、克制、有质感的章名。",
        model=model,
        temperature=temperature,
        label="name_chapter",
    )


async def _generate_one_style(
    *,
    chapter: dict[str, Any],
    style_key: str,
    premium: bool,
    n: int = 1,
) -> dict[str, Any]:
    spec = STYLE_SPECS[style_key]
    prompt = _build_prompt(chapter=chapter, style_key=style_key, n=n)
    started = time.monotonic()
    try:
        result = await _call_llm(prompt, premium=premium, temperature=0.7 if n > 1 else 0.4)
    except Exception as e:
        logger.error("[%s] LLM 调用失败：%s", style_key, e)
        return {
            "style": style_key,
            "label": spec["label"],
            "max_chars": spec["max_chars"],
            "error": str(e),
        }
    elapsed = round(time.monotonic() - started, 1)
    raw_candidates = result.get("candidates")
    if not isinstance(raw_candidates, list) or not raw_candidates:
        # 兼容老 schema：result 直接就是 {chapter_title, char_count, rationale}
        if "chapter_title" in result:
            raw_candidates = [result]
        else:
            raw_candidates = []
    candidates = []
    for c in raw_candidates:
        if not isinstance(c, dict):
            continue
        title = (c.get("chapter_title") or "").strip()
        if not title:
            continue
        candidates.append({
            "chapter_title": title,
            "char_count": c.get("char_count"),
            "rationale": c.get("rationale", ""),
        })
    return {
        "style": style_key,
        "label": spec["label"],
        "max_chars": spec["max_chars"],
        "candidates_in_style": candidates,
        "elapsed_seconds": elapsed,
    }


async def _process_one(
    *,
    idx: int,
    paths: JobPaths,
    styles: list[str],
    premium: bool,
    apply_style: str | None,
    apply_pick: int,
    n: int,
) -> dict[str, Any]:
    ep_dir = paths.episode_dir(idx)
    chapter_path = ep_dir / "chapter_for_book.json"
    if not chapter_path.exists():
        logger.warning("[ep%02d] chapter_for_book.json 不存在，跳过", idx)
        return {"index": idx, "status": "no_chapter"}

    chapter = read_json(chapter_path)
    logger.info(
        "[ep%02d] 章名候选生成：%s · 风格 %s · 每风格 %d 个",
        idx, chapter.get("chapter_title", ""), styles, n,
    )

    style_results: list[dict[str, Any]] = []
    for style_key in styles:
        sr = await _generate_one_style(
            chapter=chapter, style_key=style_key, premium=premium, n=n,
        )
        style_results.append(sr)
        if "error" in sr:
            logger.warning("[ep%02d] %s 失败：%s", idx, sr["label"], sr["error"])
            continue
        for i, c in enumerate(sr.get("candidates_in_style", []), 1):
            logger.info(
                "[ep%02d] %s #%d（≤%d字）→ %s · %s 字 · %s",
                idx, sr["label"], i, sr["max_chars"],
                c["chapter_title"], c.get("char_count") or "?",
                (c.get("rationale") or "")[:40],
            )

    out = {
        "chapter_index": idx,
        "current_chapter_title": chapter.get("chapter_title", ""),
        "current_source": chapter.get("chapter_title_source", ""),
        "styles": style_results,
        "_built_at": datetime.now().isoformat(timespec="seconds"),
    }
    write_json(ep_dir / "chapter_title_candidates.json", out)

    if apply_style:
        sr = next((s for s in style_results if s.get("style") == apply_style), None)
        if not sr or not sr.get("candidates_in_style"):
            logger.error("[ep%02d] --apply %s 失败：该风格未生成有效结果", idx, apply_style)
            return {"index": idx, "status": "apply_missing"}
        cand_list = sr["candidates_in_style"]
        if not (1 <= apply_pick <= len(cand_list)):
            logger.error(
                "[ep%02d] --apply-pick %d 越界（该风格共 %d 个候选）",
                idx, apply_pick, len(cand_list),
            )
            return {"index": idx, "status": "apply_pick_oob"}
        chosen = cand_list[apply_pick - 1]
        chapter["chapter_title_before_rename"] = chapter.get("chapter_title", "")
        chapter["chapter_title"] = chosen["chapter_title"]
        chapter["chapter_title_source"] = f"name_chapter:{apply_style}#{apply_pick}"
        chapter["_pipeline"] = chapter.get("_pipeline") or {}
        chapter["_pipeline"]["renamed_at"] = datetime.now().isoformat(timespec="seconds")
        chapter["_pipeline"]["rename_style"] = apply_style
        chapter["_pipeline"]["rename_pick"] = apply_pick
        chapter["_pipeline"]["rename_rationale"] = chosen.get("rationale", "")
        write_json(chapter_path, chapter)
        logger.info(
            "[ep%02d] ✅ 已应用 %s #%d → 章名《%s》",
            idx, apply_style, apply_pick, chosen["chapter_title"],
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
        raise SystemExit("--apply 必须配合具体的 --style（minimal/action/classic），不能用 all")

    logger.info("作业目录：%s", paths.root)
    logger.info("风格：%s · premium=%s · apply=%s", styles, args.premium, args.apply or "(no)")

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
            n=args.n,
        )
        results.append(result)

    ok = sum(1 for r in results if r.get("status") == "ok")
    logger.info("=" * 50)
    logger.info("完成：成功 %d / 总 %d", ok, len(results))
    return 0


def main() -> None:
    p = argparse.ArgumentParser(description="给整章重新命名（B 端定制章名风格）")
    p.add_argument("--job", required=True)
    p.add_argument("--only", default="", help="只跑指定单集，例：11 或 1,5,11")
    p.add_argument(
        "--style", default="all",
        choices=["all", "minimal", "action", "classic", "bookish"],
        help="章名风格：all=四种风格都生成（默认）；具体风格只生成一种",
    )
    p.add_argument(
        "--premium", action="store_true",
        help="使用 premium 模型（更贵但质量更高，章名场景推荐开）",
    )
    p.add_argument(
        "--apply", default="",
        choices=["", "minimal", "action", "classic", "bookish"],
        help="选定一种风格后写回 chapter_for_book.json 的 chapter_title",
    )
    p.add_argument(
        "--apply-pick", type=int, default=1,
        help="--apply 时挑该风格的第几个候选（1-based，默认 1）",
    )
    p.add_argument(
        "--n", type=int, default=1,
        help="每种风格让 LLM 一次返回多少个互相方向不同的候选（默认 1）",
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
