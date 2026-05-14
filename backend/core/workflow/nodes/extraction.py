"""
板块三：精华提炼（三阶段流水线）

阶段一（Extract）：从原始 transcript 中提取金句，保证原真性
阶段 1.5（Review）：审核金句质量，删除不完整/不合格的
阶段二（Place）：逐章并行，为每条金句确定精确的排版位置（段落级别）

方法论/概念的视觉呈现已移至 illustration 节点（统一使用 AI 生成图片）。

输入：transcription, composed_content
输出：highlights
"""
import asyncio
from typing import Dict, Any, List

from core.config import settings
from core.workflow.state import PodBookState, WorkflowStage
from core.workflow.prompts.registry import get_prompts
from core.services.llm_service import get_llm_service

# ================================================================
# 阶段一：从原始 transcript 提取金句和方法论
# ================================================================

EXTRACT_SYSTEM_PROMPT = """\
你是一位资深的内容策划人，拥有丰富的非虚构类图书和新媒体内容运营经验。

你的核心技能：
- 从播客对话原文中精准捕捉最具传播力和记忆点的"金句"——那些让读者想截图、想分享的话

你的工作标准：
- 你面对的是未经润色的播客原始转写，金句必须来自说话人的真实表达
- **忠实于原话**——你的工作是"选"而非"改写"，嘉宾怎么说的就怎么呈现
- 宁精勿滥——5 条精准的金句远好过 10 条掺水的"""

EXTRACT_PROMPT = """\
请从以下播客转写原文中提取金句。

【重要】你看到的是播客的原始转写文本，不是编辑后的书稿。\
每个片段前有编号 [N] 和说话人标记，请利用这些信息判断谁说了什么。

【书稿信息】
- 类型：{content_type}
- 核心主题：{core_theme}
- 说话人：{speakers_info}

【章节结构（书稿已按以下章节组织，每章对应若干转写片段）】
{chapter_metadata}

【原始转写内容】（共 {total_segments} 个片段）
{numbered_segments}

---

请按以下步骤完成：

**第一步：通读全文，识别候选金句**
通读所有转写片段，标记出你认为"可能是好金句"的句子。筛选标准：

✅ 好金句的特征（必须同时满足）：
- 来自嘉宾（非主持人）的真实表达
- **表达观点或方法论**——要有明确的判断、主张或道理，而非单纯叙述事实
- 独立成句——脱离上下文也能理解和传播
- 有认知价值——读者看完能学到东西、获得启发，而不是只了解了嘉宾个人的性格或心情
- **必须表达完整的意思**——一句金句要让读者读完就「懂了」，而不是只看到半截话
- 长度灵活：短的可以 15 字左右（如果本身意思完整且有力），长的可以到 80 字（如果需要把道理讲完整）。关键是意思完整、有记忆点，不要为了短而截断

❌ 不应选为金句的：
- 主持人的提问或串场语
- **纯叙事性的感受或经历描述**（如"那时候一用就感觉变天了""我当时很震惊"）
- **个人心境或情感自述**——只是在讲"我是什么样的人""我当时什么感受"，没有可迁移的启发（如"我是一个焦虑的人，也没什么可失去的""我一直很喜欢竞争"）
- **问句或无结论的内容**——金句必须给出明确的判断或结论（如"模型和产品的边界到底是什么？到今天也没有结论"不是金句，因为它没有给出答案）
- **只描述现状而无观点的感叹**（如"真正的AGI一定是多模态的，只是这件事太难了"——只是在感叹困难，没有方法论或启发）
- 需要前后文才能理解的半截话（宁可多选几个字把意思说完，也不要截断）
- **没有观点的陈述**（如"我们当时服务了上千家企业""那年我去了百度"）
- 口语化严重难以直接引用的

**第二步：全书精选 0-5 条，宁缺毋滥**
从候选中精选最多 5 条金句。按全书价值排序，尽量让金句分布在全书各处：
- 避免"前几章金句密集、后面大段空白"的情况
- 同一个内容块最多 1 条，避免扎堆
- 没有足够锐利的句子，可以输出 0 条

对每条金句，记录其来源片段编号（segment_ids），后续用于映射到对应章节。

**金句文本处理原则（极其重要）：**
- **你的首要职责是"选取"而非"改写"**——从转写原文中挑出最好的句子，而不是用自己的话重新表达嘉宾的意思
- 只允许做最低限度的清理：去掉"嗯""啊""那个""就是说"等无意义的口头填充词，修正明显的语法断句
- **严禁重构句式、替换用词、浓缩或合并原话**——嘉宾说"我觉得这事儿就是得快"，不要改成"速度是关键"
- 如果嘉宾的表达稍显冗长但意思完整，保留原样即可，不要为了精炼而改写
- 嘉宾的口语风格本身就是金句魅力的一部分，保留它

---

【正面示例】
转写片段：`[42]【张帆】所以我一直跟团队讲，就是说，不要在海面上修灯塔，而要造一艘船。`

✅ 提取为金句：`我一直跟团队讲，不要在海面上修灯塔，而要造一艘船。`（只去掉了"所以""就是说"等填充词，保留嘉宾原话）
segment_ids: [42]

转写片段：`[58]【陈冕】我觉得吧，就是，创业这个事最重要的就是认知和速度，你要看到别人没看到的东西，然后你要用最快的速度把它实现出来。`

✅ 提取为金句：`创业这个事最重要的就是认知和速度，你要看到别人没看到的东西，然后你要用最快的速度把它实现出来。`（去掉了"我觉得吧，就是"，其余完全保留嘉宾原话，不做任何改写）
segment_ids: [58]

❌ 错误做法：`创业最重要的是认知和速度：你要看到别人没看到的东西，你要用最快的速度把它实现。`（这是改写，把"这个事"去掉了，把"然后"去掉了，把"实现出来"改成了"实现"——这些都是嘉宾的说话风格，不应修改）

【反面示例】
转写片段：`[15]【Coco】对，我觉得这个特别有意思。`

❌ 不选。理由：主持人的附和语，无独立观点。

---

【输出格式】
严格JSON格式：
{{
    "analysis": "简要说明筛选过程（1-2句话）",
    "quotes": [
        {{
            "text": "金句原文（经最小限度书面化处理）",
            "segment_ids": [片段编号],
            "speaker": "说话人姓名"
        }}
    ]
}}"""

