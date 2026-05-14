"""
腾讯云语音识别服务

使用腾讯云 ASR 录音文件识别 API 进行音频转写
支持：
- 音频转文字（长音频，最长5小时）
- 说话人分离（diarization）
- 词级别时间戳
- 中英混合识别（16k_zh_en 大模型）
- 临时热词表
- 语气词过滤
"""
import hashlib
import time
import json
import base64
import asyncio
import os
import shutil
import subprocess
import tempfile
from typing import Dict, List, Optional, Any
from pathlib import Path
from urllib.parse import urlparse

import httpx
from qcloud_cos import CosConfig, CosS3Client
from tencentcloud.common import credential
from tencentcloud.common.profile.client_profile import ClientProfile
from tencentcloud.common.profile.http_profile import HttpProfile
from tencentcloud.asr.v20190614 import asr_client, models

from core.config import settings


ENGINE_MODEL_TYPE = "16k_zh_en"
RES_TEXT_FORMAT = 2
POLL_INTERVAL = 5
MAX_POLL_WAIT = 3600
LOCAL_FILE_MAX_BYTES = 5 * 1024 * 1024
MAX_AUDIO_DURATION = 5 * 3600
DOWNLOAD_CHUNK_BYTES = 1024 * 1024
DOWNLOAD_TIMEOUT = 120
ASR_URL_CACHE_DIR = Path(settings.STORAGE_DIR) / "asr_url_cache"
LOCAL_CHUNK_SECONDS = 15 * 60
LOCAL_CHUNK_BITRATE = "24k"


