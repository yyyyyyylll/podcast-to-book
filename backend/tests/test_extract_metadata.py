"""
从转写内容中提取播客元数据
"""
import asyncio
import json
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from core.config import settings
from core.services.llm_service import get_llm_service


EXTRACT_METADATA_PROMPT = """请从以下播客转写内容的开头部分，提取播客元数据信息。

【转写内容开头】
{opening_text}

请提取以下信息，严格输出 JSON 格式：

```json
{{
    "title": "播客节目名称（如无法确定则为空字符串）",
    "host_name": "主持人姓名",
    "guest_names": ["嘉宾1姓名", "嘉宾2姓名"],
    "company_names": ["相关公司1", "相关公司2"],
    "podcast_intro": "节目简介或本期主题（一句话概括）",
    "proper_nouns": ["领域专有名词1", "专有名词2"]
}}
```

提取要点：
1. 主持人通常会在开头自我介绍
2. 嘉宾信息通常在主持人介绍中提到
3. 相关公司是嘉宾创办或工作的公司
4. 专有名词是播客中可能出现的技术术语、产品名等
5. 只提取明确提到的信息，不要猜测"""


async def extract_metadata(segments: list) -> dict:
    """从转写内容开头提取元数据"""
    opening_parts = []
    total_chars = 0
    for seg in segments[:20]:
        text = seg.get("text", "")
        speaker = seg.get("speaker", "未知")
        opening_parts.append(f"【{speaker}】{text}")
        total_chars += len(text)
        if total_chars > 2000:
            break
    
    opening_text = "\n\n".join(opening_parts)
    
    prompt = EXTRACT_METADATA_PROMPT.format(opening_text=opening_text)
    
    llm = get_llm_service()
    result = await llm.generate_json(
        prompt=prompt,
        system_prompt="你是一位播客内容分析专家，擅长从对话内容中提取关键信息。",
        model=settings.LLM_MODEL,
        timeout=60,
    )
    
    return result


async def main():
    artifacts_dir = Path(__file__).parent / "artifacts"
    
    input_file = artifacts_dir / "transcription_minimax_20260228_154508.json"
    print(f"读取文件: {input_file.name}")
    
    with open(input_file, "r", encoding="utf-8") as f:
        data = json.load(f)
    
    transcription = data.get("transcription", {})
    segments = transcription.get("segments", [])
    print(f"片段数量: {len(segments)}")
    
    print("\n正在提取元数据...")
    metadata = await extract_metadata(segments)
    
    print("\n提取结果:")
    print(json.dumps(metadata, ensure_ascii=False, indent=2))
    
    # 将元数据添加到 transcription 中
    transcription["metadata"] = metadata
    data["transcription"] = transcription
    
    # 保存更新后的文件
    with open(input_file, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    
    print(f"\n已更新文件: {input_file.name}")
    
    # 同时构建 metadata_context 预览
    print("\n--- metadata_context 预览 ---")
    lines = ["【播客元数据】"]
    if metadata.get("title"):
        lines.append(f"- 节目名称：{metadata['title']}")
    if metadata.get("host_name"):
        lines.append(f"- 主持人：{metadata['host_name']}")
    if metadata.get("guest_names"):
        lines.append(f"- 嘉宾：{'、'.join(metadata['guest_names'])}")
    if metadata.get("company_names"):
        lines.append(f"- 相关公司/组织：{'、'.join(metadata['company_names'])}")
    if metadata.get("podcast_intro"):
        lines.append(f"- 节目简介：{metadata['podcast_intro']}")
    if metadata.get("proper_nouns"):
        lines.append(f"- 领域专有名词：{'、'.join(metadata['proper_nouns'])}")
    print("\n".join(lines))


if __name__ == "__main__":
    asyncio.run(main())