# ================================================================
# 阶段 1.5：审核金句质量
# ================================================================

REVIEW_SYSTEM_PROMPT = """\
你是一位严格的内容质量审核员。你的任务是逐条审核金句，删除不合格的。\
你只做删除判断，不修改金句内容。"""

REVIEW_PROMPT = """\
以下是从播客中提取的金句列表，请逐条审核，删除不合格的金句。

【审核标准——不合格的金句必须删除】
1. **句子被截断/不完整**（最重要！）：请对每条金句做以下检查：
   - 句子是否在逗号、顿号、"和""或""以及""然后"等连接词之后突然结束？
   - 句子是否缺少谓语或宾语（如"我觉得创业最重要的"——"最重要的"什么？没说完）？
   - 句子末尾是否没有句号、感叹号或其他终止标点，而是在逗号或无标点处断掉？
   - 读完这句话，读者是否会觉得"后面还有话没说完"？
   如果以上任一为"是"，则删除。
2. **问句或无结论的反问**：金句必须给出明确判断或结论。以下情况必须删除：
   - 以问号结尾的疑问句（如"模型和产品的边界到底是什么？"）
   - 虽然后面跟了一句话但仍然没有给出结论的（如"你是想做A还是想做B？这两者有差别。"——指出了差别但没有说应该怎么选，依然是在发问）
   - 判断标准：读完这句话，读者能否获得一个明确的行动指引或判断？如果不能，就删除
3. **无结论的感叹**：只是在感叹某件事难、某件事厉害，没有给出方法论或可迁移的道理（如"这件事太难了""那时候真的很震撼"）
4. **纯个人叙事**：只是在讲"我做了什么""我去了哪里""我是什么样的人"，没有可迁移的启发
5. **重复**：与列表中其他金句表达了几乎相同的意思（保留更好的那条，删除较弱的）

【待审核的金句】
{quotes_list}

---

请输出审核结果，严格JSON格式：
{{
    "reviews": [
        {{
            "index": 金句序号（从0开始），
            "keep": true 或 false,
            "reason": "保留/删除的理由（一句话）"
        }}
    ]
}}

注意：宁可多保留也不要误删好金句。只删除明显不合格的。"""


