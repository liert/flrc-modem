import React, { useEffect, useRef, useState } from 'react';
import { useMutation, useQuery } from '@tanstack/react-query';
import { AlertCircle, ArrowRight, FileCode, Layers, RefreshCw, Search, X } from 'lucide-react';
import { api } from '../api/client';
import type { TemplatePayload } from '../types';

export function PayloadTemplateDialog({ sampleRate, onClose, onSelect }: {
  sampleRate: number;
  onClose: () => void;
  onSelect: (payload: TemplatePayload) => void;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const [search, setSearch] = useState('');
  const templates = useQuery({ queryKey: ['templates'], queryFn: () => api.getTemplates() });
  const selection = useMutation({ mutationFn: (name: string) => api.getTemplatePayload(name, sampleRate) });
  const payloadTemplates = (templates.data ?? []).filter(template => template.category === 'message_payload');
  const term = search.trim().toLowerCase();
  const filtered = payloadTemplates.filter(template =>
    [template.title, template.description, template.name, template.parsed.template?.protocol, template.parsed.command]
      .some(value => String(value ?? '').toLowerCase().includes(term)));

  useEffect(() => {
    const dialog = ref.current;
    const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    dialog?.showModal();
    dialog?.querySelector<HTMLInputElement>('input')?.focus();
    return () => {
      if (dialog?.open) dialog.close();
      if (opener?.isConnected) opener.focus({ preventScroll: true });
    };
  }, []);

  return <dialog
    ref={ref}
    aria-labelledby="payload-template-title"
    aria-describedby="payload-template-description"
    aria-modal="true"
    onCancel={event => { event.preventDefault(); onClose(); }}
    onClick={event => {
      if (event.target !== event.currentTarget) return;
      const rect = event.currentTarget.getBoundingClientRect();
      if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) onClose();
    }}
    className="fixed inset-0 m-auto max-h-[85dvh] w-[calc(100%-2rem)] max-w-4xl overflow-hidden rounded-2xl border border-rf-border bg-rf-bg p-0 text-rf-text shadow-2xl backdrop:bg-black/70"
  >
    <div className="flex max-h-[85dvh] flex-col">
      <header className="flex shrink-0 items-start justify-between gap-3 border-b border-rf-border bg-rf-surface p-4 md:px-6">
        <div className="flex items-start gap-3">
          <Layers className="mt-0.5 h-5 w-5 shrink-0 text-emerald-400" aria-hidden="true" />
          <div>
            <h2 id="payload-template-title" className="text-base font-semibold text-zinc-100">选择模板</h2>
            <p id="payload-template-description" className="mt-1 text-xs leading-5 text-zinc-400">
              从模板库选择协议载荷，点击卡片即可载入并更新物理帧剖析。同步字与调制参数沿用当前设置。
            </p>
          </div>
        </div>
        <button type="button" aria-label="关闭模板选择" onClick={onClose}
          className="shrink-0 rounded-lg p-2 text-zinc-400 hover:bg-zinc-800 hover:text-zinc-100 focus-visible:outline focus-visible:outline-2 focus-visible:outline-emerald-400">
          <X className="h-4 w-4" aria-hidden="true" />
        </button>
      </header>
      <div className="flex shrink-0 items-center gap-3 border-b border-rf-border px-4 py-3 md:px-6">
        <div className="relative min-w-0 flex-1">
          <Search className="pointer-events-none absolute left-3 top-2.5 h-4 w-4 text-zinc-500" aria-hidden="true" />
          <input type="search" aria-label="搜索协议载荷模板" placeholder="搜索模板名称、协议或指令…"
            value={search} onChange={event => setSearch(event.target.value)}
            className="h-9 w-full rounded-lg border border-rf-border bg-rf-surface pl-9 pr-3 text-xs text-zinc-200 focus:border-emerald-500 focus:outline-none" />
        </div>
        <button type="button" disabled={templates.isFetching || selection.isPending} onClick={() => void templates.refetch()}
          aria-label="刷新模板库" className="rounded-lg border border-rf-border p-2 text-zinc-400 hover:text-emerald-300 disabled:opacity-50">
          <RefreshCw className={`h-4 w-4 ${templates.isFetching ? 'animate-spin' : ''}`} aria-hidden="true" />
        </button>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto p-4 md:p-6" aria-busy={templates.isFetching || selection.isPending}>
        {templates.isPending ? <p role="status" className="py-12 text-center text-sm text-zinc-400">正在加载模板库…</p>
          : templates.isError ? <div role="alert" className="space-y-3 rounded-xl border border-rose-500/30 bg-rose-500/10 p-4 text-sm text-rose-300">
            <p>模板加载失败：{templates.error.message}</p>
            <button type="button" onClick={() => void templates.refetch()} className="underline">重新加载</button>
          </div>
          : filtered.length === 0 ? <p role="status" className="py-12 text-center text-sm text-zinc-400">
            {payloadTemplates.length === 0 ? '模板库中暂无协议载荷模板，请在模板库中添加报文模板。' : '未找到匹配的模板，请尝试其他关键词。'}
          </p>
          : <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            {filtered.map(template => {
              const protocol = template.parsed.template?.protocol ?? 'dk32s';
              const supported = protocol === 'dk32s' || protocol === 'datalink';
              const loading = selection.isPending && selection.variables === template.name;
              return <button key={template.name} type="button" disabled={!supported || selection.isPending}
                onClick={() => selection.mutate(template.name, { onSuccess: onSelect })}
                className="group flex h-full min-w-0 flex-col gap-3 rounded-xl border border-rf-border bg-rf-surface p-4 text-left transition-colors hover:border-emerald-500/60 hover:bg-emerald-500/5 focus-visible:outline focus-visible:outline-2 focus-visible:outline-emerald-400 disabled:cursor-not-allowed disabled:opacity-50">
                <span className="flex w-full items-center justify-between gap-2">
                  <span className="rounded-md border border-emerald-500/20 bg-emerald-500/10 px-2 py-0.5 text-[10px] font-medium uppercase text-emerald-300">{protocol}</span>
                  <span className="font-mono text-[11px] text-zinc-500">{String(template.parsed.command ?? '')}</span>
                </span>
                <span className="text-sm font-semibold leading-6 text-zinc-200 group-hover:text-emerald-200">{template.title}</span>
                <span className="flex-1 text-xs leading-5 text-zinc-400">{template.description}</span>
                <span className="flex w-full min-w-0 items-center gap-1.5 border-t border-rf-border pt-3 text-[10px] text-zinc-500">
                  <FileCode className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
                  <span className="min-w-0 flex-1 break-all font-mono">{template.name}</span>
                  <span className="flex shrink-0 items-center gap-1 text-emerald-400">
                    {loading ? '正在载入…' : supported ? '载入' : '暂不支持此协议'}
                    <ArrowRight className="h-3 w-3" aria-hidden="true" />
                  </span>
                </span>
              </button>;
            })}
          </div>}
      </div>
      <footer className="shrink-0 space-y-2 border-t border-rf-border bg-rf-surface px-4 py-3 md:px-6">
        {selection.isError && <p role="alert" className="flex items-start gap-2 text-xs text-rose-300">
          <AlertCircle className="h-4 w-4 shrink-0" aria-hidden="true" />载入失败：{selection.error.message}
        </p>}
        <p role="status" className="text-xs text-zinc-500">{selection.isPending ? '正在生成协议载荷…' : `模板库 · ${filtered.length} 个协议载荷模板`}</p>
      </footer>
    </div>
  </dialog>;
}
