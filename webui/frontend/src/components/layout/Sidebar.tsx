import React from 'react';
import {
  LayoutDashboard,
  TerminalSquare,
  FileCode,
  GitBranch,
  Activity,
  ListOrdered,
  ChevronLeft,
  ChevronRight,
  Radio,
  Radar,
  Clock,
  Send,
  HardDrive,
} from 'lucide-react';
import { ActiveTab, useWorkstationStore } from '../../stores/useWorkstationStore';
import { useMediaQuery } from '../../hooks/useMediaQuery';

interface NavItem {
  id: ActiveTab;
  label: string;
  sublabel: string;
  icon: React.ComponentType<{ className?: string }>;
}

export interface SidebarProps {
  collapsed?: boolean;
  onToggle?: () => void;
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
    id: 'spectrum',
    label: 'SDR 实时频谱',
    sublabel: 'Spectrum & Waterfall',
    icon: Radio,
  },
  {
    id: 'scan',
    label: '信道盲扫感知',
    sublabel: 'Channel Sensing',
    icon: Radar,
  },
  {
    id: 'sync',
    label: '跳频时序同步',
    sublabel: 'FHSS & TDD Sync',
    icon: Clock,
  },
  {
    id: 'transmit',
    label: '调制发射交互',
    sublabel: 'Modulate & Transmit',
    icon: Send,
  },
  {
    id: 'signals',
    label: '信号与解调分析',
    sublabel: 'Signal & Analysis',
    icon: Activity,
  },
  {
    id: 'devices',
    label: 'SDR 硬件监控',
    sublabel: 'Hardware & Health',
    icon: HardDrive,
  },
  {
    id: 'tasks',
    label: '任务执行中心',
    sublabel: 'Task Center & Logs',
    icon: ListOrdered,
  },
];

