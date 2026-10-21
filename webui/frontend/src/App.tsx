import React, { lazy, Suspense, useEffect } from 'react';
import { Header } from './components/layout/Header';
import { Sidebar } from './components/layout/Sidebar';
import { DashboardView } from './views/DashboardView';
import { useWorkstationStore } from './stores/useWorkstationStore';
import { useWebSocketStore } from './stores/useWebSocketStore';
import { useQueryClient } from '@tanstack/react-query';
import { refreshForEvent } from './utils/analysis';
import { deletedRunIds, replacementRunId, purgeRunCaches } from './utils/runs';

const CommandLibraryView = lazy(() => import('./views/CommandLibraryView').then(m => ({ default: m.CommandLibraryView })));
const TemplateLibraryView = lazy(() => import('./views/TemplateLibraryView').then(m => ({ default: m.TemplateLibraryView })));
const PipelineWorkbenchView = lazy(() => import('./views/PipelineWorkbenchView').then(m => ({ default: m.PipelineWorkbenchView })));
const SignalAnalysisView = lazy(() => import('./views/SignalAnalysisView').then(m => ({ default: m.SignalAnalysisView })));
const TaskCenterView = lazy(() => import('./views/TaskCenterView').then(m => ({ default: m.TaskCenterView })));

export const App: React.FC = () => {
  const queryClient = useQueryClient();
  const activeTab = useWorkstationStore(state => state.activeTab);
  const connect = useWebSocketStore(state => state.connect);
  const disconnect = useWebSocketStore(state => state.disconnect);

  useEffect(() => {
    const unsubscribe = useWebSocketStore.subscribe((state, previous) => {
      if (state.latestEvent !== previous.latestEvent && state.latestEvent) {
        const event = state.latestEvent;
        const ids = deletedRunIds(event);
        if (ids.length) {
          const workstation = useWorkstationStore.getState();
          if (workstation.selectedRunId && ids.includes(workstation.selectedRunId)) {
            workstation.setSelectedRunId(replacementRunId(event));
            workstation.setSelectedArtifactPath(null);
          }
          for (const id of ids) useWebSocketStore.getState().clearRunData(id);
          void purgeRunCaches(queryClient, ids).then(() => refreshForEvent(queryClient, event));
        } else refreshForEvent(queryClient, event);
      }
      if (state.connected && !previous.connected) {
        for (const queryKey of [['runs'], ['artifacts'], ['analysisContext']]) {
          void queryClient.invalidateQueries({ queryKey });
        }
      }
    });
    // 启动全局 WebSocket 实时事件订阅
    connect();
    return () => {
      disconnect();
      unsubscribe();
    };
  }, [connect, disconnect, queryClient]);

  const renderCurrentView = () => {
    switch (activeTab) {
      case 'dashboard':
        return <DashboardView />;
      case 'commands':
        return <CommandLibraryView />;
      case 'templates':
        return <TemplateLibraryView />;
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
