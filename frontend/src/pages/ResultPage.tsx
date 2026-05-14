import { useEffect, useState, useCallback, useRef } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { Button, message, Result } from 'antd';
import { DownloadOutlined, EditOutlined, HomeOutlined, LoadingOutlined, ReadOutlined } from '@ant-design/icons';
import { getTaskResult, downloadTaskFile, rebuildPdf, type TaskResult } from '../services/api';
import FlipBookReader, { type FlipBookReaderHandle } from '../components/FlipBookReader';
import EditPanel from '../components/EditPanel';
import { useIsMobile } from '../hooks/useIsMobile';

export default function ResultPage() {
  const { taskId } = useParams<{ taskId: string }>();
  const navigate = useNavigate();
  const isMobile = useIsMobile();
  const [result, setResult] = useState<TaskResult | null>(null);
  const [loading, setLoading] = useState(true);
  const [isEditing, setIsEditing] = useState(false);
  const [pdfUrl, setPdfUrl] = useState<string | null>(null);
  const [pdfMissing, setPdfMissing] = useState(false);
  const flipBookReaderRef = useRef<FlipBookReaderHandle>(null);

  useEffect(() => {
    if (!taskId) return;
    getTaskResult(taskId)
      .then((data) => {
        setResult(data);
        setPdfUrl(data.pdf_url);
        setPdfMissing(Boolean(data.pdf_missing));
      })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [taskId]);

  useEffect(() => {
    if (isEditing) document.body.classList.add('editing-mode');
    else document.body.classList.remove('editing-mode');
    return () => document.body.classList.remove('editing-mode');
  }, [isEditing]);

  const handleDownloadPdf = useCallback(async () => {
    if (!taskId || !result) return;
    try {
      await downloadTaskFile(taskId, 'pdf', `${result.title}.pdf`);
    } catch (err: any) {
      message.error(err.response?.data?.detail || '下载失败');
    }
  }, [taskId, result]);

  const handleDownloadEpub = useCallback(async () => {
    if (!taskId || !result) return;
    try {
      await downloadTaskFile(taskId, 'epub', `${result.title}.epub`);
    } catch (err: any) {
      message.error(err.response?.data?.detail || '下载失败');
    }
  }, [taskId, result]);

  const handleRebuildPdf = useCallback(async () => {
    if (!taskId) return;
    const hide = message.loading('正在重新加载 PDF，请稍候...', 0);
    try {
      await rebuildPdf(taskId);
      const deadline = Date.now() + 10 * 60 * 1000;
      while (Date.now() < deadline) {
        await new Promise((resolve) => setTimeout(resolve, 3000));
        try {
          const data = await getTaskResult(taskId);
          if (data.status === 'completed' && !data.pdf_missing && data.pdf_url) {
            setResult(data);
            setPdfMissing(false);
            setPdfUrl(data.pdf_url + (data.pdf_url.includes('?') ? '&' : '?') + `t=${Date.now()}`);
            hide();
            message.success('PDF 已重新加载');
            return;
          }
          if (data.status === 'completed' && data.error_message) {
            hide();
            message.error(data.error_message);
            return;
          }
        } catch {
          // ignore transient poll errors
        }
      }
      hide();
      message.error('重新加载超时，请稍后再试');
    } catch (err: any) {
      hide();
      message.error(err.response?.data?.detail || '重新加载失败，请稍后再试');
    }
  }, [taskId]);

  if (loading) {
    return (
      <div className="page-center">
        <div className="ep-loading reveal">
          <LoadingOutlined style={{ fontSize: 32 }} />
        </div>
      </div>
    );
  }

  if (!result) {
    return (
      <div className="page-center">
        <Result
          status="404"
          title="Not Found"
          subTitle="The book you are looking for does not exist."
          extra={<Button type="primary" onClick={() => navigate('/')}>Return Home</Button>}
        />
      </div>
    );
  }

  if (result.status !== 'completed') {
    navigate(`/processing/${taskId}`, { replace: true });
    return null;
  }

  const getDisplayTitle = () => {
    let title = result.title;
    if (result.results?.annotated?.title) title = result.results.annotated.title;
    else if (result.results?.composed?.title) title = result.results.composed.title;
    return title || '书籍';
  };

  const EDIT_SPLIT_MIN_WIDTH = 768;

  const handleEditClick = () => {
    const mode = window.innerWidth < EDIT_SPLIT_MIN_WIDTH ? 'page' : 'inline';
    if (mode === 'page') navigate(`/edit/${taskId}`);
    else setIsEditing(true);
  };

  const handleSaveComplete = (newPdfUrl: string) => {
    if (newPdfUrl) {
      setPdfUrl(newPdfUrl + `?t=${Date.now()}`);
      setIsEditing(false);
    } else {
      message.warning('排版完成但未获取到新 PDF 地址，请刷新页面查看');
      setIsEditing(false);
    }
  };

  // ── Split-view editing mode ─────────────────────────────────
  if (isEditing) {
    return (
      <div className="res-edit-layout">
        <div className="res-edit-preview">
          {pdfUrl || pdfMissing ? (
            <FlipBookReader
              key={pdfUrl || 'missing'}
              pdfUrl={pdfUrl || ''}
              pdfMissing={pdfMissing}
              onRebuild={handleRebuildPdf}
              onDownload={handleDownloadPdf}
              compact
            />
          ) : (
            <div className="res-edit-preview-empty">
              <p>PDF 尚未生成</p>
            </div>
          )}
        </div>
        <div className="res-edit-panel">
          <EditPanel
            taskId={taskId!}
            onSaveComplete={handleSaveComplete}
            onCancel={() => setIsEditing(false)}
            onPreviewRefresh={(url) => { if (url) setPdfUrl(url + `?t=${Date.now()}`); }}
          />
        </div>
      </div>
    );
  }

  // ── Normal result view ────────────────────────────────────
  const pdfView = () => {
    if (!pdfUrl && !pdfMissing) return <p className="text-muted text-center" style={{ padding: 40 }}>PDF 尚未生成</p>;

    if (isMobile && !pdfMissing) {
      return (
        <div className="res-mobile-download-card">
          <div className="res-mobile-download-icon"><DownloadOutlined /></div>
          <h3>下载 PDF 开始阅读</h3>
          <p>直接点击"下载 PDF"即可在手机浏览器或系统阅读器中翻页阅读。</p>
          <Button type="primary" icon={<DownloadOutlined />} onClick={handleDownloadPdf}>
            下载 PDF 开始阅读
          </Button>
        </div>
      );
    }

    return (
      <FlipBookReader
        ref={flipBookReaderRef}
        key={pdfUrl || 'missing'}
        pdfUrl={pdfUrl || ''}
        pdfMissing={pdfMissing}
        onRebuild={handleRebuildPdf}
        onDownload={handleDownloadPdf}
      />
    );
  };

  return (
    <div className="page-center" style={{ maxWidth: 960 }}>
      <div className="res-wrap reveal">
        <div className="res-header">
          <span className="res-label">🎉 书籍已生成完毕</span>
          <h1 className="res-title serif">《{getDisplayTitle()}》</h1>
        </div>

        <div className="res-actions reveal delay-1">
          <Button icon={<HomeOutlined />} onClick={() => navigate('/')}>首页</Button>
          <Button type="primary" icon={<EditOutlined />} onClick={handleEditClick}>
            自由编辑书籍
          </Button>
          {pdfUrl && (
            <Button icon={<DownloadOutlined />} onClick={handleDownloadPdf}>
              下载 PDF
            </Button>
          )}
          {pdfUrl && (
            <Button type="primary" icon={<ReadOutlined />} onClick={handleDownloadEpub}>
              下载 EPUB
            </Button>
          )}
        </div>

        <div className="res-viewer reveal delay-2">
          {pdfView()}
        </div>
      </div>
    </div>
  );
}
