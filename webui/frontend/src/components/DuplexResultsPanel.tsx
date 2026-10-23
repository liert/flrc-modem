import React, { useState, useMemo } from 'react';
import { Download, Binary } from 'lucide-react';
import type { DuplexResult } from '../types';
import { duplexButtonClass } from './DuplexPayloadEditor';
import { useWorkstationStore } from '../stores/useWorkstationStore';
import { duplexProtocolDraft, type ProtocolRunSource } from '../utils/protocolSources';

const statusLabels: Record<string, string> = {
  reply_matched: '匹配回复', reply_timeout: '回复超时', tx_completed: '发送完成',
  send_failed: '发送失败', rejected: '请求被拒绝', cancelled: '交互已取消', interrupted: '结果未确认',
};

export function DuplexResultsPanel({ results, run }: { results: DuplexResult[]; run?: ProtocolRunSource }) {
  const [showAll, setShowAll] = useState(false);
  const [statusFilter, setStatusFilter] = useState<'all' | 'matched' | 'failed'>('all');
  const openProtocol = (result: DuplexResult, direction: 'TX' | 'RX') => {
    const draft = duplexProtocolDraft(result, direction, run);
    if (!draft) return;
    const workstation = useWorkstationStore.getState();
    workstation.openProtocol(draft);
  };

  // RTT 与交互统计指标
  const stats = useMemo(() => {
    if (!results.length) return null;
    const matched = results.filter(r => r.status === 'reply_matched' || r.reply_matched);
    const rttValues = results.filter(r => r.rtt_ms != null).map(r => r.rtt_ms!);
    const avgRtt = rttValues.length ? rttValues.reduce((a, b) => a + b, 0) / rttValues.length : null;
    const minRtt = rttValues.length ? Math.min(...rttValues) : null;
    const maxRtt = rttValues.length ? Math.max(...rttValues) : null;
    const jitter = rttValues.length > 1 && avgRtt !== null
      ? Math.sqrt(rttValues.reduce((sq, n) => sq + Math.pow(n - avgRtt, 2), 0) / (rttValues.length - 1))
      : null;

    return {
      total: results.length,
      matchedCount: matched.length,
      matchRate: ((matched.length / results.length) * 100).toFixed(1),
      avgRtt,
      minRtt,
      maxRtt,
      jitter,
    };
  }, [results]);

  const filtered = useMemo(() => {
    if (statusFilter === 'matched') return results.filter(r => r.status === 'reply_matched' || r.reply_matched);
    if (statusFilter === 'failed') return results.filter(r => ['reply_timeout', 'send_failed', 'rejected', 'interrupted'].includes(r.status));
    return results;
  }, [results, statusFilter]);

  const handleExportJson = () => {
    const blob = new Blob([JSON.stringify(results, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `duplex_results_${Date.now()}.json`;
    a.click();
    URL.revokeObjectURL(url);
  };

  return <div className="space-y-3">
    {/* 统计看板 */}
    {stats && (
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 text-xs font-mono">
        <div className="bg-rf-panel/60 p-2.5 rounded-lg border border-rf-border">
          <span className="text-zinc-500 block text-[10px]">回复匹配率</span>
          <span className="text-emerald-400 font-bold text-sm">{stats.matchRate}%</span>
          <span className="text-zinc-500 text-[10px] ml-1">({stats.matchedCount}/{stats.total})</span>
        </div>
        <div className="bg-rf-panel/60 p-2.5 rounded-lg border border-rf-border">
          <span className="text-zinc-500 block text-[10px]">平均往返时延 (RTT)</span>
          <span className="text-cyan-300 font-bold text-sm">
            {stats.avgRtt !== null ? `${stats.avgRtt.toFixed(2)} ms` : '--'}
          </span>
        </div>
        <div className="bg-rf-panel/60 p-2.5 rounded-lg border border-rf-border">
          <span className="text-zinc-500 block text-[10px]">时延极值 (Min / Max)</span>
          <span className="text-zinc-200 font-semibold text-xs">
            {stats.minRtt !== null ? `${stats.minRtt.toFixed(1)} / ${stats.maxRtt?.toFixed(1)} ms` : '--'}
          </span>
        </div>
        <div className="bg-rf-panel/60 p-2.5 rounded-lg border border-rf-border">
          <span className="text-zinc-500 block text-[10px]">时延抖动 (Jitter)</span>
          <span className="text-amber-300 font-semibold text-xs">
            {stats.jitter !== null ? `±${stats.jitter.toFixed(2)} ms` : '--'}
          </span>
        </div>
      </div>
    )}

    {/* 控制过滤条 */}
    <div className="flex flex-wrap items-center justify-between gap-2 text-zinc-400">
      <div className="flex items-center space-x-2">
        <span>最近 {results.length} 条交互 · 任务结束后仍保留</span>
        {results.length > 0 && (
          <select
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value as any)}
            className="bg-rf-surface border border-rf-border rounded px-2 py-0.5 text-[11px] text-zinc-300 font-mono focus:outline-none"
          >
            <option value="all">全部状态 ({results.length})</option>
            <option value="matched">仅匹配回复 ({stats?.matchedCount || 0})</option>
            <option value="failed">仅超时/异常</option>
          </select>
        )}
      </div>
      <div className="flex items-center space-x-2">
        {results.length > 0 && (
          <button
            type="button"
            onClick={handleExportJson}
            className={`${duplexButtonClass} flex items-center space-x-1`}
            title="导出全部交互记录为 JSON"
          >
            <Download className="w-3 h-3 text-cyan-400" />
            <span>导出记录</span>
          </button>
        )}
        {results.length > 8 && <button className={duplexButtonClass} onClick={() => setShowAll(value => !value)}>{showAll ? '仅显示最近 8 条' : '显示全部记录'}</button>}
      </div>
    </div>
    {!results.length && <p className="rounded-lg border border-dashed border-rf-border px-4 py-8 text-center text-zinc-500">尚无交互结果，发送后可在这里查看确认和回复。</p>}
    {(showAll ? filtered : filtered.slice(0, 8)).map((result, index) => <details key={`${result.request_id}-${result.timestamp || index}`} className="group/result rounded-lg border border-rf-border bg-rf-bg/50">
      <summary className="flex cursor-pointer flex-wrap items-center gap-x-3 gap-y-1 px-3 py-3 text-zinc-400 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-cyan-400">
        <span className={`font-medium ${['reply_matched', 'tx_completed'].includes(result.status) ? 'text-emerald-400' : 'text-amber-400'}`}>{statusLabels[result.status] || result.status}</span>
        <span className="font-mono">{result.timestamp && new Date(result.timestamp).toLocaleTimeString()}</span>
        <span className="min-w-0 truncate">{result.plan_id ? result.source === 'periodic' ? `周期 · ${result.item_id}` : `流程第 ${result.cycle} 轮 · ${result.item_id}` : '手动投递'}</span>
        {result.rtt_ms != null && <span className="font-mono text-zinc-500">RTT {result.rtt_ms.toFixed(3)} ms</span>}
        {result.invert_sync && <span className="text-amber-400">TX 同步字反转</span>}
        {result.reply_invert_sync && <span className="text-amber-400">回复同步字反转</span>}
      </summary>
      <div className="space-y-2 border-t border-rf-border px-3 py-3 font-mono leading-6 text-zinc-400 select-text break-all">
        <p className="text-zinc-500">{result.request_id}</p>
        <p>TX 完成：{result.tx_completed === undefined ? '未确认' : result.tx_completed ? '是' : '否'} · 回复匹配：{result.reply_matched === undefined ? '未确认' : result.reply_matched ? '是' : '否'}</p>
        {result.tx_completed && <p>t={result.tx_hw_time?.toFixed(6)} s · Slot {result.tx_slot} · {result.tx_frequency_hz} Hz</p>}
        {result.tx_sync_word_hex && <p>TX 同步字：{result.tx_sync_word_hex} · {result.invert_sync ? '已反转' : '未反转'}</p>}
        {result.reply_sync_word_hex && <p>期望回复同步字：{result.reply_sync_word_hex} · {result.reply_invert_sync ? '已反转' : '未反转'}</p>}
        {(['TX', 'RX'] as const).map(direction => {
          const payload = direction === 'TX' ? result.tx_payload_hex : result.rx_reply?.payload_hex;
          if (!payload?.trim()) return null;
          return <div key={direction} className="flex flex-wrap items-start gap-2 rounded border border-rf-border p-2">
            <p className="min-w-0 flex-1">{direction}：{payload}</p>
            <button type="button" className={duplexButtonClass + ' shrink-0'}
              onClick={() => openProtocol(result, direction)} title="载入完整物理载荷到协议解析面板，无需 JSONL 文件">
              <Binary aria-hidden="true" className="h-3.5 w-3.5" />解析 {direction}
            </button>
          </div>;
        })}
        {!result.rx_reply?.payload_hex && <p className="text-zinc-500">未记录 RX 回复载荷，不能解析未捕获的回复。</p>}
        {result.plan_id && <p>计划调度 +{result.scheduled_offset_ms} ms · 实际开始 +{result.started_offset_ms} ms · 调度迟到 {result.lateness_ms} ms · 跳过 {result.skipped_periods} 个周期</p>}
        {result.error && <p className="text-amber-400">{result.error}</p>}
      </div>
    </details>)}
  </div>;
}
