"""一次性定制封面渲染：拼团吃谷（69634795f8b05f9f75f0b282）。

在 classic 模板基础上做以下定制（仅此一期）：
1. 跳过 cover_service._truncate_text 的 30 字符硬截断；
2. 为 author_line_2 启用多行排版（white-space: normal + text-align: center）；
3. 主播行保持 classic 原有样式（单行、13px、letter-spacing: 3px）；
4. 制作组行采用略小字号（11.5px）+ 紧凑 letter-spacing，允许自动换行。

运行：
    ./backend/venv/bin/python backend/tests/custom_cover_chigu.py
"""
from __future__ import annotations

import asyncio
import base64
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from jinja2 import Template

TEMPLATE_PATH = ROOT / "backend/app/workflow/knowledge/covers/classic.html"
STORAGE_DIR = ROOT / "backend/storage/69634795f8b05f9f75f0b282"
COVER_IMAGE = STORAGE_DIR / "cover.png"
OUTPUT_PATH = STORAGE_DIR / "cover_rendered.png"

VARS = {
    "title": "拼团吃谷",
    "subtitle": "规矩里的热爱",
    "series_label": "青年消费图鉴 · 典藏系列",
    "author_line_1": "主播 / 泡泡鱼、下大雨",
    # 第二行允许 HTML：手动用 <br> 把 7 个名字拆成 4+3，视觉对称
    "author_line_2": "制作 / 谢歆蕾、李雨晔、叶冠豪、宋天祺<br>陈子妍、李思佳、黄志恩",
}

CUSTOM_CSS = """
/* === 定制：允许 author_line_2 多行排版 === */
.author-line-multi {
  font-size: 12px; color: #333;
  letter-spacing: 2.5px; line-height: 1.9;
  margin-top: 8px; flex-shrink: 0;
  width: 100%;
  text-align: center;
  white-space: normal;
  word-break: keep-all;
}
"""


def _image_to_data_uri(path: Path) -> str:
    data = path.read_bytes()
    ext = path.suffix.lower().lstrip(".") or "png"
    mime = "image/jpeg" if ext in ("jpg", "jpeg") else f"image/{ext}"
    b64 = base64.b64encode(data).decode("ascii")
    return f"data:{mime};base64,{b64}"


def build_html() -> str:
    src = TEMPLATE_PATH.read_text(encoding="utf-8")
    src = src.replace("</style>", CUSTOM_CSS + "</style>", 1)
    src = src.replace(
        '{% if author_line_2 %}<div class="author-line">{{author_line_2}}</div>{% endif %}',
        '{% if author_line_2 %}<div class="author-line-multi">{{author_line_2}}</div>{% endif %}',
        1,
    )
    script_off = "document.querySelectorAll('.author-line, .series').forEach(function(el){\n    fitInline(el, 6);\n  });"
    src = src.replace(script_off, "document.querySelectorAll('.series').forEach(function(el){ fitInline(el, 6); });\n  document.querySelectorAll('.author-line').forEach(function(el){ fitInline(el, 8); });")
    render_vars = dict(VARS)
    render_vars["cover_image"] = _image_to_data_uri(COVER_IMAGE)
    return Template(src).render(**render_vars)


async def main():
    from playwright.async_api import async_playwright
    from core.services.cover_service import _resolve_chromium_path

    html = build_html()
    chromium_path = _resolve_chromium_path()

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, executable_path=chromium_path)
        page = await browser.new_page(
            viewport={"width": 528, "height": 741},
            device_scale_factor=3,
        )
        await page.set_content(html, wait_until="commit", timeout=30000)
        try:
            await page.wait_for_load_state("domcontentloaded", timeout=8000)
        except Exception:
            pass
        await page.wait_for_timeout(2000)
        await page.screenshot(path=str(OUTPUT_PATH), full_page=True, type="png")
        await browser.close()

    print(f"[ok] 封面已写出: {OUTPUT_PATH}")


if __name__ == "__main__":
    asyncio.run(main())
