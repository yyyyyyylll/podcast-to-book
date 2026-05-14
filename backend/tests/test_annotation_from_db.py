"""
测试注释节点 —— 从数据库加载已完成任务的 composed_content 直接运行

用法：
    cd backend
    python3 -m tests.test_annotation_from_db
    python3 -m tests.test_annotation_from_db --task <task_id>   # 指定任务
"""
import asyncio
import json
import sqlite3
import sys
import argparse
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.workflow.nodes.annotation import annotation_node


def load_task_from_db(task_id: str | None = None) -> tuple[str, str, dict]:
    """从数据库加载任务数据，返回 (task_id, title, composed_content)"""
    db_path = Path(__file__).parent.parent / "storage" / "podbook.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    if task_id:
        row = conn.execute(
            "SELECT id, title, result_composed FROM tasks WHERE id = ?",
            (task_id,),
        ).fetchone()
        if not row:
            conn.close()
            raise ValueError(f"任务 {task_id} 不存在")
    else:
        row = conn.execute(
            "SELECT id, title, result_composed FROM tasks "
            "WHERE status='completed' AND result_composed IS NOT NULL "
            "ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        if not row:
            conn.close()
            raise ValueError("没有找到已完成的任务")

    conn.close()
    return row["id"], row["title"], json.loads(row["result_composed"])


async def run_test(task_id: str | None = None):
    artifacts_dir = Path(__file__).parent / "artifacts"
    artifacts_dir.mkdir(exist_ok=True)

    tid, title, composed = load_task_from_db(task_id)
    chapters = composed.get("chapters", [])
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    print(f"任务 ID : {tid}")
    print(f"标题   : {title}")
    print(f"章节数  : {len(chapters)}")
    for i, ch in enumerate(chapters):
        print(f"  [{i:2d}] {ch.get('title', '')} ({len(ch.get('content', ''))} 字)")

    state = {
        "task_id": tid,
        "composed_content": composed,
    }

    print("\n" + "=" * 60)
    print("开始运行注释节点...")
    print("=" * 60)
    t0 = datetime.now()

    result = await annotation_node(state)
    annotated = result.get("annotated_content", {})

    elapsed = (datetime.now() - t0).total_seconds()
    total_fn = annotated.get("total_footnotes", 0)

    print(f"\n完成！耗时 {elapsed:.1f}s，共添加 {total_fn} 个脚注")

    # 保存 JSON 结果
    slug = tid[:8]
    json_out = artifacts_dir / f"annotation_db_{slug}_{timestamp}.json"
    with open(json_out, "w", encoding="utf-8") as f:
        json.dump(annotated, f, ensure_ascii=False, indent=2)
    print(f"JSON 已保存: {json_out.name}")

    # 生成可读 Markdown
    md_out = artifacts_dir / f"annotation_review_db_{slug}_{timestamp}.md"
    with open(md_out, "w", encoding="utf-8") as f:
        f.write(f"# {annotated.get('core_theme', title)}\n\n")
        f.write(f"> 总脚注：{total_fn} 个 | 章节：{len(chapters)} 章 | 耗时：{elapsed:.1f}s\n\n")
        f.write("---\n\n")
        for i, ch in enumerate(annotated.get("chapters", []), 1):
            f.write(f"## 第 {i} 章：{ch.get('title', '')}\n\n")
            f.write(ch.get("content", ""))
            f.write("\n\n---\n\n")
    print(f"Markdown 已保存: {md_out.name}")

    # 打印每章脚注统计
    print("\n各章脚注分布：")
    for ch in annotated.get("chapters", []):
        content = ch.get("content", "")
        import re
        fn_marks = re.findall(r"\[\^\d+\]:", content)
        fn_count = len(fn_marks)
        title_short = ch.get("title", "")[:16]
        bar = "█" * fn_count
        print(f"  {title_short:<16} {bar} {fn_count}")

    print("\n完成。")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="测试注释节点")
    parser.add_argument("--task", help="指定任务 ID（默认使用最近完成的任务）")
    args = parser.parse_args()
    asyncio.run(run_test(args.task))
