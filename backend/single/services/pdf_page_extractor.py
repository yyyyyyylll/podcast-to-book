"""
从排版后的 PDF 中提取关键页面渲染为 PNG，供小红书等平台直接使用。

固定提取三页：
  1. 精华提要第一页
  2. 目录第一页
  3. 第二章第一页

额外随机抽取 4 页正文页面，供运营人员选择。

使用 PyMuPDF (fitz) 按文本标记定位页码，3x 分辨率渲染。
"""

import logging
import random
from pathlib import Path
from typing import Dict, List, Optional, Set

import fitz  # pymupdf

logger = logging.getLogger(__name__)

DPI_SCALE = 3.0
RANDOM_BODY_COUNT = 4
MIN_TEXT_LENGTH = 200  # 正文页至少包含这么多字符，排除空白/装饰页

PAGE_SPECS: List[Dict[str, str]] = [
    {"key": "highlights", "marker": "精华提要", "filename": "page_highlights.png"},
    {"key": "toc",        "marker": "目 录",    "filename": "page_toc.png"},
]

SKIP_MARKERS = ("精华提要", "目 录", "CHAPTER", "C H A P T E R",
                "C\u2003H\u2003A\u2003P\u2003T\u2003E\u2003R")


def _find_page_by_text(doc: fitz.Document, marker: str) -> Optional[int]:
    """找到包含指定标记文本的第一个页面索引。"""
    for i in range(doc.page_count):
        text = doc[i].get_text()
        if marker in text:
            return i
    return None


def _find_chapter2_page(doc: fitz.Document) -> Optional[int]:
    """找到第二章首页（第二个含章节标题的页面）。
    Typst 渲染后 CHAPTER 可能带有字母间距，需要匹配 'C H A P T E R' 等变体。
    """
    chapter_markers = ("CHAPTER", "C H A P T E R", "C\u2003H\u2003A\u2003P\u2003T\u2003E\u2003R")
    count = 0
    for i in range(doc.page_count):
        text = doc[i].get_text()
        if any(m in text for m in chapter_markers):
            count += 1
            if count == 2:
                return i
    return None


def _render_page(doc: fitz.Document, page_idx: int, output_path: Path) -> bool:
    """将指定页面渲染为高分辨率 PNG。"""
    try:
        page = doc[page_idx]
        mat = fitz.Matrix(DPI_SCALE, DPI_SCALE)
        pix = page.get_pixmap(matrix=mat, alpha=False)
        pix.save(str(output_path))
        logger.info(
            "PDF 页面 %d → %s (%dx%d)",
            page_idx + 1, output_path.name, pix.width, pix.height,
        )
        return True
    except Exception as e:
        logger.warning("渲染 PDF 页面 %d 失败: %s", page_idx + 1, e)
        return False


def _find_body_page_candidates(doc: fitz.Document, exclude: Set[int]) -> List[int]:
    """找出所有正文页面的索引（排除封面、目录、章节标题页、空白页等）。"""
    candidates = []
    for i in range(doc.page_count):
        if i in exclude:
            continue
        text = doc[i].get_text()
        if len(text.strip()) < MIN_TEXT_LENGTH:
            continue
        if any(m in text for m in SKIP_MARKERS):
            continue
        candidates.append(i)
    return candidates


def extract_key_pages(
    pdf_path: str,
    output_dir: str,
    *,
    random_body_count: int = RANDOM_BODY_COUNT,
) -> Dict[str, str]:
    """
    从 PDF 中提取关键页面，保存为 PNG。

    固定页面：精华提要、目录、第二章首页。
    随机页面：从正文区域随机抽取 random_body_count 页。

    返回 {key: filename} 映射，例如：
      {"highlights": "page_highlights.png", "toc": "page_toc.png",
       "chapter2": "page_chapter2.png",
       "body_01": "page_body_01.png", ...}
    """
    pdf_file = Path(pdf_path)
    if not pdf_file.exists():
        logger.warning("PDF 文件不存在: %s", pdf_path)
        return {}

    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    doc = fitz.open(str(pdf_file))
    result: Dict[str, str] = {}
    used_pages: Set[int] = set()

    # --- 固定页面 ---
    for spec in PAGE_SPECS:
        idx = _find_page_by_text(doc, spec["marker"])
        if idx is not None:
            dest = out / spec["filename"]
            if _render_page(doc, idx, dest):
                result[spec["key"]] = spec["filename"]
                used_pages.add(idx)
        else:
            logger.info("未找到包含 '%s' 的页面，跳过", spec["marker"])

    ch2_idx = _find_chapter2_page(doc)
    if ch2_idx is not None:
        dest = out / "page_chapter2.png"
        if _render_page(doc, ch2_idx, dest):
            result["chapter2"] = "page_chapter2.png"
            used_pages.add(ch2_idx)
    else:
        logger.info("未找到第二章首页，跳过")

    # --- 随机正文页面 ---
    candidates = _find_body_page_candidates(doc, used_pages)
    pick_count = min(random_body_count, len(candidates))
    if pick_count > 0:
        chosen = sorted(random.sample(candidates, pick_count))
        for seq, page_idx in enumerate(chosen, start=1):
            filename = f"page_body_{seq:02d}.png"
            dest = out / filename
            if _render_page(doc, page_idx, dest):
                result[f"body_{seq:02d}"] = filename
    else:
        logger.info("未找到足够的正文页面用于随机抽取")

    doc.close()

    if result:
        print(f"[pdf_extractor] 已提取 {len(result)} 个页面: {list(result.keys())}")
    else:
        print("[pdf_extractor] 未提取到任何页面")

    return result
