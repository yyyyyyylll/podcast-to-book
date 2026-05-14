"""
podcast_timeline — 从单期播客的 shownotes_text 中抽出作者本人写的时间轴章节。

播客平台（小宇宙、Apple Podcasts、Spotify…）的 shownotes 普遍带「时间轴章节」，例如：

    03:15 作者与“内阻力”搏斗的半生
    05:37 内阻力是一个人不幸福的根源
    ...

这是作者本人对该期内容的官方分段，命名也是作者本意，对下游有两类高价值用法：

1) **小标题命名参考**：rename_sections runner 把「落入某节时间窗的作者子标题」喂给 LLM，
   让 LLM 在命名时优先借鉴作者用过的具体字眼/抓手，而不是空泛归纳。
2) **切分参考**：compose Phase 1（plan）可以把作者时间轴当作「软建议」喂给 LLM，
   避免 LLM 在没有任何先验的情况下做章内切分。

这个模块只做「解析 + 时间窗筛选」，**不做任何下游决策**——
具体是否照抄、是否合并、是否被严格采用，由调用方在 prompt 里明确约束。

时间戳支持的格式：
    M:SS / MM:SS / H:MM:SS / HH:MM:SS

CLI（手动检查抽取效果）：

    cd backend
    python -m workbench.podcast_timeline --job possibility --only 11
    python -m workbench.podcast_timeline --job possibility           # 整作业全集
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable


# 行首允许的前缀符号（一些 shownotes 会用 ▌/■/- 开头，宽容地跳过它们）
_LINE_PREFIX = r"[\s\-\*\u25a0\u25cf\u2022\u2013\u2014\u2192\u25b6\u25b8]*"

# 匹配「(H:)MM:SS 标题」整行；时间和标题之间的分隔符可以是空格 / 全角空格 / 中英文冒号 / 制表符
_TIMELINE_LINE_RE = re.compile(
    rf"^{_LINE_PREFIX}"
    r"(?P<ts>(?:\d{1,2}:)?\d{1,2}:\d{2})"
    r"[\s\u00a0\u3000\t:：\-—.\u2014]+"
    r"(?P<title>\S.*?)\s*$",
    flags=re.MULTILINE,
)


@dataclass(frozen=True)
class TimelineEntry:
    """作者时间轴里的一条章节标记。"""

    time_sec: int          # 起始秒数（解析后的整数秒）
    time_label: str        # 原始展示标签，例如 "03:15"
    title: str             # 作者写的标题文本（已 strip）

    def to_dict(self) -> dict:
        return {
            "time_sec": self.time_sec,
            "time_label": self.time_label,
            "title": self.title,
        }


def _ts_to_seconds(ts: str) -> int | None:
    """把 'M:SS' / 'MM:SS' / 'H:MM:SS' / 'HH:MM:SS' 转成整数秒。失败返回 None。"""
    parts = ts.split(":")
    try:
        nums = [int(p) for p in parts]
    except ValueError:
        return None
    if len(nums) == 2:
        m, s = nums
        if not (0 <= s < 60):
            return None
        return m * 60 + s
    if len(nums) == 3:
        h, m, s = nums
        if not (0 <= m < 60 and 0 <= s < 60):
            return None
        return h * 3600 + m * 60 + s
    return None


def parse_timeline(shownotes_text: str | None) -> list[TimelineEntry]:
    """
    从 shownotes 全文中抽出作者时间轴。

    返回按时间顺序排好的 TimelineEntry 列表；shownotes 为空 / 没有时间轴时返回空列表。
    解析策略：
        - 逐行扫，匹配「(H:)MM:SS  标题」
        - 同时间戳出现两次时去重（保留首次）
        - 时间序整理成升序
    """
    if not shownotes_text:
        return []

    seen_secs: set[int] = set()
    entries: list[TimelineEntry] = []
    for m in _TIMELINE_LINE_RE.finditer(shownotes_text):
        ts = m.group("ts")
        title = (m.group("title") or "").strip()
        if not title:
            continue
        # 防止把日期/版本号误识别（比如 "2025/08:10" 不会进来，但保险起见过滤过短/过长标题）
        if len(title) < 2 or len(title) > 80:
            continue
        sec = _ts_to_seconds(ts)
        if sec is None or sec in seen_secs:
            continue
        seen_secs.add(sec)
        entries.append(TimelineEntry(time_sec=sec, time_label=ts, title=title))

    entries.sort(key=lambda e: e.time_sec)
    return entries


def subset_by_range(
    entries: Iterable[TimelineEntry],
    *,
    start_sec: float | None,
    end_sec: float | None,
    pad_before: float = 30.0,
) -> list[TimelineEntry]:
    """
    取出落在 [start_sec - pad_before, end_sec) 内的条目。

    `pad_before` 用来兜住「该节正好以一个作者章节起点开头、但作者标的时间稍微早一点点」
    的常见情况（比如作者标的是 03:15、ASR 切的是 03:18）。默认 30 秒已经足够覆盖。

    start / end 任一为 None 时不在该侧设限。
    """
    items = list(entries)
    if start_sec is not None:
        lo = float(start_sec) - float(pad_before)
        items = [e for e in items if e.time_sec >= lo]
    if end_sec is not None:
        hi = float(end_sec)
        items = [e for e in items if e.time_sec < hi]
    return items


def format_timeline_for_prompt(
    entries: Iterable[TimelineEntry],
    *,
    bullet: str = "-",
    indent: str = "",
) -> str:
    """把时间轴渲染成 prompt 友好的多行 markdown bullet。空列表返回空串。"""
    lines = [
        f"{indent}{bullet} {e.time_label}  {e.title}"
        for e in entries
    ]
    return "\n".join(lines)


# --- CLI（手动检查解析是否正确） -------------------------------------------------

def _main() -> int:
    import argparse
    import json
    import sys

    from multi.env import setup
    setup()
    from multi.io import JobPaths, read_json

    p = argparse.ArgumentParser(description="解析单作业下各 episode 的作者时间轴")
    p.add_argument("--job", required=True)
    p.add_argument("--only", default="", help="只看指定 episode，如 11 或 1,5,11")
    args = p.parse_args()

    paths = JobPaths.open(args.job)
    if not paths.episodes_index_json.exists():
        print(f"未找到 episodes_index.json：{paths.root}", file=sys.stderr)
        return 1

    only: set[int] | None = None
    if args.only.strip():
        only = set()
        for part in args.only.split(","):
            part = part.strip()
            if not part:
                continue
            if "-" in part:
                a, b = part.split("-", 1)
                only.update(range(int(a), int(b) + 1))
            else:
                only.add(int(part))

    index = read_json(paths.episodes_index_json)
    for ep in index.get("episodes", []):
        idx = ep.get("index")
        if only is not None and idx not in only:
            continue
        ep_dir = paths.episode_dir(idx)
        meta_path = ep_dir / "meta.json"
        if not meta_path.exists():
            print(f"[ep{idx:02d}] meta.json 缺失，跳过")
            continue
        meta = read_json(meta_path)
        entries = parse_timeline(meta.get("shownotes_text"))
        print(f"\n=== ep{idx:02d} | {meta.get('title','(no title)')} | {len(entries)} 条作者时间轴")
        for e in entries:
            print(f"  {e.time_label}  {e.title}")
        if args.only:
            print()
            print(json.dumps([e.to_dict() for e in entries], ensure_ascii=False, indent=2))

    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
