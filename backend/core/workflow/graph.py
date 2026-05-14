import asyncio
from typing import Optional, Callable
from langgraph.graph import StateGraph, END

from core.workflow.state import PodBookState, WorkflowStage
from core.services.llm_service import get_llm_service
from core.workflow.nodes.transcription import transcription_node
from core.workflow.nodes.compose import compose_node
from core.workflow.nodes.extraction import extraction_node
from core.workflow.nodes.annotation import annotation_node
from core.workflow.nodes.editor_preface import editor_preface_node
from core.workflow.nodes.illustration import illustration_node
from core.workflow.nodes.typeset import typeset_node

# 旧流水线节点（保留备用，不再接入 graph）
# from core.workflow.nodes.restructure import restructure_node
# from core.workflow.nodes.restructure_alternative import restructure_alternative_node
# from core.workflow.nodes.polish import polish_node


def _tracked_node(stage_name: str, node_fn):
    """包装节点函数，自动设置 LLM 用量追踪阶段"""
    async def wrapper(state):
        llm = get_llm_service()
        llm.set_tracking_stage(stage_name)
        return await node_fn(state)
    wrapper.__name__ = node_fn.__name__
    return wrapper


async def _parallel_enrich(state: PodBookState) -> dict:
    """compose 之后四个子节点并行执行（extraction / annotation / editor_preface / illustration）。

    每个子节点只读取 state 中已有的字段（transcription, composed_content），
    互相之间没有数据依赖，因此可安全并行。

    每个分支创建独立的 UsageTracker，避免并发写入时 stage 归属混乱。
    运行结束后将各分支的 usage records 合并回父 tracker。
    """
    from core.services.llm_service import _current_tracker, UsageTracker

    parent_tracker = _current_tracker.get()
    llm = get_llm_service()

    async def _run(stage_name: str, fn):
        branch_tracker = UsageTracker()
        branch_tracker.set_stage(stage_name)
        _current_tracker.set(branch_tracker)
        result = await fn(state)
        return result, branch_tracker

    results = await asyncio.gather(
        _run("extraction", extraction_node),
        _run("annotation", annotation_node),
        _run("editor_preface", editor_preface_node),
        _run("illustration", illustration_node),
    )

    merged: dict = {}
    for result, branch_tracker in results:
        if isinstance(result, dict):
            merged.update(result)
        if parent_tracker:
            for rec in branch_tracker._usage_records:
                parent_tracker._usage_records.append(rec)

    _current_tracker.set(parent_tracker)
    merged["current_stage"] = WorkflowStage.ENRICH.value
    return merged


def create_workflow() -> StateGraph:
    """
    创建 PodBook 文稿处理工作流

    工作流结构：

    transcription -> compose -> enrich -> typeset -> END
                                  ├── extraction  (金句)
                                  ├── annotation  (注释)
                                  ├── editor_preface (编者序)
                                  └── illustration (插图)

    compose 之后四个节点并行执行，全部完成后进入 typeset。
    """
    workflow = StateGraph(PodBookState)

    workflow.add_node("transcription", _tracked_node("transcription", transcription_node))
    workflow.add_node("compose", _tracked_node("compose", compose_node))
    workflow.add_node("enrich", _parallel_enrich)
    workflow.add_node("typeset", _tracked_node("typeset", typeset_node))

    workflow.set_entry_point("transcription")
    workflow.add_edge("transcription", "compose")
    workflow.add_edge("compose", "enrich")
    workflow.add_edge("enrich", "typeset")
    workflow.add_edge("typeset", END)

    return workflow


def compile_workflow():
    """编译工作流为可执行图"""
    workflow = create_workflow()
    return workflow.compile()


NEXT_STAGE_MAP = {
    "transcription": "compose",
    "compose": "enrich",
    "enrich": "typeset",
    "typeset": "completed",
}

STAGE_START_PROGRESS = {
    "compose": 45,
    "enrich": 70,
    "typeset": 95,
    "completed": 100,
}


