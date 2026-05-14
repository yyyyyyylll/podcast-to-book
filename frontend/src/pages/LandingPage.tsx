import { useEffect, useRef, useCallback, type MouseEvent } from 'react';
import { useNavigate } from 'react-router-dom';

const NAV_HEIGHT = 72;
const GITHUB_URL = 'https://github.com/yyyyyyylll/podcast-to-book';

function useRevealOnScroll() {
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    const targets = container.querySelectorAll('.lp-reveal');
    const observer = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          const el = entry.target as HTMLElement;
          const repeat = el.classList.contains('lp-reveal-repeat');
          if (entry.isIntersecting) {
            el.classList.add('lp-visible');
            if (!repeat) observer.unobserve(entry.target);
          } else if (repeat) {
            el.classList.remove('lp-visible');
          }
        });
      },
      { threshold: 0.2, rootMargin: '0px 0px -18% 0px' }
    );

    targets.forEach((el) => observer.observe(el));
    return () => observer.disconnect();
  }, []);

  return containerRef;
}

export default function LandingPage() {
  const navigate = useNavigate();
  const containerRef = useRevealOnScroll();
  const navRef = useRef<HTMLElement>(null);

  useEffect(() => {
    const nav = navRef.current;
    if (!nav) return;

    const onScroll = () => {
      nav.classList.toggle('lp-nav--scrolled', window.scrollY > 60);
    };
    window.addEventListener('scroll', onScroll, { passive: true });
    return () => window.removeEventListener('scroll', onScroll);
  }, []);

  const handleCTA = useCallback(() => {
    navigate('/create');
  }, [navigate]);

  const scrollTo = useCallback((e: MouseEvent<HTMLAnchorElement>) => {
    e.preventDefault();
    const id = e.currentTarget.getAttribute('href')?.slice(1);
    if (!id) return;
    const el = document.getElementById(id);
    if (!el) return;
    const top = el.getBoundingClientRect().top + window.scrollY - NAV_HEIGHT;
    window.scrollTo({ top, behavior: 'smooth' });
  }, []);

  return (
    <div className="lp" ref={containerRef}>
      {/* ── Navigation ── */}
      <nav className="lp-nav" ref={navRef}>
        <div className="lp-nav-inner">
          <div className="lp-nav-brand">
            <span className="lp-nav-name serif">podcast-to-book</span>
          </div>
          <div className="lp-nav-links">
            <a href="#how-it-works" className="lp-nav-link" onClick={scrollTo}>功能介绍</a>
            <a href="#showcase" className="lp-nav-link" onClick={scrollTo}>样例展示</a>
            <a href="#philosophy" className="lp-nav-link" onClick={scrollTo}>产品理念</a>
            <a href="#open-source" className="lp-nav-link" onClick={scrollTo}>支持项目</a>
          </div>
          <div className="ep-topbar-account">
            <a
              href={GITHUB_URL}
              target="_blank"
              rel="noreferrer"
              className="ep-topbar-login"
              style={{ textDecoration: 'none' }}
            >
              GitHub
            </a>
          </div>
        </div>
      </nav>

      {/* ── 1. Hero ── */}
      <section className="lp-hero">
        <div className="lp-hero-content">
          <div className="lp-hero-text">
            <p className="lp-hero-eyebrow lp-reveal">Podcast to Book</p>
            <h1 className="lp-hero-headline serif lp-reveal">
              这个时代的好内容，<br />
              需要被<i>写下来</i>
            </h1>
            <p className="lp-hero-sub lp-reveal">
              播客、访谈、长对话里藏着真正有价值的思考，<br className="lp-br-desktop" />
              但它们是为「听」设计的，不是为「留下来」而存在的。<br className="lp-br-desktop" />
              这个开源项目把它们变成可以翻阅、批注、收藏的书。
            </p>
            <button className="lp-hero-btn lp-reveal" onClick={handleCTA}>
              开始制作
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M5 12h14"/><path d="m12 5 7 7-7 7"/></svg>
            </button>
          </div>
          <div className="lp-hero-visual lp-reveal">
            <div className="lp-hero-book-stack">
              <div className="lp-hero-book lp-hero-book--back">
                <div className="lp-hero-book-inner">
                  <span className="lp-hero-book-spine" />
                  <img src="/hero-book-back.png" alt="书籍封面效果" draggable={false} />
                </div>
              </div>
              <div className="lp-hero-book lp-hero-book--front">
                <div className="lp-hero-book-inner">
                  <span className="lp-hero-book-spine" />
                  <img
                    src="/hero-book-front.jpg"
                    alt="书籍封面效果"
                    draggable={false}
                    decoding="async"
                    fetchPriority="high"
                  />
                </div>
              </div>
            </div>
          </div>
        </div>
      </section>

      {/* ── 2. How It Works ── */}
      <section className="lp-steps" id="how-it-works">
        <div className="lp-steps-inner">
          <h2 className="lp-steps-title serif lp-reveal">三步，从声音到书籍</h2>
          <div className="lp-steps-row">
            <div className="lp-step lp-reveal">
              <span className="lp-step-num serif">01</span>
              <h3 className="lp-step-name">粘贴链接</h3>
              <p className="lp-step-desc">
                复制粘贴一条小宇宙或 Apple Podcasts 单集链接，选择喜欢的封面模板。
              </p>
            </div>
            <div className="lp-step-divider lp-reveal" />
            <div className="lp-step lp-reveal">
              <span className="lp-step-num serif">02</span>
              <h3 className="lp-step-name">智能排版编辑</h3>
              <p className="lp-step-desc">
                自动转录、内容重组、金句提炼、插图设计、注释撰写、封面生成。
              </p>
            </div>
            <div className="lp-step-divider lp-reveal" />
            <div className="lp-step lp-reveal">
              <span className="lp-step-num serif">03</span>
              <h3 className="lp-step-name">获取书籍</h3>
              <p className="lp-step-desc">
                在网页内直接翻阅，下载精排 PDF / EPUB，或将 PDF 直接送印成纸质书。
              </p>
            </div>
          </div>
        </div>
      </section>

      {/* ── 3. eBook Showcase ── */}
      <section className="lp-showcase-ebook" id="showcase">
        <div className="lp-showcase-ebook-inner">
          <div className="lp-showcase-ebook-text lp-reveal">
            <p className="lp-showcase-eyebrow">eBook</p>
            <h2 className="lp-showcase-headline serif">
              不只是文字稿，<br />是一本完整的书
            </h2>
            <p className="lp-showcase-desc">
              封面、序言、目录、章节、插图、金句、注释——<br className="lp-br-desktop" />
              每个部分都经过精心编辑。<br className="lp-br-desktop" />
              它不再只是「听过」的内容，而是一本你可以反复翻开的书。
            </p>
          </div>
          <div className="lp-showcase-ebook-gallery lp-reveal">
            <div className="lp-showcase-book lp-showcase-book--4">
              <img src="/ebook-showcase/screenshot-4.png" alt="编者序" draggable={false} />
            </div>
            <div className="lp-showcase-book lp-showcase-book--5">
              <img
                src="/ebook-showcase/screenshot-5.png"
                alt="正文页"
                draggable={false}
                loading="lazy"
                decoding="async"
              />
            </div>
            <div className="lp-showcase-book lp-showcase-book--6">
              <img src="/ebook-showcase/screenshot-6.png" alt="正文页" draggable={false} />
            </div>
            <div className="lp-showcase-book lp-showcase-book--1">
              <img src="/ebook-showcase/screenshot-1.png" alt="目录页" draggable={false} />
            </div>
            <div className="lp-showcase-book lp-showcase-book--2">
              <img src="/ebook-showcase/screenshot-2.png" alt="封面" draggable={false} />
            </div>
            <div className="lp-showcase-book lp-showcase-book--3">
              <img src="/ebook-showcase/screenshot-3.png" alt="精华提要" draggable={false} />
            </div>
          </div>
        </div>
      </section>

      {/* ── 4. Print Showcase ── */}
      <section className="lp-showcase-print">
        <div className="lp-showcase-print-inner">
          <div className="lp-showcase-print-visual lp-reveal">
            <img src="/print-showcase.jpg" alt="实体书样品" style={{ width: '100%', height: '400px', objectFit: 'cover', borderRadius: '12px' }} />
          </div>
          <div className="lp-showcase-print-text lp-reveal">
            <p className="lp-showcase-eyebrow">Print</p>
            <h2 className="lp-showcase-headline serif">
              从屏幕走出来，<br />摆在书架上
            </h2>
            <p className="lp-showcase-desc">
              导出的 PDF 直接送印即可，自动按印刷标准排版。<br className="lp-br-desktop" />
              当一场存在于耳机里的对话，真的变成一本拿在手里的书，<br className="lp-br-desktop" />
              那种感觉很奇妙——有重量、有温度，可以翻开，也可以收藏。
            </p>
          </div>
        </div>
      </section>

      {/* ── 5. Philosophy ── */}
      <section className="lp-philosophy" id="philosophy">
        <div className="lp-philosophy-inner">
          <div className="lp-philosophy-left lp-reveal lp-reveal-repeat">
            <p className="lp-philosophy-eyebrow">Philosophy</p>
            <h2 className="lp-philosophy-title serif">
              {[
                { text: '这个时代不缺内容，', offset: 0 },
                { text: '缺的是一种', offset: 9 },
                { text: '更适合被保存的形态。', offset: 14 },
              ].map(({ text, offset }, li) => (
                <span key={li}>
                  {li > 0 && <br />}
                  {[...text].map((ch, ci) => (
                    <span
                      key={ci}
                      className="lp-phil-char"
                      style={{ '--i': offset + ci } as React.CSSProperties}
                    >
                      {ch}
                    </span>
                  ))}
                </span>
              ))}
            </h2>
          </div>
          <div className="lp-philosophy-right">
            <p className="lp-reveal lp-reveal-repeat">
              过去，最重要的思想，往往以书籍的形式保存下来。今天，很多真正有洞见的内容，反而藏在播客和访谈里。但播客天然是为表达而设计的，而非为听众的有效获取。倾听容易错过重要信息，线性播放不利于深度思考，术语在耳朵里来不及消化。好内容，听的时候过瘾，听完却很难真正留下来。
            </p>
            <p className="lp-reveal lp-reveal-repeat">
              但对话是可以被留下来的，《论语》其实就是对话的整理——被重新组织、被结构化，变成了一种可以被长期阅读的形式。同样，这个项目做的不是转录，是重新整理：让结构清晰，让观点浮现，让一场好的对话变成一本值得反复翻阅的书籍。
            </p>
          </div>
        </div>
      </section>

      {/* ── 6. Support the Project ── */}
      <section className="lp-community" id="open-source">
        <div
          style={{
            maxWidth: 1080,
            margin: '0 auto',
            padding: '0 24px',
            display: 'grid',
            gridTemplateColumns: '1.4fr 1fr',
            gap: 80,
            alignItems: 'center',
          }}
        >
          <div className="lp-reveal" style={{ minWidth: 0 }}>
            <p className="lp-showcase-eyebrow">Support</p>
            <h2 className="serif" style={{ fontSize: 40, fontWeight: 600, lineHeight: 1.25, marginBottom: 24, color: '#1a1a1a' }}>
              如果它<span className="lp-accent-bracket">「帮到了你」</span>
            </h2>
            <p style={{ fontSize: 16, lineHeight: 1.8, color: '#3F3F3F', marginBottom: 28 }}>
              如果它对你有用，欢迎用下面任一方式支持后续开发。
            </p>
            <ol style={{ fontSize: 16, lineHeight: 1.9, color: '#3F3F3F', paddingLeft: 22, margin: 0 }}>
              <li style={{ marginBottom: 10 }}>
                去 GitHub <a href={GITHUB_URL} target="_blank" rel="noreferrer" style={{ color: '#8a6d3b', textDecoration: 'underline', textUnderlineOffset: 3 }}>点个 star</a>
              </li>
              <li style={{ marginBottom: 10 }}>
                扫右侧二维码打赏一杯咖啡 ☕
              </li>
              <li>
                <a href={GITHUB_URL + '/issues'} target="_blank" rel="noreferrer" style={{ color: '#8a6d3b', textDecoration: 'underline', textUnderlineOffset: 3 }}>提 issue / PR</a> 一起把它做得更好
              </li>
            </ol>
          </div>

          <div className="lp-reveal" style={{ display: 'flex', justifyContent: 'center', alignItems: 'center' }}>
            <img
              src="/sponsor-qr.png"
              alt="赞赏二维码"
              style={{
                width: '100%',
                maxWidth: 320,
                aspectRatio: '1 / 1',
                objectFit: 'cover',
                borderRadius: 16,
                boxShadow: '0 1px 2px rgba(0,0,0,0.04), 0 24px 60px -16px rgba(0,0,0,0.12)',
              }}
            />
          </div>
        </div>
      </section>

      {/* ── 7. Footer ── */}
      <footer className="lp-footer">
        <div className="lp-footer-body">
          <div className="lp-footer-left">
            <div className="lp-footer-brand">
              <span className="lp-footer-name serif">podcast-to-book</span>
              <span className="lp-footer-eq" aria-hidden="true">
                <span /><span /><span /><span /><span />
              </span>
            </div>
            <nav className="lp-footer-nav">
              <a href="#how-it-works" onClick={scrollTo}>功能介绍</a>
              <a href="#showcase" onClick={scrollTo}>样例展示</a>
              <a href="#philosophy" onClick={scrollTo}>产品理念</a>
              <a href={GITHUB_URL} target="_blank" rel="noreferrer">GitHub</a>
            </nav>
          </div>
          <div className="lp-footer-right">
            <p className="lp-footer-slogan serif">声而有形，读有所得。</p>
            <button className="lp-footer-cta" onClick={handleCTA}>
              <span>开始制作你的第一本书</span>
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M5 12h14"/><path d="m12 5 7 7-7 7"/></svg>
            </button>
          </div>
        </div>
        <p className="lp-footer-copy">AGPL-3.0 开源 · podcast-to-book</p>
      </footer>
    </div>
  );
}
