"""Smoke test for the local book-fit web app HTML."""
from __future__ import annotations

import tempfile
from pathlib import Path

from multi.podcast_book_fit.web_app import _maybe_generate_missing_pdf
from multi.podcast_book_fit.web_app import render_app_html


def main() -> None:
    test_missing_pdf_is_generated_from_neighbor_html()
    html = render_app_html()
    assert "<!doctype html>" in html.lower()
    assert "播客成书初判" in html
    assert "white-space:nowrap" in html
    assert "clamp(34px,4.8vw,64px)" in html
    assert "podcast-url" in html
    assert "/api/run" in html
    assert "开始生成" in html
    assert "window.location.href = task.files.html" in html
    assert "task.files.pdf" in html
    assert "下载 PDF" in html
    assert "window.print()" not in html
    print("[OK] web app HTML includes URL input and run endpoint")


def test_missing_pdf_is_generated_from_neighbor_html() -> None:
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        html_path = root / "book_fit_judgment.html"
        pdf_path = root / "book_fit_judgment.pdf"
        html_path.write_text("<!doctype html><html><body>PDF ok</body></html>", encoding="utf-8")

        def fake_renderer(source: Path, target: Path) -> bool:
            assert source == html_path
            target.write_bytes(b"%PDF-1.4\n% fake\n")
            return True

        assert _maybe_generate_missing_pdf(pdf_path, renderer=fake_renderer)
        assert pdf_path.exists()


if __name__ == "__main__":
    main()
