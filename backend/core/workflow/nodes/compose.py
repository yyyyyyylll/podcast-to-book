"""
通用成稿引擎（Compose Engine）

从 compose_interview.py 改造而来的通用引擎。
根据 state 中的 content_type + narrative_type + voice_format 从 prompt registry 加载对应的 prompt 组，
执行通用 3-phase 流程：
  1. 结构规划：识别话题边界，划分章节，分配片段
  2. 逐章编辑：并行处理每个章节
  3. 前言生成：基于全部章节生成标题、导语和内容提要

所有分类维度的差异完全通过 prompt 控制，代码逻辑共享。

输出格式与现有 composed_content 兼容，对接 extraction / annotation / typeset。
"""
import asyncio
import re
import time
from pathlib import Path
from typing import Dict, Any, List

from core.workflow.state import PodBookState, WorkflowStage
from core.workflow.nodes.transcription import build_metadata_context
from core.workflow.prompts.registry import get_prompts
from core.config import settings
from core.services.llm_service import get_llm_service

KNOWLEDGE_DIR = Path(__file__).resolve().parent.parent / "knowledge" / "examples"

MAX_CHAPTER_CHARS = 25000
SUB_BATCH_TARGET_CHARS = 18000
MIN_CHAPTER_SOURCE_CHARS = 800
MIN_CHAPTER_OUTPUT_CHARS = 400


# ============ Knowledge Loading ============

def _load_style_guide(content_type: str, narrative_type: str, voice_format: str) -> str:
    """按 content_type 加载风格指南。

    查找: {content_type}_style_guide.md
    """
    name = f"{content_type}_style_guide.md"
    path = KNOWLEDGE_DIR / name
    if path.exists():
        print(f"[compose] 加载风格指南: {name}")
        return path.read_text(encoding="utf-8")
    print(f"[compose] 未找到 {content_type} 风格指南，跳过")
    return ""


def _load_few_shot_cases(
    content_type: str, narrative_type: str, voice_format: str
) -> tuple:
    """加载 few-shot 案例，按 content_type 组织，逐级回退。

    查找顺序:
      1. {content_type}_cases/          — 当前内容类型专属
      2. 任意已有的 *_cases/ 目录       — 跨类型通用参考（仅学写法）

    Returns: (cases: List[str], is_cross_type: bool)
    """
    # 1. 当前 content_type 专属
    own_dir = KNOWLEDGE_DIR / f"{content_type}_cases"
    if own_dir.exists():
        cases = _read_cases_dir(own_dir, own_dir.name)
        return cases, False

    # 2. fallback: 任意已有案例目录，作为写法参考
    for cases_dir in sorted(KNOWLEDGE_DIR.glob("*_cases")):
        if cases_dir.is_dir():
            cases = _read_cases_dir(cases_dir, cases_dir.name)
            if cases:
                print(f"[compose] 未找到 {content_type} 专属案例，"
                      f"使用通用参考案例 ({cases_dir.name})")
                return cases, True

    print(f"[compose] 未找到任何 few-shot 案例，跳过")
    return [], False


def _read_cases_dir(cases_dir: Path, label: str) -> List[str]:
    cases = []
    for p in sorted(cases_dir.glob("*.md")):
        try:
            cases.append(p.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"[compose] 加载案例 {p.name} 失败: {e}")
    print(f"[compose] 加载了 {len(cases)} 个 few-shot 案例 ({label})")
    return cases


TRIVIAL_SPEAKER_RATIO = 0.03
_ROLE_KEYWORDS = ("播报员", "播音员", "旁白", "配音", "解说", "主播", "导播", "片头", "片尾", "主持人")


def _filter_trivial_speakers(segments: List[Dict]) -> List[Dict]:
    """过滤掉占比极小或角色类说话人（如片头片尾旁白、播报员等）。"""
    if len(segments) <= 1:
        return segments

    total_chars = sum(len(seg.get("text", "")) for seg in segments)
    if total_chars == 0:
        return segments

    speaker_chars: Dict[str, int] = {}
    for seg in segments:
        sp = seg.get("speaker", "未知")
        speaker_chars[sp] = speaker_chars.get(sp, 0) + len(seg.get("text", ""))

    trivial = {
        sp for sp, chars in speaker_chars.items()
        if chars / total_chars < TRIVIAL_SPEAKER_RATIO
        or any(kw in sp for kw in _ROLE_KEYWORDS)
    }

    if not trivial:
        return segments

    filtered = [seg for seg in segments if seg.get("speaker", "未知") not in trivial]
    for sp in trivial:
        print(f"[compose] 过滤说话人 '{sp}' "
              f"({speaker_chars[sp]} 字, {speaker_chars[sp]/total_chars:.1%})")
    return filtered if filtered else segments