async def run_workflow_async(
    initial_state: PodBookState,
    on_stage_change: Optional[Callable] = None
) -> PodBookState:
    """
    异步运行工作流。
    如果提供 on_stage_change(stage, progress)，通过 astream 逐节点回调进度；
    否则直接 ainvoke 一次性执行。
    自动追踪各阶段的 LLM token 用量。
    """
    app = compile_workflow()
    llm = get_llm_service()
    llm.start_tracking()

    if on_stage_change is None:
        result = await app.ainvoke(initial_state)
        result["usage_stats"] = _build_usage_stats(llm.stop_tracking(), result)
        return result

    accumulated = dict(initial_state)
    async for event in app.astream(initial_state, stream_mode="updates"):
        for node_name, update in event.items():
            if isinstance(update, dict):
                accumulated.update(update)
            
            next_stage = NEXT_STAGE_MAP.get(node_name)
            if next_stage and on_stage_change:
                await on_stage_change(next_stage, STAGE_START_PROGRESS.get(next_stage, 90))

    accumulated["usage_stats"] = _build_usage_stats(llm.stop_tracking(), accumulated)
    return accumulated


def _build_usage_stats(llm_usage: dict, state: dict) -> dict:
    """组装完整用量统计（LLM + ASR）"""
    from core.config import settings

    asr_duration = 0.0
    transcription = state.get("transcription")
    if isinstance(transcription, dict):
        asr_duration = transcription.get("duration", 0.0) or 0.0

    asr_cost = (asr_duration / 3600) * settings.COST_ASR_PER_HOUR

    stages = llm_usage.get("stages", {})
    records = llm_usage.get("records", [])
    llm_totals = llm_usage.get("totals", {})

    def _record_cost(r: dict) -> float:
        if r.get("is_image_gen"):
            return settings.COST_IMAGE_GEN_PER_CALL
        if r.get("is_search"):
            return (
                settings.COST_SEARCH_PER_CALL
                + r["prompt_tokens"] * settings.COST_SEARCH_INPUT / 1_000_000
                + r["completion_tokens"] * settings.COST_SEARCH_OUTPUT / 1_000_000
            )
        model = r.get("model", "")
        if model == settings.VLM_REVIEW_MODEL:
            return (
                r["prompt_tokens"] * settings.COST_VLM_INPUT / 1_000_000
                + r["completion_tokens"] * settings.COST_VLM_OUTPUT / 1_000_000
            )
        if model == settings.LLM_PREMIUM_MODEL:
            return (
                r["prompt_tokens"] * settings.COST_LLM_PREMIUM_INPUT / 1_000_000
                + r["completion_tokens"] * settings.COST_LLM_PREMIUM_OUTPUT / 1_000_000
            )
        return (
            r["prompt_tokens"] * settings.COST_LLM_INPUT / 1_000_000
            + r["completion_tokens"] * settings.COST_LLM_OUTPUT / 1_000_000
        )

    llm_cost = sum(_record_cost(r) for r in records)

    stage_costs = {}
    for stage_name, stage_data in stages.items():
        stage_records = [r for r in records if r["stage"] == stage_name]
        stage_costs[stage_name] = round(sum(_record_cost(r) for r in stage_records), 6)

    return {
        "asr": {
            "duration_seconds": round(asr_duration, 1),
            "cost": round(asr_cost, 4),
        },
        "llm": {
            "stages": {
                name: {**data, "cost": stage_costs.get(name, 0)}
                for name, data in stages.items()
            },
            "totals": {
                **llm_totals,
                "cost": round(llm_cost, 4),
            },
        },
        "total_cost": round(asr_cost + llm_cost, 4),
        "cost_rates": {
            "llm_input_per_m": settings.COST_LLM_INPUT,
            "llm_output_per_m": settings.COST_LLM_OUTPUT,
            "llm_premium_input_per_m": settings.COST_LLM_PREMIUM_INPUT,
            "llm_premium_output_per_m": settings.COST_LLM_PREMIUM_OUTPUT,
            "search_input_per_m": settings.COST_SEARCH_INPUT,
            "search_output_per_m": settings.COST_SEARCH_OUTPUT,
            "search_per_call": settings.COST_SEARCH_PER_CALL,
            "asr_per_hour": settings.COST_ASR_PER_HOUR,
            "image_gen_per_call": settings.COST_IMAGE_GEN_PER_CALL,
            "vlm_input_per_m": settings.COST_VLM_INPUT,
            "vlm_output_per_m": settings.COST_VLM_OUTPUT,
        },
    }


