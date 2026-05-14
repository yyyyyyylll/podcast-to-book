"""
板块二（替代方案）：内容重组 - 单 Prompt 方案

与 restructure.py (ReAct Agent 方案) 做 A/B test。

架构：
- 生产模式：路由模型（类型判断）→ 单 Prompt 直接输出最终结果
- 开发模式：生成 → AI评估 → AI分析问题 → AI改 Prompt → 回归 → 循环

核心区别：
- Plan A (restructure.py): 运行时多步推理（20-37 次 LLM 调用，400-1000秒）
- Plan B (本文件): 编译期优化 Prompt，运行时 1-2 次调用（预计 30-60秒）
"""
import asyncio
import json
import os
import time
from dataclasses import dataclass, field, asdict
from typing import Dict, Any, List, Optional

from core.workflow.state import PodBookState, WorkflowStage
from core.config import settings
from core.services.llm_service import get_llm_service
from core.workflow.knowledge.loader import get_knowledge_loader
from core.workflow.nodes.transcription import build_metadata_context


# ============ Configuration ============

PROMPT_STORE_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "..", "prompts", "restructure_alt",
)

# 止损线：优化循环最大轮次 / 单 prompt 方案相对 Agent 方案的最低可接受分数比
MAX_OPTIMIZATION_ROUNDS = 10
STOP_LOSS_SCORE_RATIO = 0.90  # 若 N 轮后仍不到 Agent 方案分数的 90%，终止


# ============ Data Classes ============

@dataclass
class EvaluationResult:
    scores: Dict[str, Dict[str, Any]]
    weighted_score: float
    passed: bool
    issues: List[str]
    suggestions: List[str]
    raw: Dict[str, Any] = field(default_factory=dict)


@dataclass
class PromptVersion:
    version: int
    system_prompt: str
    prompt_template: str
    created_at: float = field(default_factory=time.time)
    avg_score: Optional[float] = None
    pass_rate: Optional[float] = None
    case_scores: Dict[str, float] = field(default_factory=dict)


@dataclass
class TestCase:
    case_id: str
    segments: List[Dict]
    metadata_context: str = ""
    expected_content_type: Optional[str] = None
    reference_output: Optional[Dict] = None


@dataclass
class OptimizationResult:
    final_prompt: PromptVersion
    history: List[PromptVersion]
    total_rounds: int
    converged: bool
    stop_reason: str


# ============ Router Prompt ============

ROUTER_PROMPT = """请判断以下播客转写内容属于哪种类型。

【可选类型及特征】
{type_summaries}

【内容概览】
{content_overview}

只输出JSON：
{{
    "thinking": "判断过程...",
    "content_type": "访谈/对谈/独白/圆桌/叙事",
    "confidence": 0.9
}}"""


# ============ Generator Prompts (optimization target) ============

DEFAULT_GENERATOR_SYSTEM = (
    "你是一位资深的书籍编辑，擅长将播客口语内容重组为高质量的小册子。"
    "你的核心能力是：在保留原文精华的同时，以实体书的行文方式组织内容。"
    "每个章节内部用连贯的散文叙述，不在章节正文里插入加粗小标题或列表。"
)

DEFAULT_GENERATOR_PROMPT = """【任务】
将以下播客转写内容重组为一本高质量的小册子。你需要自主完成三件事：
1. 理解内容的特点和结构
2. 设计最适合这期内容的板块结构（板块数量、类型、顺序都由你决定）
3. 为每个板块撰写内容

【结构设计原则】
- 板块结构应完全由内容驱动，不同的播客应该产出不同的结构
- 以下模板仅供参考，你可以采用、修改、组合或完全自创板块类型
- 一般建议 5-7 个板块，但如果内容简短或高度聚焦，3-4 个也完全可以
- 一般以一个导读开篇，但如果内容本身开头就足够吸引人，也可以直接进入正文
- 是否需要收尾板块（延伸思考/总结）取决于内容本身，不强制要求
- 每个板块应有明确的功能定位，板块之间应有逻辑递进关系

【板块框架参考（仅供参考，非强制）】
以下是{content_type}类播客常见的板块框架，你可以从中选取适合的，也可以自行设计：
{template}

【写作风格：章节内部用散文体】
⚠️ 这是最重要的风格要求。你正在写一本实体书/小册子。

整本小册子分为若干章节（板块），每个章节有自己的标题——这是正常的，也是必须的。
但是，每个章节的**正文内部**必须是连贯的散文，不能再用小标题把正文切成碎片。

**章节正文内部严格禁止：**
- 不得在正文中插入加粗小标题（如 `**第一点：xxx**`、`**诊断一：xxx**`、`**核心理念：xxx**`）
- 不得用编号列表或项目符号来组织正文（如 `1. xxx` `- xxx` `* xxx`）
- 不得用 markdown 加粗 `**xxx**` 来标记段落主题

**正确的做法：**
章节内部的不同要点之间，用段落换行和过渡句自然衔接。如果需要列举几个方面，用"首先…其次…此外…"等文字在散文中展开，不要用列表。需要引用嘉宾原话时使用 > 引用块格式。

【内容写作要求】
1. 标题必须具体，反映本板块的实际内容，禁止使用框架名称（如"核心观点"、"案例分析"）作为标题
2. 内容来自原文，不凭空编造。导读和收尾可以有较多 AI 组织的成分
3. 涉及故事/案例的板块应大量保留原文表达，保留叙事细节和情感
4. 同一段原文只在一个板块中完整呈现，其他板块引用时用一句话概括
5. 各板块之间应有内在联系，形成连贯的阅读体验
6. 导读不剧透核心内容和结论，用问题或场景制造好奇心
7. 如果原文中某类内容不足以支撑一个独立板块，不要强行填充
8. 使用说话人的真实姓名，不要用"说话人1"等代号

【编辑规范】
{guidelines}

【优秀案例特征参考】
{good_case_traits}

【常见错误警示】
{bad_case_traits}

{metadata_context}

【完整转写内容】（共 {total_segments} 个片段）
{numbered_segments}

请输出完整的重组结果，严格JSON格式：
{{
    "content_type": "{content_type}",
    "core_theme": "一句话核心主题",
    "theme_keywords": ["关键词1", "关键词2", "关键词3"],
    "speakers": [
        {{"name": "说话人名字", "role": "角色", "style": "表达风格"}}
    ],
    "structure_rationale": "简要说明为什么选择这样的板块结构（1-2句话）",
    "sections": [
        {{
            "section_type": "你自定义的板块类型标识（英文，如 opening, key_debate, founders_story 等）",
            "title": "具体的板块标题（非框架名称，5-20字）",
            "content": "完整的板块内容（直接可读的文本）",
            "source_segment_ids": [引用的片段编号],
            "key_points": ["要点1", "要点2"]
        }}
    ]
}}"""


