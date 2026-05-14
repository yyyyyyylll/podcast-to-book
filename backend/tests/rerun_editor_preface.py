"""只重跑编者序节点，用于快速验证 prompt 修改效果。"""
import asyncio, json, os, sys, time
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

ARTIFACT_DIR = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else None
if not ARTIFACT_DIR or not (ARTIFACT_DIR / "state.json").exists():
    print("用法: python -m tests.rerun_editor_preface <artifact_dir>")
    sys.exit(1)

async def run():
    from core.services.llm_service import _current_tracker, UsageTracker, bind_task_id
    from core.workflow.nodes.editor_preface import editor_preface_node

    state = json.loads((ARTIFACT_DIR / "state.json").read_text("utf-8"))
    task_id = state["task_id"]
    title = state.get("title", "unknown")
    bind_task_id(task_id)
    tracker = UsageTracker()
    _current_tracker.set(tracker)

    print(f"[editor_preface] 重跑编者序: {title}")
    t0 = time.perf_counter()
    output = await editor_preface_node(state)
    elapsed = time.perf_counter() - t0

    preface = output.get("editor_preface_content", "")
    print(f"\n[editor_preface] 耗时: {elapsed:.1f}s")
    print(f"[editor_preface] 字数: {len(preface)}")
    print(f"\n{'='*60}")
    print("编者序：")
    print('='*60)
    print(preface)

    # 保存
    (ARTIFACT_DIR / "05_editor_preface_v2.md").write_text(
        f"# 编者序（重跑）\n\n{preface}", encoding="utf-8"
    )
    print(f"\n已保存: {ARTIFACT_DIR / '05_editor_preface_v2.md'}")

if __name__ == "__main__":
    asyncio.run(run())
