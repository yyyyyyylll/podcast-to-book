"""
Prompt 模板注册表

所有节点的 prompt 按 (content_type, narrative_type, voice_format) 组织。
查找优先级：
  1. (node, narrative_type, voice_format, content_type) — 完整交叉键
  2. (node, content_type)                              — fallback（不需要叙事/组织维度的节点）
  3. KeyError

每个 prompt 模块需导出一个 PROMPTS 字典，包含该节点所需的 prompt 键值对。
"""
import re
from importlib import import_module
from functools import lru_cache
from typing import Dict, Optional


CONTENT_TYPES = ("business", "self_growth", "humanities")
NARRATIVE_TYPES = ("story", "opinion")
VOICE_FORMATS = ("dialogue", "prose")

_MODULE_MAP: Dict[str, str] = {
    "classify":       "core.workflow.prompts.classify",
    "transcription":  "core.workflow.prompts.transcription",
    "compose":        "core.workflow.prompts.compose",
    "extraction":     "core.workflow.prompts.extraction",
    "annotation":     "core.workflow.prompts.annotation",
    "editor_preface": "core.workflow.prompts.editor_preface",
    "illustration":   "core.workflow.prompts.illustration",
}

_CROSS_NODES = {"compose", "editor_preface"}


@lru_cache(maxsize=256)
def _load_module(module_path: str):
    """动态导入 prompt 模块并缓存。"""
    try:
        mod = import_module(module_path)
        return getattr(mod, "PROMPTS", {})
    except (ImportError, AttributeError) as e:
        print(f"[prompt_registry] 加载失败: {module_path} — {e}")
        return {}


def _build_module_path(
    node: str,
    content_type: str,
    narrative_type: Optional[str] = None,
    voice_format: Optional[str] = None,
) -> str:
    """根据节点名和维度构建 prompt 模块路径。

    交叉键节点: {base}.{narrative_type}_{voice_format}_{content_type}
    普通节点:   {base}.{content_type}
    """
    base = _MODULE_MAP.get(node)
    if not base:
        raise KeyError(f"未知节点: {node}")

    if node == "classify":
        return base

    if narrative_type and voice_format and node in _CROSS_NODES:
        return f"{base}.{narrative_type}_{voice_format}_{content_type}"

    return f"{base}.{content_type}"


# host 变体：去掉「关键标注：用 **加粗** 标注…」整条指令。
# 这条指令在 16 个 compose prompt 里以多种措辞出现，正则覆盖常见两种：
#  - 编号开头："7. **关键标注**：用 **加粗** 标注…"
#  - 简短回顾："8. **关键标注**：用 **加粗** 标注…（适量）"
_HOST_BOLD_INSTRUCTION_RE = re.compile(
    r"^\s*\d+\.\s*\*\*关键标注\*\*[^\n]*\n?",
    flags=re.MULTILINE,
)


def _strip_bold_instruction(prompts: dict) -> dict:
    """从 prompt 字典的每个字符串值里剥除「关键标注 / 加粗」指令。"""
    cleaned: dict = {}
    for key, val in prompts.items():
        if isinstance(val, str):
            cleaned[key] = _HOST_BOLD_INSTRUCTION_RE.sub("", val)
        else:
            cleaned[key] = val
    return cleaned


_VARIANT_TRANSFORMS = {
    "host_no_bold": _strip_bold_instruction,
}


def get_prompts(
    node: str,
    content_type: str = "business",
    narrative_type: Optional[str] = None,
    voice_format: Optional[str] = None,
    variant: Optional[str] = None,
) -> dict:
    """
    获取节点对应的 prompt 组。

    查找优先级：
      1. (node, narrative_type, voice_format, content_type) — 交叉键（仅 compose/editor_preface）
      2. (node, content_type) — fallback
      3. KeyError

    `variant` 参数：传入 "host_no_bold" 会在返回前剥除「关键标注 / 加粗」指令，
    供 host 自助板块使用，不影响 C 端调用方。
    """
    if node == "classify":
        prompts = _load_module(_MODULE_MAP["classify"])
    else:
        prompts = {}
        if narrative_type and voice_format and node in _CROSS_NODES:
            path = _build_module_path(node, content_type, narrative_type, voice_format)
            prompts = _load_module(path)

        if not prompts:
            path = _build_module_path(node, content_type)
            prompts = _load_module(path)

        if not prompts:
            raise KeyError(
                f"未找到 prompt: node={node}, content_type={content_type}, "
                f"narrative_type={narrative_type}, voice_format={voice_format}"
            )

    transform = _VARIANT_TRANSFORMS.get(variant or "")
    return transform(prompts) if transform else prompts


def get_voice_format_rules(voice_format: str) -> str:
    """获取 transcription 清洗阶段的 voice_format 保留规则段落。"""
    rules_module = _load_module("core.workflow.prompts.transcription.voice_format_rules")
    if not rules_module:
        return ""
    return rules_module.get(voice_format, "")
