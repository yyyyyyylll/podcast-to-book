import asyncio
import json
import re
import sys
import time
from contextvars import ContextVar
from typing import Optional, Any

from core.config import settings


# ===== 全局 LLM 调用日志（按 task_id 存储，供前端看板实时查询） =====
_task_llm_logs: dict[str, list[dict]] = {}
_current_task_id: ContextVar[Optional[str]] = ContextVar("_current_task_id", default=None)

# ===== 模型覆写（benchmark 专用，ContextVar 天然隔离并发协程）=====
_model_override: ContextVar[Optional[str]] = ContextVar("_model_override", default=None)


def set_model_override(model: str):
    _model_override.set(model)


def clear_model_override():
    _model_override.set(None)


def bind_task_id(task_id: str):
    """绑定当前协程到指定 task_id，后续 LLM 调用日志会记录到该 task 下"""
    _current_task_id.set(task_id)
    if task_id not in _task_llm_logs:
        _task_llm_logs[task_id] = []


def get_llm_logs(task_id: str) -> list[dict]:
    """获取指定任务的所有 LLM 调用日志"""
    return _task_llm_logs.get(task_id, [])


def clear_llm_logs(task_id: str):
    """清理已完成任务的日志（释放内存）"""
    _task_llm_logs.pop(task_id, None)


def _append_call_log(record: dict):
    """向当前 task 追加一条调用日志"""
    tid = _current_task_id.get()
    if tid and tid in _task_llm_logs:
        _task_llm_logs[tid].append(record)


class UsageTracker:
    """单次工作流的 token 用量追踪器，每个任务独立实例，通过 ContextVar 绑定到各自协程。"""

    def __init__(self):
        self._current_stage = "unknown"
        self._usage_records: list[dict] = []

    def set_stage(self, stage: str):
        self._current_stage = stage

    def record(self, model: str, usage, is_search: bool = False):
        if not usage:
            return
        self._usage_records.append({
            "stage": self._current_stage,
            "model": model,
            "prompt_tokens": usage.prompt_tokens or 0,
            "completion_tokens": usage.completion_tokens or 0,
            "is_search": is_search,
        })

    def summarize(self) -> dict:
        stages: dict[str, dict] = {}
        totals = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "calls": 0}
        for r in self._usage_records:
            stage = r["stage"]
            if stage not in stages:
                stages[stage] = {
                    "prompt_tokens": 0, "completion_tokens": 0,
                    "total_tokens": 0, "calls": 0, "models": [],
                }
            s = stages[stage]
            s["prompt_tokens"] += r["prompt_tokens"]
            s["completion_tokens"] += r["completion_tokens"]
            s["total_tokens"] += r["prompt_tokens"] + r["completion_tokens"]
            s["calls"] += 1
            if r["model"] not in s["models"]:
                s["models"].append(r["model"])
            totals["prompt_tokens"] += r["prompt_tokens"]
            totals["completion_tokens"] += r["completion_tokens"]
            totals["total_tokens"] += r["prompt_tokens"] + r["completion_tokens"]
            totals["calls"] += 1
        return {"stages": stages, "totals": totals, "records": self._usage_records}


# 每个 asyncio Task 独立持有自己的 UsageTracker（解决并发任务数据混淆问题）
_current_tracker: ContextVar[Optional[UsageTracker]] = ContextVar("_current_tracker", default=None)


DEFAULT_MODEL_TIMEOUT = 180


