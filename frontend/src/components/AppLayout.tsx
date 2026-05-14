import { useLocation, useNavigate } from 'react-router-dom';

interface AppLayoutProps {
  children: React.ReactNode;
}

export default function AppLayout({ children }: AppLayoutProps) {
  const location = useLocation();
  const navigate = useNavigate();
  const isLanding = location.pathname === '/';
  const isCreate = location.pathname === '/create';
  const isUsage = location.pathname.startsWith('/usage');

  // 落地页 / 创建页 / 用量页：不显示 topbar
  if (isLanding || isCreate || isUsage) return <>{children}</>;

  return (
    <>
      <header className="ep-topbar">
        <div className="ep-topbar-inner">
          <div className="ep-topbar-logo" onClick={() => navigate('/')}>
            <span className="ep-topbar-logo-text">podcast-to-book</span>
          </div>
          <div style={{ flex: 1 }} />
        </div>
      </header>
      {children}
    </>
  );
}
