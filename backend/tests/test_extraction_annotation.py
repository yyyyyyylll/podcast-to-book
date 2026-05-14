"""
测试精华提炼 + 注释节点
使用叙事体 compose 结果作为输入
"""
import asyncio
import json
from datetime import datetime
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from core.workflow.nodes.extraction import extraction_node
from core.workflow.nodes.annotation import annotation_node


async def test_extraction_and_annotation():
    artifacts_dir = Path(__file__).parent / "artifacts"
    
    # 加载转写结果
    transcription_file = artifacts_dir / "transcription_minimax_20260228_154508.json"
    with open(transcription_file, "r", encoding="utf-8") as f:
        transcription_data = json.load(f)
    
    # 加载叙事体 compose 结果
    compose_file = artifacts_dir / "compose_minimax_20260228_162041.json"
    with open(compose_file, "r", encoding="utf-8") as f:
        compose_data = json.load(f)
    
    print(f"转写文件: {transcription_file.name}")
    print(f"成稿文件: {compose_file.name}")
    print(f"章节数: {len(compose_data.get('chapters', []))}")
    
    # 构建 state
    state = {
        "task_id": "test_extraction_annotation",
        "transcription": transcription_data.get("transcription", {}),
        "composed_content": compose_data,
    }
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    
    # ========== 阶段一：精华提炼 ==========
    print("\n" + "="*50)
    print("阶段一：精华提炼 (extraction)")
    print("="*50)
    
    extraction_result = await extraction_node(state)
    highlights = extraction_result.get("highlights", {})
    
    # 保存精华提炼结果
    extraction_output = artifacts_dir / f"extraction_minimax_{timestamp}.json"
    with open(extraction_output, "w", encoding="utf-8") as f:
        json.dump(highlights, f, ensure_ascii=False, indent=2)
    print(f"\n精华提炼结果已保存: {extraction_output.name}")
    
    # 生成精华提炼可读版
    extraction_review = artifacts_dir / f"extraction_review_minimax_{timestamp}.md"
    with open(extraction_review, "w", encoding="utf-8") as f:
        f.write("# 精华提炼结果\n\n")
        
        quotes = highlights.get("quotes", [])
        f.write(f"## 金句 ({len(quotes)} 条)\n\n")
        for i, q in enumerate(quotes, 1):
            f.write(f"### 金句 {i}\n")
            f.write(f"- **原文**: {q.get('text', '')}\n")
            f.write(f"- **出处**: 「{q.get('chapter_title', '')}」\n")
            f.write(f"- **说话人**: {q.get('speaker', '')}\n")
            f.write(f"- **位置**: {q.get('placement', '')}\n\n")
        
        methodologies = highlights.get("methodologies", [])
        f.write(f"## 方法论 ({len(methodologies)} 个)\n\n")
        for i, m in enumerate(methodologies, 1):
            f.write(f"### 方法论 {i}: {m.get('name', '')}\n")
            f.write(f"- **出处**: 「{m.get('chapter_title', '')}」\n")
            steps = m.get("steps", [])
            if steps:
                f.write(f"- **步骤**:\n")
                for j, step in enumerate(steps, 1):
                    f.write(f"  {j}. {step}\n")
            f.write("\n")
    
    print(f"精华提炼可读版已保存: {extraction_review.name}")
    print(f"金句: {len(quotes)} 条, 方法论: {len(methodologies)} 个")
    
    # ========== 阶段二：内容注释 ==========
    print("\n" + "="*50)
    print("阶段二：内容注释 (annotation)")
    print("="*50)
    
    # 更新 state
    state["highlights"] = highlights
    
    annotation_result = await annotation_node(state)
    annotated_content = annotation_result.get("annotated_content", {})
    
    # 保存注释结果
    annotation_output = artifacts_dir / f"annotation_minimax_{timestamp}.json"
    with open(annotation_output, "w", encoding="utf-8") as f:
        json.dump(annotated_content, f, ensure_ascii=False, indent=2)
    print(f"\n注释结果已保存: {annotation_output.name}")
    
    # 生成注释可读版（完整书稿带脚注）
    annotation_review = artifacts_dir / f"annotation_review_minimax_{timestamp}.md"
    with open(annotation_review, "w", encoding="utf-8") as f:
        f.write(f"# {annotated_content.get('core_theme', '播客书稿')}\n\n")
        f.write(f"**内容类型**: {annotated_content.get('content_type', '')}\n")
        f.write(f"**写作风格**: {annotated_content.get('writing_style', '')}\n")
        f.write(f"**总脚注数**: {annotated_content.get('total_footnotes', 0)}\n\n")
        f.write("---\n\n")
        
        for i, ch in enumerate(annotated_content.get("chapters", []), 1):
            f.write(f"## 第 {i} 章：{ch.get('title', '')}\n\n")
            f.write(ch.get("content", ""))
            f.write("\n\n---\n\n")
    
    print(f"注释可读版已保存: {annotation_review.name}")
    print(f"总脚注数: {annotated_content.get('total_footnotes', 0)}")
    
    print("\n" + "="*50)
    print("全部完成！")
    print("="*50)


if __name__ == "__main__":
    asyncio.run(test_extraction_and_annotation())