class LLMService:
    """
    LLM 服务（异步），通过 OpenAI 兼容接口调用。

    模型配置统一在 config.py / .env 中管理：
    - LLM_MODEL: 主力模型（成稿、评估等核心任务）
    - LLM_SEARCH_MODEL: 联网搜索模型（编者序背景搜索等）
    """

    def __init__(self):
        self.default_model = settings.LLM_MODEL
        self.search_model = settings.LLM_SEARCH_MODEL
        self.api_key = settings.OPENAI_API_KEY
        self.base_url = settings.OPENAI_BASE_URL
        self._client = None
        self._last_search_queries: list[str] = []

    def _get_client(self):
        if self._client is None:
            from openai import AsyncOpenAI
            kwargs = {"api_key": self.api_key}
            if self.base_url:
                kwargs["base_url"] = self.base_url
            self._client = AsyncOpenAI(**kwargs)
        return self._client

    def _log(self, msg: str):
        print(f"[LLM] {msg}", flush=True)

    @staticmethod
    def _is_missing_image_model_error(exc: Exception) -> bool:
        text = str(exc)
        return (
            (
                "404" in text
                or "400" in text
                or "badrequesterror" in type(exc).__name__.lower()
            ) and (
                "请求模型或对应资源不存在" in text
                or "invalid image llm model" in text.lower()
                or "model" in text.lower()
                or "resource" in text.lower()
                or "not found" in text.lower()
            )
        )

    async def generate(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        model: Optional[str] = None,
        thinking_budget: Optional[int] = None,
        timeout: Optional[int] = None,
        label: Optional[str] = None,
        json_mode: bool = False,
    ) -> str:
        override = _model_override.get()
        use_model = override or model or self.default_model
        effective_timeout = timeout or DEFAULT_MODEL_TIMEOUT
        effective_temp = temperature if temperature is not None else settings.LLM_TEMPERATURE
        effective_max_tokens = max_tokens or settings.LLM_MAX_TOKENS

        tracker = _current_tracker.get()
        stage = tracker._current_stage if tracker else "unknown"

        call_log = {
            "call_id": id(prompt) & 0xFFFFFF,
            "stage": stage,
            "label": label or "",
            "model": use_model,
            "prompt_chars": 0,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "duration_s": 0,
            "tokens_per_sec": 0,
            "status": "running",
            "started_at": time.time(),
            "finished_at": None,
            "error": None,
        }

        try:
            client = self._get_client()

            messages = []
            if system_prompt:
                messages.append({"role": "system", "content": system_prompt})
            messages.append({"role": "user", "content": prompt})

            total_prompt_len = sum(len(m["content"]) for m in messages)
            call_log["prompt_chars"] = total_prompt_len
            _append_call_log(call_log)

            self._log(f"调用模型: {use_model}, prompt长度: {total_prompt_len}")

            create_kwargs = {
                "model": use_model,
                "messages": messages,
            }
            create_kwargs["max_tokens"] = effective_max_tokens
            if temperature is not None:
                create_kwargs["temperature"] = temperature
            if json_mode:
                create_kwargs["response_format"] = {"type": "json_object"}

            t0 = time.perf_counter()
            response = await asyncio.wait_for(
                client.chat.completions.create(**create_kwargs),
                timeout=effective_timeout,
            )
            duration = time.perf_counter() - t0

            choice = response.choices[0]
            content = choice.message.content or ""
            finish_reason = choice.finish_reason

            if finish_reason == "length":
                trunc_label = label or "(无标签)"
                self._log(
                    f"⚠ 输出被截断 (finish_reason=length): "
                    f"model={use_model}, label={trunc_label}, "
                    f"max_tokens={effective_max_tokens}, 输出长度={len(content)}"
                )

            usage = response.usage
            self._record_usage(use_model, usage)

            call_log["status"] = "success"
            call_log["finish_reason"] = finish_reason
            call_log["duration_s"] = round(duration, 2)
            call_log["finished_at"] = time.time()
            if usage:
                call_log["prompt_tokens"] = usage.prompt_tokens or 0
                call_log["completion_tokens"] = usage.completion_tokens or 0
                call_log["total_tokens"] = (usage.prompt_tokens or 0) + (usage.completion_tokens or 0)
                if duration > 0 and usage.completion_tokens:
                    call_log["tokens_per_sec"] = round(usage.completion_tokens / duration, 1)
                self._log(
                    f"响应成功, 输出长度: {len(content)}, "
                    f"tokens: prompt={usage.prompt_tokens}, completion={usage.completion_tokens}, "
                    f"耗时: {duration:.1f}s, 速度: {call_log['tokens_per_sec']} t/s"
                )
            else:
                self._log(f"响应成功, 输出长度: {len(content)}, 耗时: {duration:.1f}s")

            return content

        except asyncio.TimeoutError:
            call_log["status"] = "timeout"
            call_log["finished_at"] = time.time()
            call_log["duration_s"] = effective_timeout
            call_log["error"] = f"超时 ({effective_timeout}s)"
            self._log(f"ERROR: 模型 {use_model} 超时 ({effective_timeout}s)")
            raise TimeoutError(f"LLM 调用超时: model={use_model}, timeout={effective_timeout}s")
        except ImportError:
            self._log("警告: openai 未安装，使用模拟响应")
            call_log["status"] = "mock"
            call_log["finished_at"] = time.time()
            return self._mock_response(prompt, use_model)
        except Exception as e:
            call_log["status"] = "error"
            call_log["finished_at"] = time.time()
            call_log["duration_s"] = round(time.time() - call_log["started_at"], 2)
            call_log["error"] = f"{type(e).__name__}: {str(e)[:200]}"
            raise

    async def generate_json(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        model: Optional[str] = None,
        thinking_budget: Optional[int] = None,
        timeout: Optional[int] = None,
        label: Optional[str] = None,
    ) -> dict:
        result = await self.generate(
            prompt=prompt,
            system_prompt=system_prompt,
            temperature=temperature,
            max_tokens=max_tokens,
            model=model,
            thinking_budget=thinking_budget,
            timeout=timeout,
            label=label,
            json_mode=True,
        )
        return self._parse_json(result)

    def _parse_json(self, text: str) -> dict:
        """解析LLM返回的JSON，处理markdown代码块、前缀推理文字、截断等情况"""
        json_match = re.search(r'```(?:json)?\s*([\s\S]*?)\s*```', text)
        if json_match:
            text = json_match.group(1)
        elif text.strip().startswith("```"):
            text = re.sub(r'^```(?:json)?\s*', '', text.strip())

        text = text.strip()

        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # thinking 模型先输出推理文字、再输出 JSON，从右往左扫 { 更快找到根对象
        brace_positions = [m.start() for m in re.finditer(r'\{', text)]
        for pos in reversed(brace_positions):
            candidate = text[pos:]
            try:
                result = json.loads(candidate)
                if isinstance(result, dict):
                    return result
            except json.JSONDecodeError:
                pass
            last_end = candidate.rfind('}')
            if last_end > 0:
                try:
                    result = json.loads(candidate[:last_end + 1])
                    if isinstance(result, dict):
                        return result
                except json.JSONDecodeError:
                    pass
            repaired = self._repair_truncated_json(candidate)
            if repaired is not None:
                return repaired

        raise ValueError(f"JSON解析失败（含截断修复尝试）\n原始文本: {text[:500]}")

    def _repair_truncated_json(self, text: str) -> Optional[dict]:
        """修复被截断的 JSON（LLM 输出达到 token 上限时常见）。

        通过状态机找到字符串外的逗号位置作为安全截断点，
        然后补齐未闭合的括号。
        """
        in_string = False
        escape_next = False
        comma_positions: list[int] = []

        for i, ch in enumerate(text):
            if escape_next:
                escape_next = False
                continue
            if in_string:
                if ch == '\\':
                    escape_next = True
                elif ch == '"':
                    in_string = False
                continue
            if ch == '"':
                in_string = True
            elif ch == ',':
                comma_positions.append(i)

        for pos in reversed(comma_positions[-80:]):
            fragment = text[:pos]
            closing = self._calc_json_closing(fragment)
            try:
                result = json.loads(fragment + closing)
                print(f"[llm] JSON 截断修复成功 (在 {pos}/{len(text)} 处截断)")
                return result
            except json.JSONDecodeError:
                continue

        return None

    @staticmethod
    def _calc_json_closing(text: str) -> str:
        """计算 JSON 片段需要补齐的闭合字符"""
        in_string = False
        escape_next = False
        stack: list[str] = []
        for ch in text:
            if escape_next:
                escape_next = False
                continue
            if in_string:
                if ch == '\\':
                    escape_next = True
                elif ch == '"':
                    in_string = False
                continue
            if ch == '"':
                in_string = True
            elif ch in ('{', '['):
                stack.append(ch)
            elif ch == '}' and stack and stack[-1] == '{':
                stack.pop()
            elif ch == ']' and stack and stack[-1] == '[':
                stack.pop()
        return ''.join('}' if c == '{' else ']' for c in reversed(stack))

    async def generate_with_search(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        model: Optional[str] = None,
        max_tokens: Optional[int] = None,
        timeout: Optional[int] = None,
        label: Optional[str] = None,
    ) -> str:
        """使用联网搜索模型（LLM_SEARCH_MODEL）生成内容。

        该模型自带联网搜索能力，通过普通 chat completions 接口调用，
        模型在推理过程中自主搜索并整合搜索结果。
        """
        override = _model_override.get()
        use_model = override or model or self.search_model
        effective_timeout = timeout or 300
        effective_max_tokens = max_tokens or settings.LLM_MAX_TOKENS

        tracker = _current_tracker.get()
        stage = tracker._current_stage if tracker else "unknown"

        call_log = {
            "call_id": id(prompt) & 0xFFFFFF,
            "stage": stage,
            "label": label or "",
            "model": use_model,
            "prompt_chars": 0,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "duration_s": 0,
            "tokens_per_sec": 0,
            "status": "running",
            "started_at": time.time(),
            "finished_at": None,
            "error": None,
            "is_search": True,
        }

        try:
            client = self._get_client()

            messages = []
            if system_prompt:
                messages.append({"role": "system", "content": system_prompt})
            messages.append({"role": "user", "content": prompt})

            total_prompt_len = sum(len(m["content"]) for m in messages)
            call_log["prompt_chars"] = total_prompt_len
            _append_call_log(call_log)

            self._log(f"[web_search] 调用模型: {use_model}, prompt长度: {total_prompt_len}")

            create_kwargs = {
                "model": use_model,
                "messages": messages,
            }
            if _model_override.get():
                create_kwargs["max_tokens"] = effective_max_tokens
            else:
                create_kwargs["max_completion_tokens"] = effective_max_tokens

            t0 = time.perf_counter()
            response = await asyncio.wait_for(
                client.chat.completions.create(**create_kwargs),
                timeout=effective_timeout,
            )
            duration = time.perf_counter() - t0

            choice = response.choices[0]
            content = choice.message.content or ""

            usage = response.usage
            self._record_usage(use_model, usage, is_search=True)

            call_log["status"] = "success"
            call_log["duration_s"] = round(duration, 2)
            call_log["finished_at"] = time.time()
            if usage:
                call_log["prompt_tokens"] = usage.prompt_tokens or 0
                call_log["completion_tokens"] = usage.completion_tokens or 0
                call_log["total_tokens"] = (usage.prompt_tokens or 0) + (usage.completion_tokens or 0)
                if duration > 0 and usage.completion_tokens:
                    call_log["tokens_per_sec"] = round(usage.completion_tokens / duration, 1)
                self._log(
                    f"[web_search] 响应成功, 输出长度: {len(content)}, "
                    f"tokens: prompt={usage.prompt_tokens}, completion={usage.completion_tokens}, "
                    f"耗时: {duration:.1f}s"
                )
            else:
                self._log(f"[web_search] 响应成功, 输出长度: {len(content)}, 耗时: {duration:.1f}s")

            return content

        except asyncio.TimeoutError:
            call_log["status"] = "timeout"
            call_log["finished_at"] = time.time()
            call_log["duration_s"] = effective_timeout
            call_log["error"] = f"超时 ({effective_timeout}s)"
            self._log(f"[web_search] ERROR: 超时 ({effective_timeout}s)")
            raise TimeoutError(
                f"LLM web_search 调用超时: model={use_model}, timeout={effective_timeout}s"
            )
        except Exception as e:
            call_log["status"] = "error"
            call_log["finished_at"] = time.time()
            call_log["duration_s"] = round(time.time() - call_log["started_at"], 2)
            call_log["error"] = f"{type(e).__name__}: {str(e)[:200]}"
            self._log(f"[web_search] 调用失败 ({type(e).__name__}: {e})，降级为普通生成")
            return await self.generate(
                prompt=prompt, system_prompt=system_prompt,
                max_tokens=max_tokens, timeout=timeout,
            )

    # ===== 图片生成 =====

    async def generate_image(
        self,
        prompt: str,
        model: Optional[str] = None,
        size: Optional[str] = None,
        timeout: Optional[int] = None,
        label: Optional[str] = None,
    ) -> str:
        """调用 GeeKAI 图片生成 API（OpenAI images.generate 兼容），返回图片 URL。"""
        use_model = model or settings.IMAGE_GEN_MODEL
        use_size = size or settings.IMAGE_GEN_SIZE
        effective_timeout = timeout or 120

        tracker = _current_tracker.get()
        stage = tracker._current_stage if tracker else "unknown"

        call_log = {
            "call_id": id(prompt) & 0xFFFFFF,
            "stage": stage,
            "label": label or "图片生成",
            "model": use_model,
            "prompt_chars": len(prompt),
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "duration_s": 0,
            "tokens_per_sec": 0,
            "status": "running",
            "started_at": time.time(),
            "finished_at": None,
            "error": None,
            "is_image": True,
        }
        _append_call_log(call_log)

        async def _request_image(request_model: str):
            client = self._get_client()
            self._log(f"[image_gen] 调用模型: {request_model}, size: {use_size}")
            t0 = time.perf_counter()
            response = await asyncio.wait_for(
                client.images.generate(
                    model=request_model,
                    prompt=prompt,
                    n=1,
                    size=use_size,
                ),
                timeout=effective_timeout,
            )
            duration = time.perf_counter() - t0
            return response, duration, request_model

        try:
            try:
                response, duration, resolved_model = await _request_image(use_model)
            except Exception as e:
                fallback_model = settings.IMAGE_GEN_FALLBACK_MODEL
                should_fallback = (
                    not model
                    and fallback_model
                    and fallback_model != use_model
                    and self._is_missing_image_model_error(e)
                )
                if not should_fallback:
                    raise

                self._log(
                    f"[image_gen] 主模型 {use_model} 不可用，自动切换到兜底模型 {fallback_model}"
                )
                call_log["error"] = f"{type(e).__name__}: {str(e)[:200]}"
                response, duration, resolved_model = await _request_image(fallback_model)
                call_log["fallback_from"] = use_model
                call_log["fallback_to"] = fallback_model
                call_log["model"] = fallback_model

            image_url = response.data[0].url
            self._log(f"[image_gen] 生成成功: {image_url[:80]}... 耗时: {duration:.1f}s")

            call_log["status"] = "success"
            call_log["duration_s"] = round(duration, 2)
            call_log["finished_at"] = time.time()

            if tracker:
                tracker._usage_records.append({
                    "stage": tracker._current_stage,
                    "model": resolved_model,
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "is_search": False,
                    "is_image_gen": True,
                })

            return image_url

        except asyncio.TimeoutError:
            call_log["status"] = "timeout"
            call_log["finished_at"] = time.time()
            call_log["duration_s"] = effective_timeout
            call_log["error"] = f"超时 ({effective_timeout}s)"
            self._log(f"[image_gen] ERROR: 超时 ({effective_timeout}s)")
            raise TimeoutError(f"图片生成超时: model={use_model}")
        except Exception as e:
            call_log["status"] = "error"
            call_log["finished_at"] = time.time()
            call_log["duration_s"] = round(time.time() - call_log["started_at"], 2)
            call_log["error"] = f"{type(e).__name__}: {str(e)[:200]}"
            raise

    @staticmethod
    def _build_concept_review_prompt(
        expected_subject: str, context: str, visual_form: str,
    ) -> str:
        form_desc = f"（预期图表形式：{visual_form}）" if visual_form else ""
        return (
            f"你是一位极其严格的图书出版质量审核员。你的职责是确保只有真正高质量的图片才能进入正式出版物。\n"
            f"你审核的通过率通常只有 30-50%，大部分 AI 生成的图片都达不到出版标准。\n\n"
            f"【预期主题】{expected_subject}\n"
            f"【图片类型】概念配图（AI生成）{form_desc}\n"
            f"【正文上下文】{context[:500]}\n\n"
            f"请按以下 6 个维度各打 1-5 分。每个维度的评分标准如下：\n\n"
            f"1. 概念准确性 (1-5):\n"
            f"   5=图中元素与方法论的核心要素完全对应\n"
            f"   3=大致对应但有遗漏或偏差\n"
            f"   1=图片内容与预期主题无关或严重偏离\n\n"
            f"2. 结构清晰度 (1-5):\n"
            f"   5=读者 3 秒内能看懂逻辑关系和阅读顺序\n"
            f"   3=需要仔细看才能理解结构\n"
            f"   1=逻辑混乱，无法辨别元素间的关系\n\n"
            f"3. 文字质量 (1-5):\n"
            f"   5=所有文字均为正确简体中文且清晰可读\n"
            f"   3=大部分可读但有个别文字模糊或错误\n"
            f"   1=文字乱码、错字多、或混杂英文/日文/繁体\n\n"
            f"4. 视觉规范性 (1-5):\n"
            f"   5=简洁正式，配色统一（棕金暖色调），无多余装饰\n"
            f"   3=基本规范但配色不统一或有少量多余元素\n"
            f"   1=花哨、配色混乱、有装饰性图标或人物\n\n"
            f"5. 信息完整性 (1-5):\n"
            f"   5=方法论的所有关键要素都在图中体现\n"
            f"   3=体现了主要要素但缺少重要部分\n"
            f"   1=只有标题没有实质内容，或要素严重不完整\n\n"
            f"6. 出版适宜性 (1-5):\n"
            f"   5=可直接用于正式出版物，印刷效果好\n"
            f"   3=勉强可用但不够专业\n"
            f"   1=明显不适合出版（模糊、变形、比例失调）\n\n"
            f"## 一票否决条件（遇到任一项直接拒绝，approved=false）：\n"
            f"- 文字出现乱码、错字、或非简体中文字符\n"
            f"- 图片逻辑混乱，读者无法在 5 秒内理解含义\n"
            f"- 图中元素与预期方法论的核心要素不对应\n"
            f"- 有重复的文字标签（同一个词出现两次以上）\n"
            f"- 配色严重偏离暖色调（出现蓝色、灰色等冷色系主色）\n\n"
            f"严格JSON格式输出：\n"
            f'{{"scores": {{"d1": 分数, "d2": 分数, "d3": 分数, "d4": 分数, "d5": 分数, "d6": 分数}}, '
            f'"total_score": 总分, "approved": true或false, '
            f'"reason": "具体说明通过或拒绝的理由（指出具体问题）"}}\n\n'
            f"通过标准：总分 >= 24 且每项 >= 4 且无一票否决项。"
        )

    @staticmethod
    def _build_search_review_prompt(
        expected_subject: str, context: str,
    ) -> str:
        return (
            f"你是图书出版的视觉编辑，正在为一本访谈类商业图书筛选网络配图。\n"
            f"网络图片质量参差不齐，你需要严格把关，只放行真正适合印刷出版的高质量照片。\n\n"
            f"【预期主题】{expected_subject}\n"
            f"【正文上下文】{context[:500]}\n\n"
            f"请按以下 6 个维度各打 1-5 分：\n\n"
            f"1. 主题匹配 (1-5):\n"
            f"   5=一眼就能认出是预期实体\n"
            f"   3=有关联但不够直接\n"
            f"   1=与预期主题无关\n\n"
            f"2. 清晰度 (1-5):\n"
            f"   5=高清、锐利、色彩准确\n"
            f"   3=轻微模糊或压缩痕迹\n"
            f"   1=严重模糊或像素化\n\n"
            f"3. 专业感 (1-5):\n"
            f"   5=官方照片、正式场合、构图专业\n"
            f"   3=非正式但可接受\n"
            f"   1=随意拍摄、娱乐化、低俗\n\n"
            f"4. 画面干净度 (1-5):\n"
            f"   5=无水印、无广告、无多余文字\n"
            f"   3=轻微水印但不遮挡主体\n"
            f"   1=大面积水印或广告\n\n"
            f"5. 信息价值 (1-5):\n"
            f"   5=帮助读者直观理解正文内容\n"
            f"   3=有一定信息量\n"
            f"   1=纯 logo / 图标 / 装饰性图片\n\n"
            f"6. 出版适宜性 (1-5):\n"
            f"   5=可直接用于正式出版物\n"
            f"   3=勉强可用\n"
            f"   1=不适合出版\n\n"
            f"## 一票否决（任一项命中则 approved=false）：\n"
            f"- 与预期主题完全无关\n"
            f"- 大面积水印遮挡主体\n"
            f"- 截图、拼图、meme\n"
            f"- 严重模糊，不适合印刷\n"
            f"- 内容不当或有争议\n\n"
            f"严格JSON格式输出：\n"
            f'{{"scores": {{"d1": 分数, "d2": 分数, "d3": 分数, "d4": 分数, "d5": 分数, "d6": 分数}}, '
            f'"total_score": 总分, "approved": true或false, '
            f'"reason": "简要说明理由"}}\n\n'
            f"通过标准：总分 >= 24 且每项 >= 4 且无一票否决项。"
        )

    async def review_image(
        self,
        image_base64: str,
        context: str,
        expected_subject: str,
        image_type: str,
        visual_form: str = "",
        model: Optional[str] = None,
        timeout: Optional[int] = None,
        label: Optional[str] = None,
    ) -> dict:
        """使用 VLM 严格审核图片质量与相关性。

        返回 { approved: bool, total_score: int, scores: {...}, reason: str }
        """
        use_model = model or settings.VLM_REVIEW_MODEL
        effective_timeout = timeout or 60

        tracker = _current_tracker.get()
        stage = tracker._current_stage if tracker else "unknown"

        call_log = {
            "call_id": id(expected_subject) & 0xFFFFFF,
            "stage": stage,
            "label": label or f"图片审核: {expected_subject[:20]}",
            "model": use_model,
            "prompt_chars": len(context[:500]) + len(expected_subject),
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "duration_s": 0,
            "tokens_per_sec": 0,
            "status": "running",
            "started_at": time.time(),
            "finished_at": None,
            "error": None,
            "is_vlm": True,
        }
        _append_call_log(call_log)

        if image_type == "search":
            review_prompt = self._build_search_review_prompt(
                expected_subject, context,
            )
        else:
            review_prompt = self._build_concept_review_prompt(
                expected_subject, context, visual_form,
            )

        try:
            client = self._get_client()
            self._log(f"[vlm_review] 调用模型: {use_model}, 主题: {expected_subject[:30]}")

            t0 = time.perf_counter()
            response = await asyncio.wait_for(
                client.chat.completions.create(
                    model=use_model,
                    messages=[{
                        "role": "user",
                        "content": [
                            {
                                "type": "image_url",
                                "image_url": {"url": f"data:image/jpeg;base64,{image_base64}"},
                            },
                            {"type": "text", "text": review_prompt},
                        ],
                    }],
                    max_completion_tokens=512,
                ),
                timeout=effective_timeout,
            )
            duration = time.perf_counter() - t0

            content = response.choices[0].message.content or ""
            usage = response.usage
            self._record_usage(use_model, usage)

            call_log["status"] = "success"
            call_log["duration_s"] = round(duration, 2)
            call_log["finished_at"] = time.time()
            if usage:
                call_log["prompt_tokens"] = usage.prompt_tokens or 0
                call_log["completion_tokens"] = usage.completion_tokens or 0
                call_log["total_tokens"] = (usage.prompt_tokens or 0) + (usage.completion_tokens or 0)

            result = self._parse_json(content)
            total = result.get("total_score", 0)
            approved = result.get("approved", False)
            reason = result.get("reason", "")
            self._log(
                f"[vlm_review] {'通过' if approved else '拒绝'}: "
                f"{expected_subject[:20]} 总分 {total}/30 — {reason[:80]}"
            )
            return result

        except Exception as e:
            call_log["status"] = "error"
            call_log["finished_at"] = time.time()
            call_log["duration_s"] = round(time.time() - call_log["started_at"], 2)
            call_log["error"] = f"{type(e).__name__}: {str(e)[:200]}"
            self._log(f"[vlm_review] 审核失败 ({type(e).__name__}: {e})，默认拒绝")
            return {"approved": False, "total_score": 0, "scores": {}, "reason": f"审核异常: {e}"}

    # ===== Token 用量追踪 =====

    def start_tracking(self):
        """为当前 asyncio Task 创建独立的追踪器（并发任务各自隔离）"""
        _current_tracker.set(UsageTracker())

    def set_tracking_stage(self, stage: str):
        """设置当前追踪阶段"""
        tracker = _current_tracker.get()
        if tracker:
            tracker.set_stage(stage)

    def stop_tracking(self) -> dict:
        """停止追踪并返回汇总数据"""
        tracker = _current_tracker.get()
        _current_tracker.set(None)
        return tracker.summarize() if tracker else {"stages": {}, "totals": {}, "records": []}

    def _record_usage(self, model: str, usage, is_search: bool = False):
        """记录一次 API 调用的 token 用量"""
        tracker = _current_tracker.get()
        if tracker:
            tracker.record(model, usage, is_search)

    def _mock_response(self, prompt: str, model: str = "unknown") -> str:
        """开发环境的模拟响应"""
        return json.dumps({
            "mock": True,
            "model": model,
            "message": "模拟响应",
            "prompt_preview": prompt[:100]
        }, ensure_ascii=False)


# 全局单例
_llm_service: Optional[LLMService] = None


def get_llm_service() -> LLMService:
    """获取LLM服务单例"""
    global _llm_service
    if _llm_service is None:
        _llm_service = LLMService()
    return _llm_service
