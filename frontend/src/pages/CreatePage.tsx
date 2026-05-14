import { useState, useEffect, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import { Input, Button, message, Form, Image } from 'antd';
import { ZoomInOutlined } from '@ant-design/icons';
import { uploadPodcastUrl, getCoverStyles, CoverStyle } from '../services/api';

const { TextArea } = Input;

const CoverStylePicker = ({ styles, value, onChange }: { styles: CoverStyle[]; value?: string; onChange?: (v: string) => void }) => {
  const [previewOpen, setPreviewOpen] = useState(false);
  const [previewIdx, setPreviewIdx] = useState(0);
  const scrollRef = useRef<HTMLDivElement>(null);
  const selected = value || 'classic';

  const scroll = (dir: 'left' | 'right') => {
    scrollRef.current?.scrollBy({ left: dir === 'left' ? -200 : 200, behavior: 'smooth' });
  };

  return (
    <>
      <div className="cover-picker-wrapper">
        <button type="button" className="cover-picker-arrow left" onClick={() => scroll('left')} aria-label="向左滚动">‹</button>
        <div className="cover-picker-track" ref={scrollRef}>
          {styles.map((s, idx) => {
            const active = s.id === selected;
            return (
              <div
                key={s.id}
                className={`cover-pick${active ? ' active' : ''}`}
                onClick={() => onChange?.(s.id)}
              >
                <div className="cover-pick-thumb">
                  <img src={`/cover-thumbnails/${s.id}.png`} alt={s.name} draggable={false} />
                  <div
                    className="cover-pick-zoom"
                    onClick={(e) => {
                      e.stopPropagation();
                      setPreviewIdx(idx);
                      setPreviewOpen(true);
                    }}
                  >
                    <ZoomInOutlined />
                  </div>
                </div>
                <span className="cover-pick-label">{s.name}</span>
              </div>
            );
          })}
        </div>
        <button type="button" className="cover-picker-arrow right" onClick={() => scroll('right')} aria-label="向右滚动">›</button>
      </div>
      <Image.PreviewGroup
        preview={{
          visible: previewOpen,
          onVisibleChange: (v) => setPreviewOpen(v),
          current: previewIdx,
          onChange: (c) => setPreviewIdx(c),
        }}
        items={styles.map(s => `/cover-thumbnails/${s.id}.png`)}
      />
    </>
  );
};

const getCustomLen = (val: string) => {
  if (!val) return 0;
  let len = 0;
  for (let i = 0; i < val.length; i++) {
    if (/[a-zA-Z]/.test(val[i])) len += 1 / 3;
    else len += 1;
  }
  return Math.ceil(len);
};

const CustomInput = ({ maxLen, value, onChange, ...props }: any) => {
  const [focused, setFocused] = useState(false);
  const len = getCustomLen(value || '');
  const isFull = len >= maxLen;
  const handleChange = (e: any) => {
    if (getCustomLen(e.target.value) <= maxLen) onChange?.(e);
  };
  return (
    <div style={{ position: 'relative', width: '100%' }}>
      <Input
        {...props}
        value={value}
        onChange={handleChange}
        onFocus={(e) => { setFocused(true); props.onFocus?.(e); }}
        onBlur={(e) => { setFocused(false); props.onBlur?.(e); }}
        className={focused ? 'custom-input-focused' : ''}
      />
      {focused && (
        <div style={{
          position: 'absolute', bottom: 12, right: 12,
          color: isFull ? 'var(--accent)' : '#ccc',
          opacity: isFull ? 0.7 : 1,
          fontSize: 12, pointerEvents: 'none', lineHeight: 1,
        }}>
          {len}/{maxLen}
        </div>
      )}
    </div>
  );
};

const CustomTextArea = ({ maxLen, value, onChange, ...props }: any) => {
  const [focused, setFocused] = useState(false);
  const len = getCustomLen(value || '');
  const isFull = len >= maxLen;
  const handleChange = (e: any) => {
    if (getCustomLen(e.target.value) <= maxLen) onChange?.(e);
  };
  return (
    <div style={{ position: 'relative' }}>
      <TextArea
        {...props}
        value={value}
        onChange={handleChange}
        onFocus={(e) => { setFocused(true); props.onFocus?.(e); }}
        onBlur={(e) => { setFocused(false); props.onBlur?.(e); }}
        className={focused ? 'custom-textarea-focused' : ''}
      />
      {focused && (
        <div style={{
          position: 'absolute', bottom: 12, right: 12,
          color: isFull ? 'var(--accent)' : '#ccc',
          opacity: isFull ? 0.7 : 1,
          fontSize: 12, pointerEvents: 'none',
          background: 'var(--bg)', padding: '2px 4px', borderRadius: 4,
        }}>
          {len}/{maxLen}
        </div>
      )}
    </div>
  );
};

export default function CreatePage() {
  const navigate = useNavigate();
  const [loading, setLoading] = useState(false);
  const [styles, setStyles] = useState<CoverStyle[]>([]);
  const [form] = Form.useForm();

  useEffect(() => {
    getCoverStyles().then(setStyles).catch(() => {});
  }, []);

  const handleSubmit = async () => {
    const values = await form.validateFields();
    if (!values.podcast_url) {
      message.error('请输入播客链接');
      return;
    }
    setLoading(true);
    try {
      const res = await uploadPodcastUrl({
        podcast_url: values.podcast_url,
        title: values.title || '',
        host_name: values.host_name || '',
        guest_names: values.guest_names || '',
        editor_preface: values.editor_preface || '',
        cover_style: values.cover_style || 'classic',
      });
      message.success('播客解析完成，任务已创建');
      navigate(`/processing/${res.task_id}`);
    } catch (e: any) {
      message.error(e.response?.data?.detail || '解析失败，请检查链接是否正确');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="split-layout">
      <div className="split-left">
        <div className="brand-logo-row" onClick={() => navigate('/')} style={{ cursor: 'pointer' }}>
          <img src="/brand-seal.png" alt="EchoPress" className="brand-seal" />
          <div className="brand-wordmark">
            <span className="brand-wordmark-text serif">EchoPress.</span>
            <span className="brand-wordmark-line" />
          </div>
        </div>
        <div className="reveal">
          <h1 className="hero-title serif">
            Turn great conversations<br/>
            into <i>publish-ready</i> books.
          </h1>
          <p className="hero-desc">
            声波转瞬即逝，思想理应长存。<br/>
            EchoPress 将流动的音频，淬炼成极具质感的纸质典藏。<br/>
            我们精准还原字里行间的灵光，系统梳理每一次认知的升级。<br/>
            声而有形，读有所得。<br/>
            让耳边的陪伴，化作指尖的触感；让深刻的对话，从此拥有实体的生命。
          </p>
        </div>
      </div>

      <div className="split-right" style={{ padding: 0 }}>
        <div className="split-right-tabbar">
          <h2 className="form-title serif" style={{ margin: 0, lineHeight: 1 }}>Create a new book</h2>
          <div style={{ flex: 1 }} />
        </div>
        <div className="split-right-body reveal delay-1">
          <Form form={form} layout="vertical" size="large" requiredMark={false} className="home-form">
            <Form.Item
              name="podcast_url"
              label="播客链接"
              className="form-item-inline"
              rules={[{ required: true, message: '请输入播客链接！' }]}
              style={{ marginBottom: 6 }}
            >
              <Input placeholder="请粘贴小宇宙或 Apple Podcasts 单集链接" />
            </Form.Item>

            {styles.length > 0 && (
              <Form.Item
                name="cover_style"
                label="封面风格"
                initialValue="classic"
                rules={[{ required: true, message: '请选择封面风格' }]}
                style={{ marginBottom: 0 }}
              >
                <CoverStylePicker styles={styles} />
              </Form.Item>
            )}

            <div className="form-divider">选填信息</div>

            <Form.Item name="title" label="书名" className="form-item-inline">
              <CustomInput maxLen={18} placeholder="留空则AI自动生成书名" />
            </Form.Item>

            <div className="form-row-pair">
              <Form.Item name="guest_names" label="嘉宾" className="form-item-inline">
                <CustomInput maxLen={20} placeholder="多个嘉宾用顿号分隔，留空则自动识别" />
              </Form.Item>
              <Form.Item name="host_name" label="主持人" className="form-item-inline">
                <CustomInput maxLen={10} placeholder="留空则自动识别" />
              </Form.Item>
            </div>

            <Form.Item name="editor_preface" label="编者序" className="form-item-flex">
              <CustomTextArea maxLen={1500} placeholder="写在书籍开头的序言，留空则AI自动生成编者序" />
            </Form.Item>

            <Button
              type="primary"
              size="large"
              loading={loading}
              onClick={handleSubmit}
              className="generate-book-btn"
              style={{ marginTop: 14, padding: '0 40px', width: '100%' }}
            >
              {loading ? '正在解析...' : '开始生成书籍'}
            </Button>
          </Form>
        </div>
      </div>
    </div>
  );
}
