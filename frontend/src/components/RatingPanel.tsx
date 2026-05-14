import React, { useState, useEffect, useCallback, type SyntheticEvent } from 'react';
import { Rate, Input, Button, Upload, Modal, Switch, message } from 'antd';
import { PlusOutlined, EditOutlined, CheckCircleFilled, WechatOutlined } from '@ant-design/icons';
import type { UploadFile } from 'antd';
import { submitRating, getMyRating, type MyRating } from '../services/api';
import { analytics } from '../services/analytics';
import { useAuthStore } from '../stores/authStore';

const { TextArea } = Input;

const SCORE_LABELS: Record<number, string> = {
  1: '\u4e0d\u6ee1\u610f',
  2: '\u5f85\u6539\u8fdb',
  3: '\u4e00\u822c',
  4: '\u6ee1\u610f',
  5: '\u975e\u5e38\u6ee1\u610f',
};

const NPS_LABELS: Record<number, string> = {
  1: '\u4e0d\u613f\u610f',
  2: '\u4e0d\u592a\u53ef\u80fd',
  3: '\u4e0d\u786e\u5b9a',
  4: '\u53ef\u80fd\u4f1a',
  5: '\u975e\u5e38\u613f\u610f',
};

interface RatingPanelProps {
  taskId: string;
}

