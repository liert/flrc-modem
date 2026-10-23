import React, { useState, useEffect, useMemo, useRef } from 'react';
import { useQuery, useMutation } from '@tanstack/react-query';
import {
  Send,
  Layers,
  Radio,
  FileCode,
  Sliders,
  Play,
  Square,
  Copy,
  Check,
  RotateCw,
  Activity,
  Cpu,
  ChevronDown,
  ChevronUp,
  ExternalLink,
  Download,
  AlertCircle,
  FileText,
  Info,
  Clock,
  Sparkles,
} from 'lucide-react';
import { api } from '../api/client';
import {
  ModulatePreviewRequest,
  ModulatePreviewResponse,
  FrameSlice,
} from '../types';
import { useWorkstationStore } from '../stores/useWorkstationStore';
import { transmitParameters, transmitCli } from '../utils/transmit';
import { useFrameField, useTransmitField, useRadioWorkbenchStore } from '../stores/useRadioWorkbenchStore';

const PRESETS = [
  {
    name: 'DK32S 对频握手 (Pairing)',
    payload_hex: '011002030405060708090a',
    sync_word: '2bd36a07',
    protocol: 'dk32s' as const,
    desc: '遥控器与接收机对频第一阶段交互载荷 (11B)',
  },
  {
    name: 'DK32S 实时遥控 (Channels)',
    payload_hex: '200102030405060708090a0b0c0d0e0f',
    sync_word: '2bd36a07',
    desc: '包含 16 通道 PWM/SBUS 摇杆控制量的数据帧 (16B)',
    protocol: 'dk32s' as const,
  },
  {
    name: 'DataLink 遥测状态 (Telemetry)',
    payload_hex: 'ff550102030405060708090a',
    sync_word: 'b42b2bb4',
    protocol: 'datalink' as const,
    desc: '数传电台心跳包与空口信号质量遥测帧 (12B)',
  },
  {
    name: 'FLRC 极限大载荷 (Max Payload)',
    payload_hex: '0102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f202122232425262728292a2b2c2d2e2f303132333435363738393a3b3c3d3e3f40',
    sync_word: '2bd36a07',
    desc: '64 字节大容量固件升级或扩展配置帧 (64B)',
    protocol: 'dk32s' as const,
  },
  {
    name: 'DK32S 获取跳频表 (Get Hops)',
    payload_hex: '0123000000000000000000',
    sync_word: '2bd36a07',
    protocol: 'dk32s' as const,
    desc: '查询对端当前 20 频点跳频映射表 (11B)',
  },
  {
    name: 'SX1280 递增测试向量 (Pattern)',
    payload_hex: '000102030405060708090a0b0c0d0e0f',
    sync_word: 'b42b2bb4',
    protocol: 'datalink' as const,
    desc: '用于验证物理层解调位错误的 16 字节连续递增载荷 (16B)',
  },
];

