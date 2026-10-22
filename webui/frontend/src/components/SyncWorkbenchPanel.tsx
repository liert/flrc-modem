import React, { useState, useEffect, useMemo, useRef } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  Clock,
  Play,
  Square,
  Copy,
  Check,
  RotateCw,
  Activity,
  Layers,
  Radio,
  Sliders,
  FileCode,
  Gauge,
  RadioTower,
  Cpu,
  ArrowRight,
  ExternalLink,
  ChevronDown,
  ChevronUp,
  Download,
  AlertCircle,
  HelpCircle,
  Timer,
} from 'lucide-react';
import { api } from '../api/client';
import {
  SyncLiveMetric,
  SyncSimulateRequest,
  SyncSimulateResponse,
  SyncParsedTimelineResponse,
} from '../types';
import { useWorkstationStore } from '../stores/useWorkstationStore';
import { useWebSocketStore } from '../stores/useWebSocketStore';

const DEFAULT_DK32S_HOPPING_TABLE = [
  2488460000, 2448460000, 2418060000, 2474860000, 2411660000,
  2420860000, 2445660000, 2431660000, 2460860000, 2500060000,
  2454860000, 2484060000, 2408860000, 2402460000, 2438060000,
  2427260000, 2464060000, 2471660000, 2477660000, 2440860000,
];

