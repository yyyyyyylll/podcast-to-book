import React, { useState, useCallback } from 'react';
import { Modal, Input, Button, Radio, Upload, message, Space } from 'antd';
import { PlusOutlined } from '@ant-design/icons';
import type { UploadFile } from 'antd';
import { submitFeedback } from '../services/api';
import { analytics } from '../services/analytics';
import { useAuthStore } from '../stores/authStore';

const { TextArea } = Input;

const CATEGORIES = [
  { value: 'issue', label: '问题反馈' },
  { value: 'suggestion', label: '功能建议' },
  { value: 'other', label: '其他' },
];

interface FeedbackModalProps {
  open: boolean;
  onClose: () => void;
  taskId?: string;
}

const FeedbackModal: React.FC<FeedbackModalProps> = ({ open, onClose, taskId }) => {
  const { user, openLogin } = useAuthStore();
  const [category, setCategory] = useState('issue');
  const [content, setContent] = useState('');
  const [fileList, setFileList] = useState<UploadFile[]>([]);
  const [submitting, setSubmitting] = useState(false);

  const reset = useCallback(() => {
    setCategory('issue');
    setContent('');
    setFileList([]);
  }, []);

  const handleClose = useCallback(() => {
    onClose();
    setTimeout(reset, 300);
  }, [onClose, reset]);

  const handleSubmit = useCallback(async () => {
    if (!user) {
      openLogin();
      return;
    }
    if (!content.trim()) {
      message.warning('请输入反馈内容');
      return;
    }

    setSubmitting(true);
    try {
      const formData = new FormData();
      formData.append('category', category);
      formData.append('content', content.trim());
      if (taskId) formData.append('task_id', taskId);
      fileList.forEach((file) => {
        if (file.originFileObj) {
          formData.append('images', file.originFileObj);
        }
      });

      const res = await submitFeedback(formData);
      analytics.track('feedback_submitted', { category, has_text: !!content.trim() });
      message.success('您的声音，是我们变得更好的动力！我们将在 2 小时内给予您反馈。');
      handleClose();
    } catch (err: any) {
      message.error(err.response?.data?.detail || '提交失败，请稍后重试');
    } finally {
      setSubmitting(false);
    }
  }, [user, openLogin, content, category, taskId, fileList, handleClose]);

  const contentLen = content.length;
  const maxLen = 2000;

  return (
    <Modal
      open={open}
      onCancel={handleClose}
      footer={null}
      centered
      width={480}
      destroyOnClose
    >
      <div style={{ padding: '16px 0 8px' }}>
        <h2 style={{ marginBottom: 8, textAlign: 'center', fontWeight: 600 }}>
          意见反馈
        </h2>
        <p style={{ textAlign: 'center', color: '#999', fontSize: 14, marginBottom: 24 }}>
          您的声音，是我们变得更好的动力！我们将在2小时内给予您反馈。
        </p>
        <Space direction="vertical" size="middle" style={{ width: '100%' }}>
          <div>
            <div style={{ marginBottom: 8, fontWeight: 500, fontSize: 14 }}>反馈类型</div>
            <Radio.Group
              value={category}
              onChange={(e) => setCategory(e.target.value)}
              optionType="button"
              buttonStyle="solid"
              size="middle"
              style={{ display: 'flex', width: '100%' }}
            >
              {CATEGORIES.map((c) => (
                <Radio.Button key={c.value} value={c.value} style={{ flex: 1, textAlign: 'center' }}>{c.label}</Radio.Button>
              ))}
            </Radio.Group>
          </div>

          <div>
            <div style={{ marginBottom: 8, fontWeight: 500, fontSize: 14 }}>反馈内容</div>
            <div style={{ position: 'relative' }}>
              <TextArea
                value={content}
                onChange={(e) => {
                  if (e.target.value.length <= maxLen) setContent(e.target.value);
                }}
                placeholder="请详细描述您遇到的问题或建议..."
                rows={5}
                style={{ resize: 'none' }}
              />
              <div style={{
                position: 'absolute',
                bottom: 8,
                right: 12,
                fontSize: 12,
                color: contentLen >= maxLen ? 'var(--accent, #ff4d4f)' : '#ccc',
              }}>
                {contentLen}/{maxLen}
              </div>
            </div>
          </div>

          <div>
            <div style={{ marginBottom: 8, fontWeight: 500, fontSize: 14 }}>截图（可选，最多 6 张）</div>
            <Upload
              listType="picture-card"
              fileList={fileList}
              onChange={({ fileList: newList }) => setFileList(newList)}
              beforeUpload={() => false}
              accept="image/*"
              maxCount={6}
            >
              {fileList.length < 6 && (
                <div>
                  <PlusOutlined />
                  <div style={{ marginTop: 8, fontSize: 12 }}>上传截图</div>
                </div>
              )}
            </Upload>
          </div>

          <Button
            type="primary"
            size="large"
            block
            loading={submitting}
            onClick={handleSubmit}
            disabled={!content.trim()}
          >
            提交反馈
          </Button>
        </Space>
      </div>
    </Modal>
  );
};

export default FeedbackModal;
