"""
节点：访谈体内容成稿（Compose Interview）

三阶段处理，最大限度保留原始内容：
  1. 结构规划：识别话题边界，划分章节，分配片段（连续分配，不遗漏）
  2. 逐章编辑：并行处理每个章节，清理口语但保留全部实质对话
  3. 前言生成：基于全部章节生成标题、导语和内容提要

输出格式与现有 composed_content 兼容，对接 extraction / annotation / typeset。
"""
import asyncio
import time
from pathlib import Path
from typing import Dict, Any, List

from core.workflow.state import PodBookState, WorkflowStage
from core.workflow.nodes.transcription import build_metadata_context
from core.config import settings
from core.services.llm_service import get_llm_service

KNOWLEDGE_DIR = Path(__file__).resolve().parent.parent / "knowledge" / "examples"
STYLE_GUIDE_PATH = KNOWLEDGE_DIR / "interview_style_guide.md"
CASES_DIR = KNOWLEDGE_DIR / "interview_cases"

MAX_CHAPTER_CHARS = 25000
SUB_BATCH_TARGET_CHARS = 18000


# ============ Knowledge Loading ============

def _load_style_guide() -> str:
    if STYLE_GUIDE_PATH.exists():
        return STYLE_GUIDE_PATH.read_text(encoding="utf-8")
    print(f"[compose_interview] 警告: 未找到风格指南 {STYLE_GUIDE_PATH}")
    return ""


def _load_few_shot_cases() -> List[str]:
    cases = []
    if not CASES_DIR.exists():
        print(f"[compose_interview] 警告: 未找到案例文件夹 {CASES_DIR}")
        return cases
    for p in sorted(CASES_DIR.glob("*.md")):
        try:
            cases.append(p.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"[compose_interview] 加载案例 {p.name} 失败: {e}")
    print(f"[compose_interview] 加载了 {len(cases)} 个 few-shot 案例")
    return cases


# ============ Phase 1: Structure Planning ============

PLAN_SYSTEM = (
    "你是一位拥有十年经验的资深播客内容编辑。"
    "你处理过数百期不同类型的播客（访谈、圆桌、独白），"
    "擅长快速识别长对话中的话题边界、逻辑转折和叙事节奏，"
    "并将松散的对话重新组织为逻辑清晰、阅读流畅的主题板块。"
)

PLAN_PROMPT = """# 背景

你正在将一期播客转写文本整理为一本访谈体书稿。第一步需要规划章节结构。

# 播客元数据

{metadata_context}

# 转写内容（共 {total_segments} 个片段）

{numbered_segments}

# 任务

通读全部转写片段，将其划分为若干主题章节。

# 约束

1. 按话题将片段划分为若干章节（通常 4-15 个，视内容丰富度而定，相关话题尽量合并）
2. 片段必须**连续分配**：每个章节包含一段连续的片段范围 [start_id, end_id]（含两端）
3. **跳过无关片段**：以下类型的片段不应收录为章节，无论它们出现在开头、中间还是结尾：
   - 节目开场白、录制形式说明、广告口播、赞助商鸣谢
   - 布景/灯光/设备/收音等技术讨论
   - 推荐下一个/下期嘉宾、下期节目预告
   - 听众互动环节（抽奖、福利、Q&A 征集）
   - 节目订阅/关注/点赞引导、社交媒体推广
   - 纯闲聊寒暄（与访谈主题完全无关的日常闲谈）
   第一个章节不必从片段 0 开始，最后一个章节不必到最后一个片段结束——从实质内容开始，到实质内容结束即可。如果上述无关内容出现在两段实质内容之间，直接跳过即可（此时前后章节不必强制连续）
4. **实质内容连续衔接**：在实质内容范围内，章节之间应尽量无缝衔接（前一章 end_segment_id + 1 = 下一章 start_segment_id）。唯一的例外是第 3 条所述的中间无关片段——跳过它们后，前后章节之间允许存在间隙
5. **章节标题**：像书的章节名一样简洁，一个短句点明核心即可（10字以内最佳，不超过20字）。不要用顿号或逗号堆砌多个关键词。
   - 好："差点倒闭的那一年"、"为什么押注设计"、"十年大厂游历记"
   - 坏："融资误区、补贴战与团队在崩溃边缘的管理"（关键词堆砌）
   - 坏："大学与第一次创业：信息管理、辩论实践与'做产品经理'的觉醒"（冒号+堆砌）
6. 按对话的自然时间顺序排列

# 思维链

请按以下步骤思考（内部推理，不需要输出过程）：
1. 快速扫描所有片段，标记明显的话题切换点（例如主持人提出新问题、嘉宾转向新话题）
2. 识别开头和结尾的无关片段（节目介绍、广告、闲聊），确定实质内容的起止范围
3. 在实质内容范围内，按话题聚类相邻片段，形成章节
4. 为每个章节拟一个描述性标题
5. 检查：是否有遗漏的实质片段？是否混入了冗余环节（推荐嘉宾、广告、下期预告等）？标题是否简短有力？是否避免了千篇一律的冒号格式？章节数是否控制在合理范围（4-15）？

# 输出格式

严格 JSON：

```json
{{
    "chapters": [
        {{
            "title": "章节标题",
            "start_segment_id": 0,
            "end_segment_id": 25,
            "description": "该章节讨论了什么（一句话）"
        }}
    ]
}}
```"""


