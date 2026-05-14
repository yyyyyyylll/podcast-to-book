"""
分类与说话人识别模块

将 content_type + narrative_type + voice_format 分类与说话人预识别合并为一次 LLM 调用，
嵌入 transcription_node 内部（非独立 graph 节点）。

输入：raw ASR segments 采样 + 播客元数据
输出：speaker_roles, speaker_mapping, content_type, narrative_type, voice_format
"""
from typing import Dict, Any, List

from core.config import settings
from core.services.llm_service import get_llm_service


CLASSIFY_SYSTEM_PROMPT = """\
你是一位播客内容分析专家。你的任务是同时完成两件事：
1. 判断播客的内容领域、叙事载体和内容组织方式
2. 建立 ASR 说话人标签到真实身份的映射"""

CLASSIFY_PROMPT = """\
请分析以下播客的元数据和对话采样，完成内容分类和说话人识别。

{metadata_context}

【ASR 统计信息】
- 总片段数：{total_segments}
- ASR 识别到的说话人数：{speaker_count}
- 各说话人片段占比：{speaker_distribution}

【对话片段采样】（从对话不同位置采样）
{sample_text}

---

## 任务一：内容分类（三个维度）

### content_type — 主题领域（三选一）

- `business` — 商业/科技/创投/创业方法论。标志：讨论产品、融资、市场、技术架构、商业模式、增长策略
- `self_growth` — 自我成长/情感/人生智慧/心理。标志：讨论个人成长、情感关系、人生选择、心理健康、生活态度
- `humanities` — 社会人文/学术/文化评论/历史。标志：讨论社会现象、历史事件、文化批评、学术概念、哲学思考

判断优先级：元数据（播客简介通常已明确说明话题领域）> 采样内容验证

### narrative_type — 叙事载体（二选一）

- `story` — 故事/经历驱动。内容以叙事为核心骨架，章节适合按时间线或人物弧线组织。叙事主体可以是：
  - 嘉宾/叙述者本人的亲身经历（"我当时如何如何"）
  - 多位嘉宾各自分享自己的个人经历和故事（圆桌/群访中每人讲自己的经历）
  - 大家共同讲述的第三方人物或组织的故事（如雷军的创业史、腾讯的发展历程、某个历史事件的始末）
- `opinion` — 观点/论证/信息驱动。内容围绕某个议题、方法论、行业分析展开。章节适合按论点或议题递进组织

判断线索：
- 大量出现"那时候""后来""当时""回忆起"等时间线叙事词 → story
- 内容有清晰的时间序列，在讲一件事的来龙去脉 → story
- 围绕某个人物/公司/事件的历史进程展开（即使是访谈或多人讨论形式）→ story
- 多位嘉宾各自回忆、讲述自己的人生经历或个人故事 → story
- 大量出现"我认为""从数据来看""这个观点""方法是"等论证分析 → opinion
- 讨论某个话题的多个角度、方法论、行业趋势 → opinion
- 注意：即使是围绕嘉宾展开的访谈，如果嘉宾主要在输出观点而非讲述经历，也应判定为 opinion
- 注意：多人共同讲述一个"他者"的故事（人物传记式、公司史、历史叙事），仍然是 story
- 注意：多人圆桌中，如果每位嘉宾主要是分享各自的亲身经历而非抽象论述，整体仍然是 story

### voice_format — 内容组织方式（二选一）

- `dialogue` — 有明确主客关系（主持人引导/提问，嘉宾回答），对话的问答节奏本身就是内容结构的一部分
- `prose` — 无明确主客关系（双主播平等讨论、单人独白等），内容需要按主题/论点重新组织，而非按对话流

两种格式都保留说话人明确标注（**说话人：**），区别仅在于内容组织方式。

判断线索：
- 是否存在明确的主持人角色引导话题、发起提问（有 → dialogue）
- 两人/多人地位平等，轮流发表观点而非一方提问一方回答（→ prose）
- 单人播客（→ prose）
- 注意：dialogue 不等于"多人"，关键在于有没有主客角色区分

## 任务二：说话人识别

根据元数据和对话内容，建立 ASR 说话人标签到真实姓名的映射。

### 核心原则

**speaker_mapping 只能包含在音频中实际说话的人。** ASR 已通过声纹分析告诉你有几位说话人（见上方"ASR 识别到的说话人数"），speaker_mapping 的条目数不能超过这个数量。

**区分"说话的人"和"被提到的人"**：对话中会提到很多人名（讨论某人的观点、引用某人的话、提及某个行业人物），这些被提及的人并不是说话人，绝对不能出现在 speaker_mapping 中。

**别名处理**：如果某人有中英文名或别名（如"季逸超"又叫"Peak"），在 speaker_mapping 中只使用一个名字（优先中文名）。

### 第一步：身份映射

判断每个"说话人X"对应的真实人物：

1. **开场白/自我介绍**：重点关注对话开头的自我介绍和相互称呼，这是最可靠的信号
2. **行为模式**：持续提问、引导话题、做总结过渡 → 主持人；持续分享个人经历、输出专业观点、被提问后回答 → 嘉宾
3. **内容与元数据对照**：涉及个人背景时与元数据比对（如"我在XX公司创业"应与嘉宾的公司信息匹配；"我们播客上期讲了..."通常是主持人说的）
4. **节目简介中的角色描述**：元数据中的主持人/嘉宾信息优先级高
5. **说话风格一致性**：每个人有相对固定的口头禅、用词习惯和说话节奏，可用于交叉验证
6. **第三人称悖论**：说话人不会用第三人称指代自己——如果"说话人1"说"小明觉得..."，但小明是嘉宾，那这句话很可能是主持人在转述，而非"说话人1"就是小明
7. **称呼对方的方式**：A 如何称呼 B，可以反推 A 的身份（如"您今天能来参加我们节目..."说明说话者是主持人）
8. **【关键】不要把"被谈论的人"映射为说话人**：如果对话中只是提到某人的名字（如"肖弘之前做了一个项目"），但这个人并不是对话的参与者，不要将任何说话人标签映射到此人

### 第二步：ASR 标记可靠性评估

ASR 声纹识别不完美，可能把某人的话错误标记为另一人。在完成身份映射后，还需要评估采样片段中是否存在明显的 ASR 标记错误：

1. **内容一致性**：同一人不会在连续对话中自相矛盾；如果"说话人A"的某句话与其前后一贯的立场矛盾，可能是 ASR 错标
2. **问答逻辑**：主持人提问之后，下一句不应该还是同一人在回答自己的问题（除非是自问自答的设计）
3. **角色定位跨越**：如果某句话同时包含"提问引导"和"深度个人经历分享"两种特征，说明可能发生了说话人切换边界错标
4. **人称代词线索**（再次校验）：已确认为某人的标签，其话语中不应出现以该人名字为主语的第三人称叙述
5. **语言风格突变**：同一说话人标签下出现截然不同的语言风格，可能是边界处被错标
6. **上下文连贯性**：某句话明显是对上一句的直接回应，但说话人标签显示是同一人在"回应自己"，则需检查是否有错标
7. **元数据印证**：涉及个人经历（如工作经历、公司名）时，与元数据中各人的背景信息做最终比对

**修正保守原则**：
- 只在有明确证据时才在 reasoning 中指出可能的错标，不要过度猜测
- 如果无法确定，在 reasoning 中如实说明不确定性
- speaker_mapping 输出的是"最可信的整体映射"，不是逐片段的修正
- **speaker_mapping 条目数 ≤ ASR 识别到的说话人数**，不要凭空增加说话人

如果元数据中没有提供主持人和嘉宾信息，speaker_mapping 和 speaker_roles 可以为空字典，但仍需在 reasoning 中说明原因。

**【重要提醒】** 元数据中的嘉宾信息可能不准确（可能包含只是被讨论到但并未参与对话的人）。请以实际对话内容为准：只有在采样片段中能观察到其说话行为（有持续发言、与他人互动）的人，才能出现在 speaker_mapping 中。

---

请输出 JSON：
{{
    "content_type": "business | self_growth | humanities",
    "narrative_type": "story | opinion",
    "voice_format": "dialogue | prose",
    "speaker_roles": {{
        "说话人1": "host",
        "说话人2": "guest"
    }},
    "speaker_mapping": {{
        "说话人1": "真实姓名",
        "说话人2": "真实姓名"
    }},
    "reasoning": "简要说明分类和说话人判断依据"
}}

speaker_roles 说明：
- 每个说话人标签对应其角色："host"（主持人）或 "guest"（嘉宾）
- 角色判定基于行为模式：持续提问引导 → host，分享经历观点 → guest
- 双主播平等讨论的播客中，两人均标为 "host"
- 单人播客中，该说话人标为 "host"
- speaker_roles 和 speaker_mapping 的 key 应完全一致"""


