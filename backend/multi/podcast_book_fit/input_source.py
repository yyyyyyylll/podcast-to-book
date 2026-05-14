"""Input acquisition helpers for podcast book-fit judgment."""
from __future__ import annotations

import html
import re
import xml.etree.ElementTree as ET
from typing import Any
from urllib.parse import quote

import httpx

from multi.parsers.xiaoyuzhou_show import parse_show


ITUNES_SEARCH_URL = "https://itunes.apple.com/search"
_TAG_RE = re.compile(r"<[^>]+>")


def _text(node: ET.Element | None) -> str:
    return (node.text or "").strip() if node is not None else ""


def _strip_html(value: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(_TAG_RE.sub(" ", value or ""))).strip()


def parse_rss_feed(xml_text: str, *, source_url: str) -> dict[str, Any]:
    root = ET.fromstring(xml_text)
    channel = root.find("channel")
    if channel is None:
        raise ValueError("RSS 中缺少 channel")

    podcast_name = _text(channel.find("title"))
    podcast_intro = _strip_html(_text(channel.find("description")))
    episodes: list[dict[str, str]] = []
    for item in channel.findall("item"):
        title = _text(item.find("title"))
        raw_desc = _text(item.find("description"))
        if not title:
            continue
        episodes.append(
            {
                "title": title,
                "intro": raw_desc,
                "intro_text": _strip_html(raw_desc),
                "publish_date": _text(item.find("pubDate")),
            }
        )
    if not episodes:
        raise ValueError("RSS 中没有可用单集")
    return {
        "podcast_name": podcast_name,
        "podcast_intro": podcast_intro,
        "episode_list": episodes,
        "source_url": source_url,
        "source_type": "rss",
    }


async def find_apple_rss_feed(podcast_name: str) -> dict[str, Any] | None:
    params = f"?term={quote(podcast_name)}&entity=podcast&country=CN&limit=10"
    async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
        resp = await client.get(ITUNES_SEARCH_URL + params)
        resp.raise_for_status()
    data = resp.json()
    results = data.get("results") or []
    if not results:
        return None
    normalized = podcast_name.strip().lower()
    selected = None
    for item in results:
        if (item.get("collectionName") or "").strip().lower() == normalized:
            selected = item
            break
    selected = selected or results[0]
    feed_url = selected.get("feedUrl")
    if not feed_url:
        return None
    return {
        "feed_url": feed_url,
        "collection_name": selected.get("collectionName") or "",
        "collection_id": selected.get("collectionId"),
        "track_count": selected.get("trackCount"),
    }


async def build_input_from_url(url: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """Build a lightweight full-show input from a Xiaoyuzhou show URL.

    Returns:
        (input_payload, show_metadata)
    """
    parse_result = await parse_show(url)
    show = parse_result.show
    feed = await find_apple_rss_feed(show.title)
    if not feed:
        raise RuntimeError(f"没有通过 Apple Podcasts 找到 RSS feed：{show.title}")

    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        rss_resp = await client.get(feed["feed_url"])
        rss_resp.raise_for_status()
    payload = parse_rss_feed(rss_resp.text, source_url=feed["feed_url"])
    payload["podcast_name"] = payload.get("podcast_name") or show.title
    payload["podcast_intro"] = show.description_clean or payload.get("podcast_intro", "")
    payload["podcaster_names"] = list(show.podcaster_names or [])
    payload["contacts"] = list(show.contacts or [])
    payload["description"] = show.description
    payload["subscription_count"] = show.subscription_count
    payload["source_show_url"] = url
    payload["source_apple_podcast_id"] = feed.get("collection_id")
    metadata = {
        "show_title": show.title,
        "show_url": url,
        "xiaoyuzhou_episode_count": show.episode_count,
        "rss_episode_count": len(payload["episode_list"]),
        "rss_url": feed["feed_url"],
        "apple_collection_id": feed.get("collection_id"),
        "apple_track_count": feed.get("track_count"),
    }
    return payload, metadata