def run_workflow(initial_state: PodBookState) -> PodBookState:
    """
    同步运行工作流（主要用于测试）
    """
    app = compile_workflow()
    return app.invoke(initial_state)


# ===== 便捷函数 =====

async def process_audio(
    task_id: str,
    audio_path: str,
    title: str,
    author: str,
    description: str = "",
    *,
    host_name: str = "",
    guest_names: list[str] | None = None,
    company_names: list[str] | None = None,
    podcast_intro: str = "",
    proper_nouns: list[str] | None = None,
    cover_url: str = "",
    podcast_name: str = "",
    podcast_url: str = "",
    publish_date: str = "",
) -> PodBookState:
    """
    处理音频文件的便捷函数
    
    Args:
        task_id: 任务ID
        audio_path: 音频文件路径（本地路径或URL）
        title: 书籍标题
        author: 作者名
        description: 描述
        host_name: 主持人名字
        guest_names: 嘉宾名字列表
        company_names: 相关公司/组织名称
        podcast_intro: 播客简介
        proper_nouns: 领域专有名词列表
        cover_url: 播客封面图 URL
    
    Returns:
        处理完成后的状态
    """
    from core.workflow.state import create_initial_state
    
    initial_state = create_initial_state(
        task_id=task_id,
        audio_path=audio_path,
        title=title,
        author=author,
        description=description,
        host_name=host_name,
        guest_names=guest_names,
        company_names=company_names,
        podcast_intro=podcast_intro,
        proper_nouns=proper_nouns,
        cover_url=cover_url,
        podcast_name=podcast_name,
        podcast_url=podcast_url,
        publish_date=publish_date,
    )
    
    return await run_workflow_async(initial_state)


async def process_podcast_url(
    podcast_url: str,
    *,
    extra_proper_nouns: list[str] | None = None,
) -> PodBookState:
    """
    从播客平台链接直接启动全流水线

    自动提取音频 URL 和元数据（标题、主播、嘉宾等），
    然后执行 transcription → compose → extraction → annotation 全流程。

    Args:
        podcast_url: 播客平台单集链接（当前支持小宇宙 FM）
        extra_proper_nouns: 额外的专有名词列表（补充自动提取的）

    Returns:
        处理完成后的状态
    """
    from core.services.podcast_service import get_podcast_service

    print(f"[workflow] 解析播客链接: {podcast_url}")
    svc = get_podcast_service()
    episode = await svc.extract(podcast_url)

    print(f"[workflow] 播客: {episode.podcast_name}")
    print(f"[workflow] 标题: {episode.title}")
    print(f"[workflow] 音频: {episode.audio_url[:80]}...")
    print(f"[workflow] 时长: {episode.duration:.0f}s ({episode.duration/60:.1f}min)")
    if episode.host_name:
        print(f"[workflow] 主持人: {episode.host_name}")
    if episode.guest_names:
        print(f"[workflow] 嘉宾: {'、'.join(episode.guest_names)}")

    proper_nouns = episode.proper_nouns + (extra_proper_nouns or [])

    return await process_audio(
        task_id=episode.episode_id,
        audio_path=episode.audio_url,
        title=episode.title,
        author=episode.host_name or episode.podcast_name,
        description=episode.description,
        host_name=episode.host_name,
        guest_names=episode.guest_names,
        company_names=episode.company_names,
        podcast_intro=episode.shownotes_text,
        proper_nouns=proper_nouns,
        cover_url=episode.cover_url,
        podcast_name=episode.podcast_name,
        podcast_url=podcast_url,
        publish_date=episode.publish_date,
    )
