"""
merge_book_pdf — 把 workbench 单章 PDF 按 episodes_index 顺序合并成整书 PDF。

输入：
    storage/workbench/jobs/<job>/episodes_index.json
    storage/workbench/jobs/<job>/episodes/epNN/chapter_preview.pdf

输出：
    storage/workbench/jobs/<job>/merged/book_preview.pdf
    storage/workbench/jobs/<job>/merged/book_preview_manifest.json

用法：
    cd backend
    python -m workbench.runners.merge_book_pdf --job possibility
    python -m workbench.runners.merge_book_pdf --job possibility --copy-desktop

单集批处理里可用 --skip-if-incomplete：章节没齐时不报错，齐了自动产出整书。
"""
from __future__ import annotations

from multi.env import setup
setup()

import argparse
import logging
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from multi.io import JobPaths, read_json, slugify, write_json

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("merge_book_pdf")

DEFAULT_OUTPUT_NAME = "book_preview.pdf"


def _pdf_pages(path: Path) -> int | None:
    """用 pdfinfo 读取页数；工具不存在或读取失败时返回 None。"""
    pdfinfo = shutil.which("pdfinfo")
    if not pdfinfo:
        return None
    try:
        proc = subprocess.run(
            [pdfinfo, str(path)],
            check=True,
            capture_output=True,
            text=True,
        )
    except (subprocess.CalledProcessError, OSError):
        return None
    for line in proc.stdout.splitlines():
        if line.startswith("Pages:"):
            try:
                return int(line.split(":", 1)[1].strip())
            except ValueError:
                return None
    return None


def _create_blank_page_pdf(output_path: Path) -> Path:
    """编译一页空白 A5 PDF，用于章节间奇数页对齐。"""
    import typst as typst_lib
    from core.workflow.nodes.typeset import FONTS_DIR

    typ_source = "#set page(width: 154mm, height: 216mm, margin: 0mm)\n"
    typ_path = output_path.with_suffix(".typ")
    typ_path.write_text(typ_source, encoding="utf-8")
    typst_lib.compile(str(typ_path), output=str(output_path), font_paths=[FONTS_DIR])
    typ_path.unlink(missing_ok=True)
    return output_path


def _book_title(paths: JobPaths) -> str:
    front_matter_path = paths.merged_dir / "book_front_matter.json"
    if front_matter_path.exists():
        try:
            front_matter = read_json(front_matter_path)
            title = (front_matter.get("book_title") or "").strip()
            if title:
                return title
        except Exception:
            pass
    if not paths.show_json.exists():
        return paths.job_id
    try:
        show = read_json(paths.show_json)
    except Exception:
        return paths.job_id
    return (show.get("title") or paths.job_id).strip()


def _desktop_output_path(paths: JobPaths, total: int) -> Path:
    title = slugify(_book_title(paths), max_len=28)
    if title == "untitled":
        title = paths.job_id
    return Path.home() / "Desktop" / f"{title}_全书合集_{total}集.pdf"


def _expected_pdfs(paths: JobPaths, only: set[int] | None = None) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if not paths.episodes_index_json.exists():
        raise FileNotFoundError(f"未找到 episodes_index.json：{paths.episodes_index_json}")

    index = read_json(paths.episodes_index_json)
    expected: list[dict[str, Any]] = []
    missing: list[dict[str, Any]] = []

    for ep in index.get("episodes", []):
        idx = int(ep.get("index"))
        if only is not None and idx not in only:
            continue
        ep_dir = paths.episode_dir(idx)
        pdf = ep_dir / "chapter_preview.pdf"
        item = {
            "index": idx,
            "episode_dir": str(ep_dir),
            "pdf_path": str(pdf),
            "title": ep.get("title", ""),
        }
        if pdf.exists():
            expected.append(item)
        else:
            missing.append(item)

    return expected, missing


