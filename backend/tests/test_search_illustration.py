"""快速测试搜图流程：Phase 1b（LLM 识别实体）+ Phase 2b（Serper 搜图+下载）。

用法: cd backend && python -m tests.test_search_illustration
"""

import asyncio
import json
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

from core.config import settings
from core.services.llm_service import get_llm_service
from core.workflow.nodes.illustration import (
    _phase1_search,
    _serper_image_search,
    _search_and_download,
)


STATE_PATH = Path(__file__).parent / "artifacts" / "step_by_step" / "state.json"
OUTPUT_DIR = Path(__file__).parent / "artifacts" / "search_test_output"


async def test_phase1b():
    """测试 Phase 1b: LLM 识别搜图实体。"""
    print("=" * 60)
    print("Phase 1b: LLM 识别适合搜图的实体")
    print("=" * 60)

    with open(STATE_PATH) as f:
        state = json.load(f)
    composed = state["composed_content"]

    llm = get_llm_service()
    items = await _phase1_search(llm, composed)

    print(f"\n共识别 {len(items)} 个搜图实体:")
    for i, item in enumerate(items):
        print(f"  [{i}] {item['entity_name']} ({item['entity_type']})")
        print(f"      搜索词: {item['search_query']}")
        print(f"      说明: {item['caption']}")
        print(f"      章节: {item['chapter_title']}, P{item['after_paragraph']}")
    return items


async def test_serper_api():
    """测试 Serper API 连通性。"""
    print("\n" + "=" * 60)
    print("Serper API 连通性测试")
    print("=" * 60)

    if not settings.SERPER_API_KEY:
        print("SERPER_API_KEY 未配置，跳过")
        return

    results = await _serper_image_search("Lovart AI design tool", count=3)
    print(f"搜索 'Lovart AI design tool': {len(results)} 条结果")
    for r in results:
        print(f"  {r['width']}x{r['height']} — {r['content_url'][:80]}")


async def test_phase2b(items):
    """测试 Phase 2b: 搜图+下载（取前 2 个实体）。"""
    print("\n" + "=" * 60)
    print("Phase 2b: Serper 搜图 + 下载")
    print("=" * 60)

    if not items:
        print("无实体可测试")
        return

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    test_items = items[:2]

    for i, item in enumerate(test_items):
        print(f"\n--- 测试实体 [{i}]: {item['entity_name']} ---")
        candidates = await _search_and_download(item, OUTPUT_DIR, idx=i)
        print(f"下载结果: {len(candidates)} 张候选图片")
        for c in candidates:
            print(f"  {c['filename']} — {c['search_url'][:60]}")


async def main():
    await test_serper_api()
    items = await test_phase1b()
    await test_phase2b(items)

    print("\n" + "=" * 60)
    print(f"测试完成！下载的图片在: {OUTPUT_DIR}")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
