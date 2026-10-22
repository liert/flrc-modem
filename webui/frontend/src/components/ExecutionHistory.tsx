import React from 'react';
import { ChevronDown, History } from 'lucide-react';
import type { RunRecord } from '../types';
import { runTime } from '../utils/runs';

const statusNames: Record<string, string> = {
  QUEUED: '排队中', RUNNING: '执行中', SUCCEEDED: '成功', FAILED: '失败', CANCELLED: '已取消',
};

export function ExecutionHistory({ executions, selectedAttempt, onSelect }: {
  executions: RunRecord[]; selectedAttempt: number; onSelect: (attempt: number) => void;
}) {
  const latest = executions[0]?.attempt || 1;
  const selected = executions.find(run => (run.attempt || 1) === selectedAttempt);
  return (
    <section aria-label="执行历史" className="shrink-0 border-b border-rf-border bg-rf-surface/40 px-4 py-2 text-xs">
      <details className="group/history">
      <summary className="flex cursor-pointer list-none flex-wrap items-center justify-between gap-2 rounded py-1 text-xs [&::-webkit-details-marker]:hidden focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-cyan-400">
        <h3 className="flex items-center gap-1.5 font-semibold text-zinc-300">
          <History className="w-3.5 h-3.5 text-cyan-400" />
          执行历史 <span className="font-mono text-zinc-500">{executions.length} 次</span>
        </h3>
        <span className="flex items-center gap-2 text-zinc-400">正在查看第 {selectedAttempt} 次执行{selectedAttempt === latest ? ' · 最新' : ''}<ChevronDown aria-hidden="true" className="h-3.5 w-3.5 transition-transform group-open/history:rotate-180" /></span>
      </summary>
      <ol className="mt-2 flex overflow-x-auto pb-1" aria-label="执行时间线">
        {executions.map(run => {
          const attempt = run.attempt || 1;
          const active = attempt === selectedAttempt;
          const statusColor = run.status === 'SUCCEEDED' ? 'text-emerald-400' : run.status === 'FAILED' ? 'text-red-400'
            : ['RUNNING', 'QUEUED'].includes(run.status) ? 'text-cyan-300' : 'text-zinc-400';
          const duration = run.started_at && run.completed_at
            ? Math.max(0, (Date.parse(run.completed_at) - Date.parse(run.started_at)) / 1000) : null;
          return (
            <li key={attempt} className="relative shrink-0 pr-3 pt-3">
              <div aria-hidden="true" className="absolute top-1 left-0 right-0 h-px bg-rf-border" />
              <span aria-hidden="true" className={`absolute top-0 left-3 h-2.5 w-2.5 rounded-full border-2 ${active ? 'bg-cyan-400 border-cyan-300' : 'bg-rf-surface border-zinc-600'}`} />
              <button
                aria-label={`查看第 ${attempt} 次执行`}
                aria-pressed={active}
                onClick={() => onSelect(attempt)}
                className={`min-w-48 rounded-lg border px-3 py-2 text-left text-xs transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-cyan-400 ${active
                  ? 'border-cyan-500/50 bg-cyan-500/10' : 'border-rf-border bg-rf-bg/50 hover:border-zinc-500'}`}
              >
                <span className="flex items-center justify-between gap-4">
                  <span className="font-medium text-zinc-200">第 {attempt} 次{attempt === latest ? ' · 最新' : ''}</span>
                  <span className={statusColor}>{statusNames[run.status] || run.status}</span>
                </span>
                <span className="block text-zinc-400 mt-1 font-mono">{runTime(run.attempt_created_at || run.created_at)}</span>
                <span className="block text-zinc-500 mt-1">
                  {duration !== null ? `耗时 ${duration.toFixed(1)}s · ` : ''}退出码 {run.exit_code ?? '—'}
                </span>
              </button>
            </li>
          );
        })}
      </ol>
      </details>
      {selected?.error && <p className="mt-2 text-xs text-red-400 break-all select-text">{selected.error}</p>}
    </section>
  );
}
