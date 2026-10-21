import React, { useState, useEffect, useRef, useMemo, useCallback } from 'react';
import { useQuery } from '@tanstack/react-query';
import ReactECharts from 'echarts-for-react/lib/core';
import * as echarts from 'echarts/core';
import { LineChart } from 'echarts/charts';
import {
  GridComponent,
  TooltipComponent,
  LegendComponent,
  MarkPointComponent,
  DataZoomComponent,
} from 'echarts/components';
import { CanvasRenderer } from 'echarts/renderers';
import {
  Play,
  Square,
  Pause,
  RotateCcw,
  Zap,
  Radio,
  Cpu,
  Layers,
  Sliders,
  AlertTriangle,
  Flame,
  Gauge,
  Activity,
  CheckCircle2,
} from 'lucide-react';
import { api } from '../api/client';
import { HardwareDeviceSelector } from '../components/HardwareDeviceSelector';
import { clockedSampleRateOptions, normalizeClockedSampleRate } from '../utils/sampleRates';
import { connectSpectrumStream } from '../utils/spectrumStream';
import {
  buildRealtimeSpectrumOption,
  renderWaterfallRow,
  waterfallTransition,
  fitSpectrumLevels,
} from '../utils/spectrumCharts';
import type {
  SpectrumFrame,
  SpectrumStartRequest,
  SpectrumStatusResponse,
  CommandDefinition,
} from '../types';

echarts.use([
  LineChart,
  GridComponent,
  TooltipComponent,
  LegendComponent,
  MarkPointComponent,
  DataZoomComponent,
  CanvasRenderer,
]);

const SignalChart = React.memo(ReactECharts);

const FFT_CHOICES = [256, 512, 1024, 2048, 4096, 8192];
const FPS_CHOICES = [10, 15, 20, 30];
const AVERAGE_CHOICES = [
  { val: 1, label: '无平均 (实时)' },
  { val: 2, label: '2 次平滑' },
  { val: 4, label: '4 次平滑 (推荐)' },
  { val: 8, label: '8 次平滑' },
  { val: 16, label: '16 次深度平滑' },
];

