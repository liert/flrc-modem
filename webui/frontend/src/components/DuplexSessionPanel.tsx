import React, { useId, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Radio, ChevronDown, ChevronUp, Maximize2, Minimize2, Send, Plus } from 'lucide-react';
import { api } from '../api/client';
import type { DuplexPlanDefinition, DuplexPlanItem, RunRecord } from '../types';
import { DuplexPlanPanel } from './DuplexPlanPanel';
import { DuplexPayloadEditor, duplexButtonClass, duplexInputClass } from './DuplexPayloadEditor';
import { DuplexResultsPanel } from './DuplexResultsPanel';
import { parseDuplexPayload, type DuplexPayloadSource } from '../utils/duplexDraft';

const syncLabels: Record<string, string> = {
  searching: '搜索同步', acquiring: '捕获中', locked: '已锁定', holdover: '同步保持', lost: '同步失锁',
};

export function DuplexSessionPanel({ run, initialPayloadHex = '', blockedReason, requireLock = false }: {
  run: RunRecord; initialPayloadHex?: string; blockedReason?: string; requireLock?: boolean;
}) {
  const client = useQueryClient();
  const regionId = useId();
  const active = run.status === 'RUNNING';
  const [source, setSource] = useState<DuplexPayloadSource>(initialPayloadHex ? 'payload_hex' : 'message');
  const [content, setContent] = useState(initialPayloadHex);
  const [waitReply, setWaitReply] = useState(initialPayloadHex ? false : !run.command_args.no_wait);
  const [invertSync, setInvertSync] = useState(!!run.command_args.tx_invert_sync);
  const [replyInvertSync, setReplyInvertSync] = useState(!!run.command_args.reply_invert_sync);
  const [accepted, setAccepted] = useState('');
  const [tab, setTab] = useState<'message' | 'plan' | 'results'>('message');
  const [collapsed, setCollapsed] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const [error, setError] = useState('');
  const [selectedItem, setSelectedItem] = useState('');
  const [draft, setDraft] = useState<DuplexPlanDefinition>({ plan_id: 'draft', periodic: [], steps: [], cycles: 1 });
  const state = useQuery({ queryKey: ['duplexSession', run.id, run.attempt], queryFn: () => api.getSession(run.id, run.attempt), refetchInterval: active ? 1000 : false });
  const templates = useQuery({ queryKey: ['templates'], queryFn: () => api.getTemplates(), staleTime: 60000 });
  const ready = active && !!state.data?.ready && !state.data?.status?.error && !blockedReason
    && (!requireLock || (!state.data?.status?.reacquiring && ['locked', 'holdover'].includes(state.data?.status?.state || '')));
  const send = useMutation({ mutationFn: () => api.sendMessage(run.id, {
    request_id: crypto.randomUUID(), ...parseDuplexPayload(source, content), wait_reply: waitReply,
    invert_sync: invertSync, reply_invert_sync: replyInvertSync,
  }), onSuccess: result => {
    setAccepted(result.request_id); void client.invalidateQueries({ queryKey: ['duplexSession', run.id] });
  } });
  const info = state.data?.status;
  const results = state.data?.results || [];
  const resolved = results.some(result => result.request_id === accepted);
  const planRunning = !!state.data?.plan && ['accepted', 'running', 'observing', 'stopping'].includes(state.data.plan.status);
  const templateOptions = templates.data?.filter(template => template.category === 'message_payload' && template.applicable_commands.includes('duplex')) || [];
  const add = (lane: 'periodic' | 'steps') => {
    try {
      if (blockedReason) throw new Error(blockedReason);
      const payload = parseDuplexPayload(source, content);
      if (draft[lane].length >= (lane === 'periodic' ? 8 : 64)) throw new Error(lane === 'periodic' ? '最多支持 8 个周期任务' : '最多支持 64 个流程步骤');
      const item: DuplexPlanItem = { id: crypto.randomUUID().slice(0, 8), ...payload, wait_reply: waitReply,
        invert_sync: invertSync, reply_invert_sync: replyInvertSync };
      setDraft(previous => lane === 'periodic' ? { ...previous, periodic: [...previous.periodic, { ...item, interval_ms: 500, count: 0 }] }
        : { ...previous, steps: [...previous.steps, { ...item, delay_ms: 0, on_failure: 'stop' }] });
      setSelectedItem(item.id); setTab('plan'); setError('');
    } catch (failure) { setError((failure as Error).message); }
  };
  const statusText = active ? info?.reacquiring ? '重新捕获' : syncLabels[info?.state || ''] || '启动中' : '会话已结束';
  return <section aria-label="常驻同步收发会话" className="shrink-0 border-b border-rf-border bg-rf-surface/70 text-xs">
    <div className="flex flex-wrap items-center justify-between gap-2 px-4 py-3">
      <div className="flex min-w-0 flex-wrap items-center gap-2.5">
        <Radio aria-hidden="true" className="h-4 w-4 text-cyan-400" /><h3 className="font-semibold text-zinc-200">同步会话</h3>
        <span className={`rounded-md border px-2 py-0.5 ${active && !info?.reacquiring && ['locked', 'holdover'].includes(info?.state || '') ? 'border-emerald-900 bg-emerald-500/10 text-emerald-400' : 'border-rf-border text-amber-400'}`}>{statusText}</span>
        {active && info?.receive_frequency_hz !== undefined && <span className="font-mono text-zinc-400">RX {(info.receive_frequency_hz / 1e6).toFixed(3)} MHz</span>}
        {(info?.reply_invert_sync ?? state.data?.config?.reply_invert_sync ?? !!run.command_args.reply_invert_sync) && <span className="text-amber-400">接收回复同步字已反转</span>}
        {planRunning && <button className="rounded px-2 py-1 text-cyan-300 hover:bg-cyan-500/10" onClick={() => { setCollapsed(false); setTab('plan'); }}>编排运行中 · 查看</button>}
      </div>
      <div className="flex items-center gap-1">
        {!collapsed && <button className={duplexButtonClass} aria-label={expanded ? '收缩会话工作区' : '放大会话工作区'} aria-pressed={expanded} onClick={() => setExpanded(value => !value)}>
          {expanded ? <Minimize2 aria-hidden="true" className="h-3.5 w-3.5" /> : <Maximize2 aria-hidden="true" className="h-3.5 w-3.5" />}{expanded ? '收缩' : '放大'}</button>}
        <button className={duplexButtonClass} aria-expanded={!collapsed} aria-controls={regionId} onClick={() => setCollapsed(value => !value)}>
          {collapsed ? <ChevronDown aria-hidden="true" className="h-3.5 w-3.5" /> : <ChevronUp aria-hidden="true" className="h-3.5 w-3.5" />}{collapsed ? '展开会话' : '收起会话'}</button>
      </div>
    </div>
    {(blockedReason || info?.error || info?.loss_reason || state.error || state.data?.plan?.error) && <p role="alert" className="px-4 pb-3 text-amber-400 break-all">{blockedReason || info?.error || info?.loss_reason || state.error?.message || state.data?.plan?.error}</p>}
    {initialPayloadHex && <div className="px-4 pb-3 flex flex-wrap items-center gap-2 text-zinc-400">
      <p>已带入调制工作台载荷。锁定后可投递或编排；等待自动匹配回复需要符合 DK32S 请求格式的载荷。</p>
      <button type="button" className={duplexButtonClass} disabled={planRunning || send.isPending}
        onClick={() => { setSource('payload_hex'); setContent(initialPayloadHex); setWaitReply(false); send.reset(); setError(''); setTab('message'); }}>
        重新载入当前物理帧载荷
      </button>
    </div>}
    <div id={regionId} hidden={collapsed}>
      <div className="flex flex-wrap items-center justify-between gap-2 border-y border-rf-border/70 bg-rf-bg/30 px-4 py-2">
        <nav aria-label="同步会话工作区" className="flex flex-wrap gap-1">
          {([{ id: 'message', label: '报文编辑' }, { id: 'plan', label: `发送编排${draft.periodic.length + draft.steps.length ? ` (${draft.periodic.length + draft.steps.length})` : ''}` }, { id: 'results', label: `交互记录 (${results.length})` }] as const).map(item =>
            <button key={item.id} aria-pressed={tab === item.id} aria-controls={`${regionId}-${item.id}`} onClick={() => setTab(item.id)}
              className={`rounded-md px-3 py-2 font-medium transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-cyan-400 ${tab === item.id ? 'bg-cyan-500/10 text-cyan-300' : 'text-zinc-400 hover:bg-rf-panel hover:text-zinc-200'}`}>{item.label}</button>)}
        </nav>
        <span className="font-mono text-zinc-500">{info?.slot !== undefined ? `Slot ${info.slot} · ` : ''}{info?.decoded_frames ?? 0} 帧</span>
      </div>
      <div className={`overflow-y-auto overscroll-contain p-3 sm:p-4 ${expanded ? 'max-h-[70vh]' : 'max-h-[44vh]'}`}>
        <div id={`${regionId}-message`} hidden={tab !== 'message'}>
          <div className="space-y-3">
            <label className="block space-y-1.5 text-zinc-400"><span>报文模板</span>
              <select aria-label="载入会话报文模板" value="" onChange={event => {
                const template = templateOptions.find(item => item.name === event.target.value);
                if (template) { setSource('message'); setContent(template.raw_content); send.reset(); setError(''); }
              }} className={duplexInputClass}>
                <option value="">选择模板载入编辑区…</option>{templateOptions.map(item => <option key={item.name} value={item.name}>{item.title}</option>)}
              </select>
            </label>
            <DuplexPayloadEditor source={source} content={content} label="待投递报文" expanded={expanded}
              onSourceChange={value => { setSource(value); setContent(''); send.reset(); setError(''); }}
              onContentChange={value => { setContent(value); send.reset(); setError(''); }} />
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div className="flex flex-wrap gap-x-4 gap-y-2">
                <label className="flex items-center gap-2 text-zinc-300"><input type="checkbox" checked={waitReply} onChange={event => setWaitReply(event.target.checked)} />等待匹配回复</label>
                <label className="flex items-center gap-2 text-zinc-300" title="本条发送的 4 字节同步字逐位取反，CRC 随之重新计算；加入编排时一并保存">
                  <input type="checkbox" checked={invertSync} onChange={event => setInvertSync(event.target.checked)} />反转发送同步字极性</label>
                <label className={`flex items-center gap-2 ${waitReply ? 'text-zinc-300' : 'text-zinc-500'}`} title="本条等待的回复使用逐位取反的同步字；地面帧仍用基准同步字锁定，解码同时接受原字与反字">
                  <input type="checkbox" checked={replyInvertSync} disabled={!waitReply}
                    onChange={event => setReplyInvertSync(event.target.checked)} />反转接收回复同步字极性</label>
              </div>
              <div className="flex flex-wrap gap-2">
                <button className={duplexButtonClass} disabled={planRunning || !content.trim()} onClick={() => add('periodic')}><Plus aria-hidden="true" className="h-3.5 w-3.5" />加入周期任务</button>
                <button className={duplexButtonClass} disabled={planRunning || !content.trim()} onClick={() => add('steps')}><Plus aria-hidden="true" className="h-3.5 w-3.5" />加入流程步骤</button>
                <button disabled={!ready || !content.trim() || send.isPending} onClick={() => send.mutate()} className={`${duplexButtonClass} border-cyan-600 bg-cyan-500/15 text-cyan-200 hover:bg-cyan-500/25`}>
                  <Send aria-hidden="true" className="h-3.5 w-3.5" />{send.isPending ? '正在提交…' : '投递一条报文'}</button>
              </div>
            </div>
            {(error || send.error) && <p role="alert" className="text-amber-400">{error || send.error?.message}</p>}
            {accepted && <p role="status" className="text-cyan-300">{resolved ? '本条投递已有执行结果，可在交互记录查看。' : active ? '报文已接收，等待执行确认。' : '会话已结束，请在交互记录核对执行结果。'}</p>}
          </div>
        </div>
        <div id={`${regionId}-plan`} hidden={tab !== 'plan'}><DuplexPlanPanel runId={run.id} ready={ready} session={state.data} draft={draft} setDraft={setDraft}
          selectedItem={selectedItem} onSelectItem={setSelectedItem} onCompose={() => setTab('message')} /></div>
        <div id={`${regionId}-results`} hidden={tab !== 'results'}><DuplexResultsPanel results={results} /></div>
        <details className="mt-4 rounded-lg border border-rf-border/70 px-3 py-2.5 text-zinc-500">
          <summary className="cursor-pointer text-zinc-400">设备与同步详情</summary>
          <div className="mt-3 space-y-2 leading-6 select-text">
            <p className="font-mono break-all">{state.data?.ready?.device_args || run.command_args.args || 'addr=192.168.10.2'}</p>
            <p>通道 {state.data?.ready?.channel ?? run.command_args.channel ?? 0} · 实际 RX 采样率 {(info?.sample_rate || state.data?.ready?.sample_rate)?.toLocaleString() || '待打开设备'} S/s · 重捕获 {info?.reacquisition_count ?? 0} 次</p>
            <p>当前接收同步字：<span className="font-mono text-zinc-300">{(info?.accepted_sync_words?.length ? info.accepted_sync_words.join(' / ') : '') || info?.sync_word || state.data?.config?.sync || '自动恢复'}</span> · 回复同步字：<span className="font-mono text-zinc-300">{info?.reply_sync_word || state.data?.config?.reply_sync_word || '与基准字相同'}</span>（回复极性{(info?.reply_invert_sync ?? state.data?.config?.reply_invert_sync ?? !!run.command_args.reply_invert_sync) ? '已反转' : '未反转'}）。解码同时接受原字与反字，回复极性按每条报文生效；会话默认值在命令参数中设置。</p>
            <p>失锁后自动返回观测信道重新捕获，锁定后恢复跳频。结束整个会话请使用任务头部的“终止任务”。</p>
          </div>
        </details>
      </div>
    </div>
  </section>;
}