# ============ Phase 2: Per-Chapter Compose ============

CHAPTER_SYSTEM = (
    "你是一位拥有十年经验的资深播客内容编辑。"
    "你的专长是将口语对话整理为高质量的书面访谈体，"
    "在清理口语杂质的同时最大限度地保留原始内容的完整性和对话感。"
    "你绝不删减实质内容，绝不添加原文没有的信息，绝不插入 AI 套话。"
)

MONOLOGUE_CHAPTER_SYSTEM = (
    "你是一位拥有十年经验的资深播客内容编辑。"
    "你的专长是将单人独白式播客整理为高质量的书面散文体，"
    "在清理口语杂质的同时最大限度地保留原始内容的完整性和叙述深度。"
    "你绝不删减实质内容，绝不添加原文没有的信息，绝不插入 AI 套话。"
)

CHAPTER_PROMPT = """# 背景

你正在将一期播客转写文本整理为访谈体书稿。当前任务是编辑其中一个章节的原始片段。

# 章节：{chapter_title}

# 任务

将以下原始播客片段整理为书面访谈体文本，保留全部实质对话内容。

# 约束

1. **保留全部实质内容**：说话人表达的每一个观点、事实、经历、数据、案例、比喻、幽默都必须保留。绝对不允许省略或概括
2. **清理口语填充词**：删除"嗯""啊""那个""就是说""对对对""然后然后""你知道吗"等纯填充词
3. **修复语法**：修复明显的口语语病使句子通顺，但保留说话人的表达习惯和个人风格
4. **合并碎片**：同一说话人连续的短句/碎片必须合并为完整段落，不要删除内容
5. **段落原则**：同一说话人的一次发言以 `**说话人名字：**` 开头，可以包含一个或多个段落。较长的发言（约 200 字以上）应在语义过渡处自然分段——例如话题小转折、从观点转入举例、从一个论点过渡到下一个。后续段落不重复说话人标签，通过缩进延续。避免将所有内容堆在一个大段中，也不要逐句碎段
6. **说话人标签**：每段对话以 `**说话人名字：**` 开头
7. **关键标注**：用 **加粗** 标注该章节内有启发性、可迁移的观点或方法论（每章 3-6 处）。不要加粗个人经历叙事和感叹。加粗的范围要保持意思完整——可以是一句话，也可以是连续的两三句话，只要它们共同表达一个完整的道理就一起加粗
8. **禁止添加**：不要添加任何原文没有的内容、评论、过渡语或编者按
9. **禁止删减**：不要因为觉得"不重要"就删掉某段对话。只要是实质性表达，全部保留

# 编辑规范

{style_guide_excerpt}

# 原始片段

{chapter_segments}

# 输出

直接输出整理后的访谈体文本。以说话人标签开头，不要包含章节标题。"""


