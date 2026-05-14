"""Smoke test for public topic/series visibility policy."""
from __future__ import annotations

from multi.podcast_book_fit.render import public_series_clues


def main() -> None:
    assert public_series_clues([]) == []
    assert public_series_clues([{"title": "只有一期", "episode_count": 1}]) == []
    visible = public_series_clues(
        [
            {"title": "一千零一夜·系列合集", "episode_count": 10},
            {"title": "单期栏目", "episode_count": 1},
        ]
    )
    assert len(visible) == 1
    assert visible[0]["title"] == "一千零一夜·系列合集"
    print("[OK] public page hides weak topic clues")


if __name__ == "__main__":
    main()
