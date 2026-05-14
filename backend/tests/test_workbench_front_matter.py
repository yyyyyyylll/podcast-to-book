from multi.io import JobPaths, write_json
from multi.runners.render_book_front_matter import build_front_matter_typst, resolve_book_authors


def test_title_page_renders_book_subtitle(tmp_path):
    paths = JobPaths(job_id="demo", root=tmp_path)
    write_json(paths.show_json, {"title": "可能性褶皱 Possibility", "podcaster_names": ["哈梨Harriet"]})

    typst = build_front_matter_typst(
        paths=paths,
        front_matter={
            "book_title": "把褶皱活成纹理",
            "book_subtitle": "在阅读里练习清醒与温柔",
            "preface_paragraphs": [],
        },
        chapters=[],
    )

    assert '#let book-subtitle = "在阅读里练习清醒与温柔"' in typst
    assert "#book-subtitle" in typst


def test_title_page_uses_larger_consistent_serif_type(tmp_path):
    paths = JobPaths(job_id="demo", root=tmp_path)
    write_json(paths.show_json, {"title": "可能性褶皱 Possibility", "podcaster_names": ["哈梨Harriet"]})

    typst = build_front_matter_typst(
        paths=paths,
        front_matter={
            "book_title": "把褶皱活成纹理",
            "book_subtitle": "在阅读里练习清醒与温柔",
            "authors": ["哈梨", "fofo"],
        },
        chapters=[],
    )

    assert 'size: 31pt' in typst
    assert 'size: 14pt' in typst
    assert 'size: 13pt' in typst
    assert typst.count('font: ("Source Han Serif SC", "Songti SC", "Noto Serif CJK SC", "Noto Serif SC")') >= 3


def test_title_page_author_line_has_work_suffix_and_blank_verso_hides_page_number(tmp_path):
    paths = JobPaths(job_id="demo", root=tmp_path)
    write_json(paths.show_json, {"title": "可能性褶皱 Possibility", "podcaster_names": ["哈梨Harriet"]})

    typst = build_front_matter_typst(
        paths=paths,
        front_matter={
            "book_title": "把褶皱活成纹理",
            "authors": ["哈梨", "fofo"],
        },
        chapters=[],
    )

    assert '#let book-author = "哈梨、fofo 著"' in typst
    assert "if phys <= 2 { return }" in typst
    assert "扉页和扉页背面" in typst


def test_front_matter_footer_uses_podcast_name_not_book_title(tmp_path):
    paths = JobPaths(job_id="demo", root=tmp_path)
    write_json(paths.show_json, {"title": "可能性褶皱 Possibility", "podcaster_names": ["哈梨Harriet"]})

    typst = build_front_matter_typst(
        paths=paths,
        front_matter={"book_title": "把褶皱活成纹理"},
        chapters=[],
    )

    assert "let title-text = text(font: (\"Noto Sans CJK SC\", \"Noto Sans SC\"), size: 8.5pt)[#podcast-name]" in typst


def test_resolve_book_authors_adds_hosts_from_chapters_over_stale_front_matter(tmp_path):
    paths = JobPaths(job_id="demo", root=tmp_path)
    write_json(paths.show_json, {"title": "可能性褶皱 Possibility", "podcaster_names": ["哈梨Harriet"]})
    ep_dir = paths.episode_dir(1)
    write_json(
        ep_dir / "chapter_for_book.json",
        {
            "chapter_index": 1,
            "host_name": "fofo、哈梨",
            "sections": [],
        },
    )

    authors = resolve_book_authors(paths, {"authors": ["哈梨Harriet"]})

    assert authors == ["哈梨", "fofo"]
