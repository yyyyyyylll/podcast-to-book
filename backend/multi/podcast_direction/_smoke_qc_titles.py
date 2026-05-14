"""Smoke test for QC prompt seeing enough episode titles on large shows."""
from __future__ import annotations

from multi.podcast_direction.prompt import build_qc_user


def main() -> None:
    episodes = [{"title": f"第{i:03d}集", "intro": ""} for i in range(1, 71)]
    prompt = build_qc_user(
        input_payload={
            "podcast_name": "测试播客",
            "podcast_intro": "简介",
            "episode_list": episodes,
        },
        diagnosis={"overall_diagnosis": {"podcast_name": "测试播客"}},
    )
    assert "第001集" in prompt
    assert "第070集" in prompt
    assert "还有 40 集未列出" not in prompt
    print("[OK] QC prompt includes all titles for a 70-episode show")


if __name__ == "__main__":
    main()
