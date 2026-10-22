import React, { useState } from 'react';
import { Code2, WrapText } from 'lucide-react';
import type { DuplexPlanItem } from '../types';
import { duplexPayloadText, parseDuplexPayload, type DuplexPayloadSource } from '../utils/duplexDraft';

export const duplexInputClass = 'w-full min-w-0 rounded-md border border-rf-border bg-rf-bg px-3 py-2 text-xs text-zinc-200 focus:outline-none focus:border-cyan-400 focus:ring-1 focus:ring-cyan-500/30 disabled:opacity-50';
export const duplexButtonClass = 'inline-flex items-center justify-center gap-1.5 rounded-md border border-rf-border px-3 py-2 text-xs text-zinc-300 transition-colors hover:border-cyan-700 hover:text-cyan-200 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-cyan-400 disabled:opacity-40 disabled:cursor-not-allowed';

export function DuplexPayloadEditor({ source, content, onSourceChange, onContentChange, label, disabled = false, expanded = false }: {
  source: DuplexPayloadSource; content: string; onSourceChange: (source: DuplexPayloadSource) => void;
  onContentChange: (content: string) => void; label: string; disabled?: boolean; expanded?: boolean;
}) {
  const [wrap, setWrap] = useState(true);
  const [error, setError] = useState('');
  return <div className="min-w-0 overflow-hidden rounded-lg border border-rf-border bg-rf-bg">
    <div className="flex flex-wrap items-center justify-between gap-2 border-b border-rf-border bg-rf-panel/30 px-3 py-2">
      <label className="flex items-center gap-2 text-zinc-400">
        <Code2 aria-hidden="true" className="h-3.5 w-3.5" />
        <select aria-label={`${label}输入格式`} value={source} disabled={disabled}
          onChange={event => { onSourceChange(event.target.value as DuplexPayloadSource); setError(''); }}
          className="min-w-0 rounded bg-transparent py-1 text-xs text-zinc-300 focus:outline-none focus:ring-1 focus:ring-cyan-400">
          <option value="message" className="bg-rf-surface">协议 JSON 报文</option>
          <option value="payload_hex" className="bg-rf-surface">原始物理载荷 Hex</option>
        </select>
      </label>
      <div className="flex items-center gap-1">
        <button type="button" className="rounded px-2 py-1 text-zinc-400 hover:bg-rf-panel hover:text-cyan-300 disabled:opacity-40"
          disabled={disabled || source !== 'message' || !content.trim()} onClick={() => {
            try { onContentChange(duplexPayloadText(parseDuplexPayload(source, content))); setError(''); }
            catch (failure) { setError((failure as Error).message); }
          }}>格式化</button>
        <button type="button" aria-label={`${label}自动换行`} aria-pressed={wrap} onClick={() => setWrap(value => !value)}
          className={`rounded p-1.5 ${wrap ? 'bg-cyan-500/10 text-cyan-300' : 'text-zinc-400'}`}>
          <WrapText aria-hidden="true" className="h-3.5 w-3.5" />
        </button>
      </div>
    </div>
    <textarea aria-label={label} value={content} disabled={disabled} rows={expanded ? 18 : 8} spellCheck={false}
      wrap={wrap ? 'soft' : 'off'} onChange={event => { onContentChange(event.target.value); setError(''); }}
      placeholder={source === 'message' ? '选择模板，或在此编辑协议 JSON 对象' : '输入 6..127 字节十六进制载荷'}
      className={`block w-full min-w-0 resize-y bg-transparent p-3 font-mono text-xs leading-6 text-cyan-100 select-text focus:outline-none focus:ring-1 focus:ring-inset focus:ring-cyan-500/40 disabled:opacity-60 ${expanded ? 'min-h-72' : 'min-h-40'}`} />
    {error && <p role="alert" className="border-t border-rf-border px-3 py-2 text-amber-400">{error}</p>}
  </div>;
}

export function DuplexItemPayloadEditor({ item, label, disabled, onSave, onDirtyChange }: {
  item: DuplexPlanItem; label: string; disabled: boolean;
  onSave: (payload: Pick<DuplexPlanItem, 'message' | 'payload_hex'>) => void; onDirtyChange: (dirty: boolean) => void;
}) {
  const originalSource = item.message ? 'message' : 'payload_hex';
  const originalContent = duplexPayloadText(item);
  const [source, setSource] = useState<DuplexPayloadSource>(originalSource);
  const [content, setContent] = useState(originalContent);
  const [error, setError] = useState('');
  const dirty = source !== originalSource || content !== originalContent;
  return <div className="space-y-2">
    <DuplexPayloadEditor label={`${label}报文`} source={source} content={content} disabled={disabled}
      onSourceChange={value => { setSource(value); setContent(''); setError(''); onDirtyChange(value !== originalSource || originalContent !== ''); }}
      onContentChange={value => { setContent(value); setError(''); onDirtyChange(source !== originalSource || value !== originalContent); }} />
    {dirty && <div className="flex flex-wrap items-center gap-2">
      <button className={`${duplexButtonClass} border-cyan-700 text-cyan-300`} disabled={disabled} onClick={() => {
        try {
          const payload = parseDuplexPayload(source, content);
          setContent(duplexPayloadText(payload)); onSave(payload); onDirtyChange(false); setError('');
        }
        catch (failure) { setError((failure as Error).message); }
      }}>保存报文修改</button>
      <button className={duplexButtonClass} disabled={disabled} onClick={() => {
        setSource(originalSource); setContent(originalContent); setError(''); onDirtyChange(false);
      }}>取消修改</button>
      <span className="text-amber-400">报文修改尚未保存</span>
    </div>}
    {error && <p role="alert" className="text-amber-400">{error}</p>}
  </div>;
}
