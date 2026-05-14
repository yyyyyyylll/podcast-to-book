"""
不启动服务器、不打腾讯云的冷却逻辑验证脚本。

用法（在 backend/ 下）：
    source venv/bin/activate   # 或者激活你现有的 venv
    python tests/test_sms_cooldown.py

这个脚本 monkey-patch 了 _send_tencent_sms，模拟平台返回各种错误码，
然后调用 send_code，检查：
  1. 正常发送后，我方 60 秒 cooldown 生效
  2. 腾讯云返回 PhoneNumberOneHourLimit → 我方锁 1 小时
  3. 腾讯云返回 PhoneNumberDailyLimit → 我方锁到次日 0 点（北京时间）
  4. 被锁期间用户再次请求，直接被我方挡回，错误文案友好
  5. retry_after 字段正确回传（供 Retry-After 响应头使用）
"""
import asyncio
import os
import sys
import time
from pathlib import Path

# 让脚本能从任意位置运行
HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

from single.services import sms_service  # noqa: E402

# 不管 .env 有没有配置腾讯云，本测试里一律走"生产分支"（调 _send_tencent_sms），
# 由我们下面的 fake_tencent 来模拟平台返回。
sms_service.is_dev_mode = lambda: False


# --- 工具 ---------------------------------------------------------------

PHONE = "19396113949"
IP = "192.168.0.1"

GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
RESET = "\033[0m"

pass_count = 0
fail_count = 0


def check(label: str, cond: bool, detail: str = ""):
    global pass_count, fail_count
    if cond:
        print(f"  {GREEN}✓{RESET} {label}")
        pass_count += 1
    else:
        print(f"  {RED}✗ {label} — {detail}{RESET}")
        fail_count += 1


def reset_state():
    sms_service._code_store.clear()
    sms_service._send_cooldown.clear()
    sms_service._ip_sends.clear()
    sms_service._verify_fails.clear()


def fake_tencent(return_code: str):
    """返回一个 async 函数，模拟 _send_tencent_sms 的返回。"""

    async def _fake(phone, code):
        if return_code == "Ok":
            return {"ok": True, "error": "", "lock_seconds": 0}
        friendly, lock_seconds = sms_service._TENCENT_ERROR_TABLE.get(
            return_code, ("短信发送失败，请稍后重试", 0)
        )
        if lock_seconds < 0:
            lock_seconds = sms_service._seconds_until_next_cst_day()
        phone_masked = phone[:3] + "****" + phone[-4:]
        print(f"    [FAKE SMS ERROR] {phone_masked}: {return_code}")
        return {"ok": False, "error": friendly, "lock_seconds": lock_seconds}

    return _fake


# --- 场景 ---------------------------------------------------------------


async def scenario_1_normal_cooldown():
    print(f"\n{YELLOW}场景 1：正常发送后 60 秒冷却生效{RESET}")
    reset_state()
    sms_service._send_tencent_sms = fake_tencent("Ok")

    r1 = await sms_service.send_code(PHONE, IP)
    check("第 1 次发送返回 ok=True", r1["ok"], detail=str(r1))

    unlock_ts = sms_service._send_cooldown[PHONE]
    remaining = unlock_ts - time.time()
    check(
        "cooldown 锁定约 60 秒",
        58 <= remaining <= 60,
        detail=f"remaining={remaining:.1f}s",
    )

    r2 = await sms_service.send_code(PHONE, IP)
    check("第 2 次立即请求被挡回（ok=False）", not r2["ok"], detail=str(r2))
    check("错误文案含'请求次数过多'", "请求次数过多" in r2["error"], detail=r2["error"])
    check(
        "返回 retry_after 约 60 秒",
        55 <= r2.get("retry_after", 0) <= 60,
        detail=str(r2),
    )


