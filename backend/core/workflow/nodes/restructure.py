"""
板块二：内容重组 Agent

自主决策架构（ReAct + 目标驱动）：
- 将一期播客内容萃取并填入对应板块框架
- 不同类型播客使用不同板块框架
- Agent 自主拆解任务、选择工具、判断完成

Agent 循环：THINK → ACT → OBSERVE → (循环或完成)

工具集：
1. analyze_content - 分析内容类型和特征
2. load_template - 加载对应类型的板块框架
3. plan_sections - 规划板块（选择哪些板块、每个板块放什么）
4. draft_sections - 撰写所有板块内容（AI重组原文）
5. evaluate_quality - 评估质量
6. revise_section - 修订指定板块
7. finish - 完成任务
"""
import json
from typing import Dict, Any, List, Optional

from core.workflow.state import PodBookState, WorkflowStage
from core.config import settings
from core.services.llm_service import get_llm_service
from core.workflow.knowledge.loader import get_knowledge_loader
from core.workflow.nodes.transcription import build_metadata_context


MAX_AGENT_STEPS = 20


# ============ Tool Descriptions (for Orchestrator) ============

TOOLS_DESCRIPTION = """你有以下工具可以使用：

1. **analyze_content**
   功能：深度分析内容的类型、说话人、主题
   输入：无（自动使用全部片段）
   输出：内容分析报告

2. **load_template**
   功能：加载该播客类型的板块框架模板和参考案例
   输入：{"content_type": "访谈/对谈/独白/圆桌/叙事"}
   输出：板块框架、编辑规范、优秀案例、反面案例

3. **plan_sections**
   功能：规划板块——选择哪些板块、每个板块大致放什么内容
   输入：{"sections": [{"section_type": "...", "planned_title": "...", "content_description": "...", "source_hints": "大约哪些片段"}]}
   输出：板块规划

4. **draft_sections**
   功能：根据规划，为每个板块撰写内容（AI基于原文重组）
   输入：无（自动使用当前规划和全部片段）
   输出：所有板块的草稿内容

5. **evaluate_quality**
   功能：根据编辑规范评估当前结果
   输入：无
   输出：评分、问题列表、改进建议

6. **revise_section**
   功能：修订指定板块
   输入：{"section_type": "...", "issue": "需要修复的问题", "instruction": "修改要求"}
   输出：修订后的板块内容

7. **finish**
   功能：确认任务完成
   输入：无"""


# ============ Orchestrator Prompts ============

ORCHESTRATOR_SYSTEM = """你是一位资深的书籍编辑 Agent。你的任务是将一期播客内容萃取并重组为一本高质量的小册子。
你的核心能力是：在保留原文精华的同时，以实体书的行文方式组织内容。

工作方式：
- 先理解内容，再选框架，再规划板块，再撰写内容，最后检查质量
- 每一步都要有清晰的推理
- 质量不达标就继续修改，直到满意

核心原则：
- 内容来自原文，不凭空编造
- 标题必须具体，禁止用框架名称做标题
- 板块间尽量不重复内容
- 如果某板块找不到足够原文支撑，可以省略

写作风格（最高优先级）：
- 你在写一本实体书/小册子，每个章节的正文必须是连贯的散文
- 章节正文内部严禁插入加粗小标题（如**第一点：xxx**）、编号列表、项目符号
- 不同要点之间用段落换行和过渡句自然衔接
- 需要引用嘉宾原话时使用 > 引用块格式
- 严禁把原始对话原样搬入——必须将口语对话重组为书面叙述"""


ORCHESTRATOR_PROMPT = """【任务】
将 {total_segments} 个播客转写片段组织到板块框架中。

{tools_description}

【当前状态】
{current_state}

【执行历史】
{history}

请思考下一步该做什么，输出严格JSON：
{{
    "thinking": "我的推理过程...",
    "action": "工具名称",
    "action_input": {{}}
}}"""


# ============ Executor Prompts ============

