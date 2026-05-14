"""
编者序生成节点

独立于正文成稿流程，使用联网搜索模型搜索嘉宾背景、
行业趋势等外部信息，生成更有深度的编者序。

两步流程：
  Step 1  联网搜索增强生成（gpt-5.2）
  Step 2  自审修正（Chat Completions API）

降级策略：
  - 联网搜索失败 → 纯 LLM 生成
  - 整体失败 → 使用 compose 阶段已生成的 lead_paragraph 作为 fallback
"""

import time
from typing import Any, Dict, List, Optional

from core.services.llm_service import get_llm_service
from core.workflow.prompts.registry import get_prompts
from core.workflow.state import PodBookState, WorkflowStage

EDITOR_PREFACE_MODEL = "gpt-5.2"

# ============ Prompts ============

GENERATE_SYSTEM = (
    "你是一位资深非虚构作者。写东西像聊天，自然、松弛，"
    "不端着也不赶着。永远站在场景里面写，不跳到外面评论。"
)

SEARCH_SYSTEM = "你是一个搜索助手。请根据搜索结果，用简洁的要点格式总结与查询相关的关键事实和数据。只输出有据可查的事实，不要编造。如果搜索结果有限，就说「未找到相关信息」。"

SEARCH_PROMPT = "请搜索以下内容并总结关键事实：\n\n{query}"

GENERATE_PROMPT = """你正在为一本由播客访谈整理而成的书撰写「编者序」（400-700 字，3-4 段）。

# 素材

## 播客信息
{metadata_block}

## 核心主题
{core_theme}

## 书稿全文
{full_text}

## 外部搜索结果
{search_context}

# 写什么

编者序只需要讲清楚一件事：**在一个正在发生巨变的行业里，一个人做了一个什么样的选择，这个选择为什么值得关注**。

行业变局是背景，个人选择是前景，两者共同构成一幅画面。不要把它们拆成两条线来回切换，而是让它们在同一段叙事里互相支撑——讲行业的时候，读者能感觉到这跟这个人有关；讲这个人的时候，读者能感觉到背后有一个更大的东西在推动。

# 怎么写

**逻辑**：全文只有一条线，从头到尾向前推进。每一段都在回答上一段留下的问题，或者把上一段的判断往前推一步。读者读完任何一段，都能自然地想知道"然后呢"。不要在段落之间跳话题。

**搜索结果**：搜索到的行业数据和趋势是你的核心素材，至少用上 2-3 个。但不要集中在一段里甩完，也不要当装饰——每个数据出现的地方，都应该在推进你的论点。

**个人故事**：讲选择和判断，不要列履历。

**语感**：自然，像聊天。句子长短跟意思走。一句话说一件事。段落之间靠意思推进，不靠连接词。

**收尾**：用一个从前文逻辑中自然长出来的问题或悬念收束。

**禁止**：
- 元叙事（零容忍）："这场对谈""这本书讲的是""读到后面""翻到正文""你会看到"、第一人称"我"。判断标准：删掉这句话不丢信息，就是元叙事。
- AI 套话："值得注意的是""综上所述""让我们""不仅…而且…""在…的浪潮下"
- "标签+冒号"句式："问题在于：""核心是：""答案是："
- 堆砌：一段里不要连甩多个数据，不要写长并列句
- 不要搬运播客原话，不要加引用标记或 URL

# 思维链（内部推理，不输出）

1. 用一句话写出主线：在___的行业背景下，___做了___的选择，因为___。
2. 围绕这条主线，从素材里挑最有力的 2-3 个行业事实和 1-2 个个人选择。
3. 安排顺序：每一段都在把主线往前推一步。段尾自然引出下一段的内容。
4. 检查：读完全文，是一个连贯的故事还是几段拼在一起？有没有元叙事？

# 输出

直接输出编者序正文（纯文本，段落之间用空行分隔）。不要输出标题、不要输出思考过程。"""

REVIEW_SYSTEM = "你是出版社终审编辑。只做必要的最小改动，保持原文风格。"

REVIEW_PROMPT = """对照播客正文，审核这篇编者序初稿。

# 编者序初稿
{draft}

# 播客正文全文
{full_text}

# 审核清单

1. **事实**：人物背景、公司信息、数据是否与播客内容矛盾？改掉错误。
2. **元叙事（零容忍）**：逐句扫描，是否有"站在文本外面说话"的句子（"这场对谈""这本书讲的是""读到后面""翻到正文""你会看到"、第一人称"我"）？有就删或改。
3. **AI 痕迹**：套话（"值得注意的是""综上所述"）、"标签+冒号"句式（"问题在于：""核心是："）？改成正常陈述句。
4. **连贯性**：全文有没有一条清晰的逻辑主线？段与段之间的推进是否自然？如果逻辑断裂，做最小调整让它连上。
5. **最小改动**：没问题的地方不要动。

直接输出最终版编者序（纯文本，段落间空行分隔）。不要输出审核过程。"""


