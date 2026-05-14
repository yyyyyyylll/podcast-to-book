import { useEffect, useState, useRef } from 'react';
import { getTaskLLMLogs, type LLMLogsResponse, type LLMCallLog } from '../services/api';

const STAGE_LABELS: Record<string, string> = {
  transcription: '音频转写',
  compose: '内容成稿',
  extraction: '精华提炼',
  annotation: '专业注释',
  editor_preface: '编者序',
  illustration: '配图生成',
  typeset: '排版输出',
  unknown: '准备中',
};

function formatDuration(s: number): string {
  if (s < 0.1) return '< 0.1s';
  if (s < 60) return `${s.toFixed(1)}s`;
  const m = Math.floor(s / 60);
  const sec = s % 60;
  return `${m}m ${sec.toFixed(0)}s`;
}

function formatTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return String(n);
}

function StatusDot({ status }: { status: string }) {
  const color =
    status === 'running' ? '#F59E0B' :
    status === 'success' ? '#10B981' :
    '#EF4444';
  return (
    <span style={{
      display: 'inline-block',
      width: 7,
      height: 7,
      borderRadius: '50%',
      background: color,
      marginRight: 6,
      flexShrink: 0,
      animation: status === 'running' ? 'llm-pulse 1.5s ease-in-out infinite' : 'none',
    }} />
  );
}

function CallTypeTag({ call }: { call: LLMCallLog }) {
  if (call.is_image) return <span style={{ fontSize: 10, color: '#8B5CF6', marginLeft: 4 }}>IMG</span>;
  if (call.is_vlm) return <span style={{ fontSize: 10, color: '#8B5CF6', marginLeft: 4 }}>VLM</span>;
  if (call.is_search) return <span style={{ fontSize: 10, color: '#3B82F6', marginLeft: 4 }}>WEB</span>;
  return null;
}

function CallRow({ call, now }: { call: LLMCallLog; now: number }) {
  const elapsed = call.status === 'running'
    ? (now - call.started_at)
    : call.duration_s;
  const displayLabel = call.label || STAGE_LABELS[call.stage] || call.stage;

  return (
    <div style={{
      display: 'grid',
      gridTemplateColumns: '22px 1fr 80px 80px 60px 58px',
      alignItems: 'center',
      padding: '5px 0',
      borderBottom: '1px solid var(--border, #E8E8E4)',
      fontSize: 13,
      opacity: call.status === 'running' ? 1 : 0.7,
      fontVariantNumeric: 'tabular-nums',
    }}>
      <StatusDot status={call.status} />
      <div style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', paddingRight: 8 }}>
        <span style={{ color: 'var(--text-main, #333)', fontWeight: call.status === 'running' ? 500 : 400 }}>
          {displayLabel}
        </span>
        <CallTypeTag call={call} />
        <span style={{ color: '#BBB', fontSize: 11, marginLeft: 6 }}>
          {call.prompt_chars > 0 && `${formatTokens(call.prompt_chars)}字`}
        </span>
      </div>
      <div style={{ textAlign: 'right', paddingRight: 10, fontSize: 12, color: '#888' }}>
        {call.total_tokens > 0 ? (
          <span title={`in ${formatTokens(call.prompt_tokens)} / out ${formatTokens(call.completion_tokens)}`}>
            {formatTokens(call.total_tokens)}
          </span>
        ) : call.status === 'running' ? (
          <span style={{ color: '#DDD' }}>…</span>
        ) : '—'}
      </div>
      <div style={{ textAlign: 'right', paddingRight: 10, fontSize: 12 }}>
        {call.tokens_per_sec > 0 ? (
          <span style={{ color: call.tokens_per_sec < 10 ? '#EF4444' : '#888' }}>
            {call.tokens_per_sec} t/s
          </span>
        ) : ''}
      </div>
      <div style={{
        textAlign: 'right',
        paddingRight: 10,
        fontWeight: 500,
        fontSize: 12,
        color: call.status === 'running' ? '#F59E0B' :
               call.status === 'error' || call.status === 'timeout' ? '#EF4444' : '#555',
      }}>
        {formatDuration(elapsed)}
      </div>
      <div style={{ textAlign: 'right', fontSize: 11, color: '#CCC' }}>
        {call.model.replace('deepseek-', 'ds-').replace(':thinking', '')}
      </div>
    </div>
  );
}

