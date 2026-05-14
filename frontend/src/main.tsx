import React from 'react';
import ReactDOM from 'react-dom/client';
import { BrowserRouter, Routes, Route } from 'react-router-dom';
import { ConfigProvider } from 'antd';
import zhCN from 'antd/locale/zh_CN';

import LandingPage from './pages/LandingPage';
import CreatePage from './pages/CreatePage';
import ProcessingPage from './pages/ProcessingPage';
import ResultPage from './pages/ResultPage';
import EditPage from './pages/EditPage';
import UsagePage from './pages/UsagePage';
import AppLayout from './components/AppLayout';
import './index.css';

const FONT = "'DM Sans', -apple-system, BlinkMacSystemFont, 'PingFang SC', 'Noto Sans SC', sans-serif";

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <ConfigProvider
      locale={zhCN}
      theme={{
        token: {
          colorPrimary: '#111111',
          fontFamily: FONT,
          fontSize: 16,
          colorText: '#141414',
          colorTextPlaceholder: '#A3A3A3',
          controlHeight: 48,
        },
      }}
    >
      <BrowserRouter>
        <AppLayout>
          <Routes>
            <Route path="/" element={<LandingPage />} />
            <Route path="/create" element={<CreatePage />} />
            <Route path="/processing/:taskId" element={<ProcessingPage />} />
            <Route path="/result/:taskId" element={<ResultPage />} />
            <Route path="/edit/:taskId" element={<EditPage />} />
            <Route path="/usage/:taskId" element={<UsagePage />} />
          </Routes>
        </AppLayout>
      </BrowserRouter>
    </ConfigProvider>
  </React.StrictMode>,
);
