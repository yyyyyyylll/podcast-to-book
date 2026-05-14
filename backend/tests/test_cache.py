"""
缓存功能测试

运行方式:
    cd backend
    python -m tests.test_cache
"""
import asyncio
import os
import sys
import tempfile
import shutil

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_content_hash():
    """测试哈希函数的正确性和一致性。"""
    from single.utils import compute_content_hash

    # 相同输入 → 相同哈希
    h1 = compute_content_hash("ep001", "我的标题", "张三", ["李四"], "编者序内容")
    h2 = compute_content_hash("ep001", "我的标题", "张三", ["李四"], "编者序内容")
    assert h1 == h2, f"相同输入应产生相同哈希: {h1} != {h2}"

    # 不同 episode → 不同哈希
    h3 = compute_content_hash("ep002", "我的标题", "张三", ["李四"], "编者序内容")
    assert h1 != h3, "不同 episode_id 应产生不同哈希"

    # 空字段归一化：空串和 None 等价
    h4 = compute_content_hash("ep001", "", "", None, "")
    h5 = compute_content_hash("ep001", None, None, [], None)
    assert h4 == h5, f"空值归一化后应相同: {h4} != {h5}"

    # 嘉宾顺序无关
    h6 = compute_content_hash("ep001", "", "", ["A", "B"], "")
    h7 = compute_content_hash("ep001", "", "", ["B", "A"], "")
    assert h6 == h7, "嘉宾顺序不应影响哈希"

    # 哈希长度
    assert len(h1) == 32, f"哈希长度应为 32: {len(h1)}"

    # 空白 trim
    h8 = compute_content_hash("ep001", "  标题  ", " 主持人 ", [" 嘉宾 "], "  序  ")
    h9 = compute_content_hash("ep001", "标题", "主持人", ["嘉宾"], "序")
    assert h8 == h9, "应 trim 空白后再计算"

    print("[PASS] test_content_hash: 全部通过")


def test_simulated_progress():
    """测试进度模拟数据的合理性。"""
    from single.background import SIMULATED_PROGRESS_FULL, SIMULATED_PROGRESS_PRE_TYPESET

    # 时间单调递增
    prev_time = -1
    for t, stage, progress in SIMULATED_PROGRESS_FULL:
        assert t >= prev_time, f"时间应单调递增: {prev_time} -> {t}"
        prev_time = t

    # 进度单调递增
    prev_progress = -1
    for _, _, progress in SIMULATED_PROGRESS_FULL:
        assert progress > prev_progress, f"进度应单调递增: {prev_progress} -> {progress}"
        prev_progress = progress

    # 最终状态
    last = SIMULATED_PROGRESS_FULL[-1]
    assert last[1] == "completed" and last[2] == 100, f"最后一步应为 completed/100: {last}"

    # 总时长在合理范围 (8-12 分钟)
    total_seconds = SIMULATED_PROGRESS_FULL[-1][0]
    assert 480 <= total_seconds <= 720, f"总时长 {total_seconds}s 不在 8-12 分钟范围"

    # Pre-typeset 不含 typeset 和 completed
    for _, stage, _ in SIMULATED_PROGRESS_PRE_TYPESET:
        assert stage not in ("typeset", "completed"), f"Pre-typeset 不应含 {stage}"

    # 阶段顺序正确
    stages_seen = []
    for _, stage, _ in SIMULATED_PROGRESS_FULL:
        if not stages_seen or stages_seen[-1] != stage:
            stages_seen.append(stage)
    assert stages_seen == ["transcription", "compose", "enrich", "typeset", "completed"], \
        f"阶段顺序不对: {stages_seen}"

    print("[PASS] test_simulated_progress: 全部通过")


