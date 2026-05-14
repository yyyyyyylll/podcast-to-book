"""
EchoPress 整档播客内容方向诊断工具 - Prompt 模板

设计原则（来自 DESIGN.md §五 / §八）：

1. 工作流 1+2+3（整档方向识别 / 方向与子主题梳理 / 标题与诊断生成）
   合并为一次主诊断 LLM 调用（DIAGNOSIS_*），按统一 JSON schema 一次产出。

2. 工作流 4（结果校验与降噪）拆成两段：
   - 硬规则部分由 parser.py 完成（不调 LLM）
   - AI 自检部分由本文件的 QC_* 一次 LLM 调用完成

3. 所有边界（不做单期诊断 / 不凭空创造 / 不承诺出版 / 必须取舍）写死在
   system + 硬性约束里，不依赖 LLM 自觉。
"""
from __future__ import annotations

import json
from typing import Any


DIAGNOSIS_SYSTEM = """你是 EchoPress 的首席播客内容战略顾问。

你拥有出版策划、内容品牌诊断、播客栏目分析和主题编辑经验。你的任务不是分析
某一期节目，也不是生成样册正文，而是根据整档播客已经提供的文字信息，判断
这档播客整体呈现出的内容方向、方向结构、可沉淀子主题，并给出后续内容整理
的标题建议。

【边界 - 必须严守】
- 不做单期节目主题诊断、不重命名某一期节目
- 不生成样册正文、不进行音频转写
- 不承诺正式出版、公开发行、销量、爆款、畅销、广受好评等传播效果
- 不凭空创造节目中没出现过的方向；每个方向都要有具体单集标题作为证据
- 不把零散内容强行包装成宏大主题
- 必须取舍：明确指出哪个方向优先、哪个方向不建议优先

【判断风格】
- 用编辑视角，不用营销包装视角
- 节制，避免「赋能」「破圈」「内核」「主义」这类自媒体拼词
- 给客户看的诊断是连贯文字，不分点、不要 emoji
"""


def build_diagnosis_user(
    *,
    podcast_name: str,
    podcast_intro: str,
    episode_list: list[dict[str, Any]],
    host_topic_references: list[dict[str, Any]] | None = None,
) -> str:
    """主诊断 user prompt：把节目资料拼成 LLM 可读的纯文本。

    用 f-string 直接拼接，不走 .format()——避免 schema 里的 {} 触发模板冲突。
    """
    episode_count = len(episode_list)
    lines: list[str] = []
    for i, ep in enumerate(episode_list, 1):
        title = (ep.get("title") or "").strip()
        intro = (ep.get("intro") or "").strip()
        if intro:
            lines.append(f"{i}. 《{title}》\n   导读：{intro}")
        else:
            lines.append(f"{i}. 《{title}》\n   （无导读）")
    episodes_text = "\n\n".join(lines)
    topic_lines: list[str] = []
    for topic in (host_topic_references or [])[:12]:
        title = (topic.get("topic_title") or "").strip()
        if not title:
            continue
        episode_titles = [
            (item or "").strip()
            for item in (topic.get("episode_titles") or [])[:8]
            if (item or "").strip()
        ]
        episode_hint = "；".join(episode_titles) if episode_titles else "未列出"
        topic_lines.append(
            f"- {title}（{topic.get('episode_count') or len(episode_titles)} 集）：{episode_hint}"
        )
    topics_text = "\n".join(topic_lines) if topic_lines else "（未识别到主播显式专题链接）"

    return f"""以下是一档播客的整体文字资料，请基于这些资料进行整档内容方向诊断。

# 播客名称
{podcast_name}

# 播客简介
{podcast_intro}

# 节目列表（共 {episode_count} 集）
{episodes_text}

# 主播已有专题/系列线索（强参考，但需要复核）
{topics_text}

以上专题来自节目 shownotes 中的小宇宙专题链接，代表主播可能已经做过的栏目分类。请把它作为判断方向结构的证据之一，但不要直接照搬；必须结合单集标题和导读复核其真实覆盖范围。如果专题只覆盖少数单集，或与整档主方向不一致，请在 internal_note 里说明。

---

# 任务

请按以下顺序完成 7 项任务，并严格按下方 JSON schema 返回。

1. 整档内容方向识别（overall_diagnosis）
2. 内容结构类型分类（必须四选一）
3. 主要方向梳理：每个方向拆子主题 + 列证据（episode_evidence 必须是真实出现过的单集标题）
4. 判断 priority_direction（最值得优先整理）+ not_priority_direction（不建议优先）
5. 三类标题建议：克制专业型 / 文艺表达型 / 清晰说明型，每类 2-3 个
6. 200-300 字客户可见诊断（连贯文字、不分点）
7. 给每个 direction 的 editing_value 评（极高 / 高，不能全部相同）

# 硬性约束

- `content_structure_type` 只能是以下四个枚举值之一：
    "单一主方向型"
    "多方向并行型"
    "主方向清晰但有若干分支型"
    "方向较散，暂不适合系统整理型"
- `direction_structure.priority_direction` 必须等于 directions 里某个 direction_name
- 每个 direction 至少 3 个 sub_topics + 至少 2 条 episode_evidence（用真实单集标题）
- `customer_diagnosis.diagnosis_text` 严格控制在 200-320 字
- 不允许出现：「正式出版」「公开发行」「销量」「爆款」「畅销」「广受好评」「打造爆款」等承诺词
- `editing_value` 只能使用「极高」或「高」，不能出现「中」或「低」
- `editing_value` 不能所有 direction 都标同一个值，必须有取舍；最优先整理方向通常为「极高」，其余方向可标「高」

# JSON schema（严格按此结构返回，不要在 JSON 外添加任何解释文字）

```json
{{
  "overall_diagnosis": {{
    "podcast_name": "{podcast_name}",
    "overall_content_judgment": "3-5 句整体内容判断",
    "content_structure_type": "四选一",
    "main_directions": ["方向 1", "方向 2"],
    "diagnosis_reason": "为什么这样判断，引用具体节目作为依据",
    "internal_note": "给 EchoPress 内部的提醒（哪些坑、哪些方向证据偏弱等）"
  }},
  "direction_structure": {{
    "structure_type": "和 content_structure_type 一致",
    "directions": [
      {{
        "direction_name": "方向名（短，4-8 字）",
        "direction_description": "1-2 句描述这个方向具体在讲什么",
        "sub_topics": ["子主题 1", "子主题 2", "子主题 3"],
        "episode_evidence": ["节目原标题 1", "节目原标题 2"],
        "editing_value": "极高 / 高"
      }}
    ],
    "priority_direction": "等于上面某个 direction_name",
    "priority_reason": "1-2 句说为什么优先",
    "not_priority_direction": "等于上面某个 direction_name",
    "not_priority_reason": "1-2 句说为什么不优先"
  }},
  "title_suggestions": {{
    "professional_titles": [
      {{"title": "《...》", "reason": "..."}}
    ],
    "literary_titles": [
      {{"title": "《...》", "reason": "..."}}
    ],
    "clear_titles": [
      {{"title": "《...》", "reason": "..."}}
    ]
  }},
  "customer_diagnosis": {{
    "diagnosis_text": "200-320 字连贯诊断文字，不分点。包括：整档播客整体方向、内容结构判断、最适合优先整理的方向、这个方向下的子主题、后续整理时需要注意的问题。",
    "recommended_direction": "和 priority_direction 一致",
    "recommended_sub_topics": ["子主题 1", "子主题 2"],
    "main_risk": "整理时主要风险（一句话）"
  }}
}}
```
"""


