"""
内容重组 Agent 测试

使用内容解析 Agent 的输出作为输入，测试内容重组 Agent

运行方式：
cd backend
pytest tests/test_restructure.py -v -s
"""
import json
import time
from datetime import datetime
from pathlib import Path

import pytest
from core.workflow.state import PodBookState, create_initial_state, WorkflowStage


def _log_progress(message: str) -> None:
    """打印实时进度日志（配合 pytest -s 查看）"""
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[restructure-test {ts}] {message}", flush=True)


def _preview(value, limit: int = 220) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    text = text.replace("\n", " ")
    if len(text) <= limit:
        return text
    return text[:limit] + "..."


def _dump_json(path: Path, data) -> None:
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )


def _fmt_json(value, max_len: int = 0) -> str:
    """将 dict/list 格式化为缩进 JSON 字符串"""
    text = json.dumps(value, ensure_ascii=False, indent=2, default=str)
    if max_len and len(text) > max_len:
        return text[:max_len] + "\n... (已截断)"
    return text


def _build_trace_report(
    meta: dict,
    chapters: dict,
    sections: list,
    trace_records: list,
) -> str:
    """生成详细的 Agent 执行轨迹 Markdown 报告"""
    lines = [
        "# 内容重组 Agent 执行轨迹",
        "",
        "## 概览",
        "",
        f"| 项目 | 值 |",
        f"|------|------|",
        f"| 生成时间 | `{meta['created_at']}` |",
        f"| 任务ID | `{meta['task_id']}` |",
        f"| 输入片段数 | {meta['input_segments']} |",
        f"| 总耗时 | {meta['elapsed_seconds']}s |",
        f"| Agent步数 | {chapters.get('agent_steps', '?')} |",
        f"| 质量评分 | {chapters.get('quality_score', '?')} |",
        f"| 最终板块数 | {len(sections)} |",
        f"| LLM调用次数 | {len(trace_records)} |",
        "",
    ]

    # ====== 分步执行轨迹 ======
    lines.extend(["---", "", "## 分步执行轨迹", ""])

    # trace_records 是按时间顺序的 LLM 调用列表
    # 奇数调用 = 编排器（决定下一步动作）
    # 偶数调用 = 工具执行（具体任务的 LLM 调用）
    # 但实际上编排器和工具交替出现，我们按「编排器决策 + 工具执行」配对
    step_num = 0
    i = 0
    while i < len(trace_records):
        rec = trace_records[i]
        result = rec.get("result", {})

        # 判断是否是编排器调用（返回值包含 action 字段）
        is_orchestrator = isinstance(result, dict) and "action" in result

        if is_orchestrator:
            step_num += 1
            thinking = result.get("thinking", "（无）")
            action_name = result.get("action", "?")
            action_input = result.get("action_input", {})

            lines.extend([
                f"### Step {step_num}: {action_name}",
                "",
                f"> 耗时 `{rec['elapsed_seconds']}s` · LLM#{rec['call_id']}",
                "",
                "#### 思考过程",
                "",
                thinking,
                "",
                f"#### 决策 → `{action_name}`",
                "",
            ])
            if action_input:
                lines.extend([
                    "**输入参数：**",
                    "",
                    "```json",
                    _fmt_json(action_input, max_len=2000),
                    "```",
                    "",
                ])

            # 检查下一条是否是对应的工具执行结果
            if i + 1 < len(trace_records):
                tool_rec = trace_records[i + 1]
                tool_result = tool_rec.get("result", {})
                is_tool = isinstance(tool_result, dict) and "action" not in tool_result

                if is_tool:
                    i += 1
                    lines.extend([
                        f"#### 执行结果",
                        "",
                        f"> 耗时 `{tool_rec['elapsed_seconds']}s` · LLM#{tool_rec['call_id']}"
                        f" · Prompt {tool_rec['prompt_chars']} 字符",
                        "",
                    ])

                    # 根据不同工具类型，展示不同的结果内容
                    if action_name == "analyze_content":
                        lines.extend(_format_analysis_result(tool_result))
                    elif action_name == "evaluate_quality":
                        lines.extend(_format_evaluation_result(tool_result))
                    elif action_name in ("draft_sections", "revise_section"):
                        lines.extend(_format_draft_result(tool_result, action_name))
                    elif action_name == "plan_sections":
                        lines.extend(_format_plan_result(tool_result))
                    elif action_name == "load_template":
                        lines.extend([
                            "```json",
                            _fmt_json(tool_result, max_len=1000),
                            "```",
                            "",
                        ])
                    elif action_name == "finish":
                        lines.append("✅ Agent 完成任务")
                        lines.append("")
                    else:
                        lines.extend([
                            "```json",
                            _fmt_json(tool_result, max_len=3000),
                            "```",
                            "",
                        ])

                    # 如果工具执行出错
                    if isinstance(tool_result, dict) and "error" in tool_result:
                        lines.extend([
                            "⚠️ **执行出错：**",
                            "",
                            f"```\n{tool_result['error'][:500]}\n```",
                            "",
                        ])

            lines.extend(["---", ""])
        else:
            # 非编排器调用（可能是独立的工具调用或出错重试）
            step_num += 1
            lines.extend([
                f"### Step {step_num}: 直接 LLM 调用",
                "",
                f"> 耗时 `{rec['elapsed_seconds']}s` · LLM#{rec['call_id']}",
                "",
            ])
            if isinstance(result, dict):
                lines.extend([
                    "```json",
                    _fmt_json(result, max_len=3000),
                    "```",
                    "",
                ])
            else:
                lines.extend([str(result)[:2000], ""])
            lines.extend(["---", ""])

        i += 1

    # ====== 最终输出板块 ======
    lines.extend(["## 最终输出板块", ""])

    for idx, section in enumerate(sections, 1):
        section_type = section.get("section_type", "未知")
        title = section.get("title", "无标题")
        content = section.get("content", "")
        key_points = section.get("key_points", [])
        source_ids = section.get("source_segment_ids", [])

        lines.extend([
            f"### 板块 {idx}: [{section_type}] {title}",
            "",
        ])
        if key_points:
            lines.append("**要点：**")
            for kp in key_points:
                lines.append(f"- {kp}")
            lines.append("")
        if source_ids:
            lines.append(f"**来源片段：** {source_ids}")
            lines.append("")
        lines.extend([
            "<details>",
            "<summary>展开查看全文</summary>",
            "",
            content,
            "",
            "</details>",
            "",
            "---",
            "",
        ])

    # ====== LLM 调用汇总 ======
    lines.extend(["## LLM 调用汇总", ""])
    total_elapsed = sum(r["elapsed_seconds"] for r in trace_records)
    total_prompt_chars = sum(r["prompt_chars"] for r in trace_records)
    lines.extend([
        f"| # | 方法 | 模型 | Prompt长度 | 耗时 |",
        f"|---|------|------|-----------|------|",
    ])
    for rec in trace_records:
        lines.append(
            f"| {rec['call_id']} | {rec['method']} | "
            f"`{rec['model'] or 'default'}` | {rec['prompt_chars']} | "
            f"{rec['elapsed_seconds']}s |"
        )
    lines.extend([
        "",
        f"**合计：** {len(trace_records)} 次调用，"
        f"总 Prompt {total_prompt_chars} 字符，"
        f"总耗时 {round(total_elapsed, 1)}s",
        "",
    ])

    return "\n".join(lines)


