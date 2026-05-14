"""
EchoPress 整档播客内容方向诊断工具 - 工作流四的规则部分 + 合并 + 客户 markdown 渲染

职责：
1. run_rule_checks(diagnosis)  → 7 条硬规则，返回 (errors, warnings)
2. merge_qc(rule_errors, rule_warnings, ai_qc) → 最终 quality_check 块
3. render_customer_markdown(final_json)  → 客户可见 markdown（5 张卡片）

纯 Python 规则，不调 LLM。
"""
from __future__ import annotations

from typing import Any

from multi.podcast_direction.value_labels import public_editing_value


PROMOTION_BLACKLIST = [
    "正式出版",
    "公开发行",
    "销量",
    "爆款",
    "畅销",
    "广受好评",
    "打造爆款",
    "畅销书",
    "出版发行",
]

VALID_STRUCTURE_TYPES: set[str] = {
    "单一主方向型",
    "多方向并行型",
    "主方向清晰但有若干分支型",
    "方向较散，暂不适合系统整理型",
}

DIAGNOSIS_TEXT_MIN = 150
DIAGNOSIS_TEXT_MAX = 350
DIAGNOSIS_TEXT_TARGET = (200, 320)


def _text_len(text: str | None) -> int:
    return len((text or "").strip())


def run_rule_checks(diagnosis: dict[str, Any]) -> tuple[list[str], list[str]]:
    """跑 7 条硬规则。

    Returns:
        (errors, warnings)
        - errors: 严重问题，强制人工 review
        - warnings: 提示性问题，可能可接受
    """
    errors: list[str] = []
    warnings: list[str] = []

    overall = diagnosis.get("overall_diagnosis") or {}
    structure = diagnosis.get("direction_structure") or {}
    titles = diagnosis.get("title_suggestions") or {}
    customer = diagnosis.get("customer_diagnosis") or {}
    directions: list[dict[str, Any]] = structure.get("directions") or []
    direction_names = [d.get("direction_name", "") for d in directions]

    priority = (structure.get("priority_direction") or "").strip()
    if not priority:
        errors.append("规则 1：priority_direction 为空")
    elif priority not in direction_names:
        errors.append(
            f"规则 1：priority_direction「{priority}」不在 directions 列表里"
            f"（合法值：{direction_names}）"
        )

    for d in directions:
        name = d.get("direction_name", "?")
        evidence = d.get("episode_evidence") or []
        if not evidence:
            warnings.append(f"规则 2：direction「{name}」的 episode_evidence 为空")
        elif len(evidence) < 2:
            warnings.append(
                f"规则 2：direction「{name}」的 episode_evidence 只有 {len(evidence)} 条（要求 ≥ 2）"
            )

    diag_text = customer.get("diagnosis_text", "") or ""
    diag_len = _text_len(diag_text)
    if diag_len > DIAGNOSIS_TEXT_MAX:
        warnings.append(
            f"规则 3：customer_diagnosis.diagnosis_text 字数 {diag_len} 超过 {DIAGNOSIS_TEXT_MAX}"
        )
    elif diag_len < DIAGNOSIS_TEXT_MIN:
        warnings.append(
            f"规则 3：customer_diagnosis.diagnosis_text 字数 {diag_len} 少于 {DIAGNOSIS_TEXT_MIN}"
            f"（目标 {DIAGNOSIS_TEXT_TARGET[0]}-{DIAGNOSIS_TEXT_TARGET[1]}）"
        )

    blob_parts = [
        overall.get("overall_content_judgment", ""),
        overall.get("diagnosis_reason", ""),
        overall.get("internal_note", ""),
        diag_text,
        customer.get("main_risk", ""),
        structure.get("priority_reason", ""),
        structure.get("not_priority_reason", ""),
    ]
    for cat in ("professional_titles", "literary_titles", "clear_titles"):
        for t in (titles.get(cat) or []):
            blob_parts.append(t.get("title", ""))
            blob_parts.append(t.get("reason", ""))
    blob = " ".join(blob_parts)
    hits = [w for w in PROMOTION_BLACKLIST if w in blob]
    if hits:
        errors.append(f"规则 4：命中承诺词黑名单 {hits}")

    if len(directions) > 1:
        public_values = [public_editing_value(d.get("editing_value")) for d in directions]
        lacks_choice = len(set(public_values)) == 1
        if lacks_choice:
            warnings.append(
                f"规则 5：所有 {len(directions)} 个 directions 的 editing_value 展示值都相同，缺乏取舍"
            )

    stype = (overall.get("content_structure_type") or "").strip()
    if not stype:
        errors.append("规则 6：content_structure_type 为空")
    elif stype not in VALID_STRUCTURE_TYPES:
        errors.append(
            f"规则 6：content_structure_type「{stype}」不是合法枚举值"
            f"（合法值：{sorted(VALID_STRUCTURE_TYPES)}）"
        )

    structure_stype = (structure.get("structure_type") or "").strip()
    if stype and structure_stype and stype != structure_stype:
        warnings.append(
            f"规则 6（一致性）：overall.content_structure_type「{stype}」"
            f"与 direction_structure.structure_type「{structure_stype}」不一致"
        )

    for d in directions:
        name = d.get("direction_name", "?")
        topics = d.get("sub_topics") or []
        if len(topics) < 3:
            warnings.append(
                f"规则 7：direction「{name}」的 sub_topics 只有 {len(topics)} 个（要求 ≥ 3）"
            )

    return errors, warnings