QC_SYSTEM = """你是 EchoPress 的内容质量审核员。你的任务是审视一份已经生成的
播客方向诊断结果，找出三类质量问题：

1. 过度拔高 / 包装：把零散内容强行说成宏大主题、使用了营销文案套话、
   出现了「赋能 / 破圈 / 内核 / 主义 / 觉醒」这类自媒体拼词
2. 凭空创造：诊断里出现的方向、子主题、episode_evidence，在原始节目资料里
   找不到依据
3. 内部一致性问题：priority_direction 不在 directions 里、editing_value
   出现「中/低」或全部相同缺乏取舍、customer_diagnosis 字数超标、四种结构类型选错等

你不重新生成诊断，只客观指出问题。如果整体没明显问题，可以返回空 ai_warnings。
"""


def build_qc_user(
    *,
    input_payload: dict[str, Any],
    diagnosis: dict[str, Any],
    max_episode_titles: int = 100,
) -> str:
    """QC 自检 user prompt。原始资料只列前 N 个标题，避免 prompt 过长。"""
    name = input_payload.get("podcast_name", "")
    intro = input_payload.get("podcast_intro", "")
    episodes = input_payload.get("episode_list", []) or []
    titles_preview = "\n".join(
        f"- {(ep.get('title') or '').strip()}"
        for ep in episodes[:max_episode_titles]
    )
    more_hint = (
        f"\n（还有 {len(episodes) - max_episode_titles} 集未列出）"
        if len(episodes) > max_episode_titles
        else ""
    )

    diagnosis_json = json.dumps(diagnosis, ensure_ascii=False, indent=2)

    return f"""# 原始资料（节目实际内容来源）

播客名称：{name}
播客简介：{intro}

节目列表共 {len(episodes)} 集，前 {min(len(episodes), max_episode_titles)} 集标题：
{titles_preview}{more_hint}

# 待审核的诊断结果

```json
{diagnosis_json}
```

# 任务

按以下 schema 返回（严格 JSON，不要在 JSON 外添加任何解释）：

```json
{{
  "ai_warnings": [
    "具体问题描述 1",
    "具体问题描述 2"
  ],
  "ai_confidence": "高 / 中 / 低",
  "ai_review_summary": "一句话总评（30 字以内）"
}}
```

判断 ai_confidence 的参考：
- 高：诊断扎实，没有明显拔高/凭空，结构清晰、有取舍
- 中：基本可用，但某些方向证据偏弱或子主题略勉强
- 低：有明显凭空创造、过度包装、或内部不一致
"""


PROMPTS = {
    "diagnosis": {
        "system": DIAGNOSIS_SYSTEM,
        "build_user": build_diagnosis_user,
    },
    "qc": {
        "system": QC_SYSTEM,
        "build_user": build_qc_user,
    },
}