def _format_analysis_result(result: dict) -> list:
    """格式化内容分析结果"""
    lines = []
    thinking = result.get("thinking", "")
    if thinking:
        lines.extend(["**分析思路：**", "", thinking, ""])

    lines.append(f"**内容类型：** {result.get('content_type', '?')} "
                 f"(置信度 {result.get('confidence', '?')})")
    lines.append(f"**核心主题：** {result.get('core_theme', '?')}")
    lines.append(f"**关键词：** {'、'.join(result.get('theme_keywords', []))}")
    lines.append("")

    speakers = result.get("speakers", [])
    if speakers:
        lines.append("**说话人：**")
        lines.append("")
        for sp in speakers:
            lines.append(f"- **{sp.get('name', '?')}** ({sp.get('role', '?')}): "
                        f"{sp.get('style', '')}，约 {sp.get('segment_count', '?')} 个片段")
        lines.append("")

    topics = result.get("topics_found") or result.get("topics_identified", [])
    if topics:
        lines.append("**识别的话题：**")
        lines.append("")
        for t in topics:
            if isinstance(t, dict):
                lines.append(f"- {t.get('topic', '?')} "
                            f"(片段 {t.get('approx_segments', '?')}, "
                            f"{t.get('importance', '')})")
            else:
                lines.append(f"- {t}")
        lines.append("")

    return lines


