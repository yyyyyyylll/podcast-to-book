from multi.io import JobPaths, write_json
from multi.runners.merge_book_pdf import _book_title
from multi.runners.name_book import apply_book_title, resolve_apply_pick


def test_apply_book_title_merges_front_matter(tmp_path):
    paths = JobPaths(job_id="demo", root=tmp_path)
    write_json(paths.show_json, {"title": "可能性褶皱 Possibility"})
    write_json(
        paths.merged_dir / "book_front_matter.json",
        {
            "preface_title": "序言",
            "preface_paragraphs": ["已有序言"],
            "authors": ["哈梨Harriet"],
        },
    )

    updated = apply_book_title(
        paths=paths,
        title="在褶皱里重建生活",
        source="name_book#2",
        rationale="覆盖栏目名，呈现整书主题",
    )

    assert updated["book_title"] == "在褶皱里重建生活"
    assert updated["preface_paragraphs"] == ["已有序言"]
    assert updated["authors"] == ["哈梨Harriet"]
    assert updated["_workbench"]["book_title_source"] == "name_book#2"
    assert updated["_workbench"]["book_title_rationale"] == "覆盖栏目名，呈现整书主题"


def test_merge_book_title_prefers_front_matter_book_title(tmp_path):
    paths = JobPaths(job_id="demo", root=tmp_path)
    write_json(paths.show_json, {"title": "可能性褶皱 Possibility"})
    write_json(paths.merged_dir / "book_front_matter.json", {"book_title": "在褶皱里重建生活"})

    assert _book_title(paths) == "在褶皱里重建生活"


def test_resolve_apply_pick_defaults_to_recommended_pick():
    payload = {"recommended_pick": 2, "candidates": [{"book_title": "A"}, {"book_title": "B"}]}

    assert resolve_apply_pick(payload, explicit_pick=0, no_apply=False) == 2


def test_resolve_apply_pick_allows_generate_only_mode():
    payload = {"recommended_pick": 2, "candidates": [{"book_title": "A"}, {"book_title": "B"}]}

    assert resolve_apply_pick(payload, explicit_pick=0, no_apply=True) == 0
