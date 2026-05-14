"""
知识库加载器

负责加载编辑规范、结构模板和参考案例，供 Agent 在推理过程中使用。
"""
import os
from typing import Optional
from functools import lru_cache

KNOWLEDGE_DIR = os.path.dirname(os.path.abspath(__file__))

TEMPLATE_TYPE_MAP = {
    "访谈": "interview",
    "对谈": "conversation",
    "独白": "monologue",
    "圆桌": "roundtable",
    "叙事": "narrative",
    "演讲": "monologue",  # 演讲归入独白类
    "故事": "narrative",
}


class KnowledgeLoader:
    """知识库加载器"""

    def __init__(self, knowledge_dir: Optional[str] = None):
        self.base_dir = knowledge_dir or KNOWLEDGE_DIR

    def _read_file(self, *path_parts: str) -> str:
        filepath = os.path.join(self.base_dir, *path_parts)
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                return f.read()
        except FileNotFoundError:
            print(f"[knowledge] 警告: 文件不存在 {filepath}")
            return ""

    @lru_cache(maxsize=1)
    def load_guidelines(self) -> str:
        return self._read_file("editing_guidelines.md")

    @lru_cache(maxsize=8)
    def load_template(self, content_type: str) -> str:
        """
        根据内容类型加载对应的结构模板。
        如果类型未识别，返回访谈类模板作为默认。
        """
        filename = TEMPLATE_TYPE_MAP.get(content_type, "interview")
        return self._read_file("templates", f"{filename}.md")

    @lru_cache(maxsize=1)
    def load_good_cases(self) -> str:
        return self._read_file("examples", "good_cases.md")

    @lru_cache(maxsize=1)
    def load_bad_cases(self) -> str:
        return self._read_file("examples", "bad_cases.md")

    def load_all_templates_summary(self) -> str:
        """加载所有模板的类型特征摘要（用于类型判断阶段）"""
        summaries = []
        for cn_type, en_name in TEMPLATE_TYPE_MAP.items():
            if en_name in [v for v in TEMPLATE_TYPE_MAP.values()]:
                template = self._read_file("templates", f"{en_name}.md")
                if template:
                    # 只提取类型特征部分
                    lines = template.split("\n")
                    summary_lines = []
                    in_section = False
                    for line in lines:
                        if "## 类型特征" in line:
                            in_section = True
                            continue
                        elif line.startswith("## ") and in_section:
                            break
                        elif in_section:
                            summary_lines.append(line)
                    if summary_lines:
                        summaries.append(f"**{cn_type}类**:\n" + "\n".join(summary_lines))
        
        # 去重
        seen = set()
        unique = []
        for s in summaries:
            key = s[:50]
            if key not in seen:
                seen.add(key)
                unique.append(s)
        
        return "\n\n".join(unique)

    def get_available_types(self) -> list:
        """返回所有支持的内容类型"""
        return list(set(TEMPLATE_TYPE_MAP.keys()))


_loader: Optional[KnowledgeLoader] = None


def get_knowledge_loader() -> KnowledgeLoader:
    global _loader
    if _loader is None:
        _loader = KnowledgeLoader()
    return _loader
