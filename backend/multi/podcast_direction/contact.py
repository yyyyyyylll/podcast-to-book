"""
EchoPress 整档播客方向诊断 - 联系方式提取模块（纯 Python 规则，不调 LLM）

职责
====

从一档播客的栏目页元数据里抽出 **EchoPress 内部用来联系主播的关键信息**：

- 播客节目名 / 主播名 / 订阅数（直接读 show.json 已有字段）
- 个人微信号（可直接加好友的 ID，如 ``yuexiafox``）—— 从描述末尾的
  ``/小助理vx：yuexiafox`` 这类行里正则提取
- 公众号名（如"岳下"）—— 主要来自 ``contacts[type=weixin].name``，需要
  对方在微信里手动搜索，**不是直接加好友的 ID**

公众号 vs 个人号
================

小宇宙的 ``contacts`` 数组里 ``type=weixin`` 项的 ``name`` 几乎都是公众号
名字（"岳下""单读"），不是个人号。真正能加好友的个人号通常写在节目详情
末尾的"/小助理vx：xxx"这种自定义联系方式行里——这是这个模块要重点抓的。

依赖
====

- 上游：``fetch_show`` 已经把栏目页元数据解析后落到了 ``show.json``，
  其中包括 ``title`` / ``description`` (含末尾联系方式块) / ``contacts`` /
  ``podcaster_names`` / ``subscription_count``。
- 本模块只读 dict，不去网，不调 LLM，不写文件。
"""
from __future__ import annotations

import re
from typing import Any


CONTACT_BLOCK_RE = re.compile(r"\n+/[^\n]*(?:\n/[^\n]*)*\s*$")
CONTACT_LINE_RE = re.compile(r"^/\s*([^：:\n]+?)\s*[：:]\s*(.+?)\s*$", re.MULTILINE)

WECHAT_KEYWORD_RE = re.compile(
    r"(?:小助理\s*)?(?:vx|wx|微信|wechat|微信号)",
    re.IGNORECASE,
)

WECHAT_ID_RE = re.compile(
    r"(?:小助理\s*)?(?:vx|wx|微信号|微信|wechat)"
    r"\s*(?:号|id)?"
    r"\s*[：:\-]?\s*"
    r"([A-Za-z][A-Za-z0-9_\-\.]{5,29})",
    re.IGNORECASE,
)

PURE_WECHAT_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_\-\.]{5,29}$")


_TYPE_HINTS: dict[str, list[str]] = {
    "weixin": ["微信公众号", "公众号", "微信", "wechat", "wx", "vx"],
    "weibo": ["微博", "weibo"],
    "xiaohongshu": ["小红书", "红书", "xhs"],
    "douyin": ["抖音", "douyin"],
    "bilibili": ["b站", "bilibili", "哔哩"],
    "email": ["邮箱", "email", "mail"],
    "qq": ["qq"],
    "url": ["网站", "网址", "链接", "主页"],
}


def _normalize_contact_type(label: str) -> str:
    """把 ``contacts`` 数组的 ``type`` 或者 description 行的 label
    统一标准化到一个英文标签。识别不出来就返回原文。"""
    if not label:
        return ""
    norm = label.strip().lower()
    for std_type, hints in _TYPE_HINTS.items():
        for h in hints:
            if h.lower() in norm:
                return std_type
    return label.strip()


def _slice_contact_block(description: str) -> str:
    """从节目详情末尾切出"/类型：内容"形式的连续联系方式块（含两侧空白）。
    如果末尾没有此类块就返回空串。"""
    if not description:
        return ""
    m = CONTACT_BLOCK_RE.search(description)
    return m.group(0).strip() if m else ""


def _is_plausible_wechat_id(candidate: str) -> bool:
    """微信个人号合法形态：字母开头、6-30 位、字母/数字/下划线/连字符/点。"""
    if not candidate:
        return False
    return bool(PURE_WECHAT_ID_RE.match(candidate.strip()))


def _extract_wechat_personal_id(text: str) -> tuple[str, list[str]]:
    """从一段自由文本（通常是节目详情末尾的联系方式块）里提取微信个人号。

    Returns:
        (best_match_id, all_candidates)
        - best_match_id: 第一个看起来最靠谱的微信号；找不到返回空串
        - all_candidates: 所有匹配候选，便于人工核对
    """
    if not text:
        return "", []

    candidates: list[str] = []
    for m in WECHAT_ID_RE.finditer(text):
        cand = (m.group(1) or "").strip()
        if _is_plausible_wechat_id(cand):
            candidates.append(cand)

    seen: set[str] = set()
    deduped: list[str] = []
    for c in candidates:
        if c not in seen:
            seen.add(c)
            deduped.append(c)

    return (deduped[0] if deduped else "", deduped)


def _classify_contact_block_lines(block: str) -> list[dict[str, str]]:
    """把"/类型：内容"块按行解析成结构化条目。"""
    out: list[dict[str, str]] = []
    if not block:
        return out
    for m in CONTACT_LINE_RE.finditer(block):
        label = (m.group(1) or "").strip()
        value = (m.group(2) or "").strip()
        if not value:
            continue
        out.append({
            "raw_label": label,
            "type": _normalize_contact_type(label),
            "value": value,
        })
    return out


