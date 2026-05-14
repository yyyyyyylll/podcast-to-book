"""
板块一：内容解析

职责：
1. 音频ASR转写
2. 声纹识别（区分发言人）
3. 智能清理口语表达（LLM判断，避免误删）
4. 纠正语法错误
5. 保留说话者的真实语气和原意
6. LLM 智能语义分段（替代规则合并）
7. 注入播客元数据辅助人称替换和专有名词修正
8. 标记时间戳
9. 支持中英混杂表达

输入：audio_path + 播客元数据
输出：transcription
"""
import hashlib
import json
from pathlib import Path
from typing import Dict, Any, List, Optional

from core.workflow.state import PodBookState, WorkflowStage
from core.workflow.nodes.classify import classify_and_identify
from core.workflow.prompts.registry import get_prompts, get_voice_format_rules
from core.services.asr_service import get_asr_service
from core.services.llm_service import get_llm_service
from core.config import settings


# ===== 说话人暂停异常 =====


class NeedsSpeakerInputError(Exception):
    """当无法识别任何说话人身份时抛出，触发暂停等待用户输入。"""
    def __init__(self, sample_segments: List[Dict], speaker_count: int, classify_result: Dict):
        self.sample_segments = sample_segments
        self.speaker_count = speaker_count
        self.classify_result = classify_result
        super().__init__("无法识别说话人身份，需要用户手动输入")


# ===== ASR 缓存 =====

ASR_CACHE_DIR = Path(settings.STORAGE_DIR) / "asr_cache"


def _asr_cache_key(audio_path: str) -> str:
    """根据音频路径生成缓存 key。本地文件额外混入文件大小以区分同名不同内容的文件。"""
    p = Path(audio_path)
    if p.exists():
        stat = p.stat()
        raw = f"{p.resolve()}|{stat.st_size}|{stat.st_mtime}"
    else:
        raw = audio_path
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


MIN_VALID_SEGMENTS = 10
MIN_VALID_DURATION = 120


def _is_valid_asr_result(data: Dict[str, Any]) -> bool:
    """防御性校验：缓存的 ASR 结果是否像真实转写数据。"""
    segments = data.get("segments", [])
    duration = data.get("duration", 0)
    return len(segments) >= MIN_VALID_SEGMENTS and duration >= MIN_VALID_DURATION


def load_asr_cache(audio_path: str) -> Optional[Dict[str, Any]]:
    """尝试从本地文件加载 ASR 缓存结果，未命中或无效返回 None。"""
    cache_file = ASR_CACHE_DIR / f"{_asr_cache_key(audio_path)}.json"
    if not cache_file.exists():
        return None
    try:
        data = json.loads(cache_file.read_text(encoding="utf-8"))
        if not _is_valid_asr_result(data):
            print(f"[transcription] ASR 缓存无效（片段不足或时长过短），删除并重新转写: {cache_file.name}")
            cache_file.unlink(missing_ok=True)
            return None
        print(f"[transcription] ASR 缓存命中: {cache_file.name}")
        return data
    except Exception as e:
        print(f"[transcription] ASR 缓存读取失败，将重新调用 ASR: {e}")
        return None


