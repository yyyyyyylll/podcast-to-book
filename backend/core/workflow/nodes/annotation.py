"""
节点：内容注释（Annotation）

为书稿各章节添加脚注，包括：
1. 术语解释：对专业术语、缩写、行业词汇添加简洁解释
2. 背景补充：对需要补充背景的人名、公司、事件等添加客观事实说明

每章独立处理，脚注全文统一编号，避免过度注释。
跨章传递已注释术语列表，确保全书不重复注释。

输入：composed_content
输出：annotated_content
"""
import asyncio
import re
from typing import Dict, Any, List

from core.workflow.state import PodBookState, WorkflowStage
from core.config import settings
from core.services.llm_service import get_llm_service
from core.workflow.prompts.registry import get_prompts

MAX_TOTAL_FOOTNOTES = 5

ANNOTATION_SYSTEM_PROMPT = (
    "你是一位资深的图书编辑，拥有十年以上的非虚构类书籍出版经验，"
    "尤其擅长科技、商业、创投领域的内容编辑。\n\n"
    "你的目标读者画像：对科技、商业、创投话题感兴趣的大众读者。"
    "他们未必是互联网从业者，可能是学生、传统行业人士或泛科技爱好者。"
    "他们了解日常生活中常见的概念（如微信、抖音、AI），但对行业内部术语、英文缩写、"
    "特定商业模式、技术概念不一定熟悉。\n\n"
    "你的工作原则：\n"
    "- 商业和技术类的专业术语、英文缩写，如果不是大众常识，就应该注释\n"
    "- 不重要的不注释，重要的注释要**写充分**——不是一句话敷衍了事，而是提供足够的背景让读者真正理解\n"
    "- 严格区分「确定可查证的事实」与「不确定的信息」，对后者绝不编造\n"
    "- C 端整本书只有一期，脚注全书总上限为 5 条\n"
    "- 脚注直接写定义或背景事实，不使用「编者注：」「注：」等前缀"
)