async def _stage1_5_review(
    llm,
    quotes: List[Dict],
) -> List[Dict]:
    """审核金句质量，删除不合格的。"""
    if not quotes:
        return quotes

    lines = []
    for i, q in enumerate(quotes):
        speaker = q.get("speaker", "")
        lines.append(f"[{i}] 「{q['text']}」 —— {speaker}")
    quotes_list = "\n".join(lines)

    prompt = REVIEW_PROMPT.format(quotes_list=quotes_list)

    try:
        result = await llm.generate_json(
            prompt=prompt,
            system_prompt=REVIEW_SYSTEM_PROMPT,
            temperature=0.2,
            model=settings.LLM_MODEL,
            thinking_budget=0,
            timeout=60,
            label="审核金句质量",
        )

        reviews = result.get("reviews", [])
        kept = []
        removed = []
        for r in reviews:
            idx = r.get("index", -1)
            if 0 <= idx < len(quotes):
                if r.get("keep", True):
                    kept.append(quotes[idx])
                else:
                    removed.append((quotes[idx].get("text", "")[:40], r.get("reason", "")))

        for text, reason in removed:
            print(f"[extraction] 审核删除: 「{text}...」 原因: {reason}")

        print(f"[extraction] 审核完成: {len(quotes)} → {len(kept)} 条金句 "
              f"(删除 {len(removed)} 条)")
        return kept

    except Exception as e:
        print(f"[extraction] 审核失败，保留全部金句: {e}")
        return quotes


# ================================================================
# 阶段二：逐章并行定位
# ================================================================

PLACE_SYSTEM_PROMPT = """\
你是一位图书排版编辑，负责决定金句在章节中的最佳位置。\
你的目标是让金句出现在读者最需要"停下来品味"的地方，增强阅读体验。"""

PLACE_PROMPT = """\
以下是一个章节的正文内容，以及需要放入该章节的金句。\
请为每条金句确定最佳的排版位置。

【章节标题】{chapter_title}
【板块类型】{section_type}

【章节正文（已按段落编号）】
{numbered_paragraphs}

【待放置的金句】
{quotes_to_place}

---

**排版规则：**

金句的排版位置类型（placement）：
- `inline_card`：正文拉引式金句卡片，放在某个段落之后。需指定 `after_paragraph`（段落编号）。\
**定位方法**：在正文中找到包含这句金句原文（或最接近表述）的那个段落，将金句卡片放在该段落之后。金句是从嘉宾原话提取的，正文中一定有对应的段落——请仔细逐段搜索匹配，不要凭感觉随意放置。

**注意：**
- 不使用 `epigraph`，所有保留金句都用 `inline_card`
- 不要把所有金句都堆在同一个段落后面，合理分散
- 如果某条金句放在本章不合适（内容不相关），设 placement 为 `skip`

【输出格式】
严格JSON格式：
{{
    "quote_placements": [
        {{
            "text": "金句原文",
            "placement": "inline_card | skip",
            "after_paragraph": 段落编号（仅 inline_card 需要）
        }}
    ]
}}"""


# ================================================================
# 辅助函数
# ================================================================

def _format_speakers(speakers: List[Dict]) -> str:
    if not speakers:
        return "未知"
    parts = []
    for s in speakers:
        name = s.get("name", "")
        role = s.get("role", "")
        if name:
            parts.append(f"{name}（{role}）" if role else name)
    return "、".join(parts) if parts else "未知"


def _format_numbered_segments(segments: List[Dict]) -> str:
    lines = []
    for i, seg in enumerate(segments):
        speaker = seg.get("speaker", "未知")
        text = seg.get("text", "")
        lines.append(f"[{i}]【{speaker}】{text}")
    return "\n\n".join(lines)


def _format_chapter_metadata(chapters: List[Dict]) -> str:
    """轻量的章节元数据，不包含正文内容。"""
    lines = []
    for i, ch in enumerate(chapters):
        title = ch.get("title", "")
        section_type = ch.get("section_type", "")
        seg_ids = ch.get("source_segment_ids", [])
        kps = ch.get("key_points", [])
        kp_str = "、".join(kps) if kps else ""
        lines.append(
            f"第{i+1}章「{title}」[{section_type}] "
            f"对应片段: {seg_ids}"
            + (f" | 要点: {kp_str}" if kp_str else "")
        )
    return "\n".join(lines)


def _map_to_chapters(
    items: List[Dict],
    chapters: List[Dict],
) -> Dict[int, List[Dict]]:
    """
    将带有 segment_ids 的金句/方法论映射到章节。
    返回 {chapter_index: [items]} 字典。
    """
    chapter_seg_map = {}
    for ci, ch in enumerate(chapters):
        for sid in ch.get("source_segment_ids", []):
            chapter_seg_map[sid] = ci

    result: Dict[int, List[Dict]] = {i: [] for i in range(len(chapters))}
    for item in items:
        seg_ids = item.get("segment_ids", [])
        matched_chapters = set()
        for sid in seg_ids:
            ci = chapter_seg_map.get(sid)
            if ci is not None:
                matched_chapters.add(ci)
        if matched_chapters:
            target = min(matched_chapters)
            result[target].append(item)
        elif chapters:
            result[0].append(item)
    return result


