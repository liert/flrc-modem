import React, { useState } from 'react';
import type { DuplexResult } from '../types';
import { duplexButtonClass } from './DuplexPayloadEditor';

const statusLabels: Record<string, string> = {
  reply_matched: '匹配回复', reply_timeout: '回复超时', tx_completed: '发送完成',
  send_failed: '发送失败', rejected: '请求被拒绝', cancelled: '交互已取消', interrupted: '结果未确认',
};

export function DuplexResultsPanel({ results }: { results: DuplexResult[] }) {
  const [showAll, setShowAll] = useState(false);
  return <div className="space-y-3">
    <div className="flex flex-wrap items-center justify-between gap-2 text-zinc-400">
      <span>最近 {results.length} 条交互 · 任务结束后仍保留</span>
      {results.length > 8 && <button className={duplexButtonClass} onClick={() => setShowAll(value => !value)}>{showAll ? '仅显示最近 8 条' : '显示全部记录'}</button>}
    </div>
    {!results.length && <p className="rounded-lg border border-dashed border-rf-border px-4 py-8 text-center text-zinc-500">尚无交互结果，发送后可在这里查看确认和回复。</p>}
    {(showAll ? results : results.slice(0, 8)).map((result, index) => <details key={`${result.request_id}-${result.timestamp || index}`} className="group/result rounded-lg border border-rf-border bg-rf-bg/50">
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
        {result.tx_payload_hex && <p>TX：{result.tx_payload_hex}</p>}{result.rx_reply && <p>RX：{result.rx_reply.payload_hex}</p>}
        {result.plan_id && <p>计划调度 +{result.scheduled_offset_ms} ms · 实际开始 +{result.started_offset_ms} ms · 调度迟到 {result.lateness_ms} ms · 跳过 {result.skipped_periods} 个周期</p>}
        {result.error && <p className="text-amber-400">{result.error}</p>}
      </div>
    </details>)}
  </div>;
}