# ============ Generator ============

class EditorPrefaceGenerator:
    """编者序生成器：搜索增强 + 自审两步流程。"""

    def __init__(self, state: PodBookState):
        self.llm = get_llm_service()
        self.state = state
        content_type = state.get("content_type", "business")
        narrative_type = state.get("narrative_type", "opinion")
        voice_format = state.get("voice_format", "dialogue")
        try:
            self._prompts = get_prompts(
                "editor_preface", content_type, narrative_type, voice_format
            )
        except KeyError:
            self._prompts = {}

    def _build_metadata_block(self) -> str:
        parts = []
        podcast_name = self.state.get("podcast_name", "")
        if podcast_name:
            parts.append(f"- 节目：{podcast_name}")
        host = self.state.get("host_name", "")
        if host:
            parts.append(f"- 主持人：{host}")
        guests = self.state.get("guest_names") or []
        if guests:
            parts.append(f"- 嘉宾：{'、'.join(guests)}")
        companies = self.state.get("company_names") or []
        if companies:
            parts.append(f"- 相关公司/组织：{'、'.join(companies)}")
        desc = self.state.get("podcast_intro", "") or self.state.get("description", "")
        if desc:
            parts.append(f"- 节目简介：{desc[:500]}")
        return "\n".join(parts) if parts else "（无元数据）"

    def _build_full_text(self) -> str:
        """构建完整的书稿正文，供编者序生成参考。"""
        content = (
            self.state.get("annotated_content")
            or self.state.get("composed_content")
            or {}
        )
        chapters = content.get("chapters", [])
        if not chapters:
            return "（无章节内容）"
        parts = []
        for i, ch in enumerate(chapters, 1):
            title = ch.get("title", f"章节 {i}")
            text = ch.get("content", "")
            parts.append(f"## {i}. {title}\n\n{text}")
        return "\n\n---\n\n".join(parts)

    def _build_content_for_review(self) -> str:
        """为自审步骤构建完整正文（用于事实核查）。"""
        return self._build_full_text()

    def _build_search_queries(self) -> list[str]:
        """从元数据和内容中构建多维度搜索查询。"""
        content = (
            self.state.get("annotated_content")
            or self.state.get("composed_content")
            or {}
        )
        guests = self.state.get("guest_names") or []
        companies = self.state.get("company_names") or []
        core_theme = content.get("core_theme", "")
        keywords = content.get("theme_keywords") or []
        publish_date = self.state.get("publish_date", "")
        year = publish_date[:4] if publish_date else "2025"

        queries = []

        # 查询 1：嘉宾背景
        if guests:
            guest_str = " ".join(guests)
            company_hint = f" {companies[0]}" if companies else ""
            queries.append(f"{guest_str}{company_hint} 职业背景 创业经历")

        # 查询 2：核心话题的行业趋势
        if core_theme or keywords:
            topic = core_theme or " ".join(keywords[:3])
            queries.append(f"{topic} {year}年 行业趋势 市场动态")

        # 查询 3：更具体的竞争格局/事件
        if keywords and companies:
            queries.append(
                f"{' '.join(keywords[:2])} {' '.join(companies[:2])} 竞争格局 最新动态 {year}"
            )
        elif keywords:
            queries.append(f"{' '.join(keywords[:3])} 最新进展 重要事件 {year}")

        return queries

    async def _execute_searches(self, queries: list[str]) -> list[dict]:
        """并发执行多条搜索查询，返回 [{query, result}]。"""
        import asyncio

        search_sys = self._prompts.get("search_system", SEARCH_SYSTEM)
        search_usr = self._prompts.get("search_user", SEARCH_PROMPT)

        async def _search_one(query: str) -> dict:
            print(f"[editor_preface]   搜索: {query}")
            try:
                result = await self.llm.generate_with_search(
                    prompt=search_usr.format(query=query),
                    system_prompt=search_sys,
                    model=EDITOR_PREFACE_MODEL,
                    max_tokens=65536,
                    timeout=120,
                    label=f"搜索: {query[:30]}",
                )
                sub_queries = getattr(self.llm, "_last_search_queries", [])
                return {"query": query, "actual_queries": sub_queries, "result": result.strip()}
            except Exception as e:
                print(f"[editor_preface]   搜索失败: {e}")
                return {"query": query, "actual_queries": [], "result": "未找到相关信息"}

        results = await asyncio.gather(*[_search_one(q) for q in queries])
        return list(results)

    def _format_search_results(self, results: list[dict]) -> str:
        parts = []
        for r in results:
            if "未找到" in r["result"]:
                continue
            parts.append(f"### 搜索：{r['query']}\n\n{r['result']}")
        return "\n\n---\n\n".join(parts) if parts else "（搜索未返回有效结果，请基于播客内容本身撰写）"

    async def _step1_generate(self) -> str:
        """Step 1：先执行多条搜索，再用搜索结果 + 全文生成编者序初稿。"""
        # Step 1a: 构建搜索查询
        queries = self._build_search_queries()
        self.search_queries = queries
        print(f"[editor_preface] Step 1a: 构建了 {len(queries)} 条搜索查询")

        # Step 1b: 并发执行搜索
        print(f"[editor_preface] Step 1b: 执行搜索...")
        search_results = await self._execute_searches(queries)
        self.search_results = search_results
        for r in search_results:
            preview = r["result"][:100].replace("\n", " ")
            print(f"[editor_preface]   结果: {preview}...")

        # Step 1c: 用搜索结果 + 全文生成编者序
        content = (
            self.state.get("annotated_content")
            or self.state.get("composed_content")
            or {}
        )
        gen_system = self._prompts.get("generate_system", GENERATE_SYSTEM)
        gen_user = self._prompts.get("generate_user", GENERATE_PROMPT)
        prompt = gen_user.format(
            metadata_block=self._build_metadata_block(),
            core_theme=content.get("core_theme", "（未知）"),
            full_text=self._build_full_text(),
            search_context=self._format_search_results(search_results),
        )

        print(f"[editor_preface] Step 1c: 生成编者序, prompt {len(prompt)} 字符")

        draft = await self.llm.generate(
            prompt=prompt,
            system_prompt=gen_system,
            model=EDITOR_PREFACE_MODEL,
            max_tokens=65536,
            timeout=300,
            label="生成编者序初稿",
        )
        print(f"[editor_preface] Step 1 完成: {len(draft)} 字")
        return draft.strip()

    async def _step2_review(self, draft: str) -> str:
        """Step 2：自审修正，检查事实准确性和语气。"""
        rev_system = self._prompts.get("review_system", REVIEW_SYSTEM)
        rev_user = self._prompts.get("review_user", REVIEW_PROMPT)
        prompt = rev_user.format(
            draft=draft,
            full_text=self._build_content_for_review(),
        )

        print(f"[editor_preface] Step 2: 自审修正, prompt {len(prompt)} 字符")

        result = await self.llm.generate(
            prompt=prompt,
            system_prompt=rev_system,
            temperature=0.3,
            model=EDITOR_PREFACE_MODEL,
            max_tokens=65536,
            timeout=180,
            label="编者序自审修正",
        )
        print(f"[editor_preface] Step 2 完成: {len(result)} 字")
        return result.strip()

    async def run(self) -> str:
        """执行完整的编者序生成流程，返回编者序正文。"""
        start = time.time()

        draft = await self._step1_generate()
        if not draft:
            print("[editor_preface] Step 1 未生成内容，使用 fallback")
            return ""

        final = await self._step2_review(draft)
        elapsed = time.time() - start
        print(f"[editor_preface] 全部完成, 耗时 {elapsed:.1f}s, "
              f"最终 {len(final)} 字")
        return final or draft