export default function LLMMonitor({ taskId, taskStatus }: { taskId: string; taskStatus?: string }) {
  const [data, setData] = useState<LLMLogsResponse | null>(null);
  const [now, setNow] = useState(() => Date.now() / 1000);
  const listRef = useRef<HTMLDivElement>(null);
  const prevCountRef = useRef(0);

  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;

    const poll = async () => {
      if (cancelled) return;
      try {
        const result = await getTaskLLMLogs(taskId);
        if (!cancelled) setData(result);
      } catch { /* ignore */ }
      if (!cancelled) {
        const interval = taskStatus === 'processing' ? 2000 : 8000;
        timer = setTimeout(poll, interval);
      }
    };
    poll();

    return () => { cancelled = true; clearTimeout(timer); };
  }, [taskId, taskStatus]);

  useEffect(() => {
    if (taskStatus !== 'processing') return;
    const t = setInterval(() => setNow(Date.now() / 1000), 1000);
    return () => clearInterval(t);
  }, [taskStatus]);

  useEffect(() => {
    if (data && data.calls.length > prevCountRef.current && listRef.current) {
      listRef.current.scrollTop = listRef.current.scrollHeight;
    }
    if (data) prevCountRef.current = data.calls.length;
  }, [data?.calls.length]);

  if (!data || data.calls.length === 0) {
    return (
      <div style={{ padding: '16px 0', fontSize: 13, color: 'var(--text-muted, #999)', textAlign: 'center' }}>
        暂无模型调用记录
      </div>
    );
  }

  const { summary, calls } = data;

  return (
    <div style={{ marginTop: 12 }}>
      <style>{`
        @keyframes llm-pulse {
          0%, 100% { opacity: 1; }
          50% { opacity: 0.3; }
        }
      `}</style>

      <div style={{
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        padding: '8px 0',
        borderBottom: '1px solid var(--border, #E8E8E4)',
        marginBottom: 4,
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 14, fontSize: 12 }}>
          <span style={{ fontWeight: 600, letterSpacing: '0.05em', textTransform: 'uppercase', color: 'var(--text-main)' }}>
            LLM Monitor
          </span>
          <span style={{ color: '#888' }}>
            {summary.completed}/{summary.total_calls} 完成
          </span>
          {summary.running > 0 && (
            <span style={{ color: '#F59E0B', display: 'flex', alignItems: 'center', gap: 4 }}>
              <StatusDot status="running" />{summary.running} 运行中
            </span>
          )}
          {summary.failed > 0 && (
            <span style={{ color: '#EF4444' }}>{summary.failed} 失败</span>
          )}
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 16, fontSize: 12, color: '#888' }}>
          <span title="累计耗时">{formatDuration(summary.total_duration_s)}</span>
          <span title="总 tokens">{formatTokens(summary.total_tokens)} tok</span>
          {summary.avg_speed > 0 && <span title="平均速度">{summary.avg_speed} t/s</span>}
        </div>
      </div>

      <div style={{
        display: 'grid',
        gridTemplateColumns: '22px 1fr 80px 80px 60px 58px',
        padding: '4px 0',
        fontSize: 11,
        color: '#AAA',
        textTransform: 'uppercase',
        letterSpacing: '0.06em',
        borderBottom: '1px solid var(--border, #E8E8E4)',
      }}>
        <span />
        <span>调用</span>
        <span style={{ textAlign: 'right', paddingRight: 10 }}>Tokens</span>
        <span style={{ textAlign: 'right', paddingRight: 10 }}>速度</span>
        <span style={{ textAlign: 'right', paddingRight: 10 }}>耗时</span>
        <span style={{ textAlign: 'right' }}>模型</span>
      </div>

      <div
        ref={listRef}
        style={{
          maxHeight: 320,
          overflowY: 'auto',
          paddingBottom: 4,
        }}
      >
        {calls.map((call, i) => (
          <CallRow key={`${call.call_id}-${i}`} call={call} now={now} />
        ))}
      </div>

      <div style={{
        display: 'flex',
        justifyContent: 'space-between',
        alignItems: 'center',
        padding: '8px 0 4px',
        borderTop: '1px solid var(--border, #E8E8E4)',
        fontSize: 12,
        color: '#999',
      }}>
        <div style={{ display: 'flex', gap: 16 }}>
          <span>输入 {formatTokens(summary.total_prompt_tokens)}</span>
          <span>输出 {formatTokens(summary.total_completion_tokens)}</span>
        </div>
        <span>
          {taskStatus === 'processing' ? '实时更新中…' : '已完成'}
        </span>
      </div>
    </div>
  );
}
