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
  X,
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
import { useFrameField, useSyncField, useRadioWorkbenchStore } from '../stores/useRadioWorkbenchStore';
import { linkedFrameRequest, linkedSessionMatches, syncWorkbenchRequest, syncRequestMatches } from '../utils/radioLink';
import { DuplexSessionPanel } from './DuplexSessionPanel';
import { SyncLiveParameters } from './SyncLiveParameters';
import { SyncTemplateSelector } from './SyncTemplateSelector';
import { templateReference } from '../utils/syncTemplates';
import { argvPreview } from '../utils/runtime';

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
  const [mode, setMode] = useSyncField('mode');
  const [operation, setOperation] = useSyncField('operation');
  const linked = useRadioWorkbenchStore(state => state.linked);
  const frameDraft = useRadioWorkbenchStore(state => state.frame);
  const syncSettings = useRadioWorkbenchStore(state => state.sync);
  const [linkError, setLinkError] = useState<string | null>(null);
  const [parameterError, setParameterError] = useState('');
  const parameterTrigger = useRef<HTMLButtonElement>(null);
  const parameterDrawer = useRef<HTMLElement>(null);
  const runDraft = useMemo(() => {
    try { return { request: syncWorkbenchRequest(frameDraft, syncSettings), error: '' }; }
    catch (error) { return { request: null, error: (error as Error).message }; }
  }, [frameDraft, syncSettings]);
  const simulationVersion = useRef(0);
  const playbackVersion = useRef(0);

  // Live SDR parameters
  const [sdr, setSdr] = useSyncField('sdr');
  const [usrpArgs, setUsrpArgs] = useSyncField('usrpArgs');
  const [rfConfig, setRfConfig] = useSyncField('rfConfig');
  const templates = useQuery({ queryKey: ['templates'], queryFn: () => api.getTemplates(), staleTime: 60000 });
  const [initialFreq, setInitialFreq] = useSyncField('initialFreq');
  const [sampleRate, setSampleRate] = useSyncField('sampleRate');
  const [gain, setGain] = useSyncField('gain');
  const [txGain, setTxGain] = useSyncField('txGain');
  const [antenna, setAntenna] = useSyncField('antenna');
  const [protocol, setProtocol] = useFrameField('protocol');
  const [syncWord, setSyncWord] = useFrameField('syncWord');
  const [timedHopping, setTimedHopping] = useSyncField('timedHopping');
  const [duration, setDuration] = useSyncField('duration');
  const [outputPath, setOutputPath] = useSyncField('outputPath');
  const currentRunId = useWorkstationStore(state => state.syncRunId);
  const setCurrentRunId = useWorkstationStore(state => state.setSyncRunId);
  const liveMetric = useWebSocketStore(state => currentRunId
    ? state.stageMetrics[currentRunId]?.live_sync : undefined);
  const runAction = useRef(false);
  const [runActionPending, setRunActionPending] = useState<'start' | 'stop' | null>(null);
  const { data: trackedRun } = useQuery({
    queryKey: ['run', currentRunId], queryFn: () => api.getRun(currentRunId!),
    enabled: Boolean(currentRunId), refetchInterval: 1500,
  });
  const duplexRunId = useRadioWorkbenchStore(state => state.duplexRunId);
  const { data: lastSessionRun } = useQuery({
    queryKey: ['run', duplexRunId], queryFn: () => api.getRun(duplexRunId!),
    enabled: Boolean(duplexRunId) && duplexRunId !== currentRunId,
  });
  const sessionRun = trackedRun?.command_id === 'duplex' ? trackedRun : lastSessionRun;
  const activeConfigurationChanged = Boolean(currentRunId && trackedRun &&
    (!runDraft.request || !syncRequestMatches(runDraft.request, trackedRun)));
  const configurationChanged = sessionRun?.status === 'RUNNING'
    && !linkedSessionMatches(frameDraft, syncSettings, sessionRun.command_args);
  useEffect(() => {
    if (trackedRun?.id === currentRunId && ['SUCCEEDED', 'FAILED', 'CANCELLED'].includes(trackedRun.status)) {
      if (trackedRun.status === 'FAILED') setLinkError(trackedRun.error || '同步任务失败，请查看任务终端日志');
      setCurrentRunId(null);
    }
  }, [trackedRun, currentRunId, setCurrentRunId]);

  // Simulation parameters
  const [dwellMs, setDwellMs] = useSyncField('dwellMs');
  const [codingRate, setCodingRate] = useFrameField('codingRate');
  const [payloadBytes, setPayloadBytes] = useSyncField('payloadBytes');
  const [simDriftPpm, setSimDriftPpm] = useSyncField('simDriftPpm');
  const [simCycles, setSimCycles] = useSyncField('simCycles');
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
    windows_skipped: 0,
    skipped_seconds: 0,
    windows_queued: 0,
    queue_delay_seconds: 0,
    dsp_load_ratio: 0,
    anchor_count: 0,
    lock_duration_s: 0,
  });

  const [driftHistory, setDriftHistory] = useState<Array<{ time: number; drift: number }>>([]);
  const [recentFrames, setRecentFrames] = useState<any[]>([]);
  const [copied, setCopied] = useState(false);
  const [showConfig, setShowConfig] = useState(false);
  useEffect(() => {
    if (showConfig) parameterDrawer.current?.focus();
  }, [showConfig]);
  const closeParameters = () => { setShowConfig(false); setParameterError(''); parameterTrigger.current?.focus(); };

  useEffect(() => {
    if (mode !== 'live') return;
    setLiveState(previous => ({ ...previous, state: 'searching', confidence: 0,
      clock_drift_ppm: 0, decoded_frames: 0, overflow_count: 0, anchor_count: 0, lock_duration_s: 0,
      windows_skipped: 0, skipped_seconds: 0, windows_queued: 0, queue_delay_seconds: 0, dsp_load_ratio: 0 }));
    setDriftHistory([]);
  }, [mode, currentRunId]);

  // Available hopping table (DK32S default or from simulation/playback)
  const hoppingTable = useMemo(() => {
    if (mode === 'live' && rfConfig) {
      const selected = templates.data?.find(template => template.category === 'rf_config' &&
        (templateReference(template.path) === templateReference(rfConfig) || templateReference(template.name) === templateReference(rfConfig)));
      const table = selected?.parsed.hopping_table ?? selected?.parsed.radio_config?.hopping_table;
      if (Array.isArray(table) && table.length) {
        const frequencies = table.map(item => typeof item === 'object' ? item.frequency_hz : item);
        if (frequencies.every(frequency => Number.isInteger(frequency) && frequency > 0)) return frequencies as number[];
      }
    }
    if (mode !== 'live' && simData?.frequencies && simData.frequencies.length > 0) return simData.frequencies;
    return DEFAULT_DK32S_HOPPING_TABLE;
  }, [mode, rfConfig, templates.data, simData]);

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
    const timer = (mode === 'simulate' || (linked && mode === 'live')) ? setTimeout(() => { void handleRunSimulation(); }, 250) : undefined;
    return () => {
      clearTimeout(timer);
      ++simulationVersion.current;
      ++playbackVersion.current;
    };
  }, [mode, linked, frameDraft, dwellMs, payloadBytes, simDriftPpm, simCycles, rfConfig]);

  // Both sync metric.sample and duplex.status are retained by the socket store.
  useEffect(() => {
    if (mode !== 'live' || !currentRunId || !liveMetric) return;
    const p = liveMetric;
    if (p.state !== undefined || p.clock_drift_ppm !== undefined) {
      setLiveState((prev) => ({
        time_s: p.time_s ?? p.hw_time_s ?? prev.time_s,
        state: (p.state as any) ?? prev.state,
        confidence: p.confidence ?? prev.confidence,
        slot_index: p.slot_index ?? p.slot ?? prev.slot_index,
        frequency_hz: p.frequency_hz ?? prev.frequency_hz,
        next_slot_index: p.next_slot_index ?? (((p.slot_index ?? prev.slot_index) + 1) % hoppingTable.length),
        next_frequency_hz: p.next_frequency_hz ?? hoppingTable[((p.slot_index ?? prev.slot_index) + 1) % hoppingTable.length],
        slot_remaining_s: p.slot_remaining_s ?? prev.slot_remaining_s,
        tdd_phase: p.tdd_phase ?? prev.tdd_phase,
        clock_drift_ppm: p.clock_drift_ppm ?? prev.clock_drift_ppm,
        decoded_frames: p.decoded_frames ?? prev.decoded_frames,
        overflow_count: p.overflow_count ?? prev.overflow_count,
        windows_skipped: p.windows_skipped ?? prev.windows_skipped,
        skipped_seconds: p.skipped_seconds ?? prev.skipped_seconds,
        windows_queued: p.windows_queued ?? prev.windows_queued,
        queue_delay_seconds: p.queue_delay_seconds ?? prev.queue_delay_seconds,
        dsp_load_ratio: p.dsp_load_ratio ?? prev.dsp_load_ratio,
        anchor_count: p.anchor_count ?? prev.anchor_count,
        lock_duration_s: p.lock_duration_s ?? prev.lock_duration_s,
      }));

      if (p.clock_drift_ppm !== undefined) {
        setDriftHistory((prev) => [
          ...prev.slice(-40),
          { time: p.time_s ?? p.hw_time_s ?? Date.now() / 1000, drift: p.clock_drift_ppm },
        ]);
      }
    }
  }, [liveMetric, mode, currentRunId, hoppingTable]);

  useEffect(() => {
    if (!latestEvent || !currentRunId || latestEvent.run_id !== currentRunId) return;
    if (['task.state', 'task.completed'].includes(latestEvent.type)) {
      const state = latestEvent.payload?.state;
      if (state && ['SUCCEEDED', 'FAILED', 'CANCELLED'].includes(state)) setCurrentRunId(null);
    }
  }, [latestEvent, currentRunId, setCurrentRunId]);

  // Simulation step timer
  useEffect(() => {
    if (mode !== 'simulate' || !simPlaying || !simData?.simulated_slots?.length) return;
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
  }, [mode, simPlaying, simData, dwellMs]);

  // Simulation execution handler
  const handleRunSimulation = async () => {
    const version = ++simulationVersion.current;
    try {
      const res = await api.simulateSyncSchedule({
        frequencies: DEFAULT_DK32S_HOPPING_TABLE,
        dwell_ms: dwellMs,
        tdd_mode: 'reception_triggered',
        payload_bytes: payloadBytes,
        bit_rate: frameDraft.bitRate,
        coding_rate: codingRate,
        clock_drift_ppm: simDriftPpm,
        cycles: simCycles,
        rf_config: rfConfig.trim() || undefined,
        frame: linked ? linkedFrameRequest(frameDraft) : undefined,
      });
      if (version !== simulationVersion.current) return;
      setSimData(res);
      setLinkError(null);
      if (mode !== 'simulate') return;
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
      if (version === simulationVersion.current) { setLinkError(err.message || '时序验证失败'); setSimData(null); }
    }
  };

  // Playback execution handler
  const handleParsePlayback = async (runId?: string, filePath?: string) => {
    const version = ++playbackVersion.current;
    try {
      const res = await api.parseSyncTimeline({
        run_id: runId,
        file_path: filePath || (runId ? undefined : playbackFile),
      });
      if (version !== playbackVersion.current) return;
      setParsedTimeline(res);
      if (res.latest_snapshot) {
        const snap = res.latest_snapshot;
        setLiveState({
          time_s: snap.hw_time_s || 0,
          state: snap.state || 'locked',
          confidence: snap.confidence ?? 0.98,
          slot_index: snap.slot_index || 0,
          frequency_hz: snap.frequency_hz || DEFAULT_DK32S_HOPPING_TABLE[0],
          next_slot_index: snap.next_slot_index ?? 1,
          next_frequency_hz: snap.next_frequency_hz || DEFAULT_DK32S_HOPPING_TABLE[1],
          slot_remaining_s: snap.slot_remaining_s ?? 0.05,
          tdd_phase: snap.tdd_phase || 'ground_tx',
          clock_drift_ppm: snap.clock_drift_ppm || 0,
          decoded_frames: res.total_frames,
          overflow_count: 0,
          anchor_count: snap.anchor_count ?? 10,
          lock_duration_s: snap.lock_duration_s ?? 1.0,
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
  const handleStartLiveSync = async (restart = false) => {
    if (runAction.current || (currentRunId && !restart)) return;
    if (parameterError) { setLinkError(parameterError); setShowConfig(true); return; }
    const draft = useRadioWorkbenchStore.getState();
    let request: ReturnType<typeof syncWorkbenchRequest>;
    try { request = syncWorkbenchRequest(draft.frame, draft.sync); }
    catch (error) { setLinkError((error as Error).message); setShowConfig(true); return; }
    runAction.current = true;
    setRunActionPending('start');
    setLinkError(null);
    try {
      if (restart && currentRunId) {
        await api.stopRun(currentRunId);
        const previous = await api.getRun(currentRunId);
        if (!['SUCCEEDED', 'FAILED', 'CANCELLED'].includes(previous.status)) throw new Error('旧任务尚未完成停止，参数未应用');
        setCurrentRunId(null);
      }
      const run = await api.createRun(request.commandId, request.parameters, true,
        request.commandId === 'duplex' ? 'PHY 帧联动 · FHSS/TDD 同步收发' : `Live FHSS+TDD Sync (${draft.sync.sdr.toUpperCase()})`);
      if (request.commandId === 'duplex') useRadioWorkbenchStore.getState().setDuplexRunId(run.id);
      setCurrentRunId(run.id);
      setSelectedRunId(run.id);
    } catch (err: any) {
      setLinkError(`启动同步失败: ${err.message}`);
    } finally {
      runAction.current = false;
      setRunActionPending(null);
    }
  };

  // Stop Live Sync Run
  const handleStopLiveSync = async () => {
    if (!currentRunId || runAction.current) return;
    runAction.current = true;
    setRunActionPending('stop');
    try {
      await api.stopRun(currentRunId);
      setCurrentRunId(null);
    } catch (err: any) {
      setLinkError(`停止同步失败: ${err.message}`);
    } finally {
      runAction.current = false;
      setRunActionPending(null);
    }
  };

  // Dynamic CLI command string
  const cliCommand = useMemo(() => {
    if (!runDraft.request) return runDraft.error;
    const args = ['flrc', runDraft.request.commandId];
    for (const [key, value] of Object.entries(runDraft.request.parameters)) {
      if (value === false || value === undefined || value === null || value === '') continue;
      args.push(`--${key.replace(/_/g, '-')}`);
      if (value !== true) args.push(String(value));
    }
    return argvPreview(args);
  }, [runDraft]);

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

  const [copiedHops, setCopiedHops] = useState(false);

  const handleCopyHoppingTable = () => {
    navigator.clipboard.writeText(JSON.stringify(hoppingTable));
    setCopiedHops(true);
    setTimeout(() => setCopiedHops(false), 2000);
  };

  const handleExportSyncReport = () => {
    const reportData = {
      mode,
      timestamp: new Date().toISOString(),
      liveState,
      hoppingTable,
      timingBreakdown,
      simData,
      parsedTimeline,
      driftHistory: driftHistory.slice(-50),
    };
    const blob = new Blob([JSON.stringify(reportData, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `sync_analysis_${mode}_${Date.now()}.json`;
    a.click();
    URL.revokeObjectURL(url);
  };

  // State badge styling
  const getStateBadge = (state: string) => {
    switch (state.toLowerCase()) {
      case 'idle':
        return {
          bg: 'bg-zinc-800/60 text-zinc-400 border-zinc-700',
          dot: 'bg-zinc-500',
          label: '待机（未启动）',
        };
      case 'starting':
        return {
          bg: 'bg-indigo-500/15 text-indigo-300 border-indigo-500/30',
          dot: 'bg-indigo-400 animate-pulse',
          label: '正在启动采集',
        };
      case 'waiting':
        return {
          bg: 'bg-indigo-500/15 text-indigo-300 border-indigo-500/30',
          dot: 'bg-indigo-400 animate-pulse',
          label: '等待同步状态',
        };
      case 'stopping':
        return {
          bg: 'bg-amber-500/15 text-amber-300 border-amber-500/30',
          dot: 'bg-amber-400 animate-pulse',
          label: '正在停止',
        };
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

  const displayState = mode === 'live'
    ? runActionPending === 'stop' ? 'stopping'
      : currentRunId ? liveMetric ? liveState.state : 'waiting'
      : runActionPending === 'start' ? 'starting' : 'idle'
    : (mode === 'simulate' ? simData : parsedTimeline) ? liveState.state : 'idle';
  const badge = getStateBadge(displayState);

  return (
    <div className="relative h-full min-h-0 overflow-hidden bg-rf-bg text-rf-text">
    <div className="h-full overflow-y-auto p-4 md:p-6 space-y-6">
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
        <div className="flex items-center gap-2 self-start md:self-auto flex-wrap">
          <button
            type="button"
            onClick={handleExportSyncReport}
            className="px-3 py-1.5 rounded-lg bg-rf-panel hover:bg-rf-border text-zinc-300 border border-rf-border text-xs font-medium transition-all flex items-center gap-1.5 shadow"
            title="导出当前时序仿真与跳频分析快照为 JSON 文件"
          >
            <Download className="w-3.5 h-3.5 text-cyan-400" />
            <span>导出时序报告</span>
          </button>
          <div className="flex items-center bg-rf-surface border border-rf-border rounded-xl p-1 gap-1">
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
      </div>

      <section aria-label="实机同步控制" className="sticky top-0 z-20 shrink-0 flex flex-wrap items-center justify-between gap-3 rounded-xl border border-indigo-500/30 bg-rf-surface/95 px-4 py-3 backdrop-blur">
        <div className="min-w-0 space-y-1">
          <h2 className="flex items-center gap-2 text-sm font-semibold text-zinc-200"><Radio className="h-4 w-4 text-indigo-400" />实机同步采集</h2>
          <p role="status" className="text-xs text-zinc-400">
            {runActionPending === 'stop'
              ? '正在停止采集并释放设备。'
              : currentRunId
              ? `${mode === 'live' ? '' : '后台'}同步任务已启动，可随时停止或查看任务记录。`
              : mode === 'live'
                ? '尚未启动采集。点击启动后连接 SDR，开始接收并建立同步。'
                : `当前为${mode === 'simulate' ? '时序仿真' : '历史回放'}，切换到实机运行后可启动 SDR 采集。`}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {currentRunId && <button type="button" onClick={() => { setSelectedRunId(currentRunId); setActiveTab('tasks'); }}
            className="rounded-lg border border-rf-border px-3 py-2 text-xs text-zinc-300 hover:bg-rf-panel">查看同步任务</button>}
          <button type="button" ref={parameterTrigger} onClick={() => showConfig ? closeParameters() : setShowConfig(true)}
            aria-expanded={showConfig} aria-controls="sync-parameter-drawer"
            className="rounded-lg border border-rf-border px-3 py-2 text-xs text-zinc-300 hover:bg-rf-panel">
            {showConfig ? '关闭参数' : '参数设置'}
          </button>
          {mode === 'live' && currentRunId && activeConfigurationChanged && (
            <button type="button" onClick={() => void handleStartLiveSync(true)} disabled={runActionPending !== null || Boolean(parameterError || runDraft.error)}
              className="rounded-lg border border-amber-600 bg-amber-500/10 px-3 py-2 text-xs text-amber-300 disabled:opacity-50">应用参数并重启</button>
          )}
          {currentRunId || runActionPending === 'stop' ? (
            <button type="button" onClick={handleStopLiveSync} disabled={runActionPending !== null}
              className="flex items-center gap-1.5 rounded-lg bg-rose-600 px-4 py-2 text-xs font-medium text-white hover:bg-rose-500 disabled:opacity-50">
              <Square className="h-3.5 w-3.5" /><span>{runActionPending ? '正在停止…' : '停止同步采集'}</span>
            </button>
          ) : mode === 'live' ? (
            <button type="button" onClick={() => void handleStartLiveSync()} disabled={runActionPending !== null || Boolean(parameterError || runDraft.error)}
              className="flex items-center gap-1.5 rounded-lg bg-indigo-600 px-4 py-2 text-xs font-medium text-white hover:bg-indigo-500 disabled:opacity-50">
              <Play className="h-3.5 w-3.5" /><span>{runActionPending ? '正在启动…' : operation === 'duplex' ? '启动同步收发会话' : '启动实时同步采集'}</span>
            </button>
          ) : (
            <button type="button" onClick={() => { setMode('live'); setShowConfig(true); }}
              className="flex items-center gap-1.5 rounded-lg bg-indigo-600 px-4 py-2 text-xs font-medium text-white hover:bg-indigo-500">
              <Radio className="h-3.5 w-3.5" /><span>切换到实机运行</span>
            </button>
          )}
        </div>
      </section>

      {(linkError || (mode === 'live' && (parameterError || runDraft.error))) && (
        <p role="alert" className="rounded-lg border border-amber-600/40 bg-amber-500/10 px-4 py-3 text-xs text-amber-300">
          {linkError || parameterError || runDraft.error}。请打开“参数设置”调整。
        </p>
      )}
      {mode === 'live' && activeConfigurationChanged && <p role="status" className="text-xs text-amber-300">参数已修改，当前任务仍使用原配置。点击“应用参数并重启”后生效。</p>}

      <section aria-label="物理帧与同步工作台联动" className="rounded-xl border border-cyan-500/30 bg-cyan-500/5 p-4 space-y-3 text-xs">
        <div className="font-semibold text-sm text-cyan-200">物理帧与同步收发联动</div>
        <p className="text-zinc-300">当前帧：{frameDraft.syncWord} · {frameDraft.bitRate / 1000} kbps · CR {codingRate} · {frameDraft.headerType} 头</p>
        <p className="text-zinc-400">时序验证采用当前载荷的编码、FEC 和爬升时间。实时联动使用 USRP / DK32S 同步模型，先监听锁定，再手动投递或编排。</p>
        <div className="flex flex-wrap gap-2 items-center">
          <button type="button" className="px-3 py-1.5 border border-cyan-700 rounded text-cyan-200"
            onClick={() => setActiveTab('transmit')}>编辑物理帧与载荷</button>
          <button type="button" className="px-3 py-1.5 border border-cyan-700 rounded text-cyan-200"
            onClick={() => useRadioWorkbenchStore.getState().linkToSync('simulate')}>关联当前帧并验证时序</button>
          {linked && <button type="button" className="px-3 py-1.5 border border-rf-border rounded text-zinc-300"
            onClick={() => useRadioWorkbenchStore.getState().unlink()}>独立时序预览</button>}
        </div>
        <p className="text-zinc-500">预览与会话使用同一跳频表；实时收发支持 DK32S 变长头 / CRC24，采用锁定的硬件时钟和 50 ms 时隙。仿真驻留时长不修改实时模型。</p>
        {linked && simData?.frame_summary && <p className="text-cyan-200">
          关联帧 {simData.frame_summary.tx_payload_bytes} B · TX {simData.frame_summary.tx_duration_us.toFixed(2)} µs ·
          预期回复 {simData.frame_summary.reply_payload_bytes} B / {simData.frame_summary.reply_duration_us.toFixed(2)} µs ·
          时隙余量 {simData.timing_breakdown.slot_guard_ms.toFixed(3)} ms
        </p>}
      </section>

      {/* 核心指标与仪表状态条 */}
      <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
        {/* 1. 同步锁定状态 */}
        <div className="bg-rf-surface border border-rf-border rounded-xl p-3.5 flex flex-col justify-between">
          <div className="flex items-center justify-between text-xs text-zinc-400">
            <span>{mode === 'live' ? '实机同步状态' : mode === 'simulate' ? '仿真同步状态' : '回放同步状态'}</span>
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

      {mode === 'live' && currentRunId && <div className={`rounded-lg border px-3 py-2 text-xs leading-5 ${(liveState.windows_skipped ?? 0) > 0
        ? 'border-amber-500/30 bg-amber-500/10 text-amber-200' : 'border-rf-border text-zinc-400'}`}>
        待解调 {liveState.windows_queued ?? 0} 窗口 · 排队 {((liveState.queue_delay_seconds ?? 0) * 1000).toFixed(0)} ms · DSP 耗时/窗口步长 {(liveState.dsp_load_ratio ?? 0).toFixed(2)}
        {(liveState.windows_skipped ?? 0) > 0 && <span className="block">过载累计跳过 {liveState.windows_skipped} 个窗口（{(liveState.skipped_seconds ?? 0).toFixed(2)} 秒），该区间可能漏帧；已触发同步重新捕获。</span>}
      </div>}

      {mode === 'live' && sessionRun?.command_id === 'duplex' && <div className="rounded-xl border border-rf-border overflow-hidden">
        <DuplexSessionPanel key={`${sessionRun.id}:${sessionRun.attempt}`} run={sessionRun} requireLock
          initialPayloadHex={linkedFrameRequest(frameDraft).payload_hex}
          blockedReason={configurationChanged ? '关联的物理或硬件参数已修改，请停止当前会话并重新启动以应用新配置。' : undefined} />
      </div>}

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
        <div className="flex items-center justify-between flex-wrap gap-2">
          
          <div className="flex items-center gap-2">
            <Layers className="w-4 h-4 text-indigo-400" />
            <h3 className="text-sm font-semibold text-zinc-100">
              FHSS 跳频序列图案矩阵 (Hopping Sequence Matrix · 20 Slots)
            </h3>
          </div>
          <div className="flex items-center gap-3">
            <button
              type="button"
              onClick={handleCopyHoppingTable}
              className="px-2.5 py-1 text-[11px] bg-rf-panel hover:bg-rf-border text-zinc-300 border border-rf-border rounded flex items-center space-x-1 transition-colors font-mono"
              title="复制 20 频点跳频表 JSON 数组"
            >
              {copiedHops ? <Check className="w-3 h-3 text-emerald-400" /> : <Copy className="w-3 h-3 text-cyan-400" />}
              <span>{copiedHops ? '已复制跳频表' : '复制跳频表'}</span>
            </button>
            <span className="text-xs text-zinc-400 font-mono">
            周期循环长度: {hoppingTable.length * dwellMs} ms
          </span>
          </div>
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
    {showConfig && (
      <aside id="sync-parameter-drawer" ref={parameterDrawer} tabIndex={-1} aria-label="同步参数设置" aria-modal={false} role="dialog"
        onKeyDown={event => { if (event.key === 'Escape') { event.stopPropagation(); closeParameters(); } }}
        className="absolute inset-y-0 right-0 z-40 flex w-[min(36rem,100%)] flex-col border-l border-rf-border bg-rf-surface shadow-[-12px_0_32px_rgba(0,0,0,0.35)] outline-none">
        <div className="flex shrink-0 items-center justify-between gap-3 border-b border-rf-border px-4 py-3">
          <div><h2 className="text-sm font-semibold text-zinc-100">参数设置</h2>
            <p className="mt-1 text-xs text-zinc-400">{mode === 'live' ? 'SDR 采集、物理层及同步收发配置' : mode === 'simulate' ? '时序仿真配置' : '历史文件与任务回放'}</p>
          </div>
          <button type="button" onClick={closeParameters} aria-label="关闭参数抽屉" className="rounded-md p-2 text-zinc-400 hover:bg-rf-panel hover:text-zinc-100"><X className="h-4 w-4" /></button>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto p-4 space-y-4">
            {mode === 'simulate' && (
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
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
                    {linked ? '预期回复载荷 (字节)' : '载荷长度 (字节)'}
                  </label>
                  <input
                    type="number"
                    min="6"
                    max="127"
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

            {mode === 'live' && <SyncLiveParameters onValidationChange={setParameterError} />}
            {mode === 'playback' && (
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
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

          {mode !== 'live' && <SyncTemplateSelector kind="rf" />}
          {(parameterError || (mode === 'live' && runDraft.error)) && <p role="alert" className="text-xs leading-5 text-amber-300">{parameterError || runDraft.error}</p>}
        </div>
        <div className="shrink-0 space-y-2 border-t border-rf-border bg-rf-panel/60 px-4 py-3">
          <p className="text-xs text-zinc-400">{currentRunId ? '修改保存在草稿中，应用时先停止旧采集，再按新参数重新启动。' : '参数自动保留为草稿，启动采集时生效。'}</p>
          <div className="flex flex-wrap justify-end gap-2">
            <button type="button" onClick={closeParameters} className="rounded-lg border border-rf-border px-3 py-2 text-xs text-zinc-300">收起抽屉</button>
            {mode === 'live' && <button type="button" onClick={() => void handleStartLiveSync(Boolean(currentRunId))}
              disabled={runActionPending !== null || Boolean(parameterError || runDraft.error) || Boolean(currentRunId && !activeConfigurationChanged)}
              className="rounded-lg bg-indigo-600 px-3 py-2 text-xs text-white hover:bg-indigo-500 disabled:opacity-50">
              {runActionPending ? '正在处理…' : currentRunId ? '应用参数并重启' : '启动同步采集'}
            </button>}
          </div>
        </div>
      </aside>
    )}
    </div>
  );
};

export default SyncWorkbenchPanel;