export const SpectrumView: React.FC = () => {
  // 1. 设备与预设查询
  const { data: devices } = useQuery({
    queryKey: ['devices'],
    queryFn: api.getDevices,
    refetchInterval: 5000,
  });

  // 2. 表单配置状态
  const [sdr, setSdr] = useState<'hackrf' | 'usrp' | 'sim'>('hackrf');
  const [centerFreqMhz, setCenterFreqMhz] = useState(2480.0);
  const [sampleRate, setSampleRate] = useState(8000000);
  const [nFft, setNFft] = useState(1024);
  const [windowFunc, setWindowFunc] = useState<'hann' | 'hamming' | 'blackman' | 'rectangular'>('hann');
  const [fftAverage, setFftAverage] = useState(4);
  const [fpsLimit, setFpsLimit] = useState(15);
  const [dynamicRangeDb, setDynamicRangeDb] = useState(80);
  const [refLevelDbfs, setRefLevelDbfs] = useState(0);
  const [autoLevel, setAutoLevel] = useState(true);
  const autoLevelPendingRef = useRef(true);

  // HackRF 参数
  const [lnaGain, setLnaGain] = useState(24);
  const [vgaGain, setVgaGain] = useState(28);
  const [amp, setAmp] = useState(false);
  const [biasTee, setBiasTee] = useState(false);
  const [serial, setSerial] = useState('');
  const [deviceIndex, setDeviceIndex] = useState<number | ''>('');

  // USRP 参数
  const [args, setArgs] = useState('');
  const [masterClockRate, setMasterClockRate] = useState<number>(125000000);
  const [gain, setGain] = useState(35);
  const [antenna, setAntenna] = useState('TX/RX');
  const [channel, setChannel] = useState(0);

  // 3. 运行控制与流状态
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [status, setStatus] = useState<SpectrumStatusResponse>({
    session_id: null,
    state: 'idle',
    device_type: null,
    device_id: null,
    device_name: null,
    actual_center_freq: null,
    actual_sample_rate: null,
    actual_master_clock: null,
    n_fft: 1024,
    rbw_hz: null,
    fps: 0,
    hw_overflows: 0,
    dsp_dropped_frames: 0,
    client_dropped_frames: 0,
    error_code: null,
    error_message: null,
  });

  const [isPaused, setIsPaused] = useState(false);
  const [peakHoldEnabled, setPeakHoldEnabled] = useState(false);
  const [peakHoldPowers, setPeakHoldPowers] = useState<number[] | null>(null);
  const [latestFrame, setLatestFrame] = useState<SpectrumFrame | null>(null);
  const [isStarting, setIsStarting] = useState(false);
  const [actionError, setActionError] = useState('');
  const [connectionError, setConnectionError] = useState('');
  const [activeRequest, setActiveRequest] = useState<SpectrumStartRequest | null>(null);
  const [waterfallNewest, setWaterfallNewest] = useState<number | null>(null);

  // 瀑布图引用
  const waterfallCanvasRef = useRef<HTMLCanvasElement | null>(null);
  const peakHoldRef = useRef<number[] | null>(null);
  peakHoldRef.current = peakHoldPowers;

  const lastFrameRef = useRef<SpectrumFrame | null>(null);
  const waterfallAdvanceRef = useRef(0);
  const mountedRef = useRef(true);
  const sessionIdRef = useRef(sessionId);
  sessionIdRef.current = sessionId;

  // 区分请求值与已生效值：若已在运行中且修改了需重启的参数，提示重启
  const requiresRestart =
    ['running', 'paused'].includes(status.state) && activeRequest !== null &&
    (sampleRate !== activeRequest.sample_rate || nFft !== activeRequest.n_fft || windowFunc !== activeRequest.window
      || sdr !== activeRequest.sdr || (sdr === 'usrp' && (masterClockRate !== activeRequest.master_clock_rate
        || channel !== activeRequest.channel || antenna !== activeRequest.antenna || args !== (activeRequest.args || '')))
      || (sdr === 'hackrf' && (serial !== (activeRequest.serial || '') || deviceIndex !== (activeRequest.device_index ?? '')
        || biasTee !== activeRequest.bias_tee)));

  // 物理设备选择对象绑定
  const hardwareSelectorParams = useMemo(() => {
    return {
      sdr,
      serial,
      device_index: deviceIndex,
      args,
    };
  }, [sdr, serial, deviceIndex, args]);

  const handleDeviceChange = (values: Record<string, any>) => {
    if (sdr === 'hackrf') {
      if (values.serial !== undefined) setSerial(values.serial);
      if (values.device_index !== undefined) setDeviceIndex(values.device_index);
    } else if (sdr === 'usrp') {
      if (values.args !== undefined) setArgs(values.args);
    }
  };

  // USRP 主时钟分频选项
  const usrpClockCommandDef: CommandDefinition = useMemo(() => {
    return {
      id: 'spectrum',
      name: 'SDR 实时频谱',
      category: '频谱',
      description: '实时频谱采集',
      subcommand: 'spectrum',
      requires_usrp: true,
      requires_sdr: true,
      parameters: [{ name: 'master_clock_rate', default: 125000000 } as any],
    };
  }, []);

  const clockedOptions = useMemo(() => {
    if (sdr !== 'usrp') return null;
    return clockedSampleRateOptions(usrpClockCommandDef, {
      sdr: 'usrp',
      master_clock_rate: masterClockRate,
      sample_rate: sampleRate,
    });
  }, [sdr, masterClockRate, sampleRate, usrpClockCommandDef]);

  useEffect(() => {
    if (sdr === 'usrp') {
      const normalized = normalizeClockedSampleRate(usrpClockCommandDef, { sdr, master_clock_rate: masterClockRate, sample_rate: sampleRate });
      if (normalized.sample_rate !== sampleRate) setSampleRate(normalized.sample_rate);
    } else if (sdr === 'hackrf' && (sampleRate < 2000000 || sampleRate > 20000000)) {
      setSampleRate(8000000);
    }
  }, [sdr, masterClockRate, sampleRate, usrpClockCommandDef]);

  // 4. WebSocket 实时数据消费与瀑布图渲染
  const onSpectrumFrameReceived = useCallback(
    (frame: SpectrumFrame) => {
      if (frame.session_id !== sessionIdRef.current) return;
      setStatus(previous => ({ ...previous, actual_center_freq: frame.center_freq, actual_sample_rate: frame.sample_rate,
        rbw_hz: frame.rbw_hz, fps: frame.fps, hw_overflows: frame.hw_overflows,
        dsp_dropped_frames: frame.dsp_dropped_frames, client_dropped_frames: frame.client_dropped_frames }));
      if (isPaused) return;

      setLatestFrame(frame);

      const transition = waterfallTransition(lastFrameRef.current, frame);
      let displayReference = refLevelDbfs;
      let displayRange = dynamicRangeDb;
      if (autoLevel && (autoLevelPendingRef.current || transition.reset)) {
        const levels = fitSpectrumLevels(frame.powers, dynamicRangeDb);
        if (levels) {
          displayReference = levels.refLevelDb;
          displayRange = levels.dynamicRangeDb;
          if (lastFrameRef.current) {
            const canvas = waterfallCanvasRef.current;
            canvas?.getContext('2d')?.clearRect(0, 0, canvas.width, canvas.height);
          }
          setRefLevelDbfs(displayReference);
          setDynamicRangeDb(displayRange);
          autoLevelPendingRef.current = false;
        }
      }
      lastFrameRef.current = frame;
      setWaterfallNewest(frame.timestamp);
      if (transition.reset) {
        waterfallAdvanceRef.current = 0;
        peakHoldRef.current = null;
        setPeakHoldPowers(null);
        const canvas = waterfallCanvasRef.current;
        canvas?.getContext('2d')?.clearRect(0, 0, canvas.width, canvas.height);
      }

      // 更新峰值保持
      if (peakHoldEnabled && frame.powers && frame.powers.length) {
        const prev = transition.reset ? null : peakHoldRef.current;
        const next = new Array(frame.powers.length);
        for (let i = 0; i < frame.powers.length; i++) {
          next[i] = prev && prev[i] !== undefined
            ? Math.max(prev[i], frame.powers[i])
            : frame.powers[i];
        }
        peakHoldRef.current = next;
        setPeakHoldPowers(next);
      }

      // 绘制瀑布图一行
      waterfallAdvanceRef.current += transition.advanceRows;
      const advanceRows = Math.floor(waterfallAdvanceRef.current + 1e-6);
      waterfallAdvanceRef.current -= advanceRows;
      if (waterfallCanvasRef.current && frame.powers && (advanceRows > 0 || transition.reset)) {
        renderWaterfallRow(
          waterfallCanvasRef.current,
          frame.powers,
          displayReference,
          displayRange,
          transition.gap,
          1,
          transition.reset ? 400 : advanceRows
        );
      }
    },
    [isPaused, peakHoldEnabled, refLevelDbfs, dynamicRangeDb, autoLevel]
  );

  const frameHandlerRef = useRef(onSpectrumFrameReceived);
  frameHandlerRef.current = onSpectrumFrameReceived;

  // 建立与管理 WebSocket 连接
  useEffect(() => {
    if (!sessionId) return;
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${protocol}//${window.location.host}/ws/spectrum${
      sessionId ? `?session_id=${encodeURIComponent(sessionId)}` : ''
    }`;

    return connectSpectrumStream({ sessionId, url: wsUrl,
      onFrame: frame => frameHandlerRef.current(frame),
      onStatus: info => { setStatus(info); setIsPaused(info.state === 'paused'); setConnectionError(''); },
      onConnectionError: setConnectionError });
  }, [sessionId]);

  // 组件卸载时安全释放后端采集会话
  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      if (sessionIdRef.current) {
        void api.stopSpectrum(sessionIdRef.current).catch(() => {});
      }
    };
  }, []);

  // 5. 控制动作实现
  const handleStart = async () => {
    if (isStarting) return;
    setIsStarting(true);
    setActionError('');
    try {
      const req: SpectrumStartRequest = {
        sdr,
        center_freq: centerFreqMhz * 1e6,
        sample_rate: Number(sampleRate),
        n_fft: nFft,
        window: windowFunc,
        fft_average: fftAverage,
        fps_limit: fpsLimit,
        dynamic_range_db: dynamicRangeDb,
        ref_level_dbfs: refLevelDbfs,
        // HackRF
        serial: sdr === 'hackrf' ? serial || undefined : undefined,
        device_index: sdr === 'hackrf' && typeof deviceIndex === 'number' ? deviceIndex : undefined,
        lna_gain: lnaGain,
        vga_gain: vgaGain,
        amp,
        bias_tee: biasTee,
        // USRP
        args: args || undefined,
        master_clock_rate: sdr === 'usrp' ? masterClockRate || undefined : undefined,
        gain,
        antenna,
        channel,
      };

      const res = await api.startSpectrum(req);
      if (!mountedRef.current) {
        if (res.session_id) await api.stopSpectrum(res.session_id);
        return;
      }
      sessionIdRef.current = res.session_id;
      setSessionId(res.session_id);
      setActiveRequest(req);
      setStatus(res);
      setIsPaused(false);
      setPeakHoldPowers(null);
      peakHoldRef.current = null;
      lastFrameRef.current = null;
      waterfallAdvanceRef.current = 0;
      setLatestFrame(null);
      setWaterfallNewest(null);
      autoLevelPendingRef.current = true;

      // 清空旧瀑布图画布并画起始分隔线
      if (waterfallCanvasRef.current) {
        const ctx = waterfallCanvasRef.current.getContext('2d');
        if (ctx) {
          ctx.clearRect(0, 0, waterfallCanvasRef.current.width, waterfallCanvasRef.current.height);
        }
      }
    } catch (err: any) {
      setStatus((prev) => ({
        ...prev,
        state: 'error',
        error_code: 'START_FAILED',
        error_message: err.message || '启动采集失败',
      }));
    } finally {
      setIsStarting(false);
    }
  };

  const handleStop = async () => {
    if (!sessionId && status.state === 'idle') return;
    try {
      const res = await api.stopSpectrum(sessionId || undefined);
      setStatus(res);
      setIsPaused(false);
      if (['stopped', 'idle'].includes(res.state)) {
        sessionIdRef.current = null;
        setSessionId(null);
      }
    } catch (err: any) {
      setActionError(`停止失败：${err.message}`);
    }
  };

  const handleTogglePause = async () => {
    if (!sessionId) return;
    try {
      const info = await (isPaused ? api.resumeSpectrum(sessionId) : api.pauseSpectrum(sessionId));
      setIsPaused(info.state === 'paused');
      setStatus(info);
      setActionError('');
    } catch (error: any) { setActionError(error.message); }
  };

  const handleClearWaterfall = () => {
    if (waterfallCanvasRef.current) {
      const ctx = waterfallCanvasRef.current.getContext('2d');
      if (ctx) {
        ctx.fillStyle = '#0d1117';
        ctx.fillRect(0, 0, waterfallCanvasRef.current.width, waterfallCanvasRef.current.height);
      }
    }
  };

  const handleResetPeakHold = () => {
    peakHoldRef.current = null;
    setPeakHoldPowers(null);
  };

  // 热调节频率与增益
  const handleApplyTuning = async () => {
    if (!sessionId || !['running', 'paused'].includes(status.state)) return;
    if (requiresRestart) { setActionError('设备、采样率、FFT 或窗函数已修改，请先重新启动采集。'); return; }
    try {
      const result = await api.updateSpectrum(sessionId, {
        center_freq: centerFreqMhz * 1e6,
        gain: sdr === 'usrp' ? gain : undefined,
        lna_gain: sdr === 'hackrf' ? lnaGain : undefined,
        vga_gain: sdr === 'hackrf' ? vgaGain : undefined,
        amp: sdr === 'hackrf' ? amp : undefined,
        fft_average: fftAverage,
        fps_limit: fpsLimit,
        dynamic_range_db: dynamicRangeDb,
        ref_level_dbfs: refLevelDbfs,
      });
      if (result.status) setStatus(result.status);
      if (result.restarted) autoLevelPendingRef.current = true;
      setActionError('');
    } catch (err: any) {
      setActionError(`应用参数失败：${err.message}`);
    }
  };

  // 6. 构造 ECharts Option
  const spectrumOption = useMemo(() => {
    const freqMin = latestFrame?.freq_min ?? (centerFreqMhz * 1e6 - sampleRate / 2);
    const freqMax = latestFrame?.freq_max ?? (centerFreqMhz * 1e6 + sampleRate / 2);
    const rbw = latestFrame?.rbw_hz ?? sampleRate / nFft;
    const powers = latestFrame?.powers ?? [];

    return buildRealtimeSpectrumOption({
      freqMin,
      freqMax,
      rbwHz: rbw,
      powers,
      peakHoldPowers: peakHoldEnabled ? peakHoldPowers : null,
      refLevelDb: refLevelDbfs,
      dynamicRangeDb,
      peakFreq: latestFrame?.peak_freq,
      peakPower: latestFrame?.peak_power,
    });
  }, [
    latestFrame,
    centerFreqMhz,
    sampleRate,
    nFft,
    peakHoldPowers,
    peakHoldEnabled,
    refLevelDbfs,
    dynamicRangeDb,
  ]);

  const activeDeviceLock = devices
    ?.find((d) => d.device_type === sdr)
    ?.details?.active_locks?.find((l: any) => l.run_id === sessionId);

  return (
    <div className="flex h-full w-full bg-rf-bg text-rf-text overflow-hidden select-none">
      {/* ======================================================== */}
      {/* 左侧控制栏：设备选择、RF 参数、增益及操作按钮 */}
      {/* ======================================================== */}
      <aside className="w-[21rem] shrink-0 bg-rf-surface border-r border-rf-border flex flex-col h-full overflow-y-auto">
        <div className="p-4 border-b border-rf-border flex items-center justify-between">
          <div className="flex items-center space-x-2">
            <Radio className="w-4 h-4 text-cyan-400" />
            <span className="font-bold text-sm tracking-wide uppercase font-mono">
              SDR 实时频谱控制
            </span>
          </div>
          <span
            className={`text-[10px] font-mono px-2 py-0.5 rounded-full uppercase border ${
              status.state === 'running'
                ? 'bg-emerald-950/40 text-emerald-300 border-emerald-700/60 animate-pulse'
                : status.state === 'paused'
                ? 'bg-amber-950/40 text-amber-300 border-amber-600/70'
                : status.state === 'error'
                ? 'bg-red-950/40 text-red-300 border-red-700/60'
                : 'bg-zinc-800/60 text-zinc-400 border-zinc-700'
            }`}
          >
            {status.state}
          </span>
        </div>

        <div className="p-4 space-y-5 text-xs flex-1">
          {/* A. 硬件选择与物理定位 */}
          <div className="space-y-3 bg-rf-bg/60 p-3 rounded-lg border border-rf-border/80">
            <div className="flex items-center justify-between font-semibold text-zinc-300">
              <span className="flex items-center gap-1.5">
                <Cpu className="w-3.5 h-3.5 text-cyan-400" />
                SDR 设备类型
              </span>
            </div>

            <div className="grid grid-cols-3 gap-1.5">
              {(['hackrf', 'usrp', 'sim'] as const).map((kind) => (
                <button
                  key={kind}
                  type="button"
                  onClick={() => setSdr(kind)}
                  className={`h-8 rounded-md font-mono font-bold text-xs uppercase border transition-colors ${
                    sdr === kind
                      ? 'bg-cyan-500/20 text-cyan-300 border-cyan-400/80 shadow-xs'
                      : 'bg-zinc-900/60 text-zinc-400 border-zinc-800 hover:text-zinc-200'
                  }`}
                >
                  {kind}
                </button>
              ))}
            </div>

            {/* 物理设备选择组件复用 */}
            {sdr !== 'sim' && (
              <HardwareDeviceSelector
                commandId="spectrum"
                parameters={hardwareSelectorParams}
                onChange={handleDeviceChange}
              />
            )}
          </div>

          {/* B. 射频与 FFT 公共参数 */}
          <div className="space-y-3">
            <div className="flex items-center justify-between text-zinc-300 font-semibold">
              <span className="flex items-center gap-1.5">
                <Sliders className="w-3.5 h-3.5 text-cyan-400" />
                射频与 FFT 参数
              </span>
              {status.state === 'running' && (
                <button
                  type="button"
                  onClick={handleApplyTuning}
                  className="text-[11px] text-cyan-400 hover:text-cyan-300 font-mono underline"
                >
                  应用频率/增益（重启接收）
                </button>
              )}
            </div>

            {/* 中心频率 */}
            <div className="space-y-1">
              <div className="flex justify-between text-zinc-400">
                <label htmlFor="spectrum-center-freq">中心频率 (MHz)</label>
                <span className="font-mono text-cyan-400">
                  {(centerFreqMhz * 1e6).toLocaleString()} Hz
                </span>
              </div>
              <input
                id="spectrum-center-freq"
                type="number"
                step="0.001"
                value={centerFreqMhz}
                onChange={(e) => setCenterFreqMhz(parseFloat(e.target.value) || 0)}
                className="w-full h-8 bg-rf-bg border border-rf-border rounded px-2.5 font-mono text-rf-text focus:outline-none focus:border-cyan-400"
              />
            </div>

            {/* 采样率 */}
            <div className="space-y-1">
              <div className="flex justify-between text-zinc-400">
                <label htmlFor="spectrum-sample-rate">采样率 (Sps)</label>
                <span className="font-mono text-zinc-400">
                  {(sampleRate / 1e6).toFixed(3)} MSps
                </span>
              </div>

              {sdr === 'usrp' && clockedOptions ? (
                <select
                  id="spectrum-sample-rate"
                  value={sampleRate}
                  onChange={(e) => setSampleRate(Number(e.target.value))}
                  className="w-full h-8 bg-rf-bg border border-rf-border rounded px-2 font-mono text-xs text-rf-text focus:outline-none focus:border-cyan-400"
                >
                  {clockedOptions.map((opt) => (
                    <option key={opt.value} value={opt.value}>
                      {opt.label}
                    </option>
                  ))}
                </select>
              ) : (
                <select
                  id="spectrum-sample-rate"
                  value={sampleRate}
                  onChange={(e) => setSampleRate(Number(e.target.value))}
                  className="w-full h-8 bg-rf-bg border border-rf-border rounded px-2 font-mono text-xs text-rf-text focus:outline-none focus:border-cyan-400"
                >
                  {[...new Set([sampleRate, 2000000, 4000000, 8000000, 10000000, 12500000, 16000000, 20000000])].map(
                    (r) => (
                      <option key={r} value={r}>
                        {(r / 1e6).toFixed(1)} MSps ({(r).toLocaleString()} Sps)
                      </option>
                    )
                  )}
                </select>
              )}
            </div>

            {/* FFT 点数与窗函数 */}
            <div className="grid grid-cols-2 gap-2">
              <div className="space-y-1">
                <label htmlFor="spectrum-n-fft" className="text-zinc-400">FFT 点数</label>
                <select
                  id="spectrum-n-fft"
                  value={nFft}
                  onChange={(e) => setNFft(Number(e.target.value))}
                  className="w-full h-8 bg-rf-bg border border-rf-border rounded px-2 font-mono text-xs text-rf-text focus:outline-none focus:border-cyan-400"
                >
                  {FFT_CHOICES.map((pts) => (
                    <option key={pts} value={pts}>
                      {pts} 点
                    </option>
                  ))}
                </select>
              </div>

              <div className="space-y-1">
                <label htmlFor="spectrum-window" className="text-zinc-400">窗函数</label>
                <select
                  id="spectrum-window"
                  value={windowFunc}
                  onChange={(e) => setWindowFunc(e.target.value as any)}
                  className="w-full h-8 bg-rf-bg border border-rf-border rounded px-2 font-mono text-xs text-rf-text focus:outline-none focus:border-cyan-400"
                >
                  <option value="hann">Hann 汉宁窗 (推荐)</option>
                  <option value="hamming">Hamming 海明窗</option>
                  <option value="blackman">Blackman 布莱克曼</option>
                  <option value="rectangular">Rectangular 矩形窗</option>
                </select>
              </div>
            </div>

            {/* 平滑平均与刷新率上限 */}
            <div className="grid grid-cols-2 gap-2">
              <div className="space-y-1">
                <label htmlFor="spectrum-average" className="text-zinc-400">频谱平滑平均</label>
                <select
                  id="spectrum-average"
                  value={fftAverage}
                  onChange={(e) => setFftAverage(Number(e.target.value))}
                  className="w-full h-8 bg-rf-bg border border-rf-border rounded px-2 font-mono text-xs text-rf-text focus:outline-none focus:border-cyan-400"
                >
                  {AVERAGE_CHOICES.map((opt) => (
                    <option key={opt.val} value={opt.val}>
                      {opt.label}
                    </option>
                  ))}
                </select>
              </div>

              <div className="space-y-1">
                <label htmlFor="spectrum-fps-limit" className="text-zinc-400">刷新率上限</label>
                <select
                  id="spectrum-fps-limit"
                  value={fpsLimit}
                  onChange={(e) => setFpsLimit(Number(e.target.value))}
                  className="w-full h-8 bg-rf-bg border border-rf-border rounded px-2 font-mono text-xs text-rf-text focus:outline-none focus:border-cyan-400"
                >
                  {FPS_CHOICES.map((fps) => (
                    <option key={fps} value={fps}>
                      {fps} FPS
                    </option>
                  ))}
                </select>
              </div>
            </div>

            {/* 显示动态范围与参考电平 */}
            <div className="grid grid-cols-2 gap-2">
              <div className="space-y-1">
                <label htmlFor="spectrum-ref-level" className="text-zinc-400">参考电平 (dBFS)</label>
                <input
                  id="spectrum-ref-level"
                  type="number"
                  value={refLevelDbfs}
                  min={-240}
                  max={20}
                  onChange={(e) => { setRefLevelDbfs(Number(e.target.value)); setAutoLevel(false); }}
                  className="w-full h-8 bg-rf-bg border border-rf-border rounded px-2 font-mono text-rf-text focus:outline-none focus:border-cyan-400"
                />
              </div>

              <div className="space-y-1">
                <label htmlFor="spectrum-dynamic-range" className="text-zinc-400">动态范围 (dB)</label>
                <input
                  id="spectrum-dynamic-range"
                  type="number"
                  value={dynamicRangeDb}
                  min={20}
                  max={140}
                  onChange={(e) => { setDynamicRangeDb(Number(e.target.value)); setAutoLevel(false); }}
                  className="w-full h-8 bg-rf-bg border border-rf-border rounded px-2 font-mono text-rf-text focus:outline-none focus:border-cyan-400"
                />
              </div>
            </div>
            <div className="flex items-center justify-between gap-2 text-zinc-400">
              <label className="flex items-center gap-2"><input type="checkbox" checked={autoLevel}
                onChange={event => { setAutoLevel(event.target.checked); autoLevelPendingRef.current = true; }} />首次采集自动匹配电平</label>
              <button type="button" className="text-cyan-400 hover:underline" disabled={!latestFrame}
                onClick={() => {
                  const levels = latestFrame && fitSpectrumLevels(latestFrame.powers, dynamicRangeDb);
                  if (levels) { setRefLevelDbfs(levels.refLevelDb); setDynamicRangeDb(levels.dynamicRangeDb); handleClearWaterfall(); }
                }}>匹配当前电平</button>
            </div>
          </div>

          {/* C. 设备专用参数面板 (HackRF vs USRP) */}
          {sdr === 'hackrf' && (
            <div className="space-y-3 bg-rf-bg/40 p-3 rounded-lg border border-rf-border/60">
              <span className="font-semibold text-zinc-300">HackRF 专用射频参数</span>

              <div className="space-y-1">
                <div className="flex justify-between text-zinc-400">
                  <label htmlFor="spectrum-lna-gain">LNA 增益 (0-40 dB)</label>
                  <span className="font-mono text-cyan-400">{lnaGain} dB</span>
                </div>
                <input
                  id="spectrum-lna-gain"
                  type="range"
                  min="0"
                  max="40"
                  step="8"
                  value={lnaGain}
                  onChange={(e) => setLnaGain(Number(e.target.value))}
                  className="w-full accent-cyan-400"
                />
              </div>

              <div className="space-y-1">
                <div className="flex justify-between text-zinc-400">
                  <label htmlFor="spectrum-vga-gain">VGA 增益 (0-62 dB)</label>
                  <span className="font-mono text-cyan-400">{vgaGain} dB</span>
                </div>
                <input
                  id="spectrum-vga-gain"
                  type="range"
                  min="0"
                  max="62"
                  step="2"
                  value={vgaGain}
                  onChange={(e) => setVgaGain(Number(e.target.value))}
                  className="w-full accent-cyan-400"
                />
              </div>

              <div className="grid grid-cols-2 gap-2 pt-1">
                <label className="flex items-center space-x-2 text-zinc-300 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={amp}
                    onChange={(e) => setAmp(e.target.checked)}
                    className="rounded bg-rf-bg border-rf-border text-cyan-500 focus:ring-0"
                  />
                  <span>RF 放大器 (+14dB)</span>
                </label>

                <label className="flex items-center space-x-2 text-zinc-300 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={biasTee}
                    onChange={(e) => setBiasTee(e.target.checked)}
                    className="rounded bg-rf-bg border-rf-border text-cyan-500 focus:ring-0"
                  />
                  <span>Bias-Tee 天线供电</span>
                </label>
              </div>
            </div>
          )}

          {sdr === 'usrp' && (
            <div className="space-y-3 bg-rf-bg/40 p-3 rounded-lg border border-rf-border/60">
              <span className="font-semibold text-zinc-300">USRP 专用射频参数</span>

              <div className="space-y-1">
                <label htmlFor="spectrum-usrp-mcr" className="text-zinc-400">主时钟频率 (MCR)</label>
                <select
                  id="spectrum-usrp-mcr"
                  value={masterClockRate}
                  onChange={(e) => setMasterClockRate(Number(e.target.value))}
                  className="w-full h-8 bg-rf-bg border border-rf-border rounded px-2 font-mono text-xs text-rf-text focus:outline-none focus:border-cyan-400"
                >
                  <option value={125000000}>125.000 MHz (常用千兆网)</option>
                  <option value={122880000}>122.880 MHz (LTE/5G 常见倍数)</option>
                  <option value={153600000}>153.600 MHz (宽带时钟)</option>
                </select>
              </div>

              <div className="space-y-1">
                <div className="flex justify-between text-zinc-400">
                  <label htmlFor="spectrum-usrp-gain">接收模拟增益 (dB)</label>
                  <span className="font-mono text-cyan-400">{gain} dB</span>
                </div>
                <input
                  id="spectrum-usrp-gain"
                  type="range"
                  min="0"
                  max="76"
                  step="1"
                  value={gain}
                  onChange={(e) => setGain(Number(e.target.value))}
                  className="w-full accent-cyan-400"
                />
              </div>

              <div className="grid grid-cols-2 gap-2">
                <div className="space-y-1">
                  <label htmlFor="spectrum-usrp-antenna" className="text-zinc-400">天线端口</label>
                  <select
                    id="spectrum-usrp-antenna"
                    value={antenna}
                    onChange={(e) => setAntenna(e.target.value)}
                    className="w-full h-8 bg-rf-bg border border-rf-border rounded px-2 font-mono text-xs text-rf-text"
                  >
                    <option value="TX/RX">TX/RX</option>
                    <option value="RX2">RX2</option>
                  </select>
                </div>

                <div className="space-y-1">
                  <label htmlFor="spectrum-usrp-channel" className="text-zinc-400">通道</label>
                  <input
                    id="spectrum-usrp-channel"
                    type="number"
                    min="0"
                    max="3"
                    value={channel}
                    onChange={(e) => setChannel(Number(e.target.value))}
                    className="w-full h-8 bg-rf-bg border border-rf-border rounded px-2 font-mono text-rf-text"
                  />
                </div>
              </div>
            </div>
          )}

          {/* D. 参数热重载警示 */}
          {requiresRestart && (
            <div className="p-2.5 rounded bg-amber-950/40 border border-amber-600/70 text-amber-300 text-xs flex items-center justify-between">
              <span className="flex items-center gap-1.5">
                <AlertTriangle className="w-3.5 h-3.5 shrink-0" />
                设备或采集参数已变更，需重启生效
              </span>
              <button
                type="button"
                disabled={isStarting}
                onClick={handleStart}
                className="px-2 py-1 rounded bg-amber-500 text-black font-bold text-[10px] uppercase hover:bg-amber-400"
              >
                立即重启
              </button>
            </div>
          )}
        </div>

        {/* 底部操作工具栏 */}
        <div className="p-4 border-t border-rf-border bg-rf-surface/95 space-y-2">
          {status.state !== 'running' && status.state !== 'paused' ? (
            <button
              type="button"
              disabled={isStarting}
              onClick={handleStart}
              className="w-full h-10 rounded-lg bg-gradient-to-r from-emerald-500 to-teal-600 hover:from-emerald-400 hover:to-teal-500 text-black font-bold font-mono text-xs uppercase shadow-md shadow-emerald-950/40 flex items-center justify-center space-x-2 transition-all active:scale-[0.98]"
            >
              <Play className="w-4 h-4 fill-current" />
              <span>{isStarting ? '正在初始化 SDR…' : '开始频谱采集'}</span>
            </button>
          ) : (
            <div className="grid grid-cols-2 gap-2">
              <button
                type="button"
                onClick={handleTogglePause}
                className={`h-10 rounded-lg font-bold font-mono text-xs uppercase flex items-center justify-center space-x-1.5 transition-all ${
                  isPaused
                    ? 'bg-amber-500 text-black hover:bg-amber-400 shadow-md shadow-amber-950/40'
                    : 'bg-zinc-800 text-amber-300 border border-amber-600/50 hover:bg-amber-950/30'
                }`}
              >
                {isPaused ? <Play className="w-3.5 h-3.5" /> : <Pause className="w-3.5 h-3.5" />}
                <span>{isPaused ? '恢复显示' : '暂停显示'}</span>
              </button>

              <button
                type="button"
                onClick={handleStop}
                className="h-10 rounded-lg bg-red-600 hover:bg-red-500 text-white font-bold font-mono text-xs uppercase shadow-md shadow-red-950/40 flex items-center justify-center space-x-1.5 transition-all active:scale-[0.98]"
              >
                <Square className="w-3.5 h-3.5 fill-current" />
                <span>停止采集</span>
              </button>
            </div>
          )}

          <div className="grid grid-cols-2 gap-2 pt-1">
            <button
              type="button"
              onClick={() => setPeakHoldEnabled(!peakHoldEnabled)}
              className={`h-8 rounded font-mono text-xs border flex items-center justify-center space-x-1.5 transition-colors ${
                peakHoldEnabled
                  ? 'bg-amber-500/20 text-amber-300 border-amber-400'
                  : 'bg-zinc-900 text-zinc-400 border-zinc-800 hover:text-zinc-200'
              }`}
            >
              <Flame className="w-3.5 h-3.5" />
              <span>峰值保持: {peakHoldEnabled ? '开' : '关'}</span>
            </button>

            <button
              type="button"
              onClick={handleClearWaterfall}
              className="h-8 rounded bg-zinc-900 border border-zinc-800 hover:border-zinc-700 text-zinc-400 hover:text-zinc-200 font-mono text-xs flex items-center justify-center space-x-1.5 transition-colors"
            >
              <RotateCcw className="w-3.5 h-3.5" />
              <span>清空瀑布图</span>
            </button>
          </div>
          <button type="button" onClick={handleResetPeakHold} className="text-xs text-amber-300 hover:underline">重置峰值保持</button>
        </div>
      </aside>

      {/* ======================================================== */}
      {/* 右侧主视口：状态度量、实时频谱图 (ECharts) 与瀑布图 Canvas */}
      {/* ======================================================== */}
      <main className="flex-1 min-w-0 flex flex-col h-full overflow-hidden bg-rf-bg">
        {/* 顶部运行状态与度量栏 */}
        <header className="min-h-12 border-b border-rf-border bg-rf-surface/80 px-4 py-2 flex flex-wrap gap-3 items-center justify-between text-xs font-mono shrink-0">
          <div className="flex flex-wrap gap-y-2 items-center space-x-4">
            <div className="flex items-center space-x-2">
              <span className="text-zinc-400">硬件设备:</span>
              <span className="text-cyan-300 font-bold">
                {status.device_name || sdr.toUpperCase()}
              </span>
              {status.device_id && (
                <span className="text-[11px] text-zinc-400 bg-zinc-900 px-1.5 py-0.5 rounded border border-zinc-800">
                  {status.device_id}
                </span>
              )}
            </div>

            <div className="flex items-center space-x-1.5 border-l border-zinc-800 pl-3">
              <span className="text-zinc-400">实际 RF 中心:</span>
              <span className="text-rf-text font-bold">
                {status.actual_center_freq
                  ? `${(status.actual_center_freq / 1e6).toFixed(4)} MHz`
                  : `${centerFreqMhz.toFixed(4)} MHz`}
              </span>
            </div>

            <div className="flex items-center space-x-1.5 border-l border-zinc-800 pl-3">
              <span className="text-zinc-400">实际采样率:</span>
              <span className="text-rf-text font-bold">
                {status.actual_sample_rate
                  ? `${(status.actual_sample_rate / 1e6).toFixed(3)} MSps`
                  : `${(sampleRate / 1e6).toFixed(3)} MSps`}
              </span>
            </div>

            {status.actual_master_clock && (
              <div className="flex items-center space-x-1.5 border-l border-zinc-800 pl-3">
                <span className="text-zinc-400">主时钟 MCR:</span>
                <span className="text-cyan-400 font-bold">
                  {(status.actual_master_clock / 1e6).toFixed(3)} MHz
                </span>
              </div>
            )}

            <div className="flex items-center space-x-1.5 border-l border-zinc-800 pl-3">
              <span className="text-zinc-400">RBW 分辨率:</span>
              <span className="text-emerald-400 font-bold">
                {status.rbw_hz
                  ? `${(status.rbw_hz / 1e3).toFixed(2)} kHz`
                  : `${(sampleRate / nFft / 1e3).toFixed(2)} kHz`}
              </span>
            </div>
          </div>

          <div className="flex items-center space-x-4">
            {latestFrame && (
              <div className="flex items-center space-x-1.5 bg-zinc-900/80 px-2 py-0.5 rounded border border-cyan-800/40">
                <span className="text-zinc-400">峰值:</span>
                <span className="text-cyan-300 font-bold">
                  {(latestFrame.peak_freq / 1e6).toFixed(3)} MHz
                </span>
                <span className="text-emerald-400 font-bold">
                  {latestFrame.peak_power.toFixed(1)} dBFS
                </span>
              </div>
            )}

            <div className="flex items-center space-x-3 text-zinc-400">
              <span>刷新率: <strong className="text-zinc-200">{latestFrame?.fps ?? status.fps} FPS</strong></span>
              <span>频谱帧: <strong className="text-zinc-200">{latestFrame?.sequence ?? 0}</strong></span>
              <span>溢出: <strong className={status.hw_overflows > 0 ? 'text-amber-400' : 'text-zinc-400'}>{status.hw_overflows}</strong></span>
              <span>丢帧: <strong className={status.client_dropped_frames > 0 ? 'text-amber-400' : 'text-zinc-400'}>{status.client_dropped_frames}</strong></span>
            </div>
          </div>
        </header>

        {(actionError || connectionError) && <p role="alert" className="px-4 py-2 text-xs text-amber-300 bg-amber-950/30">{actionError || connectionError}</p>}
        {/* 错误提示横幅 */}
        {status.state === 'error' && (
          <div className="bg-red-950/60 border-b border-red-700/80 px-4 py-2 text-xs text-red-300 flex items-center space-x-2">
            <AlertTriangle className="w-4 h-4 text-red-400 shrink-0" />
            <div className="flex-1">
              <strong>采集异常 [{status.error_code || 'ERROR'}]: </strong>
              <span>{status.error_message || '未知硬件或接收错误，请检查设备连接或释放冲突任务。'}</span>
            </div>
          </div>
        )}

        {/* 频谱与瀑布图上下等高视图 */}
        <div className="flex-1 flex flex-col min-h-0 overflow-hidden">
          {/* 上半部分：实时频谱图 (ECharts) */}
          <div className="flex-1 min-h-[220px] relative border-b border-rf-border">
            <div className="absolute top-2 left-4 z-10 text-[11px] font-mono text-zinc-400 pointer-events-none flex items-center gap-3">
              <span className="text-cyan-400 font-bold uppercase">实时频谱</span>
              <span>• 双边 FFT {activeRequest?.window || windowFunc} 加窗</span>
              <span>• 相干增益 S1 归一化</span>
              <span>• 相对功率 (dBFS)</span>
            </div>

            <SignalChart
              echarts={echarts}
              option={spectrumOption}
              style={{ height: '100%', width: '100%' }}
              notMerge={false}
              replaceMerge={['series']}
              lazyUpdate={true}
            />
            {!latestFrame && <p className="absolute inset-x-4 top-1/2 text-center text-sm text-zinc-400 pointer-events-none">
              {status.state === 'running' ? '等待频谱数据…' : '点击“开始频谱采集”显示频谱与瀑布图'}
            </p>}
          </div>

          {/* 下半部分：瀑布图 (Waterfall Canvas) */}
          <div className="flex-1 min-h-[220px] flex flex-col bg-[#0d1117] relative">
            <div className="h-6 px-4 flex items-center justify-between text-[11px] font-mono text-zinc-400 border-b border-zinc-800/80 bg-zinc-950/60">
              <span className="text-zinc-300 font-semibold uppercase">实时瀑布图 (Waterfall)</span>
              <div className="flex items-center space-x-4">
                <span>色阶范围: [{refLevelDbfs - dynamicRangeDb} ~ {refLevelDbfs} dBFS]</span>
                <span className="text-zinc-400">20 秒窗口 · {latestFrame?.timestamp_source === 'uhd' ? 'UHD 时间映射' : '主机估计时间'} · 断流留白</span>
              </div>
            </div>

            <div className="flex-1 relative min-h-0 overflow-hidden flex">
              <div className="w-[55px] shrink-0 flex flex-col justify-between py-1 text-[10px] font-mono text-zinc-400">
                {[0, 10, 20].map(age => <span key={age}>{waterfallNewest === null ? '—' : new Date((waterfallNewest - age) * 1000).toLocaleTimeString()}</span>)}
              </div>
              {/* 瀑布图画布 */}
              <canvas
                ref={waterfallCanvasRef}
                width={1200}
                height={400}
                className="flex-1 w-full h-full object-fill bg-[#0d1117]"
              />

              {/* 右侧色阶参考标尺 */}
              <div className="w-12 border-l border-zinc-800/80 flex flex-col justify-between items-center py-2 text-[9px] font-mono text-zinc-400 bg-zinc-950/40">
                <span className="text-red-400">{refLevelDbfs}</span>
                <div className="w-2.5 flex-1 my-1 rounded-sm bg-gradient-to-b from-[#e51f24] via-[#ffff00] via-[#00e5ff] to-[#0d1117] border border-zinc-700" />
                <span className="text-cyan-400">{refLevelDbfs - dynamicRangeDb}</span>
              </div>
            </div>
            <div className="flex justify-between pl-[55px] pr-12 text-[10px] font-mono text-zinc-400">
              <span>{((latestFrame?.freq_min ?? centerFreqMhz * 1e6 - sampleRate / 2) / 1e6).toFixed(3)} MHz</span>
              <span>{((latestFrame?.freq_max ?? centerFreqMhz * 1e6 + sampleRate / 2) / 1e6).toFixed(3)} MHz</span>
            </div>
          </div>
        </div>
      </main>
    </div>
  );
};
