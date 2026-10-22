import React, { useState, useMemo, useRef, useEffect } from 'react';
import { useMutation, useQuery } from '@tanstack/react-query';
import {
  Radar,
  Radio,
  Play,
  Square,
  FileCode,
  Sliders,
  Copy,
  Check,
  Download,
  AlertCircle,
  Loader2,
  RefreshCw,
  Search,
  ExternalLink,
  ChevronDown,
  ChevronUp,
  Sparkles,
  Zap,
  Layers,
} from 'lucide-react';
import { api } from '../api/client';
import type { ChannelCandidate, ScanReport, QuickScanRequest } from '../types';
import { useWorkstationStore } from '../stores/useWorkstationStore';
import { useScanStore } from '../stores/useScanStore';

export interface ScanWorkbenchPanelProps {
  initialReport?: ScanReport | null;
  onSelectChannelForDemod?: (channel: ChannelCandidate) => void;
  onSelectChannelForSpectrum?: (channel: ChannelCandidate) => void;
}

export const ScanWorkbenchPanel: React.FC<ScanWorkbenchPanelProps> = ({
  initialReport = null,
  onSelectChannelForDemod,
  onSelectChannelForSpectrum,
}) => {
  const { setActiveTab, setSelectedCommandId } = useWorkstationStore();
  const activeScanId = useRef<string | null>(null);
  const lastProgress = useRef<{ scanId: string; revision: number } | null>(null);
  const [scanNotice, setScanNotice] = useState<string | null>(null);

  useEffect(() => () => {
    if (activeScanId.current) {
      void api.stopQuickScan(activeScanId.current).catch(() => {});
    }
  }, []);

  // 表单直接使用共享草稿，离开页面前的编辑无需启动扫描也会保留。
  const {
    scanMode, iqPath, sampleRate, centerFreqMhz, iqFormat, duration,
    startFreqMhz, stopFreqMhz, stepBwMhz, dwell, gain,
    thresholdDb, minSnr, selectedBitrate, noFlrc, showAdvanced,
  } = useScanStore(state => state.draft);
  const updateDraft = useScanStore(state => state.updateDraft);

  // 结果状态
  const report = useScanStore(state => state.report) ?? initialReport;
  const selectedChannelFrequencyHz = useScanStore(state => state.selectedChannelFrequencyHz);
  const setReport = useScanStore(state => state.setReport);
  const updateReport = useScanStore(state => state.updateReport);
  const beginScan = useScanStore(state => state.beginScan);
  const selectChannel = useScanStore(state => state.selectChannel);
  const selectedChannel = report?.channels.find(channel => channel.frequency_hz === selectedChannelFrequencyHz) ?? null;

  useEffect(() => {
    if (initialReport) setReport(initialReport);
  }, [initialReport, setReport]);

  const [copiedSync, setCopiedSync] = useState<string | null>(null);
  const [copiedCli, setCopiedCli] = useState(false);
  const [exportNotice, setExportNotice] = useState<string | null>(null);

  // 动态生成等价 CLI 命令
  const cliPreview = useMemo(() => {
    const parts = ['flrc', 'scan'];
    if (scanMode === 'offline') {
      parts.push('--iq', `"${iqPath}"`);
      parts.push('--sample-rate', String(sampleRate));
      parts.push('--center-frequency', String(Math.round(centerFreqMhz * 1e6)));
      if (iqFormat !== 'cs8') parts.push('--iq-format', iqFormat);
      if (duration !== 0.5) parts.push('--duration', String(duration));
    } else {
      parts.push('--sdr', scanMode);
      if (startFreqMhz !== 2400.0) parts.push('--start-freq', String(Math.round(startFreqMhz * 1e6)));
      if (stopFreqMhz !== 2483.5) parts.push('--stop-freq', String(Math.round(stopFreqMhz * 1e6)));
      if (stepBwMhz !== 10.0) parts.push('--step-bw', String(Math.round(stepBwMhz * 1e6)));
      if (dwell !== 0.05) parts.push('--dwell', String(dwell));
      if (gain !== 30.0) parts.push('--gain', String(gain));
    }
    if (thresholdDb !== 10.0) parts.push('--threshold-db', String(thresholdDb));
    if (minSnr !== 6.0) parts.push('--min-snr', String(minSnr));
    if (selectedBitrate) parts.push('--bit-rate', String(selectedBitrate));
    if (noFlrc) parts.push('--no-flrc');
    parts.push('--output-config', 'configs/scanned_channels.toml');
    return parts.join(' ');
  }, [
    scanMode, iqPath, sampleRate, centerFreqMhz, iqFormat, duration,
    startFreqMhz, stopFreqMhz, stepBwMhz, dwell, gain,
    thresholdDb, minSnr, selectedBitrate, noFlrc,
  ]);

  // 快速盲扫请求
  const scanMutation = useMutation({
    mutationFn: async (scanId: string) => {
      const req: QuickScanRequest = {
        scan_id: scanId,
        threshold_db: thresholdDb,
        min_snr: minSnr,
        bit_rate: selectedBitrate,
        no_flrc: noFlrc,
      };
      if (scanMode === 'offline') {
        req.iq = iqPath;
        req.sample_rate = sampleRate;
        req.center_frequency = Math.round(centerFreqMhz * 1e6);
        req.iq_format = iqFormat;
        req.duration = duration;
      } else {
        req.sdr = scanMode;
        req.start_freq = Math.round(startFreqMhz * 1e6);
        req.stop_freq = Math.round(stopFreqMhz * 1e6);
        req.step_bw = Math.round(stepBwMhz * 1e6);
        req.dwell = dwell;
        req.gain = gain;
      }
      return await api.quickScan(req);
    },
    onSuccess: (res, scanId) => {
      activeScanId.current = null;
      if ('status' in res) {
        if (res.report) updateReport(res.report, scanId);
        setScanNotice(res.report ? '扫描已停止，已保留当前结果' : '扫描已停止');
        return;
      }
      updateReport(res, scanId);
    },
    onSettled: () => {
      activeScanId.current = null;
    },
  });

  const progressScanId = scanMutation.isPending ? activeScanId.current : null;
  const progressQuery = useQuery({
    queryKey: ['scanProgress', progressScanId],
    queryFn: async () => {
      const previous = lastProgress.current;
      return api.getScanProgress(progressScanId!, previous?.scanId === progressScanId ? previous.revision : undefined);
    },
    enabled: Boolean(progressScanId),
    refetchInterval: 500,
    refetchIntervalInBackground: true,
    retry: false,
  });
  const progress = progressQuery.data?.active && progressQuery.data.scan_id === progressScanId
    ? progressQuery.data : null;

  useEffect(() => {
    // A delayed poll must not replace the final result or a newer scan.
    if (progress?.report && activeScanId.current === progress.scan_id) {
      updateReport(progress.report, progress.scan_id);
      lastProgress.current = { scanId: progress.scan_id, revision: progress.revision };
    }
  }, [progress, updateReport]);

  const stopMutation = useMutation({
    mutationFn: async (scanId: string) => {
      const result = await api.stopQuickScan(scanId);
      if (!result.stopped && activeScanId.current === scanId) {
        throw new Error('扫描尚未就绪或已结束，请重试停止');
      }
    },
  });

  const handleStartScan = () => {
    if (activeScanId.current || stopMutation.isPending) return;
    const scanId = crypto.randomUUID();
    activeScanId.current = scanId;
    beginScan(scanId);
    setScanNotice(null);
    stopMutation.reset();
    scanMutation.mutate(scanId);
  };

  // 导出 TOML 配置请求
  const exportMutation = useMutation({
    mutationFn: async () => {
      if (!report || report.channels.length === 0) return;
      const freqs = report.channels.map(c => c.frequency_hz);
      const syncs: string[] = [];
      report.channels.forEach(c => {
        c.sync_words.forEach(s => {
          if (!syncs.includes(s)) syncs.push(s);
        });
      });
      return await api.exportScanPlan({
        frequencies: freqs,
        sample_rate: report.sample_rate_sps || 10_000_000,
        center_frequency: report.center_frequency_mhz ? report.center_frequency_mhz * 1e6 : undefined,
        sync_words: syncs,
        output_path: 'configs/scanned_channels.toml',
      });
    },
    onSuccess: (res) => {
      if (res) {
        setExportNotice(`配置已保存至: ${res.path}`);
        setTimeout(() => setExportNotice(null), 4000);
      }
    },
  });

  // 转入离线解调处理
  const handleTransferToDemod = (ch: ChannelCandidate) => {
    if (onSelectChannelForDemod) {
      onSelectChannelForDemod(ch);
      return;
    }
    // 自动切换至命令控制库并预填解调
    setSelectedCommandId('demod');
    setActiveTab('commands');
  };

  // 转入实时监视处理
  const handleTransferToSpectrum = (ch: ChannelCandidate) => {
    if (onSelectChannelForSpectrum) {
      onSelectChannelForSpectrum(ch);
      return;
    }
    setActiveTab('spectrum');
  };

  // 复制同步字
  const handleCopySync = (sync: string) => {
    navigator.clipboard.writeText(sync);
    setCopiedSync(sync);
    setTimeout(() => setCopiedSync(null), 2000);
  };

  // 复制 CLI 命令
  const handleCopyCli = () => {
    navigator.clipboard.writeText(cliPreview);
    setCopiedCli(true);
    setTimeout(() => setCopiedCli(false), 2000);
  };

  // 频谱全景坐标计算 (2400.0 - 2483.5 MHz 归一化)
  const bandStart = report?.start_freq_mhz || 2400.0;
  const bandStop = report?.stop_freq_mhz || 2483.5;
  const bandSpan = Math.max(1.0, bandStop - bandStart);

  return (
    <div className="flex flex-col h-full space-y-4 p-4 text-zinc-100 overflow-y-auto">
      {/* 顶部标题与模式切换 */}
      <div className="flex flex-wrap items-center justify-between gap-3 bg-rf-panel/60 p-4 rounded-xl border border-rf-border backdrop-blur-md">
        <div className="flex items-center gap-3">
          <div className="p-2.5 bg-cyan-500/10 border border-cyan-500/30 rounded-lg text-cyan-400">
            <Radar className="w-6 h-6 animate-pulse" />
          </div>
          <div>
            <div className="flex items-center gap-2">
              <h2 className="text-lg font-bold text-zinc-100 tracking-wide">
                2.4GHz 全频段自动盲扫与信道感知
              </h2>
              <span className="px-2 py-0.5 text-xs font-medium rounded-full bg-cyan-500/20 text-cyan-300 border border-cyan-500/30">
                flrc scan
              </span>
            </div>
            <p className="text-xs text-zinc-400 mt-0.5">
              高灵敏度自适应底噪探测、CFAR 谱峰聚类、FLRC 前导相位检验与同步字恢复
            </p>
          </div>
        </div>

        {/* 模式选择按钮组 */}
        <div className="flex items-center gap-1 bg-rf-bg/80 p-1 rounded-lg border border-rf-border">
          <button
            type="button"
            onClick={() => updateDraft({ scanMode: 'offline' })}
            className={`px-3 py-1.5 text-xs font-medium rounded-md transition-all ${
              scanMode === 'offline'
                ? 'bg-cyan-600 text-white shadow-sm shadow-cyan-900/40'
                : 'text-zinc-400 hover:text-zinc-200'
            }`}
          >
            离线 IQ 文件
          </button>
          <button
            type="button"
            onClick={() => updateDraft({ scanMode: 'hackrf' })}
            className={`px-3 py-1.5 text-xs font-medium rounded-md transition-all ${
              scanMode === 'hackrf'
                ? 'bg-cyan-600 text-white shadow-sm shadow-cyan-900/40'
                : 'text-zinc-400 hover:text-zinc-200'
            }`}
          >
            HackRF 扫频
          </button>
          <button
            type="button"
            onClick={() => updateDraft({ scanMode: 'usrp' })}
            className={`px-3 py-1.5 text-xs font-medium rounded-md transition-all ${
              scanMode === 'usrp'
                ? 'bg-cyan-600 text-white shadow-sm shadow-cyan-900/40'
                : 'text-zinc-400 hover:text-zinc-200'
            }`}
          >
            USRP 扫频
          </button>
        </div>
      </div>

      {/* 参数配置区域 */}
      <div className="bg-rf-panel/40 p-4 rounded-xl border border-rf-border space-y-3">
        {scanMode === 'offline' ? (
          <div className="grid grid-cols-1 md:grid-cols-4 gap-3">
            <div className="md:col-span-2 space-y-1">
              <label className="text-xs font-medium text-zinc-300">IQ 采样文件路径</label>
              <div className="flex items-center gap-2">
                <input
                  type="text"
                  value={iqPath}
                  onChange={(e) => updateDraft({ iqPath: e.target.value })}
                  className="flex-1 px-3 py-1.5 text-xs rounded-md bg-rf-bg border border-rf-border text-zinc-100 focus:outline-none focus:border-cyan-500"
                  placeholder="如 outputs/rx.cs8"
                />
                <button
                  type="button"
                  onClick={() => updateDraft({ iqPath: 'outputs/rx.cs8' })}
                  className="px-2 py-1.5 text-[11px] bg-rf-panel hover:bg-rf-surface border border-rf-border rounded text-zinc-300"
                >
                  rx.cs8
                </button>
              </div>
            </div>

            <div className="space-y-1">
              <label className="text-xs font-medium text-zinc-300">中心载波频率 (MHz)</label>
              <input
                type="number"
                step="0.01"
                value={centerFreqMhz}
                onChange={(e) => updateDraft({ centerFreqMhz: e.target.value === '' ? 2405.0 : e.target.valueAsNumber })}
                className="w-full px-3 py-1.5 text-xs rounded-md bg-rf-bg border border-rf-border text-zinc-100 focus:outline-none focus:border-cyan-500"
              />
            </div>

            <div className="space-y-1">
              <label className="text-xs font-medium text-zinc-300">采样率 (Sps)</label>
              <select
                value={sampleRate}
                onChange={(e) => updateDraft({ sampleRate: parseInt(e.target.value, 10) })}
                className="w-full px-3 py-1.5 text-xs rounded-md bg-rf-bg border border-rf-border text-zinc-100 focus:outline-none focus:border-cyan-500"
              >
                <option value={2000000}>2.0 MSps</option>
                <option value={4000000}>4.0 MSps</option>
                <option value={8000000}>8.0 MSps</option>
                <option value={10000000}>10.0 MSps</option>
                <option value={20000000}>20.0 MSps</option>
              </select>
            </div>
          </div>
        ) : (
          <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
            <div className="space-y-1">
              <label className="text-xs font-medium text-zinc-300">起始频率 (MHz)</label>
              <input
                type="number"
                step="0.5"
                value={startFreqMhz}
                onChange={(e) => updateDraft({ startFreqMhz: e.target.value === '' ? 2400.0 : e.target.valueAsNumber })}
                className="w-full px-3 py-1.5 text-xs rounded-md bg-rf-bg border border-rf-border text-zinc-100 focus:outline-none focus:border-cyan-500"
              />
            </div>
            <div className="space-y-1">
              <label className="text-xs font-medium text-zinc-300">截止频率 (MHz)</label>
              <input
                type="number"
                step="0.5"
                value={stopFreqMhz}
                onChange={(e) => updateDraft({ stopFreqMhz: e.target.value === '' ? 2483.5 : e.target.valueAsNumber })}
                className="w-full px-3 py-1.5 text-xs rounded-md bg-rf-bg border border-rf-border text-zinc-100 focus:outline-none focus:border-cyan-500"
              />
            </div>
            <div className="space-y-1">
              <label className="text-xs font-medium text-zinc-300">步进带宽 (MHz)</label>
              <input
                type="number"
                step="1"
                value={stepBwMhz}
                onChange={(e) => updateDraft({ stepBwMhz: e.target.value === '' ? 10.0 : e.target.valueAsNumber })}
                className="w-full px-3 py-1.5 text-xs rounded-md bg-rf-bg border border-rf-border text-zinc-100 focus:outline-none focus:border-cyan-500"
              />
            </div>
            <div className="space-y-1">
              <label className="text-xs font-medium text-zinc-300">频点驻留时长 (s)</label>
              <input
                type="number"
                step="0.01"
                value={dwell}
                onChange={(e) => updateDraft({ dwell: e.target.value === '' ? 0.05 : e.target.valueAsNumber })}
                className="w-full px-3 py-1.5 text-xs rounded-md bg-rf-bg border border-rf-border text-zinc-100 focus:outline-none focus:border-cyan-500"
              />
            </div>
            <div className="space-y-1">
              <label className="text-xs font-medium text-zinc-300">SDR 增益 (dB)</label>
              <input
                type="number"
                value={gain}
                onChange={(e) => updateDraft({ gain: e.target.value === '' ? 30.0 : e.target.valueAsNumber })}
                className="w-full px-3 py-1.5 text-xs rounded-md bg-rf-bg border border-rf-border text-zinc-100 focus:outline-none focus:border-cyan-500"
              />
            </div>
          </div>
        )}

        {/* 高级判定算法参数折叠 */}
        <div className="pt-1 border-t border-rf-border/40">
          <button
            type="button"
            onClick={() => updateDraft({ showAdvanced: !showAdvanced })}
            className="flex items-center gap-1.5 text-xs text-zinc-400 hover:text-cyan-300 transition-colors"
          >
            <Sliders className="w-3.5 h-3.5" />
            <span>算法灵敏度与校验设置</span>
            {showAdvanced ? <ChevronUp className="w-3.5 h-3.5" /> : <ChevronDown className="w-3.5 h-3.5" />}
          </button>

          {showAdvanced && (
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mt-3 p-3 bg-rf-bg/40 rounded-lg border border-rf-border/30">
              <div className="space-y-1">
                <label className="text-[11px] font-medium text-zinc-400">CFAR 门限 (dB)</label>
                <input
                  type="number"
                  step="0.5"
                  value={thresholdDb}
                  onChange={(e) => updateDraft({ thresholdDb: e.target.value === '' ? 10.0 : e.target.valueAsNumber })}
                  className="w-full px-2.5 py-1 text-xs rounded bg-rf-bg border border-rf-border text-zinc-100"
                />
              </div>
              <div className="space-y-1">
                <label className="text-[11px] font-medium text-zinc-400">最小输出 SNR (dB)</label>
                <input
                  type="number"
                  step="0.5"
                  value={minSnr}
                  onChange={(e) => updateDraft({ minSnr: e.target.value === '' ? 6.0 : e.target.valueAsNumber })}
                  className="w-full px-2.5 py-1 text-xs rounded bg-rf-bg border border-rf-border text-zinc-100"
                />
              </div>
              <div className="space-y-1">
                <label className="text-[11px] font-medium text-zinc-400">目标 FLRC 码率</label>
                <select
                  value={selectedBitrate || ''}
                  onChange={(e) => updateDraft({ selectedBitrate: e.target.value ? parseInt(e.target.value, 10) : undefined })}
                  className="w-full px-2.5 py-1 text-xs rounded bg-rf-bg border border-rf-border text-zinc-100"
                >
                  <option value="">全码率盲扫 (325k/1.3M/650k/260k)</option>
                  <option value={325000}>325 kbps (标准)</option>
                  <option value={1300000}>1.3 Mbps (高速)</option>
                  <option value={650000}>650 kbps</option>
                  <option value={260000}>260 kbps</option>
                </select>
              </div>
              <div className="flex items-center gap-2 pt-4">
                <input
                  type="checkbox"
                  id="no-flrc"
                  checked={noFlrc}
                  onChange={(e) => updateDraft({ noFlrc: e.target.checked })}
                  className="rounded border-rf-border text-cyan-600 focus:ring-0"
                />
                <label htmlFor="no-flrc" className="text-xs text-zinc-300 cursor-pointer">
                  仅能量感知 (跳过前导检验)
                </label>
              </div>
            </div>
          )}
        </div>

        {/* 触发操作按钮与 CLI 提示条 */}
        <div className="flex flex-wrap items-center justify-between gap-3 pt-2">
          <div className="flex items-center gap-2">
            <button
              type="button"
              disabled={scanMutation.isPending || stopMutation.isPending}
              onClick={handleStartScan}
              className="flex items-center gap-2 px-4 py-2 bg-gradient-to-r from-cyan-600 to-blue-600 hover:from-cyan-500 hover:to-blue-500 text-white text-xs font-semibold rounded-lg shadow-md shadow-cyan-950/40 transition-all disabled:opacity-50"
            >
              {scanMutation.isPending ? (
                <>
                  <Loader2 className="w-4 h-4 animate-spin" />
                  <span>正在扫描感知…</span>
                </>
              ) : (
                <>
                  <Play className="w-4 h-4 fill-current" />
                  <span>开始盲扫感知</span>
                </>
              )}
            </button>

            {(scanMutation.isPending || stopMutation.isPending) && (
              <button
                type="button"
                disabled={stopMutation.isPending}
                onClick={() => activeScanId.current && stopMutation.mutate(activeScanId.current)}
                className="flex items-center gap-2 px-4 py-2 bg-red-600 hover:bg-red-500 text-white text-xs font-semibold rounded-lg transition-colors disabled:opacity-50"
              >
                {stopMutation.isPending
                  ? <Loader2 className="w-4 h-4 animate-spin" />
                  : <Square className="w-4 h-4 fill-current" />}
                <span>{stopMutation.isPending ? '正在停止…' : '停止扫描'}</span>
              </button>
            )}

            {scanNotice && <span role="status" className="text-xs text-zinc-300">{scanNotice}</span>}

            {report && report.channels.length > 0 && (
              <button
                type="button"
                disabled={exportMutation.isPending}
                onClick={() => exportMutation.mutate()}
                className="flex items-center gap-1.5 px-3 py-2 bg-rf-panel hover:bg-rf-surface border border-rf-border text-xs text-zinc-200 rounded-lg transition-colors"
              >
                <Download className="w-3.5 h-3.5" />
                <span>导出信道预设 (TOML)</span>
              </button>
            )}

            {exportNotice && (
              <span className="text-xs text-emerald-400 bg-emerald-500/10 border border-emerald-500/30 px-2.5 py-1 rounded">
                {exportNotice}
              </span>
            )}
          </div>

          <div className="flex items-center gap-2">
            <span className="text-[11px] text-zinc-400">CLI 命令:</span>
            <code className="text-[11px] bg-rf-bg px-2 py-1 rounded border border-rf-border font-mono text-zinc-300 max-w-sm truncate">
              {cliPreview}
            </code>
            <button
              type="button"
              onClick={handleCopyCli}
              title="复制 CLI 命令"
              className="p-1.5 bg-rf-panel hover:bg-rf-surface border border-rf-border rounded text-zinc-300 hover:text-cyan-300 transition-colors"
            >
              {copiedCli ? <Check className="w-3.5 h-3.5 text-emerald-400" /> : <Copy className="w-3.5 h-3.5" />}
            </button>
          </div>
        </div>
      </div>

      {scanMutation.isPending && (
        <div role="status" className="p-3 rounded-xl border border-cyan-500/25 bg-cyan-500/5 space-y-2 text-xs text-cyan-200">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <span>
              {{ waiting: '正在启动扫描', starting: '正在启动扫描', reading: '正在读取 IQ 文件',
                capturing: '正在采样', analyzing: '正在分析频谱', verifying: '正在检验 FLRC',
                hop_completed: '频点分析完成', completed: '正在汇总结果' }[progress?.stage || 'starting']}
              {progress?.frequency_mhz != null && ` · 当前频点 ${progress.frequency_mhz.toFixed(2)} MHz`}
              {progress?.hops_total != null && ` · 已完成 ${progress.hops_completed}/${progress.hops_total} 个频点`}
              {progress?.candidates_total != null && ` · 已${progress.stage === 'verifying' ? '检验' : '分析'} ${progress.candidates_completed}/${progress.candidates_total} 个候选`}
            </span>
            <span>{progress?.percent ?? 0}%</span>
          </div>
          <progress aria-label="盲扫进度" max={100} value={progress?.percent ?? 0} className="w-full h-1.5 accent-cyan-400" />
          <p className="text-zinc-400">信道列表随扫描更新；“待 FLRC 检验”的候选会在检验后更新类型与同步字。</p>
          {progressQuery.isError && <p className="text-amber-300">进度更新暂时中断，正在重试…</p>}
        </div>
      )}

      {stopMutation.isError && (
        <div role="alert" className="flex items-center gap-2 text-xs text-red-400">
          <AlertCircle className="w-4 h-4" />
          <span>停止失败: {stopMutation.error.message}</span>
        </div>
      )}

      {scanMutation.isError && (
        <div className="flex items-center gap-2 p-3 bg-red-500/10 border border-red-500/30 rounded-xl text-red-300 text-xs">
          <AlertCircle className="w-4 h-4 flex-shrink-0" />
          <span>扫描失败: {scanMutation.error?.message || '未知错误'}</span>
        </div>
      )}

      {/* 2.4GHz 全景频谱信道热力图 (Occupancy Map) */}
      <div className="bg-rf-panel/40 p-4 rounded-xl border border-rf-border space-y-3">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Layers className="w-4 h-4 text-cyan-400" />
            <h3 className="text-xs font-semibold text-zinc-200 uppercase tracking-wider">
              2.4GHz 频谱全景占用与信道分布图
            </h3>
            {report && (
              <span className="text-[11px] text-zinc-400">
                (底噪: {report.noise_floor_dbfs.toFixed(1)} dBFS | 门限: +{report.threshold_db} dB)
              </span>
            )}
          </div>
          <div className="flex items-center gap-3 text-[11px]">
            <span className="flex items-center gap-1 text-emerald-400">
              <span className="w-2.5 h-2.5 rounded-full bg-emerald-500 inline-block" /> FLRC 载波
            </span>
            <span className="flex items-center gap-1 text-amber-400">
              <span className="w-2.5 h-2.5 rounded-full bg-amber-500 inline-block" /> 宽带 (Wi-Fi)
            </span>
            <span className="flex items-center gap-1 text-cyan-400">
              <span className="w-2.5 h-2.5 rounded-full bg-cyan-500 inline-block" /> 窄带/BLE
            </span>
            <span className="flex items-center gap-1 text-purple-400">
              <span className="w-2.5 h-2.5 rounded-full bg-purple-500 inline-block" /> CW 单音
            </span>
          </div>
        </div>

        {/* 交互式频段热力条 */}
        <div className="relative w-full h-32 bg-rf-bg/90 rounded-lg border border-rf-border/80 overflow-hidden flex flex-col justify-end p-2 select-none">
          {/* 网格背景线 */}
          <div className="absolute inset-0 grid grid-cols-8 divide-x divide-rf-border/30 pointer-events-none" />

          {/* 底噪基准虚线 */}
          <div className="absolute left-0 right-0 bottom-6 border-b border-dashed border-zinc-600/50 pointer-events-none z-0">
            <span className="absolute right-2 -top-4 text-[10px] text-zinc-500">
              底噪 {report ? `${report.noise_floor_dbfs.toFixed(1)} dBFS` : '--'}
            </span>
          </div>

          {/* 渲染感知到的信道胶囊柱 */}
          {report?.channels.map((ch, idx) => {
            const leftPercent = Math.max(0, Math.min(100, ((ch.frequency_mhz - bandStart) / bandSpan) * 100));
            const widthPercent = Math.max(1.5, Math.min(30, (ch.bandwidth_khz / 1000 / bandSpan) * 100));
            // 高度映射 (SNR 0..60 dB -> 20%..90%)
            const heightPercent = Math.max(25, Math.min(90, (ch.snr_db / 60.0) * 80 + 15));

            let barColor = 'bg-cyan-500 border-cyan-400 text-cyan-200';
            if (ch.is_flrc) {
              barColor = 'bg-emerald-500 border-emerald-300 text-emerald-100 shadow-lg shadow-emerald-950/60';
            } else if (ch.protocol_hint.includes('宽带')) {
              barColor = 'bg-amber-500 border-amber-300 text-amber-100';
            } else if (ch.protocol_hint.includes('CW') || ch.protocol_hint.includes('单音')) {
              barColor = 'bg-purple-500 border-purple-300 text-purple-100';
            }

            const isSelected = selectedChannel?.frequency_hz === ch.frequency_hz;

            return (
              <div
                key={`ch-bar-${ch.frequency_hz}`}
                onClick={() => selectChannel(ch.frequency_hz)}
                style={{
                  left: `${leftPercent}%`,
                  width: `${widthPercent}%`,
                  height: `${heightPercent}%`,
                  transform: 'translateX(-50%)',
                }}
                className={`absolute bottom-6 rounded-t cursor-pointer border transition-all duration-200 hover:scale-105 z-10 flex flex-col items-center justify-between p-1 ${barColor} ${
                  isSelected ? 'ring-2 ring-white scale-105 z-20' : 'opacity-85 hover:opacity-100'
                }`}
                title={`CH ${idx + 1}: ${ch.frequency_mhz} MHz | SNR: ${ch.snr_db} dB | ${ch.protocol_hint}`}
              >
                <span className="text-[10px] font-bold truncate max-w-full">
                  {ch.is_flrc ? `FLRC` : `CH${idx + 1}`}
                </span>
                <span className="text-[9px] opacity-90 truncate max-w-full">
                  {ch.snr_db.toFixed(0)}dB
                </span>
              </div>
            );
          })}

          {/* 频率刻度尺 (2400 .. 2483.5 MHz) */}
          <div className="relative z-10 w-full flex justify-between text-[10px] text-zinc-500 pt-1 border-t border-rf-border">
            <span>{bandStart.toFixed(1)} MHz</span>
            <span>{(bandStart + bandSpan * 0.25).toFixed(1)} MHz</span>
            <span>{(bandStart + bandSpan * 0.5).toFixed(1)} MHz</span>
            <span>{(bandStart + bandSpan * 0.75).toFixed(1)} MHz</span>
            <span>{bandStop.toFixed(1)} MHz</span>
          </div>
        </div>
      </div>

      {/* 感知信道列表与操作卡片 */}
      <div className="bg-rf-panel/40 p-4 rounded-xl border border-rf-border space-y-3">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Zap className="w-4 h-4 text-cyan-400" />
            <h3 className="text-xs font-semibold text-zinc-200 uppercase tracking-wider">
              感知信道清单 ({report?.channels.length || 0} 个载波，{report?.flrc_channels_count || 0} 个 FLRC 调制)
              {report?.device_info.partial && <span className="ml-2 text-amber-300">部分结果</span>}
            </h3>
          </div>
        </div>

        {report && report.channels.length > 0 ? (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs border-collapse">
              <thead>
                <tr className="border-b border-rf-border/80 text-[11px] text-zinc-400 uppercase font-medium">
                  <th className="py-2 px-3">信道</th>
                  <th className="py-2 px-3">中心频点</th>
                  <th className="py-2 px-3">带宽</th>
                  <th className="py-2 px-3">峰值功率</th>
                  <th className="py-2 px-3">SNR</th>
                  <th className="py-2 px-3">突发数</th>
                  <th className="py-2 px-3">调制 / 协议特征</th>
                  <th className="py-2 px-3">候选同步字</th>
                  <th className="py-2 px-3 text-right">一键流转操作</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-rf-border/40">
                {report.channels.map((ch, idx) => {
                  const isSelected = selectedChannel?.frequency_hz === ch.frequency_hz;
                  return (
                    <tr
                      key={`ch-row-${ch.frequency_hz}`}
                      onClick={() => selectChannel(ch.frequency_hz)}
                      className={`cursor-pointer transition-colors ${
                        isSelected
                          ? 'bg-cyan-500/15 border-l-2 border-cyan-400'
                          : 'hover:bg-rf-panel/70'
                      }`}
                    >
                      <td className="py-2.5 px-3 font-semibold text-zinc-300">
                        CH {String(idx + 1).padStart(2, '0')}
                      </td>
                      <td className="py-2.5 px-3 font-mono text-zinc-100">
                        {ch.frequency_mhz.toFixed(4)} MHz
                      </td>
                      <td className="py-2.5 px-3 text-zinc-300">
                        {ch.bandwidth_khz.toFixed(1)} kHz
                      </td>
                      <td className="py-2.5 px-3 text-zinc-300">
                        {ch.peak_power_dbfs.toFixed(1)} dBFS
                      </td>
                      <td className="py-2.5 px-3">
                        <span className="font-semibold text-cyan-300">
                          {ch.snr_db.toFixed(1)} dB
                        </span>
                      </td>
                      <td className="py-2.5 px-3 text-zinc-400">
                        {ch.burst_count} 次 ({(ch.duty_cycle * 100).toFixed(1)}%)
                      </td>
                      <td className="py-2.5 px-3">
                        <span
                          className={`px-2 py-0.5 rounded text-[11px] font-medium inline-block ${
                            ch.is_flrc
                              ? 'bg-emerald-500/20 text-emerald-300 border border-emerald-500/30'
                              : ch.protocol_hint.includes('宽带')
                              ? 'bg-amber-500/20 text-amber-300 border border-amber-500/30'
                              : 'bg-zinc-800 text-zinc-300'
                          }`}
                        >
                          {ch.protocol_hint}
                        </span>
                      </td>
                      <td className="py-2.5 px-3">
                        {ch.sync_words.length > 0 ? (
                          <div className="flex items-center gap-1.5 flex-wrap">
                            {ch.sync_words.slice(0, 2).map((s) => (
                              <button
                                type="button"
                                key={s}
                                onClick={(e) => {
                                  e.stopPropagation();
                                  handleCopySync(s);
                                }}
                                title="点击复制同步字"
                                className="font-mono text-[11px] px-1.5 py-0.5 rounded bg-rf-bg border border-rf-border hover:border-cyan-500 text-zinc-200 flex items-center gap-1"
                              >
                                {s}
                                {copiedSync === s && <Check className="w-2.5 h-2.5 text-emerald-400" />}
                              </button>
                            ))}
                          </div>
                        ) : (
                          <span className="text-zinc-500">--</span>
                        )}
                      </td>
                      <td className="py-2.5 px-3 text-right">
                        <div className="flex items-center justify-end gap-1.5" onClick={(e) => e.stopPropagation()}>
                          <button
                            type="button"
                            onClick={() => handleTransferToSpectrum(ch)}
                            title="转入实时频谱监视此频点"
                            className="flex items-center gap-1 px-2 py-1 text-[11px] bg-rf-panel hover:bg-rf-surface border border-rf-border hover:border-cyan-500 text-cyan-300 rounded transition-colors"
                          >
                            <Radio className="w-3 h-3" />
                            <span>实时监视</span>
                          </button>
                          <button
                            type="button"
                            onClick={() => handleTransferToDemod(ch)}
                            title="转入离线解调分析"
                            className="flex items-center gap-1 px-2 py-1 text-[11px] bg-cyan-600/80 hover:bg-cyan-600 text-white rounded transition-colors"
                          >
                            <Play className="w-3 h-3 fill-current" />
                            <span>转入解调</span>
                          </button>
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        ) : (
          <div className="py-8 text-center text-zinc-500 text-xs">
            {scanMutation.isPending
              ? '正在扫描频段并计算谱特征，请稍候…'
              : '暂无扫描结果。请在上方选择参数后点击“开始盲扫感知”。'}
          </div>
        )}
      </div>
    </div>
  );
};
