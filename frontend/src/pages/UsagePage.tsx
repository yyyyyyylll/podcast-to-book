import { useEffect, useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { Spin, Button } from 'antd';
import { ArrowLeftOutlined, HomeOutlined } from '@ant-design/icons';
import axios from 'axios';

interface StageUsage {
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  calls: number;
  models: string[];
  cost: number;
}

interface UsageStats {
  asr: { duration_seconds: number; cost: number };
  llm: {
    stages: Record<string, StageUsage>;
    totals: { prompt_tokens: number; completion_tokens: number; total_tokens: number; calls: number; cost: number };
  };
  total_cost: number;
  cost_rates: {
    llm_input_per_m: number;
    llm_output_per_m: number;
    llm_premium_input_per_m?: number;
    llm_premium_output_per_m?: number;
    search_input_per_m: number;
    search_output_per_m: number;
    search_per_call: number;
    asr_per_hour: number;
    image_gen_per_call?: number;
    vlm_input_per_m?: number;
    vlm_output_per_m?: number;
  };
}

interface UsageData {
  id: string;
  title: string;
  status: string;
  usage_stats: UsageStats;
}

const STAGE_LABELS: Record<string, string> = {
  transcription: '语音转录',
  compose: '内容成稿',
  extraction: '金句提炼',
  annotation: '注释生成',
  editor_preface: '编者序',
  illustration: '配图生成',
  typeset: '排版出书',
};

const STAGE_ORDER = ['transcription', 'compose', 'extraction', 'annotation', 'editor_preface', 'illustration', 'typeset'];

function formatTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(2)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return String(n);
}

function formatCost(cost: number): string {
  if (cost < 0.0001) return '< ¥0.0001';
  if (cost < 0.01) return `¥${cost.toFixed(4)}`;
  return `¥${cost.toFixed(2)}`;
}

function formatDuration(seconds: number): string {
  if (!seconds) return '-';
  const min = Math.floor(seconds / 60);
  const sec = Math.round(seconds % 60);
  if (min < 1) return `${sec}秒`;
  if (min < 60) return `${min}分${sec > 0 ? sec + '秒' : ''}`;
  const hr = Math.floor(min / 60);
  return `${hr}时${min % 60}分`;
}