def _merge_structured_contacts(
    sdk_contacts: list[dict[str, Any]],
    desc_lines: list[dict[str, str]],
) -> list[dict[str, str]]:
    """合并两个来源的联系方式：
    - sdk_contacts：小宇宙官方 ``contacts`` 字段（类型已标准化）
    - desc_lines：从节目详情末尾"/...:..."块里解析出来的行

    去重规则：(type, value) 完全相同则视为同一条；保留 SDK 来源的优先级（含 url）。
    """
    merged: list[dict[str, str]] = []
    seen_keys: set[tuple[str, str]] = set()

    for c in sdk_contacts or []:
        if not isinstance(c, dict):
            continue
        ctype = _normalize_contact_type(c.get("type", "") or "")
        name = (c.get("name") or "").strip()
        if not name:
            continue
        key = (ctype, name)
        if key in seen_keys:
            continue
        seen_keys.add(key)
        merged.append({
            "type": ctype,
            "name": name,
            "url": (c.get("url") or "").strip(),
            "note": (c.get("note") or "").strip(),
            "source": "sdk",
        })

    for line in desc_lines:
        ctype = line.get("type", "")
        value = line.get("value", "")
        if not value:
            continue
        key = (ctype, value)
        if key in seen_keys:
            continue
        seen_keys.add(key)
        merged.append({
            "type": ctype,
            "name": value,
            "url": "",
            "note": line.get("raw_label", ""),
            "source": "description",
        })

    return merged


def _pick_official_account(structured: list[dict[str, str]]) -> str:
    """从合并后的联系方式里挑出最像"微信公众号"的条目名。
    优先级：raw_label/note 命中"公众号" > type=weixin。"""
    for c in structured:
        note = (c.get("note") or "").strip()
        if "公众号" in note and c.get("name"):
            return c["name"]
    for c in structured:
        if c.get("type") == "weixin" and c.get("name"):
            return c["name"]
    return ""


def extract_contact_info(
    *,
    podcast_name: str,
    podcaster_names: list[str] | None,
    contacts: list[dict[str, Any]] | None,
    description: str,
    subscription_count: int | None,
) -> dict[str, Any]:
    """主入口：把节目元数据 → 标准化的 ``contact_info`` dict。

    所有参数都允许为 None / 空。任何字段缺失都不会抛错，只在
    ``extraction_warnings`` 里记录。

    Returns:
        ``contact_info`` 字典，schema 见 examples/output_example.json。
    """
    warnings: list[str] = []

    podcast_name = (podcast_name or "").strip()
    if not podcast_name:
        warnings.append("podcast_name 为空")

    host_names_raw = podcaster_names or []
    host_names = [h.strip() for h in host_names_raw if (h or "").strip()]
    primary_host = host_names[0] if host_names else ""
    if not host_names:
        warnings.append("podcaster_names 为空，未能识别主播")

    description = description or ""
    contact_block = _slice_contact_block(description)
    if not contact_block and not contacts:
        warnings.append(
            "节目详情末尾没有 / 联系方式块，contacts 也为空 —— 该节目可能未公开任何联系方式"
        )

    desc_lines = _classify_contact_block_lines(contact_block)
    structured = _merge_structured_contacts(contacts or [], desc_lines)

    wechat_personal_id, wechat_id_candidates = _extract_wechat_personal_id(description)
    if not wechat_personal_id:
        for c in structured:
            if c.get("type") in ("weixin", "url") and c.get("name"):
                cand = c["name"].strip()
                if _is_plausible_wechat_id(cand):
                    wechat_personal_id = cand
                    if cand not in wechat_id_candidates:
                        wechat_id_candidates.append(cand)
                    break
    if not wechat_personal_id:
        warnings.append("未提取到合法的微信个人号（可能只有公众号或都没留）")

    wechat_official_account = _pick_official_account(structured)
    if (
        wechat_official_account
        and wechat_personal_id
        and wechat_official_account.strip().lower() == wechat_personal_id.strip().lower()
    ):
        wechat_official_account = ""

    sub_count: int | None
    if subscription_count is None:
        sub_count = None
        warnings.append("subscription_count 缺失")
    else:
        try:
            sub_count = int(subscription_count)
        except (TypeError, ValueError):
            sub_count = None
            warnings.append(f"subscription_count 不是合法整数：{subscription_count!r}")

    return {
        "podcast_name": podcast_name,
        "host_names": host_names,
        "primary_host": primary_host,
        "subscription_count": sub_count,
        "wechat_personal_id": wechat_personal_id,
        "wechat_personal_id_candidates": wechat_id_candidates,
        "wechat_official_account": wechat_official_account,
        "structured_contacts": structured,
        "raw_contact_text": contact_block,
        "extraction_warnings": warnings,
    }
