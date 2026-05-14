import React, { useEffect, useRef, useState, useImperativeHandle, useCallback } from 'react';
import HTMLFlipBook from 'react-pageflip';
import * as pdfjsLib from 'pdfjs-dist';
import { useIsMobile } from '../hooks/useIsMobile';
import { analytics } from '../services/analytics';

pdfjsLib.GlobalWorkerOptions.workerSrc = new URL(
  'pdfjs-dist/build/pdf.worker.min.mjs',
  import.meta.url,
).toString();

export interface FlipBookReaderHandle {
  flipToLastPage: () => void;
}

interface FlipBookReaderProps {
  pdfUrl: string;
  bottomLeft?: React.ReactNode;
  compact?: boolean;
  maxPageRatio?: number;  // 0~1, 只渲染前 N% 的页面（半文预览用）
  onPageChange?: (current: number, total: number) => void;
  stageOverlay?: React.ReactNode;
  pdfMissing?: boolean;
  onRebuild?: () => void;
  onDownload?: () => void;
}

type PdfErrorKind = 'missing' | 'network';

interface PdfError {
  kind: PdfErrorKind;
}

function classifyPdfError(err: unknown): PdfErrorKind {
  const e = err as { name?: string; status?: number; statusCode?: number; message?: string } | null;
  const status = e?.status ?? e?.statusCode;
  const name = e?.name || '';
  const msg = String(e?.message || err || '');
  if (name === 'UnexpectedResponseException' && status === 404) return 'missing';
  if (status === 404) return 'missing';
  if (/\b404\b/.test(msg) || /missing pdf/i.test(msg)) return 'missing';
  return 'network';
}

interface MobilePdfPageProps {
  pdf: any;
  pageNumber: number;
}

const DESKTOP_BOOK_HEIGHT = 660;

const PageCover = React.forwardRef<HTMLDivElement, { image: string }>(
  ({ image }, ref) => (
    <div className="fp-page fp-cover" ref={ref} data-density="hard">
      <img src={image} alt="" draggable={false} />
    </div>
  ),
);

PageCover.displayName = 'PageCover';

const Page = React.forwardRef<HTMLDivElement, { image: string; number: number }>(
  ({ image, number }, ref) => (
    <div className="fp-page" ref={ref}>
      <img src={image} alt="" draggable={false} />
    </div>
  ),
);

Page.displayName = 'Page';

function canvasToBlobUrl(canvas: HTMLCanvasElement): Promise<string> {
  return new Promise((resolve, reject) => {
    canvas.toBlob(
      (blob) => {
        if (!blob) {
          reject(new Error('toBlob failed'));
          return;
        }
        resolve(URL.createObjectURL(blob));
      },
      'image/png',
    );
  });
}

function MobilePdfPage({ pdf, pageNumber }: MobilePdfPageProps) {
  const [visible, setVisible] = useState(pageNumber <= 2);
  const [imageUrl, setImageUrl] = useState<string | null>(null);
  const [ratio, setRatio] = useState(1 / 1.414);
  const pageRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const node = pageRef.current;
    if (!node || visible) return;

    const observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((entry) => entry.isIntersecting)) {
          setVisible(true);
          observer.disconnect();
        }
      },
      { rootMargin: '240px 0px' },
    );

    observer.observe(node);
    return () => observer.disconnect();
  }, [visible]);

  useEffect(() => {
    if (!visible) return;

    let cancelled = false;
    let objectUrl: string | null = null;

    async function renderPage() {
      const page = await pdf.getPage(pageNumber);
      const baseViewport = page.getViewport({ scale: 1 });
      if (!cancelled) {
        setRatio(baseViewport.width / baseViewport.height);
      }

      const containerWidth = pageRef.current?.clientWidth || window.innerWidth - 32;
      const cssScale = Math.max(containerWidth / baseViewport.width, 1);
      const pixelScale = Math.min((window.devicePixelRatio || 1) * cssScale, 2);
      const viewport = page.getViewport({ scale: pixelScale });
      const canvas = document.createElement('canvas');
      canvas.width = viewport.width;
      canvas.height = viewport.height;

      const context = canvas.getContext('2d');
      if (!context) {
        throw new Error('Canvas context unavailable');
      }

      await page.render({ canvas, canvasContext: context, viewport }).promise;
      objectUrl = await canvasToBlobUrl(canvas);
      canvas.width = 0;
      canvas.height = 0;

      if (!cancelled) {
        setImageUrl(objectUrl);
      }
    }

    renderPage().catch((err) => {
      if (!cancelled) {
        console.error(`Mobile PDF page ${pageNumber} render failed:`, err);
      }
    });

    return () => {
      cancelled = true;
      if (objectUrl) {
        URL.revokeObjectURL(objectUrl);
      }
    };
  }, [pdf, pageNumber, visible]);

  return (
    <div
      ref={pageRef}
      className="flipbook-mobile-page"
      style={{ aspectRatio: `${ratio}` }}
    >
      {imageUrl ? (
        <img
          src={imageUrl}
          alt={`PDF 第 ${pageNumber} 页`}
          className="flipbook-mobile-page-image"
          draggable={false}
        />
      ) : (
        <div className="flipbook-mobile-page-placeholder">
          <div className="flipbook-spinner" />
          <span>第 {pageNumber} 页加载中...</span>
        </div>
      )}
      <span className="flipbook-mobile-page-num">{pageNumber}</span>
    </div>
  );
}