MONOLOGUE_CHAPTER_PROMPT = """# 背景

你正在将一期单人独白式播客（solo podcast）的转写文本整理为书面散文体书稿。当前任务是编辑其中一个章节的原始片段。

# 章节：{chapter_title}

# 任务

将以下原始播客片段整理为书面散文体文本，保留全部实质内容。

# 约束

1. **保留全部实质内容**：说话人表达的每一个观点、事实、经历、数据、案例、比喻、幽默都必须保留。绝对不允许省略或概括
2. **清理口语填充词**：删除"嗯""啊""那个""就是说""然后然后""你知道吗"等纯填充词
3. **修复语法**：修复明显的口语语病使句子通顺，但保留说话人的表达习惯和个人风格
4. **合并碎片**：连续的短句/碎片必须合并为完整段落，不要删除内容
5. **散文体格式**：这是单人播客，只有一位说话人。输出纯散文体，**不要加说话人标签**（如 `**某某：**`），直接以正文开始。章节首段以 `**说话人名字：**` 开头标明作者身份，后续段落无需重复
6. **自然分段**：按语义自然分段，保持适中的段落长度。一段围绕一个要点或一组紧密相关的论述展开；当一段较长（约 200 字以上），应在语义过渡处拆分，如从阐述转入举例、从一个论点过渡到下一个。避免堆成大段，也避免逐句碎段
7. **关键标注**：用 **加粗** 标注该章节内有启发性、可迁移的观点或方法论（每章 3-6 处）。不要加粗个人经历叙事和感叹。加粗的范围要保持意思完整
8. **禁止添加**：不要添加任何原文没有的内容、评论、过渡语或编者按
9. **禁止删减**：不要因为觉得"不重要"就删掉某段内容。只要是实质性表达，全部保留

# 编辑规范

{style_guide_excerpt}

# 原始片段

{chapter_segments}

# 输出

直接输出整理后的散文体文本。以说话人标签开头（仅第一段），不要包含章节标题。"""


CHAPTER_CONTINUATION_PROMPT = """# 背景

你正在将一期播客转写文本整理为访谈体书稿。当前章节内容较长，正在分批处理。这是该章节的后续部分。

# 章节：{chapter_title}

# 前文末尾（仅供衔接参考，请勿重复输出这部分内容）

{previous_tail}

# 任务

继续整理以下原始播客片段，保持与前文一致的风格和格式。**直接从新内容开始**，不要重复前文已有的内容。

# 约束

1. **保留全部实质内容**：说话人表达的每一个观点、事实、经历、数据、案例、比喻、幽默都必须保留。绝对不允许省略或概括
2. **清理口语填充词**：删除"嗯""啊""那个""就是说""对对对""然后然后""你知道吗"等纯填充词
3. **修复语法**：修复明显的口语语病使句子通顺，但保留说话人的表达习惯和个人风格
4. **合并碎片**：同一说话人连续的短句/碎片必须合并为完整段落，不要删除内容
5. **段落原则**：同一说话人的一次发言以 `**说话人名字：**` 开头，可以包含一个或多个段落。较长的发言（约 200 字以上）应在语义过渡处自然分段——例如话题小转折、从观点转入举例、从一个论点过渡到下一个。后续段落不重复说话人标签，通过缩进延续。避免将所有内容堆在一个大段中，也不要逐句碎段
6. **说话人标签**：每段对话以 `**说话人名字：**` 开头
7. **关键标注**：用 **加粗** 标注有启发性、可迁移的观点或方法论（适量）
8. **禁止添加**：不要添加任何原文没有的内容、评论、过渡语或编者按
9. **禁止删减**：不要因为觉得"不重要"就删掉某段对话。只要是实质性表达，全部保留
10. **衔接自然**：确保与前文末尾自然衔接，语气和格式保持一致

# 编辑规范

{style_guide_excerpt}

# 原始片段

{chapter_segments}

# 输出

直接输出整理后的访谈体文本。从新内容开始，不要重复前文。"""


