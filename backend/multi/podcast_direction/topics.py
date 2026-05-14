"""Host-owned topic/category extraction for podcast diagnosis inputs."""
from __future__ import annotations

import re
from html import unescape
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlsplit


TOPIC_PATH_RE = re.compile(r"/podcast-topic/([^/?#]+)")


class _TopicLinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[dict[str, str]] = []
        self._active_href: str | None = None
        self._active_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        attrs_dict = {k.lower(): v for k, v in attrs if k}
        href = attrs_dict.get("href") or ""
        if TOPIC_PATH_RE.search(href):
            self._active_href = href
            self._active_text = []

    def handle_data(self, data: str) -> None:
        if self._active_href:
            self._active_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() != "a" or not self._active_href:
            return
        text = _clean_text("".join(self._active_text))
        self.links.append({"href": self._active_href, "text": text})
        self._active_href = None
        self._active_text = []


def _clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", unescape(value or "")).strip()


def _normalize_topic_url(href: str) -> tuple[str, str] | None:
    match = TOPIC_PATH_RE.search(href or "")
    if not match:
        return None
    topic_id = match.group(1)
    parsed = urlsplit(href)
    if parsed.scheme and parsed.netloc:
        url = f"{parsed.scheme}://{parsed.netloc}/podcast-topic/{topic_id}"
    else:
        url = f"https://www.xiaoyuzhoufm.com/podcast-topic/{topic_id}"
    return topic_id, url


def _extract_links_from_html(html: str) -> list[dict[str, str]]:
    parser = _TopicLinkParser()
    parser.feed(html or "")
    parser.close()
    return parser.links


def extract_host_topic_references(
    episode_list: list[dict[str, Any]],
    *,
    max_episode_titles_per_topic: int = 12,
) -> list[dict[str, Any]]:
    """Extract Xiaoyuzhou host-defined topic links from episode intros.

    The result is intentionally conservative: only explicit `podcast-topic`
    links are treated as host-owned classification evidence.
    """
    grouped: dict[str, dict[str, Any]] = {}
    order: list[str] = []

    for ep in episode_list or []:
        title = _clean_text(str(ep.get("title") or ""))
        intro = str(ep.get("intro") or "")
        if not title or "podcast-topic" not in intro:
            continue
        seen_in_episode: set[str] = set()
        for link in _extract_links_from_html(intro):
            normalized = _normalize_topic_url(link.get("href", ""))
            if not normalized:
                continue
            topic_id, url = normalized
            if topic_id in seen_in_episode:
                continue
            seen_in_episode.add(topic_id)
            topic_title = _clean_text(link.get("text", "")) or topic_id
            if topic_id not in grouped:
                grouped[topic_id] = {
                    "topic_id": topic_id,
                    "topic_title": topic_title,
                    "url": url,
                    "episode_count": 0,
                    "episode_titles": [],
                }
                order.append(topic_id)
            item = grouped[topic_id]
            item["episode_count"] += 1
            if len(item["episode_titles"]) < max_episode_titles_per_topic:
                item["episode_titles"].append(title)

    return sorted(
        (grouped[topic_id] for topic_id in order),
        key=lambda item: (-int(item["episode_count"]), item["topic_title"]),
    )
