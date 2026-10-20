import React from 'react';
import { useQuery } from '@tanstack/react-query';
import { Radio, Wifi, WifiOff, StopCircle, RefreshCw, Cpu } from 'lucide-react';
import { api } from '../../api/client';
import { useWebSocketStore } from '../../stores/useWebSocketStore';

export const Header: React.FC = () => {
  const { connected } = useWebSocketStore();

  const { data: devices, refetch: refetchDevices, isFetching } = useQuery({
    queryKey: ['devices'],
    queryFn: api.getDevices,
    refetchInterval: 5000,
  });

  const { data: runs } = useQuery({
    queryKey: ['runs'],
    queryFn: () => api.getRuns(10, 0),
    refetchInterval: 3000,
  });

  const activeRun = runs?.find((r) => r.status === 'RUNNING');

  const usrp = devices?.find((d) => d.device_type === 'usrp');
  const hackrf = devices?.find((d) => d.device_type === 'hackrf');

  return (
    <header className="h-14 bg-rf-surface border-b border-rf-border px-4 flex items-center justify-between select-none">
      <div className="flex items-center space-x-3">
        <div className="w-8 h-8 rounded bg-gradient-to-br from-cyan-500 to-blue-600 flex items-center justify-center text-black font-bold shadow-lg shadow-cyan-500/20">
          <Radio className="w-5 h-5 text-black" />
        </div>
        <div>
          <h1 className="font-bold text-sm tracking-wide text-rf-text flex items-center space-x-2">
            <span>FLRC MODEM</span>
            <span className="text-[10px] px-1.5 py-0.5 rounded bg-rf-panel text-rf-accent font-mono border border-rf-border">
              v0.1.0 PRO
            </span>
          </h1>
          <p className="text-[11px] text-rf-muted">工业级 FLRC 调制解调与实时信号数据流工作台</p>
        </div>
      </div>

      {/* 硬件设备状态与运行监控徽章 */}
      <div className="flex items-center space-x-3">
        {/* USRP 硬件状态 */}
        <div
          className={`flex items-center space-x-1.5 px-2.5 py-1 rounded text-xs border font-mono ${
            usrp?.status === 'online'
              ? 'bg-emerald-950/40 text-emerald-400 border-emerald-800/60'
              : usrp?.status === 'busy'
              ? 'bg-amber-950/40 text-amber-400 border-amber-800/60 animate-pulse'
              : 'bg-zinc-900 text-zinc-500 border-zinc-800'
          }`}
          title={usrp?.error_message || JSON.stringify(usrp?.details || {})}
        >
          <Cpu className="w-3.5 h-3.5" />
          <span>USRP N310:</span>
          <span className="font-semibold uppercase">
            {usrp?.status === 'online' ? '就绪' : usrp?.status === 'busy' ? '占用' : '离线'}
          </span>
        </div>

        {/* HackRF 硬件状态 */}
        <div
          className={`flex items-center space-x-1.5 px-2.5 py-1 rounded text-xs border font-mono ${
            hackrf?.status === 'online'
              ? 'bg-emerald-950/40 text-emerald-400 border-emerald-800/60'
              : hackrf?.status === 'busy'
              ? 'bg-amber-950/40 text-amber-400 border-amber-800/60 animate-pulse'
              : 'bg-zinc-900 text-zinc-500 border-zinc-800'
          }`}
          title={hackrf?.error_message || JSON.stringify(hackrf?.details || {})}
        >
          <Radio className="w-3.5 h-3.5" />
          <span>HackRF:</span>
          <span className="font-semibold uppercase">
            {hackrf?.status === 'online' ? '就绪' : hackrf?.status === 'busy' ? '占用' : '未连接'}
          </span>
        </div>

        {/* 正在运行中的任务徽章 */}
        {activeRun && (
          <div className="flex items-center space-x-2 px-3 py-1 rounded bg-cyan-950/60 border border-cyan-800 text-cyan-300 text-xs font-mono animate-pulse">
            <RefreshCw className="w-3.5 h-3.5 animate-spin text-cyan-400" />
            <span>执行中: {activeRun.command_id}</span>
            <button
              onClick={() => api.stopRun(activeRun.id)}
              className="ml-1 text-red-400 hover:text-red-300 flex items-center space-x-1 px-1.5 py-0.5 rounded bg-red-950/50 hover:bg-red-900/60 border border-red-800/50 transition-colors"
              title="紧急停止当前任务"
            >
              <StopCircle className="w-3 h-3" />
              <span>停止</span>
            </button>
          </div>
        )}

        {/* WebSocket 连接状态 */}
        <div
          className={`flex items-center space-x-1 px-2 py-1 rounded text-[11px] font-mono border ${
            connected
              ? 'text-emerald-400 border-emerald-800/50 bg-emerald-950/20'
              : 'text-zinc-500 border-zinc-800 bg-zinc-900/50'
          }`}
          title={connected ? 'WebSocket 事件流实时同步已连接' : 'WebSocket 断开，尝试重连中'}
        >
          {connected ? <Wifi className="w-3 h-3 text-emerald-400" /> : <WifiOff className="w-3 h-3 text-zinc-500" />}
          <span>{connected ? 'WS LIVE' : 'WS OFFLINE'}</span>
        </div>

        {/* 刷新硬件按钮 */}
        <button
          onClick={() => refetchDevices()}
          disabled={isFetching}
          className="p-1.5 rounded hover:bg-rf-panel text-rf-muted hover:text-rf-text transition-colors border border-transparent hover:border-rf-border"
          title="手动探测硬件与刷新"
        >
          <RefreshCw className={`w-3.5 h-3.5 ${isFetching ? 'animate-spin text-rf-accent' : ''}`} />
        </button>
      </div>
    </header>
  );
};