def _build_speaker_distribution(segments: List[Dict]) -> str:
    """统计各说话人的片段数占比。"""
    if not segments:
        return "无数据"

    counts: Dict[str, int] = {}
    for seg in segments:
        sp = seg.get("speaker", "未知")
        counts[sp] = counts.get(sp, 0) + 1

    total = len(segments)
    parts = []
    for sp, count in sorted(counts.items(), key=lambda x: -x[1]):
        pct = count / total * 100
        parts.append(f"{sp}: {count}片段({pct:.0f}%)")
    return ", ".join(parts)


def _sample_segments(segments: List[Dict]) -> List[int]:
    """从 ASR 片段中采样索引：开头 20 + 中间 10 + 结尾 5。"""
    total = len(segments)
    if total == 0:
        return []

    indices: set = set()
    for i in range(min(20, total)):
        indices.add(i)
    mid = total // 2
    for i in range(max(0, mid - 5), min(total, mid + 5)):
        indices.add(i)
    for i in range(max(0, total - 5), total):
        indices.add(i)
    return sorted(indices)


async def classify_and_identify(
    segments: List[Dict],
    state: Dict[str, Any],
    metadata_context: str,
) -> Dict[str, Any]:
    """
    统一执行内容分类 + 说话人识别。

    Returns: {
        "content_type": str,
        "narrative_type": str,
        "voice_format": str,
        "speaker_mapping": dict,
        "reasoning": str,
    }
    """
    total = len(segments)
    if total == 0:
        return {
            "content_type": "business",
            "narrative_type": "opinion",
            "voice_format": "dialogue",
            "speaker_roles": {},
            "speaker_mapping": {},
            "reasoning": "无转写片段，使用默认值",
        }

    unique_speakers = set(seg.get("speaker", "未知") for seg in segments)

    sample_indices = _sample_segments(segments)
    lines = []
    for i in sample_indices:
        seg = segments[i]
        speaker = seg.get("speaker", "未知")
        text = seg.get("text", "")
        lines.append(f"[{i}] {speaker}: {text}")
    sample_text = "\n".join(lines)

    speaker_count = len(unique_speakers)
    prompt = CLASSIFY_PROMPT.format(
        metadata_context=metadata_context,
        total_segments=total,
        speaker_count=speaker_count,
        speaker_distribution=_build_speaker_distribution(segments),
        sample_text=sample_text,
    )

    try:
        llm_service = get_llm_service()
        result = await llm_service.generate_json(
            prompt=prompt,
            system_prompt=CLASSIFY_SYSTEM_PROMPT,
            temperature=0.1,
            model=settings.LLM_MODEL,
            max_tokens=65536,
            thinking_budget=0,
            timeout=60,
        )

        content_type = result.get("content_type", "business")
        narrative_type = result.get("narrative_type", "opinion")
        voice_format = result.get("voice_format", "dialogue")
        speaker_roles = result.get("speaker_roles", {})
        speaker_mapping = result.get("speaker_mapping", {})
        reasoning = result.get("reasoning", "")

        valid_content = ("business", "self_growth", "humanities")
        valid_narrative = ("story", "opinion")
        valid_voice = ("dialogue", "prose")

        if content_type not in valid_content:
            print(f"[classify] 未知 content_type '{content_type}'，回退到 business")
            content_type = "business"
        if narrative_type not in valid_narrative:
            print(f"[classify] 未知 narrative_type '{narrative_type}'，回退到 opinion")
            narrative_type = "opinion"
        if voice_format not in valid_voice:
            print(f"[classify] 未知 voice_format '{voice_format}'，回退到 dialogue")
            voice_format = "dialogue"

        # 校验 speaker_roles 值
        valid_roles = ("host", "guest")
        speaker_roles = {
            k: v for k, v in speaker_roles.items()
            if isinstance(v, str) and v in valid_roles
        }

        # 校验 speaker_mapping 条目数不超过 ASR 说话人数
        if speaker_mapping and len(speaker_mapping) > speaker_count:
            print(f"[classify] ⚠ speaker_mapping 条目数({len(speaker_mapping)}) > ASR 说话人数({speaker_count})，截断多余条目")
            sorted_entries = sorted(speaker_mapping.items(), key=lambda x: x[0])
            speaker_mapping = dict(sorted_entries[:speaker_count])

        # speaker_roles 和 speaker_mapping 的 key 应对齐
        all_keys = set(speaker_roles.keys()) | set(speaker_mapping.keys())
        for k in list(speaker_mapping.keys()):
            if k not in all_keys:
                continue
            if k not in speaker_roles:
                speaker_roles[k] = "guest"

        print(f"[classify] 分类完成:")
        print(f"  - content_type: {content_type}")
        print(f"  - narrative_type: {narrative_type}")
        print(f"  - voice_format: {voice_format}")
        if speaker_roles:
            for label, role in speaker_roles.items():
                print(f"  - {label} = {role}")
        if speaker_mapping:
            for label, name in speaker_mapping.items():
                print(f"  - {label} → {name}")
        if reasoning:
            print(f"  - 依据: {reasoning}")

        return {
            "content_type": content_type,
            "narrative_type": narrative_type,
            "voice_format": voice_format,
            "speaker_roles": speaker_roles,
            "speaker_mapping": speaker_mapping,
            "reasoning": reasoning,
        }
    except Exception as e:
        print(f"[classify] 分类失败，使用默认值: {e}")
        fallback_voice = "prose" if len(unique_speakers) <= 1 else "dialogue"
        return {
            "content_type": "business",
            "narrative_type": "opinion",
            "voice_format": fallback_voice,
            "speaker_roles": {},
            "speaker_mapping": {},
            "reasoning": f"分类失败 ({e})，回退到默认值",
        }


