from typing import TypedDict, Optional, List, Dict, Any, Sequence
from enum import Enum


class WorkflowStage(str, Enum):
    """工作流阶段枚举"""
    TRANSCRIPTION = "transcription"
    # 旧流水线阶段（保留向后兼容）
    RESTRUCTURE = "restructure"
    POLISH = "polish"
    # 新流水线阶段
    COMPOSE = "compose"
    ENRICH = "enrich"
    # 以下四个子阶段在 enrich 内并行执行，保留枚举用于日志/追踪
    EXTRACTION = "extraction"
    ANNOTATION = "annotation"
    EDITOR_PREFACE = "editor_preface"
    ILLUSTRATION = "illustration"
    TYPESET = "typeset"


class PodBookState(TypedDict, total=False):
    """
    PodBook 工作流状态定义
    
    LangGraph 使用 TypedDict 来定义状态，各节点可以读取和更新状态中的字段。
    使用 total=False 使所有字段变为可选，方便初始化。
    """
    
    # ===== 输入参数 =====
    task_id: str
    audio_path: str
    title: str
    author: str
    description: str
    
    # ===== 播客元数据（用于 LLM 智能替换人称、修正专有名词）=====
    host_name: str                     # 主持人名字
    guest_names: List[str]             # 嘉宾名字列表
    company_names: List[str]           # 相关公司/组织名称
    podcast_intro: str                 # 播客简介
    proper_nouns: List[str]            # 领域专有名词（产品名、技术名等）
    name_aliases: Dict[str, List[str]] # 人名别名映射：{"中文主名": ["英文名", "昵称"]}
    cover_url: str                     # 播客封面图 URL
    cover_style: str                   # 封面风格 (例如: swiss, wabisabi, newwave, artdeco)
    podcast_name: str                  # 节目名称（如"硬地骇客"）
    podcast_url: str                   # 播客单集链接
    publish_date: str                  # 发布日期（ISO 格式，如 2026-02-15）
    episode_title: str                 # 播客原始单集标题（不受用户自定义书名影响）
    
    # ===== 内容分类（classify 模块输出）=====
    content_type: str                  # business / self_growth / humanities
    narrative_type: str                # story / opinion
    voice_format: str                  # dialogue / prose

    # ===== 工作流控制 =====
    current_stage: str
    error: Optional[str]
    
    # ===== 板块1：内容解析 输出 =====
    transcription: Optional[Dict[str, Any]]
    # 结构: {
    #     "segments": [{"speaker": str, "text": str, "start_time": float, "end_time": float}],
    #     "full_text": str,
    #     "duration": float
    # }
    
    # ===== 板块2：内容重组 输出 =====
    chapters: Optional[Dict[str, Any]]
    # 结构 (由 restructure Agent 生成):
    # {
    #     "content_type": str,           # 访谈/对谈/独白/圆桌/叙事
    #     "core_theme": str,
    #     "theme_keywords": list[str],
    #     "speakers": [{"name": str, "role": str, "style": str}],
    #
    #     "sections": [{                 # 板块列表（核心输出）
    #         "section_type": str,       # 板块类型（introduction, core_viewpoints, ...）
    #         "title": str,              # 具体标题（非框架名称）
    #         "content": str,            # AI重组后的板块内容
    #         "key_points": list,
    #         "source_segment_ids": list[int],
    #         "source_segments": list,
    #         "time_range": [float, float],
    #         "word_count": int,
    #     }],
    #
    #     "chapters": [{                 # 下游兼容（sections 的映射）
    #         "title": str, "content": str, "key_points": list,
    #         "summary": str, "segments": list, "segment_ids": list[int],
    #         "time_range": [float, float],
    #     }],
    #
    #     "agent_steps": int,
    #     "quality_score": int,
    # }
    
    # ===== 新流水线：节点2 成稿输出 =====
    composed_content: Optional[Dict[str, Any]]
    # 结构: {
    #     "content_type": str,
    #     "core_theme": str,
    #     "theme_keywords": list[str],
    #     "speakers": [{"name": str, "role": str, "style": str}],
    #     "chapters": [{
    #         "title": str,
    #         "content": str,          # 已润色的正文
    #         "key_points": list[str],
    #         "section_type": str,
    #         "source_segment_ids": list[int],
    #         "time_range": [float, float],
    #     }],
    #     "plan_variant": "compose",
    #     "prompt_version": int,
    #     "elapsed_seconds": float,
    # }

    # ===== 新流水线：精华提炼输出（enrich 并行子节点 extraction）=====
    highlights: Optional[Dict[str, Any]]
    # 结构: {
    #     "quotes": [{
    #         "text": str,                              # 金句原文（来自 transcript 原话）
    #         "placement": str,                         # epigraph | inline_card
    #         "chapter_title": str,                     # 所属章节标题
    #         "after_paragraph": int | None,            # inline_card 时指定段落编号
    #     }],
    # }

    # ===== 内容注释输出（enrich 并行子节点 annotation）=====
    annotated_content: Optional[Dict[str, Any]]
    # 结构：同 composed_content，但各章节 content 字段中已插入脚注标记和脚注定义
    # 额外字段：
    #     "total_footnotes": int

    # ===== 用户显式输入（空字符串/空列表表示用户未填写，由AI生成）=====
    user_title: Optional[str]
    user_editor_preface: Optional[str]
    user_host_name: Optional[str]          # 用户显式输入的主持人名（权威值，不可被下游覆盖）
    user_guest_names: Optional[List[str]]  # 用户显式输入的嘉宾名（权威值，不可被下游覆盖）

    # ===== 编者序（enrich 并行子节点 editor_preface）=====
    editor_preface_content: Optional[str]

    # ===== 正文插图（enrich 并行子节点 illustration）=====
    illustrations: Optional[Dict[str, Any]]
    # 结构: {
    #     "images": [{
    #         "chapter_title": str,
    #         "after_paragraph": int,
    #         "filename": str,           # 相对于 task output dir（illustrations/ 子目录）
    #         "caption": str,
    #         "type": "concept",
    #         "source": "ai_generated",
    #         "review_score": int,       # VLM 审核总分 (满分 25)
    #         "review_reason": str,
    #     }],
    #     "total_count": int,
    #     "generated_count": int,
    #     "rejected_count": int,
    # }

    # ===== 排版输出（typeset 节点）=====
    pdf_path: Optional[str]
    typst_source: Optional[str]

    # ===== Token 用量统计 =====
    usage_stats: Optional[Dict[str, Any]]

    # ===== 旧流水线字段（保留向后兼容，不再写入）=====
    chapters: Optional[Dict[str, Any]]
    polished_content: Optional[Dict[str, Any]]


