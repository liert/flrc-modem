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
  Zap,
  ShieldAlert,
  FolderGit2,
  ChevronRight,
  Radar,
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

  const handleQuickLaunch = (target: string) => {
    if (target === 'scan') {
      setActiveTab('scan');
    } else if (target === 'sync') {
      setActiveTab('sync');
    } else if (target === 'spectrum') {
      setActiveTab('spectrum');
    } else if (target === 'devices') {
      setActiveTab('devices');
    } else if (target === 'transmit') {
      setActiveTab('transmit');
    } else {
      setSelectedCommandId(target);
      setActiveTab('commands');
    }
  };

  const handleTemplateLaunch = (tpl: TemplateItem) => {
    setSelectedTemplate(tpl);
    if (tpl.category === 'duplex_plan') {
      setActiveTab('templates');
      return;
    }
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
    <div className="p-6 space-y-6 max-w-7xl mx-auto select-none">
      {/* 顶部硬件状态网格 */}
      <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
        {/* USRP 状态卡片 */}
        <div className="bg-rf-surface border border-rf-border p-4 rounded-xl relative overflow-hidden shadow-sm hover:border-rf-border/80 transition-all">
          <div className="flex items-center justify-between text-xs text-rf-muted mb-2 font-mono">
            <span className="flex items-center space-x-2 font-semibold text-zinc-300">
              <Cpu className="w-4 h-4 text-cyan-400" />
              <span>USRP N310 硬件</span>
            </span>
            <span
              className={`px-2 py-0.5 rounded text-[10px] font-bold uppercase font-mono shadow-sm ${
                usrp?.status === 'online'
                  ? 'bg-emerald-950/70 text-emerald-400 border border-emerald-800/80'
                  : usrp?.status === 'busy'
                  ? 'bg-amber-950/70 text-amber-300 border border-amber-800/80 animate-pulse'
                  : 'bg-zinc-900/80 text-zinc-500 border border-zinc-800'
              }`}
            >
              {usrp?.status || 'OFFLINE'}
            </span>
          </div>
          <div className="text-sm font-bold text-rf-text truncate mt-1">
            {usrp?.status === 'online'
              ? usrp.name
              : usrp?.status === 'busy'
              ? 'USRP (任务运行独占)'
              : '未检测到设备 (离线)'}
          </div>
          <div className="text-[11px] text-zinc-400 font-mono mt-1.5 truncate">
            {usrp?.status === 'busy'
              ? `占用任务: ${usrp.locked_by_run_id}`
              : usrp?.error_message || (usrp?.details?.addr ? `IP: ${usrp.details.addr}` : 'UHD 接口待命')}
          </div>
          <div
            className={`absolute bottom-0 left-0 right-0 h-1 ${
              usrp?.status === 'online'
                ? 'bg-emerald-500'
                : usrp?.status === 'busy'
                ? 'bg-amber-500'
                : 'bg-zinc-700'
            }`}
          />
        </div>

        {/* HackRF 状态卡片 */}
        <div className="bg-rf-surface border border-rf-border p-4 rounded-xl relative overflow-hidden shadow-sm hover:border-rf-border/80 transition-all">
          <div className="flex items-center justify-between text-xs text-rf-muted mb-2 font-mono">
            <span className="flex items-center space-x-2 font-semibold text-zinc-300">
              <Radio className="w-4 h-4 text-cyan-400" />
              <span>HackRF One</span>
            </span>
            <span
              className={`px-2 py-0.5 rounded text-[10px] font-bold uppercase font-mono shadow-sm ${
                hackrf?.status === 'online'
                  ? 'bg-emerald-950/70 text-emerald-400 border border-emerald-800/80'
                  : hackrf?.status === 'busy'
                  ? 'bg-amber-950/70 text-amber-300 border border-amber-800/80 animate-pulse'
                  : 'bg-zinc-900/80 text-zinc-500 border border-zinc-800'
              }`}
            >
              {hackrf?.status || 'OFFLINE'}
            </span>
          </div>
          <div className="text-sm font-bold text-rf-text truncate mt-1">
            {hackrf?.status === 'online'
              ? 'HackRF One 就绪'
              : hackrf?.status === 'busy'
              ? 'HackRF (收发占用)'
              : '未连接 USB 硬件'}
          </div>
          <div className="text-[11px] text-zinc-400 font-mono mt-1.5 truncate">
            {hackrf?.error_message || (hackrf?.id ? `Serial: ${hackrf.id.slice(0, 16)}...` : '内置驱动就绪')}
          </div>
          <div
            className={`absolute bottom-0 left-0 right-0 h-1 ${
              hackrf?.status === 'online'
                ? 'bg-emerald-500'
                : hackrf?.status === 'busy'
                ? 'bg-amber-500'
                : 'bg-zinc-700'
            }`}
          />
        </div>

        {/* 当前活跃任务 */}
        <div className="bg-rf-surface border border-rf-border p-4 rounded-xl relative overflow-hidden shadow-sm hover:border-rf-border/80 transition-all">
          <div className="flex items-center justify-between text-xs text-rf-muted mb-2 font-mono">
            <span className="flex items-center space-x-2 font-semibold text-zinc-300">
              <Activity className="w-4 h-4 text-cyan-400" />
              <span>当前活动进程</span>
            </span>
            {activeRun && (
              <span className="w-2.5 h-2.5 rounded-full bg-cyan-400 animate-ping" />
            )}
          </div>
          <div className="text-sm font-bold text-cyan-300 truncate font-mono mt-1">
            {activeRun ? activeRun.command_id : 'IDLE 空闲'}
          </div>
          <div className="text-[11px] text-zinc-400 font-mono mt-1.5 truncate">
            {activeRun ? `PID 活跃 | 任务 ${activeRun.id}` : '无排队或执行中的子任务'}
          </div>
          <div className="absolute bottom-0 left-0 right-0 h-1 bg-cyan-500" />
        </div>

        {/* 失败统计与告警 */}
        <div className="bg-rf-surface border border-rf-border p-4 rounded-xl relative overflow-hidden shadow-sm hover:border-rf-border/80 transition-all">
          <div className="flex items-center justify-between text-xs text-rf-muted mb-2 font-mono">
            <span className="flex items-center space-x-2 font-semibold text-zinc-300">
              <XCircle className="w-4 h-4 text-red-400" />
              <span>异常与失败记录</span>
            </span>
            <span className="text-[10px] font-mono text-zinc-400 bg-rf-panel px-1.5 py-0.2 rounded border border-rf-border/50">近 10 次</span>
          </div>
          <div className="text-sm font-bold text-rf-text font-mono mt-1">
            <span className={failedRuns.length > 0 ? 'text-red-400' : 'text-zinc-300'}>{failedRuns.length}</span>{' '}
            <span className="text-xs font-normal text-zinc-500 font-sans">次失败</span>
          </div>
          <div className="text-[11px] text-zinc-400 font-mono mt-1.5 truncate">
            {failedRuns[0] ? `最近: ${failedRuns[0].error || '未知错误'}` : '近期无任务异常退出'}
          </div>
          <div className={`absolute bottom-0 left-0 right-0 h-1 ${failedRuns.length > 0 ? 'bg-red-500' : 'bg-zinc-700'}`} />
        </div>
      </div>

      {/* 常用命令快捷入口 */}
      <div className="space-y-3">
        <div className="flex items-center justify-between">
          <h2 className="text-xs font-bold uppercase tracking-wider text-rf-muted font-mono flex items-center space-x-2">
            <Terminal className="w-4 h-4 text-cyan-400" />
            <span>常用 Modem 指令快捷入口</span>
          </h2>
          <button
            onClick={() => setActiveTab('commands')}
            className="text-xs text-cyan-400 hover:text-cyan-300 hover:underline flex items-center space-x-1 font-mono font-semibold"
          >
            <span>完整命令库</span>
            <ArrowRight className="w-3.5 h-3.5" />
          </button>
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3.5">
          <button
            onClick={() => handleQuickLaunch('modulate')}
            className="bg-rf-surface hover:bg-rf-panel/80 border border-rf-border hover:border-cyan-400/50 p-4 rounded-xl text-left transition-all group shadow-sm hover:shadow-cyan-500/10"
          >
            <div className="flex items-center justify-between mb-2">
              <span className="text-xs font-bold text-rf-text group-hover:text-cyan-300 font-mono flex items-center space-x-1.5">
                <Zap className="w-3.5 h-3.5 text-cyan-400 group-hover:scale-110 transition-transform" />
                <span>信号调制 (Modulate)</span>
              </span>
              <Play className="w-3.5 h-3.5 text-zinc-500 group-hover:text-cyan-400 group-hover:translate-x-0.5 transition-all" />
            </div>
            <p className="text-[11px] text-zinc-400 line-clamp-2 leading-relaxed">
              离线将 6..127B 原始物理载荷封装硬件头、CRC24 与卷积 FEC，生成标准 FLRC IQ 采样文件。
            </p>
          </button>

          <button
            onClick={() => handleQuickLaunch('demod')}
            className="bg-rf-surface hover:bg-rf-panel/80 border border-rf-border hover:border-cyan-400/50 p-4 rounded-xl text-left transition-all group shadow-sm hover:shadow-cyan-500/10"
          >
            <div className="flex items-center justify-between mb-2">
              <span className="text-xs font-bold text-rf-text group-hover:text-cyan-300 font-mono flex items-center space-x-1.5">
                <Radio className="w-3.5 h-3.5 text-cyan-400 group-hover:scale-110 transition-transform" />
                <span>离线解调 (Demod)</span>
              </span>
              <Play className="w-3.5 h-3.5 text-zinc-500 group-hover:text-cyan-400 group-hover:translate-x-0.5 transition-all" />
            </div>
            <p className="text-[11px] text-zinc-400 line-clamp-2 leading-relaxed">
              读取交错 CS8/CU8/CF32 采样，执行多采样相位判决、前导相关并输出 CRC 校验帧。
            </p>
          </button>

          <button
            onClick={() => handleQuickLaunch('scan')}
            className="bg-rf-surface hover:bg-rf-panel/80 border border-rf-border hover:border-cyan-400/50 p-4 rounded-xl text-left transition-all group shadow-sm hover:shadow-cyan-500/10"
          >
            <div className="flex items-center justify-between mb-2">
              <span className="text-xs font-bold text-rf-text group-hover:text-cyan-300 font-mono flex items-center space-x-1.5">
                <Radar className="w-3.5 h-3.5 text-emerald-400 group-hover:scale-110 transition-transform" />
                <span>2.4GHz 自动盲扫 (Scan)</span>
              </span>
              <Play className="w-3.5 h-3.5 text-zinc-500 group-hover:text-cyan-400 group-hover:translate-x-0.5 transition-all" />
            </div>
            <p className="text-[11px] text-zinc-400 line-clamp-2 leading-relaxed">
              全频段自适应能量检测、CFAR 门限识别、提取 FLRC 同步字与 2.4GHz 时序热力走势。
            </p>
          </button>

          <button
            onClick={() => handleQuickLaunch('sync')}
            className="bg-rf-surface hover:bg-rf-panel/80 border border-rf-border hover:border-cyan-400/50 p-4 rounded-xl text-left transition-all group shadow-sm hover:shadow-cyan-500/10"
          >
            <div className="flex items-center justify-between mb-2">
              <span className="text-xs font-bold text-rf-text group-hover:text-cyan-300 font-mono flex items-center space-x-1.5">
                <Clock className="w-3.5 h-3.5 text-cyan-400 group-hover:scale-110 transition-transform" />
                <span>FHSS+TDD 硬件同步 (Sync)</span>
              </span>
              <Play className="w-3.5 h-3.5 text-zinc-500 group-hover:text-cyan-400 group-hover:translate-x-0.5 transition-all" />
            </div>
            <p className="text-[11px] text-zinc-400 line-clamp-2 leading-relaxed">
              流式接入 USRP N310 硬件时间戳，在线估计时钟漂移与 20 频点跳频时隙，输出实时时间轴。
            </p>
          </button>

          <button
            onClick={() => handleQuickLaunch('duplex')}
            className="bg-rf-surface hover:bg-rf-panel/80 border border-rf-border hover:border-cyan-400/50 p-4 rounded-xl text-left transition-all group shadow-sm hover:shadow-cyan-500/10"
          >
            <div className="flex items-center justify-between mb-2">
              <span className="text-xs font-bold text-rf-text group-hover:text-cyan-300 font-mono flex items-center space-x-1.5">
                <Layers className="w-3.5 h-3.5 text-indigo-400 group-hover:scale-110 transition-transform" />
                <span>时隙双工收发 (Duplex)</span>
              </span>
              <Play className="w-3.5 h-3.5 text-zinc-500 group-hover:text-cyan-400 group-hover:translate-x-0.5 transition-all" />
            </div>
            <p className="text-[11px] text-zinc-400 line-clamp-2 leading-relaxed">
              USRP 锁定时隙后精准发送指令，计算往返时延 RTT，支持单次交互与多任务自动化编排。
            </p>
          </button>

          <button
            onClick={() => handleQuickLaunch('spectrum')}
            className="bg-rf-surface hover:bg-rf-panel/80 border border-rf-border hover:border-cyan-400/50 p-4 rounded-xl text-left transition-all group shadow-sm hover:shadow-cyan-500/10"
          >
            <div className="flex items-center justify-between mb-2">
              <span className="text-xs font-bold text-rf-text group-hover:text-cyan-300 font-mono flex items-center space-x-1.5">
                <Activity className="w-3.5 h-3.5 text-cyan-400 group-hover:scale-110 transition-transform" />
                <span>实时宽带频谱 (Spectrum)</span>
              </span>
              <Play className="w-3.5 h-3.5 text-zinc-500 group-hover:text-cyan-400 group-hover:translate-x-0.5 transition-all" />
            </div>
            <p className="text-[11px] text-zinc-400 line-clamp-2 leading-relaxed">
              实时频谱仪与瀑布图时空框选、断流红线标记、多层级协议逐层剖析抽屉。
            </p>
          </button>

          <button
            onClick={() => handleQuickLaunch('rx')}
            className="bg-rf-surface hover:bg-rf-panel/80 border border-rf-border hover:border-cyan-400/50 p-4 rounded-xl text-left transition-all group shadow-sm hover:shadow-cyan-500/10"
          >
            <div className="flex items-center justify-between mb-2">
              <span className="text-xs font-bold text-rf-text group-hover:text-cyan-300 font-mono flex items-center space-x-1.5">
                <Radio className="w-3.5 h-3.5 text-purple-400 group-hover:scale-110 transition-transform" />
                <span>统一射频采集 (RX)</span>
              </span>
              <Play className="w-3.5 h-3.5 text-zinc-500 group-hover:text-cyan-400 group-hover:translate-x-0.5 transition-all" />
            </div>
            <p className="text-[11px] text-zinc-400 line-clamp-2 leading-relaxed">
              通过 USRP N310 (2.5MSps) 或 HackRF 进行指定时长及频点的空口 IQ 连续采样落盘。
            </p>
          </button>

          <button
            onClick={() => handleQuickLaunch('devices')}
            className="bg-rf-surface hover:bg-rf-panel/80 border border-rf-border hover:border-cyan-400/50 p-4 rounded-xl text-left transition-all group shadow-sm hover:shadow-cyan-500/10"
          >
            <div className="flex items-center justify-between mb-2">
              <span className="text-xs font-bold text-rf-text group-hover:text-cyan-300 font-mono flex items-center space-x-1.5">
                <HardDrive className="w-3.5 h-3.5 text-amber-400 group-hover:scale-110 transition-transform" />
                <span>SDR 设备健康 (Devices)</span>
              </span>
              <Play className="w-3.5 h-3.5 text-zinc-500 group-hover:text-cyan-400 group-hover:translate-x-0.5 transition-all" />
            </div>
            <p className="text-[11px] text-zinc-400 line-clamp-2 leading-relaxed">
              USRP N310 与 HackRF 硬件探测、驱动可用性、参考时钟锁定与并发互斥锁账本。
            </p>
          </button>
        </div>
      </div>

      {/* 业务与物理模板快速载入 (Templates) */}
      <div className="space-y-3">
        <div className="flex items-center justify-between">
          <h2 className="text-xs font-bold font-mono uppercase text-rf-text flex items-center space-x-2">
            <FileText className="w-4 h-4 text-cyan-400" />
            <span>业务与物理模板库 (Templates / 物理参数与协议报文)</span>
          </h2>
          <button
            type="button"
            onClick={() => setActiveTab('templates')}
            className="text-[11px] text-cyan-400 hover:text-cyan-300 font-mono flex items-center space-x-1 transition-colors"
            title="前往模板预设库查看与编辑全部模板"
          >
            <span>管理全部模板 →</span>
          </button>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-3 gap-3.5">
          {templates?.map((tpl) => (
            <div
              key={tpl.name}
              onClick={() => handleTemplateLaunch(tpl)}
              className="bg-rf-surface hover:bg-rf-panel/80 border border-rf-border hover:border-cyan-400/50 p-4 rounded-xl text-left transition-all cursor-pointer group shadow-sm hover:shadow-cyan-500/10"
            >
              <div className="flex items-center justify-between mb-2">
                <span className="text-xs font-bold text-rf-text group-hover:text-cyan-300 flex items-center space-x-2 truncate font-mono">
                  {tpl.format === 'toml' ? (
                    <SlidersHorizontal className="w-4 h-4 text-cyan-400 shrink-0" />
                  ) : (
                    <FileCode className="w-4 h-4 text-amber-400 shrink-0" />
                  )}
                  <span className="truncate">{tpl.title}</span>
                </span>
                <span className={`text-[10px] px-1.5 py-0.2 rounded font-mono font-semibold uppercase shrink-0 ${
                  tpl.format === 'toml'
                    ? 'bg-cyan-950/70 text-cyan-300 border border-cyan-800/60'
                    : 'bg-amber-950/70 text-amber-300 border border-amber-800/60'
                }`}>
                  {tpl.format}
                </span>
              </div>
              <p className="text-[11px] text-zinc-400 line-clamp-2 leading-relaxed">
                {tpl.description}
              </p>
              <div className="mt-3 flex items-center justify-between text-[11px] font-mono text-zinc-500 pt-2 border-t border-rf-border/40">
                <span className="truncate max-w-[180px]">{tpl.name}</span>
                <span className="text-cyan-400 group-hover:text-cyan-300 group-hover:underline flex items-center space-x-1 font-semibold">
                  <span>载入配置</span>
                  <ArrowRight className="w-3 h-3 group-hover:translate-x-0.5 transition-transform" />
                </span>
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* 下方双列布局：最近任务与产物 */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">
        {/* 最近执行任务 */}
        <div className="bg-rf-surface border border-rf-border rounded-xl overflow-hidden flex flex-col shadow-sm">
          <div className="p-3.5 border-b border-rf-border flex items-center justify-between bg-rf-panel/40">
            <h3 className="text-xs font-bold font-mono uppercase text-rf-text flex items-center space-x-2">
              <Clock className="w-4 h-4 text-cyan-400" />
              <span>最近任务历史 (Recent Runs)</span>
            </h3>
            <button
              onClick={() => setActiveTab('tasks')}
              className="text-xs text-zinc-400 hover:text-cyan-300 font-mono flex items-center space-x-1 transition-colors"
            >
              <span>全部任务 →</span>
            </button>
          </div>

          <div className="divide-y divide-rf-border/30 text-xs font-mono">
            {runs?.slice(0, 5).map((run) => (
              <div
                key={run.id}
                onClick={() => handleInspectRun(run.id)}
                className="p-3.5 hover:bg-rf-panel/50 cursor-pointer flex items-center justify-between transition-colors"
              >
                <div className="space-y-1 min-w-0 pr-3">
                  <div className="flex items-center space-x-2">
                    <span
                      className={`w-2 h-2 rounded-full shrink-0 ${
                        run.status === 'SUCCEEDED'
                          ? 'bg-emerald-400 shadow-sm shadow-emerald-400'
                          : run.status === 'RUNNING'
                          ? 'bg-cyan-400 animate-pulse shadow-sm shadow-cyan-400'
                          : run.status === 'FAILED'
                          ? 'bg-red-400 shadow-sm shadow-red-400'
                          : 'bg-zinc-500'
                      }`}
                    />
                    <span className="font-bold text-rf-text truncate">{runTitle(run)}</span>
                    <span className="text-[10px] text-zinc-500 shrink-0">{run.id}</span>
                  </div>
                  <div className="text-[11px] text-zinc-400 pl-4 truncate">
                    {run.error ? (
                      <span className="text-red-400 truncate block max-w-sm">{run.error}</span>
                    ) : (
                      <span>退出码: {run.exit_code ?? '-'}</span>
                    )}
                  </div>
                </div>

                <div className="text-right text-[11px] text-zinc-400 shrink-0 space-y-1">
                  <div>{runTime(run.created_at)}</div>
                  <span
                    className={`px-1.5 py-0.2 rounded text-[10px] font-bold inline-block ${
                      run.status === 'SUCCEEDED'
                        ? 'bg-emerald-950/70 text-emerald-400 border border-emerald-800/80'
                        : run.status === 'RUNNING'
                        ? 'bg-cyan-950/70 text-cyan-400 border border-cyan-800/80 animate-pulse'
                        : 'bg-red-950/70 text-red-400 border border-red-800/80'
                    }`}
                  >
                    {run.status}
                  </span>
                </div>
              </div>
            ))}
            {!runs?.length && (
              <div className="p-10 text-center text-zinc-500 text-xs font-mono">暂无任务记录</div>
            )}
          </div>
        </div>

        {/* 最近生成产物 */}
        <div className="bg-rf-surface border border-rf-border rounded-xl overflow-hidden flex flex-col shadow-sm">
          <div className="p-3.5 border-b border-rf-border flex items-center justify-between bg-rf-panel/40">
            <h3 className="text-xs font-bold font-mono uppercase text-rf-text flex items-center space-x-2">
              <HardDrive className="w-4 h-4 text-cyan-400" />
              <span>最近文件产物 (Outputs & Artifacts)</span>
            </h3>
            <button
              onClick={() => setActiveTab('signals')}
              className="text-xs text-zinc-400 hover:text-cyan-300 font-mono flex items-center space-x-1 transition-colors"
            >
              <span>信号分析台 →</span>
            </button>
          </div>

          <div className="divide-y divide-rf-border/30 text-xs font-mono">
            {recentArtifacts.map((item) => (
              <div
                key={item.id}
                onClick={() => handleAnalyzeArtifact(item.path, item.run_id)}
                className="p-3.5 hover:bg-rf-panel/50 cursor-pointer flex items-center justify-between transition-colors group"
              >
                <div className="space-y-1 min-w-0 pr-3">
                  <div className="flex items-center space-x-2 text-rf-text group-hover:text-cyan-300 font-bold truncate">
                    <FileCode className="w-4 h-4 text-zinc-400 group-hover:text-cyan-400 shrink-0" />
                    <span className="truncate">{item.path.split(/[\\/]/).pop()}</span>
                    <span className="text-[10px] px-1.5 py-0.2 rounded bg-rf-panel text-zinc-400 border border-rf-border/50 uppercase shrink-0">
                      {item.kind}
                    </span>
                    {item.file_state && !item.file_state.readable && (
                      <span className="text-red-400 text-[10px] shrink-0">
                        {fileStateLabel(item.file_state)}
                      </span>
                    )}
                  </div>
                  <div className="text-[11px] text-zinc-500 truncate max-w-sm pl-6">
                    {item.path}
                  </div>
                </div>

                <div className="text-right text-[11px] text-zinc-400 shrink-0 space-y-1">
                  <div>
                    {item.metadata?.size_bytes
                      ? `${(item.metadata.size_bytes / 1024).toFixed(1)} KB`
                      : '-'}
                  </div>
                  <span className="text-cyan-400 group-hover:text-cyan-300 group-hover:underline text-[11px] font-semibold flex items-center justify-end space-x-0.5">
                    <span>分析</span>
                    <ChevronRight className="w-3 h-3 group-hover:translate-x-0.5 transition-transform" />
                  </span>
                </div>
              </div>
            ))}
            {!recentArtifacts.length && (
              <div className="p-10 text-center text-zinc-500 text-xs font-mono">
                暂无产物，可通过调制或采集命令生成
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
};
