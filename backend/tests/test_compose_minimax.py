"""
测试 compose 节点：使用 minimax 转写结果作为输入
"""
import asyncio
import json
from datetime import datetime
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from core.workflow.nodes.compose import Composer, ComposeGenerator, METHODOLOGY_SYSTEM_PROMPT, METHODOLOGY_PROMPT_TEMPLATE


async def test_compose():
    artifacts_dir = Path(__file__).parent / "artifacts"
    
    input_file = artifacts_dir / "transcription_minimax_20260228_154508.json"
    with open(input_file, "r", encoding="utf-8") as f:
        data = json.load(f)
    
    transcription = data.get("transcription", {})
    segments = transcription.get("segments", [])
    metadata = transcription.get("metadata", {})
    
    print(f"输入文件: {input_file.name}")
    print(f"片段数量: {len(segments)}")
    
    speakers = {}
    for seg in segments:
        speaker = seg.get("speaker", "未知")
        speakers[speaker] = speakers.get(speaker, 0) + len(seg.get("text", ""))
    print(f"说话人: {speakers}")
    
    # 从文件中的 metadata 构建上下文
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
    metadata_context = "\n".join(lines)
    
    print(f"\n{metadata_context}\n")
    
    # 强制使用方法论风格
    generator = ComposeGenerator(METHODOLOGY_SYSTEM_PROMPT, METHODOLOGY_PROMPT_TEMPLATE)
    raw_result = await generator.generate(segments, metadata_context)
    
    # 构建输出格式
    chapters = []
    for sec in raw_result.get("sections", []):
        chapters.append({
            "title": sec.get("title", ""),
            "content": sec.get("content", ""),
            "key_points": sec.get("key_points", []),
            "section_type": sec.get("section_type", ""),
        })
    
    result = {
        "content_type": raw_result.get("content_type", ""),
        "core_theme": raw_result.get("core_theme", ""),
        "theme_keywords": raw_result.get("theme_keywords", []),
        "speakers": raw_result.get("speakers", []),
        "structure_rationale": raw_result.get("structure_rationale", ""),
        "chapters": chapters,
        "writing_style": "methodology",
    }
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    
    output_file = artifacts_dir / f"compose_minimax_{timestamp}.json"
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"\n结果已保存: {output_file.name}")
    
    review_file = artifacts_dir / f"compose_review_minimax_{timestamp}.md"
    with open(review_file, "w", encoding="utf-8") as f:
        f.write(f"# Compose 测试结果\n\n")
        f.write(f"- 输入文件: {input_file.name}\n")
        f.write(f"- 写作风格: {result.get('writing_style', '未知')}\n")
        f.write(f"- 内容类型: {result.get('content_type', '未知')}\n")
        f.write(f"- 核心主题: {result.get('core_theme', '未知')}\n")
        f.write(f"- 耗时: {result.get('elapsed_seconds', 0):.1f}s\n\n")
        
        f.write(f"## 章节概览\n\n")
        chapters = result.get("chapters", [])
        total_chars = 0
        for i, ch in enumerate(chapters, 1):
            content = ch.get("content", "")
            char_count = len(content)
            total_chars += char_count
            f.write(f"{i}. **{ch.get('title', '无标题')}** ({char_count} 字)\n")
        f.write(f"\n**总字数: {total_chars}**\n\n")
        
        f.write(f"---\n\n")
        for i, ch in enumerate(chapters, 1):
            f.write(f"## 第 {i} 章：{ch.get('title', '无标题')}\n\n")
            f.write(ch.get("content", "无内容"))
            f.write("\n\n---\n\n")
    
    print(f"可读版本已保存: {review_file.name}")
    print(f"\n章节数: {len(chapters)}, 总字数: {total_chars}")


if __name__ == "__main__":
    asyncio.run(test_compose())