async def scenario_2_one_hour_limit():
    print(f"\n{YELLOW}场景 2：腾讯云 1 小时限流 → 我方锁 1 小时{RESET}")
    reset_state()
    sms_service._send_tencent_sms = fake_tencent(
        "LimitExceeded.PhoneNumberOneHourLimit"
    )

    r1 = await sms_service.send_code(PHONE, IP)
    check("发送返回 ok=False", not r1["ok"], detail=str(r1))
    check(
        "retry_after 约 3600 秒",
        3500 <= r1.get("retry_after", 0) <= 3600,
        detail=str(r1),
    )
    check("文案含'1 小时'", "1 小时" in r1["error"], detail=r1["error"])

    unlock_ts = sms_service._send_cooldown[PHONE]
    remaining = unlock_ts - time.time()
    check(
        "我方 cooldown 被延长到约 1 小时",
        3500 <= remaining <= 3600,
        detail=f"remaining={remaining:.1f}s",
    )

    # 再发一次，应该被我方直接挡回，不再打腾讯云（fake 层也不会被调用）
    called = {"n": 0}
    original = sms_service._send_tencent_sms

    async def spy(phone, code):
        called["n"] += 1
        return await original(phone, code)

    sms_service._send_tencent_sms = spy
    r2 = await sms_service.send_code(PHONE, IP)
    check("第 2 次请求被挡回", not r2["ok"], detail=str(r2))
    check(
        "我方直接挡回，未再调用腾讯云",
        called["n"] == 0,
        detail=f"called={called['n']}",
    )
    check(
        "文案依然提示'请求次数过多，请 ... 后重试'",
        "请求次数过多" in r2["error"] and ("小时" in r2["error"] or "分钟" in r2["error"]),
        detail=r2["error"],
    )


async def scenario_3_daily_limit():
    print(f"\n{YELLOW}场景 3：腾讯云日限流 → 我方锁到次日 0 点（北京时间）{RESET}")
    reset_state()
    sms_service._send_tencent_sms = fake_tencent(
        "LimitExceeded.PhoneNumberDailyLimit"
    )

    r = await sms_service.send_code(PHONE, IP)
    check("发送返回 ok=False", not r["ok"], detail=str(r))
    check("文案含'今日'", "今日" in r["error"], detail=r["error"])

    unlock_ts = sms_service._send_cooldown[PHONE]
    remaining = unlock_ts - time.time()
    # 剩余应 = 距离下一个 CST 0 点的秒数。保守：至少 60，最多 86400
    check(
        "cooldown 被锁到次日 0 点（60 ~ 86400 秒）",
        60 <= remaining <= 86400,
        detail=f"remaining={remaining:.1f}s",
    )
    expected = sms_service._seconds_until_next_cst_day()
    # retry_after 是发送当时算的，我们这里再算一次，应该非常接近
    check(
        "retry_after 与次日 0 点秒数接近",
        abs(r.get("retry_after", 0) - expected) <= 2,
        detail=f"retry_after={r.get('retry_after')}, expected≈{expected}",
    )


async def scenario_4_thirty_second_no_extra_lock():
    print(f"\n{YELLOW}场景 4：腾讯云 30s 限流 → 我方不额外延长（我方 60s 已更严）{RESET}")
    reset_state()
    sms_service._send_tencent_sms = fake_tencent(
        "LimitExceeded.PhoneNumberThirtySecondLimit"
    )

    r = await sms_service.send_code(PHONE, IP)
    check("发送返回 ok=False", not r["ok"], detail=str(r))
    unlock_ts = sms_service._send_cooldown[PHONE]
    remaining = unlock_ts - time.time()
    check(
        "cooldown 保持 60 秒（未被降低，也未被无意义延长）",
        55 <= remaining <= 60,
        detail=f"remaining={remaining:.1f}s",
    )


async def scenario_5_format_hint():
    print(f"\n{YELLOW}场景 5：文案格式化工具函数{RESET}")
    fh = sms_service._format_retry_hint
    check("30 秒 → '30 秒'", fh(30) == "30 秒", fh(30))
    check("60 秒 → '1 分钟'", fh(60) == "1 分钟", fh(60))
    check("125 秒 → '2 分钟'", fh(125) == "2 分钟", fh(125))
    check("3600 秒 → '1 小时'", fh(3600) == "1 小时", fh(3600))
    check("7200 秒 → '2 小时'", fh(7200) == "2 小时", fh(7200))


async def main():
    print("=" * 60)
    print("SMS 冷却逻辑验证（离线、不打腾讯云、不开后端）")
    print("=" * 60)
    await scenario_1_normal_cooldown()
    await scenario_2_one_hour_limit()
    await scenario_3_daily_limit()
    await scenario_4_thirty_second_no_extra_lock()
    await scenario_5_format_hint()

    print("\n" + "=" * 60)
    total = pass_count + fail_count
    if fail_count == 0:
        print(f"{GREEN}全部通过：{pass_count}/{total}{RESET}")
        sys.exit(0)
    else:
        print(f"{RED}失败 {fail_count} / 共 {total}{RESET}")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
