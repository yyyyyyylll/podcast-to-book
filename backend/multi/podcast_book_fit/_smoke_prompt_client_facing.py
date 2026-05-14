"""Smoke test for client-facing book-fit prompt wording."""
from __future__ import annotations

from multi.podcast_book_fit.prompt import build_book_fit_user


def main() -> None:
    prompt = build_book_fit_user(
        podcast_name="夜夜页页",
        podcast_intro="借读书之名闲聊。",
        episode_list=[{"title": "Vol.001 一千零一夜", "intro": "开篇"}],
    )
    assert "可以直接给主播/客户看的建议" in prompt
    assert "建议书摘要" in prompt
    assert "# 任务目标" in prompt
    assert "# 判断步骤" in prompt
    assert "# 输出约束" in prompt
    assert "# Few-shot 风格示例" in prompt
    assert "先识别内容气质" in prompt
    assert "再聚合主题方向" in prompt
    assert "最后选择样章" in prompt
    assert "不要照抄示例内容" in prompt
    assert "不要写成后台判断" in prompt
    assert "不要反复说“是否值得沟通”" in prompt
    assert "面向主播本人时可以使用“您”" in prompt
    assert "第一个主题方向、第一个板块下的第一期" in prompt
    assert "建议先用这一期做样章，方便快速看到单集改写后的成稿质感。" in prompt
    assert "主题或标题候选" in prompt
    assert "整本书标题候选" in prompt
    assert "主题方向" in prompt
    assert "建议理由" in prompt
    assert "经过精挑细选后的最好方案" in prompt
    assert "同一份输入多次运行时" in prompt
    assert "优先保持相同或高度相似的方向判断" in prompt
    assert "按专家判断的优先级排序" in prompt
    assert "不要默认使用「主标题：副标题」" in prompt
    assert "最多 1 个标题可以使用冒号" in prompt
    assert "至少 2 个标题不使用冒号" in prompt
    assert "标题本身不需要承担全部解释功能" in prompt
    assert "20 期以内" in prompt
    assert "同一本书里的大章节" in prompt
    assert "每个方向必须列出具体是哪几期节目" in prompt
    assert "如果单期节目时长接近或达到 3 小时" in prompt
    assert "每个板块只列 3 期节目" in prompt
    assert "如果单期节目通常在 1-2 小时左右" in prompt
    assert "不必全部推满 4 期" in prompt
    assert "episode_titles" in prompt
    assert "from_series" in prompt
    assert "selected_episodes" in prompt
    assert "不要输出 `client_next_step`" in prompt
    assert "outreach_angle" not in prompt
    print("[OK] book-fit prompt asks for customer-facing advice")


if __name__ == "__main__":
    main()
