"""
test_host_show_parser — host 模块小宇宙节目页解析的烟雾测试。

需要外网 + 真实小宇宙节目页。受网络抖动影响，CI 上可跳过；本地手测用：

    cd backend
    pytest tests/test_host_show_parser.py -v -s
"""
from __future__ import annotations

import asyncio
import os

import pytest

from single.host.services.show_parser import parse_show_with_briefs

# 以 workbench 中已多次跑过的"可能性褶皱"为测试节目（pid 来自 fetch_show.py 文档）
DEFAULT_SHOW_URL = "https://www.xiaoyuzhoufm.com/podcast/6807993539793c80b08070f0"


@pytest.mark.skipif(
    os.getenv("RUN_NETWORK_TESTS") != "1",
    reason="需要外网，设置 RUN_NETWORK_TESTS=1 启用",
)
def test_parse_show_with_briefs_smoke():
    show_url = os.getenv("HOST_TEST_SHOW_URL", DEFAULT_SHOW_URL)
    result = asyncio.run(parse_show_with_briefs(show_url, max_episodes=3, use_cache=False))

    assert result.show["pid"]
    assert result.show["title"]
    assert result.total_episode_count > 0
    assert len(result.episodes) <= 3

    ok_episodes = [e for e in result.episodes if not e.fetch_error]
    assert ok_episodes, "至少应有一集成功抓到 brief"
    first = ok_episodes[0]
    assert first.title
    assert first.audio_url
    assert first.duration_seconds > 0


@pytest.mark.skipif(
    os.getenv("RUN_NETWORK_TESTS") != "1",
    reason="需要外网",
)
def test_parse_show_with_briefs_cache():
    show_url = os.getenv("HOST_TEST_SHOW_URL", DEFAULT_SHOW_URL)
    first = asyncio.run(parse_show_with_briefs(show_url, max_episodes=2))
    second = asyncio.run(parse_show_with_briefs(show_url, max_episodes=2))
    assert first is second  # 命中缓存返回的是同一对象


def test_invalid_url_raises():
    with pytest.raises(ValueError):
        asyncio.run(parse_show_with_briefs("https://example.com/foo"))
