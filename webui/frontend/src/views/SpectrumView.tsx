import { hexText } from '../utils/hex';
import React, { useState, useEffect, useRef, useMemo, useCallback } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
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
  Cpu,
  Layers,
  Sliders,
  AlertTriangle,
  Flame,
  Gauge,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  Settings2,
  Loader2,
  Disc,
  CircleDot,
  CheckCircle2,
  ArrowRight,
  Activity,
  X,
  Scan,
  Target,
  RotateCcw,
  Clock,
  Maximize2,
  FileCode,
  Braces,
  Trash2,
  Download,
  ChevronUp,
  Zap,
  MousePointer2,
} from 'lucide-react';
import { api } from '../api/client';
import { useWorkstationStore } from '../stores/useWorkstationStore';
import { useSpectrumDemodStore, type SpectrumDemodRecord } from '../stores/useSpectrumDemodStore';
import { spectrumProtocolDraft } from '../utils/protocolSources';
import { HardwareDeviceSelector } from '../components/HardwareDeviceSelector';
import { SpectrumFileInput } from '../components/SpectrumFileInput';
import { SpectrumFrequencyTuner } from '../components/SpectrumFrequencyTuner';
import { SpectrumStatusBar } from '../components/SpectrumStatusBar';
import { ProtocolDissectorPanel } from '../components/ProtocolDissectorPanel';
import { frameKey } from '../utils/frameSelection';
import { SpectrumDemodBand } from '../components/SpectrumDemodBand';
import { clockedSampleRateOptions, normalizeClockedSampleRate } from '../utils/sampleRates';
import { connectSpectrumStream } from '../utils/spectrumStream';
import { fitDemodBand } from '../utils/spectrumDemod';
import {
  buildRealtimeSpectrumOption,
  renderWaterfallRow,
  waterfallTransition,
  SPECTRUM_PLOT_INSETS,
  findNearestHistoryFrame,
  calculateWaterfallSelection,
  analyzeWindowFrames,
} from '../utils/spectrumCharts';
import type { WaterfallSelection, WindowAnalysisResult } from '../utils/spectrumCharts';
import type {
  SpectrumFrame,
  SpectrumStartRequest,
  SpectrumStatusResponse,
  SpectrumUpdateRequest,
  CommandDefinition,
  DecodedFrameRecord,
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

function formatFrameTime(timestamp: number): string {
  const d = new Date(timestamp * 1000);
  const timeStr = d.toLocaleTimeString('zh-CN', { hour12: false });
  const tenths = Math.floor(d.getMilliseconds() / 100);
  return `${timeStr}.${tenths}`;
}

export const SpectrumView: React.FC<{ isActive?: boolean }> = ({ isActive = true }) => {
  const queryClient = useQueryClient();
  // 1. 轮询设备状态，共享缓存供物理设备选择组件使用
  useQuery({
    queryKey: ['devices'],
    queryFn: api.getDevices,
    refetchInterval: 5000,
  });

  // 2. 表单配置状态
  const [sdr, setSdr] = useState<'hackrf' | 'usrp' | 'file'>('hackrf');
  const [iqPath, setIqPath] = useState('');
  const [iqFormat, setIqFormat] = useState<'cs8' | 'cu8' | 'cf32'>('cs8');
  const [fileUploading, setFileUploading] = useState(false);
  const spectrumTargetFreqHz = useWorkstationStore(state => state.spectrumTargetFreqHz);
  const setSpectrumTargetFreqHz = useWorkstationStore(state => state.setSpectrumTargetFreqHz);
  const [centerFreqHz, setCenterFreqHz] = useState<number>(2480000000);

  useEffect(() => {
    if (spectrumTargetFreqHz) {
      setCenterFreqHz(spectrumTargetFreqHz);
      setSpectrumTargetFreqHz(null);
    }
  }, [spectrumTargetFreqHz, setSpectrumTargetFreqHz]);
  const centerFreqMhz = centerFreqHz / 1e6;
  const setCenterFreqMhz = (mhz: number) => setCenterFreqHz(Math.round(mhz * 1e6));
  const [sampleRate, setSampleRate] = useState(8000000);
  const [nFft, setNFft] = useState(1024);
  const [windowFunc, setWindowFunc] = useState<'hann' | 'hamming' | 'blackman' | 'rectangular'>('hann');
  const [fftAverage, setFftAverage] = useState(4);
  const [fpsLimit, setFpsLimit] = useState(15);
  const [dynamicRangeDb, setDynamicRangeDb] = useState(100);
  const [refLevelDbfs, setRefLevelDbfs] = useState(0);
  const [settingsOpen, setSettingsOpen] = useState(false);

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
  const [isStopping, setIsStopping] = useState(false);
  const [actionError, setActionError] = useState('');
  const [connectionError, setConnectionError] = useState('');
  const [activeRequest, setActiveRequest] = useState<SpectrumStartRequest | null>(null);
  const [waterfallNewest, setWaterfallNewest] = useState<number | null>(null);
  const [frequencyZoom, setFrequencyZoom] = useState({ start: 0, end: 100 });

  // 实时突发录制状态
  const [recordDurationS, setRecordDurationS] = useState<number | null>(2.0);
  const [recordPrefix, setRecordPrefix] = useState('spec_live');
  const [isRecording, setIsRecording] = useState(false);
  const [recordElapsed, setRecordElapsed] = useState(0);
  const [recordedBytes, setRecordedBytes] = useState(0);
  const [recordResult, setRecordResult] = useState<import('../types').SpectrumRecordStatus | null>(null);
  const [recordError, setRecordError] = useState('');
  const chartEvents = useMemo(() => ({
    datazoom: (event: { start?: number; end?: number; batch?: { start?: number; end?: number }[] }) => {
      const range = event.batch?.[0] ?? event;
      if (typeof range.start !== 'number' || typeof range.end !== 'number') return;
      const start = Math.max(0, Math.min(100, range.start));
      const end = Math.max(0, Math.min(100, range.end));
      if (end > start) setFrequencyZoom(previous => previous.start === start && previous.end === end ? previous : { start, end });
    },
  }), []);

  // 瀑布图引用
  const waterfallCanvasRef = useRef<HTMLCanvasElement | null>(null);
  const waterfallContainerRef = useRef<HTMLDivElement | null>(null);
  const peakHoldRef = useRef<number[] | null>(null);
  peakHoldRef.current = peakHoldPowers;

  // 历史回溯与时空框选
  const historyFramesRef = useRef<SpectrumFrame[]>([]);
  const [pinnedFrame, setPinnedFrame] = useState<SpectrumFrame | null>(null);
  const [hoverSlice, setHoverSlice] = useState<{
    frame: SpectrumFrame;
    timeOffsetS: number;
    freqMhz: number;
    powerDb: number;
    xPct: number;
    yPct: number;
  } | null>(null);
  const [isDragging, setIsDragging] = useState(false);
  const [dragStart, setDragStart] = useState<{ x: number; y: number } | null>(null);
  const [dragCurrent, setDragCurrent] = useState<{ x: number; y: number } | null>(null);
  const [selection, setSelection] = useState<(WaterfallSelection & {
    sessionId: string; sampleRate: number; centerFreq: number;
  }) | null>(null);
  const [isExportingSelection, setIsExportingSelection] = useState(false);
  const selectionExportRunningRef = useRef(false);
  const [windowAnalysis, setWindowAnalysis] = useState<WindowAnalysisResult | null>(null);

  // 内置实时解调状态
  const [demodEnabled, setDemodEnabled] = useState(false);
  const [demodOffsetHz, setDemodOffsetHz] = useState(0);
  const [demodProtocol, setDemodProtocol] = useState<string>('auto');
  const [demodBitRate, setDemodBitRate] = useState<number | undefined>(undefined);
  const [demodCodingRate, setDemodCodingRate] = useState<'1/2' | '3/4' | '1' | undefined>(undefined);
  const [demodSyncWord, setDemodSyncWord] = useState<string>('');
  const [demodCrcInitial, setDemodCrcInitial] = useState<string>('');
  const [demodPhysicalTemplate, setDemodPhysicalTemplate] = useState('');
  const [isApplyingDemod, setIsApplyingDemod] = useState(false);
  const [showDemodBand, setShowDemodBand] = useState(true);
  const [isTuningDemod, setIsTuningDemod] = useState(false);
  const templates = useQuery({ queryKey: ['templates'], queryFn: () => api.getTemplates(), staleTime: 60000 });
  const physicalTemplates = templates.data?.filter(template => template.category === 'physical_config'
    && template.format === 'toml' && template.parsed.demod) || [];
  const selectedPhysicalTemplate = physicalTemplates.find(template => template.name === demodPhysicalTemplate);
  const templateCutoff = Number(selectedPhysicalTemplate?.parsed.demod.filter_cutoff
    ?? (!selectedPhysicalTemplate && demodProtocol === 'h12' ? 750000 : 350000));
  const demodBandwidthHz = (Number.isFinite(templateCutoff) && templateCutoff > 0 ? templateCutoff : 350000) * 2;
  const isAcquiring = ['running', 'paused'].includes(status.state);
  // Retain stopped plots until the user edits their capture configuration.
  // During a retune, the overlay follows the displayed frame's center while
  // the receiver request follows the requested center, using the same offset.
  const displayFrame = latestFrame && (isAcquiring || (centerFreqHz === activeRequest?.center_freq
    && sampleRate === activeRequest?.sample_rate)) ? latestFrame : null;
  const displayCenterFreqHz = displayFrame?.center_freq ?? centerFreqHz;
  const displaySampleRate = displayFrame?.sample_rate ?? (isAcquiring ? status.actual_sample_rate ?? sampleRate : sampleRate);
  const demodCaptureView = {
    minOffsetHz: displayFrame ? displayFrame.freq_min - displayCenterFreqHz : -displaySampleRate / 2,
    maxOffsetHz: displayFrame ? displayFrame.freq_max - displayCenterFreqHz : displaySampleRate / 2,
  };
  const demodBand = fitDemodBand(demodOffsetHz, displaySampleRate, demodBandwidthHz, frequencyZoom, demodCaptureView);
  const demodCenterFreqHz = centerFreqHz + demodBand.offsetHz;
  const demodCenterFreqMhz = (displayCenterFreqHz + demodBand.offsetHz) / 1e6;
  const effectiveFrequencyZoom = useMemo(() => demodBand.zoom, [demodBand.zoom.start, demodBand.zoom.end]);

  useEffect(() => {
    if (demodOffsetHz !== demodBand.offsetHz) setDemodOffsetHz(demodBand.offsetHz);
    if (frequencyZoom.start !== demodBand.zoom.start || frequencyZoom.end !== demodBand.zoom.end) {
      setFrequencyZoom(demodBand.zoom);
    }
  }, [demodOffsetHz, demodBand.offsetHz, frequencyZoom.start, frequencyZoom.end, demodBand.zoom.start, demodBand.zoom.end]);
  const demodParameters = {
    physical_template: demodPhysicalTemplate || null,
    bit_rate: demodBitRate ?? null,
    coding_rate: demodCodingRate ?? null,
    sync_word: demodSyncWord.trim() || null,
    crc_initial: demodCrcInitial.trim() || null,
    protocol: demodProtocol,
  };
  const demodRecords = useSpectrumDemodStore(state => state.records);
  const demodPackets = useMemo(() => demodRecords.map(record => record.frame), [demodRecords]);
  const pendingDemodPacketsRef = useRef<SpectrumDemodRecord[]>([]);
  const demodProtocolRef = useRef(demodProtocol);
  demodProtocolRef.current = status.demod_protocol || demodProtocol;
  const [demodPaused, setDemodPaused] = useState(false);
  const [isDemodDrawerOpen, setIsDemodDrawerOpen] = useState(false);
  const [demodFilterText, setDemodFilterText] = useState('');
  const [selectedDissectRecord, setSelectedDissectRecord] = useState<SpectrumDemodRecord | null>(null);
  const selectedDissectFrame = selectedDissectRecord?.frame ?? null;
  const setSelectedDissectFrame = (frame: DecodedFrameRecord | null) => {
    setSelectedDissectRecord(frame ? demodRecords.find(record => record.frame === frame) ?? null : null);
  };

  const demodPausedRef = useRef(demodPaused);
  demodPausedRef.current = demodPaused;

  const lastFrameRef = useRef<SpectrumFrame | null>(null);
  const waterfallAdvanceRef = useRef(0);
  const mountedRef = useRef(true);
  const sessionIdRef = useRef(sessionId);
  sessionIdRef.current = sessionId;

  // 实时录制操作处理
  const applyRecordStatus = useCallback((result: import('../types').SpectrumRecordStatus) => {
    setIsRecording(result.is_recording && !result.error);
    setRecordElapsed(result.elapsed_s || 0);
    setRecordedBytes(result.recorded_bytes || 0);
    if (result.error) {
      setRecordError(result.error);
      setRecordResult(null);
    } else if (result.completed) {
      setRecordError('');
      setRecordResult(result);
    }
  }, []);

  const handleStartRecord = async () => {
    if (!sessionId || !['running', 'paused'].includes(status.state)) return;
    setRecordError('');
    setRecordResult(null);
    try {
      const res = await api.startSpectrumRecord(sessionId, recordDurationS, recordPrefix);
      applyRecordStatus(res);
    } catch (err: any) {
      setRecordError(err.message || '启动录制失败');
      setIsRecording(false);
    }
  };

  const handleStopRecord = async () => {
    if (!sessionId) return;
    try {
      const res = await api.stopSpectrumRecord(sessionId);
      applyRecordStatus(res);
    } catch (err: any) {
      setRecordError(err.message || '停止录制失败');
      setIsRecording(false);
    }
  };

  // 轮询录制进度
  useEffect(() => {
    if (!isRecording || !sessionId) return;
    let cancelled = false;
    const interval = setInterval(async () => {
      try {
        const st = await api.getSpectrumRecordStatus(sessionId);
        if (cancelled) return;
        applyRecordStatus(st);
        if (st.error || st.completed || !st.is_recording) {
          clearInterval(interval);
        }
      } catch {
        // ignore polling error
      }
    }, 250);
    return () => { cancelled = true; clearInterval(interval); };
  }, [isRecording, sessionId, applyRecordStatus]);

  const handleJumpToAnalysis = () => {
    if (recordResult?.run_id) {
      useWorkstationStore.getState().setSelectedRunId(recordResult.run_id);
    }
    if (recordResult?.filepath) {
      useWorkstationStore.getState().setSelectedArtifactPath(recordResult.filepath);
    }
    useWorkstationStore.getState().setActiveTab('signals');
  };

  const handleJumpToDemod = () => {
    useWorkstationStore.getState().setSelectedCommandId('demod');
    if (recordResult) {
      const existing = useWorkstationStore.getState().commandDrafts['demod']?.formData || {};
      useWorkstationStore.getState().saveCommandDraft('demod', {
        formData: {
          ...existing,
          iq: recordResult.filepath || '',
          sample_rate: recordResult.sample_rate || sampleRate,
          frequency: recordResult?.center_freq || centerFreqHz,
          iq_format: recordResult.iq_format || (sdr === 'hackrf' ? 'cs8' : 'fc32'),
        },
        taskName: `录制解调_${new Date().toLocaleTimeString().replace(/:/g, '')}`,
        manualOutputs: {},
      });
    }
    useWorkstationStore.getState().setActiveTab('commands');
  };

  // Restart-only settings stay disabled throughout capture, including display pause.
  const configurationLocked = isStarting || isStopping || isAcquiring;
  const tuningEnabled = isAcquiring && !isStarting && !isStopping;
  const tuningEnabledRef = useRef(tuningEnabled);
  tuningEnabledRef.current = tuningEnabled;
  const tuningAppliedRef = useRef<{ sessionId: string; parameters: SpectrumUpdateRequest } | null>(null);
  const tuningQueueRef = useRef(Promise.resolve());
  const tuningParameters = useMemo<SpectrumUpdateRequest>(() => ({
    center_freq: centerFreqHz,
    demod_center_freq: demodCenterFreqHz,
    ...(sdr === 'usrp' ? { gain } : sdr === 'hackrf' ? { lna_gain: lnaGain, vga_gain: vgaGain, amp } : {}),
    fft_average: fftAverage,
    fps_limit: fpsLimit,
    dynamic_range_db: dynamicRangeDb,
    ref_level_dbfs: refLevelDbfs,
  }), [centerFreqHz, demodCenterFreqHz, sdr, gain, lnaGain, vgaGain, amp, fftAverage, fpsLimit, dynamicRangeDb, refLevelDbfs]);

  useEffect(() => {
    if (!sessionId || !tuningEnabled || !activeRequest) { setIsTuningDemod(false); return; }
    if (tuningAppliedRef.current?.sessionId !== sessionId) {
      tuningAppliedRef.current = { sessionId, parameters: { ...activeRequest } };
    }
    const applied = tuningAppliedRef.current;
    // Do not send incomplete numeric input while a value is being typed.
    if (!Number.isFinite(centerFreqHz) || centerFreqHz <= 0
      || !Number.isFinite(refLevelDbfs) || refLevelDbfs < -240 || refLevelDbfs > 20
      || !Number.isFinite(dynamicRangeDb) || dynamicRangeDb < 20 || dynamicRangeDb > 140) return;
    let cancelled = false;
    const timer = window.setTimeout(() => {
      // Serialize receiver updates and discard superseded queued changes.
      tuningQueueRef.current = tuningQueueRef.current.then(async () => {
        if (cancelled || !mountedRef.current || !tuningEnabledRef.current || sessionIdRef.current !== sessionId) return;
        const patch = Object.fromEntries(Object.entries(tuningParameters)
          .filter(([key, value]) => value !== applied.parameters[key as keyof SpectrumUpdateRequest]));
        if (!Object.keys(patch).length) { setIsTuningDemod(false); return; }
        const retuning = 'center_freq' in patch || 'demod_center_freq' in patch;
        if (retuning) {
          patch.center_freq = centerFreqHz;
          patch.demod_center_freq = demodCenterFreqHz;
          setIsTuningDemod(true);
        }
        try {
          const result = await api.updateSpectrum(sessionId, patch);
          applied.parameters = { ...applied.parameters, ...patch };
          if (cancelled || !mountedRef.current || !tuningEnabledRef.current || sessionIdRef.current !== sessionId) return;
          if (result.status) setStatus(previous => ({ ...result.status!, state: previous.state }));
          setActionError('');
        } catch (error: any) {
          if (!cancelled && mountedRef.current && tuningEnabledRef.current && sessionIdRef.current === sessionId) {
            setActionError(`自动调节失败：${error.message}`);
          }
        } finally {
          if (!cancelled && mountedRef.current && sessionIdRef.current === sessionId) setIsTuningDemod(false);
        }
      });
    }, 250);
    return () => { cancelled = true; window.clearTimeout(timer); };
  }, [sessionId, tuningEnabled, activeRequest, tuningParameters, centerFreqHz, refLevelDbfs, dynamicRangeDb]);

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
        dsp_dropped_frames: frame.dsp_dropped_frames, client_dropped_frames: frame.client_dropped_frames,
        iq_history: frame.iq_history }));
      // Keep the time axis fixed while drawing a region; IQ reception continues.
      if (isPaused || isDragging) return;

      setLatestFrame(frame);

      const transition = waterfallTransition(lastFrameRef.current, frame);
      lastFrameRef.current = frame;
      setWaterfallNewest(frame.timestamp);
      if (transition.parametersChanged) {
        peakHoldRef.current = null;
        setPeakHoldPowers(null);
        historyFramesRef.current = [];
        setPinnedFrame(null);
        setSelection(null);
        setWindowAnalysis(null);
      }

      // 维护历史回溯滑动缓冲池 (~20 秒，400 帧)
      historyFramesRef.current.push(frame);
      if (historyFramesRef.current.length > 400) {
        historyFramesRef.current.shift();
      }

      // 更新峰值保持
      if (peakHoldEnabled && frame.powers && frame.powers.length) {
        const prev = transition.parametersChanged ? null : peakHoldRef.current;
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
      if (waterfallCanvasRef.current && frame.powers && (advanceRows > 0 || transition.parametersChanged)) {
        renderWaterfallRow(
          waterfallCanvasRef.current,
          frame.powers,
          refLevelDbfs,
          dynamicRangeDb,
          transition.gap,
          1,
          advanceRows
        );
      }
    },
    [isPaused, isDragging, peakHoldEnabled, refLevelDbfs, dynamicRangeDb]
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

    let packetTimer: number | undefined;
    const flushPackets = () => {
      packetTimer = undefined;
      if (!pendingDemodPacketsRef.current.length) return;
      const packets = pendingDemodPacketsRef.current;
      pendingDemodPacketsRef.current = [];
      if (!demodPausedRef.current) useSpectrumDemodStore.getState().addRecords(packets);
    };
    const disconnect = connectSpectrumStream({
      sessionId,
      url: wsUrl,
      onFrame: frame => frameHandlerRef.current(frame),
      onStatus: info => {
        if (sessionIdRef.current !== sessionId) return;
        setStatus(info);
        setIsPaused(info.state === 'paused');
        if (['stopped', 'idle', 'error'].includes(info.state)) {
          setDemodEnabled(false);
        } else if (info.demod_enabled !== undefined) {
          setDemodEnabled(info.demod_enabled);
        }
        setConnectionError('');
      },
      onDemodPacket: packet => {
        if (!demodPausedRef.current) {
          const packetWithId = packet._client_id
            ? packet
            : { ...packet, _client_id: `pkt_${Date.now()}_${Math.random().toString(36).slice(2, 9)}` };
          pendingDemodPacketsRef.current.unshift({ id: packetWithId._client_id, sessionId,
            frame: packetWithId, protocol: demodProtocolRef.current });
          pendingDemodPacketsRef.current.length = Math.min(pendingDemodPacketsRef.current.length, 500);
          if (packetTimer === undefined) packetTimer = window.setTimeout(flushPackets, 100);
        }
      },
      onConnectionError: setConnectionError,
    });
    return () => {
      disconnect();
      window.clearTimeout(packetTimer);
      flushPackets();
    };
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
    if (demodEnabled && !demodBand.available) {
      setActionError('采样带宽不足以容纳解调通带，请提高采样率。');
      return;
    }
    if (sdr === 'file' && (!iqPath.trim() || fileUploading)) {
      setActionError(fileUploading ? '请等待 IQ 文件上传完成。' : '请先选择或上传 IQ 文件。');
      return;
    }
    setIsStarting(true);
    setActionError('');
    try {
      const req: SpectrumStartRequest = {
        sdr,
        iq_path: sdr === 'file' ? iqPath.trim() : undefined,
        iq_format: sdr === 'file' ? iqFormat : undefined,
        center_freq: centerFreqHz,
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
        // 内置实时解调
        demod_enabled: demodEnabled,
        demod_center_freq: demodCenterFreqHz,
        ...demodParameters,
      };

      const res = await api.startSpectrum(req);
      if (!mountedRef.current) {
        if (res.session_id) await api.stopSpectrum(res.session_id);
        return;
      }
      sessionIdRef.current = res.session_id;
      tuningAppliedRef.current = res.session_id ? { sessionId: res.session_id, parameters: { ...req } } : null;
      setSessionId(res.session_id);
      setActiveRequest(req);
      setStatus(res);
      setIsPaused(false);
      setPeakHoldPowers(null);
      peakHoldRef.current = null;
      setLatestFrame(null);
      // Restarting acquisition keeps the waterfall history and its time progression.
    } catch (err: any) {
      if (sessionIdRef.current && ['running', 'paused'].includes(status.state)) {
        setActionError(`启动失败：${err.message}`);
      } else {
        setStatus((prev) => ({
          ...prev,
          state: 'error',
          error_code: 'START_FAILED',
          error_message: err.message || '启动采集失败',
        }));
      }
    } finally {
      setIsStarting(false);
    }
  };

  const handleStop = async () => {
    if (isStopping || (!sessionId && status.state === 'idle')) return;
    tuningEnabledRef.current = false;
    setIsStopping(true);
    try {
      const res = await api.stopSpectrum(sessionId || undefined);
      if (isRecording && sessionId && ['stopped', 'idle'].includes(res.state)) {
        try {
          applyRecordStatus(await api.getSpectrumRecordStatus(sessionId));
        } catch (error: any) {
          setIsRecording(false);
          setRecordError(`无法确认录制结果：${error.message}`);
        }
      }
      setStatus(res);
      setIsPaused(false);
      if (['stopped', 'idle'].includes(res.state)) {
        setDemodEnabled(false);
        setIsTuningDemod(false);
        sessionIdRef.current = null;
        setSessionId(null);
      }
    } catch (err: any) {
      setActionError(`停止失败：${err.message}`);
    } finally {
      setIsStopping(false);
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

  const handleResetPeakHold = () => {
    peakHoldRef.current = null;
    setPeakHoldPowers(null);
  };

  // 6. 构造 ECharts Option (含实时频谱、峰值保持、历史探针切片与选区分析包络)
  const spectrumOption = useMemo(() => {
    const freqMin = displayFrame?.freq_min ?? (displayCenterFreqHz - displaySampleRate / 2);
    const freqMax = displayFrame?.freq_max ?? (displayCenterFreqHz + displaySampleRate / 2);
    const rbw = displayFrame?.rbw_hz ?? displaySampleRate / nFft;
    const powers = displayFrame?.powers ?? [];

    const activeSlice = pinnedFrame || (hoverSlice?.frame ?? null);
    const historyPowers = activeSlice?.powers ?? null;
    const historyLabel = pinnedFrame
      ? `历史切片 [${formatFrameTime(pinnedFrame.timestamp)}]`
      : hoverSlice
      ? `探针切片 (${hoverSlice.timeOffsetS.toFixed(1)}s)`
      : undefined;

    const option = buildRealtimeSpectrumOption({
      freqMin,
      freqMax,
      rbwHz: rbw,
      powers,
      peakHoldPowers: peakHoldEnabled ? peakHoldPowers : null,
      historyPowers,
      historyLabel,
      windowAnalysisPowers: windowAnalysis ? { maxPowers: windowAnalysis.maxPowers, avgPowers: windowAnalysis.avgPowers } : null,
      refLevelDb: refLevelDbfs,
      dynamicRangeDb,
      peakFreq: latestFrame?.peak_freq,
      peakPower: latestFrame?.peak_power,
    });
    return { ...option, dataZoom: option.dataZoom.map(zoom => ({ ...zoom, ...effectiveFrequencyZoom })) };
  }, [
    displayFrame,
    displayCenterFreqHz,
    displaySampleRate,
    nFft,
    peakHoldPowers,
    peakHoldEnabled,
    pinnedFrame,
    hoverSlice,
    windowAnalysis,
    refLevelDbfs,
    dynamicRangeDb,
    effectiveFrequencyZoom,
  ]);

  const waterfallColumns = `${SPECTRUM_PLOT_INSETS.left}px minmax(0, 1fr) ${SPECTRUM_PLOT_INSETS.right}px`;
  const zoomSpan = effectiveFrequencyZoom.end - effectiveFrequencyZoom.start;
  const freqMinMhz = spectrumOption.xAxis.min;
  const freqSpanMhz = spectrumOption.xAxis.max - freqMinMhz;
  const visibleMinMhz = freqMinMhz + freqSpanMhz * effectiveFrequencyZoom.start / 100;
  const visibleMaxMhz = freqMinMhz + freqSpanMhz * effectiveFrequencyZoom.end / 100;

  // 历史回溯键盘导航快捷键支持
  useEffect(() => {
    if (!isActive) return;
    const handleKeyDown = (e: KeyboardEvent) => {
      if (['INPUT', 'SELECT', 'TEXTAREA'].includes((e.target as HTMLElement)?.tagName)) return;

      if (e.key === 'Escape') {
        if (selectedDissectFrame !== null) {
          setSelectedDissectFrame(null);
          return;
        }
        setSelection(null);
        setWindowAnalysis(null);
        setPinnedFrame(null);
      } else if (selectedDissectFrame !== null) {
        // 弹窗打开时，键盘左右箭头或 P/N 键可在正序时间轴上前后步进（左箭头/P: 查看上一帧/较早帧，右箭头/N: 查看下一帧/较新帧）
        if (e.key === 'ArrowLeft' || e.key === 'p' || e.key === 'P') {
          e.preventDefault();
          if (activeDissectIndex < filteredPackets.length - 1) {
            setSelectedDissectFrame(filteredPackets[activeDissectIndex + 1]);
          }
        } else if (e.key === 'ArrowRight' || e.key === 'n' || e.key === 'N') {
          e.preventDefault();
          if (activeDissectIndex > 0) {
            setSelectedDissectFrame(filteredPackets[activeDissectIndex - 1]);
          }
        }
        return;
      } else if (e.key === 'ArrowLeft') {
        if (!historyFramesRef.current.length) return;
        e.preventDefault();
        const curTs = pinnedFrame?.timestamp ?? (waterfallNewest || Date.now() / 1000);
        const curIdx = findNearestHistoryFrame(historyFramesRef.current, curTs);
        const nextIdx = Math.max(0, curIdx - 1);
        setPinnedFrame(historyFramesRef.current[nextIdx]);
      } else if (e.key === 'ArrowRight') {
        if (!historyFramesRef.current.length) return;
        e.preventDefault();
        const curTs = pinnedFrame?.timestamp ?? (waterfallNewest || Date.now() / 1000);
        const curIdx = findNearestHistoryFrame(historyFramesRef.current, curTs);
        const nextIdx = Math.min(historyFramesRef.current.length - 1, curIdx + 1);
        setPinnedFrame(historyFramesRef.current[nextIdx]);
      }
    };

    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [isActive, pinnedFrame, waterfallNewest]);

  const handleStepPrevFrame = () => {
    if (!historyFramesRef.current.length) return;
    const curTs = pinnedFrame?.timestamp ?? (waterfallNewest || Date.now() / 1000);
    const curIdx = findNearestHistoryFrame(historyFramesRef.current, curTs);
    const nextIdx = Math.max(0, curIdx - 1);
    setPinnedFrame(historyFramesRef.current[nextIdx]);
  };

  const handleStepNextFrame = () => {
    if (!historyFramesRef.current.length) return;
    const curTs = pinnedFrame?.timestamp ?? (waterfallNewest || Date.now() / 1000);
    const curIdx = findNearestHistoryFrame(historyFramesRef.current, curTs);
    const nextIdx = Math.min(historyFramesRef.current.length - 1, curIdx + 1);
    setPinnedFrame(historyFramesRef.current[nextIdx]);
  };

  const handleClearPinnedFrame = () => {
    setPinnedFrame(null);
  };

  const handleClearSelection = () => {
    setSelection(null);
    setWindowAnalysis(null);
  };

  const handleResetZoom = () => {
    setFrequencyZoom({ start: 0, end: 100 });
  };

  const handleAnalyzeSelection = () => {
    if (!selection) return;
    const analysis = analyzeWindowFrames(
      historyFramesRef.current,
      selection.timeStart,
      selection.timeEnd
    );
    setWindowAnalysis(analysis);
  };

  const handleZoomToSelection = () => {
    if (!selection) return;
    const totalSpan = freqSpanMhz;
    if (totalSpan <= 0) return;
    const startPct = Math.max(0, Math.min(100, ((selection.freqStartMhz - freqMinMhz) / totalSpan) * 100));
    const endPct = Math.max(0, Math.min(100, ((selection.freqEndMhz - freqMinMhz) / totalSpan) * 100));
    if (endPct - startPct > 0.5) {
      setFrequencyZoom({ start: Math.round(startPct * 10) / 10, end: Math.round(endPct * 10) / 10 });
    }
  };

  const handleTuneToSelection = () => {
    if (!selection) return;
    setCenterFreqMhz(selection.centerFreqMhz);
  };

  const handleSendToDemod = async () => {
    if (!selection || selectionExportRunningRef.current) return;
    selectionExportRunningRef.current = true;
    setIsExportingSelection(true);
    setActionError('');
    try {
      const targetFrequency = Math.round(selection.centerFreqMhz * 1e6);
      if (Math.abs(targetFrequency - selection.centerFreq) + templateCutoff >= selection.sampleRate / 2) {
        throw new Error('选区频点的解调通带超出采集带宽，请选择更靠近带内的频点');
      }
      const result = await api.exportSpectrumSelection(selection.sessionId, {
        time_start: selection.timeStart, time_end: selection.timeEnd,
        sample_rate: selection.sampleRate, center_frequency: selection.centerFreq,
        target_frequency: targetFrequency,
      });
      if (!mountedRef.current) return;
      if (!result.filepath || result.samples_count <= 0) throw new Error('选区未生成有效 IQ 文件');
      const workstation = useWorkstationStore.getState();
      const physicalTemplate = demodPhysicalTemplate || (demodProtocol === 'h12' ? 'physical/h12.toml' : '');
      workstation.saveCommandDraft('demod', {
        formData: {
          iq: result.filepath,
          sample_rate: result.sample_rate,
          center_frequency: result.center_frequency,
          frequency: result.target_frequency,
          iq_format: result.iq_format,
          config: physicalTemplate ? `templates/${physicalTemplate}` : '',
          bit_rate: demodBitRate ?? '', cr: demodCodingRate ?? '',
          sync: demodSyncWord.trim(), crc_initial: demodCrcInitial.trim(),
          protocol: ['dk32s', 'dk32s-multi', 'datalink'].includes(demodProtocol) ? demodProtocol : '',
          parser: '', max_seconds: 0,
        },
        taskName: `框选解调_${(result.target_frequency / 1e6).toFixed(3)}MHz`,
        manualOutputs: {},
      });
      void queryClient.invalidateQueries({ queryKey: ['spectrumFiles'] });
      if (workstation.activeTab === 'spectrum') {
        workstation.setSelectedCommandId('demod');
        workstation.setActiveTab('commands');
      }
    } catch (error: any) {
      if (mountedRef.current) setActionError(`选区 IQ 导出失败：${error.message}`);
    } finally {
      selectionExportRunningRef.current = false;
      if (mountedRef.current) setIsExportingSelection(false);
    }
  };

  // 内置实时解调控制处理
  const handleToggleDemodLive = async (enable: boolean) => {
    if (enable && !demodBand.available) {
      setActionError('采样带宽不足以容纳解调通带，请提高采样率。');
      return;
    }
    if (enable) {
      setShowDemodBand(true);
    }
    if (sessionId && ['running', 'paused'].includes(status.state)) {
      try {
        const updateRes = await api.updateSpectrum(sessionId, {
          demod_enabled: enable,
          center_freq: centerFreqHz,
          demod_center_freq: demodCenterFreqHz,
          ...demodParameters,
        });
        if (sessionIdRef.current !== sessionId) return;
        if (updateRes.status) setStatus(updateRes.status);
        setDemodEnabled(enable);
        if (enable) setIsDemodDrawerOpen(true);
        setActionError('');
      } catch (err: any) {
        setActionError(`切换实时解调失败: ${err.message}`);
      }
    } else {
      setDemodEnabled(enable);
      if (enable) setIsDemodDrawerOpen(true);
    }
  };

  const handleSelectPhysicalTemplate = async (name: string) => {
    setDemodPhysicalTemplate(name);
    setDemodBitRate(undefined);
    setDemodCodingRate(undefined);
    setDemodSyncWord('');
    setDemodCrcInitial('');
    const matchedProtocol = physicalTemplates.find(template => template.name === name)?.parsed.template?.protocol;
    const nextProtocol = ['auto', 'dk32s', 'dk32s-multi', 'datalink', 'h12', 'raw'].includes(matchedProtocol)
      ? matchedProtocol
      : demodProtocol;
    if (nextProtocol !== demodProtocol) {
      setDemodProtocol(nextProtocol);
    }
    const nextCutoff = Number(physicalTemplates.find(template => template.name === name)?.parsed.demod.filter_cutoff
      ?? (!name && nextProtocol === 'h12' ? 750000 : 350000));
    const nextBandwidth = (Number.isFinite(nextCutoff) && nextCutoff > 0 ? nextCutoff : 350000) * 2;
    const nextBand = fitDemodBand(demodBand.offsetHz, displaySampleRate, nextBandwidth, effectiveFrequencyZoom, demodCaptureView);
    setDemodOffsetHz(nextBand.offsetHz);
    setActionError('');

    if (sessionId && ['running', 'paused'].includes(status.state)) {
      if (isApplyingDemod) return;
      setIsApplyingDemod(true);
      try {
        const updateRes = await api.updateSpectrum(sessionId, {
          demod_enabled: demodEnabled,
          center_freq: centerFreqHz,
          demod_center_freq: centerFreqHz + nextBand.offsetHz,
          physical_template: name || null,
          bit_rate: null,
          coding_rate: null,
          sync_word: null,
          crc_initial: null,
          protocol: nextProtocol,
        });
        if (sessionIdRef.current !== sessionId) return;
        if (updateRes.status) setStatus(updateRes.status);
      } catch (err: any) {
        setActionError(`应用模板失败: ${err.message}`);
      } finally {
        setIsApplyingDemod(false);
      }
    }
  };

  const handleDragDemodFrequency = (frequencyMhz: number) => {
    setDemodOffsetHz(Math.round(frequencyMhz * 1e6) - displayCenterFreqHz);
  };

  const handleDemodSelection = async () => {
    if (!selection) return;
    if (!demodBand.available) { setActionError('采样带宽不足以容纳解调通带，请提高采样率。'); return; }
    const targetBand = fitDemodBand(Math.round(selection.centerFreqMhz * 1e6) - displayCenterFreqHz,
      displaySampleRate, demodBandwidthHz, effectiveFrequencyZoom, demodCaptureView);
    setDemodOffsetHz(targetBand.offsetHz);
    setDemodEnabled(true);
    setIsDemodDrawerOpen(true);
    if (sessionId && ['running', 'paused'].includes(status.state)) {
      try {
        const updateRes = await api.updateSpectrum(sessionId, {
          demod_enabled: true,
          center_freq: centerFreqHz,
          demod_center_freq: centerFreqHz + targetBand.offsetHz,
          ...demodParameters,
        });
        if (sessionIdRef.current !== sessionId) return;
        if (updateRes.status) setStatus(updateRes.status);
      } catch (err: any) {
        if (sessionIdRef.current !== sessionId) return;
        setDemodEnabled(status.demod_enabled ?? false);
        setActionError(`启动选区解调失败: ${err.message}`);
      }
    }
  };

  const handleExportPackets = () => {
    if (!demodPackets.length) return;
    const content = demodPackets.map(p => JSON.stringify(p)).join('\n');
    const blob = new Blob([content], { type: 'application/x-ndjson;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `flrc_live_packets_${new Date().toISOString().replace(/[:.]/g, '-')}.jsonl`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const handleClearDemodPackets = () => {
    pendingDemodPacketsRef.current = [];
    useSpectrumDemodStore.getState().clearRecords();
    setSelectedDissectFrame(null);
  };

  const openDemodProtocol = (frame: DecodedFrameRecord) => {
    const record = demodRecords.find(item => item.frame === frame)
      ?? (selectedDissectRecord?.frame === frame ? selectedDissectRecord : undefined);
    const draft = record && spectrumProtocolDraft(record);
    if (!draft) return;
    setSelectedDissectFrame(null);
    useWorkstationStore.getState().openProtocol(draft);
  };

  const filteredPackets = useMemo(() => {
    if (!demodFilterText.trim()) return demodPackets;
    const q = demodFilterText.trim().toLowerCase();
    return demodPackets.filter(pkt => {
      if (pkt.payload_hex && pkt.payload_hex.toLowerCase().includes(q)) return true;
      if (pkt.application?.protocol && pkt.application.protocol.toLowerCase().includes(q)) return true;
      if (pkt.application?.command_name && pkt.application.command_name.toLowerCase().includes(q)) return true;
      if (pkt.crc_ok !== undefined && (pkt.crc_ok ? 'crc_ok ok 成功 正确' : 'crc_err err 失败 错误').toLowerCase().includes(q)) return true;
      if (pkt.frequency_hz && String(pkt.frequency_hz).includes(q)) return true;
      return false;
    });
  }, [demodPackets, demodFilterText]);

  const activeDissectIndex = useMemo(() => {
    if (!selectedDissectFrame) return -1;
    return filteredPackets.findIndex(
      p =>
        (selectedDissectFrame._client_id && p._client_id === selectedDissectFrame._client_id) ||
        p === selectedDissectFrame ||
        frameKey(p) === frameKey(selectedDissectFrame)
    );
  }, [filteredPackets, selectedDissectFrame]);

  // 计算当前选定帧在时间正序流中的固定序号（0 为最早到达帧，保证后台有新帧流入时当前帧的前置序号绝对稳定不跳动）
  const chronologicalDissectIndex = useMemo(() => {
    if (activeDissectIndex < 0 || filteredPackets.length === 0) return 0;
    return filteredPackets.length - 1 - activeDissectIndex;
  }, [activeDissectIndex, filteredPackets.length]);

  const handleWaterfallMouseDown = (e: React.MouseEvent<HTMLDivElement>) => {
    if (e.button !== 0) return;
    if (!waterfallContainerRef.current) return;
    const rect = waterfallContainerRef.current.getBoundingClientRect();
    const x = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
    const y = Math.max(0, Math.min(1, (e.clientY - rect.top) / rect.height));

    setIsDragging(true);
    setDragStart({ x, y });
    setDragCurrent({ x, y });
  };

  const handleWaterfallMouseMove = (e: React.MouseEvent<HTMLDivElement>) => {
    if (!waterfallContainerRef.current) return;
    const rect = waterfallContainerRef.current.getBoundingClientRect();
    const x = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
    const y = Math.max(0, Math.min(1, (e.clientY - rect.top) / rect.height));

    if (isDragging) {
      setDragCurrent({ x, y });
    } else {
      const zoomSpanFrac = Math.max(0.01, (effectiveFrequencyZoom.end - effectiveFrequencyZoom.start) / 100);
      const fullXFrac = effectiveFrequencyZoom.start / 100 + x * zoomSpanFrac;
      const freqMhz = freqMinMhz + fullXFrac * freqSpanMhz;
      const timeSpanS = 20;
      const newestTs = waterfallNewest || Date.now() / 1000;
      const targetTime = newestTs - y * timeSpanS;
      const timeOffsetS = -(y * timeSpanS);

      const idx = findNearestHistoryFrame(historyFramesRef.current, targetTime);
      const frame = idx >= 0 ? historyFramesRef.current[idx] : null;
      let powerDb = -100;
      if (frame && frame.powers?.length) {
        const fMin = frame.freq_min / 1e6;
        const fMax = frame.freq_max / 1e6;
        const fSpan = Math.max(0.001, fMax - fMin);
        const bin = Math.max(0, Math.min(frame.powers.length - 1, Math.floor(((freqMhz - fMin) / fSpan) * frame.powers.length)));
        powerDb = frame.powers[bin];
      }
      setHoverSlice({
        frame: frame || latestFrame || (null as any),
        timeOffsetS,
        freqMhz,
        powerDb,
        xPct: x * 100,
        yPct: y * 100,
      });
    }
  };

  const handleWaterfallMouseUp = () => {
    if (!isDragging || !dragStart || !dragCurrent) {
      setIsDragging(false);
      return;
    }
    setIsDragging(false);

    const dxPct = Math.abs(dragCurrent.x - dragStart.x);
    const dyPct = Math.abs(dragCurrent.y - dragStart.y);

    if (dxPct < 0.015 && dyPct < 0.015) {
      const timeSpanS = 20;
      const newestTs = waterfallNewest || Date.now() / 1000;
      const targetTime = newestTs - dragStart.y * timeSpanS;
      const idx = findNearestHistoryFrame(historyFramesRef.current, targetTime);
      if (idx >= 0) {
        const frame = historyFramesRef.current[idx];
        setPinnedFrame(prev => (prev?.timestamp === frame.timestamp ? null : frame));
      }
      return;
    }

    const newestTs = waterfallNewest || Date.now() / 1000;
    const sel = calculateWaterfallSelection(
      dragStart.x,
      dragCurrent.x,
      dragStart.y,
      dragCurrent.y,
      effectiveFrequencyZoom,
      freqMinMhz,
      freqMinMhz + freqSpanMhz,
      newestTs,
      20
    );
    if (!latestFrame) return;
    setSelection({ ...sel, sessionId: latestFrame.session_id,
      sampleRate: latestFrame.sample_rate, centerFreq: latestFrame.center_freq });

    const analysis = analyzeWindowFrames(
      historyFramesRef.current,
      sel.timeStart,
      sel.timeEnd
    );
    setWindowAnalysis(analysis);
  };

  const handleWaterfallMouseLeave = () => {
    if (isDragging) {
      setIsDragging(false);
    }
    setHoverSlice(null);
  };

  const iqHistory = status.iq_history ?? latestFrame?.iq_history;
  const iqHistorySeconds = iqHistory?.time_start != null && iqHistory.time_end != null
    ? Math.max(0, iqHistory.time_end - iqHistory.time_start) : 0;
  // A historical selection follows its original samples as the waterfall scrolls.
  const selectionTopPct = selection ? ((waterfallNewest ?? selection.timeEnd) - selection.timeEnd) * 5 : 0;
  const selectionBottomPct = selection ? ((waterfallNewest ?? selection.timeEnd) - selection.timeStart) * 5 : 0;
  const visibleSpanMhz = visibleMaxMhz - visibleMinMhz;
  const selectionLeftPct = selection ? (selection.freqStartMhz - visibleMinMhz) / visibleSpanMhz * 100 : 0;
  const selectionRightPct = selection ? (selection.freqEndMhz - visibleMinMhz) / visibleSpanMhz * 100 : 0;
  const selectionVisible = selection && selectionTopPct < 100 && selectionBottomPct > 0
    && selectionLeftPct < 100 && selectionRightPct > 0;

  return (
    <div className="spectrum-workspace flex flex-col h-full min-h-0 w-full text-rf-text" data-settings-open={settingsOpen}>
      <div className="spec-workspace-body flex flex-1 min-h-0">
      {/* ======================================================== */}
      {/* 左侧控制栏：设备选择、RF 参数、增益及操作按钮 */}
      {/* ======================================================== */}
      <aside aria-label="频谱采集控制" className="spec-controls shrink-0 flex flex-col min-h-0 overflow-hidden">
        <SpectrumFrequencyTuner value={centerFreqHz} onChange={setCenterFreqHz} disabled={isStarting || isStopping} />

        <section aria-label="频谱采集操作" className="shrink-0 p-3 border-b border-rf-border bg-rf-panel/30 space-y-2">
          <div className="flex items-center gap-2">
            <div className="flex-1 min-w-0">
              {status.state !== 'running' && status.state !== 'paused' ? (
                <button
                  type="button"
                  disabled={isStarting || (sdr === 'file' && (!iqPath.trim() || fileUploading))}
                  onClick={handleStart}
                  className="spec-start w-full h-10 rounded-lg text-xs flex items-center justify-center space-x-2 transition-colors disabled:opacity-50"
                >
                  {isStarting ? <Loader2 className="w-4 h-4 animate-spin" aria-hidden="true" /> : <Play className="w-4 h-4 fill-current" aria-hidden="true" />}
                  <span>{isStarting ? '正在启动…' : sdr === 'file' ? '开始文件回放' : '开始频谱采集'}</span>
                </button>
              ) : (
                <div className="grid grid-cols-2 gap-2">
                  <button
                    type="button"
                    onClick={handleTogglePause}
                    disabled={isStarting || isStopping}
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
                    disabled={isStarting || isStopping}
                    className="h-10 rounded-lg bg-red-600 hover:bg-red-500 text-white font-bold font-mono text-xs uppercase shadow-md shadow-red-950/40 flex items-center justify-center space-x-1.5 transition-all active:scale-[0.98]"
                  >
                    <Square className="w-3.5 h-3.5 fill-current" />
                    <span>{status.device_type === 'file' ? '停止回放' : '停止采集'}</span>
                  </button>
                </div>
              )}
            </div>
            <button type="button" aria-expanded={settingsOpen} aria-controls="spectrum-settings"
              onClick={() => setSettingsOpen(value => !value)}
              className="spec-mobile-settings-toggle lg:hidden h-10 shrink-0 px-3 flex items-center gap-1.5 rounded-lg border border-rf-border bg-rf-panel hover:bg-rf-border text-xs text-zinc-200">
              <Settings2 className="w-4 h-4" aria-hidden="true" />
              {settingsOpen ? '收起设置' : '采集设置'}
            </button>
          </div>
          <p className="truncate text-xs text-zinc-400 font-mono tabular-nums" title={`设置：${sdr.toUpperCase()} · ${(sampleRate / 1e6).toFixed(3)} MSps`}>设置：{sdr.toUpperCase()} · {(sampleRate / 1e6).toFixed(3)} MSps</p>

          {/* ======================================================== */}
          {/* 实时突发录制控制组 (Burst Capture & Recording) */}
          {/* ======================================================== */}
          <div className="spec-record pt-2 border-t border-rf-border/60 space-y-2">
            <div className="flex items-center justify-between text-xs">
              <span className="font-bold font-mono text-zinc-300 flex items-center space-x-1.5">
                <Disc className="w-3.5 h-3.5 text-red-400" />
                <span>快速 IQ 采样录制</span>
              </span>
              <div className="flex items-center gap-1 font-mono text-[10px]">
                {([1.0, 2.0, 5.0, null] as const).map((dur) => (
                  <button
                    key={String(dur)}
                    type="button"
                    disabled={isRecording}
                    aria-pressed={recordDurationS === dur}
                    onClick={() => setRecordDurationS(dur)}
                    className={`px-1.5 py-0.5 rounded transition-colors ${
                      recordDurationS === dur
                        ? 'bg-red-500/20 text-red-300 border border-red-500/50 font-bold'
                        : 'bg-rf-panel/60 text-zinc-400 hover:text-zinc-200 border border-rf-border/40'
                    }`}
                  >
                    {dur ? `${dur}s` : '手动'}
                  </button>
                ))}
              </div>
            </div>

            <div className="flex items-center gap-2">
              {!isRecording ? (
                <button
                  type="button"
                  onClick={handleStartRecord}
                  disabled={isStarting || !['running', 'paused'].includes(status.state)}
                  className="spec-record-start w-full h-8 rounded-lg text-xs flex items-center justify-center space-x-1.5 transition-colors disabled:opacity-40"
                  title="实时录制指定时长的交错原始采样至文件"
                >
                  <CircleDot className="w-3.5 h-3.5" aria-hidden="true" />
                  <span>
                    开始录制 {recordDurationS ? `(${recordDurationS} 秒)` : '(手动启停)'}
                  </span>
                </button>
              ) : (
                <button
                  type="button"
                  onClick={handleStopRecord}
                  className="w-full h-8 rounded-lg bg-red-600 text-white font-bold font-mono text-xs shadow-lg shadow-red-600/30 flex items-center justify-center space-x-2 animate-pulse active:scale-[0.98]"
                >
                  <Square className="w-3.5 h-3.5 fill-current" />
                  <span>
                    ● REC {recordElapsed.toFixed(1)}s ({(recordedBytes / 1024 / 1024).toFixed(1)} MB) · 停止
                  </span>
                </button>
              )}
            </div>

            {/* 录制错误提示 */}
            {recordError && (
              <p role="alert" className="text-xs text-red-400 font-mono bg-red-950/40 border border-red-800/60 p-2 rounded-lg">
                {recordError}
              </p>
            )}

            {/* 录制完成浮动操作卡片 */}
            {recordResult && recordResult.completed && (
              <div className="p-2.5 rounded-lg bg-emerald-950/40 border border-emerald-700/60 space-y-2 text-xs font-mono">
                <div className="flex items-center justify-between text-emerald-300">
                  <span className="font-bold flex items-center space-x-1">
                    <CheckCircle2 className="w-3.5 h-3.5 text-emerald-400" />
                    <span>IQ 录制完成</span>
                  </span>
                  <button
                    type="button"
                    onClick={() => setRecordResult(null)}
                    aria-label="关闭录制完成提示"
                    className="p-0.5 rounded text-zinc-400 hover:text-zinc-200"
                  >
                    <X className="w-3.5 h-3.5" />
                  </button>
                </div>
                <div className="text-[11px] text-zinc-300 break-all space-y-0.5">
                  <p className="truncate" title={recordResult.filepath || ''}>文件: {recordResult.filepath}</p>
                  <p className="text-zinc-400">
                    大小: {((recordResult.recorded_bytes || 0) / 1024 / 1024).toFixed(2)} MB · 时长: {recordResult.duration_s?.toFixed(2)}s
                  </p>
                </div>
                <div className="grid grid-cols-2 gap-1.5 pt-1">
                  <button
                    type="button"
                    onClick={handleJumpToAnalysis}
                    className="px-2 py-1 rounded bg-cyan-500/20 hover:bg-cyan-500/30 text-cyan-300 border border-cyan-500/40 font-semibold text-[11px] flex items-center justify-center space-x-1"
                  >
                    <Activity className="w-3 h-3" />
                    <span>信号分析</span>
                  </button>
                  <button
                    type="button"
                    onClick={handleJumpToDemod}
                    className="px-2 py-1 rounded bg-rf-panel hover:bg-rf-border text-zinc-200 border border-rf-border font-semibold text-[11px] flex items-center justify-center space-x-1"
                  >
                    <ArrowRight className="w-3 h-3 text-cyan-400" />
                    <span>去解调</span>
                  </button>
                </div>
              </div>
            )}
          </div>
          {/* 采集错误始终显示在操作区 */}
          {(actionError || connectionError) && <p role="alert" className="max-h-24 overflow-y-auto break-words rounded-lg px-3 py-2 text-xs text-amber-300 bg-amber-950/30">{actionError || connectionError}</p>}
          {/* 错误提示横幅 */}
          {status.state === 'error' && (
            <div role="alert" className="max-h-24 overflow-y-auto rounded-lg bg-red-950/60 border border-red-700/80 px-3 py-2 text-xs text-red-300 flex items-start space-x-2">
              <AlertTriangle className="w-4 h-4 text-red-400 shrink-0" />
              <div className="flex-1">
                <strong>采集异常 [{status.error_code || 'ERROR'}]: </strong>
                <span>{status.error_message || '未知硬件或接收错误，请检查设备连接或释放冲突任务。'}</span>
              </div>
            </div>
          )}
        </section>

        <div id="spectrum-settings" role="region" aria-label="频谱采集参数" className={`min-h-0 flex-1 overflow-y-auto overscroll-contain p-3 space-y-4 text-xs ${settingsOpen ? '' : 'hidden lg:block'}`}>
          <fieldset disabled={isStarting || isStopping} className="min-w-0 space-y-4 [&_input:disabled]:opacity-50 [&_select:disabled]:opacity-50 [&_button:disabled]:opacity-50">
          {/* A. 硬件选择与物理定位 */}
          <fieldset disabled={configurationLocked} aria-label="输入源配置" className="spec-setting-group min-w-0 space-y-3 bg-rf-bg/60 p-3 rounded-lg border border-rf-border/80">
            <div className="flex items-center justify-between font-semibold text-zinc-300">
              <span className="flex items-center gap-1.5">
                <Cpu className="w-3.5 h-3.5 text-cyan-400" />
                输入源
              </span>
            </div>

            <div className="grid grid-cols-3 gap-1.5">
              {(['hackrf', 'usrp', 'file'] as const).map((kind) => (
                <button
                  key={kind}
                  type="button"
                  onClick={() => setSdr(kind)}
                  aria-pressed={sdr === kind}
                  className={`h-8 rounded-md font-mono font-bold text-xs border transition-colors ${
                    sdr === kind
                      ? 'bg-cyan-500/20 text-cyan-300 border-cyan-400/80 shadow-xs'
                      : 'bg-zinc-900/60 text-zinc-400 border-zinc-800 hover:text-zinc-200'
                  }`}
                >
                  {kind === 'file' ? 'File' : kind.toUpperCase()}
                </button>
              ))}
            </div>

            {/* 物理设备选择组件复用 */}
            {sdr === 'file' ? (
              <SpectrumFileInput path={iqPath} format={iqFormat} onUploadingChange={setFileUploading}
                onChange={file => {
                  setIqPath(file.path);
                  setIqFormat(file.iq_format);
                  if (file.sample_rate) setSampleRate(file.sample_rate);
                  if (file.center_freq) {
                    setCenterFreqHz(file.center_freq);
                    setDemodOffsetHz(0);
                  }
                }} />
            ) : (
              <HardwareDeviceSelector
                commandId="spectrum"
                parameters={hardwareSelectorParams}
                onChange={handleDeviceChange}
              />
            )}
          </fieldset>

          {/* B. 常用接收参数优先展示 */}
          <div className="spec-setting-group space-y-3 rounded-lg border border-rf-border/80 bg-rf-bg/40 p-3">
            <div className="flex items-center justify-between text-zinc-300 font-semibold">
              <span className="flex items-center gap-1.5">
                <Sliders className="w-3.5 h-3.5 text-cyan-400" />
                采样配置
              </span>
            </div>

            {sdr === 'usrp' && (
              <div className="space-y-1">
                <label htmlFor="spectrum-usrp-mcr" className="text-zinc-400">主时钟频率 (MCR)</label>
                <select
                  id="spectrum-usrp-mcr"
                  disabled={configurationLocked}
                  value={masterClockRate}
                  onChange={(e) => setMasterClockRate(Number(e.target.value))}
                  className="w-full h-8 bg-rf-bg border border-rf-border rounded px-2 font-mono text-xs text-rf-text focus:outline-none focus:border-cyan-400"
                >
                  <option value={125000000}>125.000 MHz (常用千兆网)</option>
                  <option value={122880000}>122.880 MHz (LTE/5G 常见倍数)</option>
                  <option value={153600000}>153.600 MHz (宽带时钟)</option>
                </select>
              </div>
            )}

            {/* 采样率 */}
            <div className="space-y-1">
              <div className="flex justify-between text-zinc-400">
                <label htmlFor="spectrum-sample-rate">采样率 (Sps)</label>
                <span className="font-mono text-zinc-400">
                  {(sampleRate / 1e6).toFixed(3)} MSps
                </span>
              </div>

              {sdr === 'file' ? (
                <input id="spectrum-sample-rate" disabled={configurationLocked} type="number" min="1" step="1" value={sampleRate}
                  onChange={event => setSampleRate(Number(event.target.value))}
                  className="w-full h-8 rounded border border-rf-border bg-rf-bg px-2 font-mono text-xs text-rf-text" />
              ) : sdr === 'usrp' && clockedOptions ? (
                <select
                  id="spectrum-sample-rate"
                  disabled={configurationLocked}
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
                  disabled={configurationLocked}
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

          </div>

          {/* C. 设备专用参数面板 (HackRF vs USRP) */}
          {sdr === 'hackrf' && (
            <div className="spec-setting-group space-y-3 bg-rf-bg/40 p-3 rounded-lg border border-rf-border/60">
              <h2 className="font-semibold text-zinc-300">HackRF 接收增益</h2>

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
                    disabled={configurationLocked}
                    onChange={(e) => setBiasTee(e.target.checked)}
                    className="rounded bg-rf-bg border-rf-border text-cyan-500 focus:ring-0"
                  />
                  <span>Bias-Tee 天线供电</span>
                </label>
              </div>
            </div>
          )}

          {sdr === 'usrp' && (
            <div className="spec-setting-group space-y-3 bg-rf-bg/40 p-3 rounded-lg border border-rf-border/60">
              <h2 className="font-semibold text-zinc-300">USRP 接收增益与端口</h2>

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
                    disabled={configurationLocked}
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
                    disabled={configurationLocked}
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

          {/* C. 内置实时解调配置 (Live Demodulation Channel) */}
          <details open className="group rounded-lg border border-amber-800/60 bg-amber-950/15">
            <summary className="flex cursor-pointer list-none items-center justify-between gap-2 p-3 text-xs font-semibold text-amber-200 hover:bg-amber-950/30 [&::-webkit-details-marker]:hidden">
              <span className="flex items-center gap-1.5">
                <Zap className="w-3.5 h-3.5 text-amber-400" aria-hidden="true" />
                内置实时解调通道
                {demodEnabled ? (
                  <span className="text-[10px] px-1.5 py-0.5 rounded bg-emerald-950/80 text-emerald-300 border border-emerald-600/70 font-mono font-normal animate-pulse">
                    解调中 ({demodPackets.length})
                  </span>
                ) : (
                  <span className="text-[10px] px-1.5 py-0.5 rounded bg-zinc-800 text-zinc-400 border border-zinc-700 font-mono font-normal">
                    已停用
                  </span>
                )}
              </span>
              <ChevronDown className="w-4 h-4 group-open:rotate-180 transition-transform" aria-hidden="true" />
            </summary>

            <div className="space-y-3 border-t border-amber-900/50 p-3">
              <div className="flex items-center justify-between">
                <span className="text-zinc-300 font-medium">启用实时解调</span>
                <button
                  type="button"
                  onClick={() => handleToggleDemodLive(!demodEnabled)}
                  className={`h-7 px-3 rounded text-xs font-mono font-bold transition-all shadow-xs flex items-center gap-1.5 ${
                    demodEnabled
                      ? 'bg-amber-500 text-black hover:bg-amber-400 shadow-amber-950/40'
                      : 'bg-zinc-800 text-zinc-300 hover:bg-zinc-700 border border-zinc-700'
                  }`}
                >
                  <Zap className="w-3 h-3 fill-current" />
                  <span>{demodEnabled ? '运行中 (点击关闭)' : '开启解调'}</span>
                </button>
              </div>

              <div className="space-y-2">
                <label htmlFor="spectrum-demod-template" className="block text-zinc-400">物理层模板 · physical</label>
                <select id="spectrum-demod-template" value={demodPhysicalTemplate} disabled={templates.isPending || isApplyingDemod}
                  onChange={event => { void handleSelectPhysicalTemplate(event.target.value); }}
                  className="w-full h-8 bg-rf-bg border border-rf-border rounded px-2 text-xs text-rf-text focus:outline-none focus:border-amber-400 disabled:opacity-50">
                  <option value="">{templates.isPending ? '正在加载模板…' : '未指定 · 使用默认解调配置'}</option>
                  {physicalTemplates.map(template => <option key={template.name} value={template.name}>{template.title}</option>)}
                </select>
                {templates.error && <p role="alert" className="text-red-400">物理模板加载失败：{templates.error.message}</p>}
                {selectedPhysicalTemplate && <div className="space-y-1 rounded border border-rf-border bg-rf-bg/60 px-2.5 py-2 text-xs leading-5 text-zinc-400">
                  <p>{selectedPhysicalTemplate.description}</p>
                  <p className="font-mono break-all">{selectedPhysicalTemplate.name}</p>
                  <p>{(selectedPhysicalTemplate.parsed.demod.bit_rate ?? 325000) / 1000} kbps · CR {selectedPhysicalTemplate.parsed.demod.coding_rate ?? '3/4'} · BT {selectedPhysicalTemplate.parsed.demod.bt ?? 1}</p>
                  <p>硬件头 {selectedPhysicalTemplate.parsed.demod.header_type || 'variable'} · CRC{(selectedPhysicalTemplate.parsed.demod.crc_bytes ?? 3) * 8}
                    {Array.isArray(selectedPhysicalTemplate.parsed.demod.payload_lengths) && selectedPhysicalTemplate.parsed.demod.payload_lengths.length ? ` · 候选包长 ${selectedPhysicalTemplate.parsed.demod.payload_lengths.join(' / ')} B` : ''}</p>
                  <details><summary className="cursor-pointer text-zinc-400">查看模板解调参数</summary>
                    <pre className="mt-2 max-h-48 overflow-auto whitespace-pre-wrap break-all font-mono select-text">{JSON.stringify(selectedPhysicalTemplate.parsed.demod, null, 2)}</pre></details>
                </div>}
              </div>

              {/* 协议解析器 */}
              <div className="space-y-1">
                <label htmlFor="spectrum-demod-proto" className="text-zinc-400">应用层协议解析</label>
                <select
                  id="spectrum-demod-proto"
                  value={demodProtocol}
                  onChange={(e) => {
                    const protocol = e.target.value;
                    const cutoff = Number(selectedPhysicalTemplate?.parsed.demod.filter_cutoff
                      ?? (!selectedPhysicalTemplate && protocol === 'h12' ? 750000 : 350000));
                    const bandwidth = (Number.isFinite(cutoff) && cutoff > 0 ? cutoff : 350000) * 2;
                    const nextBand = fitDemodBand(demodBand.offsetHz, displaySampleRate, bandwidth, effectiveFrequencyZoom, demodCaptureView);
                    setDemodProtocol(protocol);
                    setDemodOffsetHz(nextBand.offsetHz);
                    if (sessionId && ['running', 'paused'].includes(status.state)) {
                      void api.updateSpectrum(sessionId, { protocol, center_freq: centerFreqHz,
                        demod_center_freq: centerFreqHz + nextBand.offsetHz })
                        .catch(error => setActionError(`切换协议失败：${error.message}`));
                    }
                  }}
                  className="w-full h-8 bg-rf-bg border border-rf-border rounded px-2 font-mono text-xs text-rf-text focus:outline-none focus:border-amber-400"
                >
                  <option value="auto">auto - 自动识别 (推荐)</option>
                  <option value="dk32s">dk32s - DK32S 遥控协议</option>
                  <option value="dk32s-multi">dk32s-multi - DK32S 多机协议</option>
                  <option value="h12">h12 - H12 物理帧</option>
                  <option value="datalink">datalink - 数传链路协议</option>
                  <option value="raw">raw - 原生裸数据</option>
                </select>
              </div>

              {/* 解调数据流快捷跳转与计数 */}
              <div className="pt-2 border-t border-amber-900/40 flex items-center justify-between text-[11px] font-mono">
                <span className="text-zinc-400">
                  捕获帧: <strong className="text-amber-300">{demodPackets.length}</strong>
                </span>
                <button
                  type="button"
                  onClick={() => setIsDemodDrawerOpen(prev => !prev)}
                  aria-expanded={isDemodDrawerOpen}
                  aria-controls="spectrum-demod-packets"
                  className="text-cyan-400 hover:underline flex items-center gap-1"
                >
                  <FileCode className="w-3 h-3" />
                  <span>{isDemodDrawerOpen ? '收起数据流' : '展开数据流'}</span>
                </button>
              </div>
            </div>
          </details>

          <details className="group rounded-lg border border-rf-border/80 bg-rf-bg/40">
            <summary className="flex cursor-pointer list-none items-center justify-between gap-2 p-3 text-xs font-semibold text-zinc-300 hover:bg-rf-panel/40 [&::-webkit-details-marker]:hidden">
              <span className="flex items-center gap-1.5"><Layers className="w-3.5 h-3.5 text-cyan-400" aria-hidden="true" />FFT 与刷新</span>
              <ChevronDown className="w-4 h-4 group-open:rotate-180 transition-transform" aria-hidden="true" />
            </summary>
            <div className="space-y-3 border-t border-rf-border/60 p-3">
            {/* FFT 点数与窗函数 */}
            <div className="grid grid-cols-2 gap-2">
              <div className="space-y-1">
                <label htmlFor="spectrum-n-fft" className="text-zinc-400">FFT 点数</label>
                <select
                  id="spectrum-n-fft"
                  disabled={configurationLocked}
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
                  disabled={configurationLocked}
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
            </div>
          </details>

          <details className="group rounded-lg border border-rf-border/80 bg-rf-bg/40">
            <summary className="flex cursor-pointer list-none items-center justify-between gap-2 p-3 text-xs font-semibold text-zinc-300 hover:bg-rf-panel/40 [&::-webkit-details-marker]:hidden">
              <span className="flex items-center gap-1.5"><Gauge className="w-3.5 h-3.5 text-cyan-400" aria-hidden="true" />显示电平</span>
              <ChevronDown className="w-4 h-4 group-open:rotate-180 transition-transform" aria-hidden="true" />
            </summary>
            <div className="space-y-3 border-t border-rf-border/60 p-3">
              <p className="text-zinc-400 leading-relaxed">调整频谱纵轴和后续瀑布图的色阶。</p>
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
                  onChange={(e) => setRefLevelDbfs(Number(e.target.value))}
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
                  onChange={(e) => setDynamicRangeDb(Number(e.target.value))}
                  className="w-full h-8 bg-rf-bg border border-rf-border rounded px-2 font-mono text-rf-text focus:outline-none focus:border-cyan-400"
                />
              </div>
            </div>
            </div>
          </details>
          </fieldset>
        </div>

      </aside>

      {/* ======================================================== */}
      {/* 右侧主视口：状态度量、实时频谱图 (ECharts) 与瀑布图 Canvas */}
      {/* ======================================================== */}
      <section aria-label="实时频谱与瀑布图" className="spec-main flex-1 min-w-0 min-h-0 flex flex-col overflow-hidden">
        <SpectrumStatusBar status={status} frame={latestFrame} sampleRate={sampleRate} nFft={nFft} />

        {/* 频谱与瀑布图上下等高视图 */}
        <div className="spec-plots min-h-0">
          {/* 上半部分：实时频谱图 (ECharts) */}
          <section aria-label="实时频谱" className="spec-plot spec-frequency min-h-0 flex flex-col">
            <div className="spec-frequency-tools shrink-0 flex flex-wrap items-center justify-between gap-2 text-xs">
              <div className="flex flex-wrap items-center gap-2 text-zinc-400">
                <h2 className="spec-plot-title">实时频谱</h2>
                <button type="button" onClick={() => setShowDemodBand(value => !value)} aria-pressed={showDemodBand}
                  className={`h-8 px-2.5 rounded-md border ${showDemodBand ? 'border-amber-600 bg-amber-500/10 text-amber-300' : 'border-rf-border bg-rf-panel text-zinc-400'}`}>
                  解调框：{showDemodBand ? '开' : '关'}</button>
                {showDemodBand && <>
                  <span className="spec-demod-frequency text-amber-300" title={`${demodCenterFreqMhz.toFixed(6)} MHz · 左右拖动解调框，松手应用`}>{demodCenterFreqMhz.toFixed(3)} MHz · {(demodBandwidthHz / 1000).toFixed(0)} kHz</span>
                  {isTuningDemod && <span role="status">正在应用…</span>}
                  <button type="button" disabled={isApplyingDemod} onClick={() => {
                    setFrequencyZoom({ start: 0, end: 100 });
                    setDemodOffsetHz(0);
                  }} className="h-8 px-2 rounded-md text-zinc-300 hover:bg-rf-panel disabled:opacity-40" title="将解调框放回采集频带中心">回中</button>
                </>}
                {!demodBand.available && <span role="status" className="text-amber-300">采样带宽不足，请提高采样率</span>}
              </div>
              <div role="group" aria-label="频谱显示操作" className="flex flex-wrap items-center gap-2">
                <button type="button" onClick={() => setPeakHoldEnabled(value => !value)} aria-pressed={peakHoldEnabled}
                  className={`h-8 px-2.5 rounded-md border flex items-center gap-1.5 transition-colors ${peakHoldEnabled
                    ? 'bg-amber-500/20 text-amber-300 border-amber-400'
                    : 'bg-rf-panel text-zinc-300 border-rf-border hover:text-white'}`}>
                  <Flame className="w-3.5 h-3.5" aria-hidden="true" />峰值保持: {peakHoldEnabled ? '开' : '关'}
                </button>
                <button type="button" onClick={handleResetPeakHold} disabled={!peakHoldPowers}
                  className="h-8 px-2 rounded-md text-zinc-300 hover:bg-rf-panel disabled:opacity-40">重置峰值</button>
              </div>
            </div>

            <div className="spec-chart-canvas flex-1 min-h-0 relative">
              <SignalChart
                echarts={echarts}
                option={spectrumOption}
                style={{ height: '100%', width: '100%' }}
                notMerge={false}
                replaceMerge={['series']}
                lazyUpdate={true}
                onEvents={chartEvents}
              />
              {showDemodBand && <div className="absolute pointer-events-none" style={{ ...SPECTRUM_PLOT_INSETS,
                top: spectrumOption.grid.top, bottom: spectrumOption.grid.bottom }}>
                <SpectrumDemodBand frequencyMhz={demodCenterFreqMhz} bandwidthHz={demodBandwidthHz}
                  visibleMinMhz={visibleMinMhz} visibleMaxMhz={visibleMaxMhz}
                  captureMinMhz={freqMinMhz} captureMaxMhz={freqMinMhz + freqSpanMhz}
                  active={demodEnabled} disabled={isApplyingDemod || isStarting || isStopping || !demodBand.available} onCommit={handleDragDemodFrequency} />
              </div>}
              {!latestFrame && <div className="spec-empty spec-empty-spectrum">
                <strong>{isStarting ? '正在连接信号源' : status.state === 'running' ? '正在等待频谱数据' : status.state === 'error' ? '信号源暂不可用' : '等待采集'}</strong>
                <p>{status.state === 'error' ? '请检查采集控制区的错误提示后重试。' : '选择信号源，点击“开始频谱采集”。'}</p>
              </div>}
            </div>
          </section>

          {/* 下半部分：瀑布图 (Waterfall Canvas) */}
          <section aria-label="实时瀑布图" className="spec-plot spec-waterfall min-h-0 flex flex-col relative">
            {/* 瀑布图顶部工具栏：时空框选/历史回溯状态与操作 */}
            <div className="spec-waterfall-tools shrink-0 flex flex-wrap items-center justify-between gap-2 text-xs">
              <div className="flex items-center gap-2 min-w-0 flex-wrap">
                <div className="font-semibold text-zinc-300 flex items-center gap-1.5">
                  <MousePointer2 className="w-3.5 h-3.5" aria-hidden="true" />
                  <h2 className="spec-plot-title">实时瀑布图</h2>
                </div>

                {/* 历史切片定格状态与逐帧步进 */}
                {pinnedFrame ? (
                  <div className="flex items-center gap-1 bg-purple-950/60 border border-purple-600/70 rounded px-2 py-0.5 text-[11px] text-purple-300">
                    <Clock className="w-3 h-3 text-purple-400" />
                    <span>已锁定: {formatFrameTime(pinnedFrame.timestamp)}</span>
                    <button
                      type="button"
                      onClick={handleStepPrevFrame}
                      className="ml-1 px-1 py-0.5 rounded hover:bg-purple-800/60 text-purple-200"
                      title="上一帧 (快捷键: ←)"
                    >
                      <ChevronLeft className="w-3 h-3" />
                    </button>
                    <button
                      type="button"
                      onClick={handleStepNextFrame}
                      className="px-1 py-0.5 rounded hover:bg-purple-800/60 text-purple-200"
                      title="下一帧 (快捷键: →)"
                    >
                      <ChevronRight className="w-3 h-3" />
                    </button>
                    <button
                      type="button"
                      onClick={handleClearPinnedFrame}
                      className="ml-1 text-zinc-400 hover:text-white"
                      title="退出定格 (Esc)"
                    >
                      <X className="w-3 h-3" />
                    </button>
                  </div>
                ) : (
                  <span className="text-[11px] text-zinc-500 hidden sm:inline">
                    拖拽框选 · 单击定格 · ←/→ 逐帧微调
                  </span>
                )}

                {/* 选区多帧分析结果摘要 */}
                {windowAnalysis && (
                  <span className="text-[11px] text-emerald-400 bg-emerald-950/50 border border-emerald-700/60 rounded px-1.5 py-0.5 flex items-center gap-1">
                    <Activity className="w-3 h-3" />
                    选区包络: {windowAnalysis.frameCount} 帧 | 峰值 {windowAnalysis.peakPowerDb} dBFS @ {windowAnalysis.peakFreqMhz.toFixed(3)} MHz
                  </span>
                )}
              </div>

              <div className="flex items-center gap-2">
                <span className="spec-waterfall-window" title="频谱显示最近 20 秒；IQ 可用时长受缓存容量和采样率限制，过期时段无法导出">
                  20 s · IQ 可用 {iqHistorySeconds.toFixed(1)} s
                </span>
                {/* 选区快捷动作栏 */}
                {selection && (
                  <div className="flex items-center gap-1.5">
                    <button
                      type="button"
                      onClick={handleDemodSelection}
                      className="h-6 px-2 rounded bg-amber-500 text-black hover:bg-amber-400 font-bold text-[11px] font-mono flex items-center gap-1 shadow transition-colors"
                      title="开启内置实时解调通道并锁定此选区中心频"
                    >
                      <Zap className="w-3 h-3 fill-current" />
                      解调此频点
                    </button>
                    <button
                      type="button"
                      onClick={handleSendToDemod}
                      disabled={isExportingSelection}
                      aria-busy={isExportingSelection}
                      className="h-6 px-2 rounded bg-cyan-500 text-black hover:bg-cyan-400 font-bold text-[11px] font-mono flex items-center gap-1 shadow transition-colors disabled:opacity-50"
                      title="导出所选时段的原始 IQ，并以选区中心频点离线解调"
                    >
                      <ArrowRight className="w-3 h-3" />
                      {isExportingSelection ? '正在提取 IQ…' : '送入解调'}
                    </button>
                    <button
                      type="button"
                      onClick={handleTuneToSelection}
                      className="h-6 px-2 rounded bg-zinc-800 hover:bg-zinc-700 text-cyan-300 text-[11px] font-mono flex items-center gap-1 transition-colors"
                      title="调谐 SDR 中心频"
                    >
                      <Target className="w-3 h-3" />
                      设为中心频
                    </button>
                    <button
                      type="button"
                      onClick={handleZoomToSelection}
                      className="h-6 px-2 rounded bg-zinc-800 hover:bg-zinc-700 text-cyan-300 text-[11px] font-mono flex items-center gap-1 transition-colors"
                      title="缩放视口至选区"
                    >
                      <Maximize2 className="w-3 h-3" />
                      视口放大
                    </button>
                    <button
                      type="button"
                      onClick={handleClearSelection}
                      className="h-6 px-1.5 rounded bg-zinc-800 hover:bg-zinc-700 text-zinc-400 hover:text-white text-[11px] font-mono transition-colors"
                      title="清除选区"
                    >
                      <X className="w-3 h-3" />
                    </button>
                  </div>
                )}

                {/* 视口复位按钮 (当放大时显示) */}
                {(effectiveFrequencyZoom.start > 0 || effectiveFrequencyZoom.end < 100) && (
                  <button
                    type="button"
                    onClick={handleResetZoom}
                    className="h-6 px-2 rounded border border-zinc-700 bg-zinc-800/80 hover:bg-zinc-700 text-zinc-300 text-[11px] flex items-center gap-1 transition-colors"
                    title="复位频段缩放为全频段"
                  >
                    <RotateCcw className="w-3 h-3" />
                    重置视口
                  </button>
                )}
              </div>
            </div>

            <div className="spec-waterfall-body flex-1 relative min-h-0 grid" style={{ gridTemplateColumns: waterfallColumns }}>
              <div className="flex flex-col justify-between items-end pr-2 text-[11px] leading-none font-mono text-zinc-400">
                {[0, 10, 20].map(age => <span key={age}>{waterfallNewest === null ? '—' : new Date((waterfallNewest - age) * 1000).toLocaleTimeString('zh-CN', { hour12: false })}</span>)}
              </div>
              {/* Crop the full-band history with the same viewport as the spectrum. */}
              <div
                ref={waterfallContainerRef}
                className="relative min-w-0 min-h-0 overflow-hidden cursor-crosshair select-none"
                onMouseDown={handleWaterfallMouseDown}
                onMouseMove={handleWaterfallMouseMove}
                onMouseUp={handleWaterfallMouseUp}
                onMouseLeave={handleWaterfallMouseLeave}
              >
                <canvas
                  ref={waterfallCanvasRef}
                  width={1200}
                  height={400}
                  style={{ width: `${10000 / zoomSpan}%`, left: `${-effectiveFrequencyZoom.start * 100 / zoomSpan}%` }}
                  className="absolute top-0 block max-w-none h-full object-fill bg-[#0d1117] pointer-events-none"
                />
                {waterfallNewest === null && <div className="spec-empty spec-empty-waterfall">
                  <strong>等待瀑布图数据</strong>
                  <p>采集后可框选频段、定格切片。</p>
                </div>}

                {/* 1. 定格历史切片指示水平线 */}
                {pinnedFrame && waterfallNewest !== null && (
                  <div
                    className="absolute left-0 right-0 pointer-events-none z-10 flex items-center"
                    style={{
                      top: `${Math.max(0, Math.min(100, ((waterfallNewest - pinnedFrame.timestamp) / 20) * 100))}%`,
                    }}
                  >
                    <div className="w-full border-t-2 border-purple-400 shadow-sm shadow-purple-950" />
                    <span className="absolute right-2 -top-4 text-[10px] font-mono bg-purple-900/90 text-purple-200 border border-purple-500/80 px-1 py-0.5 rounded shadow">
                      定格切片
                    </span>
                  </div>
                )}

                {/* 2. 悬停探针准星与 HUD 浮层 (拖拽时不显示) */}
                {hoverSlice && !isDragging && (
                  <>
                    <div
                      className="absolute top-0 bottom-0 pointer-events-none border-l border-dashed border-cyan-400/40 z-10"
                      style={{ left: `${hoverSlice.xPct}%` }}
                    />
                    <div
                      className="absolute left-0 right-0 pointer-events-none border-t border-dashed border-cyan-400/40 z-10"
                      style={{ top: `${hoverSlice.yPct}%` }}
                    />
                    <div
                      className="absolute pointer-events-none z-30 px-2 py-1 rounded bg-[#161b22]/95 border border-cyan-500/70 text-[10px] font-mono text-zinc-200 shadow-xl backdrop-blur-xs flex items-center gap-2 whitespace-nowrap"
                      style={{
                        left: `${Math.min(75, Math.max(5, hoverSlice.xPct))}%`,
                        top: `${hoverSlice.yPct > 75 ? hoverSlice.yPct - 12 : hoverSlice.yPct + 4}%`,
                      }}
                    >
                      <span className="text-cyan-400 font-bold">{hoverSlice.freqMhz.toFixed(3)} MHz</span>
                      <span className="text-emerald-400 font-bold">{hoverSlice.powerDb.toFixed(1)} dBFS</span>
                      <span className="text-purple-300 font-bold">{hoverSlice.timeOffsetS.toFixed(1)}s</span>
                    </div>
                  </>
                )}

                {/* 3. 正在拖拽框选的临时半透明矩形 */}
                {isDragging && dragStart && dragCurrent && (
                  <div
                    className="absolute border border-cyan-400 bg-cyan-400/15 pointer-events-none z-20 rounded-xs"
                    style={{
                      left: `${Math.min(dragStart.x, dragCurrent.x) * 100}%`,
                      top: `${Math.min(dragStart.y, dragCurrent.y) * 100}%`,
                      width: `${Math.max(0.5, Math.abs(dragCurrent.x - dragStart.x) * 100)}%`,
                      height: `${Math.max(0.5, Math.abs(dragCurrent.y - dragStart.y) * 100)}%`,
                    }}
                  />
                )}

                {/* 4. 已确立的时空框选区域与浮动操作栏 */}
                {selectionVisible && selection && (
                  <div
                    className="absolute border-2 border-cyan-400 bg-cyan-400/10 z-20 rounded-xs shadow-lg shadow-cyan-950/40"
                    style={{
                      left: `${Math.max(0, selectionLeftPct)}%`,
                      top: `${Math.max(0, selectionTopPct)}%`,
                      width: `${Math.max(2, Math.min(100, selectionRightPct) - Math.max(0, selectionLeftPct))}%`,
                      height: `${Math.max(2, Math.min(100, selectionBottomPct) - Math.max(0, selectionTopPct))}%`,
                    }}
                  >
                    {/* 顶部频率与带宽标签 */}
                    <div className="absolute -top-5.5 left-0 flex items-center gap-1 text-[10px] font-mono bg-cyan-950/90 text-cyan-200 border border-cyan-500/80 px-1.5 py-0.5 rounded whitespace-nowrap shadow pointer-events-none">
                      <span>{selection.centerFreqMhz.toFixed(3)} MHz</span>
                      <span className="text-cyan-400 font-bold">
                        (BW {selection.bandwidthKhz >= 1000 ? `${(selection.bandwidthKhz / 1000).toFixed(2)} MHz` : `${selection.bandwidthKhz.toFixed(0)} kHz`})
                      </span>
                    </div>

                    {/* 浮动操作按钮条 */}
                    <div
                      className="absolute z-30 flex items-center gap-1 bg-[#161b22]/95 border border-cyan-500/80 p-1 rounded-md shadow-2xl backdrop-blur-xs whitespace-nowrap pointer-events-auto"
                      style={{
                        top: selectionBottomPct > 68 ? '-32px' : 'calc(100% + 6px)',
                        left: selectionLeftPct > 60 ? 'auto' : '0',
                        right: selectionLeftPct > 60 ? '0' : 'auto',
                      }}
                      onMouseDown={(e) => e.stopPropagation()}
                      onClick={(e) => e.stopPropagation()}
                    >
                      <button
                        type="button"
                        onClick={handleDemodSelection}
                        className="h-6 px-2 rounded bg-amber-500 text-black hover:bg-amber-400 font-bold text-[10px] font-mono flex items-center gap-1 shadow transition-colors"
                        title="开启内置实时解调通道并锁定此选区中心频"
                      >
                        <Zap className="w-3 h-3 fill-current" />
                        解调此频点
                      </button>
                      <button
                        type="button"
                        onClick={handleSendToDemod}
                        disabled={isExportingSelection}
                        aria-busy={isExportingSelection}
                        className="h-6 px-2 rounded bg-cyan-500 text-black hover:bg-cyan-400 font-bold text-[10px] font-mono flex items-center gap-1 shadow transition-colors disabled:opacity-50"
                        title="导出所选时段的原始 IQ，并以选区中心频点离线解调"
                      >
                        <ArrowRight className="w-3 h-3" />
                        {isExportingSelection ? '正在提取 IQ…' : '送入解调'}
                      </button>
                      <button
                        type="button"
                        onClick={handleTuneToSelection}
                        className="h-6 px-2 rounded bg-zinc-800 hover:bg-zinc-700 text-cyan-300 text-[10px] font-mono flex items-center gap-1 transition-colors"
                        title="将 SDR 实时中心频率调谐至选区中心频"
                      >
                        <Target className="w-3 h-3" />
                        设为中心频
                      </button>
                      <button
                        type="button"
                        onClick={handleZoomToSelection}
                        className="h-6 px-2 rounded bg-zinc-800 hover:bg-zinc-700 text-cyan-300 text-[10px] font-mono flex items-center gap-1 transition-colors"
                        title="水平视口放大至选区频段"
                      >
                        <Maximize2 className="w-3 h-3" />
                        视口放大
                      </button>
                      <button
                        type="button"
                        onClick={handleAnalyzeSelection}
                        className="h-6 px-2 rounded bg-zinc-800 hover:bg-zinc-700 text-emerald-300 text-[10px] font-mono flex items-center gap-1 transition-colors"
                        title="在上方频谱图中生成选区时间窗多帧峰值与能量平均包络"
                      >
                        <Activity className="w-3 h-3" />
                        多帧分析
                      </button>
                      <button
                        type="button"
                        onClick={handleClearSelection}
                        className="h-6 px-1.5 rounded bg-zinc-800 hover:bg-red-950/60 text-zinc-400 hover:text-red-400 text-[10px] font-mono transition-colors"
                        title="清除选区"
                      >
                        <X className="w-3 h-3" />
                      </button>
                    </div>
                  </div>
                )}
              </div>

              {/* 右侧色阶参考标尺 */}
              <div aria-label="功率色阶 (dBFS)" className="flex flex-col justify-between items-center text-[11px] leading-none font-mono text-zinc-400">
                <span className="text-red-400">{refLevelDbfs}</span>
                <div className="spec-color-scale w-2.5 flex-1 my-1 rounded-sm border border-zinc-700" />
                <span className="text-cyan-400">{refLevelDbfs - dynamicRangeDb}</span>
              </div>
            </div>
            <div className="shrink-0 pt-1 pb-2 grid text-[11px] leading-none font-mono text-zinc-400" style={{ gridTemplateColumns: waterfallColumns }}>
              <div className="col-start-2 flex justify-between gap-2 min-w-0">
                <span>{visibleMinMhz.toFixed(3)} MHz</span>
                <span>{visibleMaxMhz.toFixed(3)} MHz</span>
              </div>
            </div>
          </section>
        </div>

        {/* ======================================================== */}
        {/* 底部浮动抽屉：覆盖图表，不参与主视口布局 */}
        {/* ======================================================== */}
        <section
          aria-label="实时解调数据流"
          data-open={isDemodDrawerOpen}
          className="spec-demod-drawer flex flex-col"
        >
          {/* 抽屉顶部标题与快捷工具栏 */}
          <div className="spec-demod-toolbar min-h-10 shrink-0 px-3 flex items-center justify-between text-xs select-none">
            <div className="flex items-center gap-2 min-w-0">
              <button
                type="button"
                onClick={() => setIsDemodDrawerOpen((prev) => !prev)}
                className="p-1 rounded hover:bg-zinc-800 text-zinc-300 hover:text-white transition-colors"
                title={isDemodDrawerOpen ? '收起解调抽屉' : '展开解调抽屉'}
                aria-label={isDemodDrawerOpen ? '收起解调抽屉' : '展开解调抽屉'}
                aria-expanded={isDemodDrawerOpen}
                aria-controls="spectrum-demod-packets"
              >
                {isDemodDrawerOpen ? <ChevronDown className="w-4 h-4 text-amber-400" /> : <ChevronUp className="w-4 h-4 text-amber-400" />}
              </button>

              <button type="button"
                className="flex items-center gap-2 cursor-pointer"
                onClick={() => setIsDemodDrawerOpen((prev) => !prev)}
                aria-expanded={isDemodDrawerOpen}
                aria-controls="spectrum-demod-packets"
              >
                <span className="font-bold text-zinc-200 flex items-center gap-1.5">
                  <Zap className={`w-3.5 h-3.5 ${demodEnabled ? 'text-amber-400 animate-pulse' : 'text-zinc-500'}`} />
                  实时解调数据流
                </span>

                <span
                  className={`text-[10px] px-2 py-0.5 rounded-full font-mono border ${
                    demodEnabled
                      ? 'bg-emerald-950/60 text-emerald-300 border-emerald-600/70'
                      : 'bg-zinc-900 text-zinc-500 border-zinc-800'
                  }`}
                >
                  {demodEnabled ? `监听 @ ${(demodCenterFreqMhz || centerFreqMhz).toFixed(3)} MHz` : '未开启'}
                </span>

                <span className="text-[10px] px-1.5 py-0.5 rounded bg-zinc-800 text-amber-300 font-bold border border-zinc-700">
                  {demodPackets.length} 帧
                </span>
              </button>
            </div>

            {/* 右侧动作工具 */}
            <div className="flex flex-wrap items-center justify-end gap-1.5">
              {isDemodDrawerOpen && (
                <>
                  {/* 过滤器输入框 */}
                  <div className="relative">
                    <input
                      type="text"
                      aria-label="过滤实时解调数据包"
                      placeholder="协议 / 指令 / Hex / CRC…"
                      value={demodFilterText}
                      onChange={(e) => setDemodFilterText(e.target.value)}
                      className="w-44 lg:w-56 h-6 px-2 text-[11px] bg-zinc-900 border border-zinc-700 rounded text-zinc-200 placeholder-zinc-500 focus:outline-none focus:border-cyan-400 font-mono"
                    />
                    {demodFilterText && (
                      <button
                        type="button"
                        onClick={() => setDemodFilterText('')}
                        aria-label="清空数据包过滤条件"
                        className="absolute right-1 top-1 text-zinc-400 hover:text-white"
                      >
                        <X className="w-3.5 h-3.5" />
                      </button>
                    )}
                  </div>

                  {/* 暂停/恢复滚动刷新 */}
                  <button
                    type="button"
                    onClick={() => setDemodPaused((prev) => !prev)}
                    className={`h-6 px-2 rounded text-[11px] font-mono flex items-center gap-1 border transition-colors ${
                      demodPaused
                        ? 'bg-amber-950/60 text-amber-300 border-amber-600/70'
                        : 'bg-zinc-800 text-zinc-400 border-zinc-700 hover:text-zinc-200'
                    }`}
                    title={demodPaused ? '恢复数据包自动刷新' : '暂停数据包自动刷新'}
                  >
                    {demodPaused ? <Play className="w-3 h-3 text-amber-400 fill-current" /> : <Pause className="w-3 h-3" />}
                    <span>{demodPaused ? '已暂停' : '更新中'}</span>
                  </button>

                  <button
                    type="button"
                    disabled={!demodPackets.some(packet => packet.payload_hex?.trim())}
                    onClick={() => {
                      const frame = selectedDissectFrame?.payload_hex?.trim()
                        ? selectedDissectFrame : demodPackets.find(packet => packet.payload_hex?.trim());
                      if (frame) openDemodProtocol(frame);
                    }}
                    className="h-6 px-2 rounded bg-cyan-950/50 hover:bg-cyan-500 hover:text-slate-950 text-cyan-300 text-[11px] font-mono flex items-center gap-1 border border-cyan-500/40 disabled:opacity-40 transition-colors"
                    title="将所选报文或最新报文载入独立业务协议解析工作台"
                  >
                    <Braces className="w-3 h-3" />
                    <span>业务解析</span>
                  </button>

                  {/* 导出 JSONL */}
                  <button
                    type="button"
                    disabled={!demodPackets.length}
                    onClick={handleExportPackets}
                    className="h-6 px-2 rounded bg-zinc-800 hover:bg-zinc-700 text-zinc-300 hover:text-white text-[11px] font-mono flex items-center gap-1 border border-zinc-700 disabled:opacity-40 transition-colors"
                    title="导出所有实时解调包为 JSONL 文件"
                  >
                    <Download className="w-3 h-3" />
                    <span>导出</span>
                  </button>

                  {/* 清空列表 */}
                  <button
                    type="button"
                    disabled={!demodPackets.length}
                    onClick={handleClearDemodPackets}
                    className="h-6 px-1.5 rounded bg-zinc-800 hover:bg-red-950/60 text-zinc-400 hover:text-red-400 text-[11px] font-mono flex items-center gap-1 border border-zinc-700 disabled:opacity-40 transition-colors"
                    title="清空当前已捕获的解调包"
                    aria-label="清空当前已捕获的解调包"
                  >
                    <Trash2 className="w-3 h-3" />
                  </button>
                </>
              )}

              <button
                type="button"
                onClick={() => setIsDemodDrawerOpen((prev) => !prev)}
                aria-expanded={isDemodDrawerOpen}
                aria-controls="spectrum-demod-packets"
                className="text-[11px] text-zinc-400 hover:text-zinc-200 font-mono px-1 py-0.5 rounded hover:bg-zinc-800"
              >
                {isDemodDrawerOpen ? '收起' : '展开详情'}
              </button>
            </div>
          </div>

          {/* 抽屉内容区：数据包表格与实时流 */}
          {isDemodDrawerOpen && (
            <div id="spectrum-demod-packets" className="flex-1 min-h-0 overflow-y-auto overflow-x-auto select-text font-mono text-xs">
              {filteredPackets.length === 0 ? (
                <div className="spec-demod-empty h-full flex flex-col items-center justify-center text-zinc-500 space-y-2 p-6">
                  <Zap className="w-8 h-8 text-zinc-600" />
                  <p className="text-center">
                    {demodFilterText
                      ? `未找到匹配 "${demodFilterText}" 的解调数据包`
                      : demodEnabled
                      ? '解调通道已就绪，正在监听空中 FLRC 突发数据包…'
                      : '实时解调未开启。在左侧控制面板中开启解调，或在瀑布图中框选信号后点击【解调此频点】。'}
                  </p>
                  {!demodEnabled && (
                    <button
                      type="button"
                      onClick={() => handleToggleDemodLive(true)}
                      className="px-3 py-1 rounded bg-amber-500/20 hover:bg-amber-500/30 text-amber-300 border border-amber-500/40 text-xs font-semibold flex items-center gap-1.5"
                    >
                      <Zap className="w-3.5 h-3.5 fill-current" />
                      <span>立即开启实时解调</span>
                    </button>
                  )}
                </div>
              ) : (
                <table className="w-full text-left border-collapse">
                  <thead className="sticky top-0 bg-zinc-900/95 text-[11px] text-zinc-400 border-b border-zinc-800 uppercase tracking-wider backdrop-blur-xs z-10">
                    <tr>
                      <th className="py-1.5 px-3 w-12 text-center">#</th>
                      <th className="py-1.5 px-3 w-28">时间</th>
                      <th className="py-1.5 px-3 w-24">频率</th>
                      <th className="py-1.5 px-3 w-40">协议 / 指令</th>
                      <th className="py-1.5 px-3 w-16 text-right">长度</th>
                      <th className="py-1.5 px-3 w-24 text-center">CRC</th>
                      <th className="py-1.5 px-3">Hex 载荷预览</th>
                      <th className="py-1.5 px-3 w-36 text-center whitespace-nowrap">操作</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-zinc-800/60 text-[11px]">
                    {filteredPackets.map((pkt, idx) => {
                      const payloadHex = hexText(pkt.payload_hex);
                      const previewHex = payloadHex.slice(0, 32).match(/.{1,2}/g)?.join(' ') || '—';
                      const isCrcOk = pkt.crc_ok ?? true;
                      const protoName = pkt.application?.protocol || (pkt.hardware_header ? 'flrc' : 'raw');
                      const cmdName = pkt.application?.command_name || pkt.application?.type || '';
                      const isSelected =
                        Boolean(selectedDissectFrame) &&
                        ((selectedDissectFrame?._client_id && pkt._client_id === selectedDissectFrame._client_id) ||
                          (selectedDissectFrame && frameKey(selectedDissectFrame) === frameKey(pkt)));

                      return (
                        <tr
                          key={pkt._client_id || frameKey(pkt) || idx}
                          onClick={() => setSelectedDissectFrame(pkt)}
                          className={`cursor-pointer transition-colors group ${
                            isSelected ? 'bg-cyan-950/40 ring-1 ring-cyan-500/50' : 'hover:bg-cyan-950/20'
                          }`}
                        >
                          <td className="py-1.5 px-3 text-center text-zinc-500">{filteredPackets.length - idx}</td>
                          <td className="py-1.5 px-3 text-zinc-300">
                            {pkt.time_s > 1000000000
                              ? new Date(pkt.time_s * 1000).toLocaleTimeString('zh-CN', { hour12: false }) +
                                '.' +
                                Math.floor((pkt.time_s % 1) * 1000).toString().padStart(3, '0')
                              : `${Number(pkt.time_s).toFixed(3)}s`}
                          </td>
                          <td className="py-1.5 px-3 text-cyan-400">
                            {pkt.frequency_hz ? `${(pkt.frequency_hz / 1e6).toFixed(3)} MHz` : '—'}
                          </td>
                          <td className="py-1.5 px-3">
                            <span className="text-zinc-200 font-semibold">{protoName}</span>
                            {cmdName && <span className="text-amber-400 ml-1.5">· {cmdName}</span>}
                          </td>
                          <td className="py-1.5 px-3 text-right text-zinc-400">
                            {pkt.payload_length ?? (payloadHex.length / 2)} B
                          </td>
                          <td className="py-1.5 px-3 text-center">
                            <span
                              className={`px-1.5 py-0.5 rounded text-[10px] font-bold ${
                                isCrcOk
                                  ? 'bg-emerald-950/80 text-emerald-400 border border-emerald-700/60'
                                  : 'bg-red-950/80 text-red-400 border border-red-700/60'
                              }`}
                            >
                              {isCrcOk ? 'CRC OK' : 'CRC ERR'}
                            </span>
                          </td>
                          <td className="py-1.5 px-3 text-zinc-400 font-mono truncate max-w-xs" title={payloadHex}>
                            {previewHex}
                            {payloadHex.length > 32 ? '…' : ''}
                          </td>
                          <td className="py-1.5 px-3 text-center whitespace-nowrap">
                            <div className="inline-flex items-center justify-center gap-1.5">
                              <button
                                type="button"
                                disabled={!pkt.payload_hex?.trim()}
                                onClick={event => { event.stopPropagation(); openDemodProtocol(pkt); }}
                                className="h-6 px-2 rounded bg-cyan-950/50 hover:bg-cyan-500 hover:text-slate-950 text-cyan-300 border border-cyan-500/40 text-[10px] font-bold transition-colors inline-flex items-center gap-1 disabled:opacity-40"
                                title="载入独立业务协议解析工作台"
                              >
                                <Braces className="w-3 h-3" />
                                <span>业务解析</span>
                              </button>
                              <button
                                type="button"
                                onClick={(e) => {
                                  e.stopPropagation();
                                  setSelectedDissectFrame(pkt);
                                }}
                                className="h-6 px-2 rounded bg-zinc-800 hover:bg-zinc-700 text-zinc-200 border border-zinc-700 text-[10px] font-bold transition-colors inline-flex items-center gap-1"
                                title="打开多层协议树与交互式 Hex 解剖"
                              >
                                <FileCode className="w-3 h-3 text-cyan-400" />
                                <span>解剖</span>
                              </button>
                            </div>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              )}
            </div>
          )}
        </section>
      </section>
      </div>

      {/* 弹窗：交互式协议多层解剖 (Wireshark-like Protocol Dissector) */}
      {selectedDissectFrame && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/75 backdrop-blur-xs p-3 sm:p-6"
          onClick={() => setSelectedDissectFrame(null)}
        >
          <div
            className="relative w-full max-w-6xl max-h-[90vh] bg-[#0d1117] border border-cyan-500/60 rounded-xl shadow-2xl overflow-hidden flex flex-col"
            onClick={(e) => e.stopPropagation()}
          >
            <ProtocolDissectorPanel
              frame={selectedDissectFrame}
              frameIndex={chronologicalDissectIndex}
              totalFrames={filteredPackets.length}
              allFrames={filteredPackets}
              onOpenProtocol={() => openDemodProtocol(selectedDissectFrame)}
              onClose={() => setSelectedDissectFrame(null)}
              onSelectPrev={
                activeDissectIndex < filteredPackets.length - 1
                  ? () => setSelectedDissectFrame(filteredPackets[activeDissectIndex + 1])
                  : undefined
              }
              onSelectNext={
                activeDissectIndex > 0
                  ? () => setSelectedDissectFrame(filteredPackets[activeDissectIndex - 1])
                  : undefined
              }
            />
          </div>
        </div>
      )}
    </div>
  );
};
