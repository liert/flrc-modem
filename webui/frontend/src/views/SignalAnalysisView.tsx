import { hexText } from '../utils/hex';
import React, { useState, useMemo, useRef } from 'react';
import { useQuery } from '@tanstack/react-query';
import ReactECharts from 'echarts-for-react/lib/core';
import * as echarts from 'echarts/core';
import { LineChart, ScatterChart } from 'echarts/charts';
import { GridComponent, TooltipComponent, LegendComponent, MarkLineComponent } from 'echarts/components';
import { CanvasRenderer } from 'echarts/renderers';
import {
  Activity,
  FileCode,
  RefreshCw,
  Radio,
  Zap,
  ShieldCheck,
  TrendingUp,
  Download,
  Search,
} from 'lucide-react';
import { api } from '../api/client';
import { useWorkstationStore } from '../stores/useWorkstationStore';
import { analysisFiles, fileReadable } from '../utils/analysis';
import { runOption } from '../utils/runs';
import { buildSpectrumOption, buildWaveformOption } from '../utils/signalCharts';
import { RF_MONO_FONT_FAMILY } from '../utils/fonts';
import { frameKey, frameLinkable, linkedFrameIndex } from '../utils/frameSelection';
import type { FrameSelection } from '../utils/frameSelection';
import { ProtocolDissectorPanel } from '../components/ProtocolDissectorPanel';

echarts.use([LineChart, ScatterChart, GridComponent, TooltipComponent, LegendComponent, MarkLineComponent, CanvasRenderer]);
const SignalChart = React.memo(ReactECharts);
const chartStyle = { height: '100%', width: '100%' };
const chartOptions = { renderer: 'canvas' as const };

