"""
节点：正文插图（Illustration）

只使用公开图片搜索结果为具体实体配图，不再生成 AI 信息图。

流程：
  Phase 1: LLM 逐章分析 → 识别适合搜图的实体 (search)
  Phase 2: Serper Image Search + 下载多候选
  Phase 3: VLM 审核搜索图片（逐候选审核，取第一张通过的）
  Phase 4: 合并输出

输入：composed_content
输出：illustrations
"""

import asyncio
import base64
import re
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

import httpx

from core.config import settings
from core.workflow.state import PodBookState, WorkflowStage
from core.services.llm_service import get_llm_service
from core.workflow.prompts.registry import get_prompts


# ================================================================
# Phase 1: 逐章识别可迁移方法论 + 选定图表形式
# ================================================================

CONCEPT_SYSTEM_PROMPT = """\
你是一位非虚构类图书的内容策划人。你的工作是从书稿中找到嘉宾分享的、读者能直接用在自己工作生活中的方法论，\
然后把它写成一张内容完整、一眼就能读懂的信息图。

关键原则：
- 严格忠于原文：所有内容必须来自嘉宾的原话，禁止自行归纳、推断或添加嘉宾没有明确表达的观点
- 每张图必须独立可读——读者不看正文，只看图就能理解完整的方法论
- 方法论的每个要点必须是完整的句子或动宾短语，不能是碎片化的关键词
- 图的形式服务于内容：内容适合列表就做列表卡片，适合对比就做对比图，不要硬套流程图"""

CONCEPT_PROMPT = """\
请分析以下章节，从中找出嘉宾明确分享的、有启发性且可迁移的方法论。

【章节标题】{chapter_title}
【章节内容（段落编号标注）】
{numbered_paragraphs}

---

## 什么是好的方法论（必须同时满足）

1. **可迁移**：读者换一个行业/场景也能用这个方法（如"判断方向的三个标准"可以迁移，"我怎么进的某公司"不能迁移）
2. **有结构**：嘉宾在正文中明确说了 2-5 个要点/步骤/标准。必须是嘉宾原话中显式列举的，不是你从散落的表述中自行归纳、拼凑或推断的
3. **有启发**：不是"要努力""要坚持"这类常识

## 排除（直接跳过）

- 个人经历叙事（"我住校""我在大学""我去了某公司"）
- 行业事件描述（"共享单车大战""补贴大战"）
- 没有具体要点的单一感悟
- 嘉宾没有明确表达、由你主观推断出的"方法论"

## content_items：方法论的完整要点

这是最重要的字段。每个要点必须**直接来自嘉宾在正文中的原话**，用完整的中文句子或动宾短语（8-20字）表达。严禁自行发挥、添加嘉宾没说过的要点，也不要揣测嘉宾"想表达但没说出来"的意思。如果嘉宾只说了 2 个要点，就只写 2 个，不要凑数。

正确示例：
- "先确认核心竞争力是否由产品驱动"
- "评估自己能否影响业务的关键胜负手"
- "判断自身能力是否与该方向匹配"

错误示例（碎片化关键词，禁止）：
- "产品驱动"
- "关键胜负"
- "能力匹配"

错误示例（自行添加，禁止）：
- 嘉宾只说了 A 和 B 两个标准，你自己补了一个 C → 禁止

## visual_form：选择最合适的图表形式

| visual_form | 什么时候用 |
|---|---|
| list_card | 3-5 个并列的要点/标准/原则，没有明确的先后或因果关系 → 做成一张带编号的列表卡片 |
| step_card | 2-5 个有先后顺序的步骤 → 做成一张带编号和箭头的步骤卡片 |
| comparison | A vs B 的明确对比（如"该做 vs 不该做""好环境 vs 差环境"） |
| flowchart | 有因果链条或决策分支（如"如果X则Y，否则Z"） |
| cycle | 闭环：A→B→C→回到A |
| mindmap | 一个核心概念 + 多个维度的展开 |

选择原则：
- **内容要点 ≥ 3 个且是并列关系** → 优先选 list_card
- **有明确的先后顺序** → 选 step_card
- **只有在内容有明确的因果/决策逻辑时才选 flowchart**
- 不确定选什么 → 选 list_card（最安全，内容最完整）

## image_prompt 撰写规则

- **用中文撰写**，100-200 字
- 把 content_items 中的每一条完整句子写入 prompt
- 描述你想要的整体视觉效果和布局（不要只说"列表"或"流程图"，要描述画面）
- 鼓励丰富的视觉设计：色块区分、卡片式布局、层次感、适当的图标点缀
- 每条要点在图中要完整显示、清晰可读
- 不同的方法论应该有不同的视觉风格，避免千篇一律

## 数量控制

- 本章最多 {max_per_chapter} 个，没有合适的就输出空列表

---

【输出格式】严格 JSON：
{{
    "concept_items": [
        {{
            "concept_name": "方法论标题（如'判断一个业务方向是否值得投入的三个标准'）",
            "content_items": [
                "要点一的完整句子（8-20字）",
                "要点二的完整句子（8-20字）",
                "..."
            ],
            "visual_form": "list_card|step_card|comparison|flowchart|cycle|mindmap",
            "image_prompt": "中文 prompt（100-200字，包含完整的中文句子和布局说明）",
            "after_paragraph": 段落编号
        }}
    ]
}}"""