const RatingPanel: React.FC<RatingPanelProps> = ({ taskId }) => {
  const { user, openLogin } = useAuthStore();
  const [loading, setLoading] = useState(true);
  const [existing, setExisting] = useState<MyRating | null>(null);
  const [score, setScore] = useState(0);
  const [modalScore, setModalScore] = useState(0);
  const [npsScore, setNpsScore] = useState(0);
  const [comment, setComment] = useState('');
  const [fileList, setFileList] = useState<UploadFile[]>([]);
  const [submitting, setSubmitting] = useState(false);
  const [submitted, setSubmitted] = useState(false);
  const [modalOpen, setModalOpen] = useState(false);
  const [contactOk, setContactOk] = useState(false);
  const [qrUnavailable, setQrUnavailable] = useState(false);

  useEffect(() => {
    if (!taskId || !user) {
      setLoading(false);
      return;
    }
    getMyRating(taskId)
      .then((data) => {
        if (data) {
          setExisting(data);
          setScore(data.score);
          setComment(data.comment);
          setNpsScore(data.nps_score);
          setContactOk(data.contact_ok);
          setSubmitted(true);
        }
      })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [taskId, user]);

  const handleInlineClick = useCallback((value: number) => {
    if (!user) {
      openLogin();
      return;
    }
    setModalScore(value);
    setModalOpen(true);
  }, [user, openLogin]);

  const handleEditClick = useCallback(() => {
    setModalScore(score);
    setFileList([]);
    setModalOpen(true);
  }, [score]);

  const handleQrError = useCallback((e: SyntheticEvent<HTMLImageElement>) => {
    const img = e.currentTarget;
    if (img.getAttribute('src') !== '/community-qr.png') {
      img.src = '/community-qr.png';
      return;
    }
    setQrUnavailable(true);
  }, []);

  const handleModalClose = useCallback(() => {
    if (!submitted) {
      analytics.track('rating_dismissed', { task_id: taskId, had_score: modalScore > 0 });
      setModalScore(0);
      setNpsScore(0);
    }
    setModalOpen(false);
  }, [submitted, taskId, modalScore]);

  const handleSubmit = useCallback(async () => {
    if (!user) {
      openLogin();
      return;
    }
    if (modalScore === 0) {
      message.warning('\u8bf7\u5148\u9009\u62e9\u8bc4\u5206');
      return;
    }

    setSubmitting(true);
    try {
      const formData = new FormData();
      formData.append('task_id', taskId);
      formData.append('score', String(modalScore));
      formData.append('nps_score', String(npsScore));
      formData.append('comment', comment.trim());
      formData.append('contact_ok', String(contactOk));
      fileList.forEach((file) => {
        if (file.originFileObj) {
          formData.append('images', file.originFileObj);
        }
      });

      await submitRating(formData);
      analytics.track('rating_submitted', { task_id: taskId, score: modalScore, nps_score: npsScore });
      message.success('\u611f\u8c22\u60a8\u7684\u8bc4\u4ef7\uff01');
      setScore(modalScore);
      setSubmitted(true);
      setModalOpen(false);
      setExisting({
        id: '',
        task_id: taskId,
        score: modalScore,
        nps_score: npsScore,
        comment: comment.trim(),
        image_paths: [],
        contact_ok: contactOk,
        created_at: null,
        updated_at: null,
      });
    } catch (err: any) {
      message.error(err.response?.data?.detail || '\u63d0\u4ea4\u5931\u8d25\uff0c\u8bf7\u7a0d\u540e\u91cd\u8bd5');
    } finally {
      setSubmitting(false);
    }
  }, [user, openLogin, taskId, modalScore, npsScore, comment, contactOk, fileList]);

  if (loading) return null;

  return (
    <>
      {/* Inline trigger in the bottom bar */}
      <div className="rating-inline">
        {submitted ? (
          <div className="rating-inline-done">
            <CheckCircleFilled className="rating-inline-check" />
            <Rate disabled value={score} className="rating-inline-stars" />
            <button className="rating-inline-edit" onClick={handleEditClick}>
              <EditOutlined />
            </button>
          </div>
        ) : (
          <div className="rating-inline-prompt">
            <span className="rating-inline-hint">{'\u5bf9\u751f\u6210\u8d28\u91cf\u6ee1\u610f\u5417\uff1f\u7ed9\u4e2a\u8bc4\u5206\u5427'}</span>
            <Rate
              value={0}
              onChange={handleInlineClick}
              className="rating-inline-stars"
            />
          </div>
        )}
      </div>

      {/* Modal for detailed feedback */}
      <Modal
        open={modalOpen}
        onCancel={handleModalClose}
        footer={null}
        centered
        destroyOnClose
        width={440}
        className="rating-modal"
        title={null}
      >
        <div className="rating-modal-body">
          <div className="rating-modal-scroll">
            {/* Rating item 1: book quality */}
            <div className="rating-modal-section">
              <div className="rating-modal-section-title">
                {'\u4f60\u5bf9\u4e66\u7c4d\u751f\u6210\u6548\u679c\u6ee1\u610f\u5417\uff1f'}
              </div>
              <div className="rating-modal-stars">
                <Rate
                  value={modalScore}
                  onChange={setModalScore}
                  style={{ fontSize: 32 }}
                />
                {modalScore > 0 && (
                  <span className="rating-modal-label">{SCORE_LABELS[modalScore]}</span>
                )}
              </div>
            </div>

            {/* Rating item 2: NPS */}
            <div className="rating-modal-section">
              <div className="rating-modal-section-title">
                {'\u4f60\u613f\u610f\u5c06\u672c\u4ea7\u54c1\u63a8\u8350\u7ed9\u670b\u53cb\u5417\uff1f'}
              </div>
              <div className="rating-modal-stars">
                <Rate
                  value={npsScore}
                  onChange={setNpsScore}
                  style={{ fontSize: 32 }}
                />
                {npsScore > 0 && (
                  <span className="rating-modal-label">{NPS_LABELS[npsScore]}</span>
                )}
              </div>
            </div>

            <div className="rating-modal-comment">
              <TextArea
                value={comment}
                onChange={(e) => {
                  if (e.target.value.length <= 500) setComment(e.target.value);
                }}
                placeholder={'\u544a\u8bc9\u6211\u4eec\u60a8\u7684\u611f\u53d7...\uff08\u53ef\u9009\uff09'}
                rows={3}
                style={{ resize: 'none' }}
              />
              <div className="rating-modal-count">{comment.length}/500</div>
            </div>

            <div className="rating-modal-upload">
              <div className="rating-modal-upload-label">{'\u622a\u56fe\uff08\u53ef\u9009\uff0c\u6700\u591a 6 \u5f20\uff09'}</div>
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
                    <div style={{ marginTop: 4, fontSize: 12 }}>{'\u4e0a\u4f20'}</div>
                  </div>
                )}
              </Upload>
            </div>

            <div className="rating-modal-contact">
              <div className="rating-modal-contact-row">
                <WechatOutlined className="rating-modal-contact-icon" />
                <div className="rating-modal-contact-text">
                  <span className="rating-modal-contact-title">{'\u5141\u8bb8\u5fae\u4fe1\u8054\u7cfb'}</span>
                  <span className="rating-modal-contact-desc">
                    {'\u6211\u4eec\u53ef\u80fd\u901a\u8fc7\u60a8\u7684\u624b\u673a\u53f7\u6dfb\u52a0\u5fae\u4fe1\uff0c\u9080\u60a8\u4f53\u9a8c\u65b0\u529f\u80fd\u6216\u4e86\u89e3\u4f7f\u7528\u611f\u53d7'}
                  </span>
                </div>
                <Switch
                  checked={contactOk}
                  onChange={setContactOk}
                  size="small"
                />
              </div>
            </div>

            <div className="rating-modal-qr">
              <div className="rating-modal-qr-label">{'\u52a0\u5165\u7528\u6237\u4ea4\u6d41\u7fa4'}</div>
              <div className="rating-modal-qr-wrap">
                {qrUnavailable ? (
                  <div className="rating-modal-qr-fallback">
                    <WechatOutlined style={{ fontSize: 28, color: '#07c160' }} />
                    <span>{'\u4e8c\u7ef4\u7801\u52a0\u8f7d\u4e2d\u2026'}</span>
                  </div>
                ) : (
                  <img
                    src="/community-wechat-qr.png"
                    alt={'\u5fae\u4fe1\u7528\u6237\u4ea4\u6d41\u7fa4'}
                    className="rating-modal-qr-img"
                    onError={handleQrError}
                  />
                )}
              </div>
              <div className="rating-modal-qr-hint">{'\u53cd\u9988\u611f\u53d7\u3001\u63d0\u51fa\u5efa\u8bae\uff0c\u83b7\u53d6\u6700\u65b0\u52a8\u6001\u548c\u4e13\u5c5e\u798f\u5229\uff01'}</div>
            </div>
          </div>

          <div className="rating-modal-footer">
            <Button
              type="primary"
              size="large"
              block
              loading={submitting}
              onClick={handleSubmit}
              disabled={modalScore === 0}
            >
              {'\u63d0\u4ea4\u8bc4\u4ef7'}
            </Button>
          </div>
        </div>
      </Modal>
    </>
  );
};

export default RatingPanel;
