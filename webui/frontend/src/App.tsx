import React, { useEffect } from 'react';
import { Header } from './components/layout/Header';
import { Sidebar } from './components/layout/Sidebar';
import { DashboardView } from './views/DashboardView';
import { CommandLibraryView } from './views/CommandLibraryView';
import { PipelineWorkbenchView } from './views/PipelineWorkbenchView';
import { SignalAnalysisView } from './views/SignalAnalysisView';
import { TaskCenterView } from './views/TaskCenterView';
import { useWorkstationStore } from './stores/useWorkstationStore';
import { useWebSocketStore } from './stores/useWebSocketStore';

export const App: React.FC = () => {
  const { activeTab } = useWorkstationStore();
  const { connect, disconnect } = useWebSocketStore();

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
          {renderCurrentView()}
        </main>
      </div>
    </div>
  );
};

export default App;
