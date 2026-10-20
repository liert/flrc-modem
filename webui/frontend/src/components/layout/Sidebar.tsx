import React from 'react';
import {
  LayoutDashboard,
  TerminalSquare,
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
    <aside className="w-56 bg-rf-surface border-r border-rf-border flex flex-col justify-between select-none">
      <nav className="p-2 space-y-1">
        {NAV_ITEMS.map((item) => {
          const Icon = item.icon;
          const isActive = activeTab === item.id;
          return (
            <button
              key={item.id}
              onClick={() => setActiveTab(item.id)}
              className={`w-full flex items-center space-x-3 px-3 py-2.5 rounded text-left transition-all group ${
                isActive
                  ? 'bg-cyan-500/10 text-rf-accent border border-cyan-500/30 shadow-sm shadow-cyan-500/10 font-medium'
                  : 'text-rf-muted hover:bg-rf-panel hover:text-rf-text border border-transparent'
              }`}
            >
              <Icon
                className={`w-4 h-4 flex-shrink-0 transition-colors ${
                  isActive ? 'text-rf-accent' : 'text-zinc-500 group-hover:text-rf-text'
                }`}
              />
              <div className="flex-1 min-w-0">
                <div className="text-xs truncate">{item.label}</div>
                <div className="text-[10px] text-zinc-500 truncate font-mono">{item.sublabel}</div>
              </div>
              {isActive && <div className="w-1.5 h-1.5 rounded-full bg-rf-accent shadow-sm shadow-rf-accent" />}
            </button>
          );
        })}
      </nav>

      {/* 底部信息栏 */}
      <div className="p-3 border-t border-rf-border text-[11px] text-zinc-500 space-y-1">
        <div className="flex justify-between items-center font-mono">
          <span>FLRC PHY</span>
          <span className="text-zinc-400">SX1280 Compatible</span>
        </div>
        <div className="flex justify-between items-center font-mono">
          <span>UHD API</span>
          <span className="text-zinc-400">Zero-Drift Sync</span>
        </div>
      </div>
    </aside>
  );
};
