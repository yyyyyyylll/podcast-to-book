"""
内容重组（替代方案）测试

支持两种测试模式：
1. 单次生成测试：验证路由 + 单 prompt 生成是否跑通
2. 优化循环测试：运行 prompt 自动优化流程

运行方式：
cd backend

# 单次生成（快速验证，约 30-60s）
pytest tests/test_restructure_alt.py::test_single_prompt_generate -v -s

# 优化循环（完整流程，每轮约 2-3 分钟）
pytest tests/test_restructure_alt.py::test_optimization_loop -v -s
"""
import json
import time
from datetime import datetime
from pathlib import Path

import pytest
from core.workflow.state import create_initial_state, WorkflowStage


ARTIFACTS_DIR = Path(__file__).resolve().parent / "artifacts"


def _log(message: str) -> None:
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[restructure-alt-test {ts}] {message}", flush=True)


def _dump_json(path: Path, data) -> None:
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )


def load_transcription(filename: str = "") -> dict:
    """从已有的 workflow 测试产物中加载转写结果。可指定文件名。"""
    if filename:
        target = ARTIFACTS_DIR / filename
        if not target.exists():
            raise FileNotFoundError(f"指定文件不存在: {target}")
        machine_files = [target]
    else:
        machine_files = sorted(
            ARTIFACTS_DIR.glob("workflow_machine_real_audio_*.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
    if not machine_files:
        raise FileNotFoundError(
            "找不到转写结果文件。请先运行:\n"
            "  pytest tests/test_workflow.py::test_workflow_with_mock -v -s"
        )

    latest = machine_files[0]
    _log(f"加载转写结果: {latest.name}")

    data = json.loads(latest.read_text(encoding="utf-8"))
    transcription = data.get("final_state", {}).get("transcription")
    if not transcription:
        for stage in data.get("stage_results", []):
            if stage.get("stage") == "transcription":
                transcription = stage.get("output", {}).get("transcription")
                break

    if not transcription or not transcription.get("segments"):
        raise ValueError("转写数据为空，请检查测试产物文件")

    segments = transcription["segments"]
    _log(f"已加载 {len(segments)} 个片段, "
         f"时长 {transcription.get('duration', 0):.0f}s")
    return transcription


def build_test_state(transcription: dict) -> dict:
    """构建包含转写结果的测试状态。"""
    state = create_initial_state(
        task_id="restructure_alt_test",
        audio_path="../podcast_test_1.m4a",
        title="十字路口播客测试",
        author="十字路口",
        description="张帆（前智谱AI COO）获蓝驰创投800万美金天使投资，创办元理智能",
        host_name="Coco",
        guest_names=["张帆"],
        company_names=["智谱AI", "元理智能", "妙计旅行", "蓝驰创投"],
        podcast_intro="十字路口是一档关注新一代AI技术浪潮带来的行业新变化和创业新机会的播客节目",
        proper_nouns=["toB", "ToC", "COO", "商业强化学习", "数字员工", "大模型"],
    )
    state["transcription"] = transcription
    state["current_stage"] = WorkflowStage.RESTRUCTURE.value
    return state


def write_review_report(
    chapters: dict,
    elapsed: float,
    state: dict,
    trace_records: list,
    suffix: str = "",
) -> Path:
    """写入人类可读的审查报告。"""
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    name = f"restructure_alt{'_' + suffix if suffix else ''}_{ts}"
    review_path = ARTIFACTS_DIR / f"{name}_review.md"
    machine_path = ARTIFACTS_DIR / f"{name}_machine.json"

    sections = chapters.get("sections", [])

    # 机器可读
    _dump_json(machine_path, {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "task_id": state.get("task_id", ""),
        "plan_variant": chapters.get("plan_variant", "single_prompt"),
        "prompt_version": chapters.get("prompt_version", 0),
        "elapsed_seconds": elapsed,
        "quality_score": chapters.get("quality_score", 0),
        "service_trace": trace_records,
        "chapters": chapters,
    })

    # 人类可读
    md = [
        "# 内容重组（替代方案）审查报告",
        "",
        f"- 方案: `Plan B - 单 Prompt`",
        f"- Prompt 版本: `v{chapters.get('prompt_version', 0)}`",
        f"- 生成时间: `{datetime.now().isoformat(timespec='seconds')}`",
        f"- 总耗时: `{elapsed:.1f}s`",
        f"- 内容类型: `{chapters.get('content_type', '?')}`",
        f"- 核心主题: `{chapters.get('core_theme', '?')}`",
        f"- 板块数: `{len(sections)}`",
        f"- LLM 调用次数: `{chapters.get('agent_steps', '?')}`",
        "",
        "---",
        "",
    ]

    for i, sec in enumerate(sections, 1):
        content = sec.get("content", "")
        md.extend([
            f"## 板块 {i}: {sec.get('title', '无标题')}",
            f"*类型: {sec.get('section_type', '?')} | "
            f"字数: {sec.get('word_count', len(content))} | "
            f"来源片段: {sec.get('source_segment_ids', [])}*",
            "",
            content,
            "",
            "---",
            "",
        ])

    if trace_records:
        md.extend(["## LLM 调用追踪", ""])
        for rec in trace_records:
            md.extend([
                f"### LLM #{rec.get('call_id', '?')} ({rec.get('method', '?')})",
                f"- 模型: `{rec.get('model', 'default')}`",
                f"- 耗时: `{rec.get('elapsed_seconds', '?')}s`",
                f"- Prompt 长度: `{rec.get('prompt_chars', '?')}`",
                "",
            ])

    review_path.write_text("\n".join(md), encoding="utf-8")
    _log(f"审查报告: {review_path}")
    _log(f"结构化数据: {machine_path}")
    return review_path


# ============ TracingLLMService ============

class TracingLLMService:
    def __init__(self, inner, records):
        self.inner = inner
        self.records = records
        self.call_index = 0

    async def generate(self, **kwargs):
        return await self._trace("generate", kwargs, self.inner.generate)

    async def generate_json(self, **kwargs):
        return await self._trace("generate_json", kwargs, self.inner.generate_json)

    async def _trace(self, method, kwargs, fn):
        self.call_index += 1
        cid = self.call_index
        model = kwargs.get("model")
        prompt = kwargs.get("prompt", "")

        _log(f"LLM#{cid} {method}: model={model or 'default'}, chars={len(prompt)}")
        t0 = time.perf_counter()
        result = await fn(**kwargs)
        elapsed = round(time.perf_counter() - t0, 3)
        _log(f"LLM#{cid} 完成: {elapsed}s")

        self.records.append({
            "call_id": cid,
            "method": method,
            "model": model,
            "prompt_chars": len(prompt),
            "elapsed_seconds": elapsed,
        })
        return result

    def __getattr__(self, item):
        return getattr(self.inner, item)


# ============ Test: 单次生成 ============

TEST_INPUT_FILE = "workflow_machine_real_audio_20260227_115051.json"


@pytest.mark.asyncio
async def test_single_prompt_generate(monkeypatch):
    """
    快速验证：路由 + 单 prompt 生成是否跑通。

    预期：
    - 2 次 LLM 调用（路由 1 次 + 生成 1 次）
    - 耗时 30-90 秒
    - 输出格式与 Plan A 兼容
    """
    from core.services.llm_service import get_llm_service
    import core.workflow.nodes.restructure_alternative as alt_module

    transcription = load_transcription(TEST_INPUT_FILE)
    state = build_test_state(transcription)

    trace_records = []
    tracing_llm = TracingLLMService(get_llm_service(), trace_records)
    monkeypatch.setattr(alt_module, "get_llm_service", lambda: tracing_llm)

    _log("开始单次生成测试")
    t0 = time.perf_counter()
    result = await alt_module.restructure_alternative_node(state)
    elapsed = round(time.perf_counter() - t0, 3)

    chapters = result["chapters"]
    sections = chapters.get("sections", [])

    _log(f"完成: {len(sections)} 个板块, {elapsed}s")
    _log(f"内容类型: {chapters.get('content_type')}")
    _log(f"核心主题: {chapters.get('core_theme')}")
    for i, sec in enumerate(sections, 1):
        _log(f"  {i}. [{sec.get('section_type')}] {sec.get('title')} ({sec.get('word_count', 0)}字)")

    write_review_report(chapters, elapsed, state, trace_records, suffix="single")

    # 基本断言
    assert result["current_stage"] == WorkflowStage.EXTRACTION.value
    assert len(sections) > 0, "应至少生成一个板块"
    assert chapters.get("content_type"), "应包含内容类型"
    assert chapters.get("plan_variant") == "single_prompt"

    # 检查与 Plan A 的格式兼容性
    assert "chapters" in chapters, "应包含 chapters 兼容字段"
    for sec in sections:
        assert "title" in sec
        assert "content" in sec
        assert "section_type" in sec


# ============ Test: 优化循环 ============

@pytest.mark.asyncio
async def test_optimization_loop(monkeypatch):
    """
    运行 prompt 优化循环（单个测试用例，2 轮迭代）。

    预期：
    - 每轮 3 次 LLM 调用（路由 + 生成 + 评估），最后一轮额外 1 次（优化器）
    - 2 轮总耗时约 5-10 分钟
    - prompt 版本存档到 prompts/restructure_alt/
    """
    from core.services.llm_service import get_llm_service
    import core.workflow.nodes.restructure_alternative as alt_module

    transcription = load_transcription(TEST_INPUT_FILE)
    segments = transcription["segments"]

    trace_records = []
    tracing_llm = TracingLLMService(get_llm_service(), trace_records)
    monkeypatch.setattr(alt_module, "get_llm_service", lambda: tracing_llm)

    from core.workflow.nodes.transcription import build_metadata_context
    state = build_test_state(transcription)
    metadata_ctx = build_metadata_context(state)

    runner = alt_module.OptimizationRunner()
    runner.add_test_case(
        case_id="podcast_test_1",
        segments=segments,
        metadata_context=metadata_ctx,
    )

    _log("开始优化循环（最多 10 轮）")
    t0 = time.perf_counter()
    result = await runner.run(
        max_rounds=10,
        target_score=97.0,
        target_pass_rate=1.0,
    )
    elapsed = round(time.perf_counter() - t0, 3)

    _log(f"优化完成: {result.total_rounds} 轮, "
         f"最终平均分={result.final_prompt.avg_score:.1f}, "
         f"收敛={result.converged}, "
         f"原因={result.stop_reason}")

    for v in result.history:
        _log(f"  v{v.version}: 平均分={v.avg_score:.1f}, 通过率={v.pass_rate:.0%}")

    # 存档汇总
    summary_path = ARTIFACTS_DIR / f"optimization_summary_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    _dump_json(summary_path, {
        "total_rounds": result.total_rounds,
        "converged": result.converged,
        "stop_reason": result.stop_reason,
        "elapsed_seconds": elapsed,
        "history": [
            {
                "version": v.version,
                "avg_score": v.avg_score,
                "pass_rate": v.pass_rate,
                "case_scores": v.case_scores,
            }
            for v in result.history
        ],
        "final_prompt_version": result.final_prompt.version,
    })
    _log(f"优化汇总: {summary_path}")

    # 基本断言
    assert result.total_rounds >= 1
    assert result.final_prompt is not None
    assert result.final_prompt.avg_score is not None
