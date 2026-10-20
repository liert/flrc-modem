import React, { useState, useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import ReactECharts from 'echarts-for-react/lib/core';
import * as echarts from 'echarts/core';
import { LineChart, ScatterChart } from 'echarts/charts';
import { GridComponent, TitleComponent, TooltipComponent } from 'echarts/components';
import { CanvasRenderer } from 'echarts/renderers';
import {
  Activity,
  FileCode,
  RefreshCw,
  Radio,
  Zap,
  ShieldCheck,
  TrendingUp,
} from 'lucide-react';
import { api } from '../api/client';
import { useWorkstationStore } from '../stores/useWorkstationStore';
import { analysisFiles, fileReadable } from '../utils/analysis';
import { runOption } from '../utils/runs';

echarts.use([LineChart, ScatterChart, GridComponent, TitleComponent, TooltipComponent, CanvasRenderer]);

export const SignalAnalysisView: React.FC = () => {
  const { setSelectedArtifactPath, selectedRunId, setSelectedRunId } = useWorkstationStore();
  const [nFft, setNFft] = useState(1024);

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

  // 查询频谱数据
  const { data: rawSpectrumData, error: spectrumError, isFetching: fetchingSpectrum, refetch: refetchSpectrum } = useQuery({
    queryKey: ['spectrum', activePath, analysisRunId, iqState?.revision, iqFormat, sampleRate, centerFreq, nFft],
    queryFn: () => api.getSpectrum(activePath, sampleRate, centerFreq, iqFormat, nFft, 0, analysisRunId || undefined),
    enabled: isIqPath && validSampling && canReadIq,
  });

  // 查询星座图数据
  const { data: rawConstellationData, error: constellationError, isFetching: fetchingConst, refetch: refetchConst } = useQuery({
    queryKey: ['constellation', activePath, analysisRunId, iqState?.revision, iqFormat],
    queryFn: () => api.getConstellation(activePath, iqFormat, 2000, 0, analysisRunId || undefined),
    enabled: isIqPath && canReadIq,
  });

  // 查询解码帧列表
  const { data: rawFramesData, error: framesError, refetch: refetchFrames } = useQuery({
    queryKey: ['frames', analysisRunId, correspondingJsonl, context?.status, framesState?.revision],
    queryFn: () => api.getDecodedFrames(correspondingJsonl, 500, analysisRunId || undefined),
    enabled: Boolean(correspondingJsonl) && canReadFrames,
    refetchInterval: running ? 2000 : false,
  });
  const spectrumData = canReadIq ? rawSpectrumData : undefined;
  const constellationData = canReadIq ? rawConstellationData : undefined;
  const framesData = canReadFrames ? rawFramesData : undefined;

  // 频谱 ECharts 配置（标题统一在卡片 HTML 头部展示，避免在 Canvas 内部与 Y 轴刻度堆叠重合）
  const spectrumOption = useMemo(() => {
    if (!spectrumData?.points) return {};
    const points = spectrumData.points;
    const xData = points.map((p) => (p[0] / 1e6).toFixed(3));
    const yData = points.map((p) => p[1]);

    return {
      backgroundColor: 'transparent',
      tooltip: {
        trigger: 'axis',
        backgroundColor: '#161b22',
        borderColor: '#30363d',
        textStyle: { color: '#f0f6fc' },
        formatter: (params: any) => {
          const p = params[0];
          return `<div class="font-mono text-xs"><span class="text-cyan-400">频率:</span> ${p.name} MHz<br/><span class="text-emerald-400">功率密度:</span> ${p.value} dB/Hz</div>`;
        },
      },
      grid: {
        left: 55,
        right: 35,
        top: 20,
        bottom: 40,
      },
      xAxis: {
        type: 'category',
        data: xData,
        boundaryGap: false,
        axisLine: { lineStyle: { color: '#30363d' } },
        axisTick: { lineStyle: { color: '#30363d' } },
        axisLabel: {
          color: '#8b949e',
          fontSize: 10,
          fontFamily: 'monospace',
          hideOverlap: true,
          interval: 'auto',
        },
        name: 'MHz',
        nameTextStyle: { color: '#8b949e', fontSize: 10, fontFamily: 'monospace' },
      },
      yAxis: {
        type: 'value',
        axisLine: { lineStyle: { color: '#30363d' } },
        splitLine: { lineStyle: { color: '#1f242c', type: 'dashed' } },
        axisLabel: { color: '#8b949e', fontSize: 10, fontFamily: 'monospace' },
        name: 'dB/Hz',
        nameTextStyle: { color: '#8b949e', fontSize: 10, fontFamily: 'monospace' },
      },
      series: [
        {
          name: 'PSD',
          type: 'line',
          data: yData,
          smooth: true,
          showSymbol: false,
          lineStyle: { color: '#00e5ff', width: 2 },
          areaStyle: {
            color: {
              type: 'linear',
              x: 0,
              y: 0,
              x2: 0,
              y2: 1,
              colorStops: [
                { offset: 0, color: 'rgba(0, 229, 255, 0.45)' },
                { offset: 0.6, color: 'rgba(0, 229, 255, 0.12)' },
                { offset: 1, color: 'rgba(0, 229, 255, 0.0)' },
              ],
            },
          },
        },
      ],
    };
  }, [spectrumData]);

  // 星座图 ECharts 配置（标题统一在卡片 HTML 头部展示，中心十字坐标轴对齐）
  const constellationOption = useMemo(() => {
    if (!constellationData?.points) return {};
    const points = constellationData.points;

    return {
      backgroundColor: 'transparent',
      tooltip: {
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
        top: 20,
        bottom: 35,
      },
      xAxis: {
        type: 'value',
        min: -1.2,
        max: 1.2,
        axisLine: { onZero: true, lineStyle: { color: '#444c56' } },
        splitLine: { lineStyle: { color: '#1f242c', type: 'dashed' } },
        axisLabel: { color: '#8b949e', fontSize: 10, fontFamily: 'monospace' },
        name: 'I',
        nameTextStyle: { color: '#8b949e', fontSize: 10, fontFamily: 'monospace' },
      },
      yAxis: {
        type: 'value',
        min: -1.2,
        max: 1.2,
        axisLine: { onZero: true, lineStyle: { color: '#444c56' } },
        splitLine: { lineStyle: { color: '#1f242c', type: 'dashed' } },
        axisLabel: { color: '#8b949e', fontSize: 10, fontFamily: 'monospace' },
        name: 'Q',
        nameTextStyle: { color: '#8b949e', fontSize: 10, fontFamily: 'monospace' },
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
            shadowBlur: 3,
            shadowColor: 'rgba(0, 255, 102, 0.4)',
          },
        },
      ],
    };
  }, [constellationData]);

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
              if (isIqPath && validSampling && canReadIq) refetchSpectrum();
              if (isIqPath && canReadIq) refetchConst();
              if (correspondingJsonl && canReadFrames) refetchFrames();
            }}
            className="p-2 rounded-lg bg-rf-panel hover:bg-rf-border text-zinc-300 border border-rf-border/60 transition-all shadow-sm"
            title="刷新图表"
          >
            <RefreshCw
              className={`w-3.5 h-3.5 ${
                fetchingSpectrum || fetchingConst ? 'animate-spin text-cyan-400' : ''
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

      {(spectrumError || constellationError || framesError || contextError || context?.metadata_error || (contextReady && isIqPath && (!validSampling || !iqFormat))) && (
        <p role="alert" className="text-xs text-red-300 font-mono p-3 rounded-lg bg-red-950/50 border border-red-800/80">
          {context?.metadata_error || (spectrumError || constellationError || framesError || contextError)?.message || '任务记录缺少有效的采样率、中心频率或 IQ 格式，无法生成对应的 IQ 图表。'}
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

      {/* 频谱与星座图图表网格（独立卡片头部 + 规范化高度容器） */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">
        {/* 功率谱密度卡片 */}
        <div className="bg-rf-surface border border-rf-border rounded-xl overflow-hidden shadow-md flex flex-col">
          <div className="p-3.5 border-b border-rf-border bg-rf-panel/40 flex items-center justify-between">
            <div className="flex items-center space-x-2 font-mono">
              <Activity className="w-4 h-4 text-cyan-400" />
              <h3 className="text-xs font-bold uppercase text-rf-text">
                功率谱密度 (PSD / FFT Spectrum)
              </h3>
            </div>
            <span className="text-[11px] font-mono text-zinc-400 bg-rf-panel px-2 py-0.5 rounded border border-rf-border/50">
              FFT: {nFft} 点
            </span>
          </div>
          <div className="p-2 h-80 sm:h-96 w-full flex-1">
            <ReactECharts
              echarts={echarts}
              notMerge
              option={spectrumOption}
              style={{ height: '100%', width: '100%' }}
              opts={{ renderer: 'canvas' }}
            />
          </div>
        </div>

        {/* 星座图卡片 */}
        <div className="bg-rf-surface border border-rf-border rounded-xl overflow-hidden shadow-md flex flex-col">
          <div className="p-3.5 border-b border-rf-border bg-rf-panel/40 flex items-center justify-between">
            <div className="flex items-center space-x-2 font-mono">
              <Radio className="w-4 h-4 text-emerald-400" />
              <h3 className="text-xs font-bold uppercase text-rf-text">
                I/Q 星座相位图 (Constellation Diagram)
              </h3>
            </div>
            <span className="text-[11px] font-mono text-zinc-400 bg-rf-panel px-2 py-0.5 rounded border border-rf-border/50">
              样本数: {constellationData?.points?.length || 0}
            </span>
          </div>
          <div className="p-2 h-80 sm:h-96 w-full flex-1">
            <ReactECharts
              echarts={echarts}
              notMerge
              option={constellationOption}
              style={{ height: '100%', width: '100%' }}
              opts={{ renderer: 'canvas' }}
            />
          </div>
        </div>
      </div>

      {/* 底部：解调帧与报文详情查看器 */}
      <div className="bg-rf-surface border border-rf-border rounded-xl overflow-hidden shadow-md">
        <div className="p-3.5 border-b border-rf-border bg-rf-panel/50 flex items-center justify-between">
          <h3 className="text-xs font-bold font-mono uppercase text-rf-text flex items-center space-x-2">
            <FileCode className="w-4 h-4 text-cyan-400" />
            <span>解调帧结构与载荷 Hex 检查器 ({correspondingJsonl})</span>
          </h3>
          <span className="text-[11px] font-mono text-zinc-400 bg-rf-panel px-2 py-0.5 rounded border border-rf-border/50">
            共 {framesData?.total ?? 0} 条有效记录
          </span>
        </div>

        <div className="overflow-x-auto max-h-72 divide-y divide-rf-border/30 font-mono text-xs">
          <table className="w-full text-left">
            <thead className="bg-rf-panel/70 text-zinc-400 text-[11px] sticky top-0 border-b border-rf-border">
              <tr>
                <th className="p-3">时间戳 (ms)</th>
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
              {framesData?.frames?.map((f, idx) => (
                <tr key={idx} className="hover:bg-rf-panel/40 transition-colors">
                  <td className="p-3 text-cyan-300 font-semibold">{(f.time_s * 1000).toFixed(3)}</td>
                  <td className="p-3 text-zinc-300">{(f.frequency_hz / 1e6).toFixed(3)}</td>
                  <td className="p-3 text-amber-300">
                    {Number.isFinite(f.cfo_hz) ? (f.cfo_hz / 1000).toFixed(2) : '-'}
                  </td>
                  <td className="p-3 text-zinc-400">
                    {Number.isFinite(f.sample_power_db) ? f.sample_power_db.toFixed(1) : '-'}
                  </td>
                  <td className="p-3 text-emerald-400 font-semibold" title={f.sync_word_ambiguous ? JSON.stringify(f.sync_crc_candidates) : undefined}>
                    {f.sync_word_hex}{f.sync_word_ambiguous ? '（多候选）' : ''}
                  </td>
                  <td className="p-3 text-zinc-400">{f.hardware_header_hex || '固定长度'}</td>
                  <td className="p-3 text-emerald-400">
                    <span className="px-1.5 py-0.5 rounded bg-emerald-950/70 border border-emerald-800/80 text-[10px] font-semibold">
                      CRC{typeof f.crc_hex === 'string' ? f.crc_hex.length * 4 : ''} OK
                    </span>
                  </td>
                  <td className="p-3 text-rf-text font-bold tracking-wider select-all break-all">
                    {f.payload_hex}
                  </td>
                </tr>
              ))}
              {!framesData?.frames?.length && (
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
    </div>
  );
};
