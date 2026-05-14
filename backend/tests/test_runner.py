#!/usr/bin/env python3
"""
进程隔离测试运行器 — 管理 CLI

核心原理：每个测试任务在独立的 Python 子进程中运行。
Python 在进程启动时加载所有模块到内存，之后对 .py 源码的修改
不会影响已运行的进程。这样你可以：

  1. 启动一个耗时的全流程测试
  2. 继续修改代码、迭代开发
  3. 启动新的测试任务（自动使用最新代码）
  4. 同时运行多个测试，互不影响

用法：
  cd backend

  # ── 启动全流程测试 ──
  python -m tests.test_runner run <podcast_url>
  python -m tests.test_runner run <podcast_url> --label "测试插图v2"

  # ── 从断点恢复 ──
  python -m tests.test_runner run --from-state tests/artifacts/run_xxx/state.json --start-from illustration
  python -m tests.test_runner run --from-state tests/artifacts/run_xxx/state.json --only illustration,typeset

  # ── 监控 ──
  python -m tests.test_runner list                # 所有测试
  python -m tests.test_runner status <run_id>     # 详细状态
  python -m tests.test_runner logs <run_id>       # 查看日志
  python -m tests.test_runner logs <run_id> -f    # 实时跟踪

  # ── 管理 ──
  python -m tests.test_runner kill <run_id>       # 终止测试
  python -m tests.test_runner clean               # 清理旧测试（保留最近 5 个）
"""
import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from uuid import uuid4

ARTIFACTS_DIR = Path(__file__).resolve().parent / "artifacts"


def _find_run_dir(run_id: str) -> Path:
    """通过 run_id（支持前缀匹配）找到对应目录"""
    if not ARTIFACTS_DIR.exists():
        print(f"未找到测试: {run_id}")
        sys.exit(1)

    candidates = sorted(ARTIFACTS_DIR.glob(f"run_{run_id}*"))
    if len(candidates) == 1:
        return candidates[0]
    if len(candidates) > 1:
        names = [c.name.replace("run_", "") for c in candidates]
        print(f"run_id '{run_id}' 匹配多个: {', '.join(names)}")
        print("请提供更长的前缀以唯一匹配。")
        sys.exit(1)

    print(f"未找到测试: {run_id}")
    sys.exit(1)


def _read_status(run_dir: Path) -> dict:
    status_file = run_dir / "status.json"
    if status_file.exists():
        try:
            return json.loads(status_file.read_text())
        except Exception:
            pass
    return {}


def _format_elapsed(seconds: float) -> str:
    if seconds >= 3600:
        return f"{seconds/3600:.1f}h"
    if seconds >= 60:
        return f"{seconds/60:.1f}m"
    return f"{seconds:.0f}s"


# ──────────────────────────────────────────────────────────────
# Commands
# ──────────────────────────────────────────────────────────────

def cmd_run(args):
    run_id = uuid4().hex[:8]
    run_dir = ARTIFACTS_DIR / f"run_{run_id}"
    run_dir.mkdir(parents=True, exist_ok=True)

    config = {
        "run_id": run_id,
        "output_dir": str(run_dir),
        "podcast_url": args.url,
        "from_state": None,
        "start_from": args.start_from or "metadata",
        "only_nodes": args.only.split(",") if args.only else None,
        "label": args.label,
        "created_at": datetime.now().isoformat(),
    }

    if args.from_state:
        state_src = Path(args.from_state).resolve()
        if not state_src.exists():
            print(f"状态文件不存在: {state_src}")
            sys.exit(1)
        state_dst = run_dir / "initial_state.json"
        shutil.copy2(state_src, state_dst)
        config["from_state"] = str(state_dst)
        if not args.start_from and not args.only:
            config["start_from"] = "enrich"

    if not args.url and not args.from_state:
        print("错误: 必须提供播客链接或 --from-state")
        sys.exit(1)

    (run_dir / "config.json").write_text(
        json.dumps(config, ensure_ascii=False, indent=2)
    )

    # 启动 worker 子进程
    log_file_path = run_dir / "output.log"
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"

    backend_dir = str(Path(__file__).resolve().parent.parent)

    with open(log_file_path, "w") as lf:
        proc = subprocess.Popen(
            [sys.executable, "-m", "tests.test_worker", run_id],
            stdout=lf,
            stderr=subprocess.STDOUT,
            env=env,
            cwd=backend_dir,
        )

    # 写入初始 status（含 PID 供 kill 使用）
    status = {
        "run_id": run_id,
        "pid": proc.pid,
        "status": "starting",
        "current_stage": "",
        "started_at": datetime.now().isoformat(),
        "podcast_url": args.url or "(from state)",
        "label": args.label,
    }
    (run_dir / "status.json").write_text(
        json.dumps(status, ensure_ascii=False, indent=2)
    )

    label_str = f"\n  标签:     {args.label}" if args.label else ""
    from_str = f"\n  恢复自:   {args.from_state}" if args.from_state else ""
    start_str = ""
    if args.start_from:
        start_str = f"\n  起始步骤: {args.start_from}"
    if args.only:
        start_str = f"\n  指定节点: {args.only}"

    print(f"""
┌─────────────────────────────────────────────────
│ 测试已启动
│
│ Run ID:   {run_id}
│ PID:      {proc.pid}
│ 输出目录: {run_dir}/{label_str}{from_str}{start_str}
│
│ 监控命令:
│   python -m tests.test_runner logs {run_id} -f
│   python -m tests.test_runner status {run_id}
│   python -m tests.test_runner list
│   python -m tests.test_runner kill {run_id}
└─────────────────────────────────────────────────""")