def build_deterministic_mapping(
    speaker_roles: Dict[str, str],
    llm_names: Dict[str, str],
    user_hosts: List[str],
    user_guests: List[str],
) -> Dict[str, str]:
    """根据角色识别结果和用户输入，生成最终的 speaker -> name 映射。

    单人角色 + 用户提供了对应角色的唯一名字 → 确定性赋值（不依赖 LLM 名字匹配）。
    多人同角色或用户未提供名字 → 使用 LLM 推断的名字。
    """
    host_labels = [k for k, v in speaker_roles.items() if v == "host"]
    guest_labels = [k for k, v in speaker_roles.items() if v == "guest"]
    mapping: Dict[str, str] = {}

    if len(host_labels) == 1 and len(user_hosts) == 1:
        mapping[host_labels[0]] = user_hosts[0]
    elif user_hosts and len(host_labels) == len(user_hosts):
        for i, label in enumerate(host_labels):
            mapping[label] = user_hosts[i]
    elif len(user_hosts) == 1 and len(host_labels) >= 1:
        mapping[host_labels[0]] = user_hosts[0]
        for label in host_labels[1:]:
            if label in llm_names:
                mapping[label] = llm_names[label]
    else:
        for label in host_labels:
            if label in llm_names:
                mapping[label] = llm_names[label]

    if len(guest_labels) == 1 and len(user_guests) == 1:
        mapping[guest_labels[0]] = user_guests[0]
    elif user_guests and len(guest_labels) == len(user_guests):
        for i, label in enumerate(guest_labels):
            mapping[label] = user_guests[i]
    elif len(user_guests) == 1 and len(guest_labels) >= 1:
        mapping[guest_labels[0]] = user_guests[0]
        for label in guest_labels[1:]:
            if label in llm_names:
                mapping[label] = llm_names[label]
    else:
        for label in guest_labels:
            if label in llm_names:
                mapping[label] = llm_names[label]

    # 兜底：speaker_roles 可能缺失某些 speaker，直接用 llm_names 补全
    for label, name in llm_names.items():
        if label not in mapping:
            mapping[label] = name

    return mapping


def validate_mapping_coverage(
    mapping: Dict[str, str],
    user_hosts: List[str],
    user_guests: List[str],
) -> List[str]:
    """检查 mapping 是否覆盖了所有用户提供的名字，返回缺失的名字列表。"""
    if not user_hosts and not user_guests:
        return []
    expected = set(user_hosts + user_guests)
    actual = set(mapping.values())
    return sorted(expected - actual)