ANALYZE_PROMPT = """请深度分析以下播客转写内容。

{metadata_context}

【内容】
{transcription}

【可选的内容类型参考】
{type_summaries}

请分析并输出JSON：
{{
    "thinking": "分析过程...",
    "content_type": "访谈/对谈/独白/圆桌/叙事",
    "confidence": 0.9,
    "speakers": [
        {{"name": "说话人标识", "role": "角色", "style": "风格", "segment_count": 10}}
    ],
    "core_theme": "一句话核心主题",
    "theme_keywords": ["关键词1", "关键词2"],
    "topics_found": [
        {{"topic": "话题描述", "segments": "大约在片段X-Y", "richness": "丰富/一般/较少"}}
    ],
    "has_stories": true,
    "has_methodology": true,
    "has_debate": false,
    "special_notes": "任何特别注意的内容特点"
}}"""


PLAN_PROMPT = """基于内容分析和板块框架模板，规划本期的板块结构。

【内容分析】
{analysis}

【板块框架模板（{content_type}类）】
{template}

【编辑规范】
{guidelines}

【片段概览】
共 {total_segments} 个片段：
{segments_overview}

请规划板块结构。注意：
1. 根据模板的板块框架选择适合的板块
2. 如果原文缺少某板块的内容，可以省略该板块
3. 可以根据内容特点添加"可选板块"
4. 每个板块的标题必须具体化，不能用框架名称

输出JSON：
{{
    "thinking": "我的规划思路：为什么选这些板块？每个板块放什么？",
    "planned_sections": [
        {{
            "section_type": "introduction",
            "planned_title": "一个具体的标题（非框架名称）",
            "content_plan": "这个板块大致放什么内容",
            "source_segment_hints": "大约用到哪些片段",
            "estimated_words": 800
        }}
    ],
    "skipped_sections": ["section_type1（原因）"],
    "total_estimated_words": 12000
}}"""


DRAFT_PROMPT = """请根据板块规划，为每个板块撰写内容。

{metadata_context}

【板块规划】
{section_plan}

【写作风格：章节内部用散文体】
⚠️ 这是最重要的风格要求。你正在写一本实体书/小册子。

整本小册子分为若干章节（板块），每个章节有自己的标题——这是正常的，也是必须的。
但是，每个章节的**正文内部**必须是连贯的散文，不能再用小标题把正文切成碎片。

**章节正文内部严格禁止：**
- 不得在正文中插入加粗小标题（如 `**第一点：xxx**`、`**诊断一：xxx**`）
- 不得用编号列表或项目符号来组织正文（如 `1. xxx` `- xxx`）
- 不得用 markdown 加粗 `**xxx**` 来标记段落主题
- 严禁把原始对话原样搬入——不能出现「嘉宾A：xxx\n主持人：xxx」这种对话体

**正确的做法：**
将嘉宾的口语表达重组为书面散文叙述。不同要点之间用段落换行和过渡句自然衔接。
如果需要列举几个方面，用"首先…其次…此外…"等文字在散文中展开，不要用列表。
需要引用嘉宾原话时使用 > 引用块格式，并加上"正如张帆所说"等引导语。

【编辑规范要点】
- 内容来自原文，不凭空编造
- 导读和延伸思考可以有较多AI组织的成分
- 案例/故事板块应大量保留原文精华表达，但要重组为书面叙述
- 各板块间尽量不重复同一段内容
- 如果需要在多个板块引用同一内容，后面的板块用"正如前文提到..."概括
- 使用说话人的真实姓名，不要用"说话人1"等代号

【完整转写内容】（带片段编号）
{numbered_segments}

请为每个板块撰写内容，输出JSON：
{{
    "thinking": "撰写过程和取舍...",
    "sections": [
        {{
            "section_type": "introduction",
            "title": "具体标题",
            "content": "完整的板块内容（连贯散文，不含小标题和列表）",
            "source_segment_ids": [0, 1, 5],
            "key_points": ["要点1", "要点2"],
            "word_count": 800
        }}
    ]
}}"""