MONOLOGUE_CONTINUATION_PROMPT = """# 背景

你正在将一期单人独白式播客的转写文本整理为书面散文体书稿。当前章节内容较长，正在分批处理。这是该章节的后续部分。

# 章节：{chapter_title}

# 前文末尾（仅供衔接参考，请勿重复输出这部分内容）

{previous_tail}

# 任务

继续整理以下原始播客片段，保持与前文一致的风格和格式。**直接从新内容开始**，不要重复前文已有的内容。

# 约束

1. **保留全部实质内容**：说话人表达的每一个观点、事实、经历、数据、案例、比喻、幽默都必须保留。绝对不允许省略或概括
2. **清理口语填充词**：删除"嗯""啊""那个""就是说""然后然后""你知道吗"等纯填充词
3. **修复语法**：修复明显的口语语病使句子通顺，但保留说话人的表达习惯和个人风格
4. **合并碎片**：连续的短句/碎片必须合并为完整段落，不要删除内容
5. **散文体格式**：**不要加说话人标签**，直接输出正文段落
6. **自然分段**：按语义自然分段，保持适中的段落长度。较长段落（约 200 字以上）在语义过渡处拆分
7. **关键标注**：用 **加粗** 标注有启发性、可迁移的观点或方法论（适量）
8. **禁止添加**：不要添加任何原文没有的内容、评论、过渡语或编者按
9. **禁止删减**：不要因为觉得"不重要"就删掉某段内容。只要是实质性表达，全部保留
10. **衔接自然**：确保与前文末尾自然衔接，语气和格式保持一致

# 编辑规范

{style_guide_excerpt}

# 原始片段

{chapter_segments}

# 输出

直接输出整理后的散文体文本。从新内容开始，不要重复前文。"""


# ============ Phase 3: Preamble Generation ============

PREAMBLE_SYSTEM = (
    "你是一位拥有十年经验的资深播客内容编辑。"
    "你特别擅长从长篇访谈中提炼核心洞察，撰写引人入胜的导语和精准的内容提要。"
    "你的文字自然、专业、克制，绝不插入 AI 套话（如「值得注意的是」「综上所述」「让我们」）。"
)

PREAMBLE_PROMPT = """# 背景

你已经完成了一期播客访谈的全文编辑，现在需要为这篇访谈体文章生成标题、导语和内容提要。

# 播客元数据

{metadata_context}

# 文章全文

{full_text}

# 任务

基于上述全文，生成：标题、导语（lead_paragraph）、内容提要（summary_bullets）。

# 约束

1. **标题**：这是一本书的封面书名，要求：
   - 标题要能**统领全书**——读者看到标题就能大致知道整本书讲的是什么领域、什么话题，而不是只反映书中某一个小观点
   - 格式为"主标题：副标题"，主标题 ≤10 字，副标题 ≤20 字
   - 主标题要**具体**，让人能想象到书的内容，避免空泛抽象的概念拼接（如"认知决定终局""速度即壁垒"这类放到任何书上都成立的万金油句式）
   - 副标题进一步说明这本书具体在聊什么，给读者明确预期
   - 想象这本书摆在书店里，标题要让目标读者一眼被吸引、拿起来翻
   - 禁止叙事型标题（如"从大厂跳出的人""一个创业者的故事"）
   - 禁止出现节目名、嘉宾姓名
2. **导语**（lead_paragraph）：1-4 段，融合嘉宾背景、核心精华、阅读钩子。用引人注目的事实或悬念开篇。不要用"本文整理自..."开头
3. **内容提要**（summary_bullets）：5-15 条，格式 `**核心观点/洞察短语：** 展开论证`
   - 重要：内容提要是**提炼核心观点和洞察**，不是概述各章节讲了什么
   - 每一条应该是一个**独立的论点、判断或洞察**，读者仅看提要就能获得最有价值的认知
   - **排除个人经历叙事**：如"嘉宾小时候频繁迁徙""在某公司的工作经历"等个人故事背景不属于核心观点，不应出现在提要中
   - 聚焦于：行业判断、方法论、产品/商业洞察、可迁移的认知框架
   - **直接陈述观点本身**，不要用"他认为""他判断""让他把…定位为"等第三人称转述句式。写成客观判断句，如"AI 的核心价值在于…"而非"他认为 AI 的核心价值在于…"

# 思维链

请按以下步骤思考（内部推理，不需要输出过程）：
1. 通读全文，识别访谈中最核心的 5-15 个独立观点/洞察/判断
2. 对每个观点，提炼为一个短语 + 1-3句展开论证，确保是"论点"而非"内容概述"
3. 从全文中找到最引人注目的事实、数据或反直觉断言，用它来构思导语的开篇钩子
4. 将嘉宾背景、核心精华和钩子融合为 1-3 段自然流畅的导语
5. 自检：内容提要的每一条是否都是独立观点？导语是否有吸引力？是否有 AI 套话残留？

# Few-shot 参考

以下是优秀的标题、导语和内容提要写法，请学习其风格（特别注意内容提要如何提炼核心观点）：

{few_shot_preambles}

# 输出格式

严格 JSON：

```json
{{
    "title": "文章标题",
    "core_theme": "一句话核心主题",
    "theme_keywords": ["关键词1", "关键词2", "关键词3"],
    "preamble": {{
        "lead_paragraph": "导语",
        "summary_bullets": ["**核心观点：** 展开论证"]
    }}
}}
```"""


