import React from 'react';
import {
  LayoutDashboard,
  TerminalSquare,
  FileCode,
  GitBranch,
  Activity,
  ListOrdered,
  Settings,
} from 'lucide-react';
import { ActiveTab, useWorkstationStore } from '../../stores/useWorkstationStore';

interface NavItem {
  id: ActiveTab;
  label: string;
  sublabel: string;
  icon: React.ComponentType<{ className?: string }>;
}

const NAV_ITEMS: NavItem[] = [
  {
    id: 'dashboard',
    label: '总览仪表盘',
    sublabel: 'Dashboard',
    icon: LayoutDashboard,
  },
  {
    id: 'commands',
    label: '命令控制库',
    sublabel: 'Command Library',
    icon: TerminalSquare,
  },
  {
    id: 'templates',
    label: '模板预设库',
    sublabel: 'Template Library',
    icon: FileCode,
  },
  {
    id: 'workbench',
    label: '数据流工作台',
    sublabel: 'Dataflow Pipeline',
    icon: GitBranch,
  },
  {
    id: 'signals',
    label: '信号与解调分析',
    sublabel: 'Signal & Analysis',
    icon: Activity,
  },
  {
    id: 'tasks',
    label: '任务执行中心',
    sublabel: 'Task Center & Logs',
    icon: ListOrdered,
  },
];

export const Sidebar: React.FC = () => {
  const { activeTab, setActiveTab } = useWorkstationStore();

  return (
    <aside className="w-56 bg-rf-surface/95 border-r border-rf-border flex flex-col justify-between select-none">
      <nav className="p-2 space-y-1">
        {NAV_ITEMS.map((item) => {
          const Icon = item.icon;
          const isActive = activeTab === item.id;
          return (
            <button
              key={item.id}
              onClick={() => setActiveTab(item.id)}
              className={`w-full flex items-center space-x-3 px-3 py-2.5 rounded-lg text-left transition-all group ${
                isActive
                  ? 'bg-gradient-to-r from-cyan-500/15 via-cyan-500/5 to-transparent text-cyan-300 border-l-2 border-cyan-400 font-medium shadow-xs shadow-cyan-500/10'
                  : 'text-zinc-400 hover:bg-rf-panel/70 hover:text-rf-text border-l-2 border-transparent'
              }`}
            >
              <div
                className={`p-1.5 rounded-md transition-colors ${
                  isActive
                    ? 'bg-cyan-500/20 text-cyan-300 shadow-xs'
                    : 'bg-rf-panel/60 text-zinc-400 group-hover:text-rf-text group-hover:bg-rf-panel'
                }`}
              >
                <Icon className="w-4 h-4 flex-shrink-0" />
              </div>
              <div className="flex-1 min-w-0">
                <div className={`text-xs truncate ${isActive ? 'text-white font-semibold' : 'text-zinc-300'}`}>
                  {item.label}
                </div>
                <div className="text-[10px] text-zinc-500 truncate font-mono">{item.sublabel}</div>
              </div>
              {isActive && (
                <div className="w-1.5 h-1.5 rounded-full bg-cyan-400 shadow-sm shadow-cyan-400 animate-pulse" />
              )}
            </button>
          );
        })}
      </nav>

      {/* 底部信息栏 */}
      <div className="p-3 border-t border-rf-border/80 text-[11px] text-zinc-500 space-y-1.5 bg-rf-bg/40">
        <div className="flex justify-between items-center font-mono">
          <span className="text-zinc-500">PHY MODEM</span>
          <span className="text-cyan-400 font-semibold text-[10px] px-1.5 py-0.2 rounded bg-cyan-950/60 border border-cyan-800/60">
            SX1280 FLRC
          </span>
        </div>
        <div className="flex justify-between items-center font-mono">
          <span className="text-zinc-500">SDR ENGINE</span>
          <span className="text-emerald-400 font-semibold text-[10px] px-1.5 py-0.2 rounded bg-emerald-950/60 border border-emerald-800/60">
            USRP + HACKRF
          </span>
        </div>
      </div>
    </aside>
  );
};
