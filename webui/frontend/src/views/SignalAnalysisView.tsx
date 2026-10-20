import React, { useState, useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import ReactECharts from 'echarts-for-react';
import {
  Activity,
  Radio,
  FileCode,
  Download,
  RefreshCw,
  Sliders,
  CheckCircle2,
  AlertTriangle,
  Layers,
  ArrowUpDown,
} from 'lucide-react';
import { api } from '../api/client';
import { useWorkstationStore } from '../stores/useWorkstationStore';

export const SignalAnalysisView: React.FC = () => {
  const { selectedArtifactPath, setSelectedArtifactPath } = useWorkstationStore();

  const [iqFormat, setIqFormat] = useState('cs8');
  const [sampleRate, setSampleRate] = useState(10000000);
  const [centerFreq, setCenterFreq] = useState(2480000000);
  const [nFft, setNFft] = useState(1024);

  // 获取产物列表
  const { data: artifacts, refetch: refetchArtifacts } = useQuery({
    queryKey: ['artifacts'],
    queryFn: api.getAllArtifacts,
  });

  // 筛选可用做信号分析的 IQ 文件与 JSONL 文件
  const iqArtifacts = useMemo(() => {
    const list = artifacts?.filter((a) =>
      /\.(iq|cs8|cu8|cf32)$/i.test(a.path)
    ) || [];
    return list;
  }, [artifacts]);

  const activePath = selectedArtifactPath || iqArtifacts[0]?.path || 'outputs/e2e_test.iq';

  // 查询频谱数据
  const { data: spectrumData, isFetching: fetchingSpectrum, refetch: refetchSpectrum } = useQuery({
    queryKey: ['spectrum', activePath, iqFormat, sampleRate, centerFreq, nFft],
    queryFn: () => api.getSpectrum(activePath, sampleRate, centerFreq, iqFormat, nFft),
    enabled: Boolean(activePath),
  });

  // 查询星座图数据
  const { data: constellationData, isFetching: fetchingConst, refetch: refetchConst } = useQuery({
    queryKey: ['constellation', activePath, iqFormat],
    queryFn: () => api.getConstellation(activePath, iqFormat, 2000),
    enabled: Boolean(activePath),
  });

  // 查找是否有对应的 jsonl 解调产物
  const correspondingJsonl = useMemo(() => {
    const base = activePath.replace(/\.(iq|cs8|cu8|cf32)$/i, '');
    return (
      artifacts?.find((a) => a.path.startsWith(base) && a.path.endsWith('.jsonl'))?.path ||
      'outputs/e2e_test.jsonl'
    );
  }, [activePath, artifacts]);

  // 查询解码帧列表
  const { data: framesData } = useQuery({
    queryKey: ['frames', correspondingJsonl],
    queryFn: () => api.getDecodedFrames(correspondingJsonl, 100),
    enabled: Boolean(correspondingJsonl),
  });

  // 频谱 ECharts 配置
  const spectrumOption = useMemo(() => {
    if (!spectrumData?.points) return {};
    const points = spectrumData.points;
    const xData = points.map((p) => (p[0] / 1e6).toFixed(3));
    const yData = points.map((p) => p[1]);

    return {
      backgroundColor: '#161b22',
      title: {
        text: '功率谱密度 (PSD / FFT Spectrum)',
        textStyle: { color: '#f0f6fc', fontSize: 12, fontFamily: 'monospace' },
        left: 10,
        top: 10,
      },
      tooltip: {
        trigger: 'axis',
        formatter: (params: any) => {
          const p = params[0];
          return `<div class="font-mono text-xs">频率: ${p.name} MHz<br/>功率: ${p.value} dB</div>`;
        },
      },
      grid: { left: '8%', right: '4%', top: '22%', bottom: '15%' },
      xAxis: {
        type: 'category',
        data: xData,
        axisLine: { lineStyle: { color: '#30363d' } },
        axisLabel: { color: '#8b949e', fontSize: 10, fontFamily: 'monospace' },
        name: 'MHz',
        nameTextStyle: { color: '#8b949e', fontSize: 10 },
      },
      yAxis: {
        type: 'value',
        axisLine: { lineStyle: { color: '#30363d' } },
        splitLine: { lineStyle: { color: '#21262d' } },
        axisLabel: { color: '#8b949e', fontSize: 10, fontFamily: 'monospace' },
        name: 'dB',
        nameTextStyle: { color: '#8b949e', fontSize: 10 },
      },
      series: [
        {
          name: 'PSD',
          type: 'line',
          data: yData,
          smooth: true,
          showSymbol: false,
          lineStyle: { color: '#00e5ff', width: 1.5 },
          areaStyle: {
            color: {
              type: 'linear',
              x: 0,
              y: 0,
              x2: 0,
              y2: 1,
              colorStops: [
                { offset: 0, color: 'rgba(0, 229, 255, 0.4)' },
                { offset: 1, color: 'rgba(0, 229, 255, 0.02)' },
              ],
            },
          },
        },
      ],
    };
  }, [spectrumData]);

  // 星座图 ECharts 配置
  const constellationOption = useMemo(() => {
    if (!constellationData?.points) return {};
    const points = constellationData.points;

    return {
      backgroundColor: '#161b22',
      title: {
        text: 'I/Q 星座图 (Constellation Diagram)',
        textStyle: { color: '#f0f6fc', fontSize: 12, fontFamily: 'monospace' },
        left: 10,
        top: 10,
      },
      tooltip: {
        formatter: (param: any) => {
          return `<div class="font-mono text-xs">I: ${param.value[0]}<br/>Q: ${param.value[1]}</div>`;
        },
      },
      grid: { left: '12%', right: '8%', top: '22%', bottom: '15%' },
      xAxis: {
        type: 'value',
        min: -1.2,
        max: 1.2,
        axisLine: { lineStyle: { color: '#30363d' } },
        splitLine: { lineStyle: { color: '#21262d' } },
        axisLabel: { color: '#8b949e', fontSize: 10, fontFamily: 'monospace' },
      },
      yAxis: {
        type: 'value',
        min: -1.2,
        max: 1.2,
        axisLine: { lineStyle: { color: '#30363d' } },
        splitLine: { lineStyle: { color: '#21262d' } },
        axisLabel: { color: '#8b949e', fontSize: 10, fontFamily: 'monospace' },
      },
      series: [
        {
          name: 'IQ 样本',
          type: 'scatter',
          data: points,
          symbolSize: 4,
          itemStyle: {
            color: '#00ff66',
            opacity: 0.7,
          },
        },
      ],
    };
  }, [constellationData]);

  return (
    <div className="p-6 space-y-6 max-w-7xl mx-auto select-none">
      {/* 顶部控制栏 */}
      <div className="bg-rf-surface border border-rf-border p-4 rounded-lg flex flex-wrap items-center justify-between gap-4">
        <div className="flex items-center space-x-3">
          <Activity className="w-5 h-5 text-cyan-400" />
          <div>
            <h2 className="text-sm font-bold text-rf-text">信号与载荷分析工作台</h2>
            <p className="text-[11px] text-zinc-500">
              离线与实时 IQ 频谱分析、星座相位与 CRC24 解码报文检视
            </p>
          </div>
        </div>

        {/* 文件选择器与参数设置 */}
        <div className="flex items-center space-x-3 text-xs font-mono">
          <div className="flex items-center space-x-1.5">
            <span className="text-zinc-500">文件:</span>
            <select
              value={activePath}
              onChange={(e) => setSelectedArtifactPath(e.target.value)}
              className="bg-rf-bg border border-rf-border rounded px-2.5 py-1 text-rf-text focus:outline-none focus:border-rf-accent max-w-xs truncate"
            >
              {iqArtifacts.map((art) => (
                <option key={art.id} value={art.path}>
                  {art.path.split(/[\\/]/).pop()} ({art.kind})
                </option>
              ))}
              <option value="outputs/e2e_test.iq">outputs/e2e_test.iq</option>
              <option value="outputs/test_ui.iq">outputs/test_ui.iq</option>
            </select>
          </div>

          <div className="flex items-center space-x-1.5">
            <span className="text-zinc-500">格式:</span>
            <select
              value={iqFormat}
              onChange={(e) => setIqFormat(e.target.value)}
              className="bg-rf-bg border border-rf-border rounded px-2 py-1 text-rf-text focus:outline-none focus:border-rf-accent"
            >
              <option value="cs8">cs8</option>
              <option value="cu8">cu8</option>
              <option value="cf32">cf32</option>
            </select>
          </div>

          <div className="flex items-center space-x-1.5">
            <span className="text-zinc-500">采样率:</span>
            <input
              type="number"
              value={sampleRate}
              onChange={(e) => setSampleRate(Number(e.target.value))}
              className="w-24 bg-rf-bg border border-rf-border rounded px-2 py-1 text-rf-text focus:outline-none focus:border-rf-accent"
            />
          </div>

          <button
            onClick={() => {
              refetchSpectrum();
              refetchConst();
            }}
            className="p-1.5 rounded bg-rf-panel hover:bg-rf-border text-zinc-300 transition-colors"
            title="刷新图表"
          >
            <RefreshCw
              className={`w-3.5 h-3.5 ${
                fetchingSpectrum || fetchingConst ? 'animate-spin text-rf-accent' : ''
              }`}
            />
          </button>
        </div>
      </div>

      {/* 核心指标统计卡片 */}
      <div className="grid grid-cols-1 md:grid-cols-4 gap-4 font-mono">
        <div className="bg-rf-surface border border-rf-border p-4 rounded-lg">
          <div className="text-[11px] text-zinc-500">峰值载波频率 (Peak Freq)</div>
          <div className="text-lg font-bold text-cyan-400 mt-1">
            {spectrumData?.peak_frequency_hz
              ? `${(spectrumData.peak_frequency_hz / 1e6).toFixed(3)} MHz`
              : '未知'}
          </div>
          <div className="text-[10px] text-zinc-600 mt-0.5">实测 PSD 谱峰位置</div>
        </div>

        <div className="bg-rf-surface border border-rf-border p-4 rounded-lg">
          <div className="text-[11px] text-zinc-500">峰值功率强度 (Peak Power)</div>
          <div className="text-lg font-bold text-rf-text mt-1">
            {spectrumData?.peak_power_db
              ? `${spectrumData.peak_power_db.toFixed(1)} dB`
              : '未知'}
          </div>
          <div className="text-[10px] text-zinc-600 mt-0.5">归一化 FFT 基带功率</div>
        </div>

        <div className="bg-rf-surface border border-rf-border p-4 rounded-lg">
          <div className="text-[11px] text-zinc-500">估算 EVM 误差幅度</div>
          <div className="text-lg font-bold text-emerald-400 mt-1">
            {constellationData?.evm_estimate_percent !== undefined
              ? `${constellationData.evm_estimate_percent}%`
              : '未知'}
          </div>
          <div className="text-[10px] text-zinc-600 mt-0.5">包络方差估计值</div>
        </div>

        <div className="bg-rf-surface border border-rf-border p-4 rounded-lg">
          <div className="text-[11px] text-zinc-500">CRC24 校验通过帧</div>
          <div className="text-lg font-bold text-rf-accent mt-1">
            {framesData?.total ?? 0}{' '}
            <span className="text-xs font-normal text-zinc-500">帧</span>
          </div>
          <div className="text-[10px] text-zinc-600 mt-0.5">有效物理帧总数</div>
        </div>
      </div>

      {/* 频谱与星座图图表网格 */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <div className="bg-rf-surface border border-rf-border rounded-lg p-2 h-80">
          <ReactECharts
            option={spectrumOption}
            style={{ height: '100%', width: '100%' }}
            opts={{ renderer: 'canvas' }}
          />
        </div>

        <div className="bg-rf-surface border border-rf-border rounded-lg p-2 h-80">
          <ReactECharts
            option={constellationOption}
            style={{ height: '100%', width: '100%' }}
            opts={{ renderer: 'canvas' }}
          />
        </div>
      </div>

      {/* 底部：解调帧与报文详情查看器 */}
      <div className="bg-rf-surface border border-rf-border rounded-lg overflow-hidden">
        <div className="p-3.5 border-b border-rf-border bg-rf-panel/40 flex items-center justify-between">
          <h3 className="text-xs font-bold font-mono uppercase text-rf-text flex items-center space-x-2">
            <FileCode className="w-4 h-4 text-cyan-400" />
            <span>解调帧结构与载荷 Hex 检查器 ({correspondingJsonl})</span>
          </h3>
          <span className="text-[11px] font-mono text-zinc-500">
            共 {framesData?.total ?? 0} 条有效记录
          </span>
        </div>

        <div className="overflow-x-auto max-h-72 divide-y divide-rf-border/40 font-mono text-xs">
          <table className="w-full text-left">
            <thead className="bg-rf-panel/60 text-zinc-400 text-[11px]">
              <tr>
                <th className="p-2.5">时间戳 (ms)</th>
                <th className="p-2.5">中心频率 (MHz)</th>
                <th className="p-2.5">频偏 CFO (kHz)</th>
                <th className="p-2.5">功率 (dB)</th>
                <th className="p-2.5">同步字 (Sync)</th>
                <th className="p-2.5">硬件包头</th>
                <th className="p-2.5">CRC24</th>
                <th className="p-2.5">载荷十六进制 (Payload Hex)</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-rf-border/30">
              {framesData?.frames?.map((f, idx) => (
                <tr key={idx} className="hover:bg-rf-panel/40 transition-colors">
                  <td className="p-2.5 text-cyan-300">{(f.time_s * 1000).toFixed(3)}</td>
                  <td className="p-2.5 text-zinc-300">{(f.frequency_hz / 1e6).toFixed(3)}</td>
                  <td className="p-2.5 text-amber-300">
                    {f.cfo_hz ? (f.cfo_hz / 1000).toFixed(2) : '-'}
                  </td>
                  <td className="p-2.5 text-zinc-400">
                    {f.sample_power_db ? f.sample_power_db.toFixed(1) : '-'}
                  </td>
                  <td className="p-2.5 text-emerald-400 font-semibold">{f.sync_word_hex}</td>
                  <td className="p-2.5 text-zinc-400">{f.hardware_header_hex}</td>
                  <td className="p-2.5 text-emerald-400">
                    <span className="px-1.5 py-0.5 rounded bg-emerald-950/60 border border-emerald-800 text-[10px]">
                      OK
                    </span>
                  </td>
                  <td className="p-2.5 text-rf-text font-bold tracking-wider select-all break-all">
                    {f.payload_hex}
                  </td>
                </tr>
              ))}
              {!framesData?.frames?.length && (
                <tr>
                  <td colSpan={8} className="p-8 text-center text-zinc-600">
                    未检测到配套的 JSONL 帧记录或尚未执行解调
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