# ============ Composer ============

class ComposeEngine:
    """通用三阶段成稿引擎：结构规划 → 逐章编辑 → 前言生成。

    所有分类维度的差异完全通过 prompt 控制。
    """

    MAX_CONCURRENT_CHAPTERS = 4

    def __init__(
        self,
        state: PodBookState,
        metadata_context: str = "",
    ):
        self.llm = get_llm_service()
        self.metadata_context = metadata_context
        self.content_type = state.get("content_type", "business")
        self.narrative_type = state.get("narrative_type", "opinion")
        self.voice_format = state.get("voice_format", "dialogue")

        # host 自助板块通过 state["prompt_variant"]="host_no_bold" 让加粗指令被剥除，
        # 不影响 C 端默认调用（variant=None）。
        self.prompts = get_prompts(
            "compose", self.content_type, self.narrative_type, self.voice_format,
            variant=state.get("prompt_variant"),
        )

        self._style_guide = _load_style_guide(
            self.content_type, self.narrative_type, self.voice_format
        )
        self._cases, self._cases_cross_type = _load_few_shot_cases(
            self.content_type, self.narrative_type, self.voice_format
        )

        self.speaker_count = 0

        print(f"[compose] 引擎初始化: content={self.content_type}, "
              f"narrative={self.narrative_type}, voice={self.voice_format}")

    # ---------- formatting helpers ----------

    def _format_segments(self, segments: List[Dict], start: int = 0) -> str:
        lines = []
        for i, seg in enumerate(segments, start):
            speaker = seg.get("speaker", "未知")
            text = seg.get("text", "")
            lines.append(f"[{i}]【{speaker}】{text}")
        return "\n\n".join(lines)

    def _format_range(self, segments: List[Dict], start_id: int, end_id: int) -> str:
        lines = []
        for i in range(start_id, min(end_id + 1, len(segments))):
            seg = segments[i]
            speaker = seg.get("speaker", "未知")
            text = seg.get("text", "")
            lines.append(f"[{i}]【{speaker}】{text}")
        return "\n\n".join(lines)

    def _style_guide_excerpt(self) -> str:
        if not self._style_guide:
            return "（未加载风格指南）"
        sections = []
        for header in ["5.2 说话人标签", "5.3 口语清理", "5.4 关键信息标注", "5.5 段落与排版"]:
            start = self._style_guide.find(header)
            if start == -1:
                continue
            end = self._style_guide.find("\n## ", start + 1)
            if end == -1:
                end = self._style_guide.find("\n---", start + 1)
            if end == -1:
                end = len(self._style_guide)
            sections.append(self._style_guide[start:end].strip())
        return "\n\n".join(sections) if sections else self._style_guide[:2000]

    @staticmethod
    def _split_case(case_text: str) -> tuple:
        import re
        match = re.search(r'\n访谈(?:节选|完整实录|全文|正文)\n', case_text)
        if match:
            return case_text[:match.start()].strip(), case_text[match.end():].strip()
        return case_text[:3000].strip(), ""

    def _extract_preamble_examples(self) -> str:
        if not self._cases:
            return "（无参考案例）"
        examples = []
        for i, case in enumerate(self._cases, 1):
            preamble, _ = self._split_case(case)
            examples.append(f"--- 案例 {i} ---\n{preamble}")
        body = "\n\n".join(examples)
        if self._cases_cross_type:
            body = (
                "⚠️ 以下案例来自其他内容类型，仅供学习**写作格式和结构**"
                "（标题拟法、导语组织方式、内容提要如何提炼核心观点），"
                "请勿照搬案例中的话题和术语，应完全适配当前播客的主题领域。\n\n"
                + body
            )
        return body

    # ---------- Phase 1: Plan ----------

    async def _plan_structure(self, segments: List[Dict], replan_context: str = "") -> Dict:
        plan_system = self.prompts.get("plan_system", "")
        plan_user = self.prompts.get("plan_user", "")

        prompt = plan_user.format(
            metadata_context=self.metadata_context or "（无）",
            total_segments=len(segments),
            last_segment_id=len(segments) - 1,
            numbered_segments=self._format_segments(segments),
        )
        if replan_context:
            prompt += replan_context
        print(f"[compose] Phase 1 prompt: {len(prompt)} 字符")

        plan = await self.llm.generate_json(
            prompt=prompt,
            system_prompt=plan_system,
            temperature=0.3,
            model=settings.LLM_MODEL,
            max_tokens=65536,
            timeout=180,
            label="规划章节结构",
        )

        chapters = plan.get("chapters", [])
        self._validate_plan(chapters, len(segments))
        return plan

    def _validate_plan(self, chapters: List[Dict], total_segments: int):
        if not chapters:
            raise ValueError("规划结果为空")

        for i, ch in enumerate(chapters):
            s = int(ch.get("start_segment_id", -1))
            e = int(ch.get("end_segment_id", -1))
            ch["start_segment_id"] = s
            ch["end_segment_id"] = e

            if s < 0:
                s = 0
                ch["start_segment_id"] = s
            if e >= total_segments:
                e = total_segments - 1
                ch["end_segment_id"] = e
            if e < s:
                e = s
                ch["end_segment_id"] = e

            if i > 0:
                prev_end = chapters[i - 1]["end_segment_id"]
                expected_start = prev_end + 1
                if s < expected_start:
                    print(f"[compose] 警告: 章节 {i+1} start={s} 与前章重叠"
                          f"（前章 end={prev_end}），自动修正")
                    ch["start_segment_id"] = expected_start
                elif s > expected_start:
                    gap = s - expected_start
                    print(f"[compose] 章节 {i+1} 跳过 {gap} 个无关片段 "
                          f"({expected_start}-{s-1})")

            if ch["start_segment_id"] > ch["end_segment_id"]:
                print(f"[compose] 警告: 章节 {i+1}「{ch.get('title', '')}」"
                      f"修正后 start={ch['start_segment_id']} > end={ch['end_segment_id']}，"
                      f"标记为待移除")
                ch["_invalid"] = True

        chapters[:] = [ch for ch in chapters if not ch.get("_invalid")]
        if not chapters:
            raise ValueError("所有章节在校验后均无效（片段范围冲突）")

        first_start = chapters[0]["start_segment_id"]
        last_end = chapters[-1]["end_segment_id"]
        covered = sum(ch["end_segment_id"] - ch["start_segment_id"] + 1 for ch in chapters)
        skipped_head = first_start
        skipped_tail = total_segments - 1 - last_end
        skipped_mid = (last_end - first_start + 1) - covered
        print(f"[compose] 规划校验: {len(chapters)} 章, "
              f"覆盖 {covered} 段 (片段 {first_start}-{last_end}), "
              f"跳过: 开头 {skipped_head}, 中间 {skipped_mid}, 结尾 {skipped_tail}")

    def _estimate_chapter_chars(self, segments: List[Dict], chapter: Dict) -> int:
        start_id = chapter["start_segment_id"]
        end_id = chapter["end_segment_id"]
        total = 0
        for i in range(start_id, min(end_id + 1, len(segments))):
            total += len(segments[i].get("text", ""))
        return total

    @staticmethod
    def _merge_short_chapters(
        chapter_plans: List[Dict], chapter_contents: List[str],
    ) -> tuple:
        """将输出过短的章节合并到相邻章节（优先向后合并，末尾则向前）。"""
        merged_plans: List[Dict] = []
        merged_contents: List[str] = []

        i = 0
        while i < len(chapter_plans):
            cp = chapter_plans[i]
            content = chapter_contents[i]

            if len(content) < MIN_CHAPTER_OUTPUT_CHARS and (merged_plans or i + 1 < len(chapter_plans)):
                if i + 1 < len(chapter_plans):
                    next_cp = chapter_plans[i + 1]
                    next_content = chapter_contents[i + 1]
                    combined_cp = {
                        **next_cp,
                        "start_segment_id": cp["start_segment_id"],
                        "title": next_cp.get("title", cp.get("title", "")),
                    }
                    combined_content = (content.rstrip() + "\n\n" + next_content.lstrip()).strip()
                    print(f"[compose] 合并过短章节「{cp.get('title', '')}」"
                          f"({len(content)} 字) → 并入「{next_cp.get('title', '')}」")
                    chapter_plans[i + 1] = combined_cp
                    chapter_contents[i + 1] = combined_content
                    i += 1
                    continue
                elif merged_plans:
                    prev_content = merged_contents[-1]
                    merged_contents[-1] = (prev_content.rstrip() + "\n\n" + content.lstrip()).strip()
                    merged_plans[-1]["end_segment_id"] = cp.get("end_segment_id",
                                                                 merged_plans[-1]["end_segment_id"])
                    print(f"[compose] 合并过短章节「{cp.get('title', '')}」"
                          f"({len(content)} 字) → 并入「{merged_plans[-1].get('title', '')}」")
                    i += 1
                    continue

            merged_plans.append(cp)
            merged_contents.append(content)
            i += 1

        if len(merged_plans) < len(chapter_plans):
            print(f"[compose] 短章节合并: {len(chapter_plans)} → {len(merged_plans)} 章")

        return merged_plans, merged_contents

    # ---------- Phase 2: Compose Chapters ----------

    async def _compose_all_chapters(
        self, chapter_plans: List[Dict], segments: List[Dict]
    ) -> List[str]:
        semaphore = asyncio.Semaphore(self.MAX_CONCURRENT_CHAPTERS)
        total = len(chapter_plans)

        async def _do(idx: int, cp: Dict) -> str:
            async with semaphore:
                return await self._compose_single_chapter(idx, total, cp, segments)

        tasks = [_do(i, cp) for i, cp in enumerate(chapter_plans)]
        return list(await asyncio.gather(*tasks))

    async def _compose_single_chapter(
        self, idx: int, total: int, chapter_plan: Dict, segments: List[Dict]
    ) -> str:
        start_id = chapter_plan["start_segment_id"]
        end_id = chapter_plan["end_segment_id"]
        title = chapter_plan.get("title", f"章节 {idx+1}")

        chapter_chars = self._estimate_chapter_chars(segments, chapter_plan)
        if chapter_chars > MAX_CHAPTER_CHARS:
            print(f"[compose]   [{idx+1}/{total}] 章节「{title}」"
                  f"过大 ({chapter_chars} 字符 > {MAX_CHAPTER_CHARS})，启用分批处理")
            return await self._compose_chapter_in_parts(idx, total, chapter_plan, segments)

        seg_text = self._format_range(segments, start_id, end_id)
        seg_count = end_id - start_id + 1

        if not seg_text.strip():
            print(f"[compose]   [{idx+1}/{total}] 章节「{title}」"
                  f"(片段 {start_id}-{end_id}) 原始片段为空，跳过")
            return ""

        chapter_sys = self.prompts.get("chapter_system", "")
        chapter_tmpl = self.prompts.get("chapter_user", "")

        prompt = chapter_tmpl.format(
            chapter_title=title,
            style_guide_excerpt=self._style_guide_excerpt(),
            chapter_segments=seg_text,
            speaker_count=self.speaker_count,
        )

        print(f"[compose]   [{idx+1}/{total}] {title} "
              f"(片段 {start_id}-{end_id}, {seg_count}段, "
              f"{self.speaker_count}人, "
              f"{self.narrative_type}_{self.voice_format}x{self.content_type}, prompt {len(prompt)}字符)")

        max_retries = 2
        for attempt in range(max_retries):
            try:
                result = await self.llm.generate(
                    prompt=prompt,
                    system_prompt=chapter_sys,
                    temperature=0.4,
                    model=settings.LLM_MODEL,
                    max_tokens=65536,
                    timeout=300,
                    label=f"撰写章节: {title}",
                )
                break
            except Exception as e:
                if attempt < max_retries - 1:
                    print(f"[compose]   [{idx+1}] 重试: {e}")
                    await asyncio.sleep(15)
                else:
                    raise

        print(f"[compose]   [{idx+1}/{total}] 完成: {len(result)} 字")
        return result.strip()

    # ---------- Phase 2b: Large Chapter Sub-batch ----------

    def _split_chapter_segments(
        self, segments: List[Dict], start_id: int, end_id: int,
        target_chars: int = SUB_BATCH_TARGET_CHARS,
    ) -> List[tuple]:
        sub_batches = []
        cur_start = start_id
        cur_chars = 0

        for i in range(start_id, min(end_id + 1, len(segments))):
            seg_chars = len(segments[i].get("text", ""))
            if cur_chars + seg_chars > target_chars and cur_chars > 0:
                sub_batches.append((cur_start, i - 1))
                cur_start = i
                cur_chars = seg_chars
            else:
                cur_chars += seg_chars

        if cur_start <= end_id:
            sub_batches.append((cur_start, min(end_id, len(segments) - 1)))

        return sub_batches

    async def _compose_chapter_in_parts(
        self, idx: int, total: int, chapter_plan: Dict, segments: List[Dict],
    ) -> str:
        title = chapter_plan.get("title", f"章节 {idx+1}")
        start_id = chapter_plan["start_segment_id"]
        end_id = chapter_plan["end_segment_id"]

        sub_batches = self._split_chapter_segments(segments, start_id, end_id)
        print(f"[compose]   [{idx+1}/{total}] 拆分为 {len(sub_batches)} 个子批次")

        chapter_sys = self.prompts.get("chapter_system", "")
        chapter_tmpl = self.prompts.get("chapter_user", "")
        cont_tmpl = self.prompts.get("chapter_continuation_user", "")

        parts: List[str] = []
        for bi, (sub_start, sub_end) in enumerate(sub_batches):
            seg_text = self._format_range(segments, sub_start, sub_end)
            seg_count = sub_end - sub_start + 1

            if not seg_text.strip():
                continue

            if bi == 0:
                prompt = chapter_tmpl.format(
                    chapter_title=title,
                    style_guide_excerpt=self._style_guide_excerpt(),
                    chapter_segments=seg_text,
                    speaker_count=self.speaker_count,
                )
            else:
                prev_tail = parts[-1][-800:] if parts else ""
                prompt = cont_tmpl.format(
                    chapter_title=title,
                    style_guide_excerpt=self._style_guide_excerpt(),
                    previous_tail=prev_tail,
                    chapter_segments=seg_text,
                    speaker_count=self.speaker_count,
                )

            print(f"[compose]     子批次 {bi+1}/{len(sub_batches)} "
                  f"(片段 {sub_start}-{sub_end}, {seg_count}段, prompt {len(prompt)}字符)")

            max_retries = 2
            for attempt in range(max_retries):
                try:
                    result = await self.llm.generate(
                        prompt=prompt,
                        system_prompt=chapter_sys,
                        temperature=0.4,
                        model=settings.LLM_MODEL,
                        max_tokens=65536,
                        timeout=300,
                        label=f"撰写章节(分批): {title} [{bi+1}/{len(sub_batches)}]",
                    )
                    break
                except Exception as e:
                    if attempt < max_retries - 1:
                        print(f"[compose]     子批次 {bi+1} 重试: {e}")
                        await asyncio.sleep(15)
                    else:
                        raise

            parts.append(result.strip())
            print(f"[compose]     子批次 {bi+1}/{len(sub_batches)} "
                  f"完成: {len(result)} 字")

        combined = "\n\n".join(p for p in parts if p)
        print(f"[compose]   [{idx+1}/{total}] 分批合并完成: "
              f"总计 {len(combined)} 字")
        return combined

    # ---------- Phase 3: Preamble ----------

    async def _generate_preamble(
        self, chapter_plans: List[Dict], chapter_contents: List[str]
    ) -> Dict:
        full_parts = []
        for i, (cp, content) in enumerate(zip(chapter_plans, chapter_contents)):
            title = cp.get("title", f"章节 {i+1}")
            full_parts.append(f"## {i+1}. {title}\n\n{content}")
        full_text = "\n\n---\n\n".join(full_parts)

        preamble_sys = self.prompts.get("preamble_system", "")
        preamble_tmpl = self.prompts.get("preamble_user", "")

        prompt = preamble_tmpl.format(
            metadata_context=self.metadata_context or "（无）",
            full_text=full_text,
            few_shot_preambles=self._extract_preamble_examples(),
        )

        print(f"[compose] Phase 3 prompt: {len(prompt)} 字符")

        return await self.llm.generate_json(
            prompt=prompt,
            system_prompt=preamble_sys,
            temperature=0.5,
            model=settings.LLM_PREMIUM_MODEL,
            max_tokens=65536,
            timeout=300,
            label="生成前言",
        )

    # ---------- Run ----------

    async def run(self, segments: List[Dict]) -> Dict:
        total_start = time.time()

        unique_speakers = set(
            seg.get("speaker", "未知") for seg in segments
        )
        self.speaker_count = len(unique_speakers)
        print(f"[compose] 说话人数: {self.speaker_count} ({'、'.join(sorted(unique_speakers))})")

        # Phase 1
        print(f"[compose] Phase 1: 规划章节结构 ({len(segments)} 个片段)...")
        plan = await self._plan_structure(segments)
        chapter_plans = plan.get("chapters", [])
        for i, cp in enumerate(chapter_plans, 1):
            print(f"[compose]   章节 {i}: "
                  f"[{cp['start_segment_id']}-{cp['end_segment_id']}] {cp['title']}")
        print(f"[compose] Phase 1 完成: {len(chapter_plans)} 个章节")

        oversized = []
        undersized = []
        for i, cp in enumerate(chapter_plans):
            chars = self._estimate_chapter_chars(segments, cp)
            if chars > MAX_CHAPTER_CHARS:
                oversized.append((i + 1, cp.get("title", ""), chars))
            elif chars < MIN_CHAPTER_SOURCE_CHARS and len(chapter_plans) > 1:
                undersized.append((i + 1, cp.get("title", ""), chars))

        need_replan = False
        replan_hint = "\n\n# 重要补充约束\n\n"

        if oversized:
            replan_hint += "上一次规划中，以下章节包含过多内容，必须进一步拆分为更小的子话题章节：\n"
            for num, title, chars in oversized:
                replan_hint += (
                    f"- 章节 {num}「{title}」: 约 {chars} 字符"
                    f"（上限 {MAX_CHAPTER_CHARS}）\n"
                )
            replan_hint += (
                f"\n请确保每个章节的原始文本量不超过 {MAX_CHAPTER_CHARS} 字符。"
                f"将过大的章节按其内部的子话题/子议题拆分为多个独立章节。\n"
            )
            need_replan = True

        if undersized:
            replan_hint += "以下章节内容过少，应将其合并到相邻章节中：\n"
            for num, title, chars in undersized:
                replan_hint += (
                    f"- 章节 {num}「{title}」: 仅约 {chars} 字符"
                    f"（下限 {MIN_CHAPTER_SOURCE_CHARS}）\n"
                )
            replan_hint += (
                f"\n请确保每个章节的原始文本量不少于 {MIN_CHAPTER_SOURCE_CHARS} 字符。"
                f"将过短的章节与内容最相关的相邻章节合并，不要生成过短的独立章节。\n"
            )
            need_replan = True

        if need_replan:
            issues = []
            if oversized:
                issues.append(f"{len(oversized)} 个过大章节")
            if undersized:
                issues.append(f"{len(undersized)} 个过短章节")
            print(f"[compose] 检测到 {'、'.join(issues)}，重新规划...")
            plan = await self._plan_structure(segments, replan_context=replan_hint)
            chapter_plans = plan.get("chapters", [])
            for i, cp in enumerate(chapter_plans, 1):
                print(f"[compose]   章节 {i}: "
                      f"[{cp['start_segment_id']}-{cp['end_segment_id']}] {cp['title']}")
            print(f"[compose] 重新规划完成: {len(chapter_plans)} 个章节")

        # Phase 2
        print(f"[compose] Phase 2: 逐章编辑 "
              f"({len(chapter_plans)} 章, 并发={self.MAX_CONCURRENT_CHAPTERS})...")
        chapter_contents = await self._compose_all_chapters(chapter_plans, segments)

        valid_pairs = [
            (cp, content) for cp, content in zip(chapter_plans, chapter_contents)
            if content.strip()
        ]
        if not valid_pairs:
            raise ValueError("所有章节均生成为空")
        if len(valid_pairs) < len(chapter_plans):
            removed = len(chapter_plans) - len(valid_pairs)
            print(f"[compose] 过滤掉 {removed} 个空章节")
        chapter_plans, chapter_contents = zip(*valid_pairs)
        chapter_plans = list(chapter_plans)
        chapter_contents = list(chapter_contents)

        total_chars = sum(len(c) for c in chapter_contents)
        print(f"[compose] Phase 2 完成: 总计 {total_chars} 字")

        if len(chapter_plans) > 1:
            chapter_plans, chapter_contents = self._merge_short_chapters(
                chapter_plans, chapter_contents
            )

        if self.speaker_count <= 1:
            _speaker_label_re = re.compile(r'^\*\*[^*：:]+[：:]\*\*\s*', re.MULTILINE)
            cleaned = []
            for content in chapter_contents:
                before = content
                content = _speaker_label_re.sub('', content)
                if content != before:
                    print(f"[compose] 单人模式: 清除了残留的说话人标签")
                cleaned.append(content)
            chapter_contents = cleaned

        # Phase 3
        print(f"[compose] Phase 3: 生成标题和导语...")
        preamble_data = await self._generate_preamble(chapter_plans, chapter_contents)
        print(f"[compose] Phase 3 完成: {preamble_data.get('title', '(无标题)')}")

        elapsed = time.time() - total_start
        print(f"[compose] 全部完成, 总耗时 {elapsed:.1f}s")

        return self._build_output(preamble_data, chapter_plans, chapter_contents, segments, elapsed)

    # ---------- Output ----------

    def _build_output(
        self,
        preamble_data: Dict,
        chapter_plans: List[Dict],
        chapter_contents: List[str],
        segments: List[Dict],
        elapsed: float,
    ) -> Dict:
        chapters = []
        for cp, content in zip(chapter_plans, chapter_contents):
            start_id = cp["start_segment_id"]
            end_id = cp["end_segment_id"]
            source_ids = list(range(start_id, end_id + 1))

            source_segs = [segments[sid] for sid in source_ids if 0 <= sid < len(segments)]
            start_t = source_segs[0].get("start_time", 0) if source_segs else 0
            end_t = source_segs[-1].get("end_time", 0) if source_segs else 0

            section_type = f"{self.narrative_type}_{self.voice_format}_section"
            chapters.append({
                "title": cp.get("title", ""),
                "content": content,
                "key_points": [cp.get("description", "")],
                "section_type": section_type,
                "source_segment_ids": source_ids,
                "time_range": [start_t, end_t],
            })

        return {
            "title": preamble_data.get("title", ""),
            "content_type": preamble_data.get("content_type", self.content_type),
            "core_theme": preamble_data.get("core_theme", ""),
            "theme_keywords": preamble_data.get("theme_keywords", []),
            "speakers": preamble_data.get("speakers", []),
            "structure_rationale": preamble_data.get("structure_rationale", ""),
            "preamble": preamble_data.get("preamble", {}),
            "chapters": chapters,
            "writing_style": f"{self.narrative_type}_{self.voice_format}",
            "plan_variant": f"compose_{self.narrative_type}_{self.voice_format}_{self.content_type}",
            "elapsed_seconds": elapsed,
        }