EVALUATE_PROMPT = """你是一位严苛的出版编辑和质量检查员。请逐项检查以下重组结果。

⚠️ 重要校准：你倾向于打分偏高。请刻意严格。一份"还不错"的稿件应该在 70-79 分，只有真正优秀、几乎无可挑剔的才能到 90+。

【编辑规范】
{guidelines}

【当前内容】
{sections_preview}

【参考：优秀案例特征】
{good_traits}

【参考：常见错误】
{bad_traits}

请按以下 7 个维度逐项检查。每个维度有具体检查项，每发现一个问题就扣分。

---

### 维度零：写作风格（权重 15%，基线 100 分，逐项扣分）

注意：每个章节（板块）有自己的标题是正常的。这里检查的是每个章节**正文内部**的写作风格。

逐条检查：
- [ ] 章节正文内部是否插入了加粗小标题（如 `**第一点：xxx**`）？（每处 -15 分）
- [ ] 章节正文内部是否使用了编号列表或项目符号列表来组织内容？（每处 -10 分）
- [ ] 章节正文是否是连贯的散文？还是像条目化的网络文章或对话实录？（条目化/对话体 -20 分）
- [ ] 是否存在原始对话原样搬入的情况（如"嘉宾A：xxx\n主持人：xxx"的对话体）？（每处 -20 分）
- [ ] 不同观点之间是否用过渡句自然衔接？还是用小标题机械分割？（机械分割 -15 分）

⚠️ 硬性红线：如果某个章节正文内部出现超过 3 处加粗小标题或编号列表，或出现对话体，此维度上限 40 分。

### 维度一：内容准确性（权重 20%，基线 100 分，逐项扣分）
- [ ] 是否存在原文中完全没有的"编造"内容？（每处 -15 分）
- [ ] 引用的观点/数据/案例是否与原文一致？（每处偏差 -8 分）
- [ ] 是否存在过度推断？（每处 -5 分）
- [ ] 说话人的姓名和身份是否正确？（错误 -10 分/处）
⚠️ 硬性红线：如果发现明确编造，此维度上限 60 分。

### 维度二：结构合理性（权重 15%，基线 100 分，逐项扣分）
- [ ] 板块之间是否有逻辑递进关系？（松散 -10 分）
- [ ] 导读是否起到引入作用而非内容概括？（导读实质是摘要 -15 分）
- [ ] 延伸思考是否有深度？（简单重复正文观点 -10 分）
- [ ] 板块数量是否合理（5-7 个）？（过少 -10，过多 -5）

### 维度三：标题质量（权重 10%，基线 100 分，逐项扣分）
- [ ] 是否有标题直接使用框架名称？（如"核心观点"，每个 -20 分）
- [ ] 标题是否具体且有信息量？（笼统的每个 -10 分）
- [ ] 标题长度是否在 5-20 字？（过短或过长每个 -5 分）

### 维度四：去重程度（权重 10%，基线 100 分，逐项扣分）
- [ ] 同一段原文/同一个故事在多个板块中完整重复出现？（每处 -20 分）
- [ ] 同一个观点在多个板块用相似语言表述？（每处 -10 分）
- [ ] 总体阅读是否有"车轱辘话"的感觉？（明显重复感 -15 分）

### 维度五：内容完整性（权重 15%，基线 100 分，逐项扣分）
- [ ] 原文中最重要的 3-5 个核心观点是否都有覆盖？（遗漏一个 -15 分）
- [ ] 原文中的关键故事/案例是否被保留？（遗漏关键故事 -10 分）
- [ ] 板块内容是否过度浅尝辄止？（每处 -5 分）

### 维度六：篇幅均衡性（权重 15%，基线 100 分，逐项扣分）
- [ ] 导读是否在 500-1000 字？（超出范围 -10 分）
- [ ] 核心内容板块是否在 1000-4000 字？（超出范围 -10 分/板块）
- [ ] 延伸思考是否在 800-1500 字？（超出范围 -10 分）
- [ ] 最长板块是否超过总字数的 35%？（超过 -15 分）

---

| 分数段 | 含义 | 典型特征 |
|--------|------|----------|
| 90-100 | 卓越 | 几乎无可挑剔，可直接出版 |
| 80-89  | 良好 | 整体质量高，有少量小问题 |
| 70-79  | 及格 | 基本完成任务但有明显改进空间 |
| 60-69  | 不及格 | 存在结构性问题或多处明显错误 |
| <60    | 差   | 严重问题，需要重做 |

输出JSON：
{{
    "thinking": "逐维度检查过程，必须引用具体证据...",
    "scores": {{
        "writing_style": {{"score": 0, "violations": ["具体问题"], "detail": "扣分明细"}},
        "content_accuracy": {{"score": 0, "violations": ["具体问题"], "detail": "扣分明细"}},
        "structure": {{"score": 0, "violations": ["具体问题"], "detail": "扣分明细"}},
        "title_quality": {{"score": 0, "violations": ["具体问题"], "detail": "扣分明细"}},
        "deduplication": {{"score": 0, "violations": ["具体问题"], "detail": "扣分明细"}},
        "completeness": {{"score": 0, "violations": ["具体问题"], "detail": "扣分明细"}},
        "balance": {{"score": 0, "violations": ["具体问题"], "detail": "扣分明细"}}
    }},
    "weighted_score": 0,
    "passed": false,
    "issues": ["最严重的问题1", "最严重的问题2"],
    "suggestions": ["最优先的改进建议1", "改进建议2"]
}}

评分权重：写作风格15% | 准确性20% | 结构15% | 标题10% | 去重10% | 完整性15% | 均衡15%
通过标准（全部满足才算通过）：
- 加权总分 ≥ 82
- 内容准确性 ≥ 85（硬性要求）
- 写作风格 ≥ 70（硬性要求，正文中不得出现大量小标题、列表或对话体）
- 任何单项 ≥ 65
- issues 中没有"红线"级别的问题"""


