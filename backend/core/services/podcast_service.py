"""
播客 URL 解析服务

从播客平台链接中提取音频文件 URL 和元数据。
当前支持：小宇宙 FM、Apple Podcasts

元信息提取策略：
- 音频URL、标题、播客名、时长、封面 → 结构化 JSON 字段（精确）
- 主播 → 结构化 JSON（podcast.author / podcast.podcasters）
- 嘉宾、公司、专有名词 → LLM 从标题+简介中提取
"""
import asyncio
import re
import json
import logging
import random
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List
from dataclasses import dataclass, field
from html.parser import HTMLParser
from urllib.parse import urlparse, parse_qs, unquote
from email.utils import parsedate_to_datetime
from xml.etree import ElementTree as ET

import httpx

logger = logging.getLogger(__name__)

from core.config import settings

METADATA_EXTRACT_PROMPT = """从以下播客信息中提取结构化元数据。

播客名: {podcast_name}
主播账号名: {host_names}
标题: {title}
简介:
{description}

请提取并返回 JSON（不要返回其他内容）：
{{
  "host_name": "主持人/主播的真实姓名，多人用顿号分隔",
  "guest_names": ["嘉宾真实姓名（不含头衔、职位）"],
  "name_aliases": {{"中文主名": ["英文名", "昵称", "其他别名"]}},
  "company_names": ["提及的公司/组织/产品名"],
  "proper_nouns": ["领域专有名词、技术术语、英文缩写"]
}}

规则：
- host_name：主持人/主播的真实人名（不是节目名或账号名），多人用"、"分隔。判定依据优先级：①简介中标注为"本期主播""主播""主持人"的人全部归入 host_name ②主播账号名对应的真名。如果无法确定，返回空字符串 ""
- guest_names：【关键】只包含在本期播客中实际参与对话、发言的嘉宾。判定依据：简介中明确标注为"本期嘉宾""嘉宾""对话人""受访者"的人。去掉头衔（"前XX"、"XX创始人"等），只要人名；主播不算嘉宾
- 【重要】不要把播客中"讨论到的人物"当作嘉宾——如果某人只是被主播/嘉宾谈论、引用、提及，但本人并没有参与本期对话，绝对不能放入 guest_names
- 如果某人有中英文名/别名（如"季逸超"又叫"Peak"），guest_names 和 host_name 中只使用中文主名，但在 name_aliases 中记录别名映射
- name_aliases：记录主持人和嘉宾的别名映射。key 是 host_name/guest_names 中使用的主名（中文名），value 是该人的其他名字（英文名、昵称、艺名等）。只记录在简介或标题中明确出现的别名关系。如果没有别名，返回空对象 {{}}
- company_names：只提取嘉宾当前就职/创办的公司，以及嘉宾过去就职/创办的公司；不要包含播客名、发布平台、投资机构、或节目中顺带提及的其他公司
- proper_nouns：技术术语和英文缩写（如"ToB"、"PMF"、"SaaS"、"强化学习"），不含公司名和人名
- 如果某个字段提取不到，返回空数组 [] 或空字符串 ""
- 只返回 JSON，不要解释"""


@dataclass
class PodcastEpisode:
    """解析后的播客单集信息"""
    audio_url: str
    title: str
    podcast_name: str = ""
    description: str = ""
    shownotes_text: str = ""
    duration: float = 0.0
    host_name: str = ""
    guest_names: List[str] = field(default_factory=list)
    name_aliases: Dict[str, List[str]] = field(default_factory=dict)
    company_names: List[str] = field(default_factory=list)
    proper_nouns: List[str] = field(default_factory=list)
    cover_url: str = ""
    episode_id: str = ""
    publish_date: str = ""
    raw_metadata: Dict[str, Any] = field(default_factory=dict)


