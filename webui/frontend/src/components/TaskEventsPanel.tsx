import React, { useMemo, useState } from 'react';
import { Search } from 'lucide-react';
import type { UIEvent } from '../types';
import { CopyButton } from './CopyButton';
import { duplexButtonClass, duplexInputClass } from './DuplexPayloadEditor';

export function TaskEventsPanel({ events, error, loading }: { events: UIEvent[]; error?: Error | null; loading: boolean }) {
  const [query, setQuery] = useState('');
  const [limit, setLimit] = useState(50);
  const filtered = useMemo(() => {
    const term = query.trim().toLowerCase();
    const matches = term ? events.filter(event => `${event.type} ${event.stage_id || ''} ${JSON.stringify(event.payload)}`.toLowerCase().includes(term)) : events;
    return [...matches].reverse();
  }, [events, query]);
  return <div className="flex h-full min-h-0 flex-col overflow-hidden rounded-lg border border-rf-border bg-rf-surface/40 text-xs">
    <div className="flex shrink-0 flex-wrap items-center justify-between gap-2 border-b border-rf-border bg-rf-panel/20 p-3">
      <label className="relative min-w-0 flex-1 sm:max-w-sm"><Search aria-hidden="true" className="absolute left-3 top-2.5 h-3.5 w-3.5 text-zinc-500" />
        <input type="search" aria-label="搜索结构化事件" placeholder="搜索事件类型、阶段或内容…" value={query} onChange={event => { setQuery(event.target.value); setLimit(50); }} className={`${duplexInputClass} pl-8`} /></label>
      <span className="text-zinc-500">{filtered.length} / {events.length} 条 · 最新在前</span>
    </div>
    <div className="min-h-0 flex-1 overflow-y-auto p-3 space-y-2">
      {error && <p role="alert" className="p-3 text-red-400">事件读取失败：{error.message}</p>}
      {filtered.slice(0, limit).map(event => <details key={JSON.stringify([event.seq, event.timestamp, event.type, event.stage_id, event.payload])} className="rounded-lg border border-rf-border/70 bg-rf-bg/50">
        <summary className="flex cursor-pointer flex-wrap items-center gap-x-3 gap-y-1 px-3 py-2.5 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-cyan-400">
          <span className="font-mono text-zinc-500">#{event.seq}</span><span className="min-w-0 break-all font-mono text-cyan-300">{event.type}</span>
          {event.stage_id && <span className="rounded bg-rf-panel px-2 py-0.5 text-zinc-400">{event.stage_id}</span>}
          {event.payload?.status && <span className="text-zinc-400">{String(event.payload.status)}</span>}
        </summary>
        <div className="space-y-2 border-t border-rf-border p-3"><div className="flex flex-wrap items-center justify-between gap-2 text-zinc-500">
          <span>{event.timestamp ? new Date(event.timestamp).toLocaleString() : '无时间戳'}</span><CopyButton text={JSON.stringify(event.payload, null, 2)} label="复制事件内容" /></div>
          <pre className="whitespace-pre-wrap break-all font-mono leading-6 text-zinc-300 select-text">{JSON.stringify(event.payload, null, 2)}</pre>
        </div>
      </details>)}
      {!filtered.length && !error && <p role="status" className="py-12 text-center text-zinc-500">{loading ? '正在加载事件…' : events.length ? '没有匹配的事件' : '暂无结构化事件'}</p>}
      {filtered.length > limit && <button className={`${duplexButtonClass} w-full`} onClick={() => setLimit(value => value + 50)}>再显示 50 条事件</button>}
    </div>
  </div>;
}