class ASRService:
    """腾讯云语音识别服务"""

    def __init__(self):
        self.secret_id = settings.TENCENT_SECRET_ID
        self.secret_key = settings.TENCENT_SECRET_KEY
        self.cos_bucket = settings.COS_BUCKET.strip()
        self.cos_region = settings.COS_REGION.strip()
        self.cos_prefix = settings.COS_PREFIX.strip().strip("/")
        self._cos_client: Optional[CosS3Client] = None
        if not self.secret_id or not self.secret_key:
            print("⚠️ 警告: TENCENT_SECRET_ID/TENCENT_SECRET_KEY 未配置，ASR 调用将失败")
            self.client = None
        else:
            cred = credential.Credential(self.secret_id, self.secret_key)
            http_profile = HttpProfile()
            http_profile.endpoint = "asr.tencentcloudapi.com"
            client_profile = ClientProfile()
            client_profile.httpProfile = http_profile
            self.client = asr_client.AsrClient(cred, "", client_profile)
        ASR_URL_CACHE_DIR.mkdir(parents=True, exist_ok=True)

    async def transcribe(
        self,
        audio_path: str,
        hotwords: Optional[List[str]] = None,
        audio_duration: Optional[float] = None,
    ) -> Dict[str, Any]:
        """
        转写音频文件

        Args:
            audio_path: 音频 URL 或本地文件路径
            hotwords: 热词列表，如 ["PMF", "SaaS", "ChatGPT"]
            audio_duration: 音频时长（秒），用于超时前置校验

        Returns:
            转写结果字典，格式与原阿里云版本兼容
        """
        if audio_duration and audio_duration > MAX_AUDIO_DURATION:
            hours = audio_duration / 3600
            raise ValueError(
                f"音频时长约 {hours:.1f} 小时，超过腾讯云 ASR 最大 5 小时限制。"
                "我们正在优化对超长播客的支持，敬请期待！"
            )

        if not self.client:
            raise RuntimeError(
                "TENCENT_SECRET_ID / TENCENT_SECRET_KEY 未配置，无法使用 ASR 服务"
            )

        # 本地大文件（>5MB）：直接走 COS 上传或分片转写，不走 base64 路径
        is_url = audio_path.startswith("http://") or audio_path.startswith("https://")
        if not is_url:
            local_path = Path(audio_path)
            if not local_path.is_absolute():
                local_path = Path(settings.STORAGE_DIR) / audio_path
            if not local_path.exists():
                local_path = Path(audio_path)
            if local_path.exists() and local_path.stat().st_size > LOCAL_FILE_MAX_BYTES:
                size_mb = local_path.stat().st_size / 1024 / 1024
                print(f"[ASR] 本地大文件 ({size_mb:.1f}MB)，自动选择上传策略")
                if self._cos_ready():
                    try:
                        cos_url = await self._upload_to_cos(local_path, source_url=str(local_path))
                        print(f"[ASR] 已上传到 COS: {cos_url[:80]}...")
                        result = await self._submit_and_poll(cos_url, hotwords)
                        return self._parse_result(result)
                    except Exception as e:
                        print(f"[ASR] COS 上传失败，改走本地分片转写: {e}")
                return await self._transcribe_large_local_file(local_path, hotwords)

        submit_path = audio_path

        try:
            result = await self._submit_and_poll(submit_path, hotwords)
        except Exception as e:
            if not self._should_retry_with_fallback(audio_path, e):
                raise

            print(f"[ASR] URL 拉取失败，切换兜底链路: {e}")
            return await self._transcribe_via_fallback(audio_path, hotwords)

        parsed = self._parse_result(result)
        return parsed

    async def _submit_and_poll(
        self,
        audio_path: str,
        hotwords: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        task_id = await self._submit_task(audio_path, hotwords)
        print(f"[ASR] 任务已提交: TaskId={task_id}")
        return await self._poll_result(task_id)

    async def _submit_task(
        self,
        audio_path: str,
        hotwords: Optional[List[str]] = None,
    ) -> int:
        """提交录音文件识别任务，返回 TaskId"""
        req = models.CreateRecTaskRequest()

        req.EngineModelType = ENGINE_MODEL_TYPE
        req.ChannelNum = 1
        req.ResTextFormat = RES_TEXT_FORMAT
        req.SpeakerDiarization = 1
        req.SpeakerNumber = 0
        req.ConvertNumMode = 1
        req.FilterModal = 1
        req.FilterDirty = 0

        is_url = audio_path.startswith("http://") or audio_path.startswith("https://")

        if is_url:
            req.SourceType = 0
            req.Url = audio_path
            print(f"[ASR] 使用 URL 模式: {audio_path[:80]}...")
        else:
            file_path = Path(audio_path)
            if not file_path.is_absolute():
                file_path = Path(settings.STORAGE_DIR) / audio_path
            if not file_path.exists():
                file_path = Path(audio_path)

            if not file_path.exists():
                raise FileNotFoundError(f"音频文件不存在: {audio_path}")

            file_size = file_path.stat().st_size
            if file_size > LOCAL_FILE_MAX_BYTES:
                raise ValueError(
                    f"本地文件 {file_size / 1024 / 1024:.1f}MB 超过腾讯云5MB限制，"
                    f"请先上传到 COS 或其他存储服务并使用 URL 提交"
                )

            print(f"[ASR] 使用本地文件模式: {file_path} ({file_size / 1024:.0f}KB)")
            with open(file_path, "rb") as f:
                audio_bytes = f.read()
            req.SourceType = 1
            req.Data = base64.b64encode(audio_bytes).decode("utf-8")
            req.DataLen = file_size

        if hotwords:
            hotword_str = ",".join(f"{w}|11" for w in hotwords)
            req.HotwordList = hotword_str
            print(f"[ASR] 热词表: {hotword_str[:100]}")

        loop = asyncio.get_event_loop()
        resp = await loop.run_in_executor(None, self.client.CreateRecTask, req)

        task_id = resp.Data.TaskId
        return task_id

    def _should_retry_with_fallback(self, audio_path: str, error: Exception) -> bool:
        if not isinstance(audio_path, str):
            return False
        if not (audio_path.startswith("http://") or audio_path.startswith("https://")):
            return False
        return "Failed to download audio file" in str(error)

    def _cache_key(self, audio_url: str) -> str:
        return hashlib.sha256(audio_url.encode("utf-8")).hexdigest()[:16]

    def _load_url_cache(self, audio_url: str) -> Optional[Dict[str, Any]]:
        cache_file = ASR_URL_CACHE_DIR / f"{self._cache_key(audio_url)}.json"
        if not cache_file.exists():
            return None
        try:
            return json.loads(cache_file.read_text(encoding="utf-8"))
        except Exception:
            return None

    def _save_url_cache(self, audio_url: str, payload: Dict[str, Any]) -> None:
        cache_file = ASR_URL_CACHE_DIR / f"{self._cache_key(audio_url)}.json"
        try:
            cache_file.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as e:
            print(f"[ASR] 写入 URL 缓存失败: {e}")

    async def _transcribe_via_fallback(
        self,
        audio_url: str,
        hotwords: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        cache = self._load_url_cache(audio_url)
        if cache and cache.get("cos_url"):
            print(f"[ASR] 命中 COS URL 缓存: {cache['cos_url'][:120]}")
            result = await self._submit_and_poll(cache["cos_url"], hotwords)
            return self._parse_result(result)

        tmp_path = await self._download_remote_audio(audio_url)
        try:
            file_size = tmp_path.stat().st_size

            if file_size <= LOCAL_FILE_MAX_BYTES:
                print(
                    f"[ASR] 兜底改走本地文件模式: {tmp_path.name} "
                    f"({file_size / 1024 / 1024:.2f}MB)"
                )
                result = await self._submit_and_poll(str(tmp_path), hotwords)
                return self._parse_result(result)

            if self._cos_ready():
                try:
                    cos_url = await self._upload_to_cos(tmp_path, source_url=audio_url)
                    self._save_url_cache(
                        audio_url,
                        {
                            "source_url": audio_url,
                            "cos_url": cos_url,
                            "bucket": self.cos_bucket,
                            "region": self.cos_region,
                            "size_bytes": file_size,
                        },
                    )
                    print(f"[ASR] 兜底改走 COS URL 模式: {cos_url[:120]}")
                    result = await self._submit_and_poll(cos_url, hotwords)
                    return self._parse_result(result)
                except Exception as e:
                    print(f"[ASR] COS 兜底失败，改走本地分片转写: {e}")

            print(
                f"[ASR] 大文件兜底改走本地分片转写: {tmp_path.name} "
                f"({file_size / 1024 / 1024:.2f}MB)"
            )
            return await self._transcribe_large_local_file(tmp_path, hotwords)
        finally:
            tmp_path.unlink(missing_ok=True)

    async def _download_remote_audio(self, audio_url: str) -> Path:
        suffix = Path(urlparse(audio_url).path).suffix or ".audio"
        fd, tmp_name = tempfile.mkstemp(prefix="asr_fallback_", suffix=suffix)
        os.close(fd)
        tmp_path = Path(tmp_name)

        timeout = httpx.Timeout(DOWNLOAD_TIMEOUT, connect=20)
        try:
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
                async with client.stream(
                    "GET",
                    audio_url,
                    headers={"User-Agent": "EchoPress/1.0 (ASR Fallback Downloader)"},
                ) as resp:
                    resp.raise_for_status()
                    with tmp_path.open("wb") as f:
                        async for chunk in resp.aiter_bytes(DOWNLOAD_CHUNK_BYTES):
                            if chunk:
                                f.write(chunk)
        except Exception:
            tmp_path.unlink(missing_ok=True)
            raise

        return tmp_path

    def _cos_ready(self) -> bool:
        return bool(self.secret_id and self.secret_key and self.cos_bucket and self.cos_region)

    def _get_cos_client(self) -> CosS3Client:
        if not self._cos_ready():
            raise RuntimeError("COS 未配置完整")
        if self._cos_client is None:
            config = CosConfig(
                Region=self.cos_region,
                Secret_id=self.secret_id,
                Secret_key=self.secret_key,
            )
            self._cos_client = CosS3Client(config)
        return self._cos_client

    def _build_cos_key(self, source_url: str, file_path: Path) -> str:
        source_hash = hashlib.sha256(source_url.encode("utf-8")).hexdigest()[:16]
        ext = file_path.suffix or ".bin"
        parts = [p for p in (self.cos_prefix, source_hash[:2], f"{source_hash}{ext}") if p]
        return "/".join(parts)

    async def _upload_to_cos(self, file_path: Path, *, source_url: str) -> str:
        key = self._build_cos_key(source_url, file_path)
        client = self._get_cos_client()

        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None,
            lambda: client.upload_file(
                Bucket=self.cos_bucket,
                LocalFilePath=str(file_path),
                Key=key,
                PartSize=10,
                MAXThread=4,
                EnableMD5=False,
            ),
        )
        return f"https://{self.cos_bucket}.cos.{self.cos_region}.myqcloud.com/{key}"

    async def _transcribe_large_local_file(
        self,
        file_path: Path,
        hotwords: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        chunk_dir = Path(tempfile.mkdtemp(prefix="asr_chunks_"))
        try:
            chunk_paths = await self._split_audio_for_local_asr(file_path, chunk_dir)
            if not chunk_paths:
                raise RuntimeError("音频分片失败：未生成任何片段")

            merged_segments: List[Dict[str, Any]] = []
            raw_chunks: List[Dict[str, Any]] = []
            offset = 0.0
            next_speaker_id = 1

            for idx, chunk_path in enumerate(chunk_paths, start=1):
                print(
                    f"[ASR] 分片转写 {idx}/{len(chunk_paths)}: "
                    f"{chunk_path.name} ({chunk_path.stat().st_size / 1024 / 1024:.2f}MB)"
                )
                raw = await self._submit_and_poll(str(chunk_path), hotwords)
                parsed = self._parse_result(raw)
                speaker_map: Dict[str, str] = {}

                for seg in parsed.get("segments", []):
                    label = seg.get("speaker", "说话人1")
                    if label not in speaker_map:
                        speaker_map[label] = f"说话人{next_speaker_id}"
                        next_speaker_id += 1
                    merged_segments.append(
                        {
                            "speaker": speaker_map[label],
                            "text": seg.get("text", ""),
                            "start_time": seg.get("start_time", 0.0) + offset,
                            "end_time": seg.get("end_time", 0.0) + offset,
                        }
                    )

                chunk_duration = self._probe_audio_duration(chunk_path)
                offset += chunk_duration or parsed.get("duration", 0.0)
                raw_chunks.append(
                    {
                        "chunk_index": idx,
                        "path": str(chunk_path),
                        "duration": chunk_duration,
                        "raw_response": raw,
                    }
                )

            speakers = {seg["speaker"] for seg in merged_segments}
            return {
                "segments": merged_segments,
                "full_text": " ".join(seg["text"] for seg in merged_segments),
                "duration": offset,
                "speaker_count": len(speakers),
                "raw_response": {
                    "chunked": True,
                    "chunk_count": len(chunk_paths),
                    "chunks": raw_chunks,
                },
            }
        finally:
            shutil.rmtree(chunk_dir, ignore_errors=True)

    async def _split_audio_for_local_asr(
        self,
        file_path: Path,
        output_dir: Path,
    ) -> List[Path]:
        output_dir.mkdir(parents=True, exist_ok=True)
        output_pattern = output_dir / "chunk_%03d.mp3"
        cmd = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(file_path),
            "-vn",
            "-map",
            "a:0",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-c:a",
            "libmp3lame",
            "-b:a",
            LOCAL_CHUNK_BITRATE,
            "-f",
            "segment",
            "-segment_time",
            str(LOCAL_CHUNK_SECONDS),
            "-reset_timestamps",
            "1",
            str(output_pattern),
        ]
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, lambda: subprocess.run(cmd, check=True))

        chunk_paths = sorted(output_dir.glob("chunk_*.mp3"))
        oversized = [
            p for p in chunk_paths
            if p.stat().st_size > LOCAL_FILE_MAX_BYTES
        ]
        if oversized:
            names = ", ".join(p.name for p in oversized[:3])
            raise RuntimeError(
                f"音频分片后仍有文件超过 5MB 限制: {names}"
            )
        return chunk_paths

    def _probe_audio_duration(self, file_path: Path) -> float:
        cmd = [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(file_path),
        ]
        try:
            completed = subprocess.run(
                cmd,
                check=True,
                capture_output=True,
                text=True,
            )
            return float((completed.stdout or "0").strip() or 0.0)
        except Exception:
            return 0.0

    async def _poll_result(self, task_id: int) -> Dict:
        """轮询任务结果"""
        start_time = time.time()
        loop = asyncio.get_event_loop()

        while True:
            elapsed = time.time() - start_time
            if elapsed > MAX_POLL_WAIT:
                raise TimeoutError(f"ASR任务超时，已等待{MAX_POLL_WAIT}秒")

            await asyncio.sleep(POLL_INTERVAL)

            req = models.DescribeTaskStatusRequest()
            req.TaskId = task_id

            resp = await loop.run_in_executor(
                None, self.client.DescribeTaskStatus, req
            )
            data = resp.Data
            status_str = data.StatusStr
            print(f"[ASR] 任务状态: {status_str} ({int(elapsed)}s)")

            if status_str == "success":
                return json.loads(resp.to_json_string())
            elif status_str == "failed":
                error_msg = data.ErrorMsg or "未知错误"
                raise Exception(f"ASR任务失败: {error_msg}")

    def _parse_result(self, raw_result: Dict) -> Dict[str, Any]:
        """解析腾讯云 ASR 返回结果，输出格式与原阿里云版本兼容"""
        data = raw_result.get("Data", {})
        result_detail = data.get("ResultDetail", [])
        full_text = data.get("Result", "")
        audio_duration = data.get("AudioDuration", 0)

        segments = []
        for item in result_detail:
            text = item.get("FinalSentence", "").strip()
            if not text:
                continue

            start_ms = item.get("StartMs", 0)
            end_ms = item.get("EndMs", 0)
            speaker_id = item.get("SpeakerId", 0)

            segment = {
                "speaker": f"说话人{speaker_id + 1}",
                "text": text,
                "start_time": start_ms / 1000.0,
                "end_time": end_ms / 1000.0,
            }
            segments.append(segment)

        if not segments and full_text:
            print("[ASR] 警告: ResultDetail 为空，使用 Result 全文作为单段")
            segments = [{
                "speaker": "说话人1",
                "text": full_text,
                "start_time": 0.0,
                "end_time": audio_duration,
            }]

        speakers = set(seg["speaker"] for seg in segments)
        duration = max((seg["end_time"] for seg in segments), default=0)

        return {
            "segments": segments,
            "full_text": " ".join(seg["text"] for seg in segments),
            "duration": duration or audio_duration,
            "speaker_count": len(speakers),
            "raw_response": raw_result,
        }



_asr_service: Optional[ASRService] = None


def get_asr_service() -> ASRService:
    """获取ASR服务单例"""
    global _asr_service
    if _asr_service is None:
        _asr_service = ASRService()
    return _asr_service