class _HTMLTextExtractor(HTMLParser):
    """HTML→纯文本，在块级元素间插入换行"""
    _BLOCK_TAGS = {"p", "div", "br", "li", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "figure", "blockquote"}

    def __init__(self):
        super().__init__()
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs):
        if tag.lower() in self._BLOCK_TAGS:
            self._parts.append("\n")

    def handle_data(self, data: str):
        self._parts.append(data)

    def get_text(self) -> str:
        return re.sub(r"\n{3,}", "\n\n", "".join(self._parts)).strip()


def _html_to_text(html: str) -> str:
    extractor = _HTMLTextExtractor()
    extractor.feed(html)
    return extractor.get_text()


_SHOWNOTES_CUT_PATTERNS = [
    r"🎬",
    r"📒",
    r"欢迎订阅",
    r"欢迎关注",
    r"🚦",
    r"👦🏻",
    r"👧🏻",
]


def _clean_shownotes(text: str) -> str:
    """去掉 show notes 中的平台推广、时间轴、栏目介绍，只保留节目简介"""
    cut_pos = len(text)
    for pattern in _SHOWNOTES_CUT_PATTERNS:
        m = re.search(pattern, text)
        if m and m.start() < cut_pos:
            cut_pos = m.start()
    cleaned = text[:cut_pos].strip()
    return cleaned if cleaned else text[:500]


_HOST_SECTION_RE = re.compile(
    r"[【\[]?\s*本期主播\s*[】\]]?\s*[：:]?\s*\n([\s\S]*?)(?:\n\s*\n|\n[【\[])",
)
_CHINESE_NAME_RE = re.compile(r"([\u4e00-\u9fff]{2,4})")


def _extract_hosts_from_shownotes(text: str) -> list[str]:
    """从 shownotes 的"本期主播"段落中提取中文姓名。"""
    m = _HOST_SECTION_RE.search(text)
    if not m:
        return []
    block = m.group(1)
    names = []
    for line in block.strip().splitlines():
        line = line.strip()
        if not line:
            break
        nm = _CHINESE_NAME_RE.match(line)
        if nm:
            names.append(nm.group(1))
    return names


def _parse_itunes_duration(raw: str | int | float) -> float:
    if isinstance(raw, (int, float)):
        return float(raw)
    raw = str(raw).strip()
    if not raw:
        return 0.0
    parts = raw.split(":")
    try:
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
        if len(parts) == 2:
            return int(parts[0]) * 60 + float(parts[1])
        return float(raw)
    except (ValueError, TypeError):
        return 0.0


def _rfc2822_to_ymd(date_str: str) -> str:
    if not date_str:
        return ""
    try:
        return parsedate_to_datetime(date_str).strftime("%Y-%m-%d")
    except Exception:
        return ""


def _extract_apple_episode_id_from_link(link: str) -> str:
    try:
        parsed = urlparse(link)
        return (parse_qs(parsed.query).get("i") or [""])[0]
    except Exception:
        return ""


def _normalize_episode_title(s: str) -> str:
    if not s:
        return ""
    s = s.strip().lower()
    s = re.sub(r"[\s\u3000]+", "", s)
    return re.sub(r"[|｜:：\-—_·,，。、“”\"'‘’!?！？（）()\[\]【】]", "", s)


def _extract_apple_title_hint_from_url(url: str) -> str:
    try:
        parts = [p for p in urlparse(url).path.split("/") if p]
        if "podcast" not in parts:
            return ""
        idx = parts.index("podcast")
        if idx + 1 >= len(parts):
            return ""
        return re.sub(r"-+", " ", unquote(parts[idx + 1]).strip())
    except Exception:
        return ""