def _number_paragraphs(content: str) -> str:
    paragraphs = [p.strip() for p in content.split("\n\n") if p.strip()]
    return "\n\n".join(f"[P{i}] {p}" for i, p in enumerate(paragraphs))


async def _phase1_concepts_for_chapter(
    llm,
    chapter: Dict,
    chapter_index: int,
    prompts: Optional[Dict] = None,
) -> List[Dict]:
    """对单个章节识别概念配图。"""
    title = chapter.get("title", f"Chapter {chapter_index+1}")
    content = chapter.get("content", "")
    if len(content) < 300:
        return []

    concept_sys = prompts.get("concept_system", CONCEPT_SYSTEM_PROMPT) if prompts else CONCEPT_SYSTEM_PROMPT
    concept_usr = prompts.get("concept_user", CONCEPT_PROMPT) if prompts else CONCEPT_PROMPT

    prompt = concept_usr.format(
        chapter_title=title,
        numbered_paragraphs=_number_paragraphs(content),
        max_per_chapter=settings.ILLUSTRATION_MAX_PER_CHAPTER,
    )

    try:
        result = await llm.generate_json(
            prompt=prompt,
            system_prompt=concept_sys,
            model=settings.LLM_MODEL,
            temperature=0.3,
            thinking_budget=0,
            timeout=90,
        )
        items = result.get("concept_items", [])
        for item in items:
            item["chapter_title"] = title
        return items
    except Exception as e:
        print(f"[illustration] Phase 1 章节「{title}」分析失败: {e}")
        return []


async def _phase1_concepts(
    llm,
    composed: Dict,
    prompts: Optional[Dict] = None,
) -> List[Dict]:
    """逐章并行识别概念配图。"""
    chapters = composed.get("chapters", [])
    sem = asyncio.Semaphore(4)

    async def _run(ch, idx):
        async with sem:
            return await _phase1_concepts_for_chapter(llm, ch, idx, prompts)

    results = await asyncio.gather(*[_run(ch, i) for i, ch in enumerate(chapters)])

    all_items = []
    for items in results:
        all_items.extend(items)

    if len(all_items) > settings.ILLUSTRATION_MAX_TOTAL:
        all_items = all_items[:settings.ILLUSTRATION_MAX_TOTAL]

    print(f"[illustration] Phase 1: 识别 {len(all_items)} 个概念配图")
    for i, item in enumerate(all_items):
        form = item.get("visual_form", "?")
        name = item.get("concept_name", "")
        ch = item.get("chapter_title", "")
        items = item.get("content_items", [])
        print(f"  [{i}] ({form}) {name[:60]} → {ch}")
        for ci in items[:5]:
            print(f"      · {ci}")
    return all_items


# ================================================================
# Phase 1b: 逐章识别适合搜图的实体（组织/人物/事件/产品/地标）
# ================================================================

SEARCH_SYSTEM_PROMPT = """\
你是一位非虚构类图书的视觉编辑，负责为访谈类书稿挑选适合用真实照片配图的内容。\
目标是帮助读者直观理解正文涉及的公司、产品、人物和事件，让阅读体验更丰富。

配图只来自公开图片搜索结果，不做 AI 生图。你只负责"适合用真实照片呈现的具体事物"。
抽象的方法论、框架和观点不要选；没有合适实体时宁可不配图。"""

