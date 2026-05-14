"""
Smoke test for podcast direction input trimming.

Usage:
    cd backend
    python -m workbench.podcast_direction._smoke_trim
"""
from __future__ import annotations

from multi.podcast_direction.runner import _trim_input


def main() -> int:
    short_raw = {
        "podcast_name": "Long Show",
        "podcast_intro": "A show with many short episodes.",
        "episode_list": [
            {
                "title": f"Episode {i:02d}",
                "intro": "Short intro.",
            }
            for i in range(1, 71)
        ],
    }
    trimmed, stats = _trim_input(short_raw)
    episode_count = len(trimmed["episode_list"])
    if episode_count != 70:
        print(
            "[FAIL] expected all 70 short episodes to remain, "
            f"got {episode_count}; stats={stats}"
        )
        return 1
    if stats["_input_sampled"]:
        print(f"[FAIL] expected no sampling for short input; stats={stats}")
        return 1

    long_raw = {
        "podcast_name": "Long Show",
        "podcast_intro": "A show with many long episode descriptions.",
        "episode_list": [
            {
                "title": f"Episode {i:02d} with a reasonably descriptive title",
                "intro": "This is a long intro. " * 40,
            }
            for i in range(1, 71)
        ],
    }
    trimmed, stats = _trim_input(long_raw)
    episode_count = len(trimmed["episode_list"])
    if episode_count != 70:
        print(
            "[FAIL] expected all 70 long episodes to remain with shorter intros, "
            f"got {episode_count}; stats={stats}"
        )
        return 1
    if stats["_input_sampled"]:
        print(f"[FAIL] expected no sampling for long input; stats={stats}")
        return 1

    print("[OK] trim keeps all episodes when titles fit")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
