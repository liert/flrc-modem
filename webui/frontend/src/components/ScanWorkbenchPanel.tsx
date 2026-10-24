import { hexText } from '../utils/hex';
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
  BookmarkPlus,
  History,
  Trash2,
  BarChart3,
} from 'lucide-react';
import { api } from '../api/client';
import type { ChannelCandidate, ScanReport, QuickScanRequest } from '../types';
import { useWorkstationStore } from '../stores/useWorkstationStore';
import { useScanStore } from '../stores/useScanStore';
import { sampleRateCompatible } from '../utils/sampleRates';
import { UsrpSampleRateSelect } from './UsrpSampleRateSelect';

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
    scanMode, iqPath, sampleRate, usrpSampleRate, masterClockRate, usrpArgs, usrpChannel, usrpAntenna, centerFreqMhz, iqFormat, duration,
    startFreqMhz, stopFreqMhz, stepBwMhz, dwell, passes, refineDwell, settleSeconds, discardSeconds, gain,
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

  const {
    snapshots,
    activeSnapshotId,
    saveSnapshot,
    loadSnapshot,
    deleteSnapshot,
    clearSnapshots,
  } = useScanStore();

  const heatmapBins = useMemo(() => {
    const bins: number[] = [];
    for (let f = 2400; f < 2484; f += 2) {
      bins.push(f);
    }
    return bins;
  }, []);

  const handleExportSnapshots = () => {
    const blob = new Blob([JSON.stringify(snapshots, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `scan_snapshots_${Date.now()}.json`;
    a.click();
    URL.revokeObjectURL(url);
  };

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
      if (passes !== 2) parts.push('--passes', String(passes));
      if (refineDwell !== 0.25) parts.push('--refine-dwell', String(refineDwell));
      if (gain !== 30.0) parts.push('--gain', String(gain));
      if (scanMode === 'usrp') {
        parts.push('--sample-rate', String(usrpSampleRate));
        if (settleSeconds !== 0.01) parts.push('--settle-seconds', String(settleSeconds));
        if (discardSeconds !== 0.002) parts.push('--discard-seconds', String(discardSeconds));
        if (masterClockRate !== null) parts.push('--master-clock-rate', String(masterClockRate));
        if (usrpArgs.trim()) parts.push('--args', `'${usrpArgs.trim().replace(/'/g, "''")}'`);
        if (usrpChannel !== 0) parts.push('--channel', String(usrpChannel));
        if (usrpAntenna.trim()) parts.push('--antenna', `'${usrpAntenna.trim().replace(/'/g, "''")}'`);
      }
    }
    if (thresholdDb !== 10.0) parts.push('--threshold-db', String(thresholdDb));
    if (minSnr !== 6.0) parts.push('--min-snr', String(minSnr));
    if (selectedBitrate) parts.push('--bit-rate', String(selectedBitrate));
    if (noFlrc) parts.push('--no-flrc');
    parts.push('--output-config', 'configs/scanned_channels.toml');
    return parts.join(' ');
  }, [
    scanMode, iqPath, sampleRate, usrpSampleRate, masterClockRate, usrpArgs, usrpChannel, usrpAntenna, centerFreqMhz, iqFormat, duration,
    startFreqMhz, stopFreqMhz, stepBwMhz, dwell, passes, refineDwell, settleSeconds, discardSeconds, gain,
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
        req.passes = passes;
        req.refine_dwell = refineDwell;
        req.gain = gain;
        if (scanMode === 'usrp') {
          req.sample_rate = usrpSampleRate;
          req.settle_seconds = settleSeconds;
          req.discard_seconds = discardSeconds;
          req.master_clock_rate = masterClockRate ?? undefined;
          req.args = usrpArgs.trim() || undefined;
          req.channel = usrpChannel;
          req.antenna = usrpAntenna.trim() || undefined;
        }
      }
      return await api.quickScan(req);
    },
    onSuccess: (res, scanId) => {
      activeScanId.current = null;
      if ('status' in res) {
        if (res.report) {
          updateReport(res.report, scanId);
          if (res.report.channels.length > 0) {
            saveSnapshot(`扫描已停止 (${res.report.channels.length}信道)`);
          }
        }
        setScanNotice(res.report ? '扫描已停止，已保留当前结果' : '扫描已停止');
        return;
      }
      updateReport(res, scanId);
      if (res.channels && res.channels.length > 0) {
        saveSnapshot(`盲扫完成 (${res.channels.length}信道, ${res.flrc_channels_count} FLRC)`);
      }
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
        (c.is_flrc ? c.sync_words : []).forEach(s => {
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
    navigator.clipboard.writeText(hexText(sync));
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
              <label className="text-xs font-medium text-zinc-300">扫描带宽上限 (MHz)</label>
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
              <label htmlFor="scan-passes" className="text-xs font-medium text-zinc-300">扫描轮数</label>
              <input id="scan-passes" type="number" min={1} max={20} step={1} value={passes}
                onChange={event => updateDraft({ passes: event.target.value === '' ? 2 : event.target.valueAsNumber })}
                className="w-full px-3 py-1.5 text-xs rounded-md bg-rf-bg border border-rf-border text-zinc-100" />
            </div>
            <div className="space-y-1">
              <label htmlFor="scan-refine" className="text-xs font-medium text-zinc-300">候选精扫时长 (s，0 关闭)</label>
              <input id="scan-refine" type="number" min={0} max={60} step={0.05} value={refineDwell}
                onChange={event => updateDraft({ refineDwell: event.target.value === '' ? 0.25 : event.target.valueAsNumber })}
                className="w-full px-3 py-1.5 text-xs rounded-md bg-rf-bg border border-rf-border text-zinc-100" />
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

        {scanMode === 'usrp' && (
          <div className="space-y-2 border-t border-rf-border/40 pt-3">
            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-1">
                <label htmlFor="scan-settle" className="text-xs text-zinc-300">调谐稳定等待 (s)</label>
                <input id="scan-settle" type="number" min={0} max={5} step={0.005} value={settleSeconds}
                  onChange={event => updateDraft({ settleSeconds: event.target.value === '' ? 0.01 : event.target.valueAsNumber })}
                  className="w-full px-3 py-1.5 text-xs rounded-md bg-rf-bg border border-rf-border text-zinc-100" />
              </div>
              <div className="space-y-1">
                <label htmlFor="scan-discard" className="text-xs text-zinc-300">丢弃初始采样 (s)</label>
                <input id="scan-discard" type="number" min={0} max={5} step={0.001} value={discardSeconds}
                  onChange={event => updateDraft({ discardSeconds: event.target.value === '' ? 0.002 : event.target.valueAsNumber })}
                  className="w-full px-3 py-1.5 text-xs rounded-md bg-rf-bg border border-rf-border text-zinc-100" />
              </div>
            </div>
            <div className="grid grid-cols-2 lg:grid-cols-3 gap-3">
              <div className="space-y-1">
                <label htmlFor="scan-usrp-clock" className="text-xs font-medium text-zinc-300">主时钟频率 (MHz)</label>
                <select id="scan-usrp-clock" value={masterClockRate ?? ''}
                  onChange={event => {
                    const clock = event.target.value === '' ? null : Number(event.target.value);
                    updateDraft({ masterClockRate: clock,
                      ...(clock !== null && !sampleRateCompatible(clock, usrpSampleRate)
                        ? { usrpSampleRate: clock / 10 } : {}) });
                  }}
                  className="w-full px-3 py-1.5 text-xs rounded-md bg-rf-bg border border-rf-border text-zinc-100 focus:outline-none focus:border-cyan-500">
                  <option value="">设备默认</option>
                  <option value={125000000}>125 MHz</option>
                  <option value={122880000}>122.88 MHz</option>
                  <option value={153600000}>153.6 MHz</option>
                </select>
              </div>
              <div className="space-y-1">
                <label htmlFor="scan-usrp-rate" className="text-xs font-medium text-zinc-300">采样率（按主时钟分组）</label>
                <UsrpSampleRateSelect id="scan-usrp-rate" masterClockRate={masterClockRate} sampleRate={usrpSampleRate}
                  recommendedDivisor={10} onChange={value => updateDraft({ masterClockRate: value.masterClockRate, usrpSampleRate: value.sampleRate })}
                  className="w-full px-3 py-1.5 text-xs rounded-md bg-rf-bg border border-rf-border text-zinc-100 focus:outline-none focus:border-cyan-500" />
                <p className="text-[11px] leading-5 text-zinc-500">跨组选择会同时更新主时钟；实际速率以设备回读为准。</p>
              </div>
              <div className="space-y-1">
                <label htmlFor="scan-usrp-args" className="text-xs font-medium text-zinc-300">USRP 设备参数</label>
                <input id="scan-usrp-args" type="text" value={usrpArgs} placeholder="例如 addr=192.168.10.2；留空自动发现"
                  onChange={event => updateDraft({ usrpArgs: event.target.value })}
                  className="w-full px-3 py-1.5 text-xs rounded-md bg-rf-bg border border-rf-border text-zinc-100 focus:outline-none focus:border-cyan-500" />
              </div>
              <div className="space-y-1">
                <label htmlFor="scan-usrp-channel" className="text-xs font-medium text-zinc-300">接收通道</label>
                <input id="scan-usrp-channel" type="number" min={0} step={1} value={usrpChannel}
                  onChange={event => updateDraft({ usrpChannel: event.target.value === '' ? 0 : event.target.valueAsNumber })}
                  className="w-full px-3 py-1.5 text-xs rounded-md bg-rf-bg border border-rf-border text-zinc-100 focus:outline-none focus:border-cyan-500" />
              </div>
              <div className="space-y-1">
                <label htmlFor="scan-usrp-antenna" className="text-xs font-medium text-zinc-300">接收天线端口</label>
                <input id="scan-usrp-antenna" type="text" value={usrpAntenna} placeholder="例如 RX2；留空使用设备默认"
                  onChange={event => updateDraft({ usrpAntenna: event.target.value })}
                  className="w-full px-3 py-1.5 text-xs rounded-md bg-rf-bg border border-rf-border text-zinc-100 focus:outline-none focus:border-cyan-500" />
              </div>
            </div>
            <p className="text-[11px] leading-5 text-zinc-400">主时钟在创建设备时设置。采样率独立于扫描带宽上限；频轴和扫频覆盖使用 UHD 实际回读值，USRP 全程保留浮点 IQ。</p>
          </div>
        )}
        {scanMode !== 'offline' && <p className="text-[11px] leading-5 text-zinc-400">相邻频段保留重叠区域，并覆盖起止边界。短突发可能落在两次驻留之间；驻留时长应覆盖信号发包周期，例如每 200 ms 一帧时可设为 0.25 s 或更长。</p>}

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

      {report?.scan_mode === 'live_sdr_usrp' && (
        <div role="status" className="flex flex-wrap gap-x-4 gap-y-1 rounded-lg border border-rf-border bg-rf-panel/40 px-3 py-2 text-xs text-zinc-300">
          <span>实际采样率 <strong className="font-mono text-cyan-300">{(report.sample_rate_sps / 1e6).toLocaleString('en-US', { maximumFractionDigits: 6 })} MSps</strong></span>
          {typeof report.device_info.actual_master_clock_rate === 'number' && report.device_info.actual_master_clock_rate > 0 &&
            <span>实际主时钟 <strong className="font-mono">{report.device_info.actual_master_clock_rate / 1e6} MHz</strong></span>}
          <span>已扫描 {report.device_info.hops_completed ?? 0} / {report.device_info.hops ?? 0} 个频点</span>
          {Array.isArray(report.device_info.unsupported_bit_rates) && report.device_info.unsupported_bit_rates.length > 0 &&
            <span className="text-amber-300">实际采样率不足以检验 {report.device_info.unsupported_bit_rates.map((rate: number) => `${rate / 1000} kbps`).join('、')}，这些信号仅保留能量候选</span>}
          {report.device_info.iq_format === 'cf32' && <span>浮点 IQ</span>}
          {(report.device_info.overflow_count > 0 || report.device_info.short_capture_hops > 0) &&
            <span className="text-amber-300">采集有丢样或驻留不足，可能漏检；溢出 {report.device_info.overflow_count ?? 0} 次，短采集 {report.device_info.short_capture_hops ?? 0} 个频点</span>}
        </div>
      )}

      {scanMutation.isPending && (
        <div role="status" className="p-3 rounded-xl border border-cyan-500/25 bg-cyan-500/5 space-y-2 text-xs text-cyan-200">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <span>
              {{ waiting: '正在启动扫描', starting: '正在启动扫描', reading: '正在读取 IQ 文件',
                capturing: '正在采样', analyzing: '正在分析频谱', verifying: '正在检验 FLRC',
                refining: '正在精扫候选信道', hop_completed: '频点分析完成', completed: '正在汇总结果' }[progress?.stage || 'starting']}
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
              感知信道清单 ({report?.channels.length || 0} 个载波，{report?.flrc_channels_count || 0} 个 CRC 确认)
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
                        <div>{ch.burst_count} 个突发 · CRC {ch.crc_valid_frames ?? ch.details?.crc_valid_frames ?? 0} 帧</div>
                        <div className="text-[10px] text-zinc-500">占空比 {(ch.duty_cycle * 100).toFixed(1)}% · {ch.observation_count ?? 1} 次检出</div>
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
                                {hexText(s)}
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

      {/* 盲扫历史快照与 2.4GHz 全频段占用热力时序图 */}
      <div className="bg-rf-panel/40 p-4 rounded-xl border border-rf-border space-y-3">
        <div className="flex flex-wrap items-center justify-between gap-2 border-b border-rf-border/60 pb-3">
          <div className="flex items-center space-x-2">
            <History className="w-4 h-4 text-cyan-400" />
            <h3 className="text-xs font-semibold text-zinc-200 uppercase tracking-wider">
              盲扫历史快照与 2.4GHz 信道占用时序热力 ({snapshots.length} 个快照)
            </h3>
          </div>
          <div className="flex items-center space-x-2">
            {report && report.channels.length > 0 && (
              <button
                type="button"
                onClick={() => saveSnapshot()}
                className="px-2.5 py-1 text-[11px] bg-cyan-600/80 hover:bg-cyan-600 text-white rounded flex items-center space-x-1 shadow-sm transition-colors"
                title="将当前扫描结果另存为快照"
              >
                <BookmarkPlus className="w-3.5 h-3.5" />
                <span>保存当前快照</span>
              </button>
            )}
            {snapshots.length > 0 && (
              <>
                <button
                  type="button"
                  onClick={handleExportSnapshots}
                  className="px-2.5 py-1 text-[11px] bg-rf-panel hover:bg-rf-border text-zinc-300 border border-rf-border rounded flex items-center space-x-1 transition-colors"
                  title="导出全部快照历史为 JSON"
                >
                  <Download className="w-3.5 h-3.5 text-cyan-400" />
                  <span>导出快照</span>
                </button>
                <button
                  type="button"
                  onClick={() => clearSnapshots()}
                  className="px-2 py-1 text-[11px] bg-rf-panel hover:bg-red-950/60 text-zinc-400 hover:text-red-400 border border-rf-border rounded flex items-center space-x-1 transition-colors"
                  title="清空历史快照"
                >
                  <Trash2 className="w-3.5 h-3.5" />
                  <span>清空</span>
                </button>
              </>
            )}
          </div>
        </div>

        {snapshots.length > 0 ? (
          <div className="space-y-3">
            {/* 热力时序矩阵 */}
            <div className="bg-black/50 p-3 rounded-lg border border-rf-border/80 overflow-x-auto space-y-2 select-none">
              <div className="flex items-center justify-between text-[11px] font-mono text-zinc-400 pb-1 border-b border-rf-border/40">
                <span className="flex items-center space-x-1">
                  <BarChart3 className="w-3.5 h-3.5 text-cyan-400" />
                  <span>2.4GHz 全频段信道活跃时序分布 (42 频段栅格 / 2MHz 步长)</span>
                </span>
                <div className="flex items-center space-x-3 text-[10px]">
                  <span className="flex items-center space-x-1"><span className="w-2 h-2 rounded-full bg-emerald-400" /><span>FLRC</span></span>
                  <span className="flex items-center space-x-1"><span className="w-2 h-2 rounded-full bg-amber-400" /><span>宽带</span></span>
                  <span className="flex items-center space-x-1"><span className="w-2 h-2 rounded-full bg-cyan-400" /><span>窄带</span></span>
                </div>
              </div>

              {/* 频率列标 */}
              <div className="flex items-center space-x-1 text-[9px] font-mono text-zinc-500 pl-28 min-w-[680px]">
                {heatmapBins.filter((_, i) => i % 6 === 0).map((f) => (
                  <span key={f} className="flex-1 text-left">
                    {f} MHz
                  </span>
                ))}
              </div>

              {/* 快照泳道行 */}
              <div className="space-y-1.5 min-w-[680px]">
                {snapshots.map((snap) => {
                  const isActive = activeSnapshotId === snap.id;
                  const snapDate = new Date(snap.timestamp);
                  const timeLabel = `${String(snapDate.getHours()).padStart(2, '0')}:${String(snapDate.getMinutes()).padStart(2, '0')}:${String(snapDate.getSeconds()).padStart(2, '0')}`;

                  return (
                    <div
                      key={snap.id}
                      onClick={() => loadSnapshot(snap.id)}
                      className={`flex items-center space-x-2 p-1.5 rounded cursor-pointer transition-all border ${
                        isActive
                          ? 'bg-cyan-950/40 border-cyan-500/80 ring-1 ring-cyan-500/30'
                          : 'bg-rf-surface/60 hover:bg-rf-panel border-rf-border/50'
                      }`}
                      title="点击恢复此快照的完整分析报告"
                    >
                      <div className="w-28 shrink-0 flex items-center justify-between text-[11px] font-mono">
                        <span className="text-zinc-300 font-bold">{timeLabel}</span>
                        <span className="text-[10px] px-1.5 py-0.2 rounded bg-zinc-800 text-zinc-400">
                          {snap.totalChannels}信道
                        </span>
                      </div>

                      {/* 42 个频段桶栅格 */}
                      <div className="flex-1 flex gap-0.5 h-4 bg-zinc-950/80 rounded p-0.5 border border-zinc-800/80">
                        {heatmapBins.map((freq) => {
                          const matchedCh = snap.report.channels.find(
                            (c) => c.frequency_mhz >= freq && c.frequency_mhz < freq + 2
                          );

                          if (!matchedCh) {
                            return <div key={freq} className="flex-1 bg-zinc-900/60 rounded-xs" />;
                          }

                          let cellBg = 'bg-cyan-500';
                          if (matchedCh.is_flrc) cellBg = 'bg-emerald-500 shadow-xs shadow-emerald-400';
                          else if (matchedCh.protocol_hint.includes('宽带')) cellBg = 'bg-amber-500';

                          return (
                            <div
                              key={freq}
                              className={`flex-1 rounded-xs transition-transform hover:scale-125 ${cellBg}`}
                              style={{ opacity: Math.min(1, Math.max(0.45, matchedCh.snr_db / 45)) }}
                              title={`${freq}~${freq + 2} MHz | CH: ${matchedCh.frequency_mhz} MHz, SNR: ${matchedCh.snr_db}dB, ${matchedCh.protocol_hint}`}
                            />
                          );
                        })}
                      </div>

                      <button
                        type="button"
                        onClick={(e) => {
                          e.stopPropagation();
                          deleteSnapshot(snap.id);
                        }}
                        className="text-zinc-600 hover:text-red-400 p-0.5 rounded transition-colors"
                        title="删除该快照"
                      >
                        <Trash2 className="w-3 h-3" />
                      </button>
                    </div>
                  );
                })}
              </div>
            </div>
          </div>
        ) : (
          <div className="py-4 text-center text-zinc-500 text-xs">
            暂无盲扫历史快照。完成扫描后点击“保存当前快照”，或运行多轮扫描即可自动形成 2.4GHz 信道占用时序热力走势。
          </div>
        )}
      </div>
    </div>
  );
};