_CONFIDENCE_RANK = {"高": 3, "中": 2, "低": 1}
_RANK_TO_LABEL = {3: "高", 2: "中", 1: "低"}


def _normalize_confidence(value: str | None) -> str:
    if not value:
        return "中"
    v = value.strip()
    if v in _CONFIDENCE_RANK:
        return v
    if "高" in v:
        return "高"
    if "低" in v:
        return "低"
    return "中"


def _confidence_min(a: str, b: str) -> str:
    ra = _CONFIDENCE_RANK[_normalize_confidence(a)]
    rb = _CONFIDENCE_RANK[_normalize_confidence(b)]
    return _RANK_TO_LABEL[min(ra, rb)]


def merge_qc(
    rule_errors: list[str],
    rule_warnings: list[str],
    ai_qc: dict[str, Any] | None,
) -> dict[str, Any]:
    """合并硬规则与 AI 自检结果，输出最终 quality_check 块。

    Confidence 收敛：
    - rule_errors 非空 → rule_confidence = "低"
    - rule_warnings ≥ 3 → rule_confidence = "中"
    - rule_warnings 1-2 → rule_confidence = "中"
    - rule_warnings = 0 → rule_confidence = "高"
    - 最终 confidence_level = min(rule_confidence, ai_confidence)

    Manual review 触发：
    - 任何 rule_error
    - 总 warning ≥ 3
    - AI confidence == "低"
    - 最终 confidence_level == "低"
    """
    ai_qc = ai_qc or {}
    ai_warnings: list[str] = ai_qc.get("ai_warnings") or []
    ai_confidence_raw: str = ai_qc.get("ai_confidence", "中") or "中"
    ai_confidence = _normalize_confidence(ai_confidence_raw)
    ai_summary: str = ai_qc.get("ai_review_summary", "") or ""

    rule_warning_items = [f"[RULE-ERROR] {w}" for w in rule_errors] + [
        f"[RULE-WARN] {w}" for w in rule_warnings
    ]
    ai_warning_items = [f"[AI] {w}" for w in ai_warnings]
    all_warnings = rule_warning_items + ai_warning_items

    if rule_errors:
        rule_confidence = "低"
    elif len(rule_warnings) >= 3:
        rule_confidence = "中"
    elif rule_warnings:
        rule_confidence = "中"
    else:
        rule_confidence = "高"

    final_confidence = _confidence_min(rule_confidence, ai_confidence)
    manual_review_needed = (
        bool(rule_errors)
        or len(all_warnings) >= 3
        or ai_confidence == "低"
        or final_confidence == "低"
    )

    return {
        "warnings": all_warnings,
        "rule_errors_count": len(rule_errors),
        "rule_warnings_count": len(rule_warnings),
        "ai_warnings_count": len(ai_warnings),
        "ai_confidence": ai_confidence,
        "ai_review_summary": ai_summary,
        "rule_confidence": rule_confidence,
        "confidence_level": final_confidence,
        "manual_review_needed": manual_review_needed,
    }