# ============ Evaluator Prompt ============

EVALUATOR_PROMPT = """你是一位以严苛闻名的出版社总编辑。你的职业声誉建立在"从不轻易认可"之上。请对照原始转写内容，逐项检查以下重组结果。

⚠️⚠️⚠️ 评分校准（极其重要，必须严格执行）：

你有一个已知的系统性偏差：倾向于给出过高的分数。为了矫正这个偏差，请遵守以下铁律：

1. **满分几乎不可能**：任何维度给出 100 分，意味着你认为该维度"完美到无法再改进一个字"。这在现实中极其罕见。如果你发现自己给了多个 100 分，请立刻停下来重新审视——你几乎一定遗漏了问题。
2. **每个维度至少找到一个可改进之处**：即使是非常优秀的稿件，也必须在每个维度找到至少一个具体的、可操作的改进点。找不到问题不代表没有问题，而是你审查得不够仔细。
3. **95+ 分等于"可以直接送去印刷厂"**：如果你打出 95 分以上，意味着这份稿件你愿意署上自己的名字出版。请在 thinking 中明确回答：你真的愿意吗？
4. **合理的分数分布**：一份"相当不错"的稿件，各维度通常在 80-92 分。只有各方面都近乎完美，总分才能到 90+。大多数 AI 首次生成的稿件总分应在 70-85 分。
5. **不要因为"整体感觉好"就放松标准**：必须逐条检查每一个检查项，用证据说话。

【编辑规范】
{guidelines}

【原始转写内容（用于核实准确性）】
{source_segments}

【待评估的重组结果】
{output_preview}

请按以下 7 个维度逐项检查。每个维度有具体检查项，每发现一个问题就扣分。

---

### 维度零：写作风格（权重 15%，基线 100 分，逐项扣分）

注意：每个章节（板块）有自己的标题是正常的，不扣分。这里检查的是每个章节**正文内部**是否使用了不该有的小标题和列表。

逐条检查：
- [ ] 章节正文内部是否插入了加粗小标题（如 `**第一点：xxx**`、`**诊断一：xxx**`、`**核心理念：xxx**`）？（每处 -15 分）
- [ ] 章节正文内部是否使用了编号列表或项目符号列表来组织内容？（每处 -10 分。注意：> 引用块格式不扣分）
- [ ] 章节正文读起来是否像连贯的散文？还是像条目化的网络文章？（条目化 -20 分）
- [ ] 不同观点之间是否用过渡句自然衔接？还是用小标题机械分割？（机械分割 -15 分）

⚠️ 硬性红线：如果某个章节正文内部出现超过 3 处加粗小标题或编号列表，此维度上限 40 分。

### 维度一：内容准确性（权重 20%，基线 100 分，逐项扣分）

逐条检查：
- [ ] 是否存在原文中完全没有的"编造"内容？（每处 -15 分）
- [ ] 引用的观点/数据/案例是否与原文一致？（每处偏差 -8 分）
- [ ] source_segment_ids 是否合理？随机抽查 2-3 个板块，其引用的片段编号对应的原文是否与板块内容相关？（不相关 -10 分/板块）
- [ ] 是否存在过度推断？（将原文的试探性表述断言化，每处 -5 分）
- [ ] 说话人的姓名和身份是否正确？（错误 -10 分/处）

⚠️ 硬性红线：如果发现任何一处明确编造（原文完全没有的事实或数据），此维度上限 60 分。

### 维度二：结构合理性（权重 15%，基线 100 分，逐项扣分）

逐条检查：
- [ ] 板块类型是否与内容类型的框架模板匹配？（不匹配 -15 分）
- [ ] 板块之间是否有逻辑递进关系？还是各自独立像拼凑的？（松散 -10 分）
- [ ] 导读是否起到引入作用而非内容概括？（如果导读实质是摘要、把核心结论都说了 -15 分）
- [ ] 延伸思考是否有深度？还是简单重复正文观点？（重复 -10 分）
- [ ] 板块数量是否在 5-7 个合理范围？（过少 -10，过多 -5）
- [ ] 板块之间的过渡是否自然？读者能否顺畅地从一个板块读到下一个？（生硬 -5 分）
- [ ] 整体结构是否有"叙事弧线"（开头吸引、中间深入、结尾升华）？（平铺直叙 -8 分）

### 维度三：标题质量（权重 10%，基线 100 分，逐项扣分）

逐条检查：
- [ ] 是否有标题直接使用框架名称？（如"核心观点"、"案例分析"，每个 -20 分）
- [ ] 标题是否具体且有信息量？（笼统模糊的每个 -10 分）
- [ ] 标题风格是否统一？（风格混乱 -10 分）
- [ ] 标题长度是否在 5-20 字？（过短或过长每个 -5 分）
- [ ] 标题是否能让读者仅看目录就知道这期讲什么？（不能 -10 分）

### 维度四：去重程度（权重 10%，基线 100 分，逐项扣分）

逐条检查：
- [ ] 是否有同一段原文/同一个故事在多个板块中完整重复出现？（每处 -20 分）
- [ ] 是否有同一个观点在多个板块用相似语言表述？（每处 -10 分）
- [ ] 后续板块引用前文内容时，是否做了概括处理（"正如前文提到..."）？（未处理 -5 分/处）
- [ ] 总体阅读感受是否有"车轱辘话"的感觉？（明显重复感 -15 分）

### 维度五：内容完整性（权重 15%，基线 100 分，逐项扣分）

请对照原始转写内容逐一检查（这是最需要仔细核对的维度）：
- [ ] 原文中最重要的 3-5 个核心观点是否都有覆盖？（遗漏一个核心观点 -15 分）
- [ ] 原文中的关键故事/案例是否被保留？（遗漏关键故事 -10 分）
- [ ] 是否存在大段高价值原文被完全忽略的情况？（每处 -8 分）
- [ ] 板块内容是否过度浅尝辄止？（重要话题只一笔带过 -5 分/处）
- [ ] 原文中生动的比喻、类比、金句是否被保留？（遗漏有价值的表达 -3 分/处）
- [ ] 嘉宾的思考过程、认知转变是否被充分呈现？还是只保留了结论？（只有结论没有过程 -8 分/处）
- [ ] 嘉宾提到的具体数据、案例细节是否被保留？还是被笼统概括了？（细节丢失 -5 分/处）

### 维度六：篇幅均衡性（权重 15%，基线 100 分，逐项扣分）

逐条检查：
- [ ] 导读是否在 500-1000 字？（超出范围 -10 分）
- [ ] 核心内容板块是否在 1000-4000 字？（超出范围 -10 分/板块）
- [ ] 延伸思考是否在 800-1500 字？（超出范围 -10 分）
- [ ] 最长板块是否超过总字数的 35%？（超过 -15 分）
- [ ] 各板块之间篇幅比例是否合理？（极度不均衡 -10 分）

---

### 分数锚定参考

| 分数段 | 含义 | 典型特征 | 出现频率 |
|--------|------|----------|----------|
| 95-100 | 大师级 | 可以直接送印刷厂，几乎不需要人工修改 | 极罕见（<5%的稿件） |
| 88-94  | 优秀 | 质量很高但仍有 2-3 处可改进 | 少见 |
| 80-87  | 良好 | 整体不错，有若干明显可改进之处 | 常见 |
| 70-79  | 及格 | 基本完成任务，有明显改进空间 | 常见 |
| 60-69  | 不及格 | 存在结构性问题或多处明显错误 | 较少 |
| <60    | 差   | 严重问题，需要重做 | 较少 |

⚠️ 再次提醒：如果你的加权总分超过 92，请在 thinking 中逐一解释每个维度为什么值得这么高的分数。

输出JSON：
{{
    "thinking": "逐维度检查过程，必须引用具体证据（原文片段编号、具体板块名、具体文字）...",
    "scores": {{
        "writing_style": {{"score": 0, "violations": ["具体问题1", "具体问题2"], "detail": "扣分明细"}},
        "content_accuracy": {{"score": 0, "violations": ["具体问题1", "具体问题2"], "detail": "扣分明细"}},
        "structure": {{"score": 0, "violations": ["具体问题"], "detail": "扣分明细"}},
        "title_quality": {{"score": 0, "violations": ["具体问题"], "detail": "扣分明细"}},
        "deduplication": {{"score": 0, "violations": ["具体问题"], "detail": "扣分明细"}},
        "completeness": {{"score": 0, "violations": ["具体问题"], "detail": "扣分明细"}},
        "balance": {{"score": 0, "violations": ["具体问题"], "detail": "扣分明细"}}
    }},
    "weighted_score": 0,
    "passed": false,
    "issues": ["最严重的问题1", "最严重的问题2", "..."],
    "suggestions": ["最优先的改进建议1", "改进建议2"],
    "prompt_improvement_hints": [
        "针对生成 prompt 本身的改进方向（不是改输出，而是改 prompt）"
    ]
}}

评分权重：写作风格15% | 准确性20% | 结构15% | 标题10% | 去重10% | 完整性15% | 均衡15%
通过标准（全部满足才算通过）：
- 加权总分 ≥ 82
- 内容准确性 ≥ 85（硬性要求）
- 写作风格 ≥ 70（硬性要求，正文中不得出现大量小标题和列表）
- 任何单项 ≥ 65
- issues 中没有"红线"级别的问题"""