# ============ Node Entry Point ============

async def compose_node(state: PodBookState) -> Dict[str, Any]:
    """通用成稿节点。输入：transcription → 输出：composed_content"""
    print(f"[compose] 开始处理任务: {state['task_id']}")
    print(f"[compose] content_type={state.get('content_type', 'business')}, "
          f"narrative_type={state.get('narrative_type', 'opinion')}, "
          f"voice_format={state.get('voice_format', 'dialogue')}")

    transcription = state.get("transcription")
    if not transcription:
        raise ValueError("缺少转写结果，无法进行内容成稿")

    segments = transcription.get("segments", [])
    if not segments:
        raise ValueError("转写结果中没有 segments 数据")

    segments = _filter_trivial_speakers(segments)

    metadata_context = build_metadata_context(state)
    engine = ComposeEngine(state, metadata_context=metadata_context)
    composed_content = await engine.run(segments)

    user_title = (state.get("user_title") or "").strip()
    if user_title:
        print(f"[compose] 使用用户指定书名: {user_title} "
              f"(AI 生成: {composed_content.get('title', '')})")
        composed_content["title"] = user_title

    chapter_count = len(composed_content.get("chapters", []))
    total_chars = sum(len(c.get("content", "")) for c in composed_content.get("chapters", []))
    print(f"[compose] 完成: {chapter_count} 章, "
          f"{total_chars} 字, 耗时 {composed_content.get('elapsed_seconds', 0):.1f}s")

    return {
        "composed_content": composed_content,
        "current_stage": WorkflowStage.ENRICH.value,
    }
