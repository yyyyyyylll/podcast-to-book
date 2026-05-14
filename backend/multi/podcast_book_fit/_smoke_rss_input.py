"""Smoke test for RSS input parsing helpers."""
from __future__ import annotations

from multi.podcast_book_fit.input_source import parse_rss_feed


def main() -> None:
    xml = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>夜夜页页</title>
    <description>借读书之名闲聊。</description>
    <item>
      <title>Vol.001 一千零一夜</title>
      <description><![CDATA[<p><a href="https://www.xiaoyuzhoufm.com/podcast-topic/abc">一千零一夜·系列合集</a></p><p>开篇</p>]]></description>
      <pubDate>Tue, 28 Apr 2026 00:00:00 GMT</pubDate>
    </item>
    <item>
      <title>Vol.002 西游记</title>
      <description><![CDATA[<p>细读</p>]]></description>
    </item>
  </channel>
</rss>
"""
    payload = parse_rss_feed(xml, source_url="https://feed.example.com/rss")
    assert payload["podcast_name"] == "夜夜页页"
    assert len(payload["episode_list"]) == 2
    assert "podcast-topic/abc" in payload["episode_list"][0]["intro"]
    assert payload["source_url"] == "https://feed.example.com/rss"
    print("[OK] RSS feed parsed into book-fit input payload")


if __name__ == "__main__":
    main()
