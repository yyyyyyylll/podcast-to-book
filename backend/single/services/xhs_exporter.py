"""
将 pod_to_book 完成的任务素材导出到 xhs-agent 的 content/ 目录。

xhs-agent 期望的目录结构：
    content/{episode_id}/
        transcript.md        — 主文本素材
        illustrations/       — 配图（jpg/png/webp）
"""

import logging
import shutil
from pathlib import Path

from sqlalchemy import select

from core.config import settings
from single.database import async_session
from single.models.task import Task

logger = logging.getLogger(__name__)

SUPPORTED_IMG_EXT = {".jpg", ".jpeg", ".png", ".webp"}


async def export_to_xhs_agent(task_id: str) -> str | None:
    """
    将已完成任务的素材导出到 xhs-agent 的 content/ 目录。
    返回 episode_id（目录名），若未配置目标目录则返回 None。
    """
    content_dir = settings.XHS_AGENT_CONTENT_DIR
    if not content_dir:
        return None

    content_path = Path(content_dir)
    if not content_path.is_dir():
        logger.warning("XHS_AGENT_CONTENT_DIR 不存在: %s", content_dir)
        return None

    async with async_session() as session:
        result = await session.execute(select(Task).where(Task.id == task_id))
        task = result.scalar_one_or_none()

    if not task or task.status != "completed":
        logger.warning("任务 %s 不存在或未完成，跳过导出", task_id)
        return None

    episode_id = _make_episode_id(task)
    ep_dir = content_path / episode_id

    if ep_dir.exists():
        logger.info("episode 目录已存在，跳过: %s", ep_dir)
        return episode_id

    ep_dir.mkdir(parents=True)

    transcript = _build_transcript(task)
    (ep_dir / "transcript.md").write_text(transcript, encoding="utf-8")

    _copy_illustrations(task, ep_dir)
    _extract_pdf_key_pages(task, ep_dir)

    logger.info("已导出到 xhs-agent: %s (%d chars)", episode_id, len(transcript))
    return episode_id


def _make_episode_id(task: Task) -> str:
    """生成 episode 目录名：播客名缩写 + task_id 前 8 位。"""
    podcast = task.podcast_name or "EP"
    initials = "".join(c for c in podcast if not c.isspace())[:6]
    short_id = task.id[:8]
    return f"{initials}_{short_id}"


def _build_transcript(task: Task) -> str:
    """将全量素材格式化为 xhs-agent 可消费的 Markdown。"""
    composed = task.result_composed or {}
    highlights = task.result_highlights or {}
    editor_preface = task.result_editor_preface or ""

    title = composed.get("title") or task.title
    core_theme = composed.get("core_theme", "")
    keywords = composed.get("theme_keywords", [])
    preamble = composed.get("preamble", {})
    summary_bullets = preamble.get("summary_bullets", [])
    chapters = composed.get("chapters", [])
    quotes = highlights.get("quotes", [])

    sections = []

    # 标题与元信息
    header = f"# {title}"
    meta_parts = []
    if task.podcast_name:
        meta_parts.append(f"来源：播客「{task.podcast_name}」")
    host_guest = []
    if task.host_name:
        host_guest.append(f"主持：{task.host_name}")
    guests = task.guest_names or []
    if guests:
        host_guest.append(f"嘉宾：{'、'.join(guests)}")
    if host_guest:
        meta_parts.append(" | ".join(host_guest))
    if core_theme:
        meta_parts.append(f"核心主题：{core_theme}")
    if keywords:
        meta_parts.append(f"关键词：{'、'.join(keywords)}")

    sections.append(header)
    if meta_parts:
        sections.append("\n".join(meta_parts))

    # 编者序
    if editor_preface:
        sections.append("---\n\n## 编者序\n\n" + editor_preface.strip())

    # 精华提要
    if summary_bullets:
        bullet_text = "\n".join(f"- {b}" for b in summary_bullets)
        sections.append("---\n\n## 精华提要\n\n" + bullet_text)

    # 金句
    if quotes:
        quote_lines = []
        for q in quotes:
            text = q.get("text", "")
            speaker = q.get("speaker", "")
            if speaker and speaker not in text:
                quote_lines.append(f"> {text} —— {speaker}")
            else:
                quote_lines.append(f"> {text}")
        sections.append("---\n\n## 金句\n\n" + "\n\n".join(quote_lines))

    # 章节正文
    for ch in chapters:
        ch_title = ch.get("title", "")
        ch_content = ch.get("content", "")
        if ch_title and ch_content:
            sections.append(f"---\n\n## {ch_title}\n\n{ch_content}")

    return "\n\n".join(sections) + "\n"


def _copy_illustrations(task: Task, ep_dir: Path):
    """将通过审核的插图和封面复制到 episode 目录。"""
    task_storage = Path(settings.STORAGE_DIR) / task.id

    # 复制封面
    cover = task_storage / "cover_rendered.png"
    if cover.exists():
        ill_dir = ep_dir / "illustrations"
        ill_dir.mkdir(exist_ok=True)
        shutil.copy2(cover, ill_dir / "cover.png")

    # 复制正文插图
    illustrations = task.result_illustrations or {}
    images = illustrations.get("images", [])
    if not images:
        return

    ill_dir = ep_dir / "illustrations"
    ill_dir.mkdir(exist_ok=True)

    for img in images:
        filename = img.get("filename", "")
        if not filename:
            continue
        src = task_storage / filename
        if src.exists() and src.suffix.lower() in SUPPORTED_IMG_EXT:
            shutil.copy2(src, ill_dir / src.name)


def _extract_pdf_key_pages(task: Task, ep_dir: Path):
    """从 PDF 中提取精华提要、目录、第二章首页作为小红书配图。"""
    pdf_path = task.pdf_path
    if not pdf_path or not Path(pdf_path).exists():
        return

    try:
        from single.services.pdf_page_extractor import extract_key_pages
    except ImportError:
        logger.warning("pymupdf 未安装，跳过 PDF 关键页提取")
        return

    ill_dir = ep_dir / "illustrations"
    ill_dir.mkdir(exist_ok=True)

    try:
        result = extract_key_pages(pdf_path, str(ill_dir))
        if result:
            logger.info("PDF 关键页已提取: %s", list(result.keys()))
    except Exception as e:
        logger.warning("PDF 关键页提取失败: %s", e)