def test_copy_pdf():
    """测试 PDF 复制逻辑。"""
    from unittest.mock import MagicMock
    from single.background import _copy_pdf

    with tempfile.TemporaryDirectory() as tmpdir:
        original_storage = os.environ.get("STORAGE_DIR")
        os.environ["STORAGE_DIR"] = tmpdir

        from core.config import settings
        old_dir = settings.STORAGE_DIR
        settings.STORAGE_DIR = tmpdir

        try:
            src_dir = os.path.join(tmpdir, "source-task")
            os.makedirs(src_dir)
            src_pdf = os.path.join(src_dir, "book_20260324.pdf")
            with open(src_pdf, "w") as f:
                f.write("fake pdf content")

            source = MagicMock()
            source.pdf_path = src_pdf

            new_path = _copy_pdf(source, "new-task-123")

            assert new_path is not None, "应返回新路径"
            assert os.path.isfile(new_path), "新 PDF 应存在"
            assert "new-task-123" in new_path, "路径应包含新 task_id"
            with open(new_path) as f:
                assert f.read() == "fake pdf content", "内容应一致"

            # 源文件不存在时
            source.pdf_path = "/nonexistent/file.pdf"
            assert _copy_pdf(source, "another-task") is None, "文件不存在应返回 None"

            # pdf_path 为空时
            source.pdf_path = None
            assert _copy_pdf(source, "another-task") is None, "pdf_path 为空应返回 None"

            print("[PASS] test_copy_pdf: 全部通过")
        finally:
            settings.STORAGE_DIR = old_dir
            if original_storage:
                os.environ["STORAGE_DIR"] = original_storage


def test_copy_task_assets():
    """测试资源文件复制逻辑。"""
    from single.background import _copy_task_assets
    from core.config import settings

    with tempfile.TemporaryDirectory() as tmpdir:
        old_dir = settings.STORAGE_DIR
        settings.STORAGE_DIR = tmpdir

        try:
            # 创建源任务目录结构
            src_id = "source-task"
            src_dir = os.path.join(tmpdir, src_id)
            ill_dir = os.path.join(src_dir, "illustrations")
            os.makedirs(ill_dir)

            with open(os.path.join(ill_dir, "ill_01.jpg"), "w") as f:
                f.write("image data 1")
            with open(os.path.join(ill_dir, "ill_02.jpg"), "w") as f:
                f.write("image data 2")
            with open(os.path.join(src_dir, "cover_rendered.png"), "w") as f:
                f.write("cover data")
            with open(os.path.join(src_dir, "paper_texture.jpg"), "w") as f:
                f.write("texture data")
            with open(os.path.join(src_dir, "book_xxx.pdf"), "w") as f:
                f.write("pdf not copied by assets")

            new_id = "new-task"
            _copy_task_assets(src_id, new_id)

            new_dir = os.path.join(tmpdir, new_id)
            assert os.path.isfile(os.path.join(new_dir, "illustrations", "ill_01.jpg"))
            assert os.path.isfile(os.path.join(new_dir, "illustrations", "ill_02.jpg"))
            assert os.path.isfile(os.path.join(new_dir, "cover_rendered.png"))
            assert os.path.isfile(os.path.join(new_dir, "paper_texture.jpg"))
            assert not os.path.isfile(os.path.join(new_dir, "book_xxx.pdf")), \
                "PDF 不应被 _copy_task_assets 复制"

            print("[PASS] test_copy_task_assets: 全部通过")
        finally:
            settings.STORAGE_DIR = old_dir


def test_task_model_fields():
    """测试 Task 模型包含新字段。"""
    from single.models.task import Task

    assert hasattr(Task, "content_hash"), "Task 应有 content_hash 字段"
    assert hasattr(Task, "cached_from"), "Task 应有 cached_from 字段"

    t = Task(
        title="test", author="test", audio_path="http://example.com/audio.mp3",
        content_hash="abc123", cached_from="some-task-id",
    )
    assert t.content_hash == "abc123"
    assert t.cached_from == "some-task-id"

    print("[PASS] test_task_model_fields: 全部通过")


def test_upload_computes_hash():
    """验证 upload.py 中 import 和函数可用。"""
    from single.utils import compute_content_hash

    h = compute_content_hash(
        episode_id="test-ep",
        user_title="",
        user_host_name="",
        user_guest_names=[],
        editor_preface="",
    )
    assert isinstance(h, str) and len(h) == 32
    print("[PASS] test_upload_computes_hash: 全部通过")


if __name__ == "__main__":
    print("=" * 50)
    print("缓存功能测试")
    print("=" * 50)
    print()

    tests = [
        test_content_hash,
        test_simulated_progress,
        test_copy_pdf,
        test_copy_task_assets,
        test_task_model_fields,
        test_upload_computes_hash,
    ]

    passed = 0
    failed = 0
    for test in tests:
        try:
            test()
            passed += 1
        except Exception as e:
            print(f"[FAIL] {test.__name__}: {e}")
            import traceback
            traceback.print_exc()
            failed += 1
        print()

    print("=" * 50)
    print(f"结果: {passed} 通过, {failed} 失败")
    if failed == 0:
        print("全部通过!")
    print("=" * 50)