export const SignalAnalysisView: React.FC = () => {
  const { setSelectedArtifactPath, selectedRunId, setSelectedRunId } = useWorkstationStore();
  const [nFft, setNFft] = useState(1024);
  const [windowMs, setWindowMs] = useState(5);
  const [selection, setSelection] = useState<FrameSelection | null>(null);
  const analysisSectionRef = useRef<HTMLDivElement>(null);

  const { data: runs } = useQuery({
    queryKey: ['runs', 100, 0],
    queryFn: () => api.getRuns(100, 0),
    refetchInterval: 3000,
  });
  const analysisRuns = runs?.filter(run => ['demod', 'modulate', 'rx', 'tx', 'live', 'sync'].includes(run.command_id)) || [];
  const analysisRunId = selectedRunId || analysisRuns.find(run => run.command_id === 'demod')?.id || analysisRuns[0]?.id || '';
  const { data: context, error: contextError, refetch: refetchContext } = useQuery({
    queryKey: ['analysisContext', analysisRunId],
    queryFn: () => api.getRunAnalysis(analysisRunId),
    enabled: Boolean(analysisRunId),
    refetchInterval: 3000,
    refetchOnWindowFocus: true,
  });
  const running = context?.status === 'RUNNING' || context?.status === 'QUEUED';

  const contextReady = Boolean(context && context.run_id === analysisRunId);
  const sampleRate = contextReady ? context?.sample_rate ?? NaN : NaN;
  const centerFreq = contextReady ? context?.center_frequency ?? NaN : NaN;
  const iqFormat = contextReady ? context?.iq_format ?? '' : '';
  const { path: activePath, framesPath: correspondingJsonl } = analysisFiles(null, contextReady ? context : undefined);
  const isIqPath = /\.(iq|cs8|cu8|cf32)$/i.test(activePath);
  const iqState = context?.file_states?.iq;
  const framesState = context?.file_states?.frames;
  const canReadIq = contextReady && fileReadable(iqState) && Boolean(iqFormat);
  const canReadFrames = contextReady && fileReadable(framesState);
  const validSampling = Number.isFinite(sampleRate) && sampleRate > 0 && Number.isFinite(centerFreq);

  // 查询解码帧列表
  const { data: rawFramesData, error: framesError, refetch: refetchFrames } = useQuery({
    queryKey: ['frames', analysisRunId, correspondingJsonl, context?.status, framesState?.revision],
    queryFn: () => api.getDecodedFrames(correspondingJsonl, 500, analysisRunId || undefined),
    enabled: Boolean(correspondingJsonl) && canReadFrames,
    refetchInterval: running ? 2000 : false,
  });
  const framesData = canReadFrames ? rawFramesData : undefined;
  const canLinkFrames = contextReady && context?.frame_time_reference === 'iq_start' && canReadIq && canReadFrames && validSampling;
  const selectedIndex = framesData?.frames ? linkedFrameIndex(selection, analysisRunId, framesData.frames) : -1;
  const selectedFrame = selectedIndex >= 0 ? framesData?.frames[selectedIndex] : undefined;
  const linkedFrames = canLinkFrames ? framesData?.frames.map((frame, index) => ({ frame, index })).filter(item => frameLinkable(item.frame)) || [] : [];
  const linkedPosition = linkedFrames.findIndex(item => item.index === selectedIndex);
  const selectFrame = (index: number, scroll = false) => {
    const frame = framesData?.frames[index];
    if (!frame || !frameLinkable(frame)) return;
    setSelection({ runId: analysisRunId, key: frameKey(frame) });
    if (scroll && canLinkFrames) analysisSectionRef.current?.scrollIntoView({ block: 'start', behavior: 'auto' });
  };

  const [frameFilter, setFrameFilter] = useState('');
  const filteredFramesList = useMemo(() => {
    const list = framesData?.frames || [];
    if (!frameFilter.trim()) return list;
    const q = frameFilter.trim().toLowerCase();
    return list.filter(f =>
      (f.sync_word_hex && f.sync_word_hex.toLowerCase().includes(q)) ||
      (f.payload_hex && f.payload_hex.toLowerCase().includes(q)) ||
      (f.hardware_header_hex && f.hardware_header_hex.toLowerCase().includes(q)) ||
      (f.application?.protocol && f.application.protocol.toLowerCase().includes(q))
    );
  }, [framesData?.frames, frameFilter]);

  const handleExportFramesJson = () => {
    const blob = new Blob([JSON.stringify(framesData?.frames || [], null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `decoded_frames_${Date.now()}.json`;
    a.click();
    URL.revokeObjectURL(url);
  };
  const { data: rawWindowData, error: windowError, isFetching: fetchingWindow, refetch: refetchWindow } = useQuery({
    queryKey: ['signalWindow', analysisRunId, iqState?.revision, selectedFrame ? framesState?.revision : null,
      iqFormat, sampleRate, centerFreq, nFft, windowMs, selectedFrame?.time_s ?? null],
    queryFn: ({ signal }) => api.getSignalWindow(analysisRunId, nFft, windowMs, selectedFrame?.time_s, signal),
    enabled: contextReady && isIqPath && validSampling && canReadIq,
    staleTime: Infinity,
    gcTime: 60000,
  });
  const windowData = canReadIq && !windowError ? rawWindowData : undefined;
  const spectrumData = windowData?.spectrum;
  const constellationData = windowData?.constellation;
  const waveformOption = useMemo(() => buildWaveformOption(windowData?.waveform.points, windowData?.frame_time_s),
    [windowData?.waveform.points, windowData?.frame_time_s]);

  // 频谱 ECharts 配置（标题统一在卡片 HTML 头部展示，避免在 Canvas 内部与 Y 轴刻度堆叠重合）
  const spectrumOption = useMemo(() => buildSpectrumOption(spectrumData?.points), [spectrumData?.points]);

  // 星座图 ECharts 配置（标题统一在卡片 HTML 头部展示，中心十字坐标轴对齐）
  const constellationOption = useMemo(() => {
    if (!constellationData?.points) return {};
    const points = constellationData.points;

    return {
      backgroundColor: 'transparent',
      animation: false,
      tooltip: {
        confine: true,
        transitionDuration: 0,
        backgroundColor: '#161b22',
        borderColor: '#30363d',
        textStyle: { color: '#f0f6fc' },
        formatter: (param: any) => {
          return `<div class="font-mono text-xs"><span class="text-cyan-400">I (同相):</span> ${param.value[0]}<br/><span class="text-emerald-400">Q (正交):</span> ${param.value[1]}</div>`;
        },
      },
      grid: {
        left: 45,
        right: 25,
        top: 32,
        bottom: 44,
      },
      xAxis: {
        type: 'value',
        min: -1.2,
        max: 1.2,
        axisLine: { onZero: true, lineStyle: { color: '#444c56' } },
        splitLine: { lineStyle: { color: '#1f242c', type: 'dashed' } },
        axisLabel: { color: '#8b949e', fontSize: 10, fontFamily: RF_MONO_FONT_FAMILY },
        name: 'I',
        nameTextStyle: { color: '#8b949e', fontSize: 10, fontFamily: RF_MONO_FONT_FAMILY },
      },
      yAxis: {
        type: 'value',
        min: -1.2,
        max: 1.2,
        axisLine: { onZero: true, lineStyle: { color: '#444c56' } },
        splitLine: { lineStyle: { color: '#1f242c', type: 'dashed' } },
        axisLabel: { color: '#8b949e', fontSize: 10, fontFamily: RF_MONO_FONT_FAMILY },
        name: 'Q',
        nameTextStyle: { color: '#8b949e', fontSize: 10, fontFamily: RF_MONO_FONT_FAMILY },
      },
      series: [
        {
          name: 'IQ 样本',
          type: 'scatter',
          data: points,
          symbolSize: 4.5,
          itemStyle: {
            color: '#00ff66',
            opacity: 0.75,
          },
        },
      ],
    };
  }, [constellationData?.points]);

  return (
    <div className="p-6 space-y-5 max-w-7xl mx-auto select-none">
      {/* 顶部控制栏 */}
      <div className="bg-gradient-to-r from-rf-surface via-rf-panel/40 to-rf-surface border border-rf-border p-4 rounded-xl flex flex-wrap items-center justify-between gap-4 shadow-md relative overflow-hidden">
        <div className="absolute top-0 left-0 right-0 h-[2px] bg-gradient-to-r from-cyan-500/60 via-emerald-400/30 to-transparent" />
        <div className="flex items-center space-x-3">
          <div className="w-9 h-9 rounded-lg bg-cyan-500/10 border border-cyan-500/30 flex items-center justify-center shadow-inner">
            <Activity className="w-5 h-5 text-cyan-400" />
          </div>
          <div>
            <h2 className="text-sm font-bold text-rf-text tracking-wide">信号与载荷分析工作台</h2>
            <p className="text-[11px] text-zinc-400">
              IQ 频谱分析、星座相位与硬件 CRC 解码报文检视
            </p>
          </div>
        </div>

        {/* 任务选择与显示设置 */}
        <div className="flex flex-wrap items-center gap-3 text-xs font-mono">
          <label className="flex items-center space-x-1.5">
            <span className="text-zinc-400 font-semibold">任务:</span>
            <select
              value={analysisRunId}
              onChange={event => {
                setSelectedRunId(event.target.value || null);
                setSelectedArtifactPath(null);
              }}
              className="max-w-xs bg-rf-bg border border-rf-border rounded-lg px-3 py-1.5 text-rf-text focus:outline-none focus:border-cyan-400 focus:ring-1 focus:ring-cyan-400/40 transition-all font-mono"
            >
              <option value="">选择分析任务</option>
              {context && !analysisRuns.some(run => run.id === context.run_id) && (
                <option value={context.run_id}>{context.run_id}</option>
              )}
              {analysisRuns.map(run => (
                <option key={run.id} value={run.id}>{runOption(run)}</option>
              ))}
            </select>
          </label>
          <label className="flex items-center space-x-1.5">
            <span className="text-zinc-400 font-semibold">FFT 点数:</span>
            <select
              value={nFft}
              onChange={e => setNFft(Number(e.target.value))}
              className="bg-rf-bg border border-rf-border rounded-lg px-2.5 py-1.5 text-rf-text focus:outline-none focus:border-cyan-400 focus:ring-1 focus:ring-cyan-400/40 transition-all font-mono"
            >
              {[256, 512, 1024, 2048, 4096, 8192].map(size => (
                <option key={size} value={size}>{size}</option>
              ))}
            </select>
          </label>

          <button
            onClick={() => {
              if (analysisRunId) refetchContext();
              if (isIqPath && validSampling && canReadIq) refetchWindow();
              if (correspondingJsonl && canReadFrames) refetchFrames();
            }}
            className="p-2 rounded-lg bg-rf-panel hover:bg-rf-border text-zinc-300 border border-rf-border/60 transition-all shadow-sm"
            title="刷新图表"
          >
            <RefreshCw
              className={`w-3.5 h-3.5 ${
                fetchingWindow ? 'animate-spin text-cyan-400' : ''
              }`}
            />
          </button>
        </div>
      </div>

      {/* 任务参数与文件快照卡片 */}
      <div className="bg-rf-surface border border-rf-border p-4 rounded-xl text-xs space-y-3.5 shadow-sm">
        <p className="text-zinc-400 font-sans">
          {contextReady ? '以下信息来自任务记录，只读。FFT 点数仅影响频谱显示；修改解调参数请新建任务。'
            : contextError ? '任务记录读取失败。' : analysisRunId ? '正在读取任务记录…' : '请先运行或选择一个分析任务。'}
        </p>
        {contextReady && (
          <>
            <dl className="grid grid-cols-1 sm:grid-cols-3 gap-3 font-mono">
              <div className="bg-rf-bg/70 p-2.5 rounded-lg border border-rf-border/60">
                <dt className="text-zinc-500 text-[11px]">中心频率</dt>
                <dd className="mt-1 text-cyan-300 font-bold">{Number.isFinite(centerFreq) ? `${centerFreq} Hz` : '未记录'}</dd>
              </div>
              <div className="bg-rf-bg/70 p-2.5 rounded-lg border border-rf-border/60">
                <dt className="text-zinc-500 text-[11px]">采样率</dt>
                <dd className="mt-1 text-cyan-300 font-bold">{Number.isFinite(sampleRate) ? `${sampleRate} S/s` : '未记录'}</dd>
              </div>
              <div className="bg-rf-bg/70 p-2.5 rounded-lg border border-rf-border/60">
                <dt className="text-zinc-500 text-[11px]">IQ 格式</dt>
                <dd className="mt-1 text-cyan-300 font-bold uppercase">{iqFormat || '未记录'}</dd>
              </div>
            </dl>
            <dl className="space-y-2 font-mono">
              <div className="bg-rf-bg/50 p-2 rounded-lg border border-rf-border/40">
                <dt className="text-zinc-500 text-[10px]">IQ 文件</dt>
                <dd className="mt-0.5 text-zinc-300 break-all select-text text-xs">{activePath || '未配置'}</dd>
              </div>
              <div className="bg-rf-bg/50 p-2 rounded-lg border border-rf-border/40">
                <dt className="text-zinc-500 text-[10px]">JSONL 文件</dt>
                <dd className="mt-0.5 text-zinc-300 break-all select-text text-xs">{correspondingJsonl || '未配置'}</dd>
              </div>
            </dl>
          </>
        )}
      </div>

      {context && ([['IQ', iqState], ['JSONL', framesState]] as const).map(([label, state]) => state?.message && (
        <p key={label} role="status" className={`text-xs font-mono p-2.5 rounded-lg border ${state.readable ? 'bg-amber-950/40 text-amber-300 border-amber-800/60' : 'bg-red-950/40 text-red-300 border-red-800/60'}`}>
          {label}：{state.message}
          {state.status === 'overwritten' && state.current_run_id && (
            <button className="ml-2 underline text-cyan-400 hover:text-cyan-300" onClick={() => {
              setSelectedRunId(state.current_run_id);
              setSelectedArtifactPath(null);
            }}>
              查看覆盖它的任务
            </button>
          )}
        </p>
      ))}

      {(windowError || framesError || contextError || context?.metadata_error || (contextReady && isIqPath && (!validSampling || !iqFormat))) && (
        <p role="alert" className="text-xs text-red-300 font-mono p-3 rounded-lg bg-red-950/50 border border-red-800/80">
          {context?.metadata_error || (windowError || framesError || contextError)?.message || '任务记录缺少有效的采样率、中心频率或 IQ 格式，无法生成对应的 IQ 图表。'}
        </p>
      )}

      {/* 核心指标统计卡片（采用顶部固定 2px 颜色条，消除浮动遮挡） */}
      <div className="grid grid-cols-1 md:grid-cols-4 gap-4 font-mono">
        <div className="bg-rf-surface border border-rf-border border-t-2 border-t-cyan-400 p-4 rounded-xl shadow-sm hover:border-rf-border/90 transition-all">
          <div className="text-[11px] text-zinc-400 font-semibold flex items-center space-x-2">
            <Radio className="w-4 h-4 text-cyan-400" />
            <span>峰值载波频率 (Peak Freq)</span>
          </div>
          <div className="text-xl font-bold text-cyan-400 mt-2 tracking-tight">
            {spectrumData?.peak_frequency_hz
              ? `${(spectrumData.peak_frequency_hz / 1e6).toFixed(3)} MHz`
              : '未知'}
          </div>
          <div className="text-[10px] text-zinc-500 mt-1 font-sans">实测 PSD 谱峰位置</div>
        </div>

        <div className="bg-rf-surface border border-rf-border border-t-2 border-t-cyan-400 p-4 rounded-xl shadow-sm hover:border-rf-border/90 transition-all">
          <div className="text-[11px] text-zinc-400 font-semibold flex items-center space-x-2">
            <Zap className="w-4 h-4 text-cyan-400" />
            <span>峰值功率强度 (Peak Power)</span>
          </div>
          <div className="text-xl font-bold text-rf-text mt-2 tracking-tight">
            {spectrumData?.peak_power_db != null
              ? `${spectrumData.peak_power_db.toFixed(1)} dB/Hz`
              : '未知'}
          </div>
          <div className="text-[10px] text-zinc-500 mt-1 font-sans">相对 IQ 幅度的功率谱密度</div>
        </div>

        <div className="bg-rf-surface border border-rf-border border-t-2 border-t-emerald-400 p-4 rounded-xl shadow-sm hover:border-rf-border/90 transition-all">
          <div className="text-[11px] text-zinc-400 font-semibold flex items-center space-x-2">
            <Activity className="w-4 h-4 text-emerald-400" />
            <span>包络幅度变异系数</span>
          </div>
          <div className="text-xl font-bold text-emerald-400 mt-2 tracking-tight">
            {constellationData?.evm_estimate_percent != null
              ? `${constellationData.evm_estimate_percent}%`
              : '未知'}
          </div>
          <div className="text-[10px] text-zinc-500 mt-1 font-sans">包络方差估计值</div>
        </div>

        <div className="bg-rf-surface border border-rf-border border-t-2 border-t-cyan-400 p-4 rounded-xl shadow-sm hover:border-rf-border/90 transition-all">
          <div className="text-[11px] text-zinc-400 font-semibold flex items-center space-x-2">
            <ShieldCheck className="w-4 h-4 text-cyan-400" />
            <span>硬件 CRC 校验通过帧</span>
          </div>
          <div className="text-xl font-bold text-cyan-300 mt-2 tracking-tight">
            {canReadFrames ? framesData?.total ?? 0 : '不可用'}{' '}
            <span className="text-xs font-normal text-zinc-400 font-sans">帧</span>
          </div>
          <div className="text-[10px] text-zinc-500 mt-1 font-sans">
            {running ? '解调中，自动刷新' : '当前读取记录数（最多 500 帧）'}
          </div>
        </div>
      </div>

      <div ref={analysisSectionRef} className="scroll-mt-6 bg-rf-surface border border-rf-border rounded-xl p-4 space-y-3">
        <div className="flex flex-wrap items-center justify-between gap-3 text-xs">
          <div className="font-mono text-cyan-300">
            {selectedFrame ? `已定位第 ${selectedIndex + 1} 帧 · ${(selectedFrame.time_s * 1000).toFixed(3)} ms · ${(selectedFrame.frequency_hz / 1e6).toFixed(6)} MHz` : 'IQ 文件开头预览'}
          </div>
          <div className="flex flex-wrap items-center gap-3">
            <label className="flex items-center gap-2 text-zinc-400">观察窗口
              <select aria-label="IQ 观察窗口长度" value={windowMs} onChange={event => setWindowMs(Number(event.target.value))}
                className="bg-rf-bg border border-rf-border rounded px-2 py-1 text-rf-text">
                {[0.5, 1, 2, 5, 10, 20].map(value => <option key={value} value={value}>{value} ms</option>)}
              </select>
            </label>
            <button disabled={linkedPosition <= 0} onClick={() => selectFrame(linkedFrames[linkedPosition - 1].index)}
              className="text-cyan-400 disabled:text-zinc-600">前一帧</button>
            <button disabled={linkedPosition + 1 >= linkedFrames.length} onClick={() => selectFrame(linkedFrames[linkedPosition + 1].index)}
              className="text-cyan-400 disabled:text-zinc-600">{selectedFrame ? '后一帧' : '定位首帧'}</button>
            <button disabled={!selectedFrame} onClick={() => setSelection(null)} className="text-zinc-400 disabled:text-zinc-600">返回文件开头</button>
          </div>
        </div>
        <p role="status" className="text-xs text-zinc-400">
          {fetchingWindow ? '正在加载 IQ 片段…' : windowData
            ? `观察范围 ${(windowData.start_time_s * 1000).toFixed(3)}–${(windowData.end_time_s * 1000).toFixed(3)} ms，共 ${windowData.sample_count} 个采样点。`
            : '暂无可读取的 IQ 片段。'}
          {windowData && ` 频谱与星座图从${selectedFrame ? '帧起点' : '文件开头'}读取 ${windowData.fft_sample_count} 点。`}
          {windowData?.truncated && ' 窗口已截到文件末尾。'}
        </p>
        {canLinkFrames ? <p className="text-xs text-zinc-500">点击下方帧时间戳可定位附近 IQ；黄色虚线标出帧起点。窗口不足一个 FFT 时自动扩展。I/Q 为原始采样，未经过符号同步。</p>
          : correspondingJsonl && <p className="text-xs text-zinc-500">{!context?.iq_path ? '此任务没有保存 IQ，仍可查看下方载荷记录。' : '此任务当前没有可与文件时间对应的帧记录，不能按帧定位 IQ。'}</p>}
        {spectrumData?.error && <p role="status" className="text-xs text-amber-400">{spectrumData.error}</p>}
      </div>

      <div className="min-w-0 bg-rf-surface border border-rf-border rounded-xl overflow-hidden shadow-md">
        <div className="p-3.5 border-b border-rf-border bg-rf-panel/40 text-xs font-bold text-rf-text flex flex-wrap items-center justify-between gap-2">
          <span>时域波形 · I / Q / 包络（抽样显示，保留包络峰谷）</span>
          <span className="text-zinc-400 font-normal">时间相对于 IQ 文件开头</span>
        </div>
        <div className="p-2 h-64 sm:h-72 w-full">
          <SignalChart echarts={echarts} notMerge lazyUpdate option={waveformOption} style={chartStyle} opts={chartOptions} />
        </div>
      </div>

      {/* 频谱与星座图图表网格（独立卡片头部 + 规范化高度容器） */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">
        {/* 功率谱密度卡片 */}
        <div className="min-w-0 bg-rf-surface border border-rf-border rounded-xl overflow-hidden shadow-md flex flex-col">
          <div className="min-h-14 shrink-0 p-3.5 border-b border-rf-border bg-rf-panel/40 flex flex-wrap items-center justify-between gap-2">
            <div className="min-w-0 flex items-center space-x-2 font-mono">
              <Activity className="w-4 h-4 shrink-0 text-cyan-400" />
              <h3 className="text-xs font-bold uppercase text-rf-text">
                功率谱密度 (PSD / FFT Spectrum)
              </h3>
            </div>
            <span className="shrink-0 text-[11px] font-mono text-zinc-400 bg-rf-panel px-2 py-0.5 rounded border border-rf-border/50">
              FFT: {spectrumData?.n_fft || nFft} 点
            </span>
          </div>
          {/* 固定绘图区高度，避免 flex-basis: 0 将百分比高度的 Canvas 压缩。 */}
          <div className="p-2 h-80 sm:h-96 w-full flex-none">
            <SignalChart
              echarts={echarts}
              notMerge
              lazyUpdate
              option={spectrumOption}
              style={chartStyle}
              opts={chartOptions}
            />
          </div>
        </div>

        {/* 星座图卡片 */}
        <div className="min-w-0 bg-rf-surface border border-rf-border rounded-xl overflow-hidden shadow-md flex flex-col">
          <div className="min-h-14 shrink-0 p-3.5 border-b border-rf-border bg-rf-panel/40 flex flex-wrap items-center justify-between gap-2">
            <div className="min-w-0 flex items-center space-x-2 font-mono">
              <Radio className="w-4 h-4 shrink-0 text-emerald-400" />
              <h3 className="text-xs font-bold uppercase text-rf-text">
                I/Q 星座相位图 (Constellation Diagram)
              </h3>
            </div>
            <span className="shrink-0 text-[11px] font-mono text-zinc-400 bg-rf-panel px-2 py-0.5 rounded border border-rf-border/50">
              样本数: {constellationData?.points?.length || 0}
            </span>
          </div>
          <div className="p-2 h-80 sm:h-96 w-full flex-none">
            <SignalChart
              echarts={echarts}
              notMerge
              lazyUpdate
              option={constellationOption}
              style={chartStyle}
              opts={chartOptions}
            />
          </div>
        </div>
      </div>

      {/* 底部：解调帧与报文详情查看器 */}
      <div className="bg-rf-surface border border-rf-border rounded-xl overflow-hidden shadow-md">
        <div className="p-3.5 border-b border-rf-border bg-rf-panel/50 flex flex-wrap items-center justify-between gap-2">
          <div className="flex items-center space-x-2">
            <FileCode className="w-4 h-4 text-cyan-400" />
            <h3 className="text-xs font-bold font-mono uppercase text-rf-text">
              解调帧记录 ({correspondingJsonl || '未关联'})
            </h3>
            <span className="text-[10px] text-zinc-400 font-sans hidden sm:inline">
              (点击任意行即可在下方展开 Wireshark 协议树逐层解析)
            </span>
          </div>
          <div className="flex items-center space-x-2 flex-wrap gap-2">
            <div className="relative">
              <Search className="w-3.5 h-3.5 text-zinc-400 absolute left-2 top-1.5" />
              <input
                type="search"
                placeholder="过滤同步字 / 载荷 Hex..."
                value={frameFilter}
                onChange={(e) => setFrameFilter(e.target.value)}
                className="bg-rf-bg border border-rf-border rounded-lg pl-7 pr-2.5 py-1 text-xs text-rf-text font-mono focus:outline-none focus:border-cyan-400 w-44"
              />
            </div>
            {framesData?.frames && framesData.frames.length > 0 && (
              <button
                type="button"
                onClick={handleExportFramesJson}
                className="px-2.5 py-1 rounded bg-rf-panel hover:bg-rf-border text-zinc-300 text-xs font-mono flex items-center space-x-1 border border-rf-border transition-colors"
                title="导出已解调帧列表为 JSON 文件"
              >
                <Download className="w-3 h-3 text-cyan-400" />
                <span>导出帧</span>
              </button>
            )}
            <span className="text-[11px] font-mono text-zinc-400 bg-rf-panel px-2 py-0.5 rounded border border-rf-border/50">
              共 {filteredFramesList.length} / {framesData?.total ?? 0} 条有效记录
            </span>
          </div>
        </div>

        <div className="overflow-x-auto max-h-72 divide-y divide-rf-border/30 font-mono text-xs">
          <table className="w-full text-left">
            <thead className="bg-rf-panel/70 text-zinc-400 text-[11px] sticky top-0 border-b border-rf-border">
              <tr>
                <th className="p-3">时间戳 (ms) {canLinkFrames ? '/ 定位 IQ' : ''}</th>
                <th className="p-3">中心频率 (MHz)</th>
                <th className="p-3">频偏 CFO (kHz)</th>
                <th className="p-3">功率 (dB)</th>
                <th className="p-3">同步字 (Sync)</th>
                <th className="p-3">硬件包头</th>
                <th className="p-3">硬件 CRC</th>
                <th className="p-3">载荷十六进制 (Payload Hex)</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-rf-border/20">
              {filteredFramesList.map((f, idx) => {
                const originalIndex = framesData?.frames?.indexOf(f) ?? idx;
                return (
                <tr
                  key={idx}
                  onClick={() => selectFrame(originalIndex)}
                  className={`transition-colors cursor-pointer ${
                    originalIndex === selectedIndex
                      ? 'bg-cyan-500/15 ring-1 ring-inset ring-cyan-500/40'
                      : 'hover:bg-rf-panel/40'
                  }`}
                >
                  <td className="p-3 text-cyan-300 font-semibold whitespace-nowrap">
                    {canLinkFrames && frameLinkable(f) ? (
                      <button
                        type="button"
                        onClick={(e) => {
                          e.stopPropagation();
                          selectFrame(originalIndex, true);
                        }}
                        aria-label={`定位第 ${originalIndex + 1} 帧 IQ`}
                        aria-pressed={originalIndex === selectedIndex}
                        className="underline decoration-dotted underline-offset-4 hover:text-cyan-100 focus-visible:outline focus-visible:outline-cyan-400"
                      >
                        {(f.time_s * 1000).toFixed(3)}
                      </button>
                    ) : (
                      (f.time_s * 1000).toFixed(3)
                    )}
                  </td>
                  <td className="p-3 text-zinc-300 whitespace-nowrap">{(f.frequency_hz / 1e6).toFixed(3)}</td>
                  <td className="p-3 text-amber-300 whitespace-nowrap">
                    {Number.isFinite(f.cfo_hz) ? (f.cfo_hz / 1000).toFixed(2) : '-'}
                  </td>
                  <td className="p-3 text-zinc-400 whitespace-nowrap">
                    {Number.isFinite(f.sample_power_db) ? f.sample_power_db.toFixed(1) : '-'}
                  </td>
                  <td
                    className="p-3 text-emerald-400 font-semibold whitespace-nowrap"
                    title={f.sync_word_ambiguous ? JSON.stringify(f.sync_crc_candidates) : undefined}
                  >
                    {hexText(f.sync_word_hex)}
                    {f.sync_word_ambiguous ? '（多候选）' : ''}
                  </td>
                  <td className="p-3 text-zinc-400 whitespace-nowrap">{hexText(f.hardware_header_hex) || '固定长度'}</td>
                  <td className="p-3 text-emerald-400 whitespace-nowrap">
                    <span className="px-1.5 py-0.5 rounded bg-emerald-950/70 border border-emerald-800/80 text-[10px] font-semibold">
                      CRC{typeof f.crc_hex === 'string' ? f.crc_hex.length * 4 : ''} OK
                    </span>
                  </td>
                  <td className="p-3 text-rf-text font-bold tracking-wider select-all break-all min-w-[200px]">
                    {hexText(f.payload_hex)}
                  </td>
                </tr>
              );
              })}
              {!filteredFramesList.length && (
                <tr>
                  <td colSpan={8} className="p-10 text-center text-zinc-500 font-mono">
                    {!canReadFrames ? framesState?.message : '未检测到配套的 JSONL 帧记录或尚未执行解调'}
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* 选定帧的协议树逐层解码器 */}
      {selectedFrame && (
        <ProtocolDissectorPanel
          frame={selectedFrame}
          frameIndex={selectedIndex}
          totalFrames={framesData?.frames?.length || 0}
          allFrames={framesData?.frames}
          onClose={() => setSelection(null)}
          onSelectNext={
            selectedIndex < (framesData?.frames?.length || 0) - 1
              ? () => selectFrame(selectedIndex + 1)
              : undefined
          }
          onSelectPrev={selectedIndex > 0 ? () => selectFrame(selectedIndex - 1) : undefined}
        />
      )}
    </div>
  );
};