def _format_evaluation_result(result: dict) -> list:
    """格式化质量评估结果"""
    lines = []
    thinking = result.get("thinking", "")
    if thinking:
        lines.extend(["**评估思路：**", "", thinking, ""])

    passed = result.get("passed", False)
    score = result.get("weighted_score", "?")
    lines.append(f"**总分：** {score}　{'✅ 通过' if passed else '❌ 未通过'}")
    lines.append("")

    scores = result.get("scores", {})
    if scores:
        lines.append("| 维度 | 分数 | 说明 |")
        lines.append("|------|------|------|")
        for dim, val in scores.items():
            if isinstance(val, dict):
                lines.append(f"| {dim} | {val.get('score', '?')} | {val.get('detail', '')[:80]} |")
            else:
                lines.append(f"| {dim} | {val} | |")
        lines.append("")

    issues = result.get("issues", [])
    if issues:
        lines.append("**问题：**")
        lines.append("")
        for issue in issues:
            if isinstance(issue, dict):
                lines.append(f"- [{issue.get('severity', '?')}] {issue.get('description', issue)}")
            else:
                lines.append(f"- {issue}")
        lines.append("")

    suggestions = result.get("suggestions", [])
    if suggestions:
        lines.append("**建议：**")
        lines.append("")
        for s in suggestions:
            lines.append(f"- {s}")
        lines.append("")

    return lines


def _format_draft_result(result: dict, action_name: str) -> list:
    """格式化撰写/修订结果"""
    lines = []
    thinking = result.get("thinking", "")
    if thinking:
        lines.extend(["**撰写思路：**", "", thinking, ""])

    if action_name == "revise_section":
        title = result.get("title", "")
        if title:
            lines.append(f"**修订后标题：** {title}")
        changes = result.get("changes_made", "")
        if changes:
            lines.append(f"**修改内容：** {changes}")
        content = result.get("content", "")
        if content:
            lines.extend([
                "",
                "<details>",
                "<summary>展开查看修订后内容</summary>",
                "",
                content[:3000] + ("..." if len(content) > 3000 else ""),
                "",
                "</details>",
                "",
            ])
    else:
        # draft_sections 的结果一般有 sections 列表
        draft_sections = result.get("sections", [])
        if draft_sections:
            for idx, sec in enumerate(draft_sections, 1):
                sec_type = sec.get("section_type", "?")
                sec_title = sec.get("title", "?")
                sec_content = sec.get("content", "")
                lines.append(f"**板块 {idx}：** [{sec_type}] {sec_title}")
                lines.append(f"  字数: {len(sec_content)}")
            lines.append("")
        else:
            lines.extend([
                "```json",
                _fmt_json(result, max_len=3000),
                "```",
                "",
            ])

    return lines


def _format_plan_result(result: dict) -> list:
    """格式化板块规划结果"""
    lines = []
    if result.get("plan_accepted"):
        lines.append(f"✅ 规划已接受，共 {result.get('section_count', '?')} 个板块")
        lines.append("")
    else:
        lines.extend([
            "```json",
            _fmt_json(result, max_len=2000),
            "```",
            "",
        ])
    return lines


class TracingLLMService:
    """LLM服务追踪包装器"""

    def __init__(self, inner_service, trace_records):
        self.inner = inner_service
        self.records = trace_records
        self.call_index = 0

    async def generate(self, **kwargs):
        return await self._trace_call("generate", kwargs, self.inner.generate)

    async def generate_json(self, **kwargs):
        return await self._trace_call("generate_json", kwargs, self.inner.generate_json)

    async def _trace_call(self, method_name, kwargs, method):
        self.call_index += 1
        call_id = self.call_index
        started_at = datetime.now().isoformat(timespec="seconds")

        model = kwargs.get("model")
        prompt = kwargs.get("prompt", "")
        system_prompt = kwargs.get("system_prompt")

        _log_progress(
            f"LLM#{call_id} {method_name} 开始: model={model or 'default'}, "
            f"prompt_chars={len(prompt)}"
        )

        t0 = time.perf_counter()
        result = await method(**kwargs)
        elapsed = round(time.perf_counter() - t0, 3)

        if isinstance(result, dict):
            result_summary = {"result_type": "dict", "keys": sorted(result.keys())}
        else:
            result_summary = {"result_type": type(result).__name__, "preview": _preview(result)}

        _log_progress(
            f"LLM#{call_id} {method_name} 完成: elapsed={elapsed}s, "
            f"summary={_preview(result_summary)}"
        )

        self.records.append({
            "type": "llm",
            "call_id": call_id,
            "method": method_name,
            "started_at": started_at,
            "elapsed_seconds": elapsed,
            "model": model,
            "prompt_chars": len(prompt),
            "prompt_preview": _preview(prompt, limit=500),
            "system_prompt_preview": _preview(system_prompt, limit=300) if system_prompt else "",
            "result_summary": result_summary,
            "result": result,
        })
        return result

    def __getattr__(self, item):
        return getattr(self.inner, item)