SEARCH_PROMPT = """\
请分析以下章节，找出正文中值得用真实照片配图的具体实体。

【章节标题】{chapter_title}
【章节内容（段落编号标注）】
{numbered_paragraphs}

---

## 选择标准

一个实体值得配图，需要同时满足：
1. 正文对它有实质性讨论（不是一笔带过），读者会想知道"它长什么样"
2. 它与本书的商业/科技/行业主题直接相关
3. 网络上能搜索到正式、专业的照片（产品界面、官方活动照、建筑照等）

## 实体类型

| entity_type | 说明 |
|---|---|
| organization | 公司、机构、品牌（总部大楼、办公环境、logo 等） |
| product | 科技/商业产品（产品界面、官方宣传图、使用场景） |
| event | 发布会、行业大会、标志性商业事件 |
| person | 行业知名人物（正式场合的公开照片） |
| place | 与商业叙事相关的标志性地点 |

## 排除

以下内容直接跳过，不要选：
- 日常消费品、食品、生活用品（如"榨菜""方便面"）
- 游戏、娱乐、影视内容（如"Dota""魔兽争霸"）——即使正文提到，也不适合出现在商业图书中
- 访谈主持人的个人照片
- 嘉宾的个人生活细节（母校、家乡等），除非与商业叙事直接相关
- 抽象概念、方法论、框架、商业判断、个人感悟
- 过于宽泛的描述（如"互联网行业""创业公司"），无法搜到有意义的照片

## search_query 撰写

- 用实体的官方名称（优先英文）+ 场景限定词，确保搜索结果正式、专业
- 好的例子："ByteDance headquarters Shenzhen" "CapCut video editor interface" "张一鸣 公开演讲"
- 避免的例子："某公司" "创业者" 等模糊词

## 数量控制

- 当前内容块最多 {max_per_chapter} 个；全书最终只保留少量最佳配图，宁缺毋滥
- 没有合适的就输出空列表

---

【输出格式】严格 JSON：
{{
    "search_items": [
        {{
            "entity_name": "实体名称",
            "entity_type": "organization|person|event|product|place",
            "search_query": "搜索关键词",
            "after_paragraph": 段落编号,
            "caption": "图片说明文字（中文，15-30字）"
        }}
    ]
}}"""


async def _phase1_search_for_chapter(
    llm,
    chapter: Dict,
    chapter_index: int,
    prompts: Optional[Dict] = None,
) -> List[Dict]:
    """对单个章节识别适合搜图的实体。"""
    title = chapter.get("title", f"Chapter {chapter_index+1}")
    content = chapter.get("content", "")
    if len(content) < 300:
        return []

    search_sys = prompts.get("search_system", SEARCH_SYSTEM_PROMPT) if prompts else SEARCH_SYSTEM_PROMPT
    search_usr = prompts.get("search_user", SEARCH_PROMPT) if prompts else SEARCH_PROMPT

    prompt = search_usr.format(
        chapter_title=title,
        numbered_paragraphs=_number_paragraphs(content),
        max_per_chapter=settings.ILLUSTRATION_SEARCH_MAX_PER_CHAPTER,
    )

    try:
        result = await llm.generate_json(
            prompt=prompt,
            system_prompt=search_sys,
            model=settings.LLM_MODEL,
            temperature=0.3,
            thinking_budget=0,
            timeout=90,
        )
        items = result.get("search_items", [])
        for item in items:
            item["chapter_title"] = title
        return items
    except Exception as e:
        print(f"[illustration] Phase 1b 章节「{title}」搜图分析失败: {e}")
        return []


SEARCH_SELECT_PROMPT = """\
你是一位图书编辑，正在为一本访谈类书籍做最终的配图规划。
下面是各章节分别识别出的候选配图实体，现在需要你从全书视角选出最终保留的 {max_count} 个。

【书名】{title}
【核心主题】{core_theme}
【关键词】{keywords}

【候选实体列表】
{candidate_list}

---

## 筛选原则

1. 与全书核心主题关联最紧密的实体优先
2. 嘉宾自身创办/负责的公司和产品是全书主角，务必保留
3. 配图应覆盖全书主要章节，不要集中在前几章或后几章
4. 同类实体（如多个"大厂总部"）保留最有代表性的一个即可
5. 与主题关联弱、或过于边缘的实体优先淘汰

---

请输出你选中的实体编号（从 0 开始），严格 JSON 格式：
{{"selected": [0, 3, 5, ...]}}"""


