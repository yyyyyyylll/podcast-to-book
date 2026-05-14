"""Smoke test for contact.extract_contact_info.

仅用来手测正则覆盖几个典型用例。**这个文件不是测试套件**，只是开发期手跑：

    cd backend
    python -m workbench.podcast_direction._smoke_contact
"""
from __future__ import annotations

import json

from multi.podcast_direction.contact import extract_contact_info


CASES: list[dict] = [
    {
        "_case": "1) 用户截图同款：公众号 + 小红书 + 小助理vx",
        "podcast_name": "岳下 | 户外频道",
        "podcaster_names": ["老柴"],
        "subscription_count": 12345,
        "contacts": [
            {"type": "weixin", "name": "岳下（各大渠道均可搜索获得）", "note": "订阅公众号"},
        ],
        "description": (
            "我们相信，大自然是人们获取勇气和力量的无尽源泉。\n"
            "我们喜欢所有热爱大自然的人，在这里，我们会分享——"
            "登山、徒步、滑雪、潜水、攀岩、越野跑、帆船、冲浪、钓鱼、飞行、跳伞、射击、龙舟。\n"
            "\n"
            "/微信公众号：岳下（各大渠道均可搜索获得）\n"
            "/小红书：岳下Submontane\n"
            "/小助理vx：yuexiafox"
        ),
        "_expect": {"wechat_personal_id": "yuexiafox", "wechat_official_account": "岳下（各大渠道均可搜索获得）"},
    },
    {
        "_case": "2) 简单 / 微信：echohello",
        "podcast_name": "测试播客",
        "podcaster_names": ["小明"],
        "subscription_count": 0,
        "contacts": [],
        "description": "本播客聊技术与生活。\n\n/微信：echohello",
        "_expect": {"wechat_personal_id": "echohello", "wechat_official_account": ""},
    },
    {
        "_case": "3) 没有任何联系方式",
        "podcast_name": "孤岛播客",
        "podcaster_names": ["A"],
        "subscription_count": 99,
        "contacts": [],
        "description": "我们就只是聊聊天。",
        "_expect": {"wechat_personal_id": "", "wechat_official_account": ""},
    },
    {
        "_case": "4) 只有公众号没个人号",
        "podcast_name": "单读",
        "podcaster_names": ["吴琦"],
        "subscription_count": 200000,
        "contacts": [{"type": "weixin", "name": "单读", "note": "微信公众号"}],
        "description": "单读由作家吴琦主理。\n\n/微信公众号：单读",
        "_expect": {"wechat_personal_id": "", "wechat_official_account": "单读"},
    },
    {
        "_case": "5) 大小写混合 + WX: 提取",
        "podcast_name": "随便聊聊",
        "podcaster_names": ["B"],
        "subscription_count": 88,
        "contacts": [],
        "description": "欢迎听众和我们交流。\n\n/合作WX：echo_press_2026",
        "_expect": {"wechat_personal_id": "echo_press_2026", "wechat_official_account": ""},
    },
    {
        "_case": "6) 微信号：开头需是字母（数字开头不抓）",
        "podcast_name": "测试2",
        "podcaster_names": ["C"],
        "subscription_count": 1,
        "contacts": [],
        "description": "联系我们\n\n/vx：123456789",
        "_expect": {"wechat_personal_id": ""},
    },
]


def main() -> int:
    bar = "=" * 70
    fail = 0
    for case in CASES:
        expect = case.pop("_expect", {})
        title = case.pop("_case")
        print(bar)
        print(title)
        print(bar)
        result = extract_contact_info(
            podcast_name=case["podcast_name"],
            podcaster_names=case["podcaster_names"],
            contacts=case["contacts"],
            description=case["description"],
            subscription_count=case["subscription_count"],
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        print()

        for k, v in expect.items():
            actual = result.get(k)
            ok = actual == v
            mark = "OK" if ok else "FAIL"
            print(f"  [{mark}] {k}: expected={v!r}, actual={actual!r}")
            if not ok:
                fail += 1
        print()

    print(bar)
    print(f"smoke 完成：{fail} 个断言失败")
    return 1 if fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