def _parse_only(only: str) -> set[int] | None:
    if not only:
        return None
    out: set[int] = set()
    for part in only.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            out.update(range(int(a), int(b) + 1))
        else:
            out.add(int(part))
    return out


def merge_book_pdf(
    *,
    paths: JobPaths,
    out_pdf: Path | None = None,
    only: set[int] | None = None,
    skip_if_incomplete: bool = False,
    copy_desktop: bool = False,
    renumber: bool = True,
    front_matter: bool = True,
    force_front_matter: bool = False,
) -> dict[str, Any]:
    """合并整书 PDF，返回 manifest。"""
    pdfunite = shutil.which("pdfunite")
    if not pdfunite:
        raise RuntimeError("未找到 pdfunite，请先安装 poppler（macOS: brew install poppler）")

    chapters, missing = _expected_pdfs(paths, only)
    total_expected = len(chapters) + len(missing)
    if missing:
        missing_labels = ", ".join(f"ep{m['index']:02d}" for m in missing)
        msg = f"章节 PDF 尚未齐全：缺少 {missing_labels}"
        if skip_if_incomplete:
            logger.info("%s；跳过整书合并", msg)
            return {
                "status": "skipped_incomplete",
                "missing": missing,
                "total_expected": total_expected,
                "available": len(chapters),
            }
        raise FileNotFoundError(msg)

    if not chapters:
        raise RuntimeError("没有可合并的 chapter_preview.pdf")

    if out_pdf is None:
        out_pdf = paths.merged_dir / DEFAULT_OUTPUT_NAME
    out_pdf = out_pdf.expanduser().resolve()
    out_pdf.parent.mkdir(parents=True, exist_ok=True)

    include_front_matter = front_matter and only is None
    input_paths = [Path(ch["pdf_path"]) for ch in chapters]

    last_chapter_page: int | None = None

    if renumber:
        from multi.runners.render_chapter_pdf import render_chapter_pdf

        ordered_inputs: list[Path] = []
        blank_pdf_path: Path | None = None

        page_start = 1
        for i, ch in enumerate(chapters):
            idx = int(ch["index"])
            pdf_path = Path(ch["pdf_path"])
            logger.info("[ep%02d] 校准整书连续页码：起始页 %d", idx, page_start)
            render_chapter_pdf(
                idx,
                Path(ch["episode_dir"]),
                out_pdf=pdf_path,
                page_start=page_start,
            )
            pages = _pdf_pages(pdf_path)
            ch["pages"] = pages
            ch["page_start"] = page_start
            ordered_inputs.append(pdf_path)
            if pages:
                ch["page_end"] = page_start + pages - 1
                page_start += pages
                # 章节标题页须落在奇数页；若下一章起始页为偶数，插入一页空白
                is_last = i == len(chapters) - 1
                if not is_last and page_start % 2 == 0:
                    if blank_pdf_path is None:
                        blank_pdf_path = out_pdf.parent / "_blank_page.pdf"
                        _create_blank_page_pdf(blank_pdf_path)
                    ordered_inputs.append(blank_pdf_path)
                    logger.info(
                        "[ep%02d] 插入空白页，下一章从奇数页 %d 开始",
                        idx, page_start + 1,
                    )
                    page_start += 1
            else:
                ch["page_end"] = None

        last_chapter_page = page_start - 1
        input_paths = ordered_inputs

    front_matter_path: Path | None = None
    if include_front_matter:
        from multi.runners.render_book_front_matter import render_book_front_matter

        front_matter_path = render_book_front_matter(
            paths=paths,
            chapters=chapters,
            force=force_front_matter,
        )
        input_paths = [front_matter_path, *input_paths]

    # 书末追加一张空白纸（印刷惯例）。
    # 一张纸有正反两面：
    #   末页为奇数（正面）→ 先补 1 页（背面）完成当前纸张，再加 2 页空白纸，共 3 页
    #   末页为偶数（背面）→ 当前纸张已完整，直接加 2 页空白纸，共 2 页
    trailing_blank = out_pdf.parent / "_blank_page.pdf"
    if not trailing_blank.exists():
        _create_blank_page_pdf(trailing_blank)
    if last_chapter_page is not None:
        trailing_count = 3 if last_chapter_page % 2 == 1 else 2
    else:
        trailing_count = 2
    logger.info("书末追加 %d 页空白（末章末页 %s）", trailing_count,
                f"第 {last_chapter_page} 页（奇数）" if last_chapter_page and last_chapter_page % 2 == 1
                else f"第 {last_chapter_page} 页（偶数）" if last_chapter_page else "未知")
    input_paths = [*input_paths, *([trailing_blank] * trailing_count)]

    subprocess.run(
        [pdfunite, *[str(p) for p in input_paths], str(out_pdf)],
        check=True,
    )

    for ch in chapters:
        pdf_path = Path(ch["pdf_path"])
        ch.setdefault("pages", _pdf_pages(pdf_path))
        ch["size_bytes"] = pdf_path.stat().st_size

    manifest: dict[str, Any] = {
        "status": "ok",
        "job_id": paths.job_id,
        "book_title": _book_title(paths),
        "output_pdf": str(out_pdf),
        "output_size_bytes": out_pdf.stat().st_size,
        "total_chapters": len(chapters),
        "total_pages": _pdf_pages(out_pdf),
        "front_matter_pdf": str(front_matter_path) if front_matter_path else "",
        "front_matter_pages": _pdf_pages(front_matter_path) if front_matter_path else 0,
        "chapters": chapters,
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }
    write_json(paths.merged_dir / "book_preview_manifest.json", manifest)

    logger.info(
        "✅ 整书 PDF 已合并：%d 章 · %s 页 · %.1f MB → %s",
        len(chapters),
        manifest.get("total_pages") or "?",
        manifest["output_size_bytes"] / 1024 / 1024,
        out_pdf,
    )

    if copy_desktop:
        desktop_path = _desktop_output_path(paths, len(chapters))
        shutil.copy2(out_pdf, desktop_path)
        manifest["desktop_pdf"] = str(desktop_path)
        write_json(paths.merged_dir / "book_preview_manifest.json", manifest)
        logger.info("📄 整书 PDF 已复制到桌面 → %s", desktop_path)

    return manifest