def save_asr_cache(audio_path: str, result: Dict[str, Any]) -> None:
    """将 ASR 结果保存到本地缓存文件。"""
    if not _is_valid_asr_result(result):
        print("[transcription] 跳过缓存无效 ASR 结果（片段过少或时长过短）")
        return
    ASR_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_file = ASR_CACHE_DIR / f"{_asr_cache_key(audio_path)}.json"
    to_save = {k: v for k, v in result.items() if k != "raw_response"}
    cache_file.write_text(json.dumps(to_save, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[transcription] ASR 结果已缓存: {cache_file.name}")


# ===== 配置参数 =====


# 每批最大字符数（按说话人轮次分批时的上限）
MAX_BATCH_CHARS = 8000

# 单个轮次文本过长时的硬上限（超过此长度的轮次单独成批）
MAX_SINGLE_TURN_CHARS = 10000


# ===== LLM Prompts（从 registry 加载，保留 fallback 常量用于向后兼容）=====

def _load_clean_prompts(content_type: str = "business", voice_format: str = "dialogue"):
    """从 registry 加载清洗 prompt，根据 content_type 选择基础 prompt，注入 voice_format 保留规则。"""
    try:
        prompts = get_prompts("transcription", content_type)
        clean_system = prompts.get("clean_system", "")
        clean_batch = prompts.get("clean_batch", "")
    except KeyError:
        prompts = get_prompts("transcription", "business")
        clean_system = prompts.get("clean_system", "")
        clean_batch = prompts.get("clean_batch", "")

    rules = get_voice_format_rules(voice_format)
    if rules:
        clean_system = clean_system + "\n\n" + rules

    return clean_system, clean_batch


def build_speaker_mapping_context(speaker_mapping: Dict[str, str]) -> str:
    """将预识别的说话人映射构建为 prompt 上下文块。"""
    if not speaker_mapping:
        return ""

    lines = [
        "",
        "【预识别说话人映射】（已通过全局上下文分析确认，请严格遵循）",
    ]
    for label, name in speaker_mapping.items():
        lines.append(f"- {label} → {name}")
    lines.append("")
    lines.append("请在处理本批次时严格使用以上映射关系。")
    lines.append("如果某个片段的内容明显与映射不符（如ASR将主持人的话标记为嘉宾），")
    lines.append("说明ASR的说话人标记有误，请修正为正确的说话人并设置 speaker_corrected=true。")
    lines.append("【关键】speaker 字段只能使用以上映射中出现的真实姓名。")
    lines.append("对话中被讨论、引用、提及的其他人物不是说话人，不能出现在 speaker 字段中。")

    return "\n".join(lines)


# ===== 分批策略（保持原始顺序，便于说话人修正） =====

# 跨批上下文：每批开头附带前一批最后几个片段作为上下文
CONTEXT_OVERLAP_SEGMENTS = 5


def batch_segments_sequential(
    segments: List[Dict], 
    max_chars: int = MAX_BATCH_CHARS
) -> List[Dict]:
    """
    按片段顺序分批，不预先按说话人聚合。
    
    这样做的原因：ASR的说话人标记可能有误，如果先按说话人聚合，
    会把本应连续的对话割裂，导致LLM难以判断说话人是否正确。
    
    返回: [{"segments": [...], "context_segments": [...], "start_idx": int}]
    - segments: 本批需要处理的片段
    - context_segments: 前一批的最后几个片段（作为上下文，不重复处理）
    - start_idx: 本批第一个片段在全局的起始索引
    """
    if not segments:
        return []
    
    batches = []
    current_batch_segments = []
    current_chars = 0
    batch_start_idx = 0
    
    for i, seg in enumerate(segments):
        seg_chars = len(seg.get("text", ""))
        
        if current_chars + seg_chars > max_chars and current_batch_segments:
            # 当前批次满了，保存并开始新批次
            batches.append({
                "segments": current_batch_segments,
                "start_idx": batch_start_idx,
            })
            batch_start_idx = i
            current_batch_segments = [seg]
            current_chars = seg_chars
        else:
            current_batch_segments.append(seg)
            current_chars += seg_chars
    
    # 最后一批
    if current_batch_segments:
        batches.append({
            "segments": current_batch_segments,
            "start_idx": batch_start_idx,
        })
    
    # 为每批添加上下文（前一批的最后几个片段）
    for i, batch in enumerate(batches):
        if i == 0:
            batch["context_segments"] = []
        else:
            prev_batch = batches[i - 1]
            prev_segments = prev_batch["segments"]
            # 取前一批最后 N 个片段作为上下文
            context = prev_segments[-CONTEXT_OVERLAP_SEGMENTS:] if len(prev_segments) >= CONTEXT_OVERLAP_SEGMENTS else prev_segments
            batch["context_segments"] = context
    
    return batches


# ===== 旧函数（保留以兼容，但已废弃） =====

def group_by_speaker_turn(segments: List[Dict]) -> List[Dict]:
    """
    [已废弃] 将 ASR 片段按说话人轮次分组。
    
    注意：此函数不再推荐使用，因为 ASR 说话人标记可能有误，
    预先聚合会导致上下文割裂，影响 LLM 判断说话人是否正确。
    """
    if not segments:
        return []

    turns: List[Dict] = []
    current_turn: Optional[Dict] = None

    for seg in segments:
        speaker = seg.get("speaker", "未知")
        if current_turn and speaker == current_turn["speaker"]:
            current_turn["segments"].append(seg)
        else:
            if current_turn:
                turns.append(current_turn)
            current_turn = {"speaker": speaker, "segments": [seg]}

    if current_turn:
        turns.append(current_turn)

    return turns


def _turn_char_count(turn: Dict) -> int:
    """[已废弃] 计算单个轮次的字符数"""
    return sum(len(s.get("text", "")) for s in turn["segments"])


def batch_turns(turns: List[Dict], max_chars: int = MAX_BATCH_CHARS) -> List[List[Dict]]:
    """
    [已废弃] 将说话人轮次按字数上限分批。
    
    注意：此函数不再推荐使用，改用 batch_segments_sequential。
    """
    if not turns:
        return []

    batches: List[List[Dict]] = []
    current_batch: List[Dict] = []
    current_chars = 0

    for turn in turns:
        turn_chars = _turn_char_count(turn)

        if turn_chars > max_chars:
            if current_batch:
                batches.append(current_batch)
                current_batch = []
                current_chars = 0
            batches.append([turn])
        elif current_chars + turn_chars > max_chars:
            batches.append(current_batch)
            current_batch = [turn]
            current_chars = turn_chars
        else:
            current_batch.append(turn)
            current_chars += turn_chars

    if current_batch:
        batches.append(current_batch)

    return batches


# ===== 元数据上下文构建 =====

def build_metadata_context(state: PodBookState) -> str:
    """从 state 中提取播客元数据，构建 LLM prompt 上下文块。"""
    lines = []
    title = state.get("title", "")
    host_name = state.get("host_name", "")
    guest_names = state.get("guest_names") or []
    company_names = state.get("company_names") or []
    podcast_intro = state.get("podcast_intro", "") or state.get("description", "")
    proper_nouns = state.get("proper_nouns") or []

    name_aliases = state.get("name_aliases") or {}

    has_metadata = any([host_name, guest_names, company_names, podcast_intro, proper_nouns, name_aliases])
    if not has_metadata and not title:
        return ""

    lines.append("【播客元数据】")
    if title:
        lines.append(f"- 节目名称：{title}")
    if host_name:
        lines.append(f"- 主持人：{host_name}")
    if guest_names:
        lines.append(f"- 嘉宾：{'、'.join(guest_names)}")
    if company_names:
        lines.append(f"- 相关公司/组织：{'、'.join(company_names)}")
    if podcast_intro:
        lines.append(f"- 节目简介：{podcast_intro}")
    if proper_nouns:
        lines.append(f"- 领域专有名词：{'、'.join(proper_nouns)}")
    if name_aliases:
        alias_parts = []
        for primary, aliases in name_aliases.items():
            if aliases:
                alias_parts.append(f"{primary}（又名：{'/'.join(aliases)}）")
        if alias_parts:
            lines.append(f"- 人名别名：{'、'.join(alias_parts)}")
            lines.append("  注意：以上别名指的是同一个人，speaker 字段请统一使用中文主名")

    lines.append("")
    lines.append("请根据以上元数据：")
    lines.append("1. 智能判断每个说话人标签对应的真实身份（提问引导的=主持人，分享经历观点的=嘉宾）")
    lines.append('2. 在 speaker 字段中使用真实姓名，而非"说话人1/2"')
    lines.append("3. 修正被 ASR 错误识别的人名、公司名、产品名")
    lines.append('4. 【重要】不要修改正文中的"我"、"我们"等人称代词，保持原样')
    lines.append("5. 如果无法确定说话人身份，保留原始标签")
    lines.append("6. 【关键】ASR的说话人标记可能混淆！请根据内容语义判断：")
    lines.append("   - 谁在分享个人经历（如创业故事）→ 通常是嘉宾")
    lines.append("   - 谁在提问和引导 → 通常是主持人")
    lines.append("   - 如果内容与标记的说话人不符，应修正为正确的说话人")

    return "\n".join(lines)


# ===== 核心处理逻辑 =====

def _build_conversation_text(
    segments: List[Dict], 
    start_idx: int,
    context_segments: List[Dict] = None,
    context_start_idx: int = 0,
) -> str:
    """
    构建用于 prompt 的对话文本，保持原始片段顺序。
    
    每个片段独立展示，标注说话人和全局索引，便于 LLM：
    1. 判断说话人标记是否正确
    2. 决定哪些片段应该合并
    3. 跨说话人边界进行修正
    
    Args:
        segments: 本批需要处理的片段
        start_idx: 本批第一个片段的全局索引
        context_segments: 上下文片段（来自前一批，仅供参考）
        context_start_idx: 上下文第一个片段的全局索引
    
    Returns:
        格式化的对话文本
    """
    lines = []
    
    # 添加上下文（如果有）
    if context_segments:
        lines.append("【前文上下文（仅供参考，无需重复处理）】")
        for i, seg in enumerate(context_segments):
            idx = context_start_idx + i
            speaker = seg.get("speaker", "未知")
            text = seg.get("text", "")
            lines.append(f"[{idx}] {speaker}: {text}")
        lines.append("")
        lines.append("【本批需要处理的内容】")
    
    # 添加本批片段
    for i, seg in enumerate(segments):
        idx = start_idx + i
        speaker = seg.get("speaker", "未知")
        text = seg.get("text", "")
        lines.append(f"[{idx}] {speaker}: {text}")
    
    return "\n".join(lines)


async def llm_clean_and_segment(
    segments: List[Dict],
    state: PodBookState,
    speaker_mapping: Optional[Dict[str, str]] = None,
    content_type: str = "business",
    voice_format: str = "dialogue",
) -> List[Dict]:
    """
    使用 LLM 同时完成口语清理 + 智能语义分段 + 说话人修正。
    
    流程：
    1. 按片段顺序分批（不预先按说话人聚合，保持原始对话流）
    2. 每批提供前一批的上下文，便于 LLM 判断跨批边界的说话人
    3. 利用预识别的说话人映射提高识别准确率
    4. LLM 根据内容语义判断并修正 ASR 的说话人标记错误
    5. LLM 返回清理 + 合并 + 说话人修正后的 segments
    """
    if not segments:
        return []

    clean_system_prompt, clean_batch_prompt = _load_clean_prompts(content_type, voice_format)

    llm_service = get_llm_service()
    metadata_context = build_metadata_context(state)
    speaker_mapping_context = build_speaker_mapping_context(speaker_mapping or {})

    # 按片段顺序分批（不预聚合，便于 LLM 判断说话人）
    batches = batch_segments_sequential(segments)
    print(f"[transcription] 按顺序分批: {len(segments)} 片段 → {len(batches)} 批")

    all_cleaned: List[Dict] = []
    max_retries = 2

    # 从元数据构建期望的真实姓名集合，用于检测说话人映射是否成功
    expected_names: set = set()
    host = state.get("host_name", "")
    if host:
        expected_names.add(host)
    for g in (state.get("guest_names") or []):
        expected_names.add(g)

    import re as _re_inner
    _generic_pat = _re_inner.compile(r"^说话人\d+$")

    for batch_idx, batch in enumerate(batches):
        batch_segments = batch["segments"]
        start_idx = batch["start_idx"]
        context_segments = batch.get("context_segments", [])
        
        # 计算上下文的起始索引
        context_start_idx = start_idx - len(context_segments) if context_segments else 0
        
        # 构建对话文本
        conversations_text = _build_conversation_text(
            segments=batch_segments,
            start_idx=start_idx,
            context_segments=context_segments,
            context_start_idx=context_start_idx,
        )

        prompt = clean_batch_prompt.format(
            metadata_context=metadata_context,
            speaker_mapping_context=speaker_mapping_context,
            conversations=conversations_text,
        )

        batch_success = False
        for attempt in range(max_retries + 1):
            try:
                if attempt > 0:
                    import asyncio
                    wait = 10 * attempt
                    print(f"[transcription] 批次 {batch_idx} 第 {attempt+1} 次重试（等待 {wait}s）...")
                    await asyncio.sleep(wait)

                result = await llm_service.generate_json(
                    prompt=prompt,
                    system_prompt=clean_system_prompt,
                    temperature=0.3,
                    model=settings.LLM_MODEL,
                    max_tokens=65536,
                    thinking_budget=0,
                    timeout=180,
                )

                cleaned_list = result.get("segments", [])

                # 先收集本批次的结果到临时列表，校验通过后再追加到 all_cleaned
                batch_items: List[Dict] = []

                for item in cleaned_list:
                    cleaned_text = (item.get("cleaned_text") or "").strip()
                    if not cleaned_text:
                        continue

                    source_indices = item.get("source_indices", [])
                    speaker = item.get("speaker", "未知")
                    speaker_corrected = item.get("speaker_corrected", False)

                    # 过滤掉属于上下文的索引（上下文片段已在前一批处理过）
                    valid_indices = [si for si in source_indices if si >= start_idx]
                    if not valid_indices:
                        continue

                    start_time = 0.0
                    end_time = 0.0
                    original_texts = []
                    original_speakers = []
                    for si in valid_indices:
                        if 0 <= si < len(segments):
                            seg = segments[si]
                            if not start_time or seg.get("start_time", 0) < start_time:
                                start_time = seg.get("start_time", 0)
                            if seg.get("end_time", 0) > end_time:
                                end_time = seg.get("end_time", 0)
                            original_texts.append(seg.get("text", ""))
                            original_speakers.append(seg.get("speaker", "未知"))

                    result_item = {
                        "speaker": speaker,
                        "text": cleaned_text,
                        "start_time": start_time,
                        "end_time": end_time,
                        "original_text": " | ".join(original_texts),
                    }
                    
                    if speaker_corrected or (original_speakers and speaker not in original_speakers):
                        result_item["speaker_corrected"] = True
                        result_item["original_speaker"] = original_speakers[0] if original_speakers else "未知"
                        if result_item["original_speaker"] != speaker:
                            print(f"[transcription] 说话人修正: {result_item['original_speaker']} → {speaker}")

                    batch_items.append(result_item)

                # 检测说话人映射是否成功：如果元数据有真实姓名，
                # 但本批次大部分片段仍为泛称，视为软失败并重试
                if expected_names and batch_items:
                    generic_count = sum(1 for it in batch_items if _generic_pat.match(it["speaker"]))
                    generic_ratio = generic_count / len(batch_items)
                    if generic_ratio > 0.5 and attempt < max_retries:
                        print(
                            f"[transcription] 批次 {batch_idx} 说话人映射不完整 "
                            f"({generic_count}/{len(batch_items)} 仍为泛称)，重试..."
                        )
                        continue

                all_cleaned.extend(batch_items)
                batch_success = True
                break

            except Exception as e:
                print(f"[transcription] LLM处理批次 {batch_idx} 失败 (attempt {attempt+1}/{max_retries+1}): {e}")

        if not batch_success:
            print(f"[transcription] 批次 {batch_idx} 全部重试耗尽，降级为原始数据")
            # 降级：直接使用原始片段，按连续相同说话人简单合并
            current_speaker = None
            current_texts = []
            current_start = 0.0
            current_end = 0.0
            
            for seg in batch_segments:
                seg_speaker = seg.get("speaker", "未知")
                if seg_speaker == current_speaker:
                    current_texts.append(seg.get("text", ""))
                    current_end = seg.get("end_time", 0)
                else:
                    if current_texts:
                        all_cleaned.append({
                            "speaker": current_speaker,
                            "text": "".join(current_texts),
                            "start_time": current_start,
                            "end_time": current_end,
                            "original_text": "".join(current_texts),
                        })
                    current_speaker = seg_speaker
                    current_texts = [seg.get("text", "")]
                    current_start = seg.get("start_time", 0)
                    current_end = seg.get("end_time", 0)
            
            if current_texts:
                all_cleaned.append({
                    "speaker": current_speaker,
                    "text": "".join(current_texts),
                    "start_time": current_start,
                    "end_time": current_end,
                    "original_text": "".join(current_texts),
                })

    return all_cleaned


import re as _re

_GENERIC_SPEAKER_RE = _re.compile(r"^说话人\d+$")


def fix_unmapped_speakers(segments: List[Dict]) -> List[Dict]:
    """
    后处理：将仍保留泛称（说话人1/2/…）的片段映射为真实姓名。

    策略：从已成功修正 speaker 的片段中统计映射关系
    （original_speaker → speaker），以多数票决定每个泛称对应的真实姓名，
    然后批量替换。
    """
    if not segments:
        return segments

    # 1. 从 speaker_corrected 片段收集映射: 泛称 → {真实姓名: 出现次数}
    mapping_votes: Dict[str, Dict[str, int]] = {}
    for seg in segments:
        if not seg.get("speaker_corrected"):
            continue
        orig = seg.get("original_speaker", "")
        real = seg.get("speaker", "")
        if orig and real and _GENERIC_SPEAKER_RE.match(orig) and not _GENERIC_SPEAKER_RE.match(real):
            mapping_votes.setdefault(orig, {})
            mapping_votes[orig][real] = mapping_votes[orig].get(real, 0) + 1

    if not mapping_votes:
        return segments

    # 2. 取每个泛称的最高票真实姓名
    speaker_map: Dict[str, str] = {}
    for generic, votes in mapping_votes.items():
        best = max(votes, key=votes.get)  # type: ignore
        speaker_map[generic] = best

    # 3. 找出仍使用泛称的片段并替换
    fixed_count = 0
    for seg in segments:
        speaker = seg.get("speaker", "")
        if _GENERIC_SPEAKER_RE.match(speaker) and speaker in speaker_map:
            seg["speaker"] = speaker_map[speaker]
            fixed_count += 1

    if fixed_count:
        print(f"[transcription] 说话人映射修复: {fixed_count} 个片段")
        for generic, real in speaker_map.items():
            print(f"  - {generic} → {real}")

    return segments


MERGE_MAX_CHARS_MONOLOGUE = 3000
MERGE_MAX_CHARS_MULTI = 3000


def merge_consecutive_same_speaker(segments: List[Dict]) -> List[Dict]:
    """
    后处理：合并同一说话人的连续发言。
    
    LLM 可能因为话题切换而保持分段，但从阅读体验来看，
    同一说话人的连续发言（未被其他人打断）应该被合并为一段。
    
    根据说话人数量自动选择合并上限：
    - 单人播客（独白）：较紧的上限，保证下游章节规划有足够的 segment 粒度
    - 多人播客（访谈）：较宽的上限，尊重长发言的完整性，同时防止极端合并
    """
    if not segments:
        return []

    _NON_HUMAN_KW = ("BGM", "片尾", "片头", "过场", "音效", "音乐")
    real_speakers = set(
        seg.get("speaker", "未知") for seg in segments
        if not any(kw in seg.get("speaker", "") for kw in _NON_HUMAN_KW)
    )
    max_chars = MERGE_MAX_CHARS_MONOLOGUE if len(real_speakers) <= 1 else MERGE_MAX_CHARS_MULTI
    print(f"[transcription] 合并上限: {max_chars} 字符 ({len(real_speakers)} 位真实说话人)")

    def _new_seg(seg):
        return {
            "speaker": seg.get("speaker", "未知"),
            "text": seg.get("text", ""),
            "start_time": seg.get("start_time", 0),
            "end_time": seg.get("end_time", 0),
            "original_text": seg.get("original_text", ""),
            "speaker_corrected": seg.get("speaker_corrected", False),
            "original_speaker": seg.get("original_speaker"),
        }

    merged: List[Dict] = []
    current = None
    
    for seg in segments:
        speaker = seg.get("speaker", "未知")
        seg_text = seg.get("text", "")
        
        if current is None:
            current = _new_seg(seg)
        elif speaker == current["speaker"]:
            would_be_len = len(current["text"]) + len(seg_text)
            if would_be_len > max_chars:
                merged.append(current)
                current = _new_seg(seg)
            else:
                current["text"] += " " + seg_text
                current["end_time"] = seg.get("end_time", 0)
                current["original_text"] += " | " + seg.get("original_text", "")
                if seg.get("speaker_corrected"):
                    current["speaker_corrected"] = True
                    if not current.get("original_speaker"):
                        current["original_speaker"] = seg.get("original_speaker")
        else:
            merged.append(current)
            current = _new_seg(seg)
    
    if current:
        merged.append(current)
    
    for seg in merged:
        if seg.get("original_speaker") is None:
            seg.pop("original_speaker", None)
        if not seg.get("speaker_corrected"):
            seg.pop("speaker_corrected", None)
    
    return merged


async def _merge_speaker_aliases(
    all_speakers: list,
    segments: List[Dict],
    seg_counts: Dict[str, int],
    state: dict,
) -> tuple:
    """检测并合并同一人的不同名字（如中文名/英文名/昵称）。

    策略：
    1. 如果说话人数超过 ASR 识别的说话人数，说明有别名问题
    2. 用 LLM 判断哪些名字是同一人
    3. 合并 segments 中的说话人标签
    """
    if len(all_speakers) <= 1:
        return all_speakers, segments

    _NON_HUMAN_KW = ("BGM", "片尾", "片头", "过场", "音效", "音乐")
    real_speaker_names = [s for s in all_speakers if not any(kw in s for kw in _NON_HUMAN_KW)]

    # 先用 state 中的 name_aliases 做规则匹配
    known_aliases = state.get("name_aliases") or {}
    rule_alias_map: Dict[str, str] = {}
    for primary, aliases in known_aliases.items():
        for alias in aliases:
            if alias in real_speaker_names and primary in real_speaker_names:
                rule_alias_map[alias] = primary
                print(f"[transcription] 规则别名合并: {alias} → {primary}（来自元数据）")

    if rule_alias_map:
        for seg in segments:
            sp = seg.get("speaker", "")
            if sp in rule_alias_map:
                seg["speaker"] = rule_alias_map[sp]
        new_counts: Dict[str, int] = {}
        for seg in segments:
            sp = seg.get("speaker", "")
            if sp:
                new_counts[sp] = new_counts.get(sp, 0) + 1
        all_speakers = sorted(
            set(seg["speaker"] for seg in segments if seg.get("speaker")),
            key=lambda s: -new_counts.get(s, 0),
        )
        real_speaker_names = [s for s in all_speakers if not any(kw in s for kw in _NON_HUMAN_KW)]
        seg_counts = new_counts

    if len(real_speaker_names) <= 2:
        return all_speakers, segments

    # 多于 2 位真实说话人名，可能存在别名 → 用 LLM 判断
    llm_service = get_llm_service()
    podcast_name = state.get("podcast_name", "")
    title = state.get("title", "")
    host = state.get("host_name", "")
    guests = state.get("guest_names") or []

    speaker_info = ", ".join(
        f"{sp}({seg_counts.get(sp, 0)}个片段)" for sp in real_speaker_names
    )

    prompt = (
        f"播客「{podcast_name}」的一期节目「{title}」。\n"
        f"元数据主持人：{host}，嘉宾：{'、'.join(guests)}\n\n"
        f"转写结果中出现以下说话人（含片段数）：{speaker_info}\n\n"
        f"请判断这些名字中，是否有指向同一个人的别名（如中文名/英文名/昵称）。\n"
        f"例如：'季逸超' 和 'Peak' 可能是同一人的中英文名。\n\n"
        f"返回 JSON：{{\"aliases\": [[\"主名\", \"别名1\", \"别名2\"]]}}，\n"
        f"每个内部数组代表一组同一人的名字，第一个为主名（优先中文名，优先片段数多的名字）。\n"
        f"如果没有别名情况，返回 {{\"aliases\": []}}。"
    )

    try:
        result = await llm_service.generate_json(
            prompt=prompt,
            system_prompt="你是播客元数据校验助手。判断说话人名字中是否有别名（同一人的不同称呼）。",
            temperature=0.1,
            model=settings.LLM_MODEL,
            max_tokens=2048,
            thinking_budget=0,
            timeout=30,
            label="说话人别名合并",
        )
        alias_groups = result.get("aliases", [])
        if not alias_groups:
            return all_speakers, segments

        # 构建 alias → primary 映射
        alias_map: Dict[str, str] = {}
        for group in alias_groups:
            if not isinstance(group, list) or len(group) < 2:
                continue
            primary = group[0]
            for alias in group[1:]:
                if alias != primary and alias in real_speaker_names:
                    alias_map[alias] = primary
                    print(f"[transcription] 说话人别名合并: {alias} → {primary}")

        if not alias_map:
            return all_speakers, segments

        # 替换 segments 中的说话人标签
        for seg in segments:
            sp = seg.get("speaker", "")
            if sp in alias_map:
                seg["speaker"] = alias_map[sp]

        # 重新构建 all_speakers
        new_counts: Dict[str, int] = {}
        for seg in segments:
            sp = seg.get("speaker", "")
            if sp:
                new_counts[sp] = new_counts.get(sp, 0) + 1
        new_all_speakers = sorted(
            set(seg["speaker"] for seg in segments if seg.get("speaker")),
            key=lambda s: -new_counts.get(s, 0),
        )
        return new_all_speakers, segments

    except Exception as e:
        print(f"[transcription] 说话人别名检测失败，跳过: {e}")
        return all_speakers, segments


async def _llm_verify_guests(
    llm_service,
    candidate_guests: list,
    host_names: set,
    state: dict,
) -> list:
    """用 LLM 判断候选嘉宾是否真的是播客中的发言人。

    排除两种情况：
    1. 其实是主持人（使用了昵称、英文名、艺名等不同称呼）
    2. 根本不是播客中的说话人，只是被讨论/提及的人物名字
    """
    podcast_name = state.get("podcast_name", "")
    title = state.get("title", "")
    description = (state.get("description") or "")[:500]

    prompt = (
        f"播客「{podcast_name}」的一期节目「{title}」。\n"
        f"节目简介：{description}\n\n"
        f"元数据记录的主持人名称：{'、'.join(sorted(host_names))}\n"
        f"转写中出现以下说话人标签（不含主持人）：{'、'.join(candidate_guests)}\n\n"
        f"请判断这些人名中，哪些是**真正在播客中发言的嘉宾**。需要排除以下两种情况：\n"
        f"1. 其实是主持人的别名（昵称、英文名、艺名等不同称呼）\n"
        f"2. 根本不是播客中的说话人——只是被主持人或嘉宾在对话中讨论、引用、提及的人物\n"
        f"   （例如：谈论某位行业人物的观点、引用某人的话、提及某个朋友的经历，"
        f"这些人并没有参与播客录制）\n\n"
        f"返回 JSON：{{\"actual_guests\": [\"确认是播客中真正发言的嘉宾的名字\"]}}，\n"
        f"不确定的人宁可排除，不要放入 actual_guests。\n"
        f"如果全部都不是真正的嘉宾，返回 {{\"actual_guests\": []}}。"
    )

    try:
        result = await llm_service.generate_json(
            prompt=prompt,
            system_prompt="你是播客元数据校验助手。根据上下文判断说话人身份，区分'真正在播客中说话的人'和'只是被谈论/提及的人'。",
            temperature=0.1,
            model=settings.LLM_MODEL,
            max_tokens=2048,
            thinking_budget=0,
            timeout=30,
            label="校验嘉宾身份",
        )
        actual_guests = result.get("actual_guests", candidate_guests)
        if set(actual_guests) != set(candidate_guests):
            removed = set(candidate_guests) - set(actual_guests)
            print(f"[transcription] LLM 嘉宾校验: 排除 {removed}（主持人别名或被提及的非发言人）")
        return [g for g in candidate_guests if g in actual_guests]
    except Exception as e:
        print(f"[transcription] LLM 嘉宾校验失败，保留原始结果: {e}")
        return candidate_guests


# ===== 节点入口 =====

async def transcription_node(state: PodBookState) -> Dict[str, Any]:
    """
    内容解析节点
    
    处理流程：
    1. 调用ASR服务进行音频转写（含说话人分离）
    2. 按说话人轮次聚合，按字数上限分批
    3. LLM 同时完成：口语清理 + 智能语义分段 + 元数据人称替换
    4. 输出结构化结果
    """
    print(f"[transcription] 开始处理任务: {state['task_id']}")
    print(f"[transcription] 音频路径: {state['audio_path']}")

    # 打印元数据信息
    host = state.get("host_name", "")
    guests = state.get("guest_names") or []
    companies = state.get("company_names") or []
    if host or guests or companies:
        print(f"[transcription] 元数据: 主持人={host}, 嘉宾={guests}, 公司={companies}")

    audio_path = state["audio_path"]

    # Step 1: 调用ASR服务（优先使用缓存）
    print("[transcription] Step 1: 调用ASR服务...")
    proper_nouns = state.get("proper_nouns") or []

    raw_transcription = load_asr_cache(audio_path)
    if raw_transcription is None:
        asr_service = get_asr_service()
        raw_transcription = await asr_service.transcribe(audio_path, hotwords=proper_nouns or None)
        save_asr_cache(audio_path, raw_transcription)

    raw_segments = raw_transcription.get("segments", [])
    print(f"[transcription] ASR完成，共 {len(raw_segments)} 个原始片段")

    if not raw_segments:
        print("[transcription] 警告: 没有转写结果")
        return {
            "transcription": {
                "segments": [],
                "full_text": "",
                "duration": 0,
                "speaker_count": 0,
            },
            "current_stage": WorkflowStage.RESTRUCTURE.value,
        }

    # Step 1.5: 统一分类 + 说话人识别（合并为一次 LLM 调用）
    print("[transcription] Step 1.5: 分类 + 说话人识别...")
    metadata_context = build_metadata_context(state)
    classify_result = await classify_and_identify(raw_segments, state, metadata_context)

    content_type = classify_result["content_type"]
    narrative_type = classify_result["narrative_type"]
    voice_format = classify_result["voice_format"]
    speaker_roles = classify_result.get("speaker_roles", {})
    speaker_mapping = classify_result["speaker_mapping"]

    # Step 1.6: 确定性名字分配 + 验证
    from core.workflow.nodes.classify import build_deterministic_mapping, validate_mapping_coverage

    user_hosts = [h.strip() for h in (state.get("user_host_name") or "").split("、") if h.strip()]
    if not user_hosts:
        user_hosts = [h.strip() for h in (state.get("user_host_name") or "").split(",") if h.strip()]
    user_guests = list(state.get("user_guest_names") or [])

    if user_hosts or user_guests:
        final_mapping = build_deterministic_mapping(
            speaker_roles, speaker_mapping, user_hosts, user_guests,
        )
        missing = validate_mapping_coverage(final_mapping, user_hosts, user_guests)
        if missing:
            print(f"[transcription] 映射缺失用户提供的名字 {missing}，重试 classify...")
            retry_result = await classify_and_identify(raw_segments, state, metadata_context)
            retry_roles = retry_result.get("speaker_roles", {})
            retry_names = retry_result.get("speaker_mapping", {})
            final_mapping = build_deterministic_mapping(
                retry_roles or speaker_roles, retry_names or speaker_mapping,
                user_hosts, user_guests,
            )
            still_missing = validate_mapping_coverage(final_mapping, user_hosts, user_guests)
            if still_missing:
                print(f"[transcription] 重试后仍缺失 {still_missing}，保留重试结果（已尽力匹配）")
        speaker_mapping = final_mapping
        print(f"[transcription] 最终说话人映射 (确定性): {speaker_mapping}")

    # Step 1.6b: 检测是否需要用户手动输入说话人
    # 触发条件（全部满足才触发，确保是真正的"无名"场景）：
    #   1. 用户没有提供任何主持/嘉宾名字
    #   2. 元数据也没有主持/嘉宾名字
    #   3. classify LLM 也无法推断出任何名字映射
    #   4. ASR 检测到 ≥2 位说话人（单人播客不需要区分）
    import re as _re_generic_check
    _generic_check = _re_generic_check.compile(r"^说话人\d+$")

    _has_user_names = bool(user_hosts or user_guests)
    _meta_host = state.get("host_name", "").strip()
    _meta_guests = state.get("guest_names") or []
    _has_metadata_names = bool(_meta_host) or (isinstance(_meta_guests, list) and len(_meta_guests) > 0)
    _has_classify_names = bool(speaker_mapping) and any(
        not _generic_check.match(v) for v in speaker_mapping.values()
    )
    _asr_speakers = set(seg.get("speaker", "") for seg in raw_segments)
    _asr_speaker_count = len([s for s in _asr_speakers if s and _generic_check.match(s)])

    if (not _has_user_names
            and not _has_metadata_names
            and not _has_classify_names
            and _asr_speaker_count >= 2):
        print(f"[transcription] ⚠ 无法识别任何说话人身份 (ASR={_asr_speaker_count}人)，请求用户输入")
        sample_indices = []
        for i in range(min(15, len(raw_segments))):
            sample_indices.append(i)
        mid = len(raw_segments) // 2
        for i in range(max(0, mid - 3), min(len(raw_segments), mid + 3)):
            if i not in sample_indices:
                sample_indices.append(i)
        sample_indices.sort()

        sample_segments = []
        for i in sample_indices:
            seg = raw_segments[i]
            sample_segments.append({
                "index": i,
                "speaker": seg.get("speaker", "未知"),
                "text": seg.get("text", "")[:200],
            })

        raise NeedsSpeakerInputError(
            sample_segments=sample_segments,
            speaker_count=_asr_speaker_count,
            classify_result={
                "content_type": content_type,
                "narrative_type": narrative_type,
                "voice_format": voice_format,
            },
        )

    # Step 1.7: 确定性预替换 raw segments 的说话人标签
    if speaker_mapping:
        replaced_count = 0
        for seg in raw_segments:
            label = seg.get("speaker", "")
            if label in speaker_mapping:
                seg["speaker"] = speaker_mapping[label]
                replaced_count += 1
        if replaced_count:
            print(f"[transcription] 预替换说话人标签: {replaced_count} 个片段")

    # Step 2: LLM 智能清理 + 语义分段 + 元数据人称替换（使用 content_type-aware prompt）
    print(f"[transcription] Step 2: LLM 智能清理 (content={content_type}, "
          f"narrative={narrative_type}, voice={voice_format})...")
    cleaned_segments = await llm_clean_and_segment(
        raw_segments, state,
        speaker_mapping=speaker_mapping,
        content_type=content_type,
        voice_format=voice_format,
    )

    # Step 3: 过滤空片段
    non_empty_segments = [seg for seg in cleaned_segments if seg["text"].strip()]

    # Step 3.5: 修复未映射的泛称说话人（说话人1/2 → 真实姓名）
    non_empty_segments = fix_unmapped_speakers(non_empty_segments)

    # Step 4: 合并同一说话人的连续发言（仅多人播客）
    # 过滤 BGM/音效等非人声，避免误判独白为多人播客
    _NON_HUMAN_KEYWORDS = ("BGM", "片尾", "片头", "过场", "音效", "音乐")
    real_speakers = set(
        seg["speaker"] for seg in non_empty_segments
        if not any(kw in seg["speaker"] for kw in _NON_HUMAN_KEYWORDS)
    )
    if len(real_speakers) <= 1:
        print(f"[transcription] Step 4: 检测到单人播客 (真实说话人={real_speakers})，"
              f"跳过同说话人合并（保留 LLM 语义分段结构）")
        final_segments = non_empty_segments
    else:
        print("[transcription] Step 4: 合并同一说话人的连续发言...")
        final_segments = merge_consecutive_same_speaker(non_empty_segments)
        print(f"[transcription] 合并后: {len(non_empty_segments)} → {len(final_segments)} 个片段")

    # 统计说话人修正数量
    speaker_corrections = sum(1 for seg in final_segments if seg.get("speaker_corrected"))

    # 用转写阶段识别到的真正发言人替换元数据阶段可能不准确的 guest_names
    # 当用户显式提供了 host/guest 名字时，视为权威值，跳过校正
    _user_host = (state.get("user_host_name") or "").strip()
    _user_guests = list(state.get("user_guest_names") or [])

    _GENERIC_SPEAKERS = {"观众", "听众", "主持人", "嘉宾", "未知", "提问者"}
    _ROLE_KEYWORDS = ("播报员", "播音员", "旁白", "配音", "解说", "主播", "导播", "片头", "片尾", "主持人")

    # 统计各说话人的片段数
    _speaker_seg_counts: Dict[str, int] = {}
    for seg in final_segments:
        sp = seg.get("speaker", "")
        if sp:
            _speaker_seg_counts[sp] = _speaker_seg_counts.get(sp, 0) + 1

    # 过滤极低频说话人：出现片段数不足总片段数 1% 且不超过 2 个的说话人
    # 很可能是对话中被提及的人名被错误识别为说话人
    _total_segs = len(final_segments)
    _MIN_SPEAKER_RATIO = 0.01
    _MIN_SPEAKER_SEGS = 3
    _filtered_out_speakers: set = set()
    for sp, count in _speaker_seg_counts.items():
        if count < _MIN_SPEAKER_SEGS and (_total_segs > 0 and count / _total_segs < _MIN_SPEAKER_RATIO):
            _filtered_out_speakers.add(sp)

    if _filtered_out_speakers:
        print(f"[transcription] 过滤极低频说话人（可能是被提及的人名）: {_filtered_out_speakers}")

    all_speakers = sorted(
        set(seg["speaker"] for seg in final_segments if seg.get("speaker") and seg["speaker"] not in _filtered_out_speakers),
        key=lambda s: -_speaker_seg_counts.get(s, 0),
    )
    state_updates: Dict[str, Any] = {}
    host = state.get("host_name", "")

    import re as _re_host
    _host_names_set = set(
        name.strip() for name in _re_host.split(r'[、，,&/和与\s]+', host)
        if name.strip()
    )

    def _char_diff(a: str, b: str) -> int:
        """两个等长字符串的不同字符数。"""
        return sum(1 for x, y in zip(a, b) if x != y)

    _NICKNAME_PREFIXES = ("小", "老", "阿", "大")

    def _is_host_speaker(speaker: str) -> bool:
        sp = speaker.strip()
        for h in _host_names_set:
            if sp == h:
                return True
            if len(h) >= 2 and h in sp:
                return True
            if len(sp) >= 2 and sp in h:
                return True
            if len(sp) >= 3 and len(sp) == len(h) and _char_diff(sp, h) == 1:
                return True
            if len(h) == 1 and len(sp) == 2 and sp.startswith(_NICKNAME_PREFIXES) and sp[1] == h:
                return True
        return False

    def _is_generic_role(speaker: str) -> bool:
        if speaker in _GENERIC_SPEAKERS:
            return True
        return any(kw in speaker for kw in _ROLE_KEYWORDS)

    def _is_exact_host(speaker: str) -> bool:
        """精确或子串匹配（不含编辑距离），用于更新 host_name。"""
        sp = speaker.strip()
        for h in _host_names_set:
            if sp == h:
                return True
            if len(h) >= 2 and h in sp:
                return True
            if len(sp) >= 2 and sp in h:
                return True
        return False

    if all_speakers:
        # Step 5.1: 别名合并 — 检测同一人的不同名字并合并
        all_speakers, final_segments = await _merge_speaker_aliases(
            all_speakers, final_segments, _speaker_seg_counts, state
        )

        # 嘉宾名单校正：仅当用户未显式提供嘉宾名时执行
        if _user_guests:
            print(f"[transcription] 用户显式提供嘉宾名 {_user_guests}，跳过嘉宾名单校正")
        else:
            non_generic_speakers = [s for s in all_speakers if not _is_generic_role(s)]
            verified_guests = [
                s for s in non_generic_speakers
                if not _is_host_speaker(s)
            ]

            if verified_guests and _host_names_set:
                verified_guests = await _llm_verify_guests(
                    get_llm_service(), verified_guests, _host_names_set, state
                )

            old_guests = list(state.get("guest_names") or [])
            if verified_guests != old_guests:
                state_updates["guest_names"] = verified_guests
                print(f"[transcription] 嘉宾名单校正: {old_guests} → {verified_guests}")

        # 主持人名称校正：仅当用户未显式提供主持人名时执行
        if _user_host:
            print(f"[transcription] 用户显式提供主持人名 '{_user_host}'，跳过主持人校正")
        else:
            if not host and len(all_speakers) >= 2:
                state_updates["host_name"] = all_speakers[0]
                print(f"[transcription] 从转写结果推断主持人: {all_speakers[0]}")
            elif host:
                exact_hosts = [s for s in all_speakers if _is_exact_host(s)]
                if exact_hosts:
                    actual_host = "、".join(exact_hosts)
                    if actual_host != host:
                        state_updates["host_name"] = actual_host
                        print(f"[transcription] 主持人名称校正: {host} → {actual_host}")

    # 构建输出
    transcription = {
        "segments": final_segments,
        "full_text": "\n".join([
            f"【{seg['speaker']}】{seg['text']}"
            for seg in final_segments
        ]),
        "duration": raw_transcription.get("duration", 0),
        "speaker_count": len(set(seg["speaker"] for seg in final_segments)),
        "segment_count": len(final_segments),
        "raw_segment_count": len(raw_segments),
        "speaker_corrections": speaker_corrections,
    }

    print(f"[transcription] 完成！")
    print(f"  - 原始片段: {len(raw_segments)}")
    print(f"  - 最终片段: {len(final_segments)}")
    print(f"  - 说话人数: {transcription['speaker_count']}")
    print(f"  - 说话人修正: {speaker_corrections} 处")
    print(f"  - 总时长: {transcription['duration']:.1f}秒")

    return {
        "transcription": transcription,
        "content_type": content_type,
        "narrative_type": narrative_type,
        "voice_format": voice_format,
        "current_stage": WorkflowStage.RESTRUCTURE.value,
        **state_updates,
    }


def format_timestamp(seconds: float) -> str:
    """将秒数格式化为 MM:SS 格式"""
    minutes = int(seconds // 60)
    secs = int(seconds % 60)
    return f"{minutes:02d}:{secs:02d}"