ANNOTATION_PROMPT = """\
你正在为一本由播客内容改编的小册子添加脚注。请仔细阅读本章内容，为读者可能不熟悉的术语和背景信息添加精准、克制的脚注。

【书稿上下文】
{book_context}

【章节标题】
{chapter_title}

【章节内容】
{chapter_content}

【已注释术语（前序章节已标注，本章不得重复）】
{annotated_terms}

【脚注编号】
本章脚注从 {footnote_start} 开始编号（[^{footnote_start}]、[^{footnote_next}]……以此类推）。

---

请按以下步骤完成任务：

**第一步：通读全章，识别需要注释的术语**
通读章节内容，识别候选注释项。核心判断标准——**读者是对科技/商业感兴趣的大众读者，不是行业从业者**。

❌ 不需要注释：
- 生活常识和大众文化：MBTI、Dota、电竞、网吧、辩论赛、校招、社招、保送、保研
- 人人都知道的科技产品/品牌：微信、抖音、淘宝、百度、苹果、AI
- 原文上下文已经解释清楚的概念
- 前序章节已标注过的术语
- 贯穿全书的核心人物

✅ 应该注释（重点关注商业和技术术语）：
- **商业/创投术语**：如 O2O、SaaS、B2B、冷启动、DAU、MAU、GMV、估值、对赌、现金流等——这些在行业内是常识，但普通读者未必懂
- **技术概念和英文缩写**：如 NLP、AGI、多模态、Transformer、开源、API、Token 等——除非是 AI、ChatGPT 这种已经家喻户晓的
- **非大众认知的人物**，且其背景对理解上下文至关重要（如行业先驱、科学家）
- **非主流的产品/公司/平台**，读者不了解会影响理解上下文
- **特定的行业事件或政策**，了解背景后能显著加深对原文的理解

**第二步：撰写脚注——不重要的不写，重要的写充分**
通过筛选的注释项，要提供**真正有价值的信息**，而不是一句话敷衍。

类型A - 术语/产品注释：
- 不只是给个全称翻译，而是解释**它是什么、为什么在这个上下文中重要**
- 长度：40-100字

类型B - 人物/公司/事件背景：
- 直接写事实背景，不要以"**编者注**："、"编者注："或"注："开头
- 长度：50-150字
- 【重要】只写你能100%确认的公开事实，不推测、不编造

正文中脚注标记直接跟在被注释词后面，如：`BPO[^1]`

**第三步：控制数量，自检质量**
- 全书脚注总上限 5 个；当前内容块不需要平均分配。如果本内容块确实没有值得注释的内容，可以输出 0 个
- 每个脚注都要通过这个测试：**一个非行业从业者读到这里，会不会困惑这个词是什么意思？** 如果会，就应该注释

---

【正面示例 — 人物注释】
原文片段：`他听了丁肇中的演讲，丁教授说"好的大学是允许逃课的大学"。`

✅ 好的注释：
`[^N] 丁肇中，美籍华裔物理学家，1976年因发现J/ψ粒子获诺贝尔物理学奖。以实验严谨著称，同时在教育理念上主张给予学生自主探索的空间，"允许逃课"的言论常被引用。`

【正面示例 — 商业术语注释】
原文片段：`当时一个很大的机会是O2O开始风起云涌。`

✅ 好的注释：
`[^N] O2O，即Online To Offline（线上到线下），指通过线上平台连接并交易线下服务或商品的商业模式。2014-2016年前后，围绕出行、外卖、生鲜等领域的O2O创业潮是中国移动互联网最重要的投资主题之一。`

【正面示例 — 技术术语注释】
原文片段：`我们最终选择了多模态方向，而不是做基座大模型。`

✅ 好的注释：
`[^N] 多模态（Multimodal），指AI系统同时处理和理解多种类型数据（如文本、图像、音频、视频）的能力。与仅处理文字的纯语言模型不同，多模态模型能"看图说话"或"听音生图"，被认为更接近人类的感知方式。`

【正面示例 — 正确判断不注释】
原文片段：`后来天天打 Dota。她测过 MBTI 是 ENTJ。他去了北京参加校招。`

✅ 正确做法：全部不注释。Dota、MBTI、校招是大众常识。

【反面示例 — 该注释的没注释】
❌ 错误：文中出现"冷启动"却没注释 → 虽然产品经理都懂，但普通读者不一定知道这是指产品在没有用户基础时如何获取第一批用户
❌ 错误：文中出现"DAU"却没注释 → 不是所有读者都知道这是"日活跃用户数"
❌ 错误：文中出现"SaaS"却没注释 → 普通读者不一定知道这是"软件即服务"的商业模式

【反面示例 — 注释了不该注释的】
❌ 错误：给"微信"加注释 → 人人都知道
❌ 错误：给"计算机奥赛"加注释 → 大众常识
❌ 错误：给"保送"加注释 → 中国读者的常识

【反面示例 — 注释了但写得太敷衍】
❌ 错误：`Runway：提供AI视频生成/编辑工具的公司与产品。`（太简略，读者看完还是不明白为什么原文要拿它举例）
✅ 正确：`Runway，2018年成立的AI创意工具公司，其视频生成模型Gen-2曾是该领域的标杆产品，一度估值超过15亿美元。`

---

【输出格式】
严格JSON格式：
{{
    "analysis": "简要说明本章识别了哪些候选项、最终保留了哪几个及原因（2-3句话）",
    "annotated_content": "带有脚注标记的完整章节正文（保持原文结构不变，仅在需要注释的词后加 [^N] 标记）",
    "footnotes": [
        {{"number": N, "type": "term", "text": "脚注内容"}},
        {{"number": N, "type": "editor_note", "text": "背景内容"}}
    ],
    "footnote_count": 本章实际添加的脚注数量
}}

若本章不需要添加任何脚注：
{{
    "analysis": "本章核心术语已在前序章节注释，无新增需注释项",
    "annotated_content": "（原文原封不动复制）",
    "footnotes": [],
    "footnote_count": 0
}}"""


def _build_footnote_section(footnotes: List[Dict]) -> str:
    """将脚注列表格式化为 Markdown 脚注定义。"""
    if not footnotes:
        return ""
    lines = []
    for fn in footnotes:
        num = fn["number"]
        text = fn["text"]
        lines.append(f"[^{num}]: {text}")
    return "\n\n---\n\n" + "\n".join(lines)