async def _select_top_entities(
    llm,
    candidates: List[Dict],
    composed: Dict,
    max_count: int,
    prompts: Optional[Dict] = None,
) -> List[Dict]:
    """用 LLM 从全局视角筛选最有价值的搜图实体。"""
    select_usr = prompts.get("search_select_user", SEARCH_SELECT_PROMPT) if prompts else SEARCH_SELECT_PROMPT

    lines = []
    for i, item in enumerate(candidates):
        lines.append(
            f"[{i}] {item.get('entity_name', '')} "
            f"({item.get('entity_type', '')}) "
            f"— 章节「{item.get('chapter_title', '')}」"
            f"— {item.get('caption', '')}"
        )

    prompt = select_usr.format(
        max_count=max_count,
        title=composed.get("title", ""),
        core_theme=composed.get("core_theme", ""),
        keywords=", ".join(composed.get("theme_keywords", [])),
        candidate_list="\n".join(lines),
    )

    try:
        result = await llm.generate_json(
            prompt=prompt,
            model=settings.LLM_MODEL,
            temperature=0.2,
            thinking_budget=0,
            timeout=60,
        )
        selected_indices = result.get("selected", [])
        selected = [
            candidates[i] for i in selected_indices
            if isinstance(i, int) and 0 <= i < len(candidates)
        ]
        if not selected:
            return candidates[:max_count]

        dropped = [c for i, c in enumerate(candidates) if i not in selected_indices]
        for d in dropped:
            print(f"[illustration] Phase 1b: LLM 淘汰「{d.get('entity_name', '')}」"
                  f"(章节: {d.get('chapter_title', '')})")
        return selected
    except Exception as e:
        print(f"[illustration] Phase 1b: LLM 筛选失败，回退到前 {max_count} 个: {e}")
        return candidates[:max_count]


async def _phase1_search(
    llm,
    composed: Dict,
    prompts: Optional[Dict] = None,
) -> List[Dict]:
    """逐章并行识别适合搜图的实体。"""
    if not settings.SERPER_API_KEY:
        print("[illustration] Phase 1b: SERPER_API_KEY 未配置，跳过搜图")
        return []

    chapters = composed.get("chapters", [])
    sem = asyncio.Semaphore(4)

    async def _run(ch, idx):
        async with sem:
            return await _phase1_search_for_chapter(llm, ch, idx, prompts)

    results = await asyncio.gather(*[_run(ch, i) for i, ch in enumerate(chapters)])

    all_items = []
    for items in results:
        all_items.extend(items)

    # 按 entity_name 去重：同名实体只保留首次出现（即最早章节）
    seen_names: set = set()
    deduped: List[Dict] = []
    for item in all_items:
        name = item.get("entity_name", "").strip()
        norm = re.sub(r"[（(].*?[)）]", "", name).replace(" ", "").lower()
        if norm and norm not in seen_names:
            seen_names.add(norm)
            deduped.append(item)
        elif norm:
            print(f"[illustration] Phase 1b: 去重跳过「{name}」(章节: {item.get('chapter_title', '')})")
    all_items = deduped

    max_search = settings.ILLUSTRATION_SEARCH_MAX_TOTAL
    if len(all_items) > max_search:
        print(f"[illustration] Phase 1b: 去重后 {len(all_items)} 个，超过上限 {max_search}，启动 LLM 筛选")
        all_items = await _select_top_entities(llm, all_items, composed, max_search, prompts)

    print(f"[illustration] Phase 1b: 最终 {len(all_items)} 个搜图实体")
    for i, item in enumerate(all_items):
        etype = item.get("entity_type", "?")
        name = item.get("entity_name", "")
        query = item.get("search_query", "")
        ch = item.get("chapter_title", "")
        print(f"  [{i}] ({etype}) {name} → \"{query}\" [{ch}]")
    return all_items


# ================================================================
# Phase 2a: AI 生图（按 visual_form 适配 prompt 前缀）
# ================================================================

IMAGE_STYLE_PREFIX = (
    "为非虚构类图书设计一张精美的信息图插图。"
    "整体风格：高级感、专业、适合印刷出版。"
    "配色基调为暖色（棕色、金色、米白），可搭配适当的点缀色增加层次感。"
    "所有文字必须是简体中文，清晰可读，使用完整短语。"
    "鼓励使用丰富的视觉层次：不同大小的字号、色块区分、圆角卡片、细线装饰等。"
    "画面要有设计感和呼吸感，避免机械排列。"
)


_MAGIC_SIGNATURES = {
    b"\x89PNG": ".png",
    b"RIFF": ".webp",
    b"GIF8": ".gif",
}


def _detect_and_fix_extension(save_path: Path, data: bytes) -> Path:
    """检测图片实际格式，若与扩展名不匹配则重命名。"""
    if len(data) < 4:
        return save_path
    head = data[:4]
    if head[:2] == b"\xff\xd8":
        actual_ext = ".jpg"
    else:
        actual_ext = _MAGIC_SIGNATURES.get(head, save_path.suffix)
    if actual_ext != save_path.suffix:
        new_path = save_path.with_suffix(actual_ext)
        save_path.rename(new_path)
        print(f"[illustration] 格式修正: {save_path.name} → {new_path.name}")
        return new_path
    return save_path


