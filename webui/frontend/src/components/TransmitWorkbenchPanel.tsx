import { hexText } from '../utils/hex';
import React, { useState, useEffect, useMemo, useRef } from 'react';
import { useQuery } from '@tanstack/react-query';
import {
  Send,
  Layers,
  Radio,
  FileCode,
  Sliders,
  Square,
  Copy,
  RotateCw,
  Activity,
  ChevronDown,
  ExternalLink,
  Download,
  AlertCircle,
  Sparkles,
} from 'lucide-react';
import { api } from '../api/client';
import {
  ModulatePreviewResponse,
  FrameSlice,
} from '../types';
import { useWorkstationStore } from '../stores/useWorkstationStore';
import { submitTransmit, transmitRequest, transmitRequestCli } from '../utils/transmit';
import { useFrameField, useTransmitField, useRadioWorkbenchStore } from '../stores/useRadioWorkbenchStore';
import { PayloadTemplateDialog } from './PayloadTemplateDialog';
import { FrameSliceInspector } from './FrameSliceInspector';
import { CopyButton } from './CopyButton';

export const TransmitWorkbenchPanel: React.FC = () => {
  const { setActiveTab, setSpectrumTargetFreqHz, setSelectedRunId } = useWorkstationStore();

  // Preview includes live transmit; legacy live mode opens the same panel.
  const [storedMode, setMode] = useTransmitField('mode');
  const mode = storedMode === 'live' ? 'preview' : storedMode;
  const [synchronized, setSynchronized] = useTransmitField('synchronized');

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
  const [transmitError, setTransmitError] = useState('');
  const [pushedMessage, setPushedMessage] = useState<{ runId: string; requestId: string } | null>(null);
  const [templateDialogOpen, setTemplateDialogOpen] = useState(false);
  const previewVersion = useRef(0);
  const previewController = useRef<AbortController | null>(null);
  const syncRunId = useWorkstationStore(state => state.syncRunId);
  const { data: syncRun } = useQuery({
    queryKey: ['run', syncRunId], queryFn: () => api.getRun(syncRunId!),
    enabled: synchronized && mode !== 'export' && Boolean(syncRunId), refetchInterval: 1500,
  });
  const { data: syncSession } = useQuery({
    queryKey: ['duplexSession', syncRunId, syncRun?.attempt],
    queryFn: () => api.getSession(syncRunId!, syncRun?.attempt),
    enabled: synchronized && mode !== 'export' && Boolean(syncRunId) && syncRun?.id === syncRunId
      && syncRun?.status === 'RUNNING' && syncRun?.command_id === 'duplex' && Boolean(syncRun?.command_args.session),
    refetchInterval: 1000,
  });

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

  const draft = { mode, payloadHex: cleanHex, syncWord, codingRate, headerType, sampleRate, bitRate,
    amplitude, rampTimeUs, repeat, gapMs, outputPath, centerFreq, gain, sdr, synchronized, protocol };
  let request: ReturnType<typeof transmitRequest> | null = null;
  let requestError = '';
  try { request = transmitRequest(draft, { runId: syncRunId, run: syncRun, session: syncSession }); }
  catch (error) { requestError = (error as Error).message; }
  const cliCommand = request ? transmitRequestCli(request) : '';
  const previewBlocksSend = (!synchronized || mode === 'export') && (previewLoading || Boolean(previewError));
  const pushedResult = pushedMessage?.runId === syncRunId
    ? syncSession?.results.find(result => result.request_id === pushedMessage.requestId) : undefined;

  // A synchronized send submits to the existing workbench session.
  const handleStartTransmit = async () => {
    if (runAction.current || currentRunId || !isPayloadValid || previewBlocksSend) return;
    runAction.current = true;
    setRunActionPending(true);
    setTransmitError('');
    try {
      const result = await submitTransmit(draft, api, useWorkstationStore.getState().syncRunId);
      if (result.kind === 'session') setPushedMessage({ runId: result.runId, requestId: result.requestId });
      else setCurrentRunId(result.runId);
      setSelectedRunId(result.runId);
    } catch (err: any) {
      setTransmitError(err.message || '启动发射失败');
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

  const cliPreview = <details className="group/cli border-t border-rf-border pt-3">
    <summary className="flex cursor-pointer list-none items-center gap-2 text-xs font-medium text-zinc-400 hover:text-zinc-200 [&::-webkit-details-marker]:hidden">
      <FileCode className="h-4 w-4" aria-hidden="true" />等效 CLI 命令行预览
      <ChevronDown className="ml-auto h-4 w-4 transition-transform group-open/cli:rotate-180" aria-hidden="true" />
    </summary>
    <div className="space-y-2 pt-3">
      <div className="flex justify-end"><CopyButton text={cliCommand} label="复制命令" /></div>
      {cliCommand ? <pre className="whitespace-pre-wrap break-all rounded-lg border border-zinc-800 bg-zinc-950 p-3 font-mono text-xs text-emerald-300"><code>{cliCommand}</code></pre>
        : <p className="text-xs text-zinc-500">{requestError}</p>}
    </div>
  </details>;

  return (
    <div className="h-full min-w-0 overflow-y-auto bg-rf-bg text-rf-text p-4 md:p-6 space-y-6">
      {/* 顶部标题栏与工作模式切换 */}
      <div className="flex flex-wrap items-center justify-between gap-4 border-b border-rf-border pb-4">
        <div>
          <div className="flex items-center gap-3">
            <div className="p-2.5 bg-emerald-500/20 border border-emerald-500/40 rounded-xl text-emerald-400 shadow-lg shadow-emerald-950/20">
              <Send className="w-6 h-6" />
            </div>
            <div>
              <h1 className="text-xl font-bold text-zinc-100 flex flex-wrap items-center gap-2">
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
        <div
          role="tablist"
          aria-label="调制工作模式"
          className="flex flex-wrap items-center bg-rf-surface border border-rf-border rounded-xl p-1 gap-1"
          onKeyDown={event => {
            const modes = ['preview', 'export'] as const;
            const index = modes.indexOf(mode);
            const nextIndex = event.key === 'ArrowRight' ? (index + 1) % modes.length
              : event.key === 'ArrowLeft' ? (index + modes.length - 1) % modes.length
              : event.key === 'Home' ? 0 : event.key === 'End' ? modes.length - 1 : null;
            if (nextIndex === null) return;
            event.preventDefault();
            setMode(modes[nextIndex]);
            event.currentTarget.querySelectorAll<HTMLButtonElement>('[role="tab"]')[nextIndex]?.focus();
          }}
        >
          <button
            type="button"
            role="tab"
            id="transmit-tab-preview"
            aria-selected={mode === 'preview'}
            aria-controls="transmit-panel-preview"
            tabIndex={mode === 'preview' ? 0 : -1}
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
            role="tab"
            id="transmit-tab-export"
            aria-selected={mode === 'export'}
            aria-controls="transmit-panel-export"
            tabIndex={mode === 'export' ? 0 : -1}
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

      <section
        id={`transmit-panel-${mode}`}
        role="tabpanel"
        aria-labelledby={`transmit-tab-${mode}`}
        tabIndex={0}
        className="min-w-0 space-y-6"
      >
        <p className="text-xs text-zinc-400">
          {mode === 'preview'
            ? '物理帧剖析：调整载荷与调制参数，预览空口帧并发送；勾选同步后推送到同步工作台的已锁定会话。'
            : '离线 IQ 导出：设置输出文件路径，生成当前载荷对应的调制 IQ 文件。'}
        </p>

        {/* 实时发射与离线调制控制栏 */}
        <div data-transmit-controls className="bg-rf-surface border border-rf-border rounded-xl p-4 space-y-4 shadow-md">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <Radio className="w-4 h-4 text-emerald-400" />
              <h3 className="text-sm font-semibold text-zinc-100">
                {mode === 'export' ? '离线 IQ 生成目标配置' : 'SDR 实时发射控制'}
              </h3>
            </div>
            {mode !== 'export' && (
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
                  className="flex-1 px-4 py-2 bg-emerald-600 hover:bg-emerald-500 text-white rounded-lg text-xs font-medium transition-colors flex items-center justify-center gap-1.5 shadow"
                >
                  <Download className="w-4 h-4" />
                  <span>生成并导出 IQ 文件</span>
                </button>
              </div>
            </div>
          ) : (
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
              <div>
                <label className="block text-xs font-medium text-zinc-400 mb-1">SDR 硬件</label>
                <select
                  value={synchronized ? 'usrp' : sdr}
                  disabled={synchronized || runActionPending || Boolean(currentRunId)}
                  onChange={(e) => setSdr(e.target.value as any)}
                  className="w-full bg-rf-bg border border-rf-border rounded-lg px-2.5 py-1.5 text-xs text-zinc-200 font-mono focus:border-emerald-500 focus:outline-none"
                >
                  <option value="usrp" disabled>USRP（同步工作台）</option>
                  <option value="hackrf">HackRF One</option>
                </select>
              </div>

              <div>
                <label className="block text-xs font-medium text-zinc-400 mb-1">发射增益 (dB)</label>
                <input
                  type="number"
                  value={synchronized ? syncRun?.command_args.tx_gain ?? 30 : gain}
                  disabled={synchronized}
                  onChange={(e) => setGain(Number(e.target.value))}
                  className="w-full bg-rf-bg border border-rf-border rounded-lg px-2.5 py-1.5 text-xs text-zinc-200 font-mono focus:border-emerald-500 focus:outline-none"
                />
              </div>

              {synchronized ? <div>
                <span className="block text-xs font-medium text-zinc-400 mb-1">同步会话</span>
                <p className="py-1.5 text-xs text-zinc-300">{requestError ? '无法使用' : '已锁定 · 可推送'}</p>
              </div> : <div>
                <label className="block text-xs font-medium text-zinc-400 mb-1">重复次数 (0为持续)</label>
                <input
                  type="number"
                  min="0"
                  value={repeat}
                  onChange={(e) => setRepeat(Number(e.target.value))}
                  className="w-full bg-rf-bg border border-rf-border rounded-lg px-2.5 py-1.5 text-xs text-zinc-200 font-mono focus:border-emerald-500 focus:outline-none"
                />
              </div>}

              <div className="flex flex-wrap items-end gap-3">
                <label className="flex h-8 shrink-0 items-center gap-1.5 text-xs text-zinc-300">
                  <input type="checkbox" checked={synchronized} disabled={runActionPending || Boolean(currentRunId)}
                    onChange={event => { setSynchronized(event.target.checked); setTransmitError(''); }}
                    className="h-3.5 w-3.5 rounded border-rf-border bg-rf-bg accent-emerald-500" />同步
                </label>
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
                    disabled={runActionPending || !isPayloadValid || previewBlocksSend || Boolean(requestError)}
                    className="flex-1 px-4 py-2 bg-emerald-600 hover:bg-emerald-500 text-white rounded-lg text-xs font-medium transition-colors flex items-center justify-center gap-1.5 shadow disabled:opacity-50 disabled:cursor-not-allowed"
                  >
                    <Send className="w-4 h-4" />
                    <span>{synchronized ? '同步发送' : '立即空口发射'}</span>
                  </button>
                )}
              </div>
            </div>
          )}
          {mode !== 'export' && synchronized && <p className="text-xs leading-5 text-zinc-400">
            仅推送当前载荷；发送使用同步工作台当前会话的射频与物理参数。<button type="button" onClick={() => setActiveTab('sync')} className="ml-2 text-cyan-400 hover:text-cyan-300">前往同步工作台</button>
          </p>}
          {(transmitError || requestError) && <p role="alert" className="text-xs text-amber-300">{transmitError || requestError}</p>}
          {mode !== 'export' && synchronized && pushedMessage && <p role="status" className="text-xs text-emerald-300">
            {pushedResult ? pushedResult.error || `同步指令执行结果：${pushedResult.status}` : '指令已推送到同步工作台，等待时隙发送结果。'}
            <button type="button" className="ml-2 text-cyan-400" onClick={() => { setSelectedRunId(pushedMessage.runId); setActiveTab('tasks'); }}>查看任务与交互记录</button>
          </p>}
          {cliPreview}
        </div>

        {currentRunId && mode === 'export' && (
          <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-rf-border bg-rf-surface p-4">
            <span className="min-w-0 break-all text-xs text-zinc-300">当前运行任务：{currentRunId}</span>
            <button
              type="button"
              onClick={handleStopTransmit}
              disabled={runActionPending}
              className="flex items-center gap-1.5 rounded-lg bg-rose-600 px-3 py-2 text-xs text-white hover:bg-rose-500 disabled:opacity-50"
            >
              <Square className="h-4 w-4" />
              停止当前任务
            </button>
          </div>
        )}

        {previewError && (
          <div role="alert" className="flex items-start gap-2 rounded-lg border border-rose-500/40 bg-rose-500/10 p-3 text-xs text-rose-300">
            <AlertCircle className="h-4 w-4 shrink-0" />
            <span className="min-w-0 break-words">{previewError}</span>
          </div>
        )}

        {/* 载荷与预设模板选择栏 */}
        <div className="bg-rf-surface border border-rf-border rounded-xl p-4 space-y-4 shadow-sm">
          <div className="flex flex-wrap items-center justify-between gap-2 border-b border-rf-border/60 pb-3">
            <div className="flex items-center gap-2">
              <Sparkles className="w-4 h-4 text-emerald-400" />
              <span className="text-xs font-semibold text-zinc-200">协议载荷</span>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <button
                type="button"
                aria-haspopup="dialog"
                aria-expanded={templateDialogOpen}
                onClick={() => setTemplateDialogOpen(true)}
                className="px-3 py-1.5 rounded-lg bg-emerald-500/10 hover:bg-emerald-500/20 border border-emerald-500/30 text-emerald-300 text-xs transition-colors flex items-center gap-1.5"
              >
                <Layers className="w-3.5 h-3.5" aria-hidden="true" />
                选择模板
              </button>
            </div>
          </div>

          {/* 载荷输入框与基本参数 */}
          <div className="overflow-x-auto pb-1">
            <div className="grid min-w-[800px] grid-cols-[minmax(0,2fr)_repeat(3,minmax(0,1fr))] items-start gap-4">
              <div className="min-w-0 space-y-1.5">
                <div className="flex items-center justify-between gap-2">
                  <label htmlFor="transmit-payload" className="text-xs font-medium text-zinc-300">原始物理载荷</label>
                  <span className={`shrink-0 whitespace-nowrap font-mono text-[11px] ${isPayloadValid ? 'text-emerald-400' : 'text-rose-400 font-bold'}`}>
                    {payloadByteCount} 字节 ({payloadByteCount * 8} bit)
                  </span>
                </div>
                <textarea
                  id="transmit-payload"
                  rows={1}
                  value={hexText(payloadHex)}
                  onChange={(e) => setPayloadHex(e.target.value)}
                  placeholder="输入 6..127 字节十六进制载荷"
                  className={`block h-10 w-full resize-y bg-rf-bg border rounded-lg p-2.5 text-xs font-mono text-zinc-200 focus:outline-none transition-colors ${
                    isPayloadValid
                      ? 'border-rf-border focus:border-emerald-500'
                      : 'border-rose-500/80 focus:border-rose-400'
                  }`}
                />
                {!isPayloadValid && (
                  <p className="text-rose-400 font-mono text-[10px]">(需 6..127B 偶数 Hex)</p>
                )}
              </div>

              <div className="min-w-0 space-y-1.5">
                <label htmlFor="transmit-sync-word" className="block text-xs font-medium text-zinc-300">基准物理同步字</label>
                <input
                  id="transmit-sync-word"
                  type="text"
                  value={hexText(syncWord)}
                  onChange={(e) => setSyncWord(e.target.value)}
                  className="h-10 w-full bg-rf-bg border border-rf-border rounded-lg px-2.5 text-xs font-mono text-cyan-300 focus:border-emerald-500 focus:outline-none"
                />
              </div>

              <div className="min-w-0 space-y-1.5">
                <label htmlFor="transmit-frequency" className="block text-xs font-medium text-zinc-300">频率 (Hz)</label>
                <input
                  id="transmit-frequency"
                  type="number"
                  value={centerFreq}
                  onChange={(e) => setCenterFreq(Number(e.target.value))}
                  className="h-10 w-full bg-rf-bg border border-rf-border rounded-lg px-2.5 text-xs text-zinc-200 font-mono focus:border-emerald-500 focus:outline-none"
                />
              </div>

              <div className="min-w-0 space-y-1.5">
                <label htmlFor="transmit-sample-rate" className="block text-xs font-medium text-zinc-300">采样率 (Sps)</label>
                <input
                  id="transmit-sample-rate"
                  type="number"
                  value={sampleRate}
                  onChange={(e) => setSampleRate(Number(e.target.value))}
                  className="h-10 w-full bg-rf-bg border border-rf-border rounded-lg px-2.5 text-xs text-zinc-200 font-mono focus:border-emerald-500 focus:outline-none"
                />
              </div>
            </div>
          </div>

          {/* 高级物理与射频设置 (可折叠) */}
          <details className="group/advanced border-t border-rf-border/40 pt-3">
            <summary className="flex cursor-pointer list-none items-center gap-2 rounded-lg text-xs font-medium text-zinc-400 hover:text-zinc-200 focus-visible:outline focus-visible:outline-2 focus-visible:outline-emerald-400 [&::-webkit-details-marker]:hidden">
              <Sliders className="h-3.5 w-3.5" aria-hidden="true" />
              高级参数
              <ChevronDown className="ml-auto h-4 w-4 transition-transform group-open/advanced:rotate-180" aria-hidden="true" />
            </summary>
            <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-3 pt-4">
              <div>
                <span className="block text-xs text-zinc-400 mb-1">编码率与报头结构</span>
                <div className="grid grid-cols-2 gap-2">
                  <select
                    aria-label="编码率"
                    value={codingRate}
                    onChange={(e) => setCodingRate(e.target.value as any)}
                    className="w-full bg-rf-bg border border-rf-border rounded-lg p-1.5 text-xs text-zinc-200 font-mono focus:border-emerald-500 focus:outline-none"
                  >
                    <option value="3/4">CR 3/4</option>
                    <option value="1/2">CR 1/2</option>
                    <option value="1">CR 1 (No FEC)</option>
                  </select>
                  <select
                    aria-label="报头结构"
                    value={headerType}
                    onChange={(e) => setHeaderType(e.target.value as any)}
                    className="w-full bg-rf-bg border border-rf-border rounded-lg p-1.5 text-xs text-zinc-200 font-mono focus:border-emerald-500 focus:outline-none"
                  >
                    <option value="variable">可变头 (2B)</option>
                    <option value="fixed">固定头 (0B)</option>
                  </select>
                </div>
              </div>
              <div>
                <label htmlFor="transmit-bit-rate" className="block text-xs text-zinc-400 mb-1">空口码率 (Bitrate)</label>
                <select
                  id="transmit-bit-rate"
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
                <label htmlFor="transmit-ramp-time" className="block text-xs text-zinc-400 mb-1">爬升下降时间 (Ramp µs)</label>
                <select
                  id="transmit-ramp-time"
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
                <label htmlFor="transmit-amplitude" className="block text-xs text-zinc-400 mb-1">发射归一化幅度</label>
                <input
                  id="transmit-amplitude"
                  type="number"
                  step="0.05"
                  min="0.1"
                  max="1.0"
                  value={amplitude}
                  onChange={(e) => setAmplitude(Number(e.target.value))}
                  className="w-full bg-rf-bg border border-rf-border rounded-lg px-2.5 py-1.5 text-xs text-zinc-200 font-mono focus:border-emerald-500 focus:outline-none"
                />
              </div>

            </div>
            <div className="flex flex-wrap items-center gap-3 pt-4">
              <button type="button" className="text-xs text-cyan-400 hover:text-cyan-300" onClick={() => {
                setPayloadHex(Array.from({ length: 16 }, () => Math.floor(Math.random() * 256).toString(16).padStart(2, '0')).join(''));
              }}>随机16B</button>
              <button type="button" className="text-xs text-cyan-400 hover:text-cyan-300" onClick={() => {
                setPayloadHex(Array.from({ length: 16 }, (_, i) => i.toString(16).padStart(2, '0')).join(''));
              }}>递增序列</button>
              <button type="button" className="text-xs text-amber-400 hover:text-amber-300" title="对 4 字节同步字逐位取反" onClick={() => {
                const clean = syncWord.replace(/[^0-9a-fA-F]/g, '');
                if (clean.length === 8) {
                  setSyncWord((clean.match(/.{2}/g) ?? []).map(byte => (parseInt(byte, 16) ^ 0xff).toString(16).padStart(2, '0')).join(''));
                }
              }}>反转极性</button>
              <div className="ml-auto">
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
          </details>
        </div>

        {mode === 'preview' && (
          <div className="min-w-0 space-y-6">
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
                        <button
                          type="button"
                          key={sl.name}
                          aria-pressed={isSelected}
                          onClick={() => setSelectedSlice(sl)}
                          style={{ width: `${widthPct}%` }}
                          className={`h-full rounded-lg transition-all cursor-pointer flex flex-col justify-center items-center px-1 text-[10px] font-mono border relative overflow-hidden select-none ${
                            colorMap[sl.color] || colorMap.zinc
                          } ${isSelected ? 'ring-2 ring-white shadow-lg' : 'opacity-90'}`}
                          title={`${sl.name} (${sl.bits} bits, ${sl.bytes}B)`}
                        >
                          <span className="truncate font-bold">{sl.name}</span>
                          <span className="text-[9px] opacity-80">{sl.bits}b</span>
                        </button>
                      );
                    })}
                  </div>

                  {/* 选中切片详情卡片 */}
                  {selectedSlice && <FrameSliceInspector
                    key={protocol + ':' + selectedSlice.name + ':' + selectedSlice.hex_value}
                    slice={selectedSlice}
                    protocol={protocol}
                  />}
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

          </div>
        )}

        {/* 关联同步收发入口 */}
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

      </section>
      {templateDialogOpen && <PayloadTemplateDialog
        sampleRate={sampleRate}
        onClose={() => setTemplateDialogOpen(false)}
        onSelect={payload => {
          useRadioWorkbenchStore.getState().setFrame({ payloadHex: payload.payload_hex, protocol: payload.protocol });
          setTemplateDialogOpen(false);
        }}
      />}
    </div>
  );
};

export default TransmitWorkbenchPanel;
