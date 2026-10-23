import React, { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  Cpu,
  Radio,
  RotateCw,
  CheckCircle2,
  AlertCircle,
  XCircle,
  Clock,
  Layers,
  Activity,
  Copy,
  Check,
  Zap,
  ExternalLink,
  ShieldCheck,
  Lock,
  Compass,
  RadioTower,
  Sliders,
  Info,
  Download,
} from 'lucide-react';
import { api } from '../api/client';
import { DeviceInfo, DeviceStatus, DriverInfo } from '../types';
import { useWorkstationStore } from '../stores/useWorkstationStore';

export const DeviceHealthDashboard: React.FC = () => {
  const queryClient = useQueryClient();
  const setActiveTab = useWorkstationStore(state => state.setActiveTab);
  const [copiedCmd, setCopiedCmd] = useState<string | null>(null);

  // Fetch device diagnostics
  const { data: diag, isLoading, isRefetching, error: diagnosticsError } = useQuery({
    queryKey: ['deviceDiagnostics'],
    queryFn: api.getDeviceDiagnostics,
    refetchInterval: 5000,
  });

  // Force probe mutation
  const probeMutation = useMutation({
    mutationFn: api.probeDevices,
    onMutate: () => queryClient.cancelQueries({ queryKey: ['deviceDiagnostics'] }),
    onSuccess: (data) => {
      queryClient.setQueryData(['deviceDiagnostics'], data);
    },
  });

  const handleCopy = (cmd: string) => {
    navigator.clipboard.writeText(cmd);
    setCopiedCmd(cmd);
    setTimeout(() => setCopiedCmd(null), 2000);
  };

  const getStatusBadge = (status: DeviceStatus) => {
    switch (status) {
      case 'online':
        return {
          bg: 'bg-emerald-500/20 text-emerald-400 border-emerald-500/40',
          dot: 'bg-emerald-400',
          label: '在线就绪 (ONLINE)',
        };
      case 'busy':
        return {
          bg: 'bg-blue-500/20 text-blue-400 border-blue-500/40',
          dot: 'bg-blue-400 animate-pulse',
          label: '任务独占中 (BUSY)',
        };
      case 'error':
        return {
          bg: 'bg-rose-500/20 text-rose-400 border-rose-500/40',
          dot: 'bg-rose-400',
          label: '探测异常 (ERROR)',
        };
      case 'offline':
      default:
        return {
          bg: 'bg-zinc-800 text-zinc-400 border-zinc-700',
          dot: 'bg-zinc-500',
          label: '未连接 (OFFLINE)',
        };
    }
  };

  const usrpDevice = diag?.devices.find((d) => d.device_type === 'usrp');
  const hackrfDevice = diag?.devices.find((d) => d.device_type === 'hackrf');

  const onlineCount = diag?.devices.filter((d) => d.status === 'online').length || 0;
  const busyCount = diag?.devices.filter((d) => d.status === 'busy').length || 0;

  return (
    <div className="h-full overflow-y-auto overscroll-contain bg-rf-bg text-rf-text p-4 space-y-4">
      <div className="border-b border-rf-border pb-4">
        <div className="flex flex-wrap items-center gap-2">
          {diag?.timestamp && (
            <span className="text-xs font-mono text-zinc-500 hidden sm:inline">
              最近体检: {new Date(diag.timestamp).toLocaleTimeString()}
            </span>
          )}
          {diag && (
            <button
              type="button"
              onClick={() => {
                const blob = new Blob([JSON.stringify(diag, null, 2)], { type: 'application/json' });
                const url = URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.href = url;
                a.download = `sdr_diagnostics_${Date.now()}.json`;
                a.click();
                URL.revokeObjectURL(url);
              }}
              className="px-3.5 py-2 bg-rf-panel hover:bg-rf-border text-zinc-300 border border-rf-border rounded-lg text-xs font-semibold transition-all flex items-center gap-1.5 shadow"
              title="导出当前 SDR 硬件诊断与驱动状态报告"
            >
              <Download className="w-3.5 h-3.5 text-cyan-400" />
              <span>导出诊断报告</span>
            </button>
          )}
          <button
            type="button"
            disabled={probeMutation.isPending || isRefetching}
            onClick={() => probeMutation.mutate()}
            className="px-4 py-2 bg-cyan-500/15 hover:bg-cyan-500/25 disabled:opacity-50 border border-cyan-500/30 text-cyan-300 rounded-lg text-xs font-semibold transition-all flex items-center gap-2 shadow"
          >
            <RotateCw className={`w-3.5 h-3.5 ${probeMutation.isPending || isRefetching ? 'animate-spin' : ''}`} />
            <span>{probeMutation.isPending || isRefetching ? '正在探测...' : '重新探测设备'}</span>
          </button>
        </div>
      </div>

      {(diagnosticsError || probeMutation.error) && (
        <div role="alert" className="p-3 text-xs text-rose-300 rounded-lg border border-rose-500/30">
          设备诊断失败：{(probeMutation.error || diagnosticsError)?.message}，可点击“重新探测设备”重试。
        </div>
      )}

      {/* 核心指标状态横条 */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        {/* 在线设备 */}
        <div className="bg-rf-surface border border-rf-border rounded-xl p-3.5 flex flex-col justify-between">
          <div className="flex items-center justify-between text-xs text-zinc-400">
            <span>在线 SDR 设备</span>
            <Radio className="w-3.5 h-3.5 text-purple-400" />
          </div>
          <div className="mt-2 flex items-baseline gap-2">
            <span className="text-2xl font-bold text-zinc-100 font-mono">{onlineCount}</span>
            <span className="text-xs text-zinc-400">台就绪 ({busyCount} 占用中)</span>
          </div>
          <div className="text-[11px] text-zinc-500 mt-1">
            总检测通道: {diag?.devices.length || 0}
          </div>
        </div>

        {/* UHD 驱动状态 */}
        <div className="bg-rf-surface border border-rf-border rounded-xl p-3.5 flex flex-col justify-between">
          <div className="flex items-center justify-between text-xs text-zinc-400">
            <span>USRP (UHD) 驱动</span>
            <Cpu className="w-3.5 h-3.5 text-indigo-400" />
          </div>
          <div className="mt-2 flex items-center gap-2">
            {diag?.drivers.usrp?.available ? (
              <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium border bg-emerald-500/20 text-emerald-400 border-emerald-500/40">
                <span className="w-2 h-2 rounded-full bg-emerald-400" />
                UHD 就绪
              </span>
            ) : (
              <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium border bg-zinc-800 text-zinc-400 border-zinc-700">
                <span className="w-2 h-2 rounded-full bg-zinc-500" />
                未加载
              </span>
            )}
          </div>
          <div className="text-[11px] text-zinc-500 mt-1 font-mono truncate">
            {diag?.drivers.usrp?.version || '无本地 UHD.dll'}
          </div>
        </div>

        {/* HackRF 驱动状态 */}
        <div className="bg-rf-surface border border-rf-border rounded-xl p-3.5 flex flex-col justify-between">
          <div className="flex items-center justify-between text-xs text-zinc-400">
            <span>HackRF 驱动</span>
            <RadioTower className="w-3.5 h-3.5 text-cyan-400" />
          </div>
          <div className="mt-2 flex items-center gap-2">
            {diag?.drivers.hackrf?.available ? (
              <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium border bg-emerald-500/20 text-emerald-400 border-emerald-500/40">
                <span className="w-2 h-2 rounded-full bg-emerald-400" />
                libhackrf 正常
              </span>
            ) : (
              <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium border bg-rose-500/20 text-rose-400 border-rose-500/40">
                <span className="w-2 h-2 rounded-full bg-rose-400" />
                驱动异常
              </span>
            )}
          </div>
          <div className="text-[11px] text-zinc-500 mt-1 font-mono truncate">
            {diag?.drivers.hackrf?.release || '内置 DLL'}
          </div>
        </div>

        {/* 并发互斥锁状态 */}
        <div className="bg-rf-surface border border-rf-border rounded-xl p-3.5 flex flex-col justify-between">
          <div className="flex items-center justify-between text-xs text-zinc-400">
            <span>硬件资源锁状态</span>
            <Lock className="w-3.5 h-3.5 text-amber-400" />
          </div>
          <div className="mt-2 flex items-baseline gap-2">
            <span
              className={`text-2xl font-bold font-mono ${
                (diag?.active_locks.length || 0) > 0 ? 'text-blue-400' : 'text-emerald-400'
              }`}
            >
              {diag?.active_locks.length || 0}
            </span>
            <span className="text-xs text-zinc-400">个任务持有</span>
          </div>
          <div className="text-[11px] text-zinc-500 mt-1">
            {(diag?.active_locks.length || 0) === 0 ? '所有设备就绪' : '排他独占中'}
          </div>
        </div>
      </div>

      {/* 硬件设备专栏 1: USRP (N310 / B210) 专栏 */}
      <div className="bg-rf-surface border border-rf-border rounded-xl p-5 space-y-4 shadow-md">
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 border-b border-rf-border/60 pb-3">
          <div className="flex items-center gap-3">
            <div className="p-2 bg-indigo-500/20 border border-indigo-500/40 rounded-lg text-indigo-400">
              <Cpu className="w-5 h-5" />
            </div>
            <div>
              <div className="flex flex-wrap items-center gap-2">
                <h3 className="text-base font-semibold text-zinc-100">
                  USRP
                </h3>
                {usrpDevice && (
                  <span
                    className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-xs font-medium border ${
                      getStatusBadge(usrpDevice.status).bg
                    }`}
                  >
                    <span className={`w-1.5 h-1.5 rounded-full ${getStatusBadge(usrpDevice.status).dot}`} />
                    {getStatusBadge(usrpDevice.status).label}
                  </span>
                )}
              </div>
              <p className="text-xs text-zinc-400 mt-0.5">
                支持 USRP N310 四通道收发机、UHD Timed Commands 硬件跳频与 10MHz 外部参考
              </p>
            </div>
          </div>

          <div className="flex items-center gap-2 flex-wrap">
            {usrpDevice?.status === 'online' && (
              <button
                type="button"
                onClick={() => {
                  setActiveTab('sync');
                }}
                className="flex items-center gap-1.5 px-3 py-1.5 bg-indigo-950/60 hover:bg-indigo-900/60 text-indigo-300 text-xs rounded-lg border border-indigo-700/60 transition-colors shadow"
                title="前往时隙同步与双工工作台"
              >
                <Radio className="w-3.5 h-3.5" />
                <span>进入同步收发</span>
              </button>
            )}
            <button
              type="button"
              onClick={() => handleCopy('flrc usrp info --args addr=192.168.10.2')}
              className="flex items-center gap-1.5 px-3 py-1.5 bg-zinc-800 hover:bg-zinc-700 text-zinc-200 text-xs rounded-lg border border-zinc-700 transition-colors shadow"
            >
              {copiedCmd === 'flrc usrp info --args addr=192.168.10.2' ? (
                <Check className="w-3.5 h-3.5 text-emerald-400" />
              ) : (
                <Copy className="w-3.5 h-3.5" />
              )}
              <span>复制诊断命令</span>
            </button>
          </div>
        </div>

        {/* USRP 规格与特性配置 */}
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          {/* 主时钟频率 (MCR) 矩阵 */}
          <div className="bg-rf-bg border border-rf-border rounded-lg p-3.5 space-y-2">
            <div className="flex items-center justify-between text-xs font-semibold text-zinc-300">
              <span className="flex items-center gap-1.5">
                <Clock className="w-3.5 h-3.5 text-indigo-400" />
                <span>N310 主时钟 (MCR) 对照</span>
              </span>
              <span className="text-[10px] text-zinc-500 font-mono">整数整除建议</span>
            </div>
            <div className="space-y-1.5 text-xs font-mono">
              <div className="flex items-center justify-between p-2 rounded bg-zinc-900/60 border border-zinc-800/80">
                <span className="text-zinc-300">125.00 MHz (默认)</span>
                <span className="text-emerald-400 font-bold">2.50 MSps (÷50)</span>
              </div>
              <div className="flex items-center justify-between p-2 rounded bg-zinc-900/60 border border-zinc-800/80">
                <span className="text-zinc-300">122.88 MHz</span>
                <span className="text-cyan-400">1.92 MSps (÷64)</span>
              </div>
              <div className="flex items-center justify-between p-2 rounded bg-zinc-900/60 border border-zinc-800/80">
                <span className="text-zinc-300">153.60 MHz</span>
                <span className="text-cyan-400">2.40 MSps (÷64)</span>
              </div>
            </div>
          </div>

          {/* 射频天线通道与端口状态 */}
          <div className="bg-rf-bg border border-rf-border rounded-lg p-3.5 space-y-2">
            <div className="flex items-center justify-between text-xs font-semibold text-zinc-300">
              <span className="flex items-center gap-1.5">
                <Radio className="w-3.5 h-3.5 text-cyan-400" />
                <span>射频通道与天线端口</span>
              </span>
              <span className="text-[10px] text-zinc-500 font-mono">Channel 0 & 1</span>
            </div>
            <div className="space-y-2 text-xs">
              <div className="p-2 rounded bg-zinc-900/60 border border-zinc-800/80 space-y-1">
                <div className="flex items-center justify-between font-mono">
                  <span className="text-zinc-200 font-bold">Channel 0</span>
                  <span className="text-[11px] px-1.5 py-0.2 rounded bg-indigo-500/20 text-indigo-300 border border-indigo-500/30">
                    RX2 (接收)
                  </span>
                </div>
                <div className="text-[11px] text-zinc-400">TX/RX 端口可复用发射</div>
              </div>
              <div className="p-2 rounded bg-zinc-900/60 border border-zinc-800/80 space-y-1">
                <div className="flex items-center justify-between font-mono">
                  <span className="text-zinc-200 font-bold">Channel 1</span>
                  <span className="text-[11px] px-1.5 py-0.2 rounded bg-zinc-800 text-zinc-400 border border-zinc-700">
                    备用端口
                  </span>
                </div>
                <div className="text-[11px] text-zinc-500">双机/分集模式可用</div>
              </div>
            </div>
          </div>

          {/* 时钟源与时间源同步指示 */}
          <div className="bg-rf-bg border border-rf-border rounded-lg p-3.5 space-y-2">
            <div className="flex items-center justify-between text-xs font-semibold text-zinc-300">
              <span className="flex items-center gap-1.5">
                <Compass className="w-3.5 h-3.5 text-amber-400" />
                <span>参考时钟与时间源</span>
              </span>
            </div>
            <div className="space-y-2 text-xs">
              <div className="flex items-center justify-between p-2 rounded bg-zinc-900/60 border border-zinc-800/80">
                <span className="text-zinc-300">Internal (内部晶振)</span>
                <span className="text-xs text-emerald-400 font-mono font-semibold">就绪</span>
              </div>
              <div className="flex items-center justify-between p-2 rounded bg-zinc-900/60 border border-zinc-800/80">
                <span className="text-zinc-300">External 10MHz</span>
                <span className="text-xs text-zinc-500 font-mono">支持输入</span>
              </div>
              <div className="flex items-center justify-between p-2 rounded bg-zinc-900/60 border border-zinc-800/80">
                <span className="text-zinc-300">GPSDO / SFP0</span>
                <span className="text-xs text-zinc-500 font-mono">PPS 脉冲对齐</span>
              </div>
            </div>
          </div>
        </div>

        {usrpDevice?.error_message && (
          <div className="p-3 rounded-lg bg-zinc-900/80 border border-zinc-800 text-zinc-400 text-xs flex items-center gap-2">
            <Info className="w-4 h-4 text-zinc-500 flex-shrink-0" />
            <span>{usrpDevice.error_message}</span>
          </div>
        )}
      </div>

      {/* 硬件设备专栏 2: HackRF One 专栏 */}
      <div className="bg-rf-surface border border-rf-border rounded-xl p-5 space-y-4 shadow-md">
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 border-b border-rf-border/60 pb-3">
          <div className="flex items-center gap-3">
            <div className="p-2 bg-cyan-500/20 border border-cyan-500/40 rounded-lg text-cyan-400">
              <RadioTower className="w-5 h-5" />
            </div>
            <div>
              <div className="flex flex-wrap items-center gap-2">
                <h3 className="text-base font-semibold text-zinc-100">
                  HackRF One
                </h3>
                {hackrfDevice && (
                  <span
                    className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-xs font-medium border ${
                      getStatusBadge(hackrfDevice.status).bg
                    }`}
                  >
                    <span className={`w-1.5 h-1.5 rounded-full ${getStatusBadge(hackrfDevice.status).dot}`} />
                    {getStatusBadge(hackrfDevice.status).label}
                  </span>
                )}
              </div>
              <p className="text-xs text-zinc-400 mt-0.5">
                支持 1MHz ~ 6GHz 频段、USB 2.0 高速采样（2MSps / 10MSps / 20MSps）
              </p>
            </div>
          </div>

          <div className="flex items-center gap-2 flex-wrap">
            {hackrfDevice?.status === 'online' && (
              <>
                <button
                  type="button"
                  onClick={() => {
                    setActiveTab('scan');
                  }}
                  className="flex items-center gap-1.5 px-3 py-1.5 bg-cyan-950/60 hover:bg-cyan-900/60 text-cyan-300 text-xs rounded-lg border border-cyan-700/60 transition-colors shadow"
                  title="前往 2.4GHz 盲扫工作台"
                >
                  <Radio className="w-3.5 h-3.5" />
                  <span>进入盲扫</span>
                </button>
                <button
                  type="button"
                  onClick={() => {
                    setActiveTab('spectrum');
                  }}
                  className="flex items-center gap-1.5 px-3 py-1.5 bg-cyan-950/60 hover:bg-cyan-900/60 text-cyan-300 text-xs rounded-lg border border-cyan-700/60 transition-colors shadow"
                  title="前往实时频谱监视"
                >
                  <Activity className="w-3.5 h-3.5" />
                  <span>实时频谱</span>
                </button>
              </>
            )}
            <button
              type="button"
              onClick={() => handleCopy('flrc hackrf info')}
              className="flex items-center gap-1.5 px-3 py-1.5 bg-zinc-800 hover:bg-zinc-700 text-zinc-200 text-xs rounded-lg border border-zinc-700 transition-colors shadow"
            >
              {copiedCmd === 'flrc hackrf info' ? (
                <Check className="w-3.5 h-3.5 text-emerald-400" />
              ) : (
                <Copy className="w-3.5 h-3.5" />
              )}
              <span>复制 info 指令</span>
            </button>
          </div>
        </div>

        {/* HackRF 射频前端状态与特性 */}
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          {/* 射频前端特性 */}
          <div className="bg-rf-bg border border-rf-border rounded-lg p-3.5 space-y-2">
            <div className="flex items-center justify-between text-xs font-semibold text-zinc-300">
              <span className="flex items-center gap-1.5">
                <Zap className="w-3.5 h-3.5 text-cyan-400" />
                <span>射频前端增益与供电</span>
              </span>
            </div>
            <div className="space-y-1.5 text-xs font-mono">
              <div className="flex items-center justify-between p-2 rounded bg-zinc-900/60 border border-zinc-800/80">
                <span className="text-zinc-300">RF 内部放大器 (AMP)</span>
                <span className="text-cyan-400">+14 dB</span>
              </div>
              <div className="flex items-center justify-between p-2 rounded bg-zinc-900/60 border border-zinc-800/80">
                <span className="text-zinc-300">天线馈电供电 (Bias-Tee)</span>
                <span className="text-zinc-400">3.3V DC (默认关)</span>
              </div>
              <div className="flex items-center justify-between p-2 rounded bg-zinc-900/60 border border-zinc-800/80">
                <span className="text-zinc-300">USB 传输总线</span>
                <span className="text-emerald-400">HighSpeed 480Mbps</span>
              </div>
            </div>
          </div>

          {/* 模拟基带滤波器对照表 */}
          <div className="bg-rf-bg border border-rf-border rounded-lg p-3.5 space-y-2">
            <div className="flex items-center justify-between text-xs font-semibold text-zinc-300">
              <span className="flex items-center gap-1.5">
                <Sliders className="w-3.5 h-3.5 text-indigo-400" />
                <span>模拟基带滤波器 (Baseband BW)</span>
              </span>
            </div>
            <div className="grid grid-cols-3 gap-1.5 text-[11px] font-mono text-center">
              <div className="p-1.5 rounded bg-zinc-900/80 border border-zinc-800 text-zinc-300">1.75 MHz</div>
              <div className="p-1.5 rounded bg-zinc-900/80 border border-zinc-800 text-emerald-400 font-bold">2.50 MHz</div>
              <div className="p-1.5 rounded bg-zinc-900/80 border border-zinc-800 text-zinc-300">3.50 MHz</div>
              <div className="p-1.5 rounded bg-zinc-900/80 border border-zinc-800 text-zinc-300">5.00 MHz</div>
              <div className="p-1.5 rounded bg-zinc-900/80 border border-zinc-800 text-cyan-400 font-bold">10.0 MHz</div>
              <div className="p-1.5 rounded bg-zinc-900/80 border border-zinc-800 text-zinc-300">20.0 MHz</div>
            </div>
            <div className="text-[10px] text-zinc-500 mt-1">
              FLRC 码率自动对齐最窄基带滤波器以抑制镜像杂波
            </div>
          </div>

          {/* 设备序列号与固件信息 */}
          <div className="bg-rf-bg border border-rf-border rounded-lg p-3.5 space-y-2">
            <div className="flex items-center justify-between text-xs font-semibold text-zinc-300">
              <span className="flex items-center gap-1.5">
                <ShieldCheck className="w-3.5 h-3.5 text-emerald-400" />
                <span>固件与设备识别</span>
              </span>
            </div>
            <div className="space-y-2 text-xs font-mono">
              <div className="p-2 rounded bg-zinc-900/60 border border-zinc-800/80 space-y-1">
                <div className="text-zinc-400 text-[10px]">序列号 (Serial)</div>
                <div className="text-zinc-200 text-xs truncate">
                  {hackrfDevice?.id && hackrfDevice.id !== 'hackrf_none'
                    ? hackrfDevice.id
                    : '未检测到设备'}
                </div>
              </div>
              <div className="p-2 rounded bg-zinc-900/60 border border-zinc-800/80 space-y-1">
                <div className="text-zinc-400 text-[10px]">运行库 (libhackrf)</div>
                <div className="text-emerald-400 text-xs truncate">
                  {diag?.drivers.hackrf?.version || '项目内置 libhackrf.dll'}
                </div>
              </div>
            </div>
          </div>
        </div>

        {hackrfDevice?.error_message && (
          <div className="p-3 rounded-lg bg-zinc-900/80 border border-zinc-800 text-zinc-400 text-xs flex items-center gap-2">
            <Info className="w-4 h-4 text-zinc-500 flex-shrink-0" />
            <span>{hackrfDevice.error_message}</span>
          </div>
        )}
      </div>

      {/* 资源锁账本 (Lock Ledger) */}
      <div className="bg-rf-surface border border-rf-border rounded-xl p-5 space-y-3 shadow-md">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="flex items-center gap-2">
            <Lock className="w-4 h-4 text-amber-400" />
            <h3 className="text-sm font-semibold text-zinc-100">
              设备资源占用
            </h3>
          </div>
          <span className="text-xs text-zinc-500 font-mono">
            当前任务持有的设备锁
          </span>
        </div>

        {diag?.active_locks && diag.active_locks.length > 0 ? (
          <div className="space-y-2">
            {diag.active_locks.map((lock, idx) => (
              <div
                key={idx}
                className="p-3 rounded-lg bg-blue-950/40 border border-blue-500/40 flex flex-col sm:flex-row sm:items-center justify-between gap-2"
              >
                <div className="flex items-center gap-2">
                  <span className="w-2 h-2 rounded-full bg-blue-400 animate-pulse" />
                  <span className="font-mono text-xs font-bold text-blue-300">{lock.device}</span>
                  <span className="text-xs text-zinc-400">被任务占用</span>
                </div>
                <div className="flex items-center gap-4 text-xs font-mono text-zinc-400">
                  <span>任务 ID: {lock.run_id}</span>
                  <span>申请时间: {new Date(lock.acquired_at).toLocaleTimeString()}</span>
                </div>
              </div>
            ))}
          </div>
        ) : (
          <div className="p-4 rounded-lg bg-zinc-900/60 border border-zinc-800/80 text-zinc-400 text-xs flex flex-wrap items-center justify-between gap-3">
            <div className="flex items-center gap-2">
              <CheckCircle2 className="w-4 h-4 text-emerald-400" />
              <span>当前无任何任务锁定 SDR 设备，硬件资源完全空闲，可随时启动采集、扫描或发射。</span>
            </div>
            <button
              type="button"
              onClick={() => setActiveTab('spectrum')}
              className="text-xs text-cyan-400 hover:text-cyan-300 flex items-center gap-1 transition-colors"
            >
              <span>前往实时频谱</span>
              <ExternalLink className="w-3 h-3" />
            </button>
          </div>
        )}
      </div>
    </div>
  );
};

export default DeviceHealthDashboard;
