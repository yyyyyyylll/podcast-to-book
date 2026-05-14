import os
import json
import base64
from pathlib import Path
from typing import Dict, Any, List, Optional
from jinja2 import Template

from functools import lru_cache

COVERS_DIR = Path(__file__).resolve().parent.parent / "workflow" / "knowledge" / "covers"
REGISTRY_PATH = COVERS_DIR / "registry.json"

DEFAULT_STYLE = "classic"

COVER_TEXT_LIMITS = {
    "title": 50,
    "author_line": 30,
    "series_label": 25,
}


def _truncate_text(text: str, max_chars: int) -> str:
    """按中文字符计数截断（英文字母算 1/3 字符）。"""
    if not text:
        return text
    count = 0.0
    for i, ch in enumerate(text):
        count += 1/3 if ch.isascii() and ch.isalpha() else 1
        if count > max_chars:
            return text[:i] + "…"
    return text


def _get_registry() -> list:
    if not REGISTRY_PATH.exists():
        return []
    with open(REGISTRY_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def get_available_styles() -> list:
    """获取所有可用的封面风格。"""
    return _get_registry()


def _get_style_info(style_id: str) -> Optional[Dict[str, Any]]:
    registry = _get_registry()
    for s in registry:
        if s["id"] == style_id:
            return s
    return None


def _image_to_data_uri(filepath: str) -> str:
    """将本地图片文件转换为 Base64 Data URI，避免 Playwright 的跨域/本地文件读取限制。"""
    if not filepath:
        return ""
    if filepath.startswith("http://") or filepath.startswith("https://"):
        return filepath
    
    path = Path(filepath)
    if not path.exists():
        return ""
        
    ext = path.suffix.lower()
    mime_type = "image/jpeg"
    if ext in [".png"]:
        mime_type = "image/png"
    elif ext in [".webp"]:
        mime_type = "image/webp"
        
    try:
        with open(path, "rb") as f:
            encoded = base64.b64encode(f.read()).decode("utf-8")
        return f"data:{mime_type};base64,{encoded}"
    except Exception as e:
        print(f"[CoverService] 无法读取图片 {filepath}: {e}")
        return ""


@lru_cache(maxsize=1)
def _resolve_chromium_path() -> Optional[str]:
    """优先使用环境变量，否则自动检测 Playwright 安装的 Chromium。"""
    env_path = os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH")
    if env_path and Path(env_path).exists():
        return env_path
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            detected = p.chromium.executable_path
            if detected and Path(detected).exists():
                print(f"[CoverService] 自动检测 Chromium: {detected}")
                return detected
    except Exception:
        pass
    return None


async def render_cover(
    style: str,
    variables: Dict[str, Any],
    output_path: str,
) -> str:
    """
    使用 Playwright 将指定的 HTML 封面模板渲染为图片。
    
    :param style: 风格 ID (例如 "swiss", "wabisabi", "newwave", "artdeco")
    :param variables: 模板变量
    :param output_path: 输出 PNG 文件的路径
    :return: 渲染后的图片路径
    """
    from playwright.async_api import async_playwright
    
    style_info = _get_style_info(style)
    if not style_info:
        print(f"[CoverService] 找不到风格 {style}，使用默认 {DEFAULT_STYLE}")
        style = DEFAULT_STYLE
        style_info = _get_style_info(style)
        if not style_info:
            raise ValueError(f"封面风格 {style} 配置不存在")
            
    template_file = COVERS_DIR / style_info["file"]
    if not template_file.exists():
        raise FileNotFoundError(f"模板文件缺失: {template_file}")
        
    with open(template_file, "r", encoding="utf-8") as f:
        template_content = f.read()
        
    # 处理颜色默认值
    default_colors = style_info.get("default_colors", {})
    render_vars = {
        "title": _truncate_text(variables.get("title", ""), COVER_TEXT_LIMITS["title"]),
        "subtitle": (variables.get("subtitle", "") or "").strip(),
        "author_line_1": _truncate_text(variables.get("author_line_1", ""), COVER_TEXT_LIMITS["author_line"]),
        "author_line_2": _truncate_text(variables.get("author_line_2", ""), COVER_TEXT_LIMITS["author_line"]),
        "series_label": _truncate_text(variables.get("series_label", "播客书稿 · 典藏系列"), COVER_TEXT_LIMITS["series_label"]),
        "primary_color": variables.get("primary_color") or default_colors.get("primary", "#5C1A1B"),
        "accent_color": variables.get("accent_color") or default_colors.get("accent", "#D4AF37"),
    }
    
    # 处理图片转 Base64
    cover_image_path = variables.get("cover_image", "")
    render_vars["cover_image"] = _image_to_data_uri(cover_image_path)
    
    # 渲染 HTML
    template = Template(template_content)
    html_content = template.render(**render_vars)
    
    chromium_path = _resolve_chromium_path()
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            executable_path=chromium_path,
        )
        page = await browser.new_page(
            viewport={"width": 528, "height": 741},
            device_scale_factor=3,
        )
        await page.set_content(html_content, wait_until="commit", timeout=30000)
        try:
            await page.wait_for_load_state("domcontentloaded", timeout=8000)
        except Exception:
            pass
        await page.wait_for_timeout(2000)
        await page.screenshot(path=output_path, full_page=True, type="png")
        await browser.close()
        
    print(f"[CoverService] 封面渲染完成: {output_path}")
    return output_path