# ============ Composer ============

class InterviewComposer:
    """三阶段访谈体成稿：结构规划 → 逐章编辑 → 前言生成。"""

    MAX_CONCURRENT_CHAPTERS = 4

    def __init__(self, metadata_context: str = "", is_monologue: bool = False):
        self.llm = get_llm_service()
        self.metadata_context = metadata_context
        self.is_monologue = is_monologue
        self._style_guide = _load_style_guide()
        self._cases = _load_few_shot_cases()

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
        """提取风格指南中与逐章编辑相关的部分（口语清理 + 标签 + 标注规则）"""
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
        """将案例文档拆分为 (前言部分, 正文部分)。"""
        import re
        match = re.search(r'\n访谈(?:节选|完整实录|全文|正文)\n', case_text)
        if match:
            return case_text[:match.start()].strip(), case_text[match.end():].strip()
        return case_text[:3000].strip(), ""

    def _extract_preamble_examples(self) -> str:
        """提取案例的前言部分（标题 + 内容提要 + 简介）作为 Phase 3 few-shot。"""
        if not self._cases:
            return "（无参考案例）"
        examples = []
        for i, case in enumerate(self._cases, 1):
            preamble, _ = self._split_case(case)
            examples.append(f"--- 案例 {i} ---\n{preamble}")
        return "\n\n".join(examples)


    # ---------- Phase 1: Plan ----------

    async def _plan_structure(self, segments: List[Dict], replan_context: str = "") -> Dict:
        prompt = PLAN_PROMPT.format(
            metadata_context=self.metadata_context or "（无）",
            total_segments=len(segments),
            last_segment_id=len(segments) - 1,
            numbered_segments=self._format_segments(segments),
        )
        if replan_context:
            prompt += replan_context
        print(f"[compose_interview] Phase 1 prompt: {len(prompt)} 字符")

        plan = await self.llm.generate_json(
            prompt=prompt,
            system_prompt=PLAN_SYSTEM,
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
        """校验规划结果：允许首尾跳过，也允许中间跳过无关片段"""
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
                    print(f"[compose_interview] 警告: 章节 {i+1} start={s} 与前章重叠"
                          f"（前章 end={prev_end}），自动修正")
                    ch["start_segment_id"] = expected_start
                elif s > expected_start:
                    gap = s - expected_start
                    print(f"[compose_interview] 章节 {i+1} 跳过 {gap} 个无关片段 "
                          f"({expected_start}-{s-1})")

            if ch["start_segment_id"] > ch["end_segment_id"]:
                print(f"[compose_interview] 警告: 章节 {i+1}「{ch.get('title', '')}」"
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
        print(f"[compose_interview] 规划校验: {len(chapters)} 章, "
              f"覆盖 {covered} 段 (片段 {first_start}-{last_end}), "
              f"跳过: 开头 {skipped_head}, 中间 {skipped_mid}, 结尾 {skipped_tail}")

    def _estimate_chapter_chars(self, segments: List[Dict], chapter: Dict) -> int:
        """估算某个章节涵盖的原始片段总字符数。"""
        start_id = chapter["start_segment_id"]
        end_id = chapter["end_segment_id"]
        total = 0
        for i in range(start_id, min(end_id + 1, len(segments))):
            total += len(segments[i].get("text", ""))
        return total

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
            print(f"[compose_interview]   [{idx+1}/{total}] 章节「{title}」"
                  f"过大 ({chapter_chars} 字符 > {MAX_CHAPTER_CHARS})，启用分批处理")
            return await self._compose_chapter_in_parts(idx, total, chapter_plan, segments)

        seg_text = self._format_range(segments, start_id, end_id)
        seg_count = end_id - start_id + 1

        if not seg_text.strip():
            print(f"[compose_interview]   [{idx+1}/{total}] ⚠ 章节「{title}」"
                  f"(片段 {start_id}-{end_id}) 原始片段为空，跳过")
            return ""

        chapter_tmpl = MONOLOGUE_CHAPTER_PROMPT if self.is_monologue else CHAPTER_PROMPT
        chapter_sys = MONOLOGUE_CHAPTER_SYSTEM if self.is_monologue else CHAPTER_SYSTEM

        prompt = chapter_tmpl.format(
            chapter_title=title,
            style_guide_excerpt=self._style_guide_excerpt(),
            chapter_segments=seg_text,
        )

        mode_label = "独白" if self.is_monologue else "访谈"
        print(f"[compose_interview]   [{idx+1}/{total}] {title} "
              f"(片段 {start_id}-{end_id}, {seg_count}段, {mode_label}体, prompt {len(prompt)}字符)")

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
                    print(f"[compose_interview]   [{idx+1}] 重试: {e}")
                    await asyncio.sleep(15)
                else:
                    raise

        print(f"[compose_interview]   [{idx+1}/{total}] 完成: {len(result)} 字")
        return result.strip()

    # ---------- Phase 2b: Large Chapter Sub-batch ----------

    def _split_chapter_segments(
        self, segments: List[Dict], start_id: int, end_id: int,
        target_chars: int = SUB_BATCH_TARGET_CHARS,
    ) -> List[tuple]:
        """将过大章节的片段拆分为字符数不超过 target_chars 的子批次。"""
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
        """分批处理过大章节：拆成子批次，每批独立调用 LLM 后拼接。"""
        title = chapter_plan.get("title", f"章节 {idx+1}")
        start_id = chapter_plan["start_segment_id"]
        end_id = chapter_plan["end_segment_id"]

        sub_batches = self._split_chapter_segments(segments, start_id, end_id)
        print(f"[compose_interview]   [{idx+1}/{total}] 拆分为 {len(sub_batches)} 个子批次")

        parts: List[str] = []
        for bi, (sub_start, sub_end) in enumerate(sub_batches):
            seg_text = self._format_range(segments, sub_start, sub_end)
            seg_count = sub_end - sub_start + 1

            if not seg_text.strip():
                continue

            chapter_tmpl = MONOLOGUE_CHAPTER_PROMPT if self.is_monologue else CHAPTER_PROMPT
            cont_tmpl = MONOLOGUE_CONTINUATION_PROMPT if self.is_monologue else CHAPTER_CONTINUATION_PROMPT
            chapter_sys = MONOLOGUE_CHAPTER_SYSTEM if self.is_monologue else CHAPTER_SYSTEM

            if bi == 0:
                prompt = chapter_tmpl.format(
                    chapter_title=title,
                    style_guide_excerpt=self._style_guide_excerpt(),
                    chapter_segments=seg_text,
                )
            else:
                prev_tail = parts[-1][-800:] if parts else ""
                prompt = cont_tmpl.format(
                    chapter_title=title,
                    style_guide_excerpt=self._style_guide_excerpt(),
                    previous_tail=prev_tail,
                    chapter_segments=seg_text,
                )

            print(f"[compose_interview]     子批次 {bi+1}/{len(sub_batches)} "
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
                        print(f"[compose_interview]     子批次 {bi+1} 重试: {e}")
                        await asyncio.sleep(15)
                    else:
                        raise

            parts.append(result.strip())
            print(f"[compose_interview]     子批次 {bi+1}/{len(sub_batches)} "
                  f"完成: {len(result)} 字")

        combined = "\n\n".join(p for p in parts if p)
        print(f"[compose_interview]   [{idx+1}/{total}] 分批合并完成: "
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

        prompt = PREAMBLE_PROMPT.format(
            metadata_context=self.metadata_context or "（无）",
            full_text=full_text,
            few_shot_preambles=self._extract_preamble_examples(),
        )

        print(f"[compose_interview] Phase 3 prompt: {len(prompt)} 字符")

        return await self.llm.generate_json(
            prompt=prompt,
            system_prompt=PREAMBLE_SYSTEM,
            temperature=0.5,
            model=settings.LLM_PREMIUM_MODEL,
            max_tokens=65536,
            timeout=300,
            label="生成前言",
        )

    # ---------- Run ----------

    async def run(self, segments: List[Dict]) -> Dict:
        total_start = time.time()

        # Phase 1
        print(f"[compose_interview] Phase 1: 规划章节结构 ({len(segments)} 个片段)...")
        plan = await self._plan_structure(segments)
        chapter_plans = plan.get("chapters", [])
        for i, cp in enumerate(chapter_plans, 1):
            print(f"[compose_interview]   章节 {i}: "
                  f"[{cp['start_segment_id']}-{cp['end_segment_id']}] {cp['title']}")
        print(f"[compose_interview] Phase 1 完成: {len(chapter_plans)} 个章节")

        # Check for oversized chapters and re-plan once if needed
        oversized = []
        for i, cp in enumerate(chapter_plans):
            chars = self._estimate_chapter_chars(segments, cp)
            if chars > MAX_CHAPTER_CHARS:
                oversized.append((i + 1, cp.get("title", ""), chars))

        if oversized:
            replan_hint = (
                f"\n\n# 重要补充约束\n\n"
                f"上一次规划中，以下章节包含过多内容，必须进一步拆分为更小的子话题章节：\n"
            )
            for num, title, chars in oversized:
                replan_hint += (
                    f"- 章节 {num}「{title}」: 约 {chars} 字符"
                    f"（上限 {MAX_CHAPTER_CHARS}）\n"
                )
            replan_hint += (
                f"\n请确保每个章节的原始文本量不超过 {MAX_CHAPTER_CHARS} 字符。"
                f"将过大的章节按其内部的子话题/子议题拆分为多个独立章节。\n"
            )
            print(f"[compose_interview] 检测到 {len(oversized)} 个过大章节，重新规划...")
            plan = await self._plan_structure(segments, replan_context=replan_hint)
            chapter_plans = plan.get("chapters", [])
            for i, cp in enumerate(chapter_plans, 1):
                print(f"[compose_interview]   章节 {i}: "
                      f"[{cp['start_segment_id']}-{cp['end_segment_id']}] {cp['title']}")
            print(f"[compose_interview] 重新规划完成: {len(chapter_plans)} 个章节")

        # Phase 2
        print(f"[compose_interview] Phase 2: 逐章编辑 "
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
            print(f"[compose_interview] 过滤掉 {removed} 个空章节")
        chapter_plans, chapter_contents = zip(*valid_pairs)
        chapter_plans = list(chapter_plans)
        chapter_contents = list(chapter_contents)

        total_chars = sum(len(c) for c in chapter_contents)
        print(f"[compose_interview] Phase 2 完成: 总计 {total_chars} 字")

        # Phase 3
        print(f"[compose_interview] Phase 3: 生成标题和导语...")
        preamble_data = await self._generate_preamble(chapter_plans, chapter_contents)
        print(f"[compose_interview] Phase 3 完成: {preamble_data.get('title', '(无标题)')}")

        elapsed = time.time() - total_start
        print(f"[compose_interview] 全部完成, 总耗时 {elapsed:.1f}s")

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

            section_type = "monologue_section" if self.is_monologue else "interview_section"
            chapters.append({
                "title": cp.get("title", ""),
                "content": content,
                "key_points": [cp.get("description", "")],
                "section_type": section_type,
                "source_segment_ids": source_ids,
                "time_range": [start_t, end_t],
            })

        style = "monologue" if self.is_monologue else "interview"
        return {
            "title": preamble_data.get("title", ""),
            "content_type": preamble_data.get("content_type", "独白体" if self.is_monologue else "访谈体"),
            "core_theme": preamble_data.get("core_theme", ""),
            "theme_keywords": preamble_data.get("theme_keywords", []),
            "speakers": preamble_data.get("speakers", []),
            "structure_rationale": preamble_data.get("structure_rationale", ""),
            "preamble": preamble_data.get("preamble", {}),
            "chapters": chapters,
            "writing_style": style,
            "plan_variant": "compose_interview_v2_full",
            "elapsed_seconds": elapsed,
        }


# ============ Node Entry Point ============

TRIVIAL_SPEAKER_RATIO = 0.03  # 占比 < 3% 的说话人视为片头/片尾等无关音频


def _filter_trivial_speakers(segments: List[Dict]) -> List[Dict]:
    """过滤掉占比极小的说话人（如片头片尾旁白、音乐歌词等）。

    这些"说话人"通常是 ASR 把播客 intro/outro 音频识别为独立的人声，
    不应影响内容类型判断（单人/多人），也不应出现在正文和封面中。
    """
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
    }

    if not trivial:
        return segments

    filtered = [seg for seg in segments if seg.get("speaker", "未知") not in trivial]
    for sp in trivial:
        print(f"[compose_interview] 过滤微量说话人 '{sp}' "
              f"({speaker_chars[sp]} 字, {speaker_chars[sp]/total_chars:.1%})")
    return filtered if filtered else segments


async def compose_interview_node(state: PodBookState) -> Dict[str, Any]:
    """访谈体内容成稿节点。输入：transcription → 输出：composed_content"""
    print(f"[compose_interview] 开始处理任务: {state['task_id']}")

    transcription = state.get("transcription")
    if not transcription:
        raise ValueError("缺少转写结果，无法进行内容成稿")

    segments = transcription.get("segments", [])
    if not segments:
        raise ValueError("转写结果中没有 segments 数据")

    segments = _filter_trivial_speakers(segments)

    unique_speakers = set(seg.get("speaker", "未知") for seg in segments)
    is_monologue = len(unique_speakers) <= 1
    if is_monologue:
        print(f"[compose_interview] 检测到单人播客（独白体）: 说话人={unique_speakers}")
    else:
        print(f"[compose_interview] 多人播客（访谈体）: {len(unique_speakers)} 位说话人")

    metadata_context = build_metadata_context(state)
    composer = InterviewComposer(metadata_context=metadata_context, is_monologue=is_monologue)
    composed_content = await composer.run(segments)

    user_title = (state.get("user_title") or "").strip()
    if user_title:
        print(f"[compose_interview] 使用用户指定书名: {user_title} (AI 生成: {composed_content.get('title', '')})")
        composed_content["title"] = user_title

    chapter_count = len(composed_content.get("chapters", []))
    total_chars = sum(len(c.get("content", "")) for c in composed_content.get("chapters", []))
    print(f"[compose_interview] 完成: {chapter_count} 章, "
          f"{total_chars} 字, 耗时 {composed_content.get('elapsed_seconds', 0):.1f}s")

    return {
        "composed_content": composed_content,
        "current_stage": WorkflowStage.ENRICH.value,
    }