def _parse_rss_with_stdlib(xml_text: bytes) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    ns_itunes = {"itunes": "http://www.itunes.com/dtds/podcast-1.0.dtd"}
    root = ET.fromstring(xml_text)
    channel = root.find("channel")
    if channel is None:
        raise RuntimeError("RSS 结构异常：缺少 channel")

    def _text(node: ET.Element, path: str) -> str:
        return (node.findtext(path, default="", namespaces=ns_itunes) or "").strip()

    channel_title = _text(channel, "title")
    channel_author = _text(channel, "itunes:author") or _text(channel, "author")
    channel_image = ""
    ch_itunes_img = channel.find("itunes:image", ns_itunes)
    if ch_itunes_img is not None:
        channel_image = (ch_itunes_img.attrib.get("href", "") or "").strip()
    if not channel_image:
        channel_image = _text(channel, "image/url")

    entries: list[dict[str, Any]] = []
    for item in channel.findall("item"):
        title = _text(item, "title")
        guid = _text(item, "guid")
        link = _text(item, "link")
        summary = _text(item, "itunes:summary") or _text(item, "description")
        published = _text(item, "pubDate")
        itunes_duration = _text(item, "itunes:duration")

        enclosures = []
        for enc in item.findall("enclosure"):
            enclosures.append({
                "href": (enc.attrib.get("url", "") or "").strip(),
                "type": (enc.attrib.get("type", "") or "").strip(),
            })

        itunes_image = {}
        it_img = item.find("itunes:image", ns_itunes)
        if it_img is not None and it_img.attrib.get("href"):
            itunes_image = {"href": it_img.attrib.get("href", "").strip()}

        entries.append({
            "id": guid or link,
            "guid": guid,
            "link": link,
            "title": title,
            "summary": summary,
            "description": summary,
            "published": published,
            "itunes_duration": itunes_duration,
            "itunes_image": itunes_image,
            "enclosures": enclosures,
            "links": [{"href": link, "type": "text/html"}] if link else [],
            "content": [{"value": summary}] if summary else [],
        })

    channel_dict = {
        "title": channel_title,
        "itunes_author": channel_author,
        "author": channel_author,
        "itunes_image": {"href": channel_image} if channel_image else {},
    }
    return channel_dict, entries


async def _llm_extract_metadata(
    title: str,
    description: str,
    podcast_name: str,
    host_names: str,
) -> Dict[str, Any]:
    """用 LLM 从标题和简介中提取嘉宾、公司、专有名词"""
    from core.services.llm_service import get_llm_service

    llm = get_llm_service()
    prompt = METADATA_EXTRACT_PROMPT.format(
        podcast_name=podcast_name,
        host_names=host_names,
        title=title,
        description=description[:2000],
    )

    empty_result = {"host_name": "", "guest_names": [], "name_aliases": {}, "company_names": [], "proper_nouns": []}
    max_attempts = 2

    for attempt in range(max_attempts):
        try:
            response = await llm.generate(
                prompt=prompt,
                model=settings.LLM_MODEL,
                temperature=0,
                label="提取播客元信息",
            )
            text = response.strip()
            if text.startswith("```"):
                text = re.sub(r"^```\w*\n?", "", text)
                text = re.sub(r"\n?```$", "", text)
            result = json.loads(text)
            raw_aliases = result.get("name_aliases", {})
            name_aliases = {
                k: v for k, v in raw_aliases.items()
                if isinstance(v, list) and v
            } if isinstance(raw_aliases, dict) else {}
            parsed = {
                "host_name": result.get("host_name", ""),
                "guest_names": result.get("guest_names", []),
                "name_aliases": name_aliases,
                "company_names": result.get("company_names", []),
                "proper_nouns": result.get("proper_nouns", []),
            }

            if parsed["guest_names"] or attempt == max_attempts - 1:
                return parsed

            print(f"[podcast] LLM 元信息提取: guest_names 为空，重试 ({attempt + 1}/{max_attempts})")

        except Exception as e:
            print(f"[podcast] LLM 元信息提取失败 (attempt {attempt + 1}): {e}")
            if attempt == max_attempts - 1:
                return empty_result

    return empty_result