def main() -> None:
    p = argparse.ArgumentParser(description="合并 workbench 单章 PDF 为整书 PDF")
    p.add_argument("--job", required=True, help="作业 ID")
    p.add_argument("--only", default="", help="只合并指定集，例：1,5,11 或 1-5；默认按 episodes_index 全部合并")
    p.add_argument("--out", default="", help="输出 PDF 路径；默认 merged/book_preview.pdf")
    p.add_argument("--copy-desktop", action="store_true", help="同时复制整书 PDF 到桌面")
    p.add_argument("--no-renumber", action="store_true", help="不在合并前重渲染连续页码（调试用）")
    p.add_argument("--no-front-matter", action="store_true", help="不生成/合并序言和目录前置页")
    p.add_argument("--force-front-matter", action="store_true", help="强制重写序言文本并重新渲染前置页")
    p.add_argument(
        "--skip-if-incomplete",
        action="store_true",
        help="章节 PDF 未齐时退出码仍为 0，适合单集批处理结束后自动尝试",
    )
    args = p.parse_args()

    paths = JobPaths.open(args.job)
    out_pdf = Path(args.out) if args.out else None
    manifest = merge_book_pdf(
        paths=paths,
        out_pdf=out_pdf,
        only=_parse_only(args.only),
        skip_if_incomplete=args.skip_if_incomplete,
        copy_desktop=args.copy_desktop,
        renumber=not args.no_renumber,
        front_matter=not args.no_front_matter,
        force_front_matter=args.force_front_matter,
    )
    if manifest.get("status") == "skipped_incomplete":
        return


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logger.warning("用户中断")
        sys.exit(130)