def cmd_list(args):
    if not ARTIFACTS_DIR.exists():
        print("暂无测试记录。")
        return

    runs = sorted(ARTIFACTS_DIR.glob("run_*"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not runs:
        print("暂无测试记录。")
        return

    STATUS_ICONS = {
        "completed": "✓",
        "failed": "✗",
        "running": "►",
        "starting": "…",
        "killed": "☠",
    }

    header = f"{'ID':<10} {'状态':<12} {'阶段':<20} {'标签/链接':<42} {'开始时间':<18} {'耗时':>8}"
    print(header)
    print("─" * len(header) + "─" * 10)

    for run_dir in runs:
        st = _read_status(run_dir)
        if not st:
            continue

        run_id = st.get("run_id", run_dir.name.replace("run_", ""))
        status = st.get("status", "?")
        icon = STATUS_ICONS.get(status, "?")
        stage = st.get("current_stage", "")
        label = st.get("label") or st.get("podcast_url", "")
        if len(label) > 42:
            label = label[:39] + "..."
        started = st.get("started_at", "")
        if started:
            started = started[:19].replace("T", " ")

        elapsed_str = ""
        if st.get("started_at"):
            try:
                started_dt = datetime.fromisoformat(st["started_at"])
                end_dt = (
                    datetime.fromisoformat(st["completed_at"])
                    if st.get("completed_at")
                    else datetime.now()
                )
                elapsed_str = _format_elapsed((end_dt - started_dt).total_seconds())
            except Exception:
                pass

        print(f"{run_id:<10} {icon} {status:<10} {stage:<20} {label:<42} {started:<18} {elapsed_str:>8}")


def cmd_status(args):
    run_dir = _find_run_dir(args.run_id)
    st = _read_status(run_dir)
    if not st:
        print("status.json 不存在或为空。")
        return

    status = st.get("status", "?")
    STATUS_ICONS = {
        "completed": "✓ 完成",
        "failed": "✗ 失败",
        "running": "► 运行中",
        "starting": "… 启动中",
        "killed": "☠ 已终止",
    }

    elapsed_str = ""
    if st.get("started_at"):
        try:
            started_dt = datetime.fromisoformat(st["started_at"])
            end_dt = (
                datetime.fromisoformat(st["completed_at"])
                if st.get("completed_at")
                else datetime.now()
            )
            elapsed_str = _format_elapsed((end_dt - started_dt).total_seconds())
        except Exception:
            pass

    print(f"""
Run ID:     {st.get('run_id', '?')}
状态:       {STATUS_ICONS.get(status, status)}
当前阶段:   {st.get('current_stage', '')}
PID:        {st.get('pid', '?')}
播客链接:   {st.get('podcast_url', '')}
标签:       {st.get('label') or '(无)'}
开始时间:   {st.get('started_at', '')}
完成时间:   {st.get('completed_at') or '(进行中)'}
总耗时:     {elapsed_str or '(计算中)'}
PDF:        {st.get('pdf_path') or '(未生成)'}
输出目录:   {run_dir}""")

    if st.get("timings"):
        print("\n各节点耗时:")
        for name, t in st["timings"].items():
            print(f"  {name:<25} {t:>8.1f}s")

    if st.get("error"):
        print(f"\n错误信息:\n  {st['error']}")

    if st.get("steps"):
        print(f"\n计划步骤: {' → '.join(st['steps'])}")


def cmd_logs(args):
    run_dir = _find_run_dir(args.run_id)
    log_file = run_dir / "output.log"
    if not log_file.exists():
        print("日志文件不存在。")
        return

    if not args.follow:
        print(log_file.read_text())
        return

    # Follow 模式：轮询读取新内容
    with open(log_file, "r") as f:
        content = f.read()
        if content:
            print(content, end="")

        try:
            while True:
                new = f.readline()
                if new:
                    print(new, end="")
                else:
                    st = _read_status(run_dir)
                    if st.get("status") not in ("starting", "running", ""):
                        remaining = f.read()
                        if remaining:
                            print(remaining, end="")
                        print(f"\n[test_runner] 测试已结束: {st.get('status', '?')}")
                        break
                    time.sleep(0.3)
        except KeyboardInterrupt:
            print("\n[test_runner] 停止跟踪")


def cmd_kill(args):
    run_dir = _find_run_dir(args.run_id)
    st = _read_status(run_dir)
    pid = st.get("pid")

    if not pid:
        print("无法获取 PID。")
        return

    if st.get("status") not in ("starting", "running"):
        print(f"测试不在运行中 (当前状态: {st.get('status')})")
        return

    try:
        os.kill(pid, signal.SIGTERM)
        print(f"已发送 SIGTERM 到 PID {pid}")
    except ProcessLookupError:
        print(f"进程 {pid} 不存在（可能已退出）")

    st["status"] = "killed"
    st["completed_at"] = datetime.now().isoformat()
    (run_dir / "status.json").write_text(
        json.dumps(st, ensure_ascii=False, indent=2)
    )


def cmd_clean(args):
    if not ARTIFACTS_DIR.exists():
        print("无需清理。")
        return

    runs = sorted(ARTIFACTS_DIR.glob("run_*"), key=lambda p: p.stat().st_mtime, reverse=True)
    keep = args.keep

    removed = 0
    for run_dir in runs[keep:]:
        st = _read_status(run_dir)
        if st.get("status") in ("starting", "running"):
            continue
        shutil.rmtree(run_dir)
        removed += 1

    print(f"已清理 {removed} 个旧测试（保留最近 {keep} 个）。")


# ──────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        prog="test_runner",
        description="进程隔离测试运行器 — 边测试边改代码",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  %(prog)s run https://www.xiaoyuzhoufm.com/episode/xxx
  %(prog)s run https://... --label "测试v2"
  %(prog)s run --from-state artifacts/run_abc/state.json --start-from illustration
  %(prog)s run --from-state artifacts/run_abc/state.json --only illustration,typeset
  %(prog)s list
  %(prog)s logs abc12345 -f
  %(prog)s kill abc12345
""",
    )
    sub = parser.add_subparsers(dest="command")

    # run
    p_run = sub.add_parser("run", help="启动测试")
    p_run.add_argument("url", nargs="?", help="播客链接 (小宇宙 FM)")
    p_run.add_argument("--from-state", dest="from_state", help="从已有 state.json 恢复")
    p_run.add_argument(
        "--start-from", dest="start_from",
        help="从指定步骤开始 (metadata/transcription/compose/enrich/extraction/annotation/editor_preface/illustration/typeset)",
    )
    p_run.add_argument("--only", help="只运行指定节点，逗号分隔 (如: illustration,typeset)")
    p_run.add_argument("--label", help="给测试加个标签，方便识别")

    # list
    sub.add_parser("list", aliases=["ls"], help="列出所有测试")

    # status
    p_status = sub.add_parser("status", help="查看测试详细状态")
    p_status.add_argument("run_id", help="Run ID (支持前缀匹配)")

    # logs
    p_logs = sub.add_parser("logs", help="查看测试日志")
    p_logs.add_argument("run_id", help="Run ID")
    p_logs.add_argument("-f", "--follow", action="store_true", help="实时跟踪日志")

    # kill
    p_kill = sub.add_parser("kill", help="终止测试")
    p_kill.add_argument("run_id", help="Run ID")

    # clean
    p_clean = sub.add_parser("clean", help="清理旧测试")
    p_clean.add_argument("--keep", type=int, default=5, help="保留最近 N 个 (默认 5)")

    args = parser.parse_args()

    commands = {
        "run": cmd_run,
        "list": cmd_list,
        "ls": cmd_list,
        "status": cmd_status,
        "logs": cmd_logs,
        "kill": cmd_kill,
        "clean": cmd_clean,
    }

    handler = commands.get(args.command)
    if handler:
        handler(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