async def _download_image(url: str, save_path: Path, timeout: int = 30) -> Optional[Path]:
    """下载图片到本地，自动修正扩展名与实际格式不匹配的情况。
    
    返回实际保存路径（可能与 save_path 不同），失败返回 None。
    """
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            content_type = resp.headers.get("content-type", "")
            if "image" not in content_type and not url.lower().endswith((".jpg", ".jpeg", ".png", ".webp")):
                print(f"[illustration] 非图片内容: {content_type} ({url[:60]})")
                return None
            save_path.parent.mkdir(parents=True, exist_ok=True)
            save_path.write_bytes(resp.content)
            final_path = _detect_and_fix_extension(save_path, resp.content)
            print(f"[illustration] 下载成功: {final_path.name} ({len(resp.content) / 1024:.0f} KB)")
            return final_path
    except Exception as e:
        print(f"[illustration] 下载失败: {url[:60]} — {e}")
        return None


def _resize_image(path: Path, max_width: int = 1200, max_height: int = 900, quality: int = 85):
    """将图片缩放至合适尺寸并转为 JPEG（等比缩放，不裁切）。"""
    try:
        from PIL import Image
        with Image.open(path) as img:
            if img.mode in ("RGBA", "P"):
                img = img.convert("RGB")
            w, h = img.width, img.height
            ratio = min(max_width / w, max_height / h, 1.0)
            if ratio < 1.0:
                new_size = (int(w * ratio), int(h * ratio))
                img = img.resize(new_size, Image.LANCZOS)
            jpeg_path = path.with_suffix(".jpg")
            img.save(jpeg_path, "JPEG", quality=quality)
            if jpeg_path != path:
                path.unlink(missing_ok=True)
            return jpeg_path
    except ImportError:
        print("[illustration] Pillow 未安装，跳过图片缩放")
        return path
    except Exception as e:
        print(f"[illustration] 图片缩放失败: {e}")
        return path


def _image_to_base64(path: Path) -> str:
    data = path.read_bytes()
    return base64.b64encode(data).decode("utf-8")


async def _generate_one(
    llm,
    item: Dict,
    output_dir: Path,
    idx: int,
    feedback: str = "",
) -> Optional[Dict]:
    """为单个概念生成 AI 图片并下载。"""
    base_prompt = item.get("image_prompt", "")
    if not base_prompt:
        return None

    full_prompt = IMAGE_STYLE_PREFIX + base_prompt
    if feedback:
        full_prompt += f"\n\nIMPORTANT revision based on feedback: {feedback}"

    try:
        image_url = await llm.generate_image(
            prompt=full_prompt,
            label=f"AI生图: {item.get('concept_name', '')[:20]}",
        )
        filename = f"ill_{idx}.jpg"
        save_path = output_dir / filename
        downloaded = await _download_image(image_url, save_path)
        if not downloaded:
            return None

        final_path = _resize_image(downloaded)
        return {
            **item,
            "filename": f"illustrations/{final_path.name}",
            "local_path": str(final_path),
            "type": "concept",
            "source": "ai_generated",
        }
    except Exception as e:
        print(f"[illustration] AI 生图失败 ({item.get('concept_name', '')}): {e}")
        return None


async def _phase2_generate(
    llm,
    concept_items: List[Dict],
    output_dir: Path,
) -> List[Dict]:
    """批量 AI 生图，返回候选列表。"""
    candidates = []
    sem = asyncio.Semaphore(2)

    async def _process(idx: int, item: Dict):
        async with sem:
            return await _generate_one(llm, item, output_dir, idx)

    results = await asyncio.gather(
        *[_process(i, item) for i, item in enumerate(concept_items)]
    )

    for r in results:
        if r:
            candidates.append(r)

    print(f"[illustration] Phase 2a: {len(candidates)}/{len(concept_items)} 个图片生成成功")
    return candidates


# ================================================================
# Phase 2b: Serper.dev 搜图 (Google Images) + 下载多候选
# ================================================================

SERPER_IMAGES_URL = "https://google.serper.dev/images"