# ============ Optimizer Prompt ============

OPTIMIZER_SYSTEM = (
    "你是一位 Prompt 工程专家。你的任务是分析 LLM 生成结果的评估反馈，"
    "并据此改进生成 prompt，使输出质量提升。"
    "你需要精准定位问题根源，提出针对性的 prompt 修改策略。"
)

OPTIMIZER_PROMPT = """【任务】根据评估反馈改进生成 Prompt。

【当前 System Prompt】
{current_system}

【当前 User Prompt 模板】
（注意：花括号变量如 {{template}}、{{guidelines}} 等是运行时动态填充的占位符，不可删除或修改）
{current_template}

【评估结果汇总】（{num_cases} 个测试用例）
平均分：{avg_score}
通过率：{pass_rate}

各用例评估详情：
{evaluation_details}

【共性问题】
{common_issues}

【改进要求】
1. 基于评估反馈中的具体问题，修改 system prompt 和 user prompt 模板
2. 必须保留所有 {{...}} 占位符，它们在运行时会被替换为实际内容
3. 重点改进得分最低的维度
4. 改进要具体，不要泛泛而谈（如"提高质量"无意义，"在标题要求中增加示例对比"才有意义）
5. 如果某个问题反复出现，在 prompt 中用显式规则和正反例来强调
6. 避免过度膨胀 prompt，精简优于冗长

输出JSON：
{{
    "analysis": "问题分析：哪些是 prompt 导致的系统性问题，哪些是偶发的",
    "changes": [
        {{"target": "system/template", "what": "具体改了什么", "why": "为什么这样改"}}
    ],
    "improved_system_prompt": "改进后的完整 system prompt",
    "improved_prompt_template": "改进后的完整 user prompt 模板（保留所有占位符）"
}}"""


# ============ Content Router ============