def create_initial_state(
    task_id: str,
    audio_path: str,
    title: str,
    author: str,
    description: str = "",
    *,
    host_name: str = "",
    guest_names: Optional[List[str]] = None,
    company_names: Optional[List[str]] = None,
    podcast_intro: str = "",
    proper_nouns: Optional[List[str]] = None,
    name_aliases: Optional[Dict[str, List[str]]] = None,
    cover_url: str = "",
    cover_style: str = "swiss",
    podcast_name: str = "",
    podcast_url: str = "",
    publish_date: str = "",
    episode_title: str = "",
    user_title: str = "",
    user_editor_preface: str = "",
    user_host_name: str = "",
    user_guest_names: Optional[List[str]] = None,
) -> PodBookState:
    """创建初始状态"""
    return PodBookState(
        task_id=task_id,
        audio_path=audio_path,
        title=title,
        author=author,
        description=description,
        host_name=host_name,
        guest_names=guest_names or [],
        company_names=company_names or [],
        podcast_intro=podcast_intro,
        proper_nouns=proper_nouns or [],
        name_aliases=name_aliases or {},
        cover_url=cover_url,
        cover_style=cover_style,
        podcast_name=podcast_name,
        podcast_url=podcast_url,
        publish_date=publish_date,
        content_type="",
        narrative_type="",
        voice_format="",
        current_stage=WorkflowStage.TRANSCRIPTION.value,
        error=None,
        transcription=None,
        composed_content=None,
        highlights=None,
        annotated_content=None,
        episode_title=episode_title or "",
        user_title=user_title or None,
        user_host_name=user_host_name or None,
        user_guest_names=user_guest_names or None,
        editor_preface_content=None,
        user_editor_preface=user_editor_preface or None,
        illustrations=None,
        pdf_path=None,
        typst_source=None,
        # 旧流水线字段
        chapters=None,
        polished_content=None,
    )
