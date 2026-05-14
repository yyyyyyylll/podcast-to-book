import { useEffect, useState, useRef } from 'react';
import { useParams, useNavigate, useSearchParams } from 'react-router-dom';
import { Button, Result, Progress, Input, message } from 'antd';
import { useTaskStore } from '../stores/taskStore';
import { submitSpeakers } from '../services/api';
import { analytics } from '../services/analytics';

const PREVIEW_TASK = {
  id: 'preview',
  title: 'MiniMax 创始人闫俊杰×罗永浩！大山并非无法翻越',
  status: 'processing' as const,
  progress: 70,
  stages: { transcription: 'completed', compose: 'completed', enrich: 'processing', typeset: 'pending' } as Record<string, string>,
};

const PREVIEW_SPEAKER_TASK = {
  id: 'preview-speaker',
  title: '关于人工智能与学术研究的跨学科对话',
  status: 'needs_speaker_input' as const,
  progress: 12,
  stages: { transcription: 'needs_input' } as Record<string, string>,
  speaker_input: {
    sample_segments: [
      { index: 0, speaker: '说话人1', text: '今天我们来聊一聊人工智能在学术研究中的应用，尤其是最近大语言模型在自然科学领域取得的一些突破性进展。' },
      { index: 1, speaker: '说话人2', text: '对，这个话题确实很值得探讨。我觉得最让我印象深刻的是 AlphaFold 在蛋白质结构预测上的成功，它几乎改变了整个结构生物学的研究范式。' },
      { index: 2, speaker: '说话人1', text: '没错，而且不只是生物学。在材料科学、药物发现这些领域，AI 也开始扮演越来越重要的角色。你在自己的研究中有用到类似的工具吗？' },
      { index: 3, speaker: '说话人2', text: '有的。我们组目前在用一些基于 Transformer 架构的模型来做分子性质预测。传统的方法需要大量的计算资源来做密度泛函理论计算，但现在用 AI 可以大幅加速这个过程。' },
      { index: 4, speaker: '说话人1', text: '那在准确性方面呢？我听说有些 AI 预测的结果还是会有比较大的误差。' },
      { index: 5, speaker: '说话人2', text: '这确实是一个挑战。对于一些简单的体系，准确性已经非常接近实验值了。但对于复杂的多组分体系，还需要更多的训练数据和更好的模型架构。' },
      { index: 6, speaker: '说话人1', text: '说到训练数据，你觉得目前公开的科学数据集够用吗？' },
      { index: 7, speaker: '说话人2', text: '远远不够。这其实是整个领域面临的一个核心瓶颈。高质量的标注数据非常稀缺，而且不同实验室之间的数据标准也不统一。' },
    ],
    speaker_count: 2,
  },
};

function friendlyError(raw: string | null | undefined): string {
  if (!raw) return '处理过程中遇到问题，请稍后重试';
  if (raw.includes('超时')) return '处理时间过长，已自动停止，请重新提交试试';
  if (raw.includes('服务重启')) return '服务进行了升级维护，请重新提交';
  if (raw.includes('LLM') || raw.includes('模型')) return 'AI 模型服务暂时不可用，请稍后重试';
  if (raw.includes('ASR') || raw.includes('转写')) return '音频转写服务异常，请稍后重试';
  if (raw.includes('缺少') || raw.includes('无法')) return '内容处理异常，请重新提交';
  return '处理过程中遇到问题，请稍后重试';
}

const STAGES = [
  { key: 'transcription', title: 'Transcribing', desc: '正在进行音频转写与语义分段' },
  { key: 'compose', title: 'Composing', desc: '正在重组内容为书籍章节' },
  { key: 'enrich', title: 'Enriching', desc: '正在提取金句、生成编者序与配图' },
  { key: 'typeset', title: 'Typesetting', desc: '正在排版生成 PDF' },
];

const SPEAKER_COLORS = ['#5B6770', '#A0785A', '#6B7F5E', '#7B6B8A', '#8A6B6B'];