async def _serper_image_search(query: str, count: int = 5) -> List[Dict]:
    """调用 Serper.dev Google Images API，返回候选图片信息列表。"""
    api_key = settings.SERPER_API_KEY
    if not api_key:
        return []

    payload = {
        "q": query,
        "num": count,
        "gl": "cn",
        "hl": "zh-cn",
    }
    headers = {
        "X-API-KEY": api_key,
        "Content-Type": "application/json",
    }

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                SERPER_IMAGES_URL,
                json=payload,
                headers=headers,
            )
            resp.raise_for_status()
            data = resp.json()

        results = []
        for img in data.get("images", []):
            results.append({
                "content_url": img.get("imageUrl", ""),
                "thumbnail_url": img.get("thumbnailUrl", ""),
                "width": img.get("imageWidth", 0),
                "height": img.get("imageHeight", 0),
                "host_page": img.get("link", ""),
            })
        print(f"[illustration] Serper 搜图「{query[:30]}」: {len(results)} 条结果")
        return results
    except Exception as e:
        print(f"[illustration] Serper 搜图失败「{query[:30]}」: {e}")
        return []


async def _search_and_download(
    item: Dict,
    output_dir: Path,
    idx: int,
) -> List[Dict]:
    """为单个实体搜图并下载多张候选（供 VLM 逐一审核）。"""
    query = item.get("search_query", "")
    if not query:
        return []

    search_results = await _serper_image_search(
        query, count=settings.ILLUSTRATION_SEARCH_MAX_RESULTS,
    )
    if not search_results:
        return []

    candidates = []
    skipped_small = 0
    for j, br in enumerate(search_results):
        url = br.get("content_url", "")
        if not url:
            continue
        w = br.get("width", 0)
        h = br.get("height", 0)
        if w and h and (w < settings.ILLUSTRATION_SEARCH_MIN_WIDTH
                        or h < settings.ILLUSTRATION_SEARCH_MIN_HEIGHT):
            skipped_small += 1
            continue
        filename = f"search_{idx}_{j}.jpg"
        save_path = output_dir / filename
        downloaded = await _download_image(url, save_path)
        if not downloaded:
            continue
        final_path = _resize_image(downloaded)
        candidates.append({
            **item,
            "filename": f"illustrations/{final_path.name}",
            "local_path": str(final_path),
            "type": "search",
            "source": "web_searched",
            "search_url": url,
            "_candidate_index": j,
        })

    print(f"[illustration] 搜图下载「{item.get('entity_name', '')}」: "
          f"{len(candidates)} 张下载成功, {skipped_small} 张因分辨率不足跳过")
    return candidates


async def _phase2_search(
    search_items: List[Dict],
    output_dir: Path,
) -> List[List[Dict]]:
    """批量搜图，每个 item 返回一组候选列表（用于 VLM 逐一审核）。"""
    if not search_items:
        return []

    sem = asyncio.Semaphore(3)

    async def _process(idx: int, item: Dict):
        async with sem:
            return await _search_and_download(item, output_dir, idx)

    results = await asyncio.gather(
        *[_process(i, item) for i, item in enumerate(search_items)]
    )

    non_empty = sum(1 for r in results if r)
    print(f"[illustration] Phase 2b: {non_empty}/{len(search_items)} 个实体搜图有候选")
    return list(results)


# ================================================================
# Phase 3: VLM 审核（生图 + 搜图 分离标准）
# ================================================================

def _get_review_context(candidate: Dict, composed: Dict) -> str:
    """从正文中提取候选图片周围的上下文段落。"""
    chapter_title = candidate.get("chapter_title", "")
    after_para = candidate.get("after_paragraph")
    if after_para is not None:
        try:
            after_para = int(after_para)
        except (ValueError, TypeError):
            after_para = None
    for ch in composed.get("chapters", []):
        if ch.get("title") == chapter_title:
            paragraphs = [p.strip() for p in ch.get("content", "").split("\n\n") if p.strip()]
            if after_para is not None and 0 <= after_para < len(paragraphs):
                start = max(0, after_para - 1)
                end = min(len(paragraphs), after_para + 2)
                return "\n".join(paragraphs[start:end])
            return "\n".join(paragraphs[:3])
    return ""


async def _review_single_concept(
    llm,
    candidate: Dict,
    composed: Dict,
) -> Dict:
    """VLM 审核单张 AI 生图（concept 标准）。"""
    local_path = Path(candidate["local_path"])
    if not local_path.exists():
        return {**candidate, "review_approved": False, "review_score": 0, "review_reason": "文件不存在"}

    b64 = _image_to_base64(local_path)
    context = _get_review_context(candidate, composed)
    subject = candidate.get("concept_name", "")
    form = candidate.get("visual_form", "flowchart")

    result = await llm.review_image(
        image_base64=b64,
        context=context,
        expected_subject=subject,
        image_type="concept",
        visual_form=form,
    )

    return {
        **candidate,
        "review_approved": result.get("approved", False),
        "review_score": result.get("total_score", 0),
        "review_reason": result.get("reason", ""),
    }


