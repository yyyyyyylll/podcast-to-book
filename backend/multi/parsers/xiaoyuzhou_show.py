"""
小宇宙 FM 栏目页（/podcast/<pid>）解析

栏目页 SSR 的 __NEXT_DATA__.props.pageProps.podcast 里包含完整节目元数据，
但 episodes 字段是空数组（客户端懒加载），所以用正则从 HTML 文本里直接抓
所有 /episode/<eid> 路径，得到完整单集列表。
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
import re
from dataclasses import dataclass, field
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_SHOW_URL_RE = re.compile(r"^https?://(?:www\.)?xiaoyuzhoufm\.com/podcast/([a-f0-9]+)")
_NEXT_DATA_RE = re.compile(r"__NEXT_DATA__.*?>(.*?)</script>", re.DOTALL)
_EPISODE_HREF_RE = re.compile(r"/episode/([a-zA-Z0-9]+)")

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Referer": "https://www.xiaoyuzhoufm.com/",
}


@dataclass
class ShowMetadata:
    pid: str
    show_url: str
    title: str
    brief: str                         # 一句话标语（"愿读书让我们褶皱出..."）
    description: str                   # 完整节目详情（含末尾联系方式）
    description_clean: str             # 节目详情，已切除末尾的 / 联系方式块
    cover_url: str
    cover_width: int
    cover_height: int
    color_original: str                # 封面提取的主题色（hex）
    color_light: str                   # 浅色调（hex）
    color_dark: str                    # 深色调（hex）
    author_label: str                  # podcast.author（常常是"佚名"，仅作兜底）
    podcaster_names: list[str]         # podcasters[].nickname（更可靠）
    podcaster_uids: list[str]
    podcaster_avatars: list[str]       # 主播头像 URL
    contacts: list[dict[str, str]]     # [{type, name, url?, note?}, ...]
    subscription_count: int
    episode_count: int
    pay_type: str                      # FREE / PAID
    pay_episode_count: int
    has_popular_episodes: bool
    play_time_total_seconds: int       # 全部单集累计被播放秒数
    latest_pub_date: str
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class ShowParseResult:
    show: ShowMetadata
    episode_ids: list[str]             # 页面顺序 = 最新→最旧
    episode_urls: list[str]            # https://www.xiaoyuzhoufm.com/episode/<eid>


# 节目详情末尾的联系方式块识别：以 / 开头的连续行（"/小红书"、"/商务合作"、"/邮箱"等）
_CONTACT_BLOCK_RE = re.compile(r"\n+/[^\n]*(?:\n/[^\n]*)*\s*$")


def clean_show_description(desc: str) -> str:
    """切除节目详情末尾的 / 联系方式块，保留纯净的节目介绍正文。"""
    if not desc:
        return ""
    cleaned = _CONTACT_BLOCK_RE.sub("", desc).strip()
    return cleaned or desc.strip()


def is_show_url(url: str) -> bool:
    return bool(_SHOW_URL_RE.match(url or ""))


async def fetch_show_html(url: str, *, max_attempts: int = 5) -> str:
    """拉取栏目页 HTML，带 403/429 退避重试。"""
    last_error: Exception | None = None
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        for attempt in range(max_attempts):
            try:
                resp = await client.get(url, headers=_HEADERS)
                if resp.status_code in (403, 429) and attempt < max_attempts - 1:
                    delay = 2 ** attempt + random.random()
                    logger.warning(
                        "小宇宙栏目页 %s，第 %d 次重试，等待 %.1fs",
                        resp.status_code, attempt + 1, delay,
                    )
                    await asyncio.sleep(delay)
                    continue
                resp.raise_for_status()
                return resp.text
            except (httpx.TimeoutException, httpx.HTTPError) as e:
                last_error = e
                if attempt < max_attempts - 1:
                    await asyncio.sleep(0.8 * (attempt + 1))
    raise RuntimeError(f"拉取小宇宙栏目页失败: {url}") from last_error


def parse_show_html(html: str, *, show_url: str) -> ShowParseResult:
    m = _NEXT_DATA_RE.search(html)
    if not m:
        raise RuntimeError("无法从栏目页提取 __NEXT_DATA__")

    data = json.loads(m.group(1))
    pod = (
        data.get("props", {})
            .get("pageProps", {})
            .get("podcast", {})
    )
    if not pod:
        raise RuntimeError("__NEXT_DATA__ 中找不到 podcast 字段")

    pid_match = _SHOW_URL_RE.match(show_url)
    pid = pid_match.group(1) if pid_match else pod.get("pid", "")

    image = pod.get("image") or {}
    cover_url = ""
    cover_width = 0
    cover_height = 0
    if isinstance(image, dict):
        cover_url = (
            image.get("picUrl")
            or image.get("largePicUrl")
            or image.get("middlePicUrl")
            or ""
        )
        cover_width = int(image.get("width") or 0)
        cover_height = int(image.get("height") or 0)

    color = pod.get("color") or {}
    if not isinstance(color, dict):
        color = {}

    podcasters = pod.get("podcasters") or []
    podcaster_names: list[str] = []
    podcaster_uids: list[str] = []
    podcaster_avatars: list[str] = []
    for p in podcasters:
        if p.get("nickname"):
            podcaster_names.append(p["nickname"])
        if p.get("uid"):
            podcaster_uids.append(p["uid"])
        avatar = p.get("avatar") or {}
        pic = avatar.get("picture") or {} if isinstance(avatar, dict) else {}
        avatar_url = (
            pic.get("picUrl")
            or pic.get("middlePicUrl")
            or pic.get("smallPicUrl")
            or ""
        ) if isinstance(pic, dict) else ""
        if avatar_url:
            podcaster_avatars.append(avatar_url)

    raw_contacts = pod.get("contacts") or []
    contacts: list[dict[str, str]] = []
    for c in raw_contacts:
        if not isinstance(c, dict):
            continue
        contacts.append({
            "type": str(c.get("type", "") or ""),
            "name": str(c.get("name", "") or ""),
            "url": str(c.get("url", "") or ""),
            "note": str(c.get("note", "") or ""),
        })

    play_time_ms = int(pod.get("playTime") or 0)

    desc_full = pod.get("description", "") or ""
    desc_clean = clean_show_description(desc_full)

    show = ShowMetadata(
        pid=pid,
        show_url=show_url,
        title=pod.get("title", "") or "",
        brief=pod.get("brief", "") or "",
        description=desc_full,
        description_clean=desc_clean,
        cover_url=cover_url,
        cover_width=cover_width,
        cover_height=cover_height,
        color_original=str(color.get("original", "") or ""),
        color_light=str(color.get("light", "") or ""),
        color_dark=str(color.get("dark", "") or ""),
        author_label=pod.get("author", "") or "",
        podcaster_names=podcaster_names,
        podcaster_uids=podcaster_uids,
        podcaster_avatars=podcaster_avatars,
        contacts=contacts,
        subscription_count=int(pod.get("subscriptionCount") or 0),
        episode_count=int(pod.get("episodeCount") or 0),
        pay_type=str(pod.get("payType", "") or ""),
        pay_episode_count=int(pod.get("payEpisodeCount") or 0),
        has_popular_episodes=bool(pod.get("hasPopularEpisodes") or False),
        play_time_total_seconds=play_time_ms // 1000 if play_time_ms else 0,
        latest_pub_date=str(pod.get("latestEpisodePubDate", "") or ""),
        raw=pod,
    )

    # SSR 里 podcast.episodes 通常是空数组，从 HTML 文本里抓所有 /episode/<eid>
    episode_ids: list[str] = []
    seen: set[str] = set()
    for eid in _EPISODE_HREF_RE.findall(html):
        if eid not in seen:
            seen.add(eid)
            episode_ids.append(eid)

    if not episode_ids:
        raise RuntimeError("HTML 中未找到任何 /episode/<eid>，可能页面结构变更")

    if show.episode_count and abs(len(episode_ids) - show.episode_count) > 2:
        logger.warning(
            "抓到的单集数 (%d) 与节目元数据 episode_count (%d) 不一致，请确认是否分页加载",
            len(episode_ids), show.episode_count,
        )

    episode_urls = [f"https://www.xiaoyuzhoufm.com/episode/{eid}" for eid in episode_ids]

    return ShowParseResult(show=show, episode_ids=episode_ids, episode_urls=episode_urls)


async def parse_show(url: str) -> ShowParseResult:
    """高层入口：URL → ShowParseResult"""
    if not is_show_url(url):
        raise ValueError(
            f"不是合法的小宇宙栏目链接（应形如 https://www.xiaoyuzhoufm.com/podcast/<pid>）: {url}"
        )
    html = await fetch_show_html(url)
    return parse_show_html(html, show_url=url)
