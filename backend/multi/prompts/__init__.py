"""
工作台专用 prompt 覆盖层。

设计原则：
- B 端"一期 = 一章" 的章法跟 C 端"一期 = 一本"的章法不同，C 端"每章 1-5 条注释"
  在 B 端语义下相当于"每节 1-5 条 × 5 节 = 5-25 条"，对实体书过度堆砌。
- 因此 workbench 写一套**显式"per-section"克制版**注释 prompt，覆盖 C 端版本。
- 其他节点（extraction/illustration）C 端 prompt 已支持 max_per_chapter 等数量参数，
  workbench 不需要改 prompt，只需在 runner 层调整数量上限并做后筛。

工作流：
    workbench/runners/run_pipeline.py 在调 _annotate_chapter 时传入 get_workbench_prompts。
"""
from __future__ import annotations

from importlib import import_module
from typing import Optional

# B 端目前重点支持的内容类型
SUPPORTED_TYPES = ("self_growth", "business", "humanities")


def get_workbench_prompts(node: str, content_type: str) -> Optional[dict]:
    """
    加载 workbench 自有 prompt 覆盖。

    若 workbench/prompts/<node>/<content_type>.py 存在则返回其 PROMPTS dict，否则 None。
    返回 None 表示"不覆盖，沿用 C 端"。
    """
    module_path = f"workbench.prompts.{node}.{content_type}"
    try:
        mod = import_module(module_path)
    except ModuleNotFoundError:
        return None
    return getattr(mod, "PROMPTS", None)