def _number_paragraphs(content: str) -> str:
    """将章节正文按段落编号，方便模型引用。"""
    paragraphs = [p.strip() for p in content.split("\n\n") if p.strip()]
    lines = []
    for i, para in enumerate(paragraphs):
        lines.append(f"[P{i}] {para}")
    return "\n\n".join(lines)


def _format_quotes_for_placement(quotes: List[Dict]) -> str:
    if not quotes:
        return "（本章无金句需要放置）"
    lines = []
    for i, q in enumerate(quotes):
        lines.append(f"{i+1}. 「{q['text']}」 —— {q.get('speaker', '')}")
    return "\n".join(lines)


# ================================================================
# 阶段一：提取
# ================================================================

async def _stage1_extract(llm, segments: List[Dict], composed: Dict, prompts=None) -> List[Dict]:
    """从原始 transcript 中提取金句。"""
    chapters = composed.get("chapters", [])
    speakers = composed.get("speakers", [])

    extract_system = prompts.get("extract_system", EXTRACT_SYSTEM_PROMPT) if prompts else EXTRACT_SYSTEM_PROMPT
    extract_user = prompts.get("extract_user", EXTRACT_PROMPT) if prompts else EXTRACT_PROMPT
    prompt = extract_user.format(
        content_type=composed.get("content_type", "访谈"),
        core_theme=composed.get("core_theme", ""),
        speakers_info=_format_speakers(speakers),
        chapter_metadata=_format_chapter_metadata(chapters),
        total_segments=len(segments),
        numbered_segments=_format_numbered_segments(segments),
    )

    result = await llm.generate_json(
        prompt=prompt,
        system_prompt=extract_system,
        temperature=0.4,
        model=settings.LLM_MODEL,
        thinking_budget=0,
        timeout=180,
    )

    quotes = result.get("quotes", [])
    print(f"[extraction] 阶段一完成: 提取 {len(quotes)} 条金句")
    return quotes


# ================================================================
# 阶段二：逐章并行定位
# ================================================================

async def _place_for_chapter(
    llm,
    chapter_index: int,
    chapter: Dict,
    quotes: List[Dict],
) -> Dict:
    """为单个章节中的金句确定排版位置。"""
    if not quotes:
        return {"quote_placements": []}

    content = chapter.get("content", "")
    prompt = PLACE_PROMPT.format(
        chapter_title=chapter.get("title", ""),
        section_type=chapter.get("section_type", ""),
        numbered_paragraphs=_number_paragraphs(content),
        quotes_to_place=_format_quotes_for_placement(quotes),
    )

    try:
        result = await llm.generate_json(
            prompt=prompt,
            system_prompt=PLACE_SYSTEM_PROMPT,
            model=settings.LLM_MODEL,
            thinking_budget=0,
            timeout=60,
        )
        return result
    except Exception as e:
        print(f"[extraction] 章节 {chapter_index} 定位失败: {e}")
        return {"quote_placements": []}


async def _stage2_place(
    llm,
    chapters: List[Dict],
    raw_quotes: List[Dict],
) -> Dict:
    """逐章并行，为金句确定排版位置。"""
    quote_map = _map_to_chapters(raw_quotes, chapters)

    tasks = []
    for ci, chapter in enumerate(chapters):
        tasks.append(_place_for_chapter(
            llm, ci, chapter,
            quote_map.get(ci, []),
        ))

    results = await asyncio.gather(*tasks)

    all_quotes = []

    speaker_lookup: Dict[str, str] = {}
    for q in raw_quotes:
        t = q.get("text", "").strip()
        if t and q.get("speaker"):
            speaker_lookup[t] = q["speaker"]

    for ci, (chapter, placement) in enumerate(zip(chapters, results)):
        chapter_title = chapter.get("title", "")

        for qp in placement.get("quote_placements", []):
            p = qp.get("placement", "inline_card")
            if p == "skip":
                continue
            qt = qp.get("text", "")
            speaker = qp.get("speaker", "") or speaker_lookup.get(qt.strip(), "")
            all_quotes.append({
                "text": qt,
                "speaker": speaker,
                "placement": p if p in VALID_PLACEMENTS else "inline_card",
                "chapter_title": chapter_title,
                "after_paragraph": qp.get("after_paragraph"),
            })

    print(f"[extraction] 阶段二完成: {len(all_quotes)} 条金句已定位")

    return {"quotes": all_quotes}