def render_customer_markdown(final_json: dict[str, Any]) -> str:
    """5 张卡片 markdown（DESIGN.md §十一）。

    第 5 张卡片 = 客户可见诊断 = 可一键复制部分。
    """
    overall = final_json.get("overall_diagnosis") or {}
    structure = final_json.get("direction_structure") or {}
    titles = final_json.get("title_suggestions") or {}
    customer = final_json.get("customer_diagnosis") or {}

    podcast_name = overall.get("podcast_name", "") or "（未填写播客名）"

    parts: list[str] = []
    parts.append(f"# 《{podcast_name}》内容方向诊断")
    parts.append("")

    parts.append("## 1. 整体内容判断")
    parts.append("")
    parts.append(overall.get("overall_content_judgment", "（无）"))
    parts.append("")
    parts.append(f"**内容结构类型**：{overall.get('content_structure_type', '—')}")
    parts.append("")

    parts.append("## 2. 主要内容方向")
    parts.append("")
    main_dirs = overall.get("main_directions") or []
    if main_dirs:
        for d in main_dirs:
            parts.append(f"- {d}")
    else:
        parts.append("（无）")
    parts.append("")

    parts.append("## 3. 方向与子主题")
    parts.append("")
    for d in (structure.get("directions") or []):
        parts.append(
            f"### {d.get('direction_name', '?')}　"
            f"_整理价值：{public_editing_value(d.get('editing_value'))}_"
        )
        desc = d.get("direction_description", "")
        if desc:
            parts.append("")
            parts.append(desc)
        topics = d.get("sub_topics") or []
        if topics:
            parts.append("")
            parts.append("**子主题**：" + "、".join(topics))
        evidence = d.get("episode_evidence") or []
        if evidence:
            parts.append("")
            parts.append("**节目证据**：" + "、".join(f"《{e}》" for e in evidence[:5]))
        parts.append("")
    if structure.get("priority_direction"):
        parts.append(
            f"**优先整理方向**：{structure.get('priority_direction')}　"
            f"_{structure.get('priority_reason', '')}_"
        )
        parts.append("")
    if structure.get("not_priority_direction"):
        parts.append(
            f"**不建议优先**：{structure.get('not_priority_direction')}　"
            f"_{structure.get('not_priority_reason', '')}_"
        )
        parts.append("")

    parts.append("## 4. 建议标题")
    parts.append("")
    for cat_key, cat_label in [
        ("professional_titles", "克制专业型"),
        ("literary_titles", "文艺表达型"),
        ("clear_titles", "清晰说明型"),
    ]:
        items = titles.get(cat_key) or []
        if not items:
            continue
        parts.append(f"### {cat_label}")
        for t in items:
            parts.append(f"- {t.get('title', '')}　_{t.get('reason', '')}_")
        parts.append("")

    parts.append("## 5. 客户可见诊断（可一键复制发给主播）")
    parts.append("")
    diag_text = (customer.get("diagnosis_text", "") or "（无）").strip()
    for line in diag_text.split("\n"):
        parts.append(f"> {line}")
    parts.append("")
    if customer.get("recommended_direction"):
        parts.append(f"**建议优先整理方向**：{customer.get('recommended_direction')}")
    rec_topics = customer.get("recommended_sub_topics") or []
    if rec_topics:
        parts.append("**建议子主题**：" + "、".join(rec_topics))
    if customer.get("main_risk"):
        parts.append(f"**主要风险**：{customer.get('main_risk')}")
    parts.append("")

    contact = final_json.get("contact_info") or {}
    if contact:
        parts.append("---")
        parts.append("")
        parts.append("## 节目联系信息（内部用，不发给客户）")
        parts.append("")
        parts.append(f"- 节目名：**{contact.get('podcast_name', '—') or '—'}**")
        host_names = contact.get("host_names") or []
        host_label = "、".join(host_names) if host_names else "—"
        parts.append(f"- 主播：**{host_label}**")
        sub_count = contact.get("subscription_count")
        sub_label = f"{sub_count:,}" if isinstance(sub_count, int) else "—"
        parts.append(f"- 订阅数：**{sub_label}**")
        wechat_id = contact.get("wechat_personal_id") or ""
        if wechat_id:
            parts.append(f"- 微信号（个人）：`{wechat_id}`")
        else:
            parts.append("- 微信号（个人）：— _未在节目详情里识别到合法微信号_")
        oa = contact.get("wechat_official_account") or ""
        if oa:
            parts.append(f"- 公众号：{oa}")
        structured = contact.get("structured_contacts") or []
        if structured:
            parts.append("")
            parts.append("**全部联系方式：**")
            for c in structured:
                ctype = c.get("type", "—")
                name = c.get("name", "")
                note = c.get("note", "")
                url = c.get("url", "")
                bits: list[str] = []
                if note:
                    bits.append(f"_{note}_")
                if url:
                    bits.append(f"<{url}>")
                tail = "（" + " · ".join(bits) + "）" if bits else ""
                parts.append(f"- [{ctype}] {name}{tail}")
        warnings_contact = contact.get("extraction_warnings") or []
        if warnings_contact:
            parts.append("")
            parts.append("<details><summary>联系方式提取警告</summary>")
            parts.append("")
            for w in warnings_contact:
                parts.append(f"- {w}")
            parts.append("")
            parts.append("</details>")
        parts.append("")

    qc = final_json.get("quality_check") or {}
    if qc:
        parts.append("---")
        parts.append("")
        parts.append("## 内部质检（不发给客户）")
        parts.append("")
        parts.append(f"- 置信度：**{qc.get('confidence_level', '—')}**")
        parts.append(f"- 是否需人工 review：**{qc.get('manual_review_needed', False)}**")
        parts.append(
            f"- Warnings：{qc.get('rule_errors_count', 0)} errors / "
            f"{qc.get('rule_warnings_count', 0)} rule-warns / "
            f"{qc.get('ai_warnings_count', 0)} ai-warns"
        )
        if qc.get("ai_review_summary"):
            parts.append(f"- AI 总评：{qc.get('ai_review_summary')}")
        warnings_list = qc.get("warnings") or []
        if warnings_list:
            parts.append("")
            parts.append("<details><summary>Warning 详情</summary>")
            parts.append("")
            for w in warnings_list:
                parts.append(f"- {w}")
            parts.append("")
            parts.append("</details>")
        parts.append("")

    return "\n".join(parts)