def _build_book_context(composed: Dict) -> str:
    """从成稿元数据构建书稿上下文摘要，帮助模型了解全书背景。"""
    parts = []
    if composed.get("core_theme"):
        parts.append(f"主题：{composed['core_theme']}")
    if composed.get("content_type"):
        parts.append(f"类型：{composed['content_type']}")
    if composed.get("theme_keywords"):
        parts.append(f"关键词：{'、'.join(composed['theme_keywords'])}")
    speakers = composed.get("speakers", [])
    if speakers:
        speaker_strs = []
        for s in speakers:
            name = s.get("name", "")
            role = s.get("role", "")
            if name:
                speaker_strs.append(f"{name}（{role}）" if role else name)
        if speaker_strs:
            parts.append(f"说话人：{'、'.join(speaker_strs)}")
    chapters = composed.get("chapters", [])
    if chapters:
        titles = [ch.get("title", "") for ch in chapters if ch.get("title")]
        if titles:
            parts.append(f"全书章节：{'／'.join(titles)}")
    return "\n".join(parts) if parts else "无额外上下文"


def _format_annotated_terms(annotated_terms: List[str]) -> str:
    """格式化已注释术语列表。"""
    if not annotated_terms:
        return "（本章是第一章，暂无已注释术语）"
    return "、".join(annotated_terms)


import re as _re

_FOOTNOTE_PREFIX_RE = re.compile(
    r"^\s*(\*\*)?(编者注|编者按|译者注|译注|按|注)(\*\*)?\s*[：:]\s*",
)


def _strip_footnote_prefix(text: str) -> str:
    """Remove redundant editor-note prefixes from generated footnotes."""
    if not text:
        return text
    return _FOOTNOTE_PREFIX_RE.sub("", text, count=1).lstrip()


def _remove_footnote_markers(text: str, numbers: List[int]) -> str:
    """Remove [^N] markers for footnotes dropped by the global budget."""
    if not text or not numbers:
        return text
    nums = "|".join(str(n) for n in sorted(set(numbers)))
    return re.sub(rf"\[\^({nums})\]", "", text)

def _extract_entity_name(text: str) -> str:
    """从脚注文本中提取核心实体名（人名/产品名/术语）。"""
    text = text.strip().strip("\u201c\u201d\u300a\u300b\"'")
    m = _re.match(
        r'^([\w\s·\-/（）()「」\u4e00-\u9fff]+?)(?:[\u201c\u201d，,是为指即])',
        text,
    )
    if m:
        return m.group(1).strip()
    parts = _re.split(r'[\u201c\u201d，,：:（(]', text, maxsplit=1)
    return parts[0].strip()


def _extract_term_names(footnotes: List[Dict]) -> List[str]:
    """从脚注列表中提取术语/实体名称，用于跨章去重。"""
    terms = []
    for fn in footnotes:
        text = fn.get("text", "")
        if fn.get("type") == "term":
            name = _extract_entity_name(text)
        elif fn.get("type") == "editor_note":
            cleaned = _strip_footnote_prefix(text)
            name = _extract_entity_name(cleaned)
        else:
            continue
        if name and len(name) <= 30:
            terms.append(name)
    return terms