async def _review_single_search(
    llm,
    candidate: Dict,
    composed: Dict,
) -> Dict:
    """VLM 审核单张搜索图片（search 标准）。"""
    local_path = Path(candidate["local_path"])
    if not local_path.exists():
        return {**candidate, "review_approved": False, "review_score": 0, "review_reason": "文件不存在"}

    b64 = _image_to_base64(local_path)
    context = _get_review_context(candidate, composed)
    subject = candidate.get("entity_name", "")

    result = await llm.review_image(
        image_base64=b64,
        context=context,
        expected_subject=subject,
        image_type="search",
    )

    return {
        **candidate,
        "review_approved": result.get("approved", False),
        "review_score": result.get("total_score", 0),
        "review_reason": result.get("reason", ""),
    }


# --- Phase 3a: AI 生图审核（含重试重新生成） ---

async def _phase3_review_generated(
    llm,
    candidates: List[Dict],
    composed: Dict,
    output_dir: Path,
) -> Tuple[List[Dict], int]:
    """VLM 审核 AI 生图候选，含一次重试。"""
    if not candidates:
        return [], 0

    sem = asyncio.Semaphore(4)

    async def _review_with_sem(c):
        async with sem:
            return await _review_single_concept(llm, c, composed)

    reviewed = await asyncio.gather(*[_review_with_sem(c) for c in candidates])

    approved = []
    rejected_for_retry = []
    rejected_count = 0

    for c in reviewed:
        if c["review_approved"]:
            approved.append(c)
        else:
            rejected_for_retry.append(c)

    if rejected_for_retry:
        print(f"[illustration] 生图审核第一轮: {len(approved)} 通过, "
              f"{len(rejected_for_retry)} 拒绝，开始重试...")

        retry_candidates = []
        for c in rejected_for_retry:
            feedback = c.get("review_reason", "")
            idx_match = re.search(r'ill_(\d+)', c.get("filename", ""))
            idx = int(idx_match.group(1)) if idx_match else 0
            retry_result = await _generate_one(
                llm, c, output_dir, idx=100 + idx, feedback=feedback,
            )
            if retry_result:
                retry_candidates.append(retry_result)

        if retry_candidates:
            retry_reviewed = await asyncio.gather(
                *[_review_with_sem(c) for c in retry_candidates]
            )
            for c in retry_reviewed:
                if c["review_approved"]:
                    approved.append(c)
                    print(f"[illustration] 生图重试通过: {c.get('concept_name', '')} "
                          f"(score={c['review_score']}/30)")
                else:
                    rejected_count += 1
                    Path(c["local_path"]).unlink(missing_ok=True)
                    print(f"[illustration] 生图重试仍拒绝: {c.get('concept_name', '')} "
                          f"(score={c['review_score']}/30) — {c.get('review_reason', '')}")

        rejected_count += len(rejected_for_retry) - len(retry_candidates)

    approved_paths = {c["local_path"] for c in approved}

    for c in candidates:
        if c.get("local_path") and c["local_path"] not in approved_paths:
            Path(c["local_path"]).unlink(missing_ok=True)

    for c in approved:
        p = c.get("local_path", "")
        if p and not Path(p).exists():
            print(f"[illustration] WARNING: 审核通过但文件消失: {c.get('filename', '')}")

    print(f"[illustration] Phase 3a(生图): 通过 {len(approved)} 张, 拒绝 {rejected_count} 张")
    return approved, rejected_count


# --- Phase 3b: 搜图审核（逐候选审核，取第一张通过的） ---

async def _phase3_review_searched(
    llm,
    candidate_groups: List[List[Dict]],
    composed: Dict,
) -> Tuple[List[Dict], int]:
    """VLM 审核搜索图片，每组候选逐一审核直到有一张通过。"""
    if not candidate_groups:
        return [], 0

    approved = []
    rejected_count = 0
    sem = asyncio.Semaphore(4)

    async def _review_group(group: List[Dict]) -> Optional[Dict]:
        """对一组候选逐一审核，返回第一张通过的，或 None。"""
        nonlocal rejected_count
        for c in group:
            async with sem:
                reviewed = await _review_single_search(llm, c, composed)
            if reviewed["review_approved"]:
                for other in group:
                    if other["local_path"] != reviewed["local_path"]:
                        Path(other["local_path"]).unlink(missing_ok=True)
                entity = reviewed.get("entity_name", "")
                print(f"[illustration] 搜图通过: {entity} "
                      f"(score={reviewed['review_score']}/30, 候选#{c.get('_candidate_index', '?')})")
                return reviewed
            else:
                rejected_count += 1
                Path(c["local_path"]).unlink(missing_ok=True)
        return None

    results = await asyncio.gather(*[_review_group(g) for g in candidate_groups])

    for r in results:
        if r:
            approved.append(r)

    print(f"[illustration] Phase 3b(搜图): 通过 {len(approved)} 张, 拒绝 {rejected_count} 张")
    return approved, rejected_count


