import React, { useState } from 'react';
import { useMutation, useQuery } from '@tanstack/react-query';
import { Binary, Loader2, RefreshCw } from 'lucide-react';
import { api } from '../api/client';
import { useWorkstationStore } from '../stores/useWorkstationStore';
import { fileReadable } from '../utils/analysis';
import { runOption } from '../utils/runs';
import { ProtocolInspectionResult } from '../components/ProtocolInspectionResult';
import { duplexProtocolDraft } from '../utils/protocolSources';

const inputClass = 'w-full rounded-lg border border-rf-border bg-rf-bg p-2 text-sm text-rf-text focus:outline-none focus:border-cyan-400 disabled:opacity-50';

export function ProtocolAnalysisView() {
  const draft = useWorkstationStore(state => state.protocolDraft);
  const setDraft = useWorkstationStore(state => state.setProtocolDraft);
  const [sourceRunId, setSourceRunId] = useState(draft.origin?.runId || '');
  const [sourceAttempt, setSourceAttempt] = useState<number | undefined>(draft.origin?.attempt);
  const protocols = useQuery({ queryKey: ['protocols'], queryFn: api.getProtocols, staleTime: Infinity });
  const runs = useQuery({ queryKey: ['runs', 100, 0], queryFn: () => api.getRuns(100, 0) });
  const listedRun = runs.data?.find(run => run.id === sourceRunId);
  const runLookup = useQuery({ queryKey: ['run', sourceRunId], queryFn: () => api.getRun(sourceRunId), enabled: Boolean(sourceRunId && !listedRun) });
  const sourceRun = listedRun || runLookup.data;
  const isDuplex = sourceRun?.command_id === 'duplex';
  const attempts = useQuery({ queryKey: ['runAttempts', sourceRunId], queryFn: () => api.getRunAttempts(sourceRunId), enabled: isDuplex });
  const attempt = sourceAttempt ?? sourceRun?.attempt ?? 1;
  const sessionRun = sourceRun && (sourceRun.attempt ?? 1) === attempt ? sourceRun : attempts.data?.find(run => run.attempt === attempt);
  const sessionReadable = Boolean(isDuplex && sessionRun?.command_args.session);
  const session = useQuery({
    queryKey: ['duplexSession', sourceRunId, attempt], queryFn: () => api.getSession(sourceRunId, attempt),
    enabled: sessionReadable,
  });
  const context = useQuery({ queryKey: ['analysisContext', sourceRunId], queryFn: () => api.getRunAnalysis(sourceRunId), enabled: Boolean(sourceRun && !isDuplex) });
  const source = context.data?.run_id === sourceRunId ? context.data : undefined;
  const framesPath = source?.frames_path || '';
  const frames = useQuery({
    queryKey: ['frames', sourceRunId, framesPath, source?.file_states?.frames?.revision],
    queryFn: () => api.getDecodedFrames(framesPath, 500, sourceRunId),
    enabled: Boolean(sourceRunId && framesPath && fileReadable(source?.file_states?.frames)),
  });
  const mutation = useMutation({ mutationFn: (input: { protocol: string; payloadHex: string }) => api.decodeProtocol(input.protocol, input.payloadHex) });
  const currentResult = mutation.variables?.protocol === draft.protocol && mutation.variables?.payloadHex === draft.payloadHex;
  const selectedProtocol = protocols.data?.find(item => item.id === draft.protocol);
  const result = currentResult ? mutation.data : undefined;
  const updateHex = (text: string) => setDraft({ protocol: draft.protocol, payloadHex: text, source: '手动输入' });
  const interactionPayloads = sessionRun ? (session.data?.results || []).flatMap(result =>
    (['TX', 'RX'] as const).flatMap(direction => {
      const input = duplexProtocolDraft(result, direction, sessionRun);
      return input ? [{ input, label: [direction, result.request_id, result.timestamp, result.status].filter(Boolean).join(' · ') }] : [];
    })) : [];

  return <div className="p-4 sm:p-6 space-y-6 max-w-[1600px] mx-auto">
    <div className="flex items-start gap-3">
      <Binary className="w-7 h-7 text-cyan-400 shrink-0" aria-hidden="true" />
      <div><h1 className="text-xl font-bold">协议解析</h1><p className="mt-1 text-sm text-zinc-400">粘贴物理载荷 Hex 或载入任务报文，使用与 CLI 相同的协议解析器查看业务内容。</p></div>
    </div>
    <div className="grid grid-cols-1 lg:grid-cols-3 gap-4 items-start">
      <form onSubmit={event => { event.preventDefault(); mutation.mutate({ protocol: draft.protocol, payloadHex: draft.payloadHex }); }} className="lg:col-span-2 rounded-xl border border-rf-border bg-rf-surface p-4 space-y-3">
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          <div><label htmlFor="protocol-select" className="block mb-1 text-xs text-zinc-400">业务协议</label>
            <select id="protocol-select" className={inputClass} value={draft.protocol} disabled={!protocols.data?.length}
              onChange={event => setDraft({ ...draft, protocol: event.target.value })}>
              {protocols.data?.length ? protocols.data.map(item => <option key={item.id} value={item.id}>{item.title}</option>) : <option value={draft.protocol}>{protocols.isError ? '协议列表加载失败' : '正在加载协议…'}</option>}
            </select>
          </div>
          <div className="text-xs text-zinc-400 flex items-end pb-2">{selectedProtocol?.description}</div>
        </div>
        {protocols.error && <div role="alert" className="text-sm text-red-300">{protocols.error.message} <button type="button" onClick={() => void protocols.refetch()} className="underline">重新加载</button></div>}
        <div><label htmlFor="protocol-payload" className="block mb-1 text-xs text-zinc-400">完整物理载荷 Hex（包括软件长度和业务 sum8，不含同步字、硬件头和物理 CRC）</label>
          <textarea id="protocol-payload" rows={5} maxLength={8192} spellCheck={false} value={draft.payloadHex}
            onChange={event => updateHex(event.target.value)} className={inputClass + ' font-mono resize-y break-all'}
            placeholder="粘贴 payload_hex，支持连续 Hex、空格和换行" aria-describedby="protocol-input-help" />
          <p id="protocol-input-help" className="text-xs text-zinc-500 mt-1">每字节两位，最多 1024 字节。输入内容保留在本次页面会话中；修改后需重新解析。</p>
        </div>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <span className="text-xs text-zinc-400 break-all">输入来源：{draft.source}</span>
          <div className="flex gap-2">
            <button type="button" onClick={() => { updateHex(''); mutation.reset(); }} className="px-3 py-2 rounded-lg text-xs text-zinc-400 hover:bg-rf-panel">清空</button>
            <button type="submit" disabled={!selectedProtocol || !draft.payloadHex.trim() || mutation.isPending} className="px-4 py-2 rounded-lg bg-cyan-500 text-black font-semibold text-sm flex items-center gap-2 disabled:opacity-50 hover:bg-cyan-400">
              {mutation.isPending && <Loader2 className="w-4 h-4 animate-spin" aria-hidden="true" />}{mutation.isPending ? '正在解析…' : '解析报文'}
            </button>
          </div>
        </div>
        {mutation.isError && currentResult && <p role="alert" className="text-red-300 text-sm">{mutation.error.message}</p>}
      </form>
      <section aria-label="从任务载入报文" className="rounded-xl border border-rf-border bg-rf-surface p-4 space-y-3">
        <div className="flex justify-between items-center gap-2"><h2 className="font-semibold text-sm">从任务载入报文</h2><button type="button" onClick={() => {
          void runs.refetch();
          if (sourceRunId && !listedRun) void runLookup.refetch();
          if (isDuplex) { void attempts.refetch(); if (sessionReadable) void session.refetch(); }
          else if (sourceRunId) { void context.refetch(); if (framesPath) void frames.refetch(); }
        }} className="p-1 text-zinc-400 hover:text-cyan-300" aria-label="刷新任务报文"><RefreshCw className="w-4 h-4" /></button></div>
        <label htmlFor="protocol-source-run" className="block text-xs text-zinc-400">最近任务（最多 100 个）</label>
        <select id="protocol-source-run" value={sourceRunId} onChange={event => { setSourceRunId(event.target.value); setSourceAttempt(undefined); }} className={inputClass}>
          <option value="">选择解调任务或同步收发任务</option>
          {sourceRunId && !listedRun && <option value={sourceRunId}>{sourceRun ? runOption(sourceRun) : sourceRunId}</option>}
          {runs.data?.map(run => <option key={run.id} value={run.id}>{runOption(run)}</option>)}
        </select>
        {runs.error && <p role="alert" className="text-red-300 text-xs">{runs.error.message}</p>}
        {sourceRunId && !listedRun && runLookup.isPending && <p role="status" className="text-zinc-400 text-xs">正在读取任务…</p>}
        {sourceRunId && !listedRun && runLookup.error && <p role="alert" className="text-red-300 text-xs">{runLookup.error.message}</p>}
        {isDuplex && <>
          <label htmlFor="protocol-source-attempt" className="block text-xs text-zinc-400">同步收发执行轮次</label>
          <select id="protocol-source-attempt" value={attempt} onChange={event => setSourceAttempt(Number(event.target.value))} className={inputClass}>
            {!attempts.data?.some(run => run.attempt === attempt) && <option value={attempt}>第 {attempt} 次执行</option>}
            {attempts.data?.map(run => <option key={run.attempt} value={run.attempt}>第 {run.attempt} 次执行 · {run.status}</option>)}
          </select>
          {attempts.error && <p role="alert" className="text-red-300 text-xs">{attempts.error.message}</p>}
          {!sessionRun && attempts.isPending && <p role="status" className="text-zinc-400 text-xs">正在读取执行记录…</p>}
          {sessionRun && !sessionReadable && <p className="text-amber-300 text-xs">该轮不是常驻同步会话，没有可读取的会话交互记录；可手动输入 Hex。</p>}
          {sessionReadable && <>
            <label htmlFor="protocol-source-interaction" className="block text-xs text-zinc-400">同步收发交互报文（最近 50 条交互）</label>
            <select id="protocol-source-interaction" value="" disabled={!interactionPayloads.length} className={inputClass}
              onChange={event => { if (event.target.value !== '') { const item = interactionPayloads[Number(event.target.value)]; if (item) setDraft(item.input); } }}>
              <option value="">{session.isPending ? '正在读取交互记录…' : '选择 TX 或 RX 载入输入框'}</option>
              {interactionPayloads.map((item, index) => <option key={index} value={index}>{item.label}</option>)}
            </select>
            {session.error && <p role="alert" className="text-red-300 text-xs">{session.error.message}</p>}
            {session.isSuccess && !interactionPayloads.length && <p className="text-zinc-400 text-xs">此轮没有已保存的 TX/RX 载荷；超时记录不会生成虚构 RX。</p>}
          </>}
          <p className="text-xs text-zinc-500">交互记录来自任务数据库，无需 JSONL。运行中可点击刷新读取新记录。</p>
        </>}
        {sourceRun && !isDuplex && context.isPending && <p role="status" className="text-zinc-400 text-xs">正在读取任务…</p>}
        {!isDuplex && sourceRunId && context.error && <p role="alert" className="text-red-300 text-xs">{context.error.message}</p>}
        {!isDuplex && source && (!framesPath || !fileReadable(source.file_states?.frames)) && <p className="text-amber-300 text-xs">{source.file_states?.frames?.message || '此任务没有可读取的解调 JSONL；仍可手动输入 Hex。'}</p>}
        {!isDuplex && source && framesPath && fileReadable(source.file_states?.frames) && <>
          <label htmlFor="protocol-source-frame" className="block text-xs text-zinc-400">解调帧（本次最多读取 500 帧）</label>
          <select id="protocol-source-frame" className={inputClass} value="" disabled={!frames.data?.frames.length}
            onChange={event => {
              if (event.target.value === '') return;
              const frame = frames.data?.frames[Number(event.target.value)];
              if (!frame || typeof frame.payload_hex !== 'string') return;
              const protocol = frame.application?.rc_mode === 'multi' ? 'dk32s-multi' : frame.application?.protocol;
              setDraft({ payloadHex: frame.payload_hex, protocol: protocols.data?.some(item => item.id === protocol) ? protocol : draft.protocol,
                source: sourceRunId + ' · 第 ' + (Number(event.target.value) + 1) + ' 帧' });
            }}>
            <option value="">{frames.isPending ? '正在读取报文…' : '选择一帧载入输入框'}</option>
            {frames.data?.frames.map((frame, index) => <option key={index} value={index}>{index + 1} · {Number.isFinite(frame.time_s) ? frame.time_s.toFixed(4) + ' s' : '未知时间'} · {frame.application?.command_name || frame.payload_hex?.slice(0, 28) || '无载荷'}</option>)}
          </select>
          {frames.error && <p role="alert" className="text-red-300 text-xs">{frames.error.message}</p>}
          {frames.isSuccess && !frames.data.frames.length && <p className="text-zinc-400 text-xs">尚无解调帧，请刷新或手动输入。</p>}
        </>}
        <p className="text-xs text-zinc-500">载入后可以重新选择协议，不需要重新解调 IQ。</p>
      </section>
    </div>
    <p className="rounded-lg border border-rf-border bg-rf-panel/40 p-3 text-xs text-zinc-400">本页面仅检查业务封装、长度和 sum8；物理 CRC 未在此验证。方向依据已知固件布局推断，不代表发射设备身份。未确认字段保留原值。</p>
    {result ? <ProtocolInspectionResult key={result.protocol + ':' + result.payload_hex} result={result} /> : <div className="p-10 rounded-xl border border-dashed border-rf-border text-center text-zinc-500 text-sm">{mutation.isPending ? '正在解析当前输入…' : '载入或粘贴载荷后，点击“解析报文”查看字段与字节。'}</div>}
  </div>;
}
