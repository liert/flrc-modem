import React, { lazy, Suspense, useEffect } from 'react';
import { Header } from './components/layout/Header';
import { Sidebar } from './components/layout/Sidebar';
import { DashboardView } from './views/DashboardView';
import { useWorkstationStore } from './stores/useWorkstationStore';
import { useWebSocketStore } from './stores/useWebSocketStore';

const CommandLibraryView = lazy(() => import('./views/CommandLibraryView').then(m => ({ default: m.CommandLibraryView })));
const PipelineWorkbenchView = lazy(() => import('./views/PipelineWorkbenchView').then(m => ({ default: m.PipelineWorkbenchView })));
const SignalAnalysisView = lazy(() => import('./views/SignalAnalysisView').then(m => ({ default: m.SignalAnalysisView })));
const TaskCenterView = lazy(() => import('./views/TaskCenterView').then(m => ({ default: m.TaskCenterView })));

export const App: React.FC = () => {
  const activeTab = useWorkstationStore(state => state.activeTab);
  const connect = useWebSocketStore(state => state.connect);
  const disconnect = useWebSocketStore(state => state.disconnect);

  useEffect(() => {
    // 启动全局 WebSocket 实时事件订阅
    connect();
    return () => {
      disconnect();
    };
  }, [connect, disconnect]);

  const renderCurrentView = () => {
    switch (activeTab) {
      case 'dashboard':
        return <DashboardView />;
      case 'commands':
        return <CommandLibraryView />;
      case 'workbench':
        return <PipelineWorkbenchView />;
      case 'signals':
        return <SignalAnalysisView />;
      case 'tasks':
        return <TaskCenterView />;
      default:
        return <DashboardView />;
    }
  };

  return (
    <div className="flex flex-col h-screen w-screen bg-rf-bg text-rf-text overflow-hidden">
      <Header />
      <div className="flex flex-1 overflow-hidden">
        <Sidebar />
        <main className="flex-1 overflow-y-auto bg-rf-bg">
          <Suspense fallback={<div className="p-6 text-zinc-400" role="status">正在加载工作台…</div>}>
            {renderCurrentView()}
          </Suspense>
        </main>
      </div>
    </div>
  );
};

export default App;