class ContentRouter:
    """轻量级路由模型，快速判断播客内容类型。"""

    def __init__(self):
        self.llm = get_llm_service()
        self.knowledge = get_knowledge_loader()

    async def route(self, segments: List[Dict], metadata_context: str = "") -> str:
        overview = self._build_overview(segments)
        type_summaries = self.knowledge.load_all_templates_summary()

        prompt = ROUTER_PROMPT.format(
            type_summaries=type_summaries,
            content_overview=overview,
        )

        last_error = None
        for attempt in range(1, 4):
            try:
                result = await self.llm.generate_json(
                    prompt=prompt,
                    system_prompt="你是一位内容分类专家，需要快速准确地判断播客类型。",
                    temperature=0.1,
                    model=settings.LLM_MODEL,
                    thinking_budget=0,
                    timeout=120,
                    label="路由: 判断内容类型",
                )
                content_type = result.get("content_type", "访谈")
                print(f"[restructure-alt] 路由判定: {content_type} "
                      f"(置信度: {result.get('confidence', '?')})")
                return content_type
            except (TimeoutError, Exception) as e:
                last_error = e
                print(f"[restructure-alt] 路由第 {attempt} 次失败: {e}")
                if attempt < 3:
                    import asyncio
                    await asyncio.sleep(2 * attempt)

        print(f"[restructure-alt] 路由全部失败，默认返回'访谈'类型")
        return "访谈"

    def _build_overview(self, segments: List[Dict]) -> str:
        total = len(segments)
        lines = []

        speakers = set()
        for seg in segments:
            speakers.add(seg.get("speaker", "未知"))
        lines.append(f"共 {total} 个片段，{len(speakers)} 位说话人: {'、'.join(speakers)}")

        sample_indices = [0, total // 4, total // 2, total * 3 // 4, total - 1]
        for idx in sample_indices:
            if 0 <= idx < total:
                seg = segments[idx]
                text = seg.get("text", "")[:120]
                lines.append(f"[{idx}]【{seg.get('speaker', '')}】{text}...")

        return "\n".join(lines)


# ============ Single-Prompt Generator ============

class SinglePromptGenerator:
    """单次 LLM 调用生成完整重组内容。prompt 可被优化循环替换。"""

    def __init__(
        self,
        system_prompt: str = DEFAULT_GENERATOR_SYSTEM,
        prompt_template: str = DEFAULT_GENERATOR_PROMPT,
        max_tokens: int = 65536,
    ):
        self.llm = get_llm_service()
        self.knowledge = get_knowledge_loader()
        self.system_prompt = system_prompt
        self.prompt_template = prompt_template
        self.max_tokens = max_tokens

    async def generate(
        self,
        segments: List[Dict],
        content_type: str,
        metadata_context: str = "",
    ) -> Dict:
        template = self.knowledge.load_template(content_type)
        guidelines = self.knowledge.load_guidelines()
        good_cases = self.knowledge.load_good_cases()
        bad_cases = self.knowledge.load_bad_cases()

        prompt = self.prompt_template.format(
            content_type=content_type,
            template=template,
            guidelines=guidelines,
            good_case_traits=self._extract_traits(good_cases, "优秀"),
            bad_case_traits=self._extract_traits(bad_cases, "问题"),
            metadata_context=metadata_context,
            total_segments=len(segments),
            numbered_segments=self._format_segments(segments),
        )

        print(f"[restructure-alt] 生成中... (prompt 长度: {len(prompt)} 字符)")
        start = time.time()

        max_retries = 3
        for attempt in range(max_retries):
            try:
                result = await self.llm.generate_json(
                    prompt=prompt,
                    system_prompt=self.system_prompt,
                    temperature=0.5,
                    model=settings.LLM_MODEL,
                    max_tokens=self.max_tokens,
                    timeout=300,
                    label="生成重组内容",
                )
                break
            except Exception as e:
                if attempt < max_retries - 1:
                    wait = 30 * (attempt + 1)
                    print(f"[restructure-alt] 生成失败 (attempt {attempt+1}/{max_retries}): {e}")
                    print(f"[restructure-alt] {wait}秒后重试...")
                    await asyncio.sleep(wait)
                else:
                    raise

        elapsed = time.time() - start
        sections = result.get("sections", [])
        print(f"[restructure-alt] 生成完成: {len(sections)} 个板块, 耗时 {elapsed:.1f}s")

        return result

    def _format_segments(self, segments: List[Dict]) -> str:
        lines = []
        for i, seg in enumerate(segments):
            speaker = seg.get("speaker", "未知")
            text = seg.get("text", "")
            lines.append(f"[{i}]【{speaker}】{text}")
        return "\n\n".join(lines)

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


# ============ Quality Evaluator ============

class QualityEvaluator:
    """评估重组结果的质量。会将原始转写内容传给评估模型，用于核实准确性。"""

    def __init__(self):
        self.llm = get_llm_service()
        self.knowledge = get_knowledge_loader()

    async def evaluate(
        self,
        output: Dict,
        segments: List[Dict],
    ) -> EvaluationResult:
        guidelines = self.knowledge.load_guidelines()

        prompt = EVALUATOR_PROMPT.format(
            guidelines=guidelines,
            source_segments=self._format_source_segments(segments),
            output_preview=self._format_output_preview(output),
        )

        sys_prompt = (
            "你是一位以严苛著称的出版编辑。你的打分历来偏低而精准。"
            "你宁可漏判优秀也不会误判为优秀。请拿出最严格的标准。"
            "注意：thinking 部分请简明扼要，重点放在 JSON 输出上。"
        )

        for attempt in range(1, 3):
            try:
                result = await self.llm.generate_json(
                    prompt=prompt,
                    system_prompt=sys_prompt,
                    temperature=0.2,
                    model=settings.LLM_MODEL,
                    max_tokens=65536,
                    thinking_budget=4096,
                    timeout=300,
                    label="评估重组质量",
                )
                return EvaluationResult(
                    scores=result.get("scores", {}),
                    weighted_score=result.get("weighted_score", 0),
                    passed=result.get("passed", False),
                    issues=result.get("issues", []),
                    suggestions=result.get("suggestions", []),
                    raw=result,
                )
            except (ValueError, Exception) as e:
                print(f"[evaluator] 第 {attempt} 次评估失败: {e}")
                if attempt >= 2:
                    print("[evaluator] 评估失败，返回默认低分结果")
                    return EvaluationResult(
                        scores={},
                        weighted_score=50.0,
                        passed=False,
                        issues=[f"评估失败: {str(e)[:200]}"],
                        suggestions=["评估器输出解析失败，请检查 max_tokens 配置"],
                        raw={},
                    )

    def _format_source_segments(self, segments: List[Dict]) -> str:
        lines = []
        for i, seg in enumerate(segments):
            speaker = seg.get("speaker", "未知")
            text = seg.get("text", "")
            lines.append(f"[{i}]【{speaker}】{text}")
        full = "\n\n".join(lines)
        # 截断过长的原文（评估用，不需要完整保留每个字）
        if len(full) > 40000:
            return full[:40000] + "\n\n...（原文过长，已截断）"
        return full

    def _format_output_preview(self, output: Dict) -> str:
        lines = []
        lines.append(f"内容类型: {output.get('content_type', '未知')}")
        lines.append(f"核心主题: {output.get('core_theme', '未知')}")
        lines.append(f"说话人: {json.dumps(output.get('speakers', []), ensure_ascii=False)}")
        lines.append("")

        for sec in output.get("sections", []):
            content = sec.get("content", "")
            lines.append(f"## [{sec.get('section_type')}] {sec.get('title', '')}")
            lines.append(f"字数: {len(content)}")
            lines.append(f"来源片段: {sec.get('source_segment_ids', [])}")
            lines.append(f"内容:\n{content}\n")

        return "\n".join(lines)


# ============ Prompt Optimizer ============

class PromptOptimizer:
    """分析评估结果，改进生成 prompt。这是"改 prompt 的 AI"。"""

    def __init__(self):
        self.llm = get_llm_service()

    async def improve(
        self,
        current_system: str,
        current_template: str,
        evaluations: List[Dict],
    ) -> Dict[str, str]:
        """
        Args:
            current_system: 当前 system prompt
            current_template: 当前 user prompt 模板
            evaluations: 各测试用例的评估结果列表，每项包含 case_id, score, issues, suggestions, prompt_hints

        Returns:
            {"system_prompt": ..., "prompt_template": ...}
        """
        eval_details = self._format_evaluations(evaluations)
        common_issues = self._find_common_issues(evaluations)
        scores = [e["score"] for e in evaluations]
        avg_score = sum(scores) / len(scores) if scores else 0
        pass_count = sum(1 for e in evaluations if e.get("passed", False))
        pass_rate = f"{pass_count}/{len(evaluations)}"

        prompt = OPTIMIZER_PROMPT.format(
            current_system=current_system,
            current_template=current_template,
            num_cases=len(evaluations),
            avg_score=f"{avg_score:.1f}",
            pass_rate=pass_rate,
            evaluation_details=eval_details,
            common_issues=common_issues,
        )

        max_retries = 3
        for attempt in range(max_retries):
            try:
                result = await self.llm.generate_json(
                    prompt=prompt,
                    system_prompt=OPTIMIZER_SYSTEM,
                    temperature=0.3,
                    model=settings.LLM_MODEL,
                    max_tokens=65536,
                    timeout=300,
                    label="优化提示词",
                )
                break
            except Exception as e:
                if attempt < max_retries - 1:
                    wait = 30 * (attempt + 1)
                    print(f"[optimizer] 调用失败 (attempt {attempt+1}/{max_retries}): {e}")
                    print(f"[optimizer] {wait}秒后重试...")
                    await asyncio.sleep(wait)
                else:
                    raise

        changes = result.get("changes", [])
        print(f"[optimizer] 提出 {len(changes)} 项修改:")
        for c in changes:
            print(f"  - [{c.get('target')}] {c.get('what')}")

        new_template = result.get("improved_prompt_template", current_template)
        new_template = self._sanitize_template(new_template)

        return {
            "system_prompt": result.get("improved_system_prompt", current_system),
            "prompt_template": new_template,
            "analysis": result.get("analysis", ""),
            "changes": changes,
        }

    @staticmethod
    def _sanitize_template(template: str) -> str:
        """确保 prompt 模板中只有合法的占位符花括号。
        
        策略：先把合法占位符临时替换掉，然后转义所有剩余花括号，最后还原占位符。
        """
        VALID_PLACEHOLDERS = [
            "content_type", "template", "guidelines",
            "good_case_traits", "bad_case_traits",
            "metadata_context", "total_segments", "numbered_segments",
        ]

        result = template
        for ph in VALID_PLACEHOLDERS:
            token = f"__PLACEHOLDER_{ph.upper()}__"
            result = result.replace("{" + ph + "}", token)

        result = result.replace("{", "{{").replace("}", "}}")

        for ph in VALID_PLACEHOLDERS:
            token = f"__PLACEHOLDER_{ph.upper()}__"
            result = result.replace(token, "{" + ph + "}")

        return result

    def _format_evaluations(self, evaluations: List[Dict]) -> str:
        lines = []
        for e in evaluations:
            lines.append(f"--- 用例: {e.get('case_id', '?')} | 总分: {e.get('score', 0)} ---")
            for dim, info in e.get("scores", {}).items():
                if isinstance(info, dict):
                    lines.append(f"  {dim}: {info.get('score', 0)} - {info.get('detail', '')}")
            if e.get("issues"):
                lines.append(f"  问题: {'; '.join(e['issues'][:5])}")
            if e.get("prompt_hints"):
                lines.append(f"  Prompt改进方向: {'; '.join(e['prompt_hints'][:3])}")
            lines.append("")
        return "\n".join(lines)

    def _find_common_issues(self, evaluations: List[Dict]) -> str:
        issue_counts: Dict[str, int] = {}
        for e in evaluations:
            for issue in e.get("issues", []):
                key = issue[:50]
                issue_counts[key] = issue_counts.get(key, 0) + 1

        common = sorted(issue_counts.items(), key=lambda x: -x[1])
        if not common:
            return "无明显共性问题"
        return "\n".join(f"- [{count}次] {issue}" for issue, count in common[:10])


# ============ Optimization Runner ============

class OptimizationRunner:
    """
    Prompt 优化循环编排器。

    使用方法：
        runner = OptimizationRunner()
        runner.add_test_case(case_id="demo1", segments=[...], metadata_context="...")
        result = await runner.run(max_rounds=5, target_score=85)
    """

    def __init__(self):
        self.test_cases: List[TestCase] = []
        self.router = ContentRouter()
        self.evaluator = QualityEvaluator()
        self.optimizer = PromptOptimizer()
        self.history: List[PromptVersion] = []

    def add_test_case(
        self,
        case_id: str,
        segments: List[Dict],
        metadata_context: str = "",
        expected_content_type: Optional[str] = None,
    ):
        self.test_cases.append(TestCase(
            case_id=case_id,
            segments=segments,
            metadata_context=metadata_context,
            expected_content_type=expected_content_type,
        ))

    async def run(
        self,
        max_rounds: int = MAX_OPTIMIZATION_ROUNDS,
        target_score: float = 85.0,
        target_pass_rate: float = 0.8,
        initial_system: str = DEFAULT_GENERATOR_SYSTEM,
        initial_template: str = DEFAULT_GENERATOR_PROMPT,
    ) -> OptimizationResult:
        if not self.test_cases:
            raise ValueError("至少需要一个测试用例。使用 add_test_case() 添加。")

        current_system = initial_system
        current_template = initial_template
        self.history = []

        print(f"\n{'='*60}")
        print(f"Prompt 优化循环启动")
        print(f"测试用例: {len(self.test_cases)} 个")
        print(f"目标: 平均分 ≥ {target_score}, 通过率 ≥ {target_pass_rate*100:.0f}%")
        print(f"最大轮次: {max_rounds}")
        print(f"{'='*60}\n")

        for round_num in range(1, max_rounds + 1):
            print(f"\n--- 第 {round_num}/{max_rounds} 轮 ---")

            # Step 1: 用当前 prompt 跑所有测试用例
            generator = SinglePromptGenerator(current_system, current_template)
            evaluations = []
            round_outputs: Dict[str, Dict] = {}

            for tc in self.test_cases:
                print(f"\n[round {round_num}] 测试用例: {tc.case_id}")

                content_type = tc.expected_content_type
                if not content_type:
                    content_type = await self.router.route(tc.segments, tc.metadata_context)

                output = await generator.generate(tc.segments, content_type, tc.metadata_context)
                round_outputs[tc.case_id] = output

                eval_result = await self.evaluator.evaluate(output, tc.segments)

                print(f"  评分: {eval_result.weighted_score}, 通过: {eval_result.passed}")
                if eval_result.issues:
                    print(f"  问题: {eval_result.issues[:3]}")

                evaluations.append({
                    "case_id": tc.case_id,
                    "score": eval_result.weighted_score,
                    "passed": eval_result.passed,
                    "scores": eval_result.scores,
                    "issues": eval_result.issues,
                    "suggestions": eval_result.suggestions,
                    "prompt_hints": eval_result.raw.get("prompt_improvement_hints", []),
                })

            # Step 2: 计算汇总指标
            scores = [e["score"] for e in evaluations]
            avg_score = sum(scores) / len(scores)
            pass_count = sum(1 for e in evaluations if e["passed"])
            pass_rate = pass_count / len(evaluations)

            version = PromptVersion(
                version=round_num,
                system_prompt=current_system,
                prompt_template=current_template,
                avg_score=avg_score,
                pass_rate=pass_rate,
                case_scores={e["case_id"]: e["score"] for e in evaluations},
            )
            self.history.append(version)

            print(f"\n[round {round_num}] 汇总: 平均分={avg_score:.1f}, 通过率={pass_rate*100:.0f}%")

            # Step 3: 存档本轮全部数据（prompt + 评估 + 生成结果）
            save_prompt_version(
                version=version,
                evaluations=evaluations,
                outputs=round_outputs,
            )

            # Step 4: 检查收敛
            if avg_score >= target_score and pass_rate >= target_pass_rate:
                print(f"\n已达标！平均分 {avg_score:.1f} ≥ {target_score}, "
                      f"通过率 {pass_rate*100:.0f}% ≥ {target_pass_rate*100:.0f}%")
                return OptimizationResult(
                    final_prompt=version,
                    history=self.history,
                    total_rounds=round_num,
                    converged=True,
                    stop_reason="target_reached",
                )

            # 检查止损：连续 3 轮无改善
            if len(self.history) >= 3:
                recent_scores = [h.avg_score for h in self.history[-3:]]
                if max(recent_scores) - min(recent_scores) < 1.0:
                    print(f"\n止损：连续 3 轮分数波动 < 1 分，优化可能已收敛于局部最优")
                    return OptimizationResult(
                        final_prompt=version,
                        history=self.history,
                        total_rounds=round_num,
                        converged=False,
                        stop_reason="plateau_detected",
                    )

            if round_num == max_rounds:
                break

            # Step 5: AI 改 prompt
            print(f"\n[round {round_num}] 请求 AI 优化 prompt...")
            improvement = await self.optimizer.improve(
                current_system, current_template, evaluations
            )
            current_system = improvement["system_prompt"]
            current_template = improvement["prompt_template"]

            # 存档优化器的分析（改了什么、为什么改）
            save_prompt_version(
                version=PromptVersion(
                    version=round_num,
                    system_prompt=current_system,
                    prompt_template=current_template,
                ),
                optimizer_analysis={
                    "analysis": improvement.get("analysis", ""),
                    "changes": improvement.get("changes", []),
                    "from_version": round_num,
                    "to_version": round_num + 1,
                },
            )

            print(f"[round {round_num}] Prompt 已更新并存档，进入下一轮回归测试")

        # 达到最大轮次，选历史最佳
        best = max(self.history, key=lambda v: v.avg_score or 0)
        print(f"\n达到最大轮次 {max_rounds}，最佳版本: v{best.version} (平均分: {best.avg_score:.1f})")
        return OptimizationResult(
            final_prompt=best,
            history=self.history,
            total_rounds=max_rounds,
            converged=False,
            stop_reason="max_rounds_reached",
        )


# ============ Prompt Persistence ============

def _render_output_as_markdown(
    output_data: Dict,
    version: "PromptVersion",
    case_id: str,
) -> str:
    """将生成结果渲染为干净的 markdown，去掉所有代码痕迹。"""
    lines = []

    core_theme = output_data.get("core_theme", "")
    keywords = output_data.get("theme_keywords", [])
    speakers = output_data.get("speakers", [])

    # 封面信息
    lines.append(f"# {core_theme}")
    lines.append("")

    if keywords:
        lines.append(f"**关键词**：{'、'.join(keywords)}")
        lines.append("")

    if speakers:
        speaker_parts = []
        for s in speakers:
            name = s.get("name", "")
            role = s.get("role", "")
            speaker_parts.append(f"{name}（{role}）" if role else name)
        lines.append(f"**嘉宾**：{'｜'.join(speaker_parts)}")
        lines.append("")

    lines.append("---")
    lines.append("")

    # 正文章节
    sections = output_data.get("sections", [])
    for section in sections:
        title = section.get("title", "无标题")
        content = section.get("content", "")

        lines.append(f"## {title}")
        lines.append("")
        lines.append(content)
        lines.append("")

    # 页脚：版本信息（小字，不影响阅读）
    lines.append("---")
    lines.append("")
    lines.append(f"*Prompt 版本：v{version.version}*")
    if version.avg_score is not None:
        lines.append(f"*评估得分：{version.avg_score}*")

    return "\n".join(lines)


def _render_prompt_as_markdown(version: "PromptVersion") -> str:
    """将 prompt 版本渲染为可读的 markdown。"""
    lines = []
    lines.append(f"# Prompt v{version.version}")
    lines.append("")

    if version.avg_score is not None:
        pr = version.pass_rate if version.pass_rate is not None else 0
        lines.append(f"**评估得分**：{version.avg_score}　｜　**通过率**：{pr * 100:.0f}%")
        lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("## System Prompt")
    lines.append("")
    lines.append(version.system_prompt)
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## User Prompt Template")
    lines.append("")
    lines.append(version.prompt_template)
    lines.append("")

    return "\n".join(lines)


def _render_evals_as_markdown(
    evaluations: List[Dict],
    version: "PromptVersion",
) -> str:
    """将评估结果渲染为可读的 markdown 报告。"""
    lines = []
    lines.append(f"# 评估报告 — Prompt v{version.version}")
    lines.append("")

    avg = version.avg_score if version.avg_score is not None else 0
    pr = version.pass_rate if version.pass_rate is not None else 0
    lines.append(f"**平均分**：{avg}　｜　**通过率**：{pr * 100:.0f}%")
    lines.append("")
    lines.append("---")
    lines.append("")

    for e in evaluations:
        case_id = e.get("case_id", "?")
        score = e.get("score", 0)
        passed = "通过" if e.get("passed") else "未通过"

        lines.append(f"## 用例：{case_id}")
        lines.append("")
        lines.append(f"**总分**：{score}　｜　**结果**：{passed}")
        lines.append("")

        # 各维度得分
        scores = e.get("scores", {})
        if scores:
            lines.append("### 各维度得分")
            lines.append("")
            dim_names = {
                "writing_style": "写作风格",
                "content_accuracy": "内容准确性",
                "structure": "结构合理性",
                "title_quality": "标题质量",
                "deduplication": "去重程度",
                "completeness": "内容完整性",
                "balance": "篇幅均衡性",
            }
            lines.append("| 维度 | 分数 | 说明 |")
            lines.append("|------|------|------|")
            for dim_key, dim_data in scores.items():
                dim_label = dim_names.get(dim_key, dim_key)
                if isinstance(dim_data, dict):
                    s = dim_data.get("score", "—")
                    detail = dim_data.get("detail", "")
                    lines.append(f"| {dim_label} | {s} | {detail} |")
                else:
                    lines.append(f"| {dim_label} | {dim_data} | |")
            lines.append("")

        # 问题列表
        issues = e.get("issues", [])
        if issues:
            lines.append("### 发现的问题")
            lines.append("")
            for issue in issues:
                lines.append(f"- {issue}")
            lines.append("")

        # 改进建议
        suggestions = e.get("suggestions", [])
        if suggestions:
            lines.append("### 改进建议")
            lines.append("")
            for sug in suggestions:
                lines.append(f"- {sug}")
            lines.append("")

        lines.append("---")
        lines.append("")

    return "\n".join(lines)


def _render_changes_as_markdown(
    optimizer_analysis: Dict,
    version_num: int,
) -> str:
    """将优化器的修改分析渲染为可读的 markdown。"""
    lines = []
    from_v = optimizer_analysis.get("from_version", version_num)
    to_v = optimizer_analysis.get("to_version", version_num + 1)

    lines.append(f"# Prompt 修改记录 — v{from_v} → v{to_v}")
    lines.append("")

    analysis = optimizer_analysis.get("analysis", "")
    if analysis:
        lines.append("## 问题分析")
        lines.append("")
        lines.append(analysis)
        lines.append("")

    changes = optimizer_analysis.get("changes", [])
    if changes:
        lines.append(f"## 修改项（共 {len(changes)} 项）")
        lines.append("")
        for i, c in enumerate(changes, 1):
            target = c.get("target", "?")
            what = c.get("what", "")
            why = c.get("why", "")
            lines.append(f"### {i}. [{target}] {what}")
            lines.append("")
            if why:
                lines.append(f"**原因**：{why}")
                lines.append("")
        lines.append("---")
        lines.append("")

    return "\n".join(lines)


def save_prompt_version(
    version: PromptVersion,
    evaluations: Optional[List[Dict]] = None,
    outputs: Optional[Dict[str, Dict]] = None,
    optimizer_analysis: Optional[Dict] = None,
    directory: Optional[str] = None,
):
    """
    将一轮优化的完整记录持久化到磁盘。

    存储结构 (prompts/restructure_alt/):
        v1.json          ← prompt + 元信息
        v1_evals.json    ← 各用例的评估详情
        v1_outputs.json  ← 各用例的生成结果（可选，用于人工审阅）
        v1_changes.json  ← 优化器的分析和修改说明
    """
    store_dir = directory or PROMPT_STORE_DIR
    os.makedirs(store_dir, exist_ok=True)
    prefix = f"v{version.version}"

    # 1. Prompt 本体
    prompt_path = os.path.join(store_dir, f"{prefix}.json")
    prompt_data = {
        "version": version.version,
        "system_prompt": version.system_prompt,
        "prompt_template": version.prompt_template,
        "created_at": version.created_at,
        "avg_score": version.avg_score,
        "pass_rate": version.pass_rate,
        "case_scores": version.case_scores,
    }
    with open(prompt_path, "w", encoding="utf-8") as f:
        json.dump(prompt_data, f, ensure_ascii=False, indent=2)

    # 1.1 Prompt 可读版 md
    prompt_md = _render_prompt_as_markdown(version)
    prompt_md_path = os.path.join(store_dir, f"{prefix}_prompt.md")
    with open(prompt_md_path, "w", encoding="utf-8") as f:
        f.write(prompt_md)

    # 2. 评估详情
    if evaluations:
        eval_path = os.path.join(store_dir, f"{prefix}_evals.json")
        with open(eval_path, "w", encoding="utf-8") as f:
            json.dump(evaluations, f, ensure_ascii=False, indent=2)

        # 2.1 评估报告 md
        eval_md = _render_evals_as_markdown(evaluations, version)
        eval_md_path = os.path.join(store_dir, f"{prefix}_evals.md")
        with open(eval_md_path, "w", encoding="utf-8") as f:
            f.write(eval_md)

    # 3. 生成结果（供人工审阅）
    if outputs:
        output_path = os.path.join(store_dir, f"{prefix}_outputs.json")
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(outputs, f, ensure_ascii=False, indent=2)

        # 3.1 生成可读 md 文件（给老板看的干净版本）
        for case_id, output_data in outputs.items():
            md_content = _render_output_as_markdown(output_data, version, case_id)
            md_path = os.path.join(store_dir, f"{prefix}_{case_id}.md")
            with open(md_path, "w", encoding="utf-8") as f:
                f.write(md_content)

    # 4. 优化器分析（改了什么、为什么改）
    if optimizer_analysis:
        changes_path = os.path.join(store_dir, f"{prefix}_changes.json")
        with open(changes_path, "w", encoding="utf-8") as f:
            json.dump(optimizer_analysis, f, ensure_ascii=False, indent=2)

        # 4.1 修改记录 md
        changes_md = _render_changes_as_markdown(optimizer_analysis, version.version)
        changes_md_path = os.path.join(store_dir, f"{prefix}_changes.md")
        with open(changes_md_path, "w", encoding="utf-8") as f:
            f.write(changes_md)

    saved_files = [f"{prefix}_prompt.md"]
    if evaluations:
        saved_files.append(f"{prefix}_evals.md")
    if outputs:
        for case_id in outputs:
            saved_files.append(f"{prefix}_{case_id}.md")
    if optimizer_analysis:
        saved_files.append(f"{prefix}_changes.md")
    print(f"[prompt-store] v{version.version} 已存档: {', '.join(saved_files)}")


def load_latest_prompt(directory: Optional[str] = None) -> Optional[PromptVersion]:
    """从磁盘加载最新版本的 prompt。"""
    store_dir = directory or PROMPT_STORE_DIR
    if not os.path.isdir(store_dir):
        return None

    files = [
        f for f in os.listdir(store_dir)
        if f.startswith("v") and f.endswith(".json") and "_" not in f
    ]
    if not files:
        return None

    files.sort(key=lambda f: int(f[1:].split(".")[0]), reverse=True)
    filepath = os.path.join(store_dir, files[0])

    with open(filepath, "r", encoding="utf-8") as f:
        data = json.load(f)

    return PromptVersion(
        version=data["version"],
        system_prompt=data["system_prompt"],
        prompt_template=data["prompt_template"],
        created_at=data.get("created_at", 0),
        avg_score=data.get("avg_score"),
        pass_rate=data.get("pass_rate"),
        case_scores=data.get("case_scores", {}),
    )


def load_prompt_version(version: int, directory: Optional[str] = None) -> Optional[PromptVersion]:
    """加载指定版本的 prompt。"""
    store_dir = directory or PROMPT_STORE_DIR
    filepath = os.path.join(store_dir, f"v{version}.json")
    if not os.path.isfile(filepath):
        return None

    with open(filepath, "r", encoding="utf-8") as f:
        data = json.load(f)

    return PromptVersion(
        version=data["version"],
        system_prompt=data["system_prompt"],
        prompt_template=data["prompt_template"],
        created_at=data.get("created_at", 0),
        avg_score=data.get("avg_score"),
        pass_rate=data.get("pass_rate"),
        case_scores=data.get("case_scores", {}),
    )


def list_prompt_versions(directory: Optional[str] = None) -> List[Dict]:
    """列出所有已存档的 prompt 版本及其分数。"""
    store_dir = directory or PROMPT_STORE_DIR
    if not os.path.isdir(store_dir):
        return []

    files = [
        f for f in os.listdir(store_dir)
        if f.startswith("v") and f.endswith(".json") and "_" not in f
    ]
    files.sort(key=lambda f: int(f[1:].split(".")[0]))

    versions = []
    for fname in files:
        filepath = os.path.join(store_dir, fname)
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
        versions.append({
            "version": data["version"],
            "avg_score": data.get("avg_score"),
            "pass_rate": data.get("pass_rate"),
            "case_scores": data.get("case_scores", {}),
            "created_at": data.get("created_at"),
        })
    return versions


# ============ Production Runner ============

class SinglePromptRestructurer:
    """
    生产环境入口。
    优先加载优化后的 prompt，否则使用默认 prompt。
    """

    def __init__(self, metadata_context: str = ""):
        self.metadata_context = metadata_context
        self.router = ContentRouter()

        optimized = load_latest_prompt()
        if optimized and optimized.avg_score and optimized.avg_score >= 80:
            print(f"[restructure-alt] 使用优化版 prompt v{optimized.version} "
                  f"(平均分: {optimized.avg_score:.1f})")
            self.generator = SinglePromptGenerator(
                optimized.system_prompt, optimized.prompt_template
            )
            self.prompt_version = optimized.version
        else:
            print("[restructure-alt] 使用默认 prompt")
            self.generator = SinglePromptGenerator()
            self.prompt_version = 0

    async def run(self, segments: List[Dict]) -> Dict:
        start = time.time()

        # Step 1: 路由
        content_type = await self.router.route(segments, self.metadata_context)

        # Step 2: 单 prompt 生成
        result = await self.generator.generate(segments, content_type, self.metadata_context)

        # Step 3: 后处理 — 构建与 Plan A 兼容的输出格式
        output = self._build_output(result, segments, time.time() - start)
        return output

    def _build_output(self, raw: Dict, segments: List[Dict], elapsed: float) -> Dict:
        sections = raw.get("sections", [])
        output_sections = []

        for sec in sections:
            source_ids = sec.get("source_segment_ids", [])
            source_segs = [
                segments[sid] for sid in source_ids
                if 0 <= sid < len(segments)
            ]
            start_time = source_segs[0].get("start_time", 0) if source_segs else 0
            end_time = source_segs[-1].get("end_time", 0) if source_segs else 0

            output_sections.append({
                "section_type": sec.get("section_type", ""),
                "title": sec.get("title", ""),
                "content": sec.get("content", ""),
                "key_points": sec.get("key_points", []),
                "source_segment_ids": source_ids,
                "source_segments": source_segs,
                "time_range": [start_time, end_time],
                "word_count": len(sec.get("content", "")),
            })

        return {
            "content_type": raw.get("content_type", "访谈"),
            "core_theme": raw.get("core_theme", ""),
            "theme_keywords": raw.get("theme_keywords", []),
            "speakers": raw.get("speakers", []),
            "sections": output_sections,
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
            "agent_steps": 2,  # 路由 1 次 + 生成 1 次
            "quality_score": 0,  # 生产模式不做实时评估，依赖编译期质量保证
            "plan_variant": "single_prompt",
            "prompt_version": self.prompt_version,
            "elapsed_seconds": elapsed,
        }


# ============ Node Entry Point ============

async def restructure_alternative_node(state: PodBookState) -> Dict[str, Any]:
    """
    工作流节点入口 — 与 restructure_node 接口完全兼容。

    在 graph.py 中替换 restructure_node 即可切换到单 prompt 方案：
        workflow.add_node("restructure", restructure_alternative_node)
    """
    print(f"[restructure-alt] 开始处理任务: {state['task_id']}")

    transcription = state.get("transcription")
    if not transcription:
        raise ValueError("缺少转写结果，无法进行内容重组")

    segments = transcription.get("segments", [])
    if not segments:
        raise ValueError("转写结果中没有 segments 数据")

    metadata_context = build_metadata_context(state)
    runner = SinglePromptRestructurer(metadata_context=metadata_context)
    chapters = await runner.run(segments)

    print(f"[restructure-alt] 完成: {len(chapters.get('sections', []))} 个板块, "
          f"prompt_v{chapters.get('prompt_version', 0)}, "
          f"耗时 {chapters.get('elapsed_seconds', 0):.1f}s")

    return {
        "chapters": chapters,
        "current_stage": WorkflowStage.EXTRACTION.value,
    }