export const TransmitWorkbenchPanel: React.FC = () => {
  const { setActiveTab, setSpectrumTargetFreqHz, setSelectedRunId } = useWorkstationStore();

  // Mode: preview, live transmit, offline IQ export
  const [mode, setMode] = useTransmitField('mode');

  // Modulation & Frame Parameters
  const [payloadHex, setPayloadHex] = useFrameField('payloadHex');
  const [syncWord, setSyncWord] = useFrameField('syncWord');
  const [codingRate, setCodingRate] = useFrameField('codingRate');
  const [headerType, setHeaderType] = useFrameField('headerType');
  const [bitRate, setBitRate] = useFrameField('bitRate');
  const [sampleRate, setSampleRate] = useFrameField('sampleRate');
  const [rampTimeUs, setRampTimeUs] = useFrameField('rampTimeUs');
  const [amplitude, setAmplitude] = useFrameField('amplitude');
  const [protocol, setProtocol] = useFrameField('protocol');

  // Live SDR Transmit Parameters
  const [sdr, setSdr] = useTransmitField('sdr');
  const [centerFreq, setCenterFreq] = useTransmitField('centerFreq');
  const [gain, setGain] = useTransmitField('gain');
  const [repeat, setRepeat] = useTransmitField('repeat');
  const [gapMs, setGapMs] = useTransmitField('gapMs');
  const [outputPath, setOutputPath] = useTransmitField('outputPath');

  // State & Results
  const [previewData, setPreviewData] = useState<ModulatePreviewResponse | null>(null);
  const [selectedSlice, setSelectedSlice] = useState<FrameSlice | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const currentRunId = useWorkstationStore(state => state.transmitRunId);
  const setCurrentRunId = useWorkstationStore(state => state.setTransmitRunId);
  const runAction = useRef(false);
  const [runActionPending, setRunActionPending] = useState(false);
  const [copied, setCopied] = useState(false);
  const [showConfig, setShowConfig] = useState(true);
  const previewVersion = useRef(0);
  const previewController = useRef<AbortController | null>(null);

  const { data: trackedRun } = useQuery({
    queryKey: ['run', currentRunId], queryFn: () => api.getRun(currentRunId!),
    enabled: Boolean(currentRunId), refetchInterval: 1500,
  });
  useEffect(() => {
    if (trackedRun?.id === currentRunId && ['SUCCEEDED', 'FAILED', 'CANCELLED'].includes(trackedRun.status)) {
      setCurrentRunId(null);
    }
  }, [trackedRun, currentRunId, setCurrentRunId]);

  // Validate payload length (6..127B)
  const cleanHex = useMemo(() => {
    return payloadHex.trim().replace(/\s+/g, '').replace(/^0x/i, '');
  }, [payloadHex]);

  const payloadByteCount = useMemo(() => {
    return cleanHex.length % 2 === 0 ? cleanHex.length / 2 : Math.floor(cleanHex.length / 2);
  }, [cleanHex]);

  const isPayloadValid = useMemo(() => {
    const isHex = /^[0-9a-fA-F]*$/.test(cleanHex);
    return isHex && payloadByteCount >= 6 && payloadByteCount <= 127 && cleanHex.length % 2 === 0;
  }, [cleanHex, payloadByteCount]);

  // Initial and reactive preview fetch
  const fetchPreview = async () => {
    const version = ++previewVersion.current;
    previewController.current?.abort();
    if (!isPayloadValid) return;
    const controller = new AbortController();
    previewController.current = controller;
    setPreviewLoading(true);
    setPreviewError(null);
    try {
      const res = await api.previewModulation({
        payload_hex: cleanHex,
        sync_word: syncWord,
        coding_rate: codingRate,
        header_type: headerType,
        bit_rate: bitRate,
        sample_rate: sampleRate,
        ramp_time_us: rampTimeUs,
        amplitude: amplitude,
      }, controller.signal);
      if (version !== previewVersion.current) return;
      setPreviewData(res);
      setSelectedSlice(previous => res.slices.find(slice => slice.name === previous?.name)
        ?? res.slices.find(slice => slice.name === 'Payload Data') ?? res.slices[0] ?? null);
    } catch (err: any) {
      if (version === previewVersion.current && !controller.signal.aborted) {
        setPreviewError(err.message || '预览生成失败');
      }
    } finally {
      if (version === previewVersion.current) setPreviewLoading(false);
    }
  };

  useEffect(() => {
    if (!isPayloadValid) {
      setPreviewData(null);
      setSelectedSlice(null);
      setPreviewError('请输入 6..127 字节的有效十六进制载荷');
      setPreviewLoading(false);
    }
    const timer = setTimeout(() => {
      void fetchPreview();
    }, 250);
    return () => {
      clearTimeout(timer);
      ++previewVersion.current;
      previewController.current?.abort();
    };
  }, [cleanHex, syncWord, codingRate, headerType, bitRate, sampleRate, rampTimeUs, amplitude]);

  // Start SDR Transmit Run
  const handleStartTransmit = async () => {
    if (runAction.current || currentRunId || !isPayloadValid || previewLoading || previewError) return;
    runAction.current = true;
    setRunActionPending(true);
    try {
      const params = transmitParameters({ mode, payloadHex: cleanHex, syncWord, codingRate, headerType,
        sampleRate, bitRate, amplitude, rampTimeUs, repeat, gapMs, outputPath, centerFreq, gain, sdr });

      const cmdId = mode === 'export' ? 'modulate' : 'transmit';

      const run = await api.createRun(
        cmdId,
        params,
        true,
        mode === 'export' ? 'Export Modulated IQ' : `Live SDR Transmit (${sdr.toUpperCase()})`
      );
      setCurrentRunId(run.id);
      setSelectedRunId(run.id);
    } catch (err: any) {
      alert(`启动发射失败: ${err.message}`);
    } finally {
      runAction.current = false;
      setRunActionPending(false);
    }
  };

  const handleStopTransmit = async () => {
    if (!currentRunId || runAction.current) return;
    runAction.current = true;
    setRunActionPending(true);
    try {
      await api.stopRun(currentRunId);
      setCurrentRunId(null);
    } catch (err: any) {
      alert(`停止失败: ${err.message}`);
    } finally {
      runAction.current = false;
      setRunActionPending(false);
    }
  };

  // Dynamic CLI Command Generation
  const cliCommand = useMemo(() => {
    return transmitCli({ mode, payloadHex: cleanHex, syncWord, codingRate, headerType,
      sampleRate, bitRate, amplitude, rampTimeUs, repeat, gapMs, outputPath, centerFreq, gain, sdr });
  }, [mode, cleanHex, syncWord, headerType, bitRate, codingRate, amplitude, rampTimeUs, outputPath, centerFreq, sampleRate, repeat, gapMs, sdr, gain]);

  const copyCli = () => {
    navigator.clipboard.writeText(cliCommand);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <div className="h-full flex flex-col overflow-y-auto bg-rf-bg text-rf-text p-4 md:p-6 space-y-6">
      {/* 顶部标题栏与工作模式切换 */}
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-4 border-b border-rf-border pb-4">
        <div>
          <div className="flex items-center gap-3">
            <div className="p-2.5 bg-emerald-500/20 border border-emerald-500/40 rounded-xl text-emerald-400 shadow-lg shadow-emerald-950/20">
              <Send className="w-6 h-6" />
            </div>
            <div>
              <h1 className="text-xl font-bold text-zinc-100 flex items-center gap-2">
                <span>调制发射与物理空口帧剖析工作台</span>
                <span className="text-xs px-2 py-0.5 rounded bg-emerald-950/60 border border-emerald-500/30 text-emerald-300 font-mono">
                  flrc transmit / modulate
                </span>
              </h1>
              <p className="text-xs text-zinc-400 mt-0.5">
                实时剖析 FLRC 空口帧结构切片、预览高斯频率成形与时域包络，并进行一键空口发射
              </p>
            </div>
          </div>
        </div>

        {/* 模式选择器 */}
        <div className="flex items-center bg-rf-surface border border-rf-border rounded-xl p-1 gap-1 self-start md:self-auto">
          <button
            type="button"
            onClick={() => setMode('preview')}
            className={`px-3 py-1.5 rounded-lg text-xs font-medium transition-all flex items-center gap-1.5 ${
              mode === 'preview'
                ? 'bg-emerald-600 text-white shadow'
                : 'text-zinc-400 hover:text-zinc-200 hover:bg-zinc-800/40'
            }`}
          >
            <Layers className="w-3.5 h-3.5" />
            <span>物理帧剖析 (PHY Preview)</span>
          </button>
          <button
            type="button"
            onClick={() => setMode('live')}
            className={`px-3 py-1.5 rounded-lg text-xs font-medium transition-all flex items-center gap-1.5 ${
              mode === 'live'
                ? 'bg-emerald-600 text-white shadow'
                : 'text-zinc-400 hover:text-zinc-200 hover:bg-zinc-800/40'
            }`}
          >
            <Radio className="w-3.5 h-3.5" />
            <span>SDR 实时发射 (Live Transmit)</span>
          </button>
          <button
            type="button"
            onClick={() => setMode('export')}
            className={`px-3 py-1.5 rounded-lg text-xs font-medium transition-all flex items-center gap-1.5 ${
              mode === 'export'
                ? 'bg-emerald-600 text-white shadow'
                : 'text-zinc-400 hover:text-zinc-200 hover:bg-zinc-800/40'
            }`}
          >
            <Download className="w-3.5 h-3.5" />
            <span>离线 IQ 调制导出 (Modulate)</span>
          </button>
        </div>
      </div>

      {/* 载荷与预设模板选择栏 */}
      <div className="rounded-xl border border-cyan-500/30 bg-cyan-500/5 p-4 space-y-3">
        <div className="text-sm font-semibold text-cyan-200">关联 FHSS / TDD 同步收发</div>
        <p className="text-xs text-zinc-400">将当前载荷、同步字、码率和编码配置带入 USRP 同步收发；会话先监听并锁定，再由您手动投递载荷、查看回复或编排周期任务。</p>
        <div className="flex flex-wrap gap-2">
          <button type="button" disabled={!isPayloadValid || previewLoading || Boolean(previewError) || protocol !== 'dk32s' || headerType !== 'variable'}
            className="px-3 py-2 text-xs rounded-lg bg-indigo-600 text-white disabled:opacity-50"
            onClick={() => { useRadioWorkbenchStore.getState().linkToSync(); setActiveTab('sync'); }}>
            带入 USRP 同步收发（DK32S）
          </button>
        </div>
        {(protocol === 'datalink' || headerType !== 'variable') && <p className="text-xs text-amber-300">实时联动采用 DK32S 变长头 / CRC24 同步模型，请调整协议和帧头后带入。</p>}
      </div>

      <div className="bg-rf-surface border border-rf-border rounded-xl p-4 space-y-4 shadow-sm">
        <div className="flex flex-wrap items-center justify-between gap-2 border-b border-rf-border/60 pb-3">
          <div className="flex items-center gap-2">
            <Sparkles className="w-4 h-4 text-emerald-400" />
            <span className="text-xs font-semibold text-zinc-200">快速协议载荷模板预设</span>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            {PRESETS.map((p, idx) => (
              <button
                key={idx}
                type="button"
                onClick={() => {
                  setPayloadHex(p.payload_hex);
                  setSyncWord(p.sync_word);
                  setProtocol(p.protocol);
                }}
                className="px-2.5 py-1 rounded-lg bg-zinc-800/80 hover:bg-zinc-700/80 border border-zinc-700 text-zinc-300 text-xs transition-colors flex items-center gap-1"
                title={p.desc}
              >
                <span>{p.name}</span>
              </button>
            ))}
          </div>
        </div>

        {/* 载荷输入框与基本参数 */}
        <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
          <div className="md:col-span-2 space-y-1.5">
            <div className="flex items-center justify-between text-xs">
              <label className="font-medium text-zinc-300 flex items-center gap-1.5">
                <span>原始物理载荷 (Payload Hex)</span>
                <span className="text-[10px] text-zinc-500 font-mono">6..127 Bytes</span>
              </label>
              <div className="flex items-center gap-2 font-mono text-[11px]">
                <button
                  type="button"
                  onClick={() => {
                    const bytes = Array.from({ length: 16 }, () => Math.floor(Math.random() * 256).toString(16).padStart(2, '0')).join('');
                    setPayloadHex(bytes);
                  }}
                  className="text-cyan-400 hover:text-cyan-300 text-[10px] underline"
                >
                  随机16B
                </button>
                <button
                  type="button"
                  onClick={() => {
                    const bytes = Array.from({ length: 16 }, (_, i) => i.toString(16).padStart(2, '0')).join('');
                    setPayloadHex(bytes);
                  }}
                  className="text-cyan-400 hover:text-cyan-300 text-[10px] underline"
                >
                  递增序列
                </button>
                <span
                  className={
                    isPayloadValid ? 'text-emerald-400' : 'text-rose-400 font-bold'
                  }
                >
                  {payloadByteCount} 字节 ({payloadByteCount * 8} bit)
                </span>
                {!isPayloadValid && (
                  <span className="text-rose-400 text-[10px]">
                    (需 6..127B 偶数 Hex)
                  </span>
                )}
              </div>
            </div>
            <textarea
              rows={2}
              value={payloadHex}
              onChange={(e) => setPayloadHex(e.target.value)}
              placeholder="输入十六进制原始载荷，如: 0102030405060708..."
              className={`w-full bg-rf-bg border rounded-lg p-2.5 text-xs font-mono text-zinc-200 focus:outline-none transition-colors ${
                isPayloadValid
                  ? 'border-rf-border focus:border-emerald-500'
                  : 'border-rose-500/80 focus:border-rose-400'
              }`}
            />
          </div>

          <div>
            <div className="flex items-center justify-between mb-1.5 text-xs">
              <label className="font-medium text-zinc-300">
                基准物理同步字 (4B Sync Word)
              </label>
              <button
                type="button"
                onClick={() => {
                  const clean = syncWord.replace(/[^0-9a-fA-F]/g, '');
                  if (clean.length === 8) {
                    const inverted = [];
                    for (let i = 0; i < 8; i += 2) {
                      const byte = parseInt(clean.slice(i, i + 2), 16);
                      inverted.push((byte ^ 0xff).toString(16).padStart(2, '0'));
                    }
                    setSyncWord(inverted.join(''));
                  }
                }}
                className="text-amber-400 hover:text-amber-300 text-[10px] underline font-mono"
                title="对 4 字节同步字逐位取反 (Bitwise Invert)"
              >
                反转极性
              </button>
            </div>
            <input
              type="text"
              value={syncWord}
              onChange={(e) => setSyncWord(e.target.value)}
              className="w-full bg-rf-bg border border-rf-border rounded-lg p-2 text-xs font-mono text-cyan-300 focus:border-emerald-500 focus:outline-none"
            />
            <div className="text-[10px] text-zinc-500 mt-1 font-mono">
              常用: 2bd36a07 (DK32S) / b42b2bb4
            </div>
          </div>

          <div>
            <label className="block text-xs font-medium text-zinc-300 mb-1.5">
              编码率与报头结构
            </label>
            <div className="grid grid-cols-2 gap-2">
              <select
                value={codingRate}
                onChange={(e) => setCodingRate(e.target.value as any)}
                className="w-full bg-rf-bg border border-rf-border rounded-lg p-1.5 text-xs text-zinc-200 font-mono focus:border-emerald-500 focus:outline-none"
              >
                <option value="3/4">CR 3/4</option>
                <option value="1/2">CR 1/2</option>
                <option value="1">CR 1 (No FEC)</option>
              </select>
              <select
                value={headerType}
                onChange={(e) => setHeaderType(e.target.value as any)}
                className="w-full bg-rf-bg border border-rf-border rounded-lg p-1.5 text-xs text-zinc-200 font-mono focus:border-emerald-500 focus:outline-none"
              >
                <option value="variable">可变头 (2B)</option>
                <option value="fixed">固定头 (0B)</option>
              </select>
            </div>
            <div className="text-[10px] text-zinc-500 mt-1">
              空口码率: {(bitRate / 1000).toFixed(0)} kbps
            </div>
          </div>
        </div>

        {/* 高级物理与射频设置 (可折叠) */}
        {showConfig && (
          <div className="grid grid-cols-2 md:grid-cols-5 gap-3 pt-3 border-t border-rf-border/40">
            <div>
              <label className="block text-xs text-zinc-400 mb-1">空口码率 (Bitrate)</label>
              <select
                value={bitRate}
                onChange={(e) => setBitRate(Number(e.target.value))}
                className="w-full bg-rf-bg border border-rf-border rounded-lg px-2.5 py-1.5 text-xs text-zinc-200 font-mono focus:border-emerald-500 focus:outline-none"
              >
                <option value={325000}>325 kbps (BW=260kHz)</option>
                <option value={650000}>650 kbps (BW=520kHz)</option>
                <option value={1300000}>1300 kbps (BW=1040kHz)</option>
              </select>
            </div>

            <div>
              <label className="block text-xs text-zinc-400 mb-1">采样率 (Sample Rate)</label>
              <input
                type="number"
                value={sampleRate}
                onChange={(e) => setSampleRate(Number(e.target.value))}
                className="w-full bg-rf-bg border border-rf-border rounded-lg px-2.5 py-1.5 text-xs text-zinc-200 font-mono focus:border-emerald-500 focus:outline-none"
              />
            </div>

            <div>
              <label className="block text-xs text-zinc-400 mb-1">爬升下降时间 (Ramp µs)</label>
              <select
                value={rampTimeUs}
                onChange={(e) => setRampTimeUs(Number(e.target.value))}
                className="w-full bg-rf-bg border border-rf-border rounded-lg px-2.5 py-1.5 text-xs text-zinc-200 font-mono focus:border-emerald-500 focus:outline-none"
              >
                <option value={8}>8 µs</option>
                <option value={12}>12 µs (标准)</option>
                <option value={16}>16 µs</option>
                <option value={20}>20 µs</option>
              </select>
            </div>

            <div>
              <label className="block text-xs text-zinc-400 mb-1">发射归一化幅度</label>
              <input
                type="number"
                step="0.05"
                min="0.1"
                max="1.0"
                value={amplitude}
                onChange={(e) => setAmplitude(Number(e.target.value))}
                className="w-full bg-rf-bg border border-rf-border rounded-lg px-2.5 py-1.5 text-xs text-zinc-200 font-mono focus:border-emerald-500 focus:outline-none"
              />
            </div>

            <div className="flex items-end">
              <button
                type="button"
                onClick={fetchPreview}
                className="w-full px-3 py-1.5 bg-emerald-600 hover:bg-emerald-500 text-white rounded-lg text-xs font-medium transition-colors flex items-center justify-center gap-1.5 shadow"
              >
                <RotateCw className={`w-3.5 h-3.5 ${previewLoading ? 'animate-spin' : ''}`} />
                <span>重新调制剖析</span>
              </button>
            </div>
          </div>
        )}
      </div>

      {/* 核心可视化 1: 物理空口帧切片剖析图 (PHY Frame Slices) */}
      <div className="bg-rf-surface border border-rf-border rounded-xl p-4 space-y-4 shadow-md">
        <div className="flex flex-col md:flex-row md:items-center justify-between gap-2">
          <div className="flex items-center gap-2">
            <Layers className="w-4 h-4 text-emerald-400" />
            <h3 className="text-sm font-semibold text-zinc-100">
              空中物理帧解剖切片图 (Air-Frame PHY Slices)
            </h3>
          </div>
          {previewData && (
            <div className="flex items-center gap-4 text-xs font-mono text-zinc-400">
              <span>总比特: {previewData.total_bits} bits</span>
              <span>折合字节: {previewData.total_bytes.toFixed(1)} Bytes</span>
              <span className="text-emerald-400 font-bold">
                空中发射时长: {previewData.duration_us.toFixed(1)} µs
              </span>
            </div>
          )}
        </div>

        {/* 多段彩色切片条 */}
        {previewData?.slices ? (
          <div className="space-y-3">
            <div className="w-full h-10 bg-zinc-950 border border-rf-border rounded-xl overflow-hidden flex shadow-inner p-1 gap-1">
              {previewData.slices.map((sl, idx) => {
                const isSelected = selectedSlice?.name === sl.name;
                const widthPct = Math.max(5, (sl.bits / previewData.total_bits) * 100);

                const colorMap: Record<string, string> = {
                  zinc: 'bg-zinc-700/80 hover:bg-zinc-600 text-zinc-200 border-zinc-600',
                  purple: 'bg-purple-700/80 hover:bg-purple-600 text-purple-200 border-purple-500',
                  cyan: 'bg-cyan-700/80 hover:bg-cyan-600 text-cyan-200 border-cyan-400',
                  blue: 'bg-blue-700/80 hover:bg-blue-600 text-blue-200 border-blue-400',
                  emerald: 'bg-emerald-700/80 hover:bg-emerald-600 text-emerald-200 border-emerald-400',
                  amber: 'bg-amber-700/80 hover:bg-amber-600 text-amber-200 border-amber-400',
                  rose: 'bg-rose-700/80 hover:bg-rose-600 text-rose-200 border-rose-400',
                };

                return (
                  <div
                    key={idx}
                    onClick={() => setSelectedSlice(sl)}
                    style={{ width: `${widthPct}%` }}
                    className={`h-full rounded-lg transition-all cursor-pointer flex flex-col justify-center items-center px-1 text-[10px] font-mono border relative overflow-hidden select-none ${
                      colorMap[sl.color] || colorMap.zinc
                    } ${isSelected ? 'ring-2 ring-white shadow-lg' : 'opacity-90'}`}
                    title={`${sl.name} (${sl.bits} bits, ${sl.bytes}B)`}
                  >
                    <span className="truncate font-bold">{sl.name}</span>
                    <span className="text-[9px] opacity-80">{sl.bits}b</span>
                  </div>
                );
              })}
            </div>

            {/* 选中切片详情卡片 */}
            {selectedSlice && (
              <div className="bg-rf-bg border border-rf-border rounded-xl p-3.5 flex flex-col md:flex-row md:items-center justify-between gap-4">
                <div className="space-y-1">
                  <div className="flex items-center gap-2">
                    <span className="font-bold text-sm text-zinc-200">{selectedSlice.name}</span>
                    <span className="text-xs px-2 py-0.5 rounded bg-zinc-800 text-zinc-400 font-mono border border-zinc-700">
                      {selectedSlice.bits} bits ({selectedSlice.bytes} Bytes)
                    </span>
                  </div>
                  <p className="text-xs text-zinc-400">{selectedSlice.description}</p>
                </div>
                {selectedSlice.hex_value && (
                  <div className="bg-zinc-950 px-3 py-2 rounded-lg border border-zinc-800 font-mono text-xs text-emerald-300 break-all select-all">
                    HEX: {selectedSlice.hex_value}
                  </div>
                )}
              </div>
            )}
          </div>
        ) : (
          <div className="h-24 flex items-center justify-center text-zinc-500 text-xs font-mono">
            正在生成物理空口帧切片...
          </div>
        )}
      </div>

      {/* 核心可视化 2: 时域幅度包络与瞬时频偏预览 */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {/* 归一化幅度包络预览 */}
        <div className="bg-rf-surface border border-rf-border rounded-xl p-4 space-y-3 shadow-md flex flex-col">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <Activity className="w-4 h-4 text-emerald-400" />
              <h3 className="text-sm font-semibold text-zinc-100">
                时域幅度包络 (Amplitude Envelope |IQ|)
              </h3>
            </div>
            <span className="text-xs text-zinc-500 font-mono">
              包含 {rampTimeUs}µs 线性爬升/下降
            </span>
          </div>

          <div className="flex-1 min-h-[160px] bg-rf-bg border border-rf-border rounded-lg p-3 relative flex flex-col justify-between overflow-hidden">
            <svg className="w-full h-full overflow-visible" viewBox="0 0 400 120" preserveAspectRatio="none">
              <line x1="0" y1="20" x2="400" y2="20" stroke="#27272a" strokeDasharray="3 3" />
              <text x="5" y="18" fill="#52525b" fontSize="9" fontFamily="monospace">
                Amp = 1.0
              </text>
              <line x1="0" y1="110" x2="400" y2="110" stroke="#3f3f46" strokeWidth={1} />
              <text x="5" y="105" fill="#52525b" fontSize="9" fontFamily="monospace">
                0.0
              </text>

              {previewData?.envelope_samples && previewData.envelope_samples.length > 1 && (
                <polyline
                  fill="none"
                  stroke="#10b981"
                  strokeWidth="2.5"
                  strokeLinejoin="round"
                  points={previewData.envelope_samples
                    .map((val, idx) => {
                      const x = (idx / (previewData.envelope_samples.length - 1)) * 400;
                      const y = 110 - (val / 1.0) * 90;
                      return `${x},${y}`;
                    })
                    .join(' ')}
                />
              )}
            </svg>
            <div className="flex items-center justify-between text-[10px] text-zinc-500 font-mono pt-1 border-t border-zinc-800">
              <span>0 µs (Ramp Up)</span>
              <span>发射持续期 ({previewData?.duration_us.toFixed(0)} µs)</span>
              <span>Ramp Down</span>
            </div>
          </div>
        </div>

        {/* 瞬时频偏调制轨迹 */}
        <div className="bg-rf-surface border border-rf-border rounded-xl p-4 space-y-3 shadow-md flex flex-col">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <Radio className="w-4 h-4 text-cyan-400" />
              <h3 className="text-sm font-semibold text-zinc-100">
                瞬时频率偏差 (Instantaneous Frequency Deviation)
              </h3>
            </div>
            <span className="text-xs text-zinc-500 font-mono">
              h=0.5 · ±{(bitRate / 2000).toFixed(1)} kHz
            </span>
          </div>

          <div className="flex-1 min-h-[160px] bg-rf-bg border border-rf-border rounded-lg p-3 relative flex flex-col justify-between overflow-hidden">
            <svg className="w-full h-full overflow-visible" viewBox="0 0 400 120" preserveAspectRatio="none">
              <line x1="0" y1="65" x2="400" y2="65" stroke="#3f3f46" strokeWidth={1} />
              <text x="5" y="60" fill="#71717a" fontSize="9" fontFamily="monospace">
                0 Hz
              </text>
              <line x1="0" y1="20" x2="400" y2="20" stroke="#27272a" strokeDasharray="3 3" />
              <text x="5" y="18" fill="#52525b" fontSize="9" fontFamily="monospace">
                +{(bitRate / 2000).toFixed(0)} kHz
              </text>
              <line x1="0" y1="110" x2="400" y2="110" stroke="#27272a" strokeDasharray="3 3" />
              <text x="5" y="105" fill="#52525b" fontSize="9" fontFamily="monospace">
                -{(bitRate / 2000).toFixed(0)} kHz
              </text>

              {previewData?.frequency_samples && previewData.frequency_samples.length > 1 && (
                <polyline
                  fill="none"
                  stroke="#38bdf8"
                  strokeWidth="2"
                  strokeLinejoin="round"
                  points={previewData.frequency_samples
                    .map((val, idx) => {
                      const x = (idx / (previewData.frequency_samples.length - 1)) * 400;
                      const maxDev = bitRate / 2;
                      const clamped = Math.max(-maxDev, Math.min(maxDev, val));
                      const y = 65 - (clamped / maxDev) * 45;
                      return `${x},${y}`;
                    })
                    .join(' ')}
                />
              )}
            </svg>
            <div className="flex items-center justify-between text-[10px] text-zinc-500 font-mono pt-1 border-t border-zinc-800">
              <span>前导比特</span>
              <span>高斯成形数据流 (BT=1.0)</span>
              <span>CRC & 尾比特</span>
            </div>
          </div>
        </div>
      </div>

      {/* 实时发射与离线调制控制栏 */}
      <div className="bg-rf-surface border border-rf-border rounded-xl p-4 space-y-4 shadow-md">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Radio className="w-4 h-4 text-emerald-400" />
            <h3 className="text-sm font-semibold text-zinc-100">
              {mode === 'export' ? '离线 IQ 生成目标配置' : 'SDR 实时发射控制'}
            </h3>
          </div>
          {mode === 'live' && (
            <button
              type="button"
              onClick={() => {
                setSpectrumTargetFreqHz(centerFreq);
                setActiveTab('spectrum');
              }}
              className="text-xs text-cyan-400 hover:text-cyan-300 flex items-center gap-1 transition-colors"
            >
              <span>前往 SDR 频谱监视</span>
              <ExternalLink className="w-3 h-3" />
            </button>
          )}
        </div>

        {mode === 'export' ? (
          <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
            <div className="md:col-span-2">
              <label className="block text-xs font-medium text-zinc-400 mb-1">
                输出 IQ 文件路径
              </label>
              <input
                type="text"
                value={outputPath}
                onChange={(e) => setOutputPath(e.target.value)}
                className="w-full bg-rf-bg border border-rf-border rounded-lg px-2.5 py-1.5 text-xs text-zinc-200 font-mono focus:border-emerald-500 focus:outline-none"
              />
            </div>
            <div className="flex items-end">
              <button
                type="button"
                onClick={handleStartTransmit}
                disabled={runActionPending || Boolean(currentRunId) || !isPayloadValid || previewLoading || Boolean(previewError)}
                className="w-full px-4 py-2 bg-emerald-600 hover:bg-emerald-500 text-white rounded-lg text-xs font-medium transition-colors flex items-center justify-center gap-1.5 shadow"
              >
                <Download className="w-4 h-4" />
                <span>生成并导出 IQ 文件</span>
              </button>
            </div>
          </div>
        ) : (
          <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
            <div>
              <label className="block text-xs font-medium text-zinc-400 mb-1">SDR 硬件</label>
              <select
                value={sdr}
                onChange={(e) => setSdr(e.target.value as any)}
                className="w-full bg-rf-bg border border-rf-border rounded-lg px-2.5 py-1.5 text-xs text-zinc-200 font-mono focus:border-emerald-500 focus:outline-none"
              >
                <option value="usrp" disabled>USRP（请先导出 IQ 后使用 tx）</option>
                <option value="hackrf">HackRF One</option>
              </select>
            </div>

            <div>
              <label className="block text-xs font-medium text-zinc-400 mb-1">中心发射频率 (Hz)</label>
              <input
                type="number"
                value={centerFreq}
                onChange={(e) => setCenterFreq(Number(e.target.value))}
                className="w-full bg-rf-bg border border-rf-border rounded-lg px-2.5 py-1.5 text-xs text-zinc-200 font-mono focus:border-emerald-500 focus:outline-none"
              />
            </div>

            <div>
              <label className="block text-xs font-medium text-zinc-400 mb-1">发射增益 (dB)</label>
              <input
                type="number"
                value={gain}
                onChange={(e) => setGain(Number(e.target.value))}
                className="w-full bg-rf-bg border border-rf-border rounded-lg px-2.5 py-1.5 text-xs text-zinc-200 font-mono focus:border-emerald-500 focus:outline-none"
              />
            </div>

            <div>
              <label className="block text-xs font-medium text-zinc-400 mb-1">重复次数 (0为持续)</label>
              <input
                type="number"
                min="0"
                value={repeat}
                onChange={(e) => setRepeat(Number(e.target.value))}
                className="w-full bg-rf-bg border border-rf-border rounded-lg px-2.5 py-1.5 text-xs text-zinc-200 font-mono focus:border-emerald-500 focus:outline-none"
              />
            </div>

            <div className="flex items-end">
              {currentRunId ? (
                <button
                  type="button"
                  onClick={handleStopTransmit}
                  disabled={runActionPending}
                  className="w-full px-4 py-2 bg-rose-600 hover:bg-rose-500 text-white rounded-lg text-xs font-medium transition-colors flex items-center justify-center gap-1.5 shadow"
                >
                  <Square className="w-4 h-4" />
                  <span>停止发射</span>
                </button>
              ) : (
                <button
                  type="button"
                  onClick={handleStartTransmit}
                  disabled={runActionPending || !isPayloadValid || previewLoading || Boolean(previewError)}
                  className="w-full px-4 py-2 bg-emerald-600 hover:bg-emerald-500 text-white rounded-lg text-xs font-medium transition-colors flex items-center justify-center gap-1.5 shadow"
                >
                  <Send className="w-4 h-4" />
                  <span>立即空口发射</span>
                </button>
              )}
            </div>
          </div>
        )}
      </div>

      {/* 底部 CLI 命令行预览与复制 */}
      <div className="bg-rf-surface border border-rf-border rounded-xl p-4 space-y-2 shadow-md">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Radio className="w-4 h-4 text-emerald-400" />
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
        <div className="bg-zinc-950 rounded-lg p-3 font-mono text-xs text-emerald-300 overflow-x-auto border border-zinc-800/80">
          <code>{cliCommand}</code>
        </div>
      </div>
    </div>
  );
};

export default TransmitWorkbenchPanel;