export default function UsagePage() {
  const { taskId } = useParams<{ taskId: string }>();
  const navigate = useNavigate();
  const [data, setData] = useState<UsageData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  useEffect(() => {
    if (!taskId) return;
    setLoading(true);
    axios.get(`/api/v1/tasks/${taskId}/usage`)
      .then(res => setData(res.data))
      .catch(() => setError('无法加载用量数据'))
      .finally(() => setLoading(false));
  }, [taskId]);

  if (loading) {
    return (
      <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', minHeight: '60vh' }}>
        <Spin size="large" />
      </div>
    );
  }

  if (error || !data?.usage_stats?.llm) {
    return (
      <div style={{ maxWidth: 800, margin: '0 auto', padding: '80px 24px', textAlign: 'center' }}>
        <div style={{ fontSize: 18, color: 'var(--text-muted)', marginBottom: 24 }}>
          {error || '该任务暂无用量统计数据'}
        </div>
        <Button onClick={() => navigate(-1)}>返回</Button>
      </div>
    );
  }

  const { asr, llm, total_cost, cost_rates } = data.usage_stats;
  const sortedStages = STAGE_ORDER.filter(s => s in llm.stages);
  const CURRENT_ASR_TIERS = [2.40, 1.80, 1.30];
  const isCurrentTier = CURRENT_ASR_TIERS.includes(cost_rates.asr_per_hour);

  const maxTokens = Math.max(...sortedStages.map(s => llm.stages[s].total_tokens), 1);

  return (
    <div style={{ maxWidth: 900, margin: '0 auto', padding: '40px 24px 80px' }}>
      {/* Header */}
      <div className="reveal" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 40 }}>
        <div>
          <div style={{ fontSize: 12, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.1em', color: 'var(--accent)', marginBottom: 8 }}>
            Usage Dashboard
          </div>
          <h1 className="serif" style={{ fontSize: 32, margin: '0 0 8px', letterSpacing: '-0.02em' }}>
            Token 计费看板
          </h1>
          <div style={{ fontSize: 15, color: 'var(--text-muted)', maxWidth: 500, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
            {data.title}
          </div>
        </div>
        <div style={{ display: 'flex', gap: 8 }}>
          <Button icon={<ArrowLeftOutlined />} onClick={() => navigate(-1)}>返回</Button>
          <Button icon={<HomeOutlined />} onClick={() => navigate('/')}>首页</Button>
        </div>
      </div>

      {/* Summary Cards */}
      <div className="reveal delay-1" style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 16, marginBottom: 40 }}>
        <SummaryCard label="总成本" value={formatCost(total_cost)} color="var(--accent)" />
        <SummaryCard label="LLM 成本" value={formatCost(llm.totals.cost)} color="var(--text-main)" />
        <SummaryCard label="ASR 成本" value={formatCost(asr.cost)} color="#1677ff" />
        <SummaryCard label="总 Token" value={formatTokens(llm.totals.total_tokens)} color="#52c41a" />
      </div>

      {/* ASR Section */}
      <div className="reveal delay-2" style={{ marginBottom: 40 }}>
        <SectionTitle>ASR 语音识别</SectionTitle>
        <div style={{ background: 'var(--bg-surface)', padding: '24px 28px' }}>
          <div style={{ display: 'flex', gap: 48, flexWrap: 'wrap', marginBottom: 24 }}>
            <StatItem label="音频时长" value={formatDuration(asr.duration_seconds)} />
            <StatItem label="计费时长" value={`${Math.ceil(asr.duration_seconds / 60)} 分钟`} />
            <StatItem label={isCurrentTier ? '适用费率' : '费率（历史）'} value={`¥${cost_rates.asr_per_hour}/小时`} />
            <StatItem label="ASR 费用" value={formatCost(asr.cost)} highlight />
          </div>
          {/* Tiered pricing reference */}
          <div style={{ borderTop: '1px solid var(--border)', paddingTop: 16 }}>
            <div style={{ fontSize: 12, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.08em', color: 'var(--text-muted)', marginBottom: 10 }}>
              阶梯计费规则（月用量）
            </div>
            <table style={{ fontSize: 13, borderCollapse: 'collapse', width: '100%', maxWidth: 420 }}>
              <thead>
                <tr style={{ borderBottom: '1px solid var(--border)' }}>
                  <th style={{ padding: '6px 12px 6px 0', fontWeight: 600, textAlign: 'left', color: 'var(--text-muted)' }}>月用量梯度</th>
                  <th style={{ padding: '6px 0', fontWeight: 600, textAlign: 'left', color: 'var(--text-muted)' }}>单价（元/小时）</th>
                </tr>
              </thead>
              <tbody>
                {[
                  { tier: '0 ~ 12 万小时', price: '2.40', active: cost_rates.asr_per_hour === 2.40 },
                  { tier: '12 万 ~ 30 万小时', price: '1.80', active: cost_rates.asr_per_hour === 1.80 },
                  { tier: '30 万小时以上', price: '1.30', active: cost_rates.asr_per_hour === 1.30 },
                ].map(({ tier, price, active }) => (
                  <tr key={tier} style={{
                    borderBottom: '1px solid var(--border)',
                    background: active ? 'rgba(22, 119, 255, 0.06)' : 'transparent',
                  }}>
                    <td style={{ padding: '8px 12px 8px 0', color: active ? 'var(--text-main)' : 'var(--text-muted)' }}>
                      {active && <span style={{ display: 'inline-block', width: 6, height: 6, borderRadius: '50%', background: '#1677ff', marginRight: 8, verticalAlign: 'middle' }} />}
                      {tier}
                    </td>
                    <td style={{ padding: '8px 0', fontFamily: 'monospace', fontWeight: active ? 700 : 400, color: active ? '#1677ff' : 'var(--text-muted)' }}>
                      {price}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </div>

      {/* LLM Breakdown */}
      <div className="reveal delay-3" style={{ marginBottom: 40 }}>
        <SectionTitle>LLM 各阶段用量</SectionTitle>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 14 }}>
          <thead>
            <tr style={{ borderBottom: '2px solid var(--text-main)', textAlign: 'left' }}>
              <th style={thStyle}>阶段</th>
              <th style={{ ...thStyle, width: 100 }}>调用次数</th>
              <th style={{ ...thStyle, width: 110 }}>输入 Token</th>
              <th style={{ ...thStyle, width: 110 }}>输出 Token</th>
              <th style={{ ...thStyle, width: 110 }}>合计 Token</th>
              <th style={{ ...thStyle, width: 200 }}>占比</th>
              <th style={{ ...thStyle, width: 90, textAlign: 'right' }}>费用</th>
            </tr>
          </thead>
          <tbody>
            {sortedStages.map(stageName => {
              const s = llm.stages[stageName];
              const pct = llm.totals.total_tokens > 0 ? (s.total_tokens / llm.totals.total_tokens * 100) : 0;
              return (
                <tr key={stageName} style={{ borderBottom: '1px solid var(--border)' }}>
                  <td style={tdStyle}>
                    <span style={{ fontWeight: 500 }}>{STAGE_LABELS[stageName] || stageName}</span>
                    <div style={{ fontSize: 12, color: 'var(--text-muted)', marginTop: 2 }}>
                      {s.models.join(', ')}
                    </div>
                  </td>
                  <td style={{ ...tdStyle, fontFamily: 'monospace' }}>{s.calls}</td>
                  <td style={{ ...tdStyle, fontFamily: 'monospace' }}>{formatTokens(s.prompt_tokens)}</td>
                  <td style={{ ...tdStyle, fontFamily: 'monospace' }}>{formatTokens(s.completion_tokens)}</td>
                  <td style={{ ...tdStyle, fontFamily: 'monospace', fontWeight: 600 }}>{formatTokens(s.total_tokens)}</td>
                  <td style={tdStyle}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                      <div style={{ flex: 1, height: 6, background: 'var(--bg)', borderRadius: 0, overflow: 'hidden' }}>
                        <div style={{
                          width: `${(s.total_tokens / maxTokens) * 100}%`,
                          height: '100%',
                          background: 'var(--text-main)',
                          transition: 'width 0.6s var(--ease)',
                        }} />
                      </div>
                      <span style={{ fontSize: 12, color: 'var(--text-muted)', width: 36, textAlign: 'right' }}>
                        {pct.toFixed(0)}%
                      </span>
                    </div>
                  </td>
                  <td style={{ ...tdStyle, fontFamily: 'monospace', textAlign: 'right', fontWeight: 500 }}>
                    {formatCost(s.cost)}
                  </td>
                </tr>
              );
            })}
            {/* Total Row */}
            <tr style={{ borderTop: '2px solid var(--text-main)' }}>
              <td style={{ ...tdStyle, fontWeight: 600 }}>合计</td>
              <td style={{ ...tdStyle, fontFamily: 'monospace', fontWeight: 600 }}>{llm.totals.calls}</td>
              <td style={{ ...tdStyle, fontFamily: 'monospace', fontWeight: 600 }}>{formatTokens(llm.totals.prompt_tokens)}</td>
              <td style={{ ...tdStyle, fontFamily: 'monospace', fontWeight: 600 }}>{formatTokens(llm.totals.completion_tokens)}</td>
              <td style={{ ...tdStyle, fontFamily: 'monospace', fontWeight: 600 }}>{formatTokens(llm.totals.total_tokens)}</td>
              <td style={tdStyle} />
              <td style={{ ...tdStyle, fontFamily: 'monospace', textAlign: 'right', fontWeight: 700, color: 'var(--accent)' }}>
                {formatCost(llm.totals.cost)}
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      {/* Cost Rates Footer */}
      <div style={{ fontSize: 13, color: 'var(--text-muted)', lineHeight: 2, borderTop: '1px solid var(--border)', paddingTop: 16 }}>
        <div><span style={{ fontWeight: 600 }}>gemini-3-flash-preview：</span>输入 ¥{cost_rates.llm_input_per_m}/M tokens · 输出 ¥{cost_rates.llm_output_per_m}/M tokens</div>
        <div><span style={{ fontWeight: 600 }}>gpt-5.2：</span>输入 ¥{cost_rates.llm_premium_input_per_m ?? cost_rates.search_input_per_m}/M tokens · 输出 ¥{cost_rates.llm_premium_output_per_m ?? cost_rates.search_output_per_m}/M tokens</div>
        <div><span style={{ fontWeight: 600 }}>doubao-seedream-5.0-lite：</span>¥{cost_rates.image_gen_per_call ?? 0.22}/张</div>
        <div><span style={{ fontWeight: 600 }}>qwen-vl-max：</span>输入 ¥{cost_rates.vlm_input_per_m ?? 3.0}/M tokens · 输出 ¥{cost_rates.vlm_output_per_m ?? 9.0}/M tokens</div>
        <div><span style={{ fontWeight: 600 }}>ASR：</span>阶梯计费 ¥2.40 / ¥1.80 / ¥1.30 每小时</div>
      </div>
    </div>
  );
}

/* ===== Sub-components ===== */

const thStyle: React.CSSProperties = { padding: '12px 8px', fontWeight: 600 };
const tdStyle: React.CSSProperties = { padding: '14px 8px' };

function SummaryCard({ label, value, color }: { label: string; value: string; color: string }) {
  return (
    <div style={{ background: 'var(--bg-surface)', padding: '20px 24px', borderBottom: `2px solid ${color}` }}>
      <div style={{ fontSize: 13, color: 'var(--text-muted)', marginBottom: 4 }}>{label}</div>
      <div style={{ fontSize: 28, fontWeight: 600, color, fontFamily: 'monospace', letterSpacing: '-0.02em' }}>{value}</div>
    </div>
  );
}

function SectionTitle({ children }: { children: React.ReactNode }) {
  return (
    <div style={{
      fontSize: 14, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.08em',
      color: 'var(--text-muted)', borderBottom: '1px solid var(--border)', paddingBottom: 8, marginBottom: 16,
    }}>
      {children}
    </div>
  );
}

function StatItem({ label, value, highlight }: { label: string; value: string; highlight?: boolean }) {
  return (
    <div>
      <div style={{ fontSize: 13, color: 'var(--text-muted)', marginBottom: 4 }}>{label}</div>
      <div style={{ fontSize: 18, fontWeight: highlight ? 700 : 500, color: highlight ? 'var(--accent)' : 'var(--text-main)', fontFamily: 'monospace' }}>
        {value}
      </div>
    </div>
  );
}