# ============ Node Entry Point ============

async def editor_preface_node(state: PodBookState) -> Dict[str, Any]:
    """编者序生成节点。annotation 之后、typeset 之前执行。"""
    print(f"[editor_preface] 开始处理任务: {state['task_id']}")

    user_preface = state.get("user_editor_preface", "")
    if user_preface and user_preface.strip():
        print(f"[editor_preface] 使用用户自定义编者序 ({len(user_preface)} 字)")
        return {
            "editor_preface_content": user_preface.strip(),
            "current_stage": WorkflowStage.TYPESET.value,
        }

    fallback = ""
    content = state.get("annotated_content") or state.get("composed_content") or {}
    preamble = content.get("preamble", {})
    fallback = preamble.get("lead_paragraph", "")

    try:
        generator = EditorPrefaceGenerator(state)
        result = await generator.run()
        if result:
            return {
                "editor_preface_content": result,
                "current_stage": WorkflowStage.TYPESET.value,
            }
    except Exception as e:
        print(f"[editor_preface] 生成失败: {e}，使用 fallback")

    print(f"[editor_preface] 使用 compose 阶段的 lead_paragraph 作为 fallback")
    return {
        "editor_preface_content": fallback,
        "current_stage": WorkflowStage.TYPESET.value,
    }
