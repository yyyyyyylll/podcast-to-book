"""
板块四：文本润色

职责：
1. 统一章节语气
2. 确保叙事连贯性
3. 优化阅读节奏
4. 增加导语、解释语
5. 补充行业知识

输入：chapters, highlights
输出：polished_content
"""
from typing import Dict, Any, List

from core.workflow.state import PodBookState, WorkflowStage
from core.services.llm_service import get_llm_service


POLISH_SYSTEM_PROMPT = """你是一位资深的出版编辑，负责将初稿润色为可出版的书稿。
你需要统一语气风格、优化行文节奏、增强可读性，同时保持原文的核心信息和特色表达。"""

POLISH_CHAPTER_PROMPT = """请对以下章节进行润色，使其达到可出版的水平：

书籍信息：
- 书名：{title}
- 作者：{author}
- 核心主题：{core_theme}

当前章节：
标题：{chapter_title}
导语：{chapter_intro}

原始内容：
---
{chapter_content}
---

章节要点：{key_points}

润色要求：
1. 统一语气风格（正式但不失亲和，适合书籍阅读）
2. 优化段落过渡，增强叙事连贯性
3. 调整阅读节奏（长短句交错，适当留白）
4. 在合适位置增加导语和解释性文字
5. 补充必要的行业背景知识
6. 保留原文的精彩表达和关键金句

请直接输出润色后的完整章节内容（纯文本，不需要JSON格式）。
章节结构：先是导语（斜体标注的引导段落），然后是正文内容。"""


async def polish_node(state: PodBookState) -> Dict[str, Any]:
    """
    文本润色节点
    
    处理流程：
    1. 获取章节结构和精华提炼结果
    2. 逐章调用LLM进行润色
    3. 整合所有润色后的章节
    4. 输出最终文稿
    """
    print(f"[polish] 开始处理任务: {state['task_id']}")
    
    chapters = state.get("chapters")
    highlights = state.get("highlights")
    
    if not chapters:
        raise ValueError("缺少章节结构，无法进行文本润色")
    
    # 构建章节导语映射
    intro_map = {}
    if highlights and highlights.get("chapter_intros"):
        for intro in highlights["chapter_intros"]:
            intro_map[intro.get("chapter_title", "")] = intro.get("intro", "")
    
    # 逐章润色
    llm_service = get_llm_service()
    polished_chapters = []
    
    for i, chapter in enumerate(chapters.get("chapters", [])):
        print(f"[polish] 润色第 {i+1} 章: {chapter.get('title', '')}")
        
        chapter_title = chapter.get("title", f"第{i+1}章")
        chapter_intro = intro_map.get(chapter_title, "")
        
        prompt = POLISH_CHAPTER_PROMPT.format(
            title=state.get("title", ""),
            author=state.get("author", ""),
            core_theme=chapters.get("core_theme", ""),
            chapter_title=chapter_title,
            chapter_intro=chapter_intro,
            chapter_content=chapter.get("content", ""),
            key_points=", ".join(chapter.get("key_points", []))
        )
        
        polished_content = await llm_service.generate(
            prompt=prompt,
            system_prompt=POLISH_SYSTEM_PROMPT,
            temperature=0.6,
            max_tokens=65536
        )
        
        polished_chapters.append({
            "title": chapter_title,
            "intro": chapter_intro,
            "content": polished_content.strip()
        })
    
    # 整合输出
    polished_result = {
        "title": state.get("title", ""),
        "author": state.get("author", ""),
        "core_theme": chapters.get("core_theme", ""),
        "content_type": chapters.get("content_type", ""),
        "chapters": polished_chapters,
        "quotes": highlights.get("quotes", []) if highlights else [],
        "methodologies": highlights.get("methodologies", []) if highlights else []
    }
    
    print(f"[polish] 完成，共润色 {len(polished_chapters)} 个章节")
    
    return {
        "polished_content": polished_result,
        "current_stage": "completed"  # MVP最后一个阶段
    }


def estimate_reading_time(content: str) -> int:
    """估算阅读时间（分钟），按每分钟300字计算"""
    char_count = len(content)
    return max(1, char_count // 300)
