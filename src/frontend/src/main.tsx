import React from 'react';
import ReactDOM from 'react-dom/client';
import { ConfigProvider } from 'antd';
import zhCN from 'antd/locale/zh_CN';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { BrowserRouter } from 'react-router-dom';
import App from './App';
import './styles.css';

const client = new QueryClient({ defaultOptions: { queries: { retry: 1, refetchOnWindowFocus: false, staleTime: 1000 } } });
ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <ConfigProvider locale={zhCN} theme={{ token: { colorPrimary: '#9f4e32', colorText: '#2f2924', colorBgContainer: '#fffefb', colorBorder: '#e5dbcf', borderRadius: 10, fontFamily: 'Inter, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif', fontSize: 14 }, components: { Button: { controlHeight: 38 }, Select: { controlHeight: 30 }, Modal: { borderRadiusLG: 16 } } }}>
      <QueryClientProvider client={client}><BrowserRouter><App /></BrowserRouter></QueryClientProvider>
    </ConfigProvider>
  </React.StrictMode>,
);