def load_transcription_result() -> dict:
    """加载内容解析 Agent 的输出结果"""
    artifacts_dir = Path(__file__).resolve().parent / "artifacts"
    
    # 查找最新的 real_audio 测试结果
    machine_files = sorted(
        artifacts_dir.glob("workflow_machine_real_audio_*.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True
    )
    
    if not machine_files:
        raise FileNotFoundError(
            "找不到内容解析 Agent 的输出文件，请先运行 test_workflow.py 测试"
        )
    
    latest_file = machine_files[0]
    _log_progress(f"加载转写结果: {latest_file.name}")
    
    data = json.loads(latest_file.read_text(encoding="utf-8"))
    
    # 从 final_state 或 stage_results 中提取 transcription
    transcription = data.get("final_state", {}).get("transcription")
    if not transcription:
        # 尝试从 stage_results 中获取
        for stage_result in data.get("stage_results", []):
            if stage_result.get("stage") == "transcription":
                transcription = stage_result.get("output", {}).get("transcription")
                break
    
    if not transcription:
        raise ValueError("无法从测试结果中提取 transcription 数据")
    
    segment_count = len(transcription.get("segments", []))
    _log_progress(f"已加载 {segment_count} 个片段")
    
    return transcription


@pytest.mark.asyncio
async def test_restructure_agent(monkeypatch):
    """
    测试内容重组 Agent
    
    使用内容解析 Agent 的真实输出作为输入
    """
    from core.services.llm_service import get_llm_service
    import core.workflow.nodes.restructure as restructure_module
    
    # 加载转写结果
    transcription = load_transcription_result()
    
    # 创建初始状态（带有 transcription）
    initial_state = create_initial_state(
        task_id="restructure_test",
        audio_path="../podcast_test_1.m4a",
        title="十字路口播客测试",
        author="测试"
    )
    # 注入 transcription 数据
    initial_state["transcription"] = transcription
    initial_state["current_stage"] = WorkflowStage.RESTRUCTURE.value

    # 设置追踪
    trace_records = []
    real_llm_service = get_llm_service()
    tracing_llm = TracingLLMService(real_llm_service, trace_records)
    monkeypatch.setattr(restructure_module, "get_llm_service", lambda: tracing_llm)

    # 执行 restructure 节点
    _log_progress("开始执行内容重组 Agent")
    _log_progress(f"输入片段数: {len(transcription.get('segments', []))}")
    
    stage_start = time.perf_counter()
    stage_output = await restructure_module.restructure_node(initial_state)
    stage_elapsed = round(time.perf_counter() - stage_start, 3)

    # 更新状态
    final_state = dict(initial_state)
    final_state.update(stage_output)

    _log_progress(
        f"内容重组完成: elapsed={stage_elapsed}s, "
        f"output_keys={sorted(stage_output.keys())}"
    )

    # 输出结果
    chapters = stage_output.get("chapters", {})
    sections = chapters.get("sections", [])
    _log_progress(f"生成板块数: {len(sections)}")
    _log_progress(f"Agent步数: {chapters.get('agent_steps', 0)}")
    _log_progress(f"质量评分: {chapters.get('quality_score', 0)}")

    # 保存输出文件
    artifacts_dir = Path(__file__).resolve().parent / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    base_name = f"restructure_test_{ts}"
    review_path = artifacts_dir / f"restructure_review_{base_name}.md"
    machine_path = artifacts_dir / f"restructure_machine_{base_name}.json"

    # 机器可读输出
    machine_payload = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "task_id": initial_state["task_id"],
        "input_segments": len(transcription.get("segments", [])),
        "elapsed_seconds": stage_elapsed,
        "service_trace": trace_records,
        "chapters": chapters,
        "final_state": final_state,
    }
    _dump_json(machine_path, machine_payload)

    # 人类可读审查报告（详细执行轨迹）
    review_path.write_text(
        _build_trace_report(machine_payload, chapters, sections, trace_records),
        encoding="utf-8",
    )

    _log_progress(f"审查报告已写入: {review_path}")
    _log_progress(f"结构化结果已写入: {machine_path}")

    # 验证输出
    assert final_state is not None
    assert "chapters" in final_state
    assert final_state["chapters"] is not None
    assert "sections" in chapters
    assert len(sections) > 0, "应该生成至少一个板块"
    assert review_path.exists()
    assert machine_path.exists()

    # 打印板块概览
    _log_progress("=" * 50)
    _log_progress("板块概览:")
    for i, section in enumerate(sections, 1):
        _log_progress(f"  {i}. [{section.get('section_type', '?')}] {section.get('title', '?')}")
    _log_progress("=" * 50)
