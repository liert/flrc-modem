import React from 'react';
import { useQuery } from '@tanstack/react-query';
import { Radio, Wifi, WifiOff, StopCircle, RefreshCw, Cpu, Activity } from 'lucide-react';
import { api } from '../../api/client';
import { useWebSocketStore } from '../../stores/useWebSocketStore';

export const Header: React.FC = () => {
  const connected = useWebSocketStore(state => state.connected);

  const { data: devices, refetch: refetchDevices, isFetching } = useQuery({
    queryKey: ['devices'],
    queryFn: api.getDevices,
    refetchInterval: 5000,
  });

  const { data: runs } = useQuery({
    queryKey: ['runs', 10, 0],
    queryFn: () => api.getRuns(10, 0),
    refetchInterval: 3000,
  });

  const activeRun = runs?.find((r) => r.status === 'RUNNING');

  const usrp = devices?.find((d) => d.device_type === 'usrp');
  const hackrf = devices?.find((d) => d.device_type === 'hackrf');

  return (
    <header className="h-14 bg-rf-surface/95 backdrop-blur-md border-b border-rf-border px-4 flex items-center justify-between select-none sticky top-0 z-50 shadow-sm shadow-black/30">
      {/* 品牌与标题 */}
      <div className="flex items-center space-x-3.5">
        <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-cyan-400 via-cyan-500 to-blue-600 flex items-center justify-center text-black shadow-md shadow-cyan-500/20 border border-cyan-300/40">
          <Activity className="w-5 h-5 text-black stroke-[2.5]" />
        </div>
        <div>
          <div className="flex items-center space-x-2">
            <h1 className="font-bold text-sm tracking-wide text-rf-text font-mono flex items-center space-x-2">
              <span className="bg-gradient-to-r from-white via-cyan-100 to-cyan-300 bg-clip-text text-transparent">
                FLRC MODEM
              </span>
            </h1>
            <span className="text-[10px] px-1.5 py-0.2 rounded-full bg-cyan-950/70 text-cyan-300 font-mono border border-cyan-800/80 shadow-xs">
              v0.2.0 PRO
            </span>
          </div>
          <p className="text-[11px] text-zinc-400 font-sans tracking-tight">
            工业级 2.4GHz FLRC 调制解调与实时信号分析工作台
          </p>
        </div>
      </div>

      {/* 硬件设备状态与运行监控徽章 */}
      <div className="flex items-center space-x-2.5">
        {/* USRP 硬件状态 */}
        <div
          className={`flex items-center space-x-2 px-2.5 py-1 rounded-md text-xs border font-mono transition-colors shadow-xs ${
            usrp?.status === 'online'
              ? 'bg-emerald-950/30 text-emerald-300 border-emerald-700/60'
              : usrp?.status === 'busy'
              ? 'bg-amber-950/40 text-amber-300 border-amber-600/70 animate-pulse'
              : 'bg-zinc-900/60 text-zinc-400 border-zinc-800'
          }`}
          title={usrp?.error_message || JSON.stringify(usrp?.details || {})}
        >
          <div className="flex items-center space-x-1.5">
            <div
              className={`w-1.5 h-1.5 rounded-full ${
                usrp?.status === 'online'
                  ? 'bg-emerald-400 shadow-sm shadow-emerald-400'
                  : usrp?.status === 'busy'
                  ? 'bg-amber-400 shadow-sm shadow-amber-400'
                  : 'bg-zinc-600'
              }`}
            />
            <Cpu className="w-3.5 h-3.5 opacity-80" />
            <span className="text-zinc-400">USRP N310:</span>
          </div>
          <span className="font-semibold uppercase tracking-wider text-[11px]">
            {usrp?.status === 'online' ? '就绪' : usrp?.status === 'busy' ? '占用中' : '未连接'}
          </span>
        </div>

        {/* HackRF 硬件状态 */}
        <div
          className={`flex items-center space-x-2 px-2.5 py-1 rounded-md text-xs border font-mono transition-colors shadow-xs ${
            hackrf?.status === 'online'
              ? 'bg-emerald-950/30 text-emerald-300 border-emerald-700/60'
              : hackrf?.status === 'busy'
              ? 'bg-amber-950/40 text-amber-300 border-amber-600/70 animate-pulse'
              : 'bg-zinc-900/60 text-zinc-400 border-zinc-800'
          }`}
          title={hackrf?.error_message || JSON.stringify(hackrf?.details || {})}
        >
          <div className="flex items-center space-x-1.5">
            <div
              className={`w-1.5 h-1.5 rounded-full ${
                hackrf?.status === 'online'
                  ? 'bg-emerald-400 shadow-sm shadow-emerald-400'
                  : hackrf?.status === 'busy'
                  ? 'bg-amber-400 shadow-sm shadow-amber-400'
                  : 'bg-zinc-600'
              }`}
            />
            <Radio className="w-3.5 h-3.5 opacity-80" />
            <span className="text-zinc-400">HackRF:</span>
          </div>
          <span className="font-semibold uppercase tracking-wider text-[11px]">
            {hackrf?.status === 'online' ? '就绪' : hackrf?.status === 'busy' ? '占用中' : '未连接'}
          </span>
        </div>

        {/* 正在运行中的任务徽章 */}
        {activeRun && (
          <div className="flex items-center space-x-2 px-3 py-1 rounded-md bg-cyan-950/50 border border-cyan-500/50 text-cyan-200 text-xs font-mono shadow-sm shadow-cyan-500/20">
            <RefreshCw className="w-3.5 h-3.5 animate-spin text-cyan-400" />
            <span className="text-[11px] font-semibold text-cyan-300">
              任务执行中: <span className="text-white">{activeRun.command_id}</span>
            </span>
            <button
              onClick={() => api.stopRun(activeRun.id)}
              className="ml-1 text-red-300 hover:text-white flex items-center space-x-1 px-2 py-0.5 rounded bg-red-950/70 hover:bg-red-900/90 border border-red-700/70 transition-all text-[10px] font-bold"
              title="紧急停止当前任务"
            >
              <StopCircle className="w-3 h-3 text-red-400" />
              <span>停止</span>
            </button>
          </div>
        )}

        {/* WebSocket 连接状态 */}
        <div
          className={`flex items-center space-x-1.5 px-2.5 py-1 rounded-md text-[11px] font-mono border transition-colors ${
            connected
              ? 'text-emerald-400 border-emerald-800/60 bg-emerald-950/20 shadow-xs'
              : 'text-zinc-500 border-zinc-800 bg-zinc-900/40'
          }`}
          title={connected ? 'WebSocket 事件流实时同步已就绪' : 'WebSocket 断开，正在自动尝试重连'}
        >
          {connected ? (
            <div className="w-1.5 h-1.5 rounded-full bg-emerald-400 shadow-sm shadow-emerald-400 animate-pulse" />
          ) : (
            <WifiOff className="w-3 h-3 text-zinc-500" />
          )}
          <span className="font-semibold">{connected ? 'LIVE' : 'OFFLINE'}</span>
        </div>

        {/* 刷新硬件按钮 */}
        <button
          onClick={() => refetchDevices()}
          disabled={isFetching}
          className="p-1.5 rounded-md hover:bg-rf-panel text-zinc-400 hover:text-zinc-200 transition-colors border border-transparent hover:border-rf-border active:scale-95"
          title="手动探测硬件与刷新"
        >
          <RefreshCw className={`w-3.5 h-3.5 ${isFetching ? 'animate-spin text-cyan-400' : ''}`} />
        </button>
      </div>
    </header>
  );
};