export const SyncWorkbenchPanel: React.FC = () => {
  const queryClient = useQueryClient();
  const { setActiveTab, setSpectrumTargetFreqHz, setSelectedRunId } = useWorkstationStore();
  const { latestEvent } = useWebSocketStore();

  // Mode: live SDR, simulation, playback
  const [mode, setMode] = useState<'live' | 'simulate' | 'playback'>('simulate');

  // Live SDR parameters
  const [sdr, setSdr] = useState<'usrp' | 'hackrf'>('usrp');
  const [usrpArgs, setUsrpArgs] = useState('addr=192.168.10.2');
  const [initialFreq, setInitialFreq] = useState(2471660000);
  const [sampleRate, setSampleRate] = useState(2500000);
  const [gain, setGain] = useState(30.0);
  const [antenna, setAntenna] = useState('RX2');
  const [protocol, setProtocol] = useState('dk32s');
  const [syncWord, setSyncWord] = useState('2bd36a07');
  const [timedHopping, setTimedHopping] = useState(false);
  const [duration, setDuration] = useState(0);
  const [outputPath, setOutputPath] = useState('outputs/sync_frames.jsonl');
  const [currentRunId, setCurrentRunId] = useState<string | null>(null);

  // Simulation parameters
  const [dwellMs, setDwellMs] = useState(50.0);
  const [codingRate, setCodingRate] = useState<'1/2' | '3/4' | '1'>('3/4');
  const [payloadBytes, setPayloadBytes] = useState(33);
  const [simDriftPpm, setSimDriftPpm] = useState(12.5);
  const [simCycles, setSimCycles] = useState(2);
  const [simData, setSimData] = useState<SyncSimulateResponse | null>(null);
  const [simStepIndex, setSimStepIndex] = useState(0);
  const [simPlaying, setSimPlaying] = useState(false);

  // Playback parameters
  const [playbackFile, setPlaybackFile] = useState('outputs/sync_frames.jsonl');
  const [parsedTimeline, setParsedTimeline] = useState<SyncParsedTimelineResponse | null>(null);

  // Live state tracking
  const [liveState, setLiveState] = useState<SyncLiveMetric>({
    time_s: 0,
    state: 'searching',
    confidence: 0,
    slot_index: 0,
    frequency_hz: DEFAULT_DK32S_HOPPING_TABLE[0],
    next_slot_index: 1,
    next_frequency_hz: DEFAULT_DK32S_HOPPING_TABLE[1],
    slot_remaining_s: 0.050,
    tdd_phase: 'ground_tx',
    clock_drift_ppm: 0,
    decoded_frames: 0,
    overflow_count: 0,
    anchor_count: 0,
    lock_duration_s: 0,
  });

  const [driftHistory, setDriftHistory] = useState<Array<{ time: number; drift: number }>>([]);
  const [recentFrames, setRecentFrames] = useState<any[]>([]);
  const [copied, setCopied] = useState(false);
  const [showConfig, setShowConfig] = useState(true);

  // Available hopping table (DK32S default or from simulation/playback)
  const hoppingTable = useMemo(() => {
    if (simData?.frequencies && simData.frequencies.length > 0) {
      return simData.frequencies;
    }
    return DEFAULT_DK32S_HOPPING_TABLE;
  }, [simData]);

  // Query runs to select for playback
  const { data: runsList } = useQuery({
    queryKey: ['runs', 20, 0],
    queryFn: () => api.getRuns(20, 0),
    refetchInterval: 3000,
  });

  const syncRuns = useMemo(() => {
    return runsList?.filter((r) => r.command_id === 'sync') || [];
  }, [runsList]);

  // Initial simulation run on mount
  useEffect(() => {
    void handleRunSimulation();
  }, []);

  // Handle WebSocket events for live sync
  useEffect(() => {
    if (!latestEvent || mode !== 'live') return;
    if (currentRunId && latestEvent.run_id !== currentRunId) return;

    if (latestEvent.type === 'metric' && latestEvent.payload) {
      const p = latestEvent.payload;
      if (p.state !== undefined || p.clock_drift_ppm !== undefined) {
        setLiveState((prev) => ({
          time_s: p.time_s ?? prev.time_s,
          state: (p.state as any) ?? prev.state,
          confidence: p.confidence ?? prev.confidence,
          slot_index: p.slot_index ?? prev.slot_index,
          frequency_hz: p.frequency_hz ?? prev.frequency_hz,
          next_slot_index: p.next_slot_index ?? ((p.slot_index + 1) % hoppingTable.length),
          next_frequency_hz: p.next_frequency_hz ?? hoppingTable[(p.slot_index + 1) % hoppingTable.length],
          slot_remaining_s: p.slot_remaining_s ?? prev.slot_remaining_s,
          tdd_phase: p.tdd_phase ?? prev.tdd_phase,
          clock_drift_ppm: p.clock_drift_ppm ?? prev.clock_drift_ppm,
          decoded_frames: p.decoded_frames ?? prev.decoded_frames,
          overflow_count: p.overflow_count ?? prev.overflow_count,
          anchor_count: p.anchor_count ?? prev.anchor_count,
          lock_duration_s: p.lock_duration_s ?? prev.lock_duration_s,
        }));

        if (p.clock_drift_ppm !== undefined) {
          setDriftHistory((prev) => [
            ...prev.slice(-40),
            { time: p.time_s ?? Date.now() / 1000, drift: p.clock_drift_ppm },
          ]);
        }
      }
    } else if (latestEvent.type === 'task.state') {
      const state = latestEvent.payload?.state;
      if (state === 'SUCCEEDED' || state === 'FAILED' || state === 'CANCELLED') {
        setCurrentRunId(null);
      }
    }
  }, [latestEvent, mode, currentRunId, hoppingTable]);

  // Simulation step timer
  useEffect(() => {
    if (!simPlaying || !simData?.simulated_slots?.length) return;
    const interval = setInterval(() => {
      setSimStepIndex((prev) => {
        const next = (prev + 1) % simData.simulated_slots.length;
        const cur = simData.simulated_slots[next];
        setLiveState({
          time_s: cur.start_time_s,
          state: cur.state as any,
          confidence: cur.confidence,
          slot_index: cur.slot_index,
          frequency_hz: cur.frequency_hz,
          next_slot_index: cur.next_slot_index,
          next_frequency_hz: cur.next_frequency_hz,
          slot_remaining_s: cur.duration_s,
          tdd_phase: 'ground_tx',
          clock_drift_ppm: cur.clock_drift_ppm,
          decoded_frames: next + 1,
          overflow_count: 0,
          anchor_count: Math.min(next + 1, 20),
          lock_duration_s: cur.end_time_s,
        });
        setDriftHistory((hist) => [
          ...hist.slice(-40),
          { time: cur.start_time_s, drift: cur.clock_drift_ppm + (Math.random() * 0.4 - 0.2) },
        ]);
        return next;
      });
    }, Math.max(50, dwellMs));

    return () => clearInterval(interval);
  }, [simPlaying, simData, dwellMs]);

  // Simulation execution handler
  const handleRunSimulation = async () => {
    try {
      const res = await api.simulateSyncSchedule({
        frequencies: DEFAULT_DK32S_HOPPING_TABLE,
        dwell_ms: dwellMs,
        tdd_mode: 'reception_triggered',
        payload_bytes: payloadBytes,
        bit_rate: 325000,
        coding_rate: codingRate,
        clock_drift_ppm: simDriftPpm,
        cycles: simCycles,
      });
      setSimData(res);
      setSimStepIndex(0);
      if (res.simulated_slots.length > 0) {
        const first = res.simulated_slots[0];
        setLiveState({
          time_s: first.start_time_s,
          state: first.state as any,
          confidence: first.confidence,
          slot_index: first.slot_index,
          frequency_hz: first.frequency_hz,
          next_slot_index: first.next_slot_index,
          next_frequency_hz: first.next_frequency_hz,
          slot_remaining_s: first.duration_s,
          tdd_phase: 'ground_tx',
          clock_drift_ppm: first.clock_drift_ppm,
          decoded_frames: 0,
          overflow_count: 0,
          anchor_count: 1,
          lock_duration_s: 0,
        });
      }
    } catch (err: any) {
      console.error('仿真失败:', err);
    }
  };

  // Playback execution handler
  const handleParsePlayback = async (runId?: string, filePath?: string) => {
    try {
      const res = await api.parseSyncTimeline({
        run_id: runId,
        file_path: filePath || playbackFile,
      });
      setParsedTimeline(res);
      if (res.latest_snapshot) {
        const snap = res.latest_snapshot;
        setLiveState({
          time_s: snap.hw_time_s || 0,
          state: snap.state || 'locked',
          confidence: snap.confidence || 0.98,
          slot_index: snap.slot_index || 0,
          frequency_hz: snap.frequency_hz || DEFAULT_DK32S_HOPPING_TABLE[0],
          next_slot_index: snap.next_slot_index || 1,
          next_frequency_hz: snap.next_frequency_hz || DEFAULT_DK32S_HOPPING_TABLE[1],
          slot_remaining_s: snap.slot_remaining_s || 0.05,
          tdd_phase: snap.tdd_phase || 'ground_tx',
          clock_drift_ppm: snap.clock_drift_ppm || 0,
          decoded_frames: res.total_frames,
          overflow_count: 0,
          anchor_count: snap.anchor_count || 10,
          lock_duration_s: snap.lock_duration_s || 1.0,
        });
      }
      if (res.drift_series?.length) {
        setDriftHistory(res.drift_series.map((d) => ({ time: d.time_s, drift: d.drift_ppm })));
      }
      if (res.frames?.length) {
        setRecentFrames(res.frames);
      }
    } catch (err: any) {
      alert(`解析失败: ${err.message}`);
    }
  };

  // Start Live Sync Run
  const handleStartLiveSync = async () => {
    try {
      const params: Record<string, any> = {
        sdr,
        protocol,
        output: outputPath,
      };
      if (sdr === 'usrp') {
        params.args = usrpArgs;
        params.antenna = antenna;
        if (timedHopping) params.timed_hopping = true;
      }
      if (initialFreq) params.frequency = initialFreq;
      if (gain !== undefined) params.gain = gain;
      if (sampleRate) params.sample_rate = sampleRate;
      if (syncWord) params.sync = syncWord;
      if (duration > 0) params.duration = duration;

      const run = await api.createRun(
        'sync',
        params,
        true,
        `Live FHSS+TDD Sync (${sdr.toUpperCase()})`
      );
      setCurrentRunId(run.id);
      setSelectedRunId(run.id);
    } catch (err: any) {
      alert(`启动同步失败: ${err.message}`);
    }
  };

  // Stop Live Sync Run
  const handleStopLiveSync = async () => {
    if (!currentRunId) return;
    try {
      await api.stopRun(currentRunId);
      setCurrentRunId(null);
    } catch (err: any) {
      alert(`停止失败: ${err.message}`);
    }
  };

  // Dynamic CLI command string
  const cliCommand = useMemo(() => {
    const parts = ['flrc', 'sync'];
    parts.push(`--sdr ${sdr}`);
    if (sdr === 'usrp') {
      if (usrpArgs) parts.push(`--args "${usrpArgs}"`);
      if (antenna) parts.push(`--antenna ${antenna}`);
      if (timedHopping) parts.push('--timed-hopping');
    }
    if (initialFreq) parts.push(`--frequency ${initialFreq}`);
    if (gain !== undefined) parts.push(`--gain ${gain}`);
    if (protocol) parts.push(`--protocol ${protocol}`);
    if (syncWord) parts.push(`--sync ${syncWord}`);
    if (duration > 0) parts.push(`--duration ${duration}`);
    if (outputPath) parts.push(`--output ${outputPath}`);
    return parts.join(' ');
  }, [sdr, usrpArgs, antenna, timedHopping, initialFreq, gain, protocol, syncWord, duration, outputPath]);

  const copyCli = () => {
    navigator.clipboard.writeText(cliCommand);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  // Timing breakdown percentages for TDD window
  const timingBreakdown = useMemo(() => {
    if (simData?.timing_breakdown) {
      return simData.timing_breakdown;
    }
    // Theoretical DK32S 50ms default breakdown
    return {
      ground_tx_ms: 1.502,
      turnaround_guard_ms: 0.810,
      air_rx_ms: 0.504,
      slot_guard_ms: 47.184,
      dwell_ms: 50.0,
    };
  }, [simData]);

  // State badge styling
  const getStateBadge = (state: string) => {
    switch (state.toLowerCase()) {
      case 'locked':
        return {
          bg: 'bg-emerald-500/20 text-emerald-400 border-emerald-500/40',
          dot: 'bg-emerald-400',
          label: '已锁定 (LOCKED)',
        };
      case 'acquiring':
        return {
          bg: 'bg-blue-500/20 text-blue-400 border-blue-500/40',
          dot: 'bg-blue-400',
          label: '捕获中 (ACQUIRING)',
        };
      case 'holdover':
        return {
          bg: 'bg-amber-500/20 text-amber-400 border-amber-500/40',
          dot: 'bg-amber-400',
          label: '保持态 (HOLDOVER)',
        };
      case 'lost':
        return {
          bg: 'bg-rose-500/20 text-rose-400 border-rose-500/40',
          dot: 'bg-rose-400',
          label: '同步丢失 (LOST)',
        };
      case 'searching':
      default:
        return {
          bg: 'bg-zinc-700/40 text-zinc-300 border-zinc-600',
          dot: 'bg-zinc-400 animate-ping',
          label: '搜网中 (SEARCHING)',
        };
    }
  };

  const badge = getStateBadge(liveState.state);

  return (
    <div className="h-full flex flex-col overflow-y-auto bg-rf-bg text-rf-text p-4 md:p-6 space-y-6">
      {/* 顶部标题栏与模式切换 */}
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-4 border-b border-rf-border pb-4">
        <div>
          <div className="flex items-center gap-3">
            <div className="p-2.5 bg-indigo-500/20 border border-indigo-500/40 rounded-xl text-indigo-400 shadow-lg shadow-indigo-950/20">
              <Clock className="w-6 h-6" />
            </div>
            <div>
              <h1 className="text-xl font-bold text-zinc-100 flex items-center gap-2">
                <span>FHSS 跳频与 TDD 时序同步工作台</span>
                <span className="text-xs px-2 py-0.5 rounded bg-indigo-950/60 border border-indigo-500/30 text-indigo-300 font-mono">
                  flrc sync
                </span>
              </h1>
              <p className="text-xs text-zinc-400 mt-0.5">
                实时估计硬件时钟漂移、跟踪 20-Hop 跳频时隙与 TDD 收发窗口交互时序
              </p>
            </div>
          </div>
        </div>

        {/* 模式选择器 */}
        <div className="flex items-center bg-rf-surface border border-rf-border rounded-xl p-1 gap-1 self-start md:self-auto">
          <button
            type="button"
            onClick={() => setMode('simulate')}
            className={`px-3 py-1.5 rounded-lg text-xs font-medium transition-all flex items-center gap-1.5 ${
              mode === 'simulate'
                ? 'bg-indigo-600 text-white shadow'
                : 'text-zinc-400 hover:text-zinc-200 hover:bg-zinc-800/40'
            }`}
          >
            <Cpu className="w-3.5 h-3.5" />
            <span>时序仿真 (Simulation)</span>
          </button>
          <button
            type="button"
            onClick={() => setMode('live')}
            className={`px-3 py-1.5 rounded-lg text-xs font-medium transition-all flex items-center gap-1.5 ${
              mode === 'live'
                ? 'bg-indigo-600 text-white shadow'
                : 'text-zinc-400 hover:text-zinc-200 hover:bg-zinc-800/40'
            }`}
          >
            <Radio className="w-3.5 h-3.5" />
            <span>实机运行 (Live SDR)</span>
          </button>
          <button
            type="button"
            onClick={() => setMode('playback')}
            className={`px-3 py-1.5 rounded-lg text-xs font-medium transition-all flex items-center gap-1.5 ${
              mode === 'playback'
                ? 'bg-indigo-600 text-white shadow'
                : 'text-zinc-400 hover:text-zinc-200 hover:bg-zinc-800/40'
            }`}
          >
            <Layers className="w-3.5 h-3.5" />
            <span>历史回放 (Playback)</span>
          </button>
        </div>
      </div>

      {/* 核心指标与仪表状态条 */}
      <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
        {/* 1. 同步锁定状态 */}
        <div className="bg-rf-surface border border-rf-border rounded-xl p-3.5 flex flex-col justify-between">
          <div className="flex items-center justify-between text-xs text-zinc-400">
            <span>同步状态</span>
            <Activity className="w-3.5 h-3.5 text-zinc-500" />
          </div>
          <div className="mt-2 flex items-center gap-2">
            <span className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium border ${badge.bg}`}>
              <span className={`w-2 h-2 rounded-full ${badge.dot}`} />
              {badge.label}
            </span>
          </div>
          <div className="text-[11px] text-zinc-500 mt-1 font-mono">
            置信度: {(liveState.confidence * 100).toFixed(1)}%
          </div>
        </div>

        {/* 2. 时钟漂移表盘 (PPM) */}
        <div className="bg-rf-surface border border-rf-border rounded-xl p-3.5 flex flex-col justify-between">
          <div className="flex items-center justify-between text-xs text-zinc-400">
            <span>硬件时钟漂移</span>
            <Gauge className="w-3.5 h-3.5 text-indigo-400" />
          </div>
          <div className="mt-1 flex items-baseline gap-1.5">
            <span
              className={`text-xl font-bold font-mono ${
                Math.abs(liveState.clock_drift_ppm) < 20
                  ? 'text-emerald-400'
                  : Math.abs(liveState.clock_drift_ppm) < 50
                  ? 'text-amber-400'
                  : 'text-rose-400'
              }`}
            >
              {liveState.clock_drift_ppm > 0 ? `+${liveState.clock_drift_ppm.toFixed(2)}` : liveState.clock_drift_ppm.toFixed(2)}
            </span>
            <span className="text-xs text-zinc-400 font-mono">ppm</span>
          </div>
          {/* 微型双极偏置指示刻度 */}
          <div className="w-full bg-zinc-800/80 rounded-full h-1.5 mt-2 relative overflow-hidden">
            <div className="absolute left-1/2 top-0 bottom-0 w-0.5 bg-zinc-500 z-10" />
            <div
              className={`h-full transition-all duration-300 ${
                liveState.clock_drift_ppm >= 0 ? 'bg-cyan-500' : 'bg-amber-500'
              }`}
              style={{
                width: `${Math.min(50, (Math.abs(liveState.clock_drift_ppm) / 100) * 50)}%`,
                left: liveState.clock_drift_ppm >= 0 ? '50%' : `${50 - Math.min(50, (Math.abs(liveState.clock_drift_ppm) / 100) * 50)}%`,
                position: 'absolute',
              }}
            />
          </div>
        </div>

        {/* 3. 当前时隙与频点 */}
        <div className="bg-rf-surface border border-rf-border rounded-xl p-3.5 flex flex-col justify-between">
          <div className="flex items-center justify-between text-xs text-zinc-400">
            <span>当前时隙 / 频点</span>
            <RadioTower className="w-3.5 h-3.5 text-cyan-400" />
          </div>
          <div className="mt-1">
            <div className="text-base font-bold text-zinc-100 font-mono">
              Slot #{String(liveState.slot_index).padStart(2, '0')}{' '}
              <span className="text-xs font-normal text-zinc-400">/ {hoppingTable.length}</span>
            </div>
            <div className="text-xs text-cyan-400 font-mono mt-0.5">
              {(liveState.frequency_hz / 1e6).toFixed(2)} MHz
            </div>
          </div>
          <div className="text-[11px] text-zinc-500 mt-1">
            下个跳频: Slot #{String(liveState.next_slot_index ?? ((liveState.slot_index + 1) % hoppingTable.length)).padStart(2, '0')}
          </div>
        </div>

        {/* 4. TDD 交互阶段 */}
        <div className="bg-rf-surface border border-rf-border rounded-xl p-3.5 flex flex-col justify-between">
          <div className="flex items-center justify-between text-xs text-zinc-400">
            <span>TDD 交互时序阶段</span>
            <Timer className="w-3.5 h-3.5 text-amber-400" />
          </div>
          <div className="mt-1">
            <div className="text-base font-bold text-amber-300 font-mono capitalize">
              {liveState.tdd_phase.replace('_', ' ')}
            </div>
            <div className="text-xs text-zinc-400 mt-0.5">
              50ms 周期守候
            </div>
          </div>
          <div className="text-[11px] text-zinc-500 mt-1 font-mono">
            剩余: {(liveState.slot_remaining_s * 1000).toFixed(1)} ms
          </div>
        </div>

        {/* 5. 帧统计与硬件健康 */}
        <div className="bg-rf-surface border border-rf-border rounded-xl p-3.5 flex flex-col justify-between col-span-2 md:col-span-1">
          <div className="flex items-center justify-between text-xs text-zinc-400">
            <span>累计解调 / 溢出</span>
            <Activity className="w-3.5 h-3.5 text-emerald-400" />
          </div>
          <div className="mt-1 flex items-baseline gap-2">
            <span className="text-xl font-bold text-emerald-400 font-mono">
              {liveState.decoded_frames ?? 0}
            </span>
            <span className="text-xs text-zinc-400">帧</span>
          </div>
          <div className="text-[11px] text-zinc-500 mt-1 flex items-center justify-between">
            <span>溢出: {liveState.overflow_count ?? 0}</span>
            <span>锚点: {liveState.anchor_count ?? 0}</span>
          </div>
        </div>
      </div>

      {/* 参数配置栏 (可折叠) */}
      <div className="bg-rf-surface border border-rf-border rounded-xl overflow-hidden shadow-sm">
        <div
          onClick={() => setShowConfig(!showConfig)}
          className="px-4 py-3 bg-rf-panel border-b border-rf-border/60 flex items-center justify-between cursor-pointer hover:bg-zinc-800/40 transition-colors"
        >
          <div className="flex items-center gap-2 text-sm font-semibold text-zinc-200">
            <Sliders className="w-4 h-4 text-indigo-400" />
            <span>
              {mode === 'simulate'
                ? '时序仿真参数配置'
                : mode === 'live'
                ? 'SDR 实时同步参数'
                : '历史文件与任务回放'}
            </span>
          </div>
          <div className="flex items-center gap-2">
            <span className="text-xs text-zinc-400">
              {showConfig ? '收起配置' : '展开配置'}
            </span>
            {showConfig ? <ChevronUp className="w-4 h-4 text-zinc-400" /> : <ChevronDown className="w-4 h-4 text-zinc-400" />}
          </div>
        </div>

        {showConfig && (
          <div className="p-4 space-y-4">
            {mode === 'simulate' && (
              <div className="grid grid-cols-2 md:grid-cols-6 gap-3">
                <div>
                  <label className="block text-xs font-medium text-zinc-400 mb-1">
                    时隙驻留时间 (Dwell ms)
                  </label>
                  <input
                    type="number"
                    step="5"
                    min="10"
                    max="200"
                    value={dwellMs}
                    onChange={(e) => setDwellMs(Number(e.target.value))}
                    className="w-full bg-rf-bg border border-rf-border rounded-lg px-2.5 py-1.5 text-xs text-zinc-200 font-mono focus:border-indigo-500 focus:outline-none"
                  />
                </div>
                <div>
                  <label className="block text-xs font-medium text-zinc-400 mb-1">
                    FLRC 编码率 (CR)
                  </label>
                  <select
                    value={codingRate}
                    onChange={(e) => setCodingRate(e.target.value as any)}
                    className="w-full bg-rf-bg border border-rf-border rounded-lg px-2.5 py-1.5 text-xs text-zinc-200 font-mono focus:border-indigo-500 focus:outline-none"
                  >
                    <option value="3/4">3/4 (标准)</option>
                    <option value="1/2">1/2 (高纠错)</option>
                    <option value="1">1 (无 FEC)</option>
                  </select>
                </div>
                <div>
                  <label className="block text-xs font-medium text-zinc-400 mb-1">
                    载荷长度 (字节)
                  </label>
                  <input
                    type="number"
                    min="1"
                    max="128"
                    value={payloadBytes}
                    onChange={(e) => setPayloadBytes(Number(e.target.value))}
                    className="w-full bg-rf-bg border border-rf-border rounded-lg px-2.5 py-1.5 text-xs text-zinc-200 font-mono focus:border-indigo-500 focus:outline-none"
                  />
                </div>
                <div>
                  <label className="block text-xs font-medium text-zinc-400 mb-1">
                    模拟时钟漂移 (ppm)
                  </label>
                  <input
                    type="number"
                    step="0.5"
                    value={simDriftPpm}
                    onChange={(e) => setSimDriftPpm(Number(e.target.value))}
                    className="w-full bg-rf-bg border border-rf-border rounded-lg px-2.5 py-1.5 text-xs text-zinc-200 font-mono focus:border-indigo-500 focus:outline-none"
                  />
                </div>
                <div>
                  <label className="block text-xs font-medium text-zinc-400 mb-1">
                    仿真循环周期
                  </label>
                  <input
                    type="number"
                    min="1"
                    max="10"
                    value={simCycles}
                    onChange={(e) => setSimCycles(Number(e.target.value))}
                    className="w-full bg-rf-bg border border-rf-border rounded-lg px-2.5 py-1.5 text-xs text-zinc-200 font-mono focus:border-indigo-500 focus:outline-none"
                  />
                </div>
                <div className="flex items-end gap-2">
                  <button
                    type="button"
                    onClick={handleRunSimulation}
                    className="flex-1 px-3 py-1.5 bg-indigo-600 hover:bg-indigo-500 text-white rounded-lg text-xs font-medium transition-colors flex items-center justify-center gap-1.5 shadow"
                  >
                    <RotateCw className="w-3.5 h-3.5" />
                    <span>重新计算</span>
                  </button>
                  <button
                    type="button"
                    onClick={() => setSimPlaying(!simPlaying)}
                    className={`px-3 py-1.5 rounded-lg text-xs font-medium transition-colors flex items-center justify-center gap-1.5 shadow ${
                      simPlaying
                        ? 'bg-amber-600 hover:bg-amber-500 text-white'
                        : 'bg-emerald-600 hover:bg-emerald-500 text-white'
                    }`}
                  >
                    {simPlaying ? <Square className="w-3.5 h-3.5" /> : <Play className="w-3.5 h-3.5" />}
                    <span>{simPlaying ? '暂停' : '连续跳频'}</span>
                  </button>
                </div>
              </div>
            )}

            {mode === 'live' && (
              <div className="grid grid-cols-2 md:grid-cols-6 gap-3">
                <div>
                  <label className="block text-xs font-medium text-zinc-400 mb-1">SDR 设备</label>
                  <select
                    value={sdr}
                    onChange={(e) => setSdr(e.target.value as any)}
                    className="w-full bg-rf-bg border border-rf-border rounded-lg px-2.5 py-1.5 text-xs text-zinc-200 font-mono focus:border-indigo-500 focus:outline-none"
                  >
                    <option value="usrp">USRP (N310/B210)</option>
                    <option value="hackrf">HackRF One</option>
                  </select>
                </div>
                {sdr === 'usrp' ? (
                  <div>
                    <label className="block text-xs font-medium text-zinc-400 mb-1">USRP 定位参数</label>
                    <input
                      type="text"
                      value={usrpArgs}
                      onChange={(e) => setUsrpArgs(e.target.value)}
                      className="w-full bg-rf-bg border border-rf-border rounded-lg px-2.5 py-1.5 text-xs text-zinc-200 font-mono focus:border-indigo-500 focus:outline-none"
                    />
                  </div>
                ) : (
                  <div>
                    <label className="block text-xs font-medium text-zinc-400 mb-1">天线 / 端口</label>
                    <input
                      type="text"
                      disabled
                      value="ANT (Default)"
                      className="w-full bg-rf-bg border border-rf-border/40 rounded-lg px-2.5 py-1.5 text-xs text-zinc-500 font-mono"
                    />
                  </div>
                )}
                <div>
                  <label className="block text-xs font-medium text-zinc-400 mb-1">初始监听频点 (Hz)</label>
                  <input
                    type="number"
                    value={initialFreq}
                    onChange={(e) => setInitialFreq(Number(e.target.value))}
                    className="w-full bg-rf-bg border border-rf-border rounded-lg px-2.5 py-1.5 text-xs text-zinc-200 font-mono focus:border-indigo-500 focus:outline-none"
                  />
                </div>
                <div>
                  <label className="block text-xs font-medium text-zinc-400 mb-1">射频增益 (dB)</label>
                  <input
                    type="number"
                    value={gain}
                    onChange={(e) => setGain(Number(e.target.value))}
                    className="w-full bg-rf-bg border border-rf-border rounded-lg px-2.5 py-1.5 text-xs text-zinc-200 font-mono focus:border-indigo-500 focus:outline-none"
                  />
                </div>
                <div>
                  <label className="block text-xs font-medium text-zinc-400 mb-1">物理同步字 (Hex)</label>
                  <input
                    type="text"
                    value={syncWord}
                    onChange={(e) => setSyncWord(e.target.value)}
                    className="w-full bg-rf-bg border border-rf-border rounded-lg px-2.5 py-1.5 text-xs text-zinc-200 font-mono focus:border-indigo-500 focus:outline-none"
                  />
                </div>
                <div className="flex items-end gap-2">
                  {currentRunId ? (
                    <button
                      type="button"
                      onClick={handleStopLiveSync}
                      className="w-full px-3 py-1.5 bg-rose-600 hover:bg-rose-500 text-white rounded-lg text-xs font-medium transition-colors flex items-center justify-center gap-1.5 shadow"
                    >
                      <Square className="w-3.5 h-3.5" />
                      <span>停止同步</span>
                    </button>
                  ) : (
                    <button
                      type="button"
                      onClick={handleStartLiveSync}
                      className="w-full px-3 py-1.5 bg-indigo-600 hover:bg-indigo-500 text-white rounded-lg text-xs font-medium transition-colors flex items-center justify-center gap-1.5 shadow"
                    >
                      <Play className="w-3.5 h-3.5" />
                      <span>启动实时同步</span>
                    </button>
                  )}
                </div>
              </div>
            )}

            {mode === 'playback' && (
              <div className="grid grid-cols-1 md:grid-cols-4 gap-3">
                <div className="md:col-span-2">
                  <label className="block text-xs font-medium text-zinc-400 mb-1">历史 Sync 任务</label>
                  <select
                    onChange={(e) => {
                      const r = syncRuns.find((item) => item.id === e.target.value);
                      if (r) {
                        void handleParsePlayback(r.id, r.command_args?.output);
                      }
                    }}
                    className="w-full bg-rf-bg border border-rf-border rounded-lg px-2.5 py-1.5 text-xs text-zinc-200 font-mono focus:border-indigo-500 focus:outline-none"
                  >
                    <option value="">选择已有任务...</option>
                    {syncRuns.map((r) => (
                      <option key={r.id} value={r.id}>
                        {r.name || r.id} ({r.created_at.slice(0, 19).replace('T', ' ')}) - {r.status}
                      </option>
                    ))}
                  </select>
                </div>
                <div>
                  <label className="block text-xs font-medium text-zinc-400 mb-1">JSONL 文件路径</label>
                  <input
                    type="text"
                    value={playbackFile}
                    onChange={(e) => setPlaybackFile(e.target.value)}
                    className="w-full bg-rf-bg border border-rf-border rounded-lg px-2.5 py-1.5 text-xs text-zinc-200 font-mono focus:border-indigo-500 focus:outline-none"
                  />
                </div>
                <div className="flex items-end">
                  <button
                    type="button"
                    onClick={() => handleParsePlayback(undefined, playbackFile)}
                    className="w-full px-3 py-1.5 bg-indigo-600 hover:bg-indigo-500 text-white rounded-lg text-xs font-medium transition-colors flex items-center justify-center gap-1.5 shadow"
                  >
                    <RotateCw className="w-3.5 h-3.5" />
                    <span>加载解析</span>
                  </button>
                </div>
              </div>
            )}
          </div>
        )}
      </div>

      {/* 核心可视化 1: TDD 收发时隙时序环盘 / 预算条 */}
      <div className="bg-rf-surface border border-rf-border rounded-xl p-4 space-y-3 shadow-md">
        <div className="flex flex-col md:flex-row md:items-center justify-between gap-2">
          <div className="flex items-center gap-2">
            <Timer className="w-4 h-4 text-amber-400" />
            <h3 className="text-sm font-semibold text-zinc-100">
              TDD 半双工时分窗口分解 (Dwell Time: {timingBreakdown.dwell_ms.toFixed(1)} ms)
            </h3>
          </div>
          <div className="flex items-center gap-4 text-xs font-mono text-zinc-400">
            <span className="flex items-center gap-1.5">
              <span className="w-2.5 h-2.5 rounded bg-blue-500" />
              地面TX: {timingBreakdown.ground_tx_ms.toFixed(3)} ms
            </span>
            <span className="flex items-center gap-1.5">
              <span className="w-2.5 h-2.5 rounded bg-amber-500" />
              转换保护: {timingBreakdown.turnaround_guard_ms.toFixed(3)} ms
            </span>
            <span className="flex items-center gap-1.5">
              <span className="w-2.5 h-2.5 rounded bg-emerald-500" />
              空中RX: {timingBreakdown.air_rx_ms.toFixed(3)} ms
            </span>
            <span className="flex items-center gap-1.5">
              <span className="w-2.5 h-2.5 rounded bg-zinc-600" />
              槽尾守候: {timingBreakdown.slot_guard_ms.toFixed(3)} ms
            </span>
          </div>
        </div>

        {/* 动态比例时间条 */}
        <div className="w-full h-8 bg-zinc-900 border border-rf-border rounded-lg overflow-hidden flex relative shadow-inner">
          {/* Ground TX */}
          <div
            style={{ width: `${(timingBreakdown.ground_tx_ms / timingBreakdown.dwell_ms) * 100}%` }}
            className="h-full bg-blue-600/80 hover:bg-blue-500 transition-colors flex items-center justify-center text-[10px] text-white font-mono font-bold border-r border-blue-400/40 relative group"
            title={`地面发送: ${timingBreakdown.ground_tx_ms.toFixed(3)} ms`}
          >
            TX
          </div>
          {/* Turnaround Guard */}
          <div
            style={{ width: `${(timingBreakdown.turnaround_guard_ms / timingBreakdown.dwell_ms) * 100}%` }}
            className="h-full bg-amber-600/80 hover:bg-amber-500 transition-colors flex items-center justify-center text-[9px] text-white font-mono border-r border-amber-400/40 relative group"
            title={`保护间隔: ${timingBreakdown.turnaround_guard_ms.toFixed(3)} ms (810µs)`}
          >
            G
          </div>
          {/* Air RX */}
          <div
            style={{ width: `${(timingBreakdown.air_rx_ms / timingBreakdown.dwell_ms) * 100}%` }}
            className="h-full bg-emerald-600/80 hover:bg-emerald-500 transition-colors flex items-center justify-center text-[10px] text-white font-mono font-bold border-r border-emerald-400/40 relative group"
            title={`空中回复: ${timingBreakdown.air_rx_ms.toFixed(3)} ms`}
          >
            RX
          </div>
          {/* Slot Guard */}
          <div
            style={{ width: `${(timingBreakdown.slot_guard_ms / timingBreakdown.dwell_ms) * 100}%` }}
            className="h-full bg-zinc-800/80 flex items-center justify-start px-3 text-[10px] text-zinc-400 font-mono"
            title={`时隙空闲守候: ${timingBreakdown.slot_guard_ms.toFixed(3)} ms`}
          >
            空闲守候 ({(timingBreakdown.slot_guard_ms).toFixed(2)} ms)
          </div>

          {/* 动画时间指针 (扫过 50ms) */}
          {simPlaying && (
            <div
              className="absolute top-0 bottom-0 w-0.5 bg-cyan-300 shadow-[0_0_8px_#22d3ee] z-20 animate-pulse pointer-events-none"
              style={{
                left: `${((Date.now() % Math.max(10, timingBreakdown.dwell_ms)) / timingBreakdown.dwell_ms) * 100}%`,
              }}
            />
          )}
        </div>
      </div>

      {/* 核心可视化 2: FHSS 20-Hop 跳频序列矩阵 */}
      <div className="bg-rf-surface border border-rf-border rounded-xl p-4 space-y-3 shadow-md">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Layers className="w-4 h-4 text-indigo-400" />
            <h3 className="text-sm font-semibold text-zinc-100">
              FHSS 跳频序列图案矩阵 (Hopping Sequence Matrix · 20 Slots)
            </h3>
          </div>
          <span className="text-xs text-zinc-400 font-mono">
            周期循环长度: {hoppingTable.length * dwellMs} ms
          </span>
        </div>

        <div className="grid grid-cols-2 sm:grid-cols-4 md:grid-cols-5 gap-2.5">
          {hoppingTable.map((freq, idx) => {
            const isActive = liveState.slot_index === idx;
            const isNext = liveState.next_slot_index === idx;

            return (
              <div
                key={idx}
                onClick={() => {
                  setSpectrumTargetFreqHz(freq);
                  setActiveTab('spectrum');
                }}
                className={`p-2.5 rounded-lg border transition-all cursor-pointer relative overflow-hidden group ${
                  isActive
                    ? 'bg-indigo-950/60 border-indigo-400 shadow-md shadow-indigo-950/40 ring-1 ring-indigo-400'
                    : isNext
                    ? 'bg-zinc-800/60 border-cyan-500/50 border-dashed hover:border-cyan-400'
                    : 'bg-rf-bg border-rf-border/80 hover:border-zinc-500 hover:bg-zinc-800/40'
                }`}
              >
                {isActive && (
                  <div className="absolute top-0 right-0 w-2 h-2 rounded-bl bg-indigo-400" />
                )}
                <div className="flex items-center justify-between text-xs">
                  <span className={`font-mono font-bold ${isActive ? 'text-indigo-300' : 'text-zinc-400'}`}>
                    #{String(idx).padStart(2, '0')}
                  </span>
                  {isActive && (
                    <span className="text-[10px] px-1.5 py-0.2 rounded bg-indigo-500/20 text-indigo-300 font-mono border border-indigo-500/40 animate-pulse">
                      ACTIVE
                    </span>
                  )}
                  {isNext && !isActive && (
                    <span className="text-[10px] px-1 py-0.2 rounded bg-cyan-500/20 text-cyan-300 font-mono border border-cyan-500/30">
                      NEXT
                    </span>
                  )}
                </div>
                <div className="mt-1 font-mono text-sm font-semibold text-zinc-200">
                  {(freq / 1e6).toFixed(2)}{' '}
                  <span className="text-[10px] font-normal text-zinc-500">MHz</span>
                </div>
                <div className="mt-1 flex items-center justify-between text-[10px] text-zinc-500 group-hover:text-cyan-400 transition-colors">
                  <span>调谐监视</span>
                  <ExternalLink className="w-2.5 h-2.5" />
                </div>
              </div>
            );
          })}
        </div>
      </div>

      {/* 核心可视化 3 & 4: 跳频阶梯瀑布图与时钟漂移收敛折线 */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {/* 跳频阶梯瀑布图 */}
        <div className="bg-rf-surface border border-rf-border rounded-xl p-4 space-y-3 shadow-md flex flex-col">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <Radio className="w-4 h-4 text-cyan-400" />
              <h3 className="text-sm font-semibold text-zinc-100">
                跳频阶梯频点跃迁轨迹 (Frequency Trajectory)
              </h3>
            </div>
            <span className="text-xs text-zinc-500">2.40 ~ 2.50 GHz</span>
          </div>

          <div className="flex-1 min-h-[180px] bg-rf-bg border border-rf-border rounded-lg p-3 relative flex flex-col justify-between overflow-hidden">
            {/* SVG 轨迹折线 */}
            <svg className="w-full h-full overflow-visible" viewBox="0 0 400 140" preserveAspectRatio="none">
              {/* 网格水平参考线 */}
              <line x1="0" y1="20" x2="400" y2="20" stroke="#27272a" strokeDasharray="3 3" />
              <line x1="0" y1="70" x2="400" y2="70" stroke="#27272a" strokeDasharray="3 3" />
              <line x1="0" y1="120" x2="400" y2="120" stroke="#27272a" strokeDasharray="3 3" />

              {/* 绘制 20 个时隙的频率台阶 */}
              {hoppingTable.map((f, i) => {
                const x1 = (i / hoppingTable.length) * 400;
                const x2 = ((i + 1) / hoppingTable.length) * 400;
                // Normalize frequency 2400e6 ~ 2500e6 -> 140 ~ 0
                const y = 140 - ((f - 2400e6) / 100e6) * 120 - 10;
                const isActive = liveState.slot_index === i;

                return (
                  <g key={i}>
                    <line
                      x1={x1}
                      y1={y}
                      x2={x2}
                      y2={y}
                      stroke={isActive ? '#38bdf8' : '#6366f1'}
                      strokeWidth={isActive ? 3 : 1.5}
                      strokeLinecap="round"
                    />
                    {i > 0 && (
                      <line
                        x1={x1}
                        y1={140 - ((hoppingTable[i - 1] - 2400e6) / 100e6) * 120 - 10}
                        x2={x1}
                        y2={y}
                        stroke="#3f3f46"
                        strokeDasharray="2 2"
                        strokeWidth={1}
                      />
                    )}
                    {isActive && (
                      <circle cx={(x1 + x2) / 2} cy={y} r={4} fill="#38bdf8" className="animate-ping" />
                    )}
                  </g>
                );
              })}
            </svg>
            <div className="flex items-center justify-between text-[10px] text-zinc-500 font-mono pt-1 border-t border-zinc-800">
              <span>Slot 0 (0ms)</span>
              <span>Slot 10 (500ms)</span>
              <span>Slot 20 (1000ms)</span>
            </div>
          </div>
        </div>

        {/* 时钟漂移跟踪收敛曲线 */}
        <div className="bg-rf-surface border border-rf-border rounded-xl p-4 space-y-3 shadow-md flex flex-col">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <Activity className="w-4 h-4 text-emerald-400" />
              <h3 className="text-sm font-semibold text-zinc-100">
                时钟漂移跟踪收敛曲线 (Clock Drift Convergence)
              </h3>
            </div>
            <span className="text-xs text-zinc-500 font-mono">
              最新: {liveState.clock_drift_ppm.toFixed(2)} ppm
            </span>
          </div>

          <div className="flex-1 min-h-[180px] bg-rf-bg border border-rf-border rounded-lg p-3 relative flex flex-col justify-between overflow-hidden">
            {/* SVG 漂移收敛曲线 */}
            <svg className="w-full h-full overflow-visible" viewBox="0 0 400 140" preserveAspectRatio="none">
              {/* 0 ppm 水平基准线 */}
              <line x1="0" y1="70" x2="400" y2="70" stroke="#3f3f46" strokeWidth={1} />
              <text x="5" y="65" fill="#71717a" fontSize="10" fontFamily="monospace">
                0 ppm
              </text>
              <line x1="0" y1="25" x2="400" y2="25" stroke="#27272a" strokeDasharray="3 3" />
              <text x="5" y="22" fill="#52525b" fontSize="9" fontFamily="monospace">
                +50 ppm
              </text>
              <line x1="0" y1="115" x2="400" y2="115" stroke="#27272a" strokeDasharray="3 3" />
              <text x="5" y="112" fill="#52525b" fontSize="9" fontFamily="monospace">
                -50 ppm
              </text>

              {/* 绘制历史折线 */}
              {driftHistory.length > 1 && (
                <polyline
                  fill="none"
                  stroke="#34d399"
                  strokeWidth="2"
                  strokeLinejoin="round"
                  points={driftHistory
                    .map((d, idx) => {
                      const x = (idx / (driftHistory.length - 1)) * 400;
                      // map ppm -50 ~ +50 to 120 ~ 20
                      const clamped = Math.max(-50, Math.min(50, d.drift));
                      const y = 70 - (clamped / 50) * 45;
                      return `${x},${y}`;
                    })
                    .join(' ')}
                />
              )}
            </svg>
            <div className="flex items-center justify-between text-[10px] text-zinc-500 font-mono pt-1 border-t border-zinc-800">
              <span>历史首点</span>
              <span>Kalman 拟合收敛区间</span>
              <span>当前最新</span>
            </div>
          </div>
        </div>
      </div>

      {/* 底部 CLI 命令行预览与复制 */}
      <div className="bg-rf-surface border border-rf-border rounded-xl p-4 space-y-2 shadow-md">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Radio className="w-4 h-4 text-indigo-400" />
            <span className="text-xs font-semibold text-zinc-300">等效 CLI 命令行预览</span>
          </div>
          <button
            type="button"
            onClick={copyCli}
            className="flex items-center gap-1.5 px-3 py-1 bg-zinc-800 hover:bg-zinc-700 text-zinc-200 text-xs rounded-lg border border-zinc-700 transition-colors shadow"
          >
            {copied ? <Check className="w-3.5 h-3.5 text-emerald-400" /> : <Copy className="w-3.5 h-3.5" />}
            <span>{copied ? '已复制' : '复制命令'}</span>
          </button>
        </div>
        <div className="bg-zinc-950 rounded-lg p-3 font-mono text-xs text-indigo-300 overflow-x-auto border border-zinc-800/80">
          <code>{cliCommand}</code>
        </div>
      </div>
    </div>
  );
};

export default SyncWorkbenchPanel;