# ================================================================
# 节点入口
# ================================================================

def _format_concept_image(c: Dict) -> Dict:
    return {
        "chapter_title": c.get("chapter_title", ""),
        "after_paragraph": c.get("after_paragraph"),
        "filename": c.get("filename", ""),
        "concept_name": c.get("concept_name", ""),
        "content_items": c.get("content_items", []),
        "type": "concept",
        "source": "ai_generated",
        "visual_form": c.get("visual_form", ""),
        "image_prompt": c.get("image_prompt", ""),
        "review_score": c.get("review_score", 0),
        "review_reason": c.get("review_reason", ""),
    }


def _format_search_image(c: Dict) -> Dict:
    return {
        "chapter_title": c.get("chapter_title", ""),
        "after_paragraph": c.get("after_paragraph"),
        "filename": c.get("filename", ""),
        "caption": c.get("caption", ""),
        "entity_name": c.get("entity_name", ""),
        "entity_type": c.get("entity_type", ""),
        "search_query": c.get("search_query", ""),
        "type": "search",
        "source": "web_searched",
        "review_score": c.get("review_score", 0),
        "review_reason": c.get("review_reason", ""),
    }


async def illustration_node(state: PodBookState) -> Dict[str, Any]:
    """
    正文插图节点（只搜图，不 AI 生图）。

    Phase 1: LLM 逐章分析 → 识别适合搜图的实体（search）
    Phase 2: 搜图 + 下载多候选
    Phase 3: VLM 审核搜索图片（逐候选审核）
    Phase 4: 合并输出

    输入：composed_content
    输出：illustrations
    """
    task_id = state.get("task_id", "unknown")
    print(f"[illustration] 开始处理任务: {task_id}")

    composed = state.get("composed_content")
    if not composed:
        print("[illustration] 无书稿内容，跳过插图生成")
        return {
            "illustrations": _empty_result(),
            "current_stage": WorkflowStage.ILLUSTRATION.value,
        }

    llm = get_llm_service()

    content_type = state.get("content_type", "business")
    try:
        prompts = get_prompts("illustration", content_type)
    except KeyError:
        prompts = {}

    output_dir = Path(settings.STORAGE_DIR) / task_id / "illustrations"
    output_dir.mkdir(parents=True, exist_ok=True)

    # Phase 1: 只识别可搜索的具体实体；AI 生图路径已下线。
    search_items = await _phase1_search(llm, composed, prompts)

    if not search_items:
        print("[illustration] 未识别到可配图内容，跳过")
        return {
            "illustrations": _empty_result(),
            "current_stage": WorkflowStage.ILLUSTRATION.value,
        }

    # Phase 2: 搜图下载候选
    search_candidate_groups = await _phase2_search(search_items, output_dir)

    # Phase 3: 搜图审核
    search_approved, search_rejected = await _phase3_review_searched(
        llm, search_candidate_groups, composed,
    )

    # Phase 4: 合并输出（验证文件存在性）
    images = []
    missing_count = 0
    for c in search_approved:
        local = c.get("local_path", "")
        if local and not Path(local).exists():
            print(f"[illustration] WARNING: 审核通过但文件缺失: {c.get('filename', '')} — 跳过")
            missing_count += 1
            continue
        images.append(_format_search_image(c))

    total_rejected = search_rejected + missing_count
    illustrations = {
        "images": images,
        "total_count": len(images),
        "generated_count": 0,
        "searched_count": sum(1 for i in images if i.get("source") == "web_searched"),
        "rejected_count": total_rejected,
    }

    print(f"[illustration] 完成: "
          f"{sum(1 for i in images if i.get('source') == 'web_searched')} 张搜图, "
          f"{total_rejected} 张被拒绝"
          + (f" ({missing_count} 张文件缺失)" if missing_count else ""))

    return {
        "illustrations": illustrations,
        "current_stage": WorkflowStage.ILLUSTRATION.value,
    }


async def _noop_list():
    return []


def _empty_result() -> Dict[str, Any]:
    return {
        "images": [],
        "total_count": 0,
        "generated_count": 0,
        "searched_count": 0,
        "rejected_count": 0,
    }