function SpeakerInputPanel({
  taskId,
  title,
  speakerInput,
  onResume,
}: {
  taskId: string;
  title: string;
  speakerInput: { sample_segments: Array<{ index: number; speaker: string; text: string }>; speaker_count: number };
  onResume: () => void;
}) {
  const speakerLabels = Array.from(new Set(speakerInput.sample_segments.map(s => s.speaker))).sort();
  const colorMap: Record<string, string> = {};
  speakerLabels.forEach((l, i) => { colorMap[l] = SPEAKER_COLORS[i % SPEAKER_COLORS.length]; });

  const [names, setNames] = useState<Record<string, string>>(() => {
    const init: Record<string, string> = {};
    speakerLabels.forEach(l => { init[l] = ''; });
    return init;
  });
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    analytics.track('speaker_input_shown', {
      task_id: taskId,
      speaker_count: speakerInput.speaker_count,
      attempt_id: analytics.getAttemptIdForTask(taskId) || undefined,
    });
  }, [taskId]);

  const handleSubmit = async () => {
    const filled = Object.entries(names).filter(([, v]) => v.trim());
    if (filled.length === 0) {
      message.warning('请至少填写一个说话人的名字');
      return;
    }

    setSubmitting(true);
    try {
      const hostName = filled[0]?.[1]?.trim() || '';
      const guestNames = filled.slice(1).map(([, v]) => v.trim()).filter(Boolean).join(',');
      await submitSpeakers(taskId, hostName, guestNames);
      analytics.track('speaker_input_submitted', {
        task_id: taskId,
        speaker_count: filled.length,
        attempt_id: analytics.getAttemptIdForTask(taskId) || undefined,
      });
      message.success('已提交，任务将继续处理');
      onResume();
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '提交失败，请重试');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="speaker-wrap">
      <div className="reveal" style={{ maxWidth: 960, width: '100%', margin: '0 auto' }}>

        <div style={{ textAlign: 'center', marginBottom: 28 }}>
          <div className="process-label" style={{ letterSpacing: '0.15em', fontSize: 18, marginBottom: 8, color: 'var(--text-muted)' }}>需要您的帮助</div>
          <h1 className="serif" style={{ fontSize: 32, margin: 0, fontWeight: 400 }}>《{title}》</h1>
        </div>

        <div className="speaker-panel">

          <div className="speaker-panel-samples">
            <div style={{ fontSize: 16, color: 'var(--text-muted)', marginBottom: 10, lineHeight: 1.6 }}>
              检测到 <strong style={{ color: 'var(--text-main)' }}>{speakerInput.speaker_count}</strong> 位说话人，请根据对话片段识别身份：
            </div>
            <div className="speaker-panel-segments">
              {speakerInput.sample_segments.map((seg, i) => (
                <div key={i} style={{ marginBottom: 6, display: 'flex', gap: 8, alignItems: 'baseline' }}>
                  <span style={{
                    flexShrink: 0,
                    display: 'inline-block',
                    padding: '0 8px',
                    borderRadius: 4,
                    fontSize: 11,
                    fontWeight: 600,
                    lineHeight: '20px',
                    color: '#fff',
                    background: colorMap[seg.speaker],
                  }}>{seg.speaker}</span>
                  <span style={{ color: 'var(--text-main)' }}>{seg.text}</span>
                </div>
              ))}
            </div>
          </div>

          <div className="speaker-panel-form">
            <div style={{ fontSize: 14, fontWeight: 500, color: 'var(--text-main)', marginBottom: 20 }}>
              填写说话人姓名
            </div>

            <div style={{ display: 'flex', flexDirection: 'column', gap: 14, marginBottom: 24 }}>
              {speakerLabels.map((label) => (
                <div key={label}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
                    <span style={{
                      display: 'inline-block',
                      width: 10,
                      height: 10,
                      borderRadius: '50%',
                      background: colorMap[label],
                      flexShrink: 0,
                    }} />
                    <span style={{ fontSize: 13, color: 'var(--text-muted)' }}>{label}</span>
                  </div>
                  <Input
                    placeholder="名称"
                    value={names[label] || ''}
                    onChange={e => setNames(prev => ({ ...prev, [label]: e.target.value }))}
                    size="large"
                    style={{ borderRadius: 8 }}
                  />
                </div>
              ))}
            </div>

            <Button
              type="primary"
              size="large"
              block
              loading={submitting}
              onClick={handleSubmit}
              style={{ height: 44, borderRadius: 8, fontWeight: 500 }}
            >
              提交并继续生成
            </Button>
          </div>
        </div>
      </div>
    </div>
  );
}