async def _annotate_chapter(
    llm,
    chapter: Dict,
    footnote_start: int,
    book_context: str,
    annotated_terms: List[str],
    remaining_budget: int,
    prompts=None,
) -> Dict:
    """
    对单个章节进行脚注注释。
    返回带注释的章节内容、脚注列表和新增术语名。
    """
    chapter_title = chapter.get("title", "")
    chapter_content = chapter.get("content", "")

    if remaining_budget <= 0 or len(chapter_content) < 100:
        return {
            **chapter,
            "content": chapter_content,
            "footnotes": [],
            "new_terms": [],
        }

    ann_system = prompts.get("annotation_system", ANNOTATION_SYSTEM_PROMPT) if prompts else ANNOTATION_SYSTEM_PROMPT
    ann_user = prompts.get("annotation_user", ANNOTATION_PROMPT) if prompts else ANNOTATION_PROMPT
    ann_system = (
        f"{ann_system}\n"
        f"- C 端整本书只有一期，脚注全书总上限为 {MAX_TOTAL_FOOTNOTES} 条；"
        f"当前内容块剩余预算最多 {remaining_budget} 条，绝不超过。"
    )
    ann_user = (
        f"{ann_user}\n\n"
        f"【本内容块数量上限】\n"
        f"整本书脚注总上限为 {MAX_TOTAL_FOOTNOTES} 条；当前内容块最多输出 {remaining_budget} 条。"
        "如果没有足够重要的对象，可以输出 0 条。"
    )

    prompt = ann_user.format(
        book_context=book_context,
        chapter_title=chapter_title,
        chapter_content=chapter_content,
        annotated_terms=_format_annotated_terms(annotated_terms),
        footnote_start=str(footnote_start),
        footnote_next=str(footnote_start + 1),
    )

    try:
        result = await llm.generate_json(
            prompt=prompt,
            system_prompt=ann_system,
            model=settings.LLM_MODEL,
            max_tokens=65536,
            thinking_budget=0,
            timeout=180,
            label=f"注释章节: {chapter_title[:20]}",
        )
        annotated_text = result.get("annotated_content", chapter_content)
        raw_footnotes = result.get("footnotes", [])
        if not isinstance(raw_footnotes, list):
            raw_footnotes = []
        footnotes = []
        for fn in raw_footnotes:
            if isinstance(fn, dict):
                fn["text"] = _strip_footnote_prefix((fn.get("text") or "").strip())
                footnotes.append(fn)
        dropped = footnotes[remaining_budget:]
        footnotes = footnotes[:remaining_budget]
        dropped_numbers = [
            int(fn.get("number"))
            for fn in dropped
            if str(fn.get("number", "")).isdigit()
        ]
        annotated_text = _remove_footnote_markers(annotated_text, dropped_numbers)
        for fn in footnotes:
            if isinstance(fn.get("number"), str) and fn["number"].isdigit():
                fn["number"] = int(fn["number"])

        footnote_section = _build_footnote_section(footnotes)
        full_content = annotated_text + footnote_section
        new_terms = _extract_term_names(footnotes)

        print(f"[annotation] 章节「{chapter_title}」: "
              f"添加了 {len(footnotes)} 个脚注")

        return {
            **chapter,
            "content": full_content,
            "footnotes": footnotes,
            "new_terms": new_terms,
        }
    except Exception as e:
        print(f"[annotation] 章节「{chapter_title}」注释失败，保留原文: {e}")
        return {
            **chapter,
            "footnotes": [],
            "new_terms": [],
        }


async def annotation_node(state: PodBookState) -> Dict[str, Any]:
    """
    内容注释节点：为各章节添加脚注。

    输入：composed_content
    输出：annotated_content
    """
    print(f"[annotation] 开始处理任务: {state['task_id']}")

    composed = state.get("composed_content")
    if not composed:
        raise ValueError("缺少成稿内容，无法进行注释")

    chapters = composed.get("chapters", [])
    if not chapters:
        raise ValueError("成稿中没有章节数据")

    llm = get_llm_service()
    book_context = _build_book_context(composed)
    content_type = state.get("content_type", "business")
    try:
        prompts = get_prompts("annotation", content_type)
    except KeyError:
        prompts = None
    annotated_chapters = []
    annotated_terms: List[str] = []
    footnote_counter = 1

    for chapter in chapters:
        remaining_budget = max(0, MAX_TOTAL_FOOTNOTES - (footnote_counter - 1))
        annotated = await _annotate_chapter(
            llm, chapter, footnote_counter, book_context, annotated_terms,
            remaining_budget=remaining_budget, prompts=prompts,
        )
        footnote_counter += len(annotated.get("footnotes", []))
        annotated_terms.extend(annotated.get("new_terms", []))
        clean_chapter = {
            k: v for k, v in annotated.items()
            if k not in ("footnotes", "new_terms")
        }
        annotated_chapters.append(clean_chapter)

    total_footnotes = footnote_counter - 1
    print(f"[annotation] 完成，共添加 {total_footnotes} 个脚注，"
          f"覆盖 {len(chapters)} 个章节")

    annotated_content = {
        **composed,
        "chapters": annotated_chapters,
        "total_footnotes": total_footnotes,
    }

    return {
        "annotated_content": annotated_content,
        "current_stage": WorkflowStage.ANNOTATION.value,
    }
