"""Smoke test for parser.render_customer_markdown with the new contact_info block.

    cd backend
    python -m workbench.podcast_direction._smoke_render
"""
from __future__ import annotations

import json
from pathlib import Path

from multi.podcast_direction.parser import render_customer_markdown


HERE = Path(__file__).resolve().parent
EXAMPLES = HERE / "examples"


def main() -> int:
    with (EXAMPLES / "output_example.json").open("r", encoding="utf-8") as f:
        full_json = json.load(f)

    full_json.pop("_note", None)

    print("=" * 70)
    print("CASE A: 完整 final_json（含 contact_info）")
    print("=" * 70)
    md_a = render_customer_markdown(full_json)
    print(md_a)
    print()

    print("=" * 70)
    print("CASE B: 兼容性 — 不含 contact_info 的旧 final_json")
    print("=" * 70)
    legacy = dict(full_json)
    legacy.pop("contact_info", None)
    md_b = render_customer_markdown(legacy)
    print(md_b)
    print()

    print("=" * 70)
    print("CASE C: contact_info 大部分字段为空（只有 podcast_name）")
    print("=" * 70)
    sparse = dict(full_json)
    sparse["contact_info"] = {
        "podcast_name": "孤岛播客",
        "host_names": [],
        "primary_host": "",
        "subscription_count": None,
        "wechat_personal_id": "",
        "wechat_official_account": "",
        "structured_contacts": [],
        "raw_contact_text": "",
        "extraction_warnings": ["节目详情末尾没有 / 联系方式块"],
    }
    md_c = render_customer_markdown(sparse)
    print(md_c)
    print()

    must_in_a = ["节目联系信息", "zhezhouli_echo", "18,420", "小柒", "褶皱里PleatedTimes"]
    bad_in_a = []
    for token in must_in_a:
        if token not in md_a:
            bad_in_a.append(token)
    if "节目联系信息" in md_b:
        bad_in_a.append("CASE B 不应该出现联系信息卡片")
    if "节目联系信息" not in md_c or "未在节目详情里识别到合法微信号" not in md_c:
        bad_in_a.append("CASE C 应该出现卡片但展示空状态")

    print("=" * 70)
    if bad_in_a:
        for b in bad_in_a:
            print(f"  [FAIL] {b}")
        print(f"smoke 完成：{len(bad_in_a)} 个断言失败")
        return 1
    print("[OK] 所有断言通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
