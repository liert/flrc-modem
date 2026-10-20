import React from 'react';
import { useQuery } from '@tanstack/react-query';
import {
  Cpu,
  Radio,
  Play,
  CheckCircle,
  XCircle,
  Clock,
  FileCode,
  ArrowRight,
  RefreshCw,
  HardDrive,
  Activity,
  Layers,
  Terminal,
  FileText,
  SlidersHorizontal,
} from 'lucide-react';
import { api } from '../api/client';
import { TemplateItem } from '../types';
import { useWorkstationStore } from '../stores/useWorkstationStore';
import { fileStateLabel } from '../utils/analysis';
import { runTitle, runTime } from '../utils/runs';

export const DashboardView: React.FC = () => {
  const {
    setActiveTab,
    setSelectedCommandId,
    setSelectedRunId,
    setSelectedArtifactPath,
    setSelectedTemplate,
  } = useWorkstationStore();

  const { data: devices } = useQuery({
    queryKey: ['devices'],
    queryFn: api.getDevices,
    refetchInterval: 5000,
  });

  const { data: runs, refetch: refetchRuns } = useQuery({
    queryKey: ['runs', 10, 0],
    queryFn: () => api.getRuns(10, 0),
    refetchInterval: 3000,
  });

  const { data: artifacts } = useQuery({
    queryKey: ['artifacts'],
    queryFn: api.getAllArtifacts,
    refetchInterval: 5000,
  });

  const { data: templates } = useQuery({
    queryKey: ['templates'],
    queryFn: () => api.getTemplates(),
  });

  const usrp = devices?.find((d) => d.device_type === 'usrp');
  const hackrf = devices?.find((d) => d.device_type === 'hackrf');

  const activeRun = runs?.find((r) => r.status === 'RUNNING');
  const failedRuns = runs?.filter((r) => r.status === 'FAILED') || [];
  const recentArtifacts = artifacts?.slice(0, 6) || [];

  const handleQuickLaunch = (cmdId: string) => {
    setSelectedCommandId(cmdId);
    setActiveTab('commands');
  };

  const handleTemplateLaunch = (tpl: TemplateItem) => {
    setSelectedTemplate(tpl);
    const targetCmd = tpl.applicable_commands[0] || 'demod';
    setSelectedCommandId(targetCmd);
    setActiveTab('commands');
  };

  const handleInspectRun = (runId: string) => {
    setSelectedRunId(runId);
    setActiveTab('tasks');
  };

  const handleAnalyzeArtifact = (path: string, runId: string) => {
    setSelectedRunId(runId);
    setSelectedArtifactPath(path);
    setActiveTab('signals');
  };

  return (
    <div className="p-6 space-y-6 max-w-7xl mx-auto">
      {/* 顶部状态网格 */}
      <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
        {/* USRP 状态卡片 */}
        <div className="bg-rf-surface border border-rf-border p-4 rounded-lg relative overflow-hidden">
          <div className="flex items-center justify-between text-xs text-rf-muted mb-2">
            <span className="flex items-center space-x-1.5 font-mono">
              <Cpu className="w-3.5 h-3.5 text-cyan-400" />
              <span>USRP N310 硬件</span>
            </span>
            <span
              className={`px-1.5 py-0.5 rounded text-[10px] font-bold uppercase font-mono ${
                usrp?.status === 'online'
                  ? 'bg-emerald-950 text-emerald-400 border border-emerald-800'
                  : usrp?.status === 'busy'
                  ? 'bg-amber-950 text-amber-400 border border-amber-800'
                  : 'bg-zinc-900 text-zinc-500 border border-zinc-800'
              }`}
            >
              {usrp?.status || 'OFFLINE'}
            </span>
          </div>
          <div className="text-base font-semibold text-rf-text truncate">
            {usrp?.status === 'online'
              ? usrp.name
              : usrp?.status === 'busy'
              ? 'USRP (任务运行独占)'
              : '未检测到设备 (离线)'}
          </div>
          <div className="text-[11px] text-zinc-500 font-mono mt-1 truncate">
            {usrp?.status === 'busy'
              ? `占用任务: ${usrp.locked_by_run_id}`
              : usrp?.error_message || (usrp?.details?.addr ? `IP: ${usrp.details.addr}` : 'UHD 接口待命')}
          </div>
          <div
            className={`absolute bottom-0 left-0 right-0 h-0.5 ${
              usrp?.status === 'online'
                ? 'bg-emerald-500'
                : usrp?.status === 'busy'
                ? 'bg-amber-500'
                : 'bg-zinc-700'
            }`}
          />
        </div>

        {/* HackRF 状态卡片 */}
        <div className="bg-rf-surface border border-rf-border p-4 rounded-lg relative overflow-hidden">
          <div className="flex items-center justify-between text-xs text-rf-muted mb-2">
            <span className="flex items-center space-x-1.5 font-mono">
              <Radio className="w-3.5 h-3.5 text-cyan-400" />
              <span>HackRF One</span>
            </span>
            <span
              className={`px-1.5 py-0.5 rounded text-[10px] font-bold uppercase font-mono ${
                hackrf?.status === 'online'
                  ? 'bg-emerald-950 text-emerald-400 border border-emerald-800'
                  : hackrf?.status === 'busy'
                  ? 'bg-amber-950 text-amber-400 border border-amber-800'
                  : 'bg-zinc-900 text-zinc-500 border border-zinc-800'
              }`}
            >
              {hackrf?.status || 'OFFLINE'}
            </span>
          </div>
          <div className="text-base font-semibold text-rf-text truncate">
            {hackrf?.status === 'online'
              ? 'HackRF One 就绪'
              : hackrf?.status === 'busy'
              ? 'HackRF (收发占用)'
              : '未连接 USB 硬件'}
          </div>
          <div className="text-[11px] text-zinc-500 font-mono mt-1 truncate">
            {hackrf?.error_message || (hackrf?.id ? `Serial: ${hackrf.id.slice(0, 16)}...` : '内置驱动就绪')}
          </div>
          <div
            className={`absolute bottom-0 left-0 right-0 h-0.5 ${
              hackrf?.status === 'online'
                ? 'bg-emerald-500'
                : hackrf?.status === 'busy'
                ? 'bg-amber-500'
                : 'bg-zinc-700'
            }`}
          />
        </div>

        {/* 当前活跃任务 */}
        <div className="bg-rf-surface border border-rf-border p-4 rounded-lg relative overflow-hidden">
          <div className="flex items-center justify-between text-xs text-rf-muted mb-2">
            <span className="flex items-center space-x-1.5 font-mono">
              <Activity className="w-3.5 h-3.5 text-cyan-400" />
              <span>当前活动进程</span>
            </span>
            {activeRun && (
              <span className="w-2 h-2 rounded-full bg-cyan-400 animate-ping" />
            )}
          </div>
          <div className="text-base font-semibold text-rf-text truncate font-mono">
            {activeRun ? activeRun.command_id : 'IDLE 空闲'}
          </div>
          <div className="text-[11px] text-zinc-500 font-mono mt-1 truncate">
            {activeRun ? `PID 活跃 | 任务 ${activeRun.id}` : '无排队或执行中的子任务'}
          </div>
          <div className="absolute bottom-0 left-0 right-0 h-0.5 bg-cyan-500" />
        </div>

        {/* 失败统计与告警 */}
        <div className="bg-rf-surface border border-rf-border p-4 rounded-lg relative overflow-hidden">
          <div className="flex items-center justify-between text-xs text-rf-muted mb-2">
            <span className="flex items-center space-x-1.5 font-mono">
              <XCircle className="w-3.5 h-3.5 text-red-400" />
              <span>异常与失败记录</span>
            </span>
            <span className="text-[10px] font-mono text-zinc-400">近 10 次</span>
          </div>
          <div className="text-base font-semibold text-rf-text font-mono">
            {failedRuns.length} <span className="text-xs font-normal text-zinc-500">次失败</span>
          </div>
          <div className="text-[11px] text-zinc-500 font-mono mt-1 truncate">
            {failedRuns[0] ? `最近: ${failedRuns[0].error || '未知错误'}` : '近期无任务异常退出'}
          </div>
          <div className="absolute bottom-0 left-0 right-0 h-0.5 bg-red-500" />
        </div>
      </div>

      {/* 常用命令快捷入口 */}
      <div className="space-y-3">
        <div className="flex items-center justify-between">
          <h2 className="text-xs font-bold uppercase tracking-wider text-rf-muted font-mono flex items-center space-x-2">
            <Terminal className="w-3.5 h-3.5 text-rf-accent" />
            <span>常用 Modem 指令快捷入口</span>
          </h2>
          <button
            onClick={() => setActiveTab('commands')}
            className="text-xs text-rf-accent hover:underline flex items-center space-x-1"
          >
            <span>完整命令库</span>
            <ArrowRight className="w-3 h-3" />
          </button>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-4 gap-3">
          <button
            onClick={() => handleQuickLaunch('modulate')}
            className="bg-rf-surface hover:bg-rf-panel border border-rf-border hover:border-cyan-500/50 p-4 rounded-lg text-left transition-all group"
          >
            <div className="flex items-center justify-between mb-2">
              <span className="text-xs font-semibold text-rf-text group-hover:text-rf-accent">
                信号调制 (Modulate)
              </span>
              <Play className="w-3.5 h-3.5 text-zinc-500 group-hover:text-rf-accent" />
            </div>
            <p className="text-[11px] text-zinc-500 line-clamp-2">
              离线将 6..127B 原始十六进制物理载荷封装变长硬件头、CRC24、卷积 FEC 并调制成 IQ 文件。
            </p>
          </button>

          <button
            onClick={() => handleQuickLaunch('demod')}
            className="bg-rf-surface hover:bg-rf-panel border border-rf-border hover:border-cyan-500/50 p-4 rounded-lg text-left transition-all group"
          >
            <div className="flex items-center justify-between mb-2">
              <span className="text-xs font-semibold text-rf-text group-hover:text-rf-accent">
                离线解调 (Demod)
              </span>
              <Play className="w-3.5 h-3.5 text-zinc-500 group-hover:text-rf-accent" />
            </div>
            <p className="text-[11px] text-zinc-500 line-clamp-2">
              读取交错 CS8/CU8/CF32 采样，执行多采样相位判决、前导相关并输出 CRC 通过的原始帧。
            </p>
          </button>

          <button
            onClick={() => handleQuickLaunch('rx')}
            className="bg-rf-surface hover:bg-rf-panel border border-rf-border hover:border-cyan-500/50 p-4 rounded-lg text-left transition-all group"
          >
            <div className="flex items-center justify-between mb-2">
              <span className="text-xs font-semibold text-rf-text group-hover:text-rf-accent">
                统一射频采集 (RX)
              </span>
              <Play className="w-3.5 h-3.5 text-zinc-500 group-hover:text-rf-accent" />
            </div>
            <p className="text-[11px] text-zinc-500 line-clamp-2">
              通过 USRP N310 (2.5MSps) 或 HackRF 进行指定时长及频点的空口 IQ 连续采样落盘。
            </p>
          </button>

          <button
            onClick={() => handleQuickLaunch('sync')}
            className="bg-rf-surface hover:bg-rf-panel border border-rf-border hover:border-cyan-500/50 p-4 rounded-lg text-left transition-all group"
          >
            <div className="flex items-center justify-between mb-2">
              <span className="text-xs font-semibold text-rf-text group-hover:text-rf-accent">
                FHSS+TDD 硬件同步 (Sync)
              </span>
              <Play className="w-3.5 h-3.5 text-zinc-500 group-hover:text-rf-accent" />
            </div>
            <p className="text-[11px] text-zinc-500 line-clamp-2">
              流式接入 USRP N310 硬件时间戳，在线估计时钟漂移与 20 频点跳频时隙，输出实时时间轴。
            </p>
          </button>
        </div>
      </div>

      {/* 业务与物理模板快速载入 (Templates) */}
      <div className="space-y-3">
        <div className="flex items-center justify-between">
          <h2 className="text-xs font-bold font-mono uppercase text-rf-text flex items-center space-x-2">
            <FileText className="w-3.5 h-3.5 text-cyan-400" />
            <span>业务与物理模板库 (Templates / 物理参数与协议报文)</span>
          </h2>
          <span className="text-[11px] text-zinc-500 font-mono">
            点击直接载入对应命令与参数
          </span>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
          {templates?.map((tpl) => (
            <div
              key={tpl.name}
              onClick={() => handleTemplateLaunch(tpl)}
              className="bg-rf-surface hover:bg-rf-panel border border-rf-border hover:border-cyan-500/50 p-3.5 rounded-lg text-left transition-all cursor-pointer group"
            >
              <div className="flex items-center justify-between mb-1.5">
                <span className="text-xs font-semibold text-rf-text group-hover:text-rf-accent flex items-center space-x-1.5 truncate">
                  {tpl.format === 'toml' ? (
                    <SlidersHorizontal className="w-3.5 h-3.5 text-cyan-400 shrink-0" />
                  ) : (
                    <FileCode className="w-3.5 h-3.5 text-amber-400 shrink-0" />
                  )}
                  <span className="truncate">{tpl.title}</span>
                </span>
                <span className="text-[9px] px-1.5 py-0.2 rounded bg-black/40 text-zinc-400 font-mono uppercase shrink-0">
                  {tpl.format}
                </span>
              </div>
              <p className="text-[11px] text-zinc-400 line-clamp-2 leading-relaxed">
                {tpl.description}
              </p>
              <div className="mt-2.5 flex items-center justify-between text-[10px] font-mono text-zinc-500">
                <span>{tpl.name}</span>
                <span className="text-cyan-400 group-hover:underline flex items-center space-x-0.5">
                  <span>载入配置</span>
                  <ArrowRight className="w-2.5 h-2.5" />
                </span>
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* 下方双列布局：最近任务与产物 */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* 最近执行任务 */}
        <div className="bg-rf-surface border border-rf-border rounded-lg overflow-hidden flex flex-col">
          <div className="p-3 border-b border-rf-border flex items-center justify-between bg-rf-panel/40">
            <h3 className="text-xs font-bold font-mono uppercase text-rf-text flex items-center space-x-2">
              <Clock className="w-3.5 h-3.5 text-cyan-400" />
              <span>最近任务历史 (Recent Runs)</span>
            </h3>
            <button
              onClick={() => setActiveTab('tasks')}
              className="text-[11px] text-rf-muted hover:text-rf-accent font-mono"
            >
              全部任务 →
            </button>
          </div>

          <div className="divide-y divide-rf-border/40 text-xs font-mono">
            {runs?.slice(0, 5).map((run) => (
              <div
                key={run.id}
                onClick={() => handleInspectRun(run.id)}
                className="p-3 hover:bg-rf-panel/60 cursor-pointer flex items-center justify-between transition-colors"
              >
                <div className="space-y-0.5">
                  <div className="flex items-center space-x-2">
                    <span
                      className={`w-2 h-2 rounded-full ${
                        run.status === 'SUCCEEDED'
                          ? 'bg-emerald-400'
                          : run.status === 'RUNNING'
                          ? 'bg-cyan-400 animate-pulse'
                          : run.status === 'FAILED'
                          ? 'bg-red-400'
                          : 'bg-zinc-500'
                      }`}
                    />
                    <span className="font-semibold text-rf-text">{runTitle(run)}</span>
                    <span className="text-[10px] text-zinc-500">{run.id}</span>
                  </div>
                  <div className="text-[11px] text-zinc-400">
                    {run.error ? (
                      <span className="text-red-400 truncate block max-w-xs">{run.error}</span>
                    ) : (
                      <span>退出码: {run.exit_code ?? '-'}</span>
                    )}
                  </div>
                </div>

                <div className="text-right text-[11px] text-zinc-500">
                  <div>{runTime(run.created_at)}</div>
                  <span
                    className={`px-1.5 py-0.5 rounded text-[9px] font-bold ${
                      run.status === 'SUCCEEDED'
                        ? 'bg-emerald-950 text-emerald-400'
                        : run.status === 'RUNNING'
                        ? 'bg-cyan-950 text-cyan-400'
                        : 'bg-red-950 text-red-400'
                    }`}
                  >
                    {run.status}
                  </span>
                </div>
              </div>
            ))}
            {!runs?.length && (
              <div className="p-8 text-center text-zinc-600 text-xs font-mono">暂无任务记录</div>
            )}
          </div>
        </div>

        {/* 最近生成产物 */}
        <div className="bg-rf-surface border border-rf-border rounded-lg overflow-hidden flex flex-col">
          <div className="p-3 border-b border-rf-border flex items-center justify-between bg-rf-panel/40">
            <h3 className="text-xs font-bold font-mono uppercase text-rf-text flex items-center space-x-2">
              <HardDrive className="w-3.5 h-3.5 text-cyan-400" />
              <span>最近文件产物 (Outputs & Artifacts)</span>
            </h3>
            <button
              onClick={() => setActiveTab('signals')}
              className="text-[11px] text-rf-muted hover:text-rf-accent font-mono"
            >
              信号分析台 →
            </button>
          </div>

          <div className="divide-y divide-rf-border/40 text-xs font-mono">
            {recentArtifacts.map((item) => (
              <div
                key={item.id}
                onClick={() => handleAnalyzeArtifact(item.path, item.run_id)}
                className="p-3 hover:bg-rf-panel/60 cursor-pointer flex items-center justify-between transition-colors group"
              >
                <div className="space-y-0.5">
                  <div className="flex items-center space-x-2 text-rf-text group-hover:text-rf-accent font-medium">
                    <FileCode className="w-3.5 h-3.5 text-zinc-400" />
                    <span>{item.path.split(/[\\/]/).pop()}</span>
                    <span className="text-[10px] px-1 rounded bg-rf-panel text-zinc-400">
                      {item.kind}
                    </span>
                    {item.file_state && !item.file_state.readable && <span className="text-red-400 text-[10px]">
                      {fileStateLabel(item.file_state)}
                    </span>}
                  </div>
                  <div className="text-[11px] text-zinc-500 truncate max-w-sm">
                    {item.path}
                  </div>
                </div>

                <div className="text-right text-[11px] text-zinc-500">
                  <div>
                    {item.metadata?.size_bytes
                      ? `${(item.metadata.size_bytes / 1024).toFixed(1)} KB`
                      : '-'}
                  </div>
                  <span className="text-cyan-400 group-hover:underline text-[10px]">分析 →</span>
                </div>
              </div>
            ))}
            {!recentArtifacts.length && (
              <div className="p-8 text-center text-zinc-600 text-xs font-mono">
                暂无产物，可通过调制或采集命令生成
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
};