export default function ProcessingPage() {
  const { taskId } = useParams<{ taskId: string }>();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const previewParam = searchParams.get('preview');
  const isPreview = previewParam !== null;
  const { currentTask: realTask, startPolling, stopPolling } = useTaskStore();
  const currentTask = isPreview
    ? (previewParam === 'speaker' ? PREVIEW_SPEAKER_TASK : PREVIEW_TASK)
    : realTask;
  const [dots, setDots] = useState('');
  const viewedRef = useRef(false);

  const processingStartRef = useRef(Date.now());

  useEffect(() => {
    if (taskId && !isPreview) startPolling(taskId);
    processingStartRef.current = Date.now();
    return () => stopPolling();
  }, [taskId, isPreview]);

  useEffect(() => {
    if (!currentTask || isPreview) return;
    if (currentTask.status === 'completed' && taskId) {
      analytics.track('task_processing_completed', {
        task_id: taskId,
        wait_duration_ms: Date.now() - processingStartRef.current,
      });
      navigate(`/result/${taskId}`, { replace: true });
    }
  }, [currentTask?.status, taskId, navigate]);

  useEffect(() => {
    const interval = setInterval(() => {
      setDots(prev => prev.length >= 3 ? '' : prev + '.');
    }, 500);
    return () => clearInterval(interval);
  }, []);

  useEffect(() => {
    if (!viewedRef.current && currentTask && taskId && !isPreview && currentTask.status === 'processing') {
      viewedRef.current = true;
      analytics.track('task_processing_viewed', {
        task_id: taskId,
        status: currentTask.status,
        current_stage: Object.entries(currentTask.stages || {}).find(([, v]) => v === 'processing')?.[0],
      });
    }
  }, [currentTask, taskId, isPreview]);

  if (!currentTask) {
    return (
      <div className="page-center" style={{ minHeight: 'calc(100vh - 56px)', display: 'flex', flexDirection: 'column', alignItems: 'center', paddingTop: '12vh' }}>
        <div className="ep-loading reveal" style={{ textAlign: 'center' }}>
          <div className="ep-loader-icon" style={{ fontSize: 40, color: 'var(--text-main)' }}>❋</div>
          <div className="process-label" style={{ marginTop: 24, letterSpacing: '0.2em' }}>Fetching Task</div>
        </div>
      </div>
    );
  }

  if (currentTask.status === 'needs_speaker_input' && currentTask.speaker_input && taskId) {
    return (
      <SpeakerInputPanel
        taskId={taskId}
        title={currentTask.title}
        speakerInput={currentTask.speaker_input}
        onResume={() => startPolling(taskId)}
      />
    );
  }

  if (currentTask.status === 'failed') {
    return (
      <div className="page-center" style={{ minHeight: 'calc(100vh - 56px)', display: 'flex', flexDirection: 'column', alignItems: 'center', paddingTop: '12vh' }}>
        <Result
          status="error"
          title="处理失败"
          subTitle={friendlyError(currentTask.error_message)}
          extra={<Button type="primary" size="large" onClick={() => navigate('/')}>返回首页</Button>}
        />
      </div>
    );
  }

  const stages = currentTask.stages || {};
  const activeIndex = STAGES.findIndex(s => stages[s.key] === 'processing');
  const active = activeIndex >= 0 ? STAGES[activeIndex] : null;
  const progressPercent = currentTask.progress || 0;

  return (
    <div className="page-center processing-progress-center">

      <div className="process-header reveal" style={{ marginBottom: 48 }}>
        <div className="ep-loader-icon" style={{ fontSize: 32, color: 'var(--accent)', marginBottom: 12, lineHeight: 1 }}>❋</div>
        <div className="process-label" style={{ letterSpacing: '0.2em', fontSize: '15px', marginBottom: 12 }}>Working on it</div>
        <h1 className="process-title serif" style={{ marginBottom: 0 }}>《{currentTask.title}》</h1>
      </div>

      <div className="reveal delay-2" style={{ maxWidth: 480, margin: '0 auto', width: '100%', textAlign: 'center' }}>

        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', marginBottom: 16 }}>
          <span className="serif" style={{ fontSize: 104, lineHeight: 1, fontWeight: 400, color: 'var(--text-main)', letterSpacing: '-0.02em' }}>
            {progressPercent}
          </span>
          <span className="serif" style={{ fontSize: 36, color: 'var(--text-muted)', marginLeft: 4 }}>%</span>
        </div>

        <Progress
          percent={progressPercent}
          showInfo={false}
          strokeColor="var(--text-main)"
          trailColor="var(--border)"
          strokeWidth={12}
          className="ep-progress-animated"
          style={{ marginBottom: 24 }}
        />

        <div style={{ minHeight: 64, position: 'relative' }}>
          {active ? (
            <div className="fade-in-up" key={active.key} style={{ position: 'absolute', width: '100%', left: 0 }}>
              <div className="serif" style={{ fontSize: 28, marginBottom: 6, color: 'var(--text-main)' }}>
                {active.title}{dots}
              </div>
              <div style={{ fontSize: 15, color: 'var(--text-muted)' }}>
                {active.desc}
              </div>
            </div>
          ) : (
            <div className="serif" style={{ fontSize: 28, color: 'var(--text-muted)', position: 'absolute', width: '100%', left: 0 }}>
              Preparing{dots}
            </div>
          )}
        </div>

        <div style={{ fontSize: 13, color: '#A3A3A3', lineHeight: 1.8, marginTop: 28, paddingTop: 28, borderTop: '1px solid var(--border)' }}>
          <div>根据播客时长，生成书籍通常需要 15～40 分钟，请您耐心等待</div>
          <div style={{ marginTop: 4 }}>
            您可以关闭此页面，制作会在后台继续进行<br />
            再次访问时，点击右上角头像 →「查看订单」即可找到
          </div>
        </div>
      </div>

    </div>
  );
}