# ================================================================
# 验证与常量
# ================================================================

VALID_PLACEMENTS = {"epigraph", "inline_card"}
MAX_INLINE_QUOTES = 5


def validate_highlights(highlights: Dict) -> Dict:
    """验证并修正精华提炼结果。"""
    if not isinstance(highlights, dict):
        raise ValueError("精华提炼结果格式错误")

    if "quotes" not in highlights:
        highlights["quotes"] = []

    clean_quotes = []
    for q in highlights["quotes"]:
        if isinstance(q, str) and q.strip():
            clean_quotes.append({
                "text": q,
                "placement": "inline_card",
                "chapter_title": "",
                "after_paragraph": None,
            })
        elif isinstance(q, dict) and q.get("text"):
            placement = q.get("placement", "inline_card")
            if placement not in VALID_PLACEMENTS:
                placement = "inline_card"
            clean_quotes.append({
                "text": q["text"],
                "speaker": q.get("speaker", ""),
                "placement": placement,
                "chapter_title": q.get("chapter_title", ""),
                "after_paragraph": q.get("after_paragraph"),
            })
    epigraph_found = False
    for q in clean_quotes:
        if q["placement"] == "epigraph":
            if epigraph_found:
                q["placement"] = "inline_card"
            else:
                epigraph_found = True
    highlights["quotes"] = clean_quotes

    return highlights


def _apply_workbench_quote_policy(highlights: Dict) -> Dict:
    """Keep one chapter-equivalent amount of pull quotes for the whole C-side book."""
    quotes = []
    seen_chapters = set()
    for q in highlights.get("quotes", []):
        if not isinstance(q, dict):
            continue
        text = (q.get("text") or "").strip()
        if not text:
            continue
        chapter_title = q.get("chapter_title", "")
        if chapter_title in seen_chapters:
            continue
        seen_chapters.add(chapter_title)
        quotes.append({
            **q,
            "text": text,
            "placement": "inline_card",
        })
        if len(quotes) >= MAX_INLINE_QUOTES:
            break
    return {**highlights, "quotes": quotes}


# ================================================================
# 节点入口
# ================================================================

async def extraction_node(state: PodBookState) -> Dict[str, Any]:
    """
    精华提炼节点（三阶段流水线）。

    阶段一：从原始 transcript 提取金句（保证原真性）
    阶段 1.5：审核金句质量，删除不完整/不合格的
    阶段二：逐章并行定位排版位置（段落级精度）

    输入：transcription, composed_content
    输出：highlights
    """
    print(f"[extraction] 开始处理任务: {state['task_id']}")

    transcription = state.get("transcription")
    if not transcription:
        raise ValueError("缺少转写结果，无法进行精华提炼")

    composed = state.get("composed_content")
    if not composed:
        raise ValueError("缺少成稿内容，无法进行精华提炼")

    segments = transcription.get("segments", [])
    chapters = composed.get("chapters", [])
    if not segments:
        raise ValueError("转写结果中没有 segments 数据")

    llm = get_llm_service()

    content_type = state.get("content_type", "business")
    try:
        prompts = get_prompts("extraction", content_type)
    except KeyError:
        prompts = None

    # 阶段一：从 transcript 提取金句
    raw_quotes = await _stage1_extract(llm, segments, composed, prompts=prompts)

    # 阶段 1.5：审核金句质量
    reviewed_quotes = await _stage1_5_review(llm, raw_quotes)

    # 阶段二：逐章并行定位
    highlights = await _stage2_place(llm, chapters, reviewed_quotes)

    highlights = validate_highlights(highlights)
    highlights = _apply_workbench_quote_policy(highlights)

    print(f"[extraction] 全部完成: {len(highlights['quotes'])} 条金句")

    return {
        "highlights": highlights,
        "current_stage": WorkflowStage.EXTRACTION.value,
    }


# ================================================================
# 兼容旧调用的辅助函数
# ================================================================

def format_chapters_for_prompt(chapters: List[Dict]) -> str:
    """格式化章节内容用于提示词（保留向后兼容）。"""
    result = []
    for chapter in chapters:
        title = chapter.get("title", "")
        content = chapter.get("content", "")
        section_type = chapter.get("section_type", "")
        header = f"## {title}"
        if section_type:
            header += f"  [板块类型: {section_type}]"
        chapter_text = f"{header}\n{content}"
        result.append(chapter_text)
    return "\n\n".join(result)