function FlipBookReaderInner(
  { pdfUrl, bottomLeft, compact, maxPageRatio, onPageChange, stageOverlay, pdfMissing, onRebuild, onDownload }: FlipBookReaderProps,
  ref: React.Ref<FlipBookReaderHandle>,
) {
  const isMobile = useIsMobile();
  const [pages, setPages] = useState<string[]>([]);
  const [mobilePdf, setMobilePdf] = useState<any | null>(null);
  const [totalPages, setTotalPages] = useState(0);
  const [currentPage, setCurrentPage] = useState(0);
  const [loading, setLoading] = useState(true);
  const [progress, setProgress] = useState(0);
  const [error, setError] = useState<PdfError | null>(null);
  const [loadNonce, setLoadNonce] = useState(0);
  const [isRebuilding, setIsRebuilding] = useState(false);
  const [isFullscreen, setIsFullscreen] = useState(false);
  const [pageRatio, setPageRatio] = useState(550 / 777);
  const [editingPage, setEditingPage] = useState<string | null>(null);

  useEffect(() => {
    if (!pdfMissing) setIsRebuilding(false);
  }, [pdfMissing]);

  const handleReload = useCallback(() => {
    if (error?.kind === 'missing') {
      setIsRebuilding(true);
      onRebuild?.();
      return;
    }
    setLoadNonce((n) => n + 1);
  }, [error, onRebuild]);

  const containerRef = useRef<HTMLDivElement>(null);
  const flipBookRef = useRef<any>(null);
  const blobUrlsRef = useRef<string[]>([]);
  const maxPageRef = useRef(0);
  const readStartRef = useRef(Date.now());
  const lastFlipTrackRef = useRef(0);

  const trackPageTurn = useCallback((page: number, total: number) => {
    if (page > maxPageRef.current) maxPageRef.current = page;
    const now = Date.now();
    if (now - lastFlipTrackRef.current > 3000) {
      lastFlipTrackRef.current = now;
      analytics.track('reader_page_turned', { page, total_pages: total });
    }
  }, []);

  useEffect(() => {
    readStartRef.current = Date.now();
    maxPageRef.current = 0;
    return () => {
      const total = totalPages || pages.length;
      if (total > 0 && maxPageRef.current > 0) {
        analytics.track('reader_max_depth', {
          max_page: maxPageRef.current,
          total_pages: total,
          reading_duration_ms: Date.now() - readStartRef.current,
        });
      }
    };
  }, [pdfUrl]);

  useImperativeHandle(ref, () => ({
    flipToLastPage: () => {
      const pf = flipBookRef.current?.pageFlip();
      if (pf && pages.length > 0) {
        const last = pages.length - 1;
        pf.turnToPage(last);
        setCurrentPage(last);
      }
    },
  }), [pages.length]);

  useEffect(() => {
    if (isMobile) {
      setError(null);
      setPages([]);
      setProgress(0);
      setCurrentPage(0);
      return;
    }

    if (pdfMissing) {
      setLoading(false);
      setError({ kind: 'missing' });
      setPages([]);
      setProgress(0);
      setCurrentPage(0);
      return;
    }

    let cancelled = false;

    async function loadPdf(attempt = 1): Promise<void> {
      try {
        setLoading(true);
        setError(null);
        setPages([]);
        setProgress(0);
        setCurrentPage(0);

        const pdf = await pdfjsLib.getDocument({
          url: pdfUrl,
          disableAutoFetch: true,
          disableStream: true,
        }).promise;
        const rawCount = pdf.numPages;
        const count = maxPageRatio && maxPageRatio < 1
          ? Math.max(1, Math.ceil(rawCount * maxPageRatio))
          : rawCount;
        const urls: string[] = [];

        setTotalPages(count);

        for (let pageIndex = 1; pageIndex <= count; pageIndex += 1) {
          if (cancelled) {
            break;
          }

          const page = await pdf.getPage(pageIndex);
          if (pageIndex === 1) {
            const viewport = page.getViewport({ scale: 1 });
            setPageRatio(viewport.width / viewport.height);
          }

          const dpr = window.devicePixelRatio || 1;
          const renderScale = isMobile
            ? Math.min(Math.max(1.25, dpr), 1.75)
            : Math.max(2, dpr);
          const viewport = page.getViewport({ scale: renderScale });
          const canvas = document.createElement('canvas');
          canvas.width = viewport.width;
          canvas.height = viewport.height;

          const context = canvas.getContext('2d');
          if (!context) {
            throw new Error('Canvas context unavailable');
          }

          await page.render({ canvas, canvasContext: context, viewport }).promise;
          urls.push(await canvasToBlobUrl(canvas));
          canvas.width = 0;
          canvas.height = 0;
          setProgress(pageIndex);
        }

        if (!cancelled) {
          blobUrlsRef.current = urls;
          setPages(urls);
          setLoading(false);
          onPageChange?.(0, urls.length);
        }
      } catch (loadError) {
        if (cancelled) return;
        const kind = classifyPdfError(loadError);
        if (kind === 'network' && attempt < 2) {
          console.warn(`PDF load attempt ${attempt} failed, retrying...`, loadError);
          await new Promise((resolve) => setTimeout(resolve, 800));
          if (cancelled) return;
          return loadPdf(attempt + 1);
        }
        console.error('PDF load error:', loadError);
        setError({ kind });
        setLoading(false);
      }
    }

    loadPdf();

    return () => {
      cancelled = true;
      blobUrlsRef.current.forEach((url) => URL.revokeObjectURL(url));
      blobUrlsRef.current = [];
    };
  }, [isMobile, pdfUrl, pdfMissing, loadNonce]);

  useEffect(() => {
    if (!isMobile) {
      setMobilePdf(null);
      return;
    }

    if (pdfMissing) {
      setLoading(false);
      setError({ kind: 'missing' });
      setMobilePdf(null);
      setTotalPages(0);
      return;
    }

    let cancelled = false;
    let pdfDoc: any | null = null;

    async function loadMobilePdf(attempt = 1): Promise<void> {
      try {
        setLoading(true);
        setError(null);
        setTotalPages(0);
        const pdf = await pdfjsLib.getDocument({
          url: pdfUrl,
          disableAutoFetch: true,
          disableStream: true,
        }).promise;
        pdfDoc = pdf;

        if (!cancelled) {
          setMobilePdf(pdf);
          const mobileCount = maxPageRatio && maxPageRatio < 1
            ? Math.max(1, Math.ceil(pdf.numPages * maxPageRatio))
            : pdf.numPages;
          setTotalPages(mobileCount);
          setLoading(false);
        } else {
          pdf.destroy();
        }
      } catch (loadError) {
        if (cancelled) return;
        const kind = classifyPdfError(loadError);
        if (kind === 'network' && attempt < 2) {
          console.warn(`Mobile PDF load attempt ${attempt} failed, retrying...`, loadError);
          await new Promise((resolve) => setTimeout(resolve, 800));
          if (cancelled) return;
          return loadMobilePdf(attempt + 1);
        }
        console.error('Mobile PDF load error:', loadError);
        setError({ kind });
        setLoading(false);
      }
    }

    loadMobilePdf();

    return () => {
      cancelled = true;
      if (pdfDoc) {
        pdfDoc.destroy().catch(() => {});
      }
    };
  }, [isMobile, pdfUrl, pdfMissing, loadNonce]);

  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if ((event.target as HTMLElement)?.tagName === 'INPUT') {
        return;
      }

      if (event.key === 'ArrowRight' || event.key === 'ArrowDown') {
        flipBookRef.current?.pageFlip()?.flipNext();
      }

      if (event.key === 'ArrowLeft' || event.key === 'ArrowUp') {
        flipBookRef.current?.pageFlip()?.flipPrev();
      }
    }

    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, []);

  useEffect(() => {
    function onFullscreenChange() {
      setIsFullscreen(Boolean(document.fullscreenElement));
    }

    document.addEventListener('fullscreenchange', onFullscreenChange);
    return () => document.removeEventListener('fullscreenchange', onFullscreenChange);
  }, []);

  function jumpToPage(pageNumber: number) {
    const pageFlip = flipBookRef.current?.pageFlip();
    if (!pageFlip) {
      return;
    }

    const targetPage = Math.max(0, Math.min(pageNumber - 1, pages.length - 1));
    pageFlip.turnToPage(targetPage);
    setCurrentPage(targetPage);
  }

  async function toggleFullscreen() {
    if (!containerRef.current) {
      return;
    }

    try {
      const entering = !document.fullscreenElement;
      if (entering) {
        await containerRef.current.requestFullscreen();
      } else {
        await document.exitFullscreen();
      }
      analytics.track('reader_fullscreen_toggled', { entered: entering });
    } catch {
      // Ignore fullscreen API failures on unsupported browsers.
    }
  }

  if (isMobile) {
    if (isRebuilding) {
      return (
        <div className="flipbook-container flipbook-container-mobile" ref={containerRef}>
          <div className="flipbook-loading-center">
            <div className="flipbook-spinner" />
            <p>正在重新加载 PDF，请稍候...</p>
          </div>
        </div>
      );
    }

    if (error) {
      return (
        <div className="flipbook-container flipbook-container-mobile" ref={containerRef}>
          <div className="flipbook-error">
            <p>PDF 加载失败</p>
            <div className="flipbook-error-actions">
              <button className="btn-lift-primary" onClick={handleReload}>
                重新加载
              </button>
              {onDownload && error.kind !== 'missing' && (
                <button className="btn-lift-ghost" onClick={onDownload}>
                  直接下载
                </button>
              )}
            </div>
          </div>
        </div>
      );
    }

    if (loading || !mobilePdf) {
      return (
        <div className="flipbook-container flipbook-container-mobile" ref={containerRef}>
          <div className="flipbook-loading-center">
            <div className="flipbook-spinner" />
            <p>{totalPages > 0 ? `正在准备预览... 共 ${totalPages} 页` : '正在加载 PDF...'}</p>
          </div>
        </div>
      );
    }

    return (
      <div className="flipbook-container flipbook-container-mobile" ref={containerRef}>
        <div className="flipbook-mobile-list">
          {Array.from({ length: totalPages }, (_, index) => (
            <MobilePdfPage
              key={`${pdfUrl}-${index + 1}`}
              pdf={mobilePdf}
              pageNumber={index + 1}
            />
          ))}
        </div>
        <div className="flipbook-mobile-hint">
          手机端已切换为逐页预览，向下滚动即可继续加载后续页面
        </div>
      </div>
    );
  }

  if (isRebuilding) {
    return (
      <div className="flipbook-container" ref={containerRef}>
        <div className="flipbook-loading-center">
          <div className="flipbook-spinner" />
          <p>正在重新加载 PDF，请稍候...</p>
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="flipbook-container" ref={containerRef}>
        <div className="flipbook-error">
          <p>PDF 加载失败</p>
          <div className="flipbook-error-actions">
            <button className="btn-lift-primary" onClick={handleReload}>
              重新加载
            </button>
            {onDownload && error.kind !== 'missing' && (
              <button className="btn-lift-ghost" onClick={onDownload}>
                直接下载
              </button>
            )}
          </div>
        </div>
      </div>
    );
  }

  if (loading || pages.length === 0) {
    return (
      <div className="flipbook-container" ref={containerRef}>
        <div className="flipbook-loading-center">
          <div className="flipbook-spinner" />
          <p>{totalPages > 0 ? `加载中... ${progress}/${totalPages}` : '正在加载 PDF...'}</p>
        </div>
      </div>
    );
  }

  return (
    <div
      className={`flipbook-container ${isFullscreen ? 'flipbook-fullscreen' : ''}`}
      ref={containerRef}
    >
      <div className="flipbook-stage">
        {/* @ts-expect-error react-pageflip types are incomplete for the forwarded ref API */}
        <HTMLFlipBook
          ref={flipBookRef}
          width={Math.round((compact ? 580 : DESKTOP_BOOK_HEIGHT) * pageRatio)}
          height={compact ? 580 : DESKTOP_BOOK_HEIGHT}
          size="stretch"
          minWidth={Math.round((compact ? 260 : 330) * pageRatio)}
          maxWidth={Math.round((compact ? 1100 : 1200) * pageRatio)}
          minHeight={compact ? 260 : 330}
          maxHeight={compact ? 1200 : 1320}
          showCover={true}
          maxShadowOpacity={0.4}
          mobileScrollSupport={isMobile}
          drawShadow={true}
          flippingTime={600}
          usePortrait={true}
          autoSize={true}
          onFlip={(event: { data: number }) => {
            setCurrentPage(event.data);
            onPageChange?.(event.data, pages.length);
            trackPageTurn(event.data, pages.length);
          }}
          className="fp-book"
        >
          {pages.map((image, index) => {
            if (index === 0 || index === pages.length - 1) {
              return <PageCover key={index} image={image} />;
            }

            return <Page key={index} image={image} number={index + 1} />;
          })}
        </HTMLFlipBook>
        {stageOverlay}
      </div>

      <div className="flipbook-bottom-bar">
        {bottomLeft && (
          <div className="flipbook-bottom-left">{bottomLeft}</div>
        )}

        <div className="flipbook-controls">
          <button
            className="flipbook-btn"
            onClick={() => flipBookRef.current?.pageFlip()?.flipPrev()}
            disabled={currentPage <= 0}
            title="上一页"
          >
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <polyline points="15 18 9 12 15 6" />
            </svg>
          </button>

          <span className="flipbook-page-info">
            <input
              className="flipbook-page-input"
              type="text"
              inputMode="numeric"
              value={editingPage !== null ? editingPage : String(currentPage + 1)}
              onChange={(event) => setEditingPage(event.target.value.replace(/\D/g, ''))}
              onKeyDown={(event) => {
                if (event.key === 'Enter') {
                  (event.target as HTMLInputElement).blur();
                }
                if (event.key === 'Escape') {
                  setEditingPage(null);
                  (event.target as HTMLInputElement).blur();
                }
                event.stopPropagation();
              }}
              onFocus={(event) => {
                setEditingPage(String(currentPage + 1));
                window.setTimeout(() => (event.target as HTMLInputElement).select(), 0);
              }}
              onBlur={() => {
                if (editingPage !== null && editingPage !== '') {
                  jumpToPage(Number(editingPage));
                }
                setEditingPage(null);
              }}
            />
            <span>/ {totalPages || pages.length}</span>
          </span>

          <button
            className="flipbook-btn"
            onClick={() => flipBookRef.current?.pageFlip()?.flipNext()}
            disabled={currentPage >= pages.length - 2}
            title="下一页"
          >
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <polyline points="9 18 15 12 9 6" />
            </svg>
          </button>

          <div className="flipbook-controls-separator" />

          <button
            className="flipbook-btn"
            onClick={toggleFullscreen}
            title={isFullscreen ? '退出全屏' : '全屏阅读'}
          >
            {isFullscreen ? (
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <polyline points="4 14 8 14 8 18" />
                <polyline points="20 10 16 10 16 6" />
                <line x1="14" y1="10" x2="21" y2="3" />
                <line x1="3" y1="21" x2="10" y2="14" />
              </svg>
            ) : (
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <polyline points="15 3 21 3 21 9" />
                <polyline points="9 21 3 21 3 15" />
                <line x1="21" y1="3" x2="14" y2="10" />
                <line x1="3" y1="21" x2="10" y2="14" />
              </svg>
            )}
          </button>
        </div>
      </div>
    </div>
  );
}

const FlipBookReader = React.forwardRef(FlipBookReaderInner);
export default FlipBookReader;