export const Sidebar: React.FC<SidebarProps> = ({ collapsed, onToggle }) => {
  const savedCollapsed = useWorkstationStore(state => state.sidebarCollapsed);
  const savedToggle = useWorkstationStore(state => state.toggleSidebar);
  const activeTab = useWorkstationStore(state => state.activeTab);
  const setActiveTab = useWorkstationStore(state => state.setActiveTab);
  const compact = useMediaQuery('(max-width: 1023px)');
  const sidebarCollapsed = compact || (collapsed ?? savedCollapsed);
  const toggleSidebar = onToggle ?? savedToggle;

  return (
    <aside
      aria-label="工作台主导航"
      className={`relative flex flex-col justify-between select-none z-20 flex-shrink-0 bg-rf-surface/95 border-r border-rf-border transition-all duration-300 ease-in-out group/sidebar ${
        sidebarCollapsed ? 'w-16' : 'w-56'
      }`}
      onTransitionEnd={(e) => {
        if (e.target === e.currentTarget && e.propertyName === 'width') {
          window.dispatchEvent(new Event('resize'));
        }
      }}
    >
      {/* 侧边栏折叠/展开切换按钮 (悬浮于右侧边框) */}
      {!compact && <button
        type="button"
        onClick={toggleSidebar}
        title={sidebarCollapsed ? '展开侧边栏 (Sidebar Expand)' : '折叠侧边栏 (Sidebar Collapse)'}
        aria-label={sidebarCollapsed ? '展开侧边栏' : '折叠侧边栏'}
        className="absolute -right-3 top-7 z-30 w-6 h-6 rounded-full border border-rf-border bg-rf-panel/95 backdrop-blur-md flex items-center justify-center text-zinc-400 hover:text-cyan-300 hover:border-cyan-500/60 hover:bg-rf-surface shadow-md shadow-black/40 transition-all duration-200 cursor-pointer opacity-0 group-hover/sidebar:opacity-100 focus:opacity-100 hover:scale-110 active:scale-95 before:content-[''] before:absolute before:-inset-1.5"
      >
        {sidebarCollapsed ? (
          <ChevronRight className="w-3.5 h-3.5 transition-transform" />
        ) : (
          <ChevronLeft className="w-3.5 h-3.5 transition-transform" />
        )}
      </button>}

      <nav className="p-2 space-y-1" aria-label="功能视图">
        {NAV_ITEMS.map((item) => {
          const Icon = item.icon;
          const isActive = activeTab === item.id;
          return (
            <button
              key={item.id}
              type="button"
              aria-label={item.label}
              aria-current={isActive ? 'page' : undefined}
              onClick={() => setActiveTab(item.id)}
              title={sidebarCollapsed ? `${item.label} (${item.sublabel})` : undefined}
              className={`w-full flex items-center rounded-lg text-left transition-all duration-200 group relative ${
                sidebarCollapsed
                  ? 'justify-center p-2'
                  : 'space-x-3 px-3 py-2.5'
              } ${
                isActive
                  ? 'bg-gradient-to-r from-cyan-500/15 via-cyan-500/5 to-transparent text-cyan-300 font-medium shadow-xs shadow-cyan-500/10 border-l-2 border-cyan-400'
                  : 'text-zinc-400 hover:bg-rf-panel/70 hover:text-rf-text border-l-2 border-transparent'
              }`}
            >
              <div
                className={`p-1.5 rounded-md transition-colors flex-shrink-0 relative ${
                  isActive
                    ? 'bg-cyan-500/20 text-cyan-300 shadow-xs ring-1 ring-cyan-500/30'
                    : 'bg-rf-panel/60 text-zinc-400 group-hover:text-rf-text group-hover:bg-rf-panel'
                }`}
              >
                <Icon className="w-4 h-4 flex-shrink-0" />
                {/* 折叠状态下选中时的小角标脉冲点 */}
                {sidebarCollapsed && isActive && (
                  <span className="absolute -top-0.5 -right-0.5 w-1.5 h-1.5 rounded-full bg-cyan-400 ring-2 ring-rf-surface animate-pulse" />
                )}
              </div>

              {/* 展开状态文字：平滑宽度与透明度过渡 */}
              <div
                className={`min-w-0 transition-all duration-300 ease-in-out whitespace-nowrap overflow-hidden ${
                  sidebarCollapsed
                    ? 'w-0 opacity-0 max-w-0 pointer-events-none'
                    : 'flex-1 opacity-100 max-w-[160px]'
                }`}
              >
                <div className={`text-xs truncate ${isActive ? 'text-white font-semibold' : 'text-zinc-300'}`}>
                  {item.label}
                </div>
                <div className="text-[10px] text-zinc-500 truncate font-mono">{item.sublabel}</div>
              </div>

              {/* 展开状态尾部活动指示点 */}
              {!sidebarCollapsed && isActive && (
                <div className="w-1.5 h-1.5 rounded-full bg-cyan-400 shadow-sm shadow-cyan-400 animate-pulse flex-shrink-0 ml-1.5" />
              )}

              {/* 折叠状态下的悬停 Tooltip */}
              {sidebarCollapsed && (
                <div
                  role="tooltip"
                  aria-hidden="true"
                  className="absolute left-full ml-3 top-1/2 -translate-y-1/2 px-2.5 py-1.5 bg-rf-panel/95 backdrop-blur-md border border-rf-border rounded-md shadow-2xl text-xs whitespace-nowrap pointer-events-none opacity-0 group-hover:opacity-100 group-focus-visible:opacity-100 transition-all duration-150 z-50 flex flex-col gap-0.5 transform -translate-x-1 group-hover:translate-x-0"
                >
                  <div className="flex items-center space-x-1.5">
                    <span className="font-semibold text-white tracking-wide">{item.label}</span>
                    {isActive && (
                      <span className="w-1.5 h-1.5 rounded-full bg-cyan-400 shadow-xs shadow-cyan-400 animate-pulse" />
                    )}
                  </div>
                  <span className="text-[10px] text-zinc-400 font-mono">{item.sublabel}</span>
                  {/* 侧向小指示箭头 */}
                  <div className="absolute top-1/2 -left-1 -translate-y-1/2 w-2 h-2 bg-rf-panel border-l border-b border-rf-border rotate-45" />
                </div>
              )}
            </button>
          );
        })}
      </nav>

      {/* 底部信息栏 */}
      <div className="p-2.5 border-t border-rf-border/80 text-[11px] text-zinc-500 bg-rf-bg/40 transition-all duration-300 relative group/footer">
        {!sidebarCollapsed ? (
          <div className="space-y-1.5 whitespace-nowrap overflow-hidden transition-all duration-300">
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
        ) : (
          <div className="flex flex-col items-center justify-center space-y-1 py-1 transition-all duration-300 cursor-default">
            <span
              className="text-cyan-400 font-semibold font-mono text-[9px] px-1 py-0.5 rounded bg-cyan-950/80 border border-cyan-800/80 leading-none"
              title="PHY MODEM: SX1280 FLRC"
            >
              FLRC
            </span>
            <span
              className="text-emerald-400 font-semibold font-mono text-[9px] px-1 py-0.5 rounded bg-emerald-950/80 border border-emerald-800/80 leading-none"
              title="SDR ENGINE: USRP + HACKRF"
            >
              SDR
            </span>
          </div>
        )}

        {/* 折叠状态下底部信息悬停 Tooltip */}
        {sidebarCollapsed && (
          <div
            role="tooltip"
            className="absolute left-full ml-3 bottom-2 px-2.5 py-1.5 bg-rf-panel/95 backdrop-blur-md border border-rf-border rounded-md shadow-2xl text-xs whitespace-nowrap pointer-events-none opacity-0 group-hover/footer:opacity-100 transition-all duration-150 z-50 flex flex-col gap-1 transform -translate-x-1 group-hover/footer:translate-x-0"
          >
            <div className="flex items-center justify-between space-x-3 font-mono text-[10px]">
              <span className="text-zinc-400">PHY MODEM:</span>
              <span className="text-cyan-300 font-semibold">SX1280 FLRC</span>
            </div>
            <div className="flex items-center justify-between space-x-3 font-mono text-[10px]">
              <span className="text-zinc-400">SDR ENGINE:</span>
              <span className="text-emerald-300 font-semibold">USRP + HACKRF</span>
            </div>
            <div className="absolute bottom-3 -left-1 w-2 h-2 bg-rf-panel border-l border-b border-rf-border rotate-45" />
          </div>
        )}
      </div>
    </aside>
  );
};