class PodcastService:
    """播客 URL 解析服务"""

    SUPPORTED_DOMAINS = ["xiaoyuzhoufm.com", "podcasts.apple.com"]

    @classmethod
    def is_supported(cls, url: str) -> bool:
        return any(d in url for d in cls.SUPPORTED_DOMAINS)

    async def extract(self, url: str) -> PodcastEpisode:
        """
        从播客平台 URL 提取音频和元数据

        Args:
            url: 播客平台的单集链接

        Returns:
            PodcastEpisode 数据对象
        """
        if "xiaoyuzhoufm.com" in url:
            return await self._extract_xiaoyuzhou(url)
        if "podcasts.apple.com" in url:
            return await self._extract_apple_podcasts(url)
        raise ValueError(f"不支持的播客平台: {url}")

    async def _extract_from_rss(
        self,
        *,
        feed_url: str,
        episode_itunes_id: str = "",
        episode_title_hint: str = "",
    ) -> PodcastEpisode:
        rss_bytes: bytes | None = None
        last_error: Exception | None = None

        for attempt in range(1, 4):
            try:
                async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
                    resp = await client.get(
                        feed_url,
                        headers={"User-Agent": "EchoPress/1.0 (Podcast RSS Reader)"},
                    )
                    resp.raise_for_status()
                    rss_bytes = resp.content
                    break
            except (httpx.TimeoutException, httpx.HTTPError) as e:
                last_error = e
                if attempt < 3:
                    await asyncio.sleep(0.8 * attempt)

        if rss_bytes is None:
            if isinstance(last_error, httpx.TimeoutException):
                raise RuntimeError("RSS 拉取超时，请稍后重试")
            raise RuntimeError("RSS 拉取失败，请稍后重试")

        channel, entries = _parse_rss_with_stdlib(rss_bytes)
        if not entries:
            raise RuntimeError("RSS Feed 中没有找到任何单集")

        podcast_name = channel.get("title", "")
        channel_author = channel.get("itunes_author", "") or channel.get("author", "")
        channel_image = ""
        itunes_img = channel.get("itunes_image", {})
        if isinstance(itunes_img, dict):
            channel_image = itunes_img.get("href", "")

        entry = None
        if episode_itunes_id:
            for e in entries:
                link = str(e.get("link", "") or "")
                if _extract_apple_episode_id_from_link(link) == episode_itunes_id:
                    entry = e
                    break
                eid = str(e.get("id", "") or "")
                guid = str(e.get("guid", "") or "")
                if episode_itunes_id in eid or episode_itunes_id in guid:
                    entry = e
                    break

        if not entry and episode_title_hint and not episode_itunes_id:
            hint_norm = _normalize_episode_title(episode_title_hint)
            for e in entries:
                entry_title = str(e.get("title", "") or "")
                entry_norm = _normalize_episode_title(entry_title)
                if (
                    episode_title_hint.lower() in entry_title.lower()
                    or (hint_norm and entry_norm and (hint_norm in entry_norm or entry_norm in hint_norm))
                ):
                    entry = e
                    break

        if not entry and episode_itunes_id:
            raise RuntimeError("未在 Apple 提供的公开 Feed 中找到这期节目，请尝试较新的单集链接")
        if not entry:
            entry = entries[0]

        audio_url = ""
        for enc in entry.get("enclosures", []):
            href = enc.get("href", "")
            if enc.get("type", "").startswith("audio/") or href.split("?")[0].endswith((".mp3", ".m4a", ".wav", ".ogg")):
                audio_url = href
                break
        if not audio_url:
            raise RuntimeError("该播客单集未提供可解析的音频地址")

        title = str(entry.get("title", "") or "")
        description = str(entry.get("summary", "") or entry.get("description", "") or "")
        if "<" in description:
            description = _html_to_text(description)
        content_list = entry.get("content", [{}])
        if content_list and isinstance(content_list, list):
            raw_content = content_list[0].get("value", "")
            if raw_content and len(raw_content) > len(description):
                description = _html_to_text(raw_content) if "<" in raw_content else raw_content

        shownotes_clean = _clean_shownotes(description)
        duration = _parse_itunes_duration(entry.get("itunes_duration", 0))

        cover_url = ""
        entry_itunes_img = entry.get("itunes_image", {})
        if isinstance(entry_itunes_img, dict):
            cover_url = entry_itunes_img.get("href", "")
        if not cover_url:
            cover_url = channel_image

        publish_date = _rfc2822_to_ymd(str(entry.get("published", "") or ""))
        episode_id = str(entry.get("id", "") or entry.get("guid", "") or "")

        print("[podcast] 使用 LLM 提取元信息...")
        llm_meta = await _llm_extract_metadata(
            title=title,
            description=shownotes_clean,
            podcast_name=podcast_name,
            host_names=channel_author,
        )

        return PodcastEpisode(
            audio_url=audio_url,
            title=title,
            podcast_name=podcast_name,
            description=shownotes_clean,
            shownotes_text=shownotes_clean,
            duration=duration,
            host_name=llm_meta.get("host_name", "") or channel_author,
            guest_names=llm_meta.get("guest_names", []),
            name_aliases=llm_meta.get("name_aliases", {}),
            company_names=llm_meta.get("company_names", []),
            proper_nouns=llm_meta.get("proper_nouns", []),
            cover_url=cover_url,
            episode_id=episode_id,
            publish_date=publish_date,
            raw_metadata={"feed_url": feed_url, "entry": entry},
        )

    async def _extract_apple_podcasts(self, url: str) -> PodcastEpisode:
        podcast_id_match = re.search(r"/id(\d+)", url)
        if not podcast_id_match:
            raise ValueError(f"无法从 Apple Podcasts URL 提取节目 ID: {url}")

        podcast_id = podcast_id_match.group(1)
        episode_itunes_id = (parse_qs(urlparse(url).query).get("i") or [""])[0]
        episode_title_hint = _extract_apple_title_hint_from_url(url)

        feed_url = ""
        episode_info: dict[str, Any] | None = None
        lookup_url = f"https://itunes.apple.com/lookup?id={podcast_id}"

        for attempt in range(1, 4):
            try:
                async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
                    show_resp = await client.get(lookup_url, headers={"User-Agent": "Mozilla/5.0"})
                    show_resp.raise_for_status()
                    show_data = show_resp.json()
                    for item in show_data.get("results", []):
                        if item.get("feedUrl"):
                            feed_url = item["feedUrl"]
                            break

                    episodes_resp = await client.get(
                        f"https://itunes.apple.com/lookup?id={podcast_id}&entity=podcastEpisode&limit=200",
                        headers={"User-Agent": "Mozilla/5.0"},
                    )
                    episodes_resp.raise_for_status()
                    for item in episodes_resp.json().get("results", [])[1:]:
                        track_view_url = item.get("trackViewUrl", "") or ""
                        if episode_itunes_id and episode_itunes_id in track_view_url:
                            episode_info = item
                            if item.get("feedUrl"):
                                feed_url = item["feedUrl"]
                            break
                break
            except (httpx.TimeoutException, httpx.HTTPError) as e:
                if attempt >= 3:
                    if isinstance(e, httpx.TimeoutException):
                        raise RuntimeError("Apple Podcasts 解析超时，请稍后重试")
                    raise RuntimeError("Apple Podcasts 请求失败，请稍后重试")
                await asyncio.sleep(0.8 * attempt)

        if episode_info:
            title = str(episode_info.get("trackName", "") or "")
            description = str(episode_info.get("description", "") or "")
            shownotes_clean = _clean_shownotes(description)
            audio_url = str(
                episode_info.get("episodeUrl", "")
                or episode_info.get("previewUrl", "")
                or ""
            )
            if not audio_url:
                raise RuntimeError("Apple Podcasts 返回了单集信息，但未提供音频地址")

            print("[podcast] 使用 LLM 提取元信息...")
            llm_meta = await _llm_extract_metadata(
                title=title,
                description=shownotes_clean,
                podcast_name=str(episode_info.get("collectionName", "") or ""),
                host_names=str(episode_info.get("artistName", "") or ""),
            )

            release_date = str(episode_info.get("releaseDate", "") or "")
            publish_date = ""
            if release_date:
                try:
                    publish_date = datetime.fromisoformat(release_date.replace("Z", "+00:00")).strftime("%Y-%m-%d")
                except ValueError:
                    publish_date = ""

            return PodcastEpisode(
                audio_url=audio_url,
                title=title,
                podcast_name=str(episode_info.get("collectionName", "") or ""),
                description=shownotes_clean,
                shownotes_text=shownotes_clean,
                duration=float((episode_info.get("trackTimeMillis") or 0) / 1000),
                host_name=llm_meta.get("host_name", "") or str(episode_info.get("artistName", "") or ""),
                guest_names=llm_meta.get("guest_names", []),
                name_aliases=llm_meta.get("name_aliases", {}),
                company_names=llm_meta.get("company_names", []),
                proper_nouns=llm_meta.get("proper_nouns", []),
                cover_url=str(
                    episode_info.get("artworkUrl600", "")
                    or episode_info.get("artworkUrl160", "")
                    or episode_info.get("artworkUrl100", "")
                    or ""
                ),
                episode_id=str(episode_info.get("trackId", "") or ""),
                publish_date=publish_date,
                raw_metadata=episode_info,
            )

        if not feed_url:
            raise RuntimeError("Apple Podcasts 未返回公开 Feed，暂时无法稳定解析这期节目")

        return await self._extract_from_rss(
            feed_url=feed_url,
            episode_itunes_id=episode_itunes_id,
            episode_title_hint=episode_title_hint,
        )

    async def _extract_xiaoyuzhou(self, url: str) -> PodcastEpisode:
        """解析小宇宙 FM 单集页面"""
        eid_match = re.search(r"/episode/([a-f0-9]+)", url)
        if not eid_match:
            raise ValueError(f"无法从 URL 提取 episode ID: {url}")

        episode_id = eid_match.group(1)

        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Referer": "https://www.xiaoyuzhoufm.com/",
        }
        import asyncio
        max_attempts = 5
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            for attempt in range(max_attempts):
                resp = await client.get(url, headers=headers)
                if resp.status_code in (403, 429) and attempt < max_attempts - 1:
                    delay = 2 ** attempt + random.random()
                    logger.warning(
                        f"小宇宙请求 {resp.status_code}，第 {attempt+1} 次重试，等待 {delay:.1f}s"
                    )
                    await asyncio.sleep(delay)
                    continue
                resp.raise_for_status()
                break
            html = resp.text

        m = re.search(r"__NEXT_DATA__.*?>(.*?)</script>", html)
        if not m:
            raise RuntimeError("无法从页面提取 __NEXT_DATA__")

        next_data = json.loads(m.group(1))
        ep = next_data["props"]["pageProps"]["episode"]

        # === 结构化字段（精确） ===
        audio_url = (
            ep.get("media", {}).get("source", {}).get("url")
            or ep.get("enclosure", {}).get("url")
            or ""
        )
        if not audio_url:
            media_key = ep.get("mediaKey", "")
            if media_key:
                audio_url = f"https://media.xyzcdn.net/{media_key}"

        if not audio_url:
            raise RuntimeError("无法提取音频 URL")

        title = ep.get("title", "")
        podcast_info = ep.get("podcast", {})
        podcast_name = podcast_info.get("title", "")
        duration = ep.get("duration", 0)
        cover_url = ep.get("image", {}).get("picUrl", "") or podcast_info.get("image", {}).get("picUrl", "")

        publish_date = ""
        pub_ts = ep.get("pubDate") or ep.get("publishTime") or ep.get("gmtCreated")
        if pub_ts:
            try:
                if isinstance(pub_ts, str) and "T" in pub_ts:
                    publish_date = datetime.fromisoformat(pub_ts.replace("Z", "+00:00")).strftime("%Y-%m-%d")
                else:
                    ts = int(pub_ts) / 1000 if int(pub_ts) > 1e12 else int(pub_ts)
                    publish_date = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
            except (ValueError, OSError):
                pass

        shownotes_html = ep.get("shownotes", "") or ep.get("description", "")
        shownotes_raw = _html_to_text(shownotes_html)
        shownotes_clean = _clean_shownotes(shownotes_raw)

        # 主播：结构化数据（可能是账号名/节目名而非真人名）
        podcasters = podcast_info.get("podcasters", [])
        podcaster_names = [p["nickname"] for p in podcasters if p.get("nickname")]
        host_name_raw = podcast_info.get("author", "") or (podcaster_names[0] if podcaster_names else "")

        # === LLM 提取主持人真名、嘉宾、公司、专有名词 ===
        print("[podcast] 使用 LLM 提取元信息...")
        llm_meta = await _llm_extract_metadata(
            title=title,
            description=shownotes_clean,
            podcast_name=podcast_name,
            host_names="、".join(podcaster_names) if podcaster_names else host_name_raw,
        )

        host_name = llm_meta.get("host_name", "") or host_name_raw
        guest_names = llm_meta.get("guest_names", [])

        # 用平台注册主播列表校正：LLM 可能把部分主播误归为嘉宾
        if podcaster_names and guest_names:
            host_set = set(
                n.strip() for n in re.split(r'[、，,\s]+', host_name) if n.strip()
            )
            misplaced_hosts = []
            remaining_guests = []
            for g in guest_names:
                is_podcaster = any(
                    g == pn or (len(pn) >= 2 and pn in g) or (len(g) >= 2 and g in pn)
                    for pn in podcaster_names
                )
                if is_podcaster and g not in host_set:
                    misplaced_hosts.append(g)
                else:
                    remaining_guests.append(g)
            if misplaced_hosts:
                host_name = "、".join(list(host_set) + misplaced_hosts)
                guest_names = remaining_guests
                print(f"[podcast] 主播校正: {misplaced_hosts} 从嘉宾移入主持人")

        # 兜底：从 shownotes "本期主播" 段落提取姓名，补全遗漏的主播
        shownotes_hosts = _extract_hosts_from_shownotes(shownotes_clean)
        if shownotes_hosts:
            host_set = set(
                n.strip() for n in re.split(r'[、，,\s]+', host_name) if n.strip()
            )
            added = []
            for sh in shownotes_hosts:
                if sh not in host_set:
                    host_set.add(sh)
                    added.append(sh)
                    # 同时从嘉宾列表移除
                    guest_names = [g for g in guest_names if g != sh]
            if added:
                host_name = "、".join(
                    n for n in shownotes_hosts if n in host_set
                ) or "、".join(host_set)
                print(f"[podcast] shownotes 主播补全: +{added} → {host_name}")

        name_aliases = llm_meta.get("name_aliases", {})
        if name_aliases:
            print(f"[podcast] 人名别名: {name_aliases}")

        return PodcastEpisode(
            audio_url=audio_url,
            title=title,
            podcast_name=podcast_name,
            description=shownotes_clean,
            shownotes_text=shownotes_clean,
            duration=float(duration),
            host_name=host_name,
            guest_names=guest_names,
            name_aliases=name_aliases,
            company_names=llm_meta.get("company_names", []),
            proper_nouns=llm_meta.get("proper_nouns", []),
            cover_url=cover_url,
            episode_id=episode_id,
            publish_date=publish_date,
            raw_metadata=ep,
        )


_podcast_service: Optional[PodcastService] = None


def get_podcast_service() -> PodcastService:
    global _podcast_service
    if _podcast_service is None:
        _podcast_service = PodcastService()
    return _podcast_service
