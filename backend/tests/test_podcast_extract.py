"""测试播客 URL 解析"""
import asyncio
import json
import sys
import os
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.services.podcast_service import PodcastService

TEST_URL = "https://www.xiaoyuzhoufm.com/episode/691952ddcbba038b42bd9077"


async def main():
    svc = PodcastService()

    print(f"解析链接: {TEST_URL}\n")
    episode = await svc.extract(TEST_URL)

    print("=" * 60)
    print(f"播客名:  {episode.podcast_name}")
    print(f"标题:    {episode.title}")
    print(f"音频URL: {episode.audio_url}")
    print(f"时长:    {episode.duration:.0f}s ({episode.duration/60:.1f}min)")
    print(f"主持人:  {episode.host_name or '(未提取到)'}")
    print(f"嘉宾:    {'、'.join(episode.guest_names) if episode.guest_names else '(未提取到)'}")
    print(f"公司:    {'、'.join(episode.company_names) if episode.company_names else '(未提取到)'}")
    print(f"专有名词: {'、'.join(episode.proper_nouns) if episode.proper_nouns else '(未提取到)'}")
    print(f"封面:    {episode.cover_url[:80] if episode.cover_url else '(无)'}")

    artifacts_dir = os.path.join(os.path.dirname(__file__), "artifacts")
    os.makedirs(artifacts_dir, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")

    # JSON
    json_path = os.path.join(artifacts_dir, f"podcast_extract_{ts}.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump({
            "audio_url": episode.audio_url,
            "title": episode.title,
            "podcast_name": episode.podcast_name,
            "duration": episode.duration,
            "host_name": episode.host_name,
            "guest_names": episode.guest_names,
            "company_names": episode.company_names,
            "proper_nouns": episode.proper_nouns,
            "description": episode.description,
        }, f, ensure_ascii=False, indent=2)

    # 可读 Markdown
    md_path = os.path.join(artifacts_dir, f"podcast_extract_{ts}.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(f"# 播客元信息提取结果\n\n")
        f.write(f"- **播客名**: {episode.podcast_name}\n")
        f.write(f"- **标题**: {episode.title}\n")
        f.write(f"- **音频URL**: {episode.audio_url}\n")
        f.write(f"- **时长**: {episode.duration:.0f}s ({episode.duration/60:.1f}min)\n")
        f.write(f"- **主持人**: {episode.host_name or '(未提取到)'}\n")
        f.write(f"- **嘉宾**: {'、'.join(episode.guest_names) if episode.guest_names else '(未提取到)'}\n")
        f.write(f"- **公司**: {'、'.join(episode.company_names) if episode.company_names else '(未提取到)'}\n")
        f.write(f"- **专有名词**: {'、'.join(episode.proper_nouns) if episode.proper_nouns else '(未提取到)'}\n")
        f.write(f"- **封面**: {episode.cover_url or '(无)'}\n\n")
        f.write(f"## 简介\n\n{episode.description}\n")

    print(f"\n已保存:")
    print(f"  JSON: {json_path}")
    print(f"  可读: {md_path}")


if __name__ == "__main__":
    asyncio.run(main())