REVISE_PROMPT = """请修订以下板块。

【需要修订的板块】
类型：{section_type}
当前标题：{current_title}
当前内容：
{current_content}

【问题】
{issue}

【修改要求】
{instruction}

【写作风格要求（最高优先级）】
修订后的正文必须是连贯的散文体。严格禁止：
- 在正文中插入加粗小标题（如 `**xxx**`）
- 使用编号列表或项目符号
- 保留原始对话体（如"嘉宾：xxx\n主持人：xxx"）
正确做法：用段落换行和过渡句衔接不同观点。引用嘉宾原话时使用 > 引用块。

【原始转写片段（可参考）】
{relevant_segments}

请输出修订后的内容（JSON）：
{{
    "thinking": "修改思路...",
    "title": "修订后的标题",
    "content": "修订后的完整内容（连贯散文，不含小标题和列表）",
    "source_segment_ids": [引用的片段],
    "key_points": ["要点"],
    "changes_made": ["具体修改了什么"]
}}"""


# ============ ReAct Agent ============

class RestructureAgent:

    def __init__(self, metadata_context: str = ""):
        self.llm = get_llm_service()
        self.knowledge = get_knowledge_loader()
        self.segments: List[Dict] = []
        self.metadata_context = metadata_context
        self.state: Dict[str, Any] = {}
        self.history: List[Dict] = []
        self.step_count = 0

    async def run(self, segments: List[Dict]) -> Dict:
        self.segments = segments
        self.state = {
            "total_segments": len(segments),
            "analysis": None,
            "template_loaded": False,
            "content_type": None,
            "plan": None,
            "sections": None,
            "evaluation": None,
            "finished": False,
        }

        print(f"[restructure-agent] 启动，共 {len(segments)} 个片段")

        while self.step_count < MAX_AGENT_STEPS and not self.state["finished"]:
            self.step_count += 1
            print(f"\n[restructure-agent] === Step {self.step_count} ===")

            action = await self._think()
            if not action:
                break

            action_name = action.get("action", "")
            action_input = action.get("action_input") or {}
            thinking = action.get("thinking", "")

            print(f"[restructure-agent] 思考: {thinking[:120]}...")
            print(f"[restructure-agent] 动作: {action_name}")

            result = await self._execute(action_name, action_input)

            self.history.append({
                "step": self.step_count,
                "thinking": thinking,
                "action": action_name,
                "result_summary": self._summarize(result),
            })

            if action_name == "finish":
                self.state["finished"] = True

        return self._build_final_output()

    # ---------- Orchestrator ----------

    async def _think(self) -> Optional[Dict]:
        prompt = ORCHESTRATOR_PROMPT.format(
            total_segments=len(self.segments),
            tools_description=TOOLS_DESCRIPTION,
            current_state=self._format_state(),
            history=self._format_history(),
        )
        try:
            return await self.llm.generate_json(
                prompt=prompt,
                system_prompt=ORCHESTRATOR_SYSTEM,
                temperature=0.3,
                model=settings.LLM_MODEL,
                label="编排器决策",
            )
        except Exception as e:
            print(f"[restructure-agent] 编排器错误: {e}")
            return self._fallback_action()

    # ---------- Tool Executors ----------

    async def _execute(self, action: str, action_input: Dict) -> Dict:
        executors = {
            "analyze_content": self._exec_analyze,
            "load_template": self._exec_load_template,
            "plan_sections": self._exec_plan,
            "draft_sections": self._exec_draft,
            "evaluate_quality": self._exec_evaluate,
            "revise_section": self._exec_revise,
            "finish": self._exec_finish,
        }
        executor = executors.get(action)
        if not executor:
            return {"error": f"未知工具: {action}"}
        try:
            return await executor(action_input)
        except Exception as e:
            print(f"[restructure-agent] 工具 {action} 执行失败: {e}")
            return {"error": str(e)}

    async def _exec_analyze(self, _input: Dict) -> Dict:
        text = self._format_segments_for_analysis()
        type_summaries = self.knowledge.load_all_templates_summary()

        result = await self.llm.generate_json(
            prompt=ANALYZE_PROMPT.format(
                metadata_context=self.metadata_context,
                transcription=text,
                type_summaries=type_summaries,
            ),
            system_prompt="你是一位资深的内容分析专家。",
            temperature=0.3,
            model=settings.LLM_MODEL,
            label="分析内容结构",
        )
        self.state["analysis"] = result
        return result

    async def _exec_load_template(self, action_input: Dict) -> Dict:
        content_type = action_input.get("content_type", "")
        if not content_type and self.state.get("analysis"):
            content_type = self.state["analysis"].get("content_type", "访谈")

        template = self.knowledge.load_template(content_type)
        guidelines = self.knowledge.load_guidelines()
        good_cases = self.knowledge.load_good_cases()
        bad_cases = self.knowledge.load_bad_cases()

        self.state["template_loaded"] = True
        self.state["content_type"] = content_type
        # 缓存这些知识供后续工具使用
        self.state["_template"] = template
        self.state["_guidelines"] = guidelines
        self.state["_good_cases"] = good_cases
        self.state["_bad_cases"] = bad_cases

        return {
            "content_type": content_type,
            "template_loaded": True,
            "template_preview": template[:300] + "...",
        }

    async def _exec_plan(self, action_input: Dict) -> Dict:
        # 如果 agent 直接给了 plan
        if action_input.get("sections") or action_input.get("planned_sections"):
            plan = action_input
            self.state["plan"] = plan
            sections = plan.get("sections") or plan.get("planned_sections", [])
            return {"plan_accepted": True, "section_count": len(sections)}

        content_type = self.state.get("content_type", "访谈")
        template = self.state.get("_template", self.knowledge.load_template(content_type))
        guidelines = self.state.get("_guidelines", self.knowledge.load_guidelines())
        overview = self._create_segments_overview()

        result = await self.llm.generate_json(
            prompt=PLAN_PROMPT.format(
                analysis=json.dumps(self.state.get("analysis", {}), ensure_ascii=False, indent=2),
                content_type=content_type,
                template=template,
                guidelines=guidelines,
                total_segments=len(self.segments),
                segments_overview=overview,
            ),
            system_prompt="你是一位专业的书籍结构设计师。",
            temperature=0.3,
            model=settings.LLM_MODEL,
            label="规划板块结构",
        )
        self.state["plan"] = result
        return result

    async def _exec_draft(self, _input: Dict) -> Dict:
        plan = self.state.get("plan", {})
        numbered = self._format_segments_with_ids()

        result = await self.llm.generate_json(
            prompt=DRAFT_PROMPT.format(
                metadata_context=self.metadata_context,
                section_plan=json.dumps(plan, ensure_ascii=False, indent=2),
                numbered_segments=numbered,
            ),
            system_prompt="你是一位资深的出版编辑，擅长将播客口语内容重组为高质量的书稿。"
                         "你的核心能力是以实体书的行文方式组织内容。"
                         "每个章节内部用连贯的散文叙述，严禁插入加粗小标题、列表或对话体。"
                         "保留原文精华表达，不凭空编造。",
            temperature=0.5,
            model=settings.LLM_MODEL,
            max_tokens=65536,
            label="起草书稿",
        )

        sections = result.get("sections", [])
        self.state["sections"] = sections
        self.state["evaluation"] = None  # 重置评估
        return {"drafted": True, "section_count": len(sections)}

    async def _exec_evaluate(self, _input: Dict) -> Dict:
        sections = self.state.get("sections")
        if not sections:
            return {"error": "还没有内容，无法评估"}

        guidelines = self.state.get("_guidelines", self.knowledge.load_guidelines())
        good_cases = self.state.get("_good_cases", "")
        bad_cases = self.state.get("_bad_cases", "")

        result = await self.llm.generate_json(
            prompt=EVALUATE_PROMPT.format(
                guidelines=guidelines,
                sections_preview=self._format_sections_preview(),
                good_traits=self._extract_traits(good_cases, "优秀"),
                bad_traits=self._extract_traits(bad_cases, "问题"),
            ),
            system_prompt="你是一位严格的质量检查编辑。",
            temperature=0.2,
            model=settings.LLM_MODEL,
            label="评估质量",
        )
        self.state["evaluation"] = result
        return result

    async def _exec_revise(self, action_input: Dict) -> Dict:
        section_type = action_input.get("section_type", "")
        issue = action_input.get("issue", "")
        instruction = action_input.get("instruction", "")

        # 找到要修订的板块
        sections = self.state.get("sections", [])
        target = None
        target_idx = -1
        for i, sec in enumerate(sections):
            if sec.get("section_type") == section_type:
                target = sec
                target_idx = i
                break

        if not target:
            return {"error": f"未找到板块: {section_type}"}

        # 获取相关片段
        source_ids = target.get("source_segment_ids", [])
        relevant = self._get_segments_text(source_ids)

        result = await self.llm.generate_json(
            prompt=REVISE_PROMPT.format(
                section_type=section_type,
                current_title=target.get("title", ""),
                current_content=target.get("content", ""),
                issue=issue,
                instruction=instruction,
                relevant_segments=relevant,
            ),
            system_prompt="你是一位精益求精的出版编辑。修订后的正文必须是连贯散文，严禁小标题、列表和对话体。",
            temperature=0.4,
            model=settings.LLM_MODEL,
            label=f"修订板块: {section_type}",
        )

        # 更新板块
        if result.get("content"):
            sections[target_idx] = {
                "section_type": section_type,
                "title": result.get("title", target.get("title", "")),
                "content": result["content"],
                "source_segment_ids": result.get("source_segment_ids", source_ids),
                "key_points": result.get("key_points", target.get("key_points", [])),
            }
            self.state["sections"] = sections
            self.state["evaluation"] = None

        return {"revised": True, "section_type": section_type, "changes": result.get("changes_made", [])}

    async def _exec_finish(self, _input: Dict) -> Dict:
        return {"finished": True}

    # ---------- Formatting ----------

    def _format_segments_for_analysis(self) -> str:
        lines = []
        for i, seg in enumerate(self.segments):
            speaker = seg.get("speaker", "未知")
            text = seg.get("text", "")
            lines.append(f"[{i}]【{speaker}】{text}")
        return "\n\n".join(lines)

    def _format_segments_with_ids(self) -> str:
        lines = []
        for i, seg in enumerate(self.segments):
            speaker = seg.get("speaker", "未知")
            text = seg.get("text", "")
            lines.append(f"[{i}]【{speaker}】{text}")
        return "\n\n".join(lines)

    def _create_segments_overview(self) -> str:
        total = len(self.segments)
        if total <= 15:
            return self._format_segments_with_ids()

        lines = []
        sample_ranges = [
            (0, min(4, total)),
            (total // 2 - 2, total // 2 + 2),
            (max(total - 4, 0), total),
        ]
        for start, end in sample_ranges:
            for i in range(start, min(end, total)):
                seg = self.segments[i]
                text = seg.get("text", "")[:100]
                lines.append(f"[{i}]【{seg.get('speaker', '')}】{text}...")
            lines.append("...")
        return "\n".join(lines)

    def _format_sections_preview(self) -> str:
        sections = self.state.get("sections", [])
        lines = []
        for sec in sections:
            content = sec.get("content", "")
            preview = content[:300] + "..." if len(content) > 300 else content
            lines.append(f"\n## [{sec.get('section_type')}] {sec.get('title', '')}")
            lines.append(f"字数: {len(content)}")
            lines.append(f"来源片段: {sec.get('source_segment_ids', [])}")
            lines.append(f"内容预览: {preview}")
        return "\n".join(lines)

    def _get_segments_text(self, segment_ids: List[int]) -> str:
        lines = []
        for sid in segment_ids:
            if 0 <= sid < len(self.segments):
                seg = self.segments[sid]
                lines.append(f"[{sid}]【{seg.get('speaker', '')}】{seg.get('text', '')}")
        return "\n\n".join(lines) if lines else "（无指定片段）"

    def _extract_traits(self, text: str, keyword: str) -> str:
        if not text:
            return "无"
        lines = text.split("\n")
        result = []
        capturing = False
        for line in lines:
            if keyword in line and ("###" in line or "**" in line):
                capturing = True
                result.append(line)
                continue
            elif line.startswith("---") and capturing:
                capturing = False
            elif line.startswith("## ") and capturing:
                capturing = False
            elif capturing:
                result.append(line)
        return "\n".join(result[:30]).strip() if result else "无"

    def _format_state(self) -> str:
        lines = [f"总片段数: {self.state['total_segments']}"]

        if self.state["analysis"]:
            a = self.state["analysis"]
            lines.append(f"内容分析: 完成 → 类型={a.get('content_type')}, 主题={a.get('core_theme')}")
            topics = a.get("topics_found", [])
            if topics:
                lines.append(f"  发现 {len(topics)} 个话题")
        else:
            lines.append("内容分析: 未完成")

        if self.state["template_loaded"]:
            lines.append(f"板块模板: 已加载（{self.state.get('content_type', '')}类）")
        else:
            lines.append("板块模板: 未加载")

        if self.state["plan"]:
            planned = self.state["plan"].get("planned_sections", [])
            lines.append(f"板块规划: 完成 → {len(planned)} 个板块")
            for p in planned:
                lines.append(f"  - [{p.get('section_type')}] {p.get('planned_title', '')}")
        else:
            lines.append("板块规划: 未完成")

        if self.state["sections"]:
            lines.append(f"内容撰写: 完成 → {len(self.state['sections'])} 个板块")
            for sec in self.state["sections"]:
                content = sec.get("content", "")
                lines.append(f"  - [{sec.get('section_type')}] {sec.get('title', '')} ({len(content)}字)")
        else:
            lines.append("内容撰写: 未完成")

        if self.state["evaluation"]:
            ev = self.state["evaluation"]
            lines.append(f"质量评估: 完成 → 总分={ev.get('weighted_score', 0)}, "
                        f"通过={'是' if ev.get('passed') else '否'}")
            if ev.get("issues"):
                for issue in ev["issues"][:3]:
                    lines.append(f"  问题: {issue}")
        else:
            lines.append("质量评估: 未完成")

        return "\n".join(lines)

    def _format_history(self) -> str:
        if not self.history:
            return "（刚开始，还没有执行任何操作）"
        lines = []
        for h in self.history[-5:]:
            lines.append(f"Step {h['step']}: [{h['action']}] {h['result_summary']}")
        if len(self.history) > 5:
            lines.insert(0, f"（共 {len(self.history)} 步，显示最近5步）")
        return "\n".join(lines)

    def _summarize(self, result: Dict) -> str:
        if "error" in result:
            return f"错误: {result['error']}"
        if result.get("finished"):
            return "任务完成"
        if result.get("content_type"):
            return f"分析完成: {result.get('content_type')}类, {result.get('core_theme', '')}"
        if result.get("template_loaded"):
            return f"已加载{result.get('content_type', '')}类模板"
        if result.get("plan_accepted"):
            return f"规划已确认: {result.get('section_count', 0)} 个板块"
        if result.get("planned_sections"):
            return f"规划完成: {len(result['planned_sections'])} 个板块"
        if result.get("drafted"):
            return f"撰写完成: {result.get('section_count', 0)} 个板块"
        if result.get("weighted_score") is not None:
            return f"评估: {result.get('weighted_score')}分, {'通过' if result.get('passed') else '未通过'}"
        if result.get("revised"):
            return f"已修订: {result.get('section_type')}"
        return str(result)[:100]

    # ---------- Fallback ----------

    def _fallback_action(self) -> Dict:
        if not self.state["analysis"]:
            return {"thinking": "降级：先分析内容", "action": "analyze_content", "action_input": {}}
        if not self.state["template_loaded"]:
            return {"thinking": "降级：加载模板", "action": "load_template", "action_input": {}}
        if not self.state["plan"]:
            return {"thinking": "降级：规划板块", "action": "plan_sections", "action_input": {}}
        if not self.state["sections"]:
            return {"thinking": "降级：撰写内容", "action": "draft_sections", "action_input": {}}
        if not self.state["evaluation"]:
            return {"thinking": "降级：评估质量", "action": "evaluate_quality", "action_input": {}}
        return {"thinking": "降级：完成", "action": "finish", "action_input": {}}

    # ---------- Output ----------

    def _build_final_output(self) -> Dict:
        analysis = self.state.get("analysis", {})
        sections = self.state.get("sections", [])
        evaluation = self.state.get("evaluation", {})
        plan = self.state.get("plan", {})

        # 构建 sections 输出
        output_sections = []
        for sec in sections:
            # 收集来源片段的完整数据
            source_ids = sec.get("source_segment_ids", [])
            source_segments = [
                self.segments[sid]
                for sid in source_ids
                if 0 <= sid < len(self.segments)
            ]

            # 时间范围
            start_time = source_segments[0].get("start_time", 0) if source_segments else 0
            end_time = source_segments[-1].get("end_time", 0) if source_segments else 0

            output_sections.append({
                "section_type": sec.get("section_type", ""),
                "title": sec.get("title", ""),
                "content": sec.get("content", ""),
                "key_points": sec.get("key_points", []),
                "source_segment_ids": source_ids,
                "source_segments": source_segments,
                "time_range": [start_time, end_time],
                "word_count": len(sec.get("content", "")),
            })

        result = {
            "content_type": analysis.get("content_type", "访谈"),
            "core_theme": analysis.get("core_theme", ""),
            "theme_keywords": analysis.get("theme_keywords", []),
            "speakers": analysis.get("speakers", []),
            "sections": output_sections,
            # 下游兼容：sections 映射为 chapters
            "chapters": [
                {
                    "title": sec["title"],
                    "content": sec["content"],
                    "key_points": sec["key_points"],
                    "summary": sec.get("section_type", ""),
                    "segments": sec.get("source_segments", []),
                    "segment_ids": sec["source_segment_ids"],
                    "time_range": sec["time_range"],
                }
                for sec in output_sections
            ],
            "agent_steps": self.step_count,
            "quality_score": evaluation.get("weighted_score", 0) if evaluation else 0,
        }

        return result


# ============ Node Entry Point ============

async def restructure_node(state: PodBookState) -> Dict[str, Any]:
    print(f"[restructure] 开始处理任务: {state['task_id']}")

    transcription = state.get("transcription")
    if not transcription:
        raise ValueError("缺少转写结果，无法进行内容重组")

    segments = transcription.get("segments", [])
    if not segments:
        raise ValueError("转写结果中没有segments数据")

    metadata_context = build_metadata_context(state)
    agent = RestructureAgent(metadata_context=metadata_context)
    chapters = await agent.run(segments)

    print(f"[restructure] Agent 完成: {len(chapters.get('sections', []))} 个板块, "
          f"{chapters.get('agent_steps', 0)} 步, 质量分={chapters.get('quality_score', 0)}")

    return {
        "chapters": chapters,
        "current_stage": WorkflowStage.EXTRACTION.value,
    }
