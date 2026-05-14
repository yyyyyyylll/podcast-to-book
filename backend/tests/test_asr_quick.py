"""腾讯云 ASR 快速测试"""
import asyncio
import json
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.services.asr_service import ASRService


TEST_AUDIO_URL = "https://asr-audio-1300466766.cos.ap-nanjing.myqcloud.com/test16k.wav"


async def main():
    service = ASRService()

    if not service.client:
        print("❌ 腾讯云凭证未配置")
        return

    print("✅ 腾讯云客户端初始化成功")
    print(f"📎 测试音频: {TEST_AUDIO_URL}")
    print("🚀 开始转写...\n")

    result = await service.transcribe(TEST_AUDIO_URL)

    print(f"\n{'='*60}")
    print(f"📊 转写结果:")
    print(f"  - 片段数: {len(result['segments'])}")
    print(f"  - 说话人数: {result['speaker_count']}")
    print(f"  - 音频时长: {result['duration']:.1f}s")
    print(f"\n📝 全文:")
    print(f"  {result['full_text'][:500]}")
    print(f"\n🗣️ 分段详情:")
    for i, seg in enumerate(result["segments"][:10]):
        print(f"  [{seg['start_time']:.1f}s-{seg['end_time']:.1f}s] {seg['speaker']}: {seg['text'][:80]}")

    artifacts_dir = os.path.join(os.path.dirname(__file__), "artifacts")
    os.makedirs(artifacts_dir, exist_ok=True)
    out_path = os.path.join(artifacts_dir, "asr_tencent_test.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"\n💾 结果已保存: {out_path}")


if __name__ == "__main__":
    asyncio.run(main())