# ================================================================
# 封底（Back Cover）渲染
# ================================================================

_BACK_COVER_BG = {
    "swiss": "#fafaf8",
    "wabisabi": "#ece6db",
    "newwave": "#f0f0f0",
    "artdeco": "#181828",
    "classic": "#f4f1ea",
    "emboss": "#f5f3f0",
    "deepblue": "#1a2a5e",
    "darklit": "#f0ede8",
    "pastoral": "#ec788a",
    "stripe": "#f0f2f5",
    "mono": "#f5f3f0",
    "vertright": "#fafafa",
    "circlemotif": "#8DB55A",
    "halfcover": "#ebe7e0",
    "halftone": "#f8f6f2",
}


async def render_back_cover(
    style: str,
    output_path: str,
) -> str:
    """
    渲染封底图片：保留封面风格的背景色与装饰元素，不包含任何文字内容。

    :param style: 风格 ID (例如 "swiss", "wabisabi", "newwave", "artdeco")
    :param output_path: 输出 PNG 文件的路径
    :return: 渲染后的图片路径
    """
    from playwright.async_api import async_playwright

    bg_color = _BACK_COVER_BG.get(style)
    if not bg_color:
        style_info = _get_style_info(style)
        bg_color = style_info.get("default_colors", {}).get("primary", "#ffffff") if style_info else "#ffffff"
    html_content = (
        f'<!DOCTYPE html><html><head><meta charset="UTF-8"><style>'
        f'body {{ margin:0; padding:0; width:528px; height:741px; background:{bg_color}; }}'
        f'</style></head><body></body></html>'
    )

    chromium_path = _resolve_chromium_path()
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            executable_path=chromium_path,
        )
        page = await browser.new_page(
            viewport={"width": 528, "height": 741},
            device_scale_factor=3,
        )
        await page.set_content(html_content, wait_until="commit", timeout=30000)
        try:
            await page.wait_for_load_state("domcontentloaded", timeout=5000)
        except Exception:
            pass
        await page.wait_for_timeout(500)
        await page.screenshot(path=output_path, full_page=True, type="png")
        await browser.close()

    print(f"[CoverService] 封底渲染完成: {output_path}")
    return output_path


# ================================================================
# 封面预览缩略图（批量渲染）
# ================================================================

async def render_cover_previews(
    variables: Dict[str, Any],
    output_dir: str,
) -> Dict[str, str]:
    """
    批量渲染所有封面模板的预览缩略图，复用同一浏览器实例。

    使用低 DPR (device_scale_factor=1) 生成 528x741 的缩略图，
    比正式渲染 (3x) 体积更小、速度更快。

    :param variables: 封面模板变量 (title, subtitle, author_line_1, etc.)
    :param output_dir: 任务存储目录 (storage/{task_id})
    :return: {style_id: "cover_previews/{style_id}.png"} 映射
    """
    from playwright.async_api import async_playwright

    previews_dir = Path(output_dir) / "cover_previews"
    previews_dir.mkdir(parents=True, exist_ok=True)

    styles = get_available_styles()
    if not styles:
        return {}

    cover_image_path = variables.get("cover_image", "")
    cover_image_data_uri = _image_to_data_uri(cover_image_path)

    result_map: Dict[str, str] = {}
    chromium_path = _resolve_chromium_path()

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            executable_path=chromium_path,
        )

        for style_info in styles:
            style_id = style_info.get("id", "")
            if not style_id:
                continue

            try:
                template_file = COVERS_DIR / style_info["file"]
                if not template_file.exists():
                    continue

                with open(template_file, "r", encoding="utf-8") as f:
                    template_content = f.read()

                default_colors = style_info.get("default_colors", {})
                render_vars = {
                    "title": _truncate_text(variables.get("title", ""), COVER_TEXT_LIMITS["title"]),
                    "subtitle": (variables.get("subtitle", "") or "").strip(),
                    "author_line_1": _truncate_text(variables.get("author_line_1", ""), COVER_TEXT_LIMITS["author_line"]),
                    "author_line_2": _truncate_text(variables.get("author_line_2", ""), COVER_TEXT_LIMITS["author_line"]),
                    "series_label": _truncate_text(variables.get("series_label", "播客书稿 · 典藏系列"), COVER_TEXT_LIMITS["series_label"]),
                    "primary_color": variables.get("primary_color") or default_colors.get("primary", "#5C1A1B"),
                    "accent_color": variables.get("accent_color") or default_colors.get("accent", "#D4AF37"),
                    "cover_image": cover_image_data_uri,
                }

                template = Template(template_content)
                html_content = template.render(**render_vars)

                page = await browser.new_page(
                    viewport={"width": 528, "height": 741},
                    device_scale_factor=1,
                )
                await page.set_content(html_content, wait_until="commit", timeout=15000)
                try:
                    await page.wait_for_load_state("domcontentloaded", timeout=5000)
                except Exception:
                    pass
                await page.wait_for_timeout(800)

                out_path = str(previews_dir / f"{style_id}.png")
                await page.screenshot(path=out_path, full_page=True, type="png")
                await page.close()

                result_map[style_id] = f"cover_previews/{style_id}.png"
            except Exception as e:
                print(f"[CoverService] 预览渲染失败 ({style_id}): {e}")
                continue

        await browser.close()

    print(f"[CoverService] 封面预览批量渲染完成: {len(result_map)}/{len(styles)} 个")
    return result_map
