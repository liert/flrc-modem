import { hexText } from '../utils/hex';
import React, { useRef, useState } from 'react';
import { useMutation, useQuery } from '@tanstack/react-query';
import { Loader2, RefreshCw, Braces, Database, ArrowRight, CheckCircle2, Activity } from 'lucide-react';
import { api } from '../api/client';
import { useWorkstationStore } from '../stores/useWorkstationStore';
import { fileReadable } from '../utils/analysis';
import { runOption } from '../utils/runs';
import { ProtocolInspectionResult } from '../components/ProtocolInspectionResult';
import { duplexProtocolDraft } from '../utils/protocolSources';

const inputClass = 'pi-input';
const quickFrames = [
  { label: '地对空 · 时间戳帧', icon: 'TX', hex: '10 78 56 34 12 01 00 24 01 02 03 04 05 06 07 08 9A' },
  { label: '空对地 · 紧凑帧', icon: 'RX', hex: '08 02 00 24 01 48 02 7F 80 81 82 83 84 85 86 2C' },
  { label: '异常校验帧', icon: '!', hex: '08 78 56 34 12 01 00 24 01 02 03 04 05 06 07 FF' },
];
const topology = [
  ['0', 'SoftLen', '1B', 'cyan'], ['1..4', 'Timestamp', '4B', 'indigo'], ['5..6', 'Dir&Seq', '2B', 'amber'],
  ['7..N-2', 'Payload', 'N B', 'green'], ['N-1', 'Sum8', '1B', 'rose'],
];

export function ProtocolAnalysisView() {
  const draft = useWorkstationStore(state => state.protocolDraft);
  const setDraft = useWorkstationStore(state => state.setProtocolDraft);
  const [editorOpen, setEditorOpen] = useState(true);
  const [inputMode, setInputMode] = useState<'hex' | 'task'>(draft.origin ? 'task' : 'hex');
  const sourceTabs = useRef<HTMLDivElement>(null);
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
  const mutation = useMutation({
    mutationFn: (input: { protocol: string; payloadHex: string }) => api.decodeProtocol(input.protocol, input.payloadHex),
    onSuccess: () => {},
    onError: () => {}, 
  });
  const currentResult = mutation.variables?.protocol === draft.protocol && mutation.variables?.payloadHex === draft.payloadHex;
  const selectedProtocol = protocols.data?.find(item => item.id === draft.protocol);
  const result = currentResult ? mutation.data : undefined;
  const updateHex = (text: string) => setDraft({ protocol: draft.protocol, payloadHex: text, source: '手动输入' });
  const interactionPayloads = sessionRun ? (session.data?.results || []).flatMap(result =>
    (['TX', 'RX'] as const).flatMap(direction => {
      const input = duplexProtocolDraft(result, direction, sessionRun);
      return input ? [{ input, label: [direction, result.request_id, result.timestamp, result.status].filter(Boolean).join(' · ') }] : [];
    })) : [];

  const compactHex = draft.payloadHex.replace(/\s/g, '');
  const validHex = compactHex.length > 0 && compactHex.length % 2 === 0 && /^[0-9a-f]+$/i.test(compactHex);
  const byteCount = validHex ? compactHex.length / 2 : 0;
  const inputIssue = !compactHex ? '' : !/^[0-9a-f]+$/i.test(compactHex) ? '只允许 Hex 字符、空格和换行。'
    : compactHex.length % 2 ? '还缺半个字节：每字节需要两位 Hex。' : byteCount > 1024 ? '单次最多解析 1024 字节。' : '';
  const canParse = Boolean(selectedProtocol && validHex && !inputIssue && !mutation.isPending);
  const parse = () => { if (canParse) mutation.mutate({ protocol: draft.protocol, payloadHex: draft.payloadHex }); };
  const refreshSources = () => {
    void runs.refetch();
    if (sourceRunId && !listedRun) void runLookup.refetch();
    if (isDuplex) { void attempts.refetch(); if (sessionReadable) void session.refetch(); }
    else if (sourceRunId) { void context.refetch(); if (framesPath) void frames.refetch(); }
  };

  return <div className="pi-studio">
    <header className="pi-header">
      <div className="pi-heading"><div className="pi-mark"><Braces aria-hidden="true" /></div>
        <div className="pi-heading-copy"><div className="pi-eyebrow">FLRC / INSPECTOR</div><h1>协议解析</h1><p>把原始报文拆成可验证、可追踪的字段结构。</p></div>
      </div>
      <div className="pi-header-meta"><span className="pi-capability"><i />解析器在线</span><span className="pi-shortcut">⌘ K <span>快捷操作</span></span></div>
    </header>
    <form className="pi-composer pi-single-page" aria-label="报文解析准备区" onSubmit={event => { event.preventDefault(); parse(); }}
      onKeyDown={event => { if (event.nativeEvent.isComposing || event.keyCode === 229) return; if ((event.ctrlKey || event.metaKey) && event.key === 'Enter') { event.preventDefault(); parse(); } }}>
      <div className="pi-toolbar">
        <div className="pi-toolbar-label"><span className="pi-number">01</span><h2>准备报文</h2></div>
        {editorOpen && <div ref={sourceTabs} className="pi-tabs" role="tablist" aria-label="载荷来源" onKeyDown={event => {
          if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
          event.preventDefault();
          const next = event.key === 'Home' ? 'hex' : event.key === 'End' ? 'task' : inputMode === 'hex' ? 'task' : 'hex';
          setInputMode(next);
          sourceTabs.current?.querySelector<HTMLButtonElement>('#pi-tab-' + next)?.focus();
        }}>
          <button type="button" id="pi-tab-hex" role="tab" aria-selected={inputMode === 'hex'} aria-controls="pi-hex-panel"
            tabIndex={inputMode === 'hex' ? 0 : -1}
            onClick={() => setInputMode('hex')} className="pi-tab"><Braces aria-hidden="true" />Hex 输入</button>
          <button type="button" id="pi-tab-task" role="tab" aria-selected={inputMode === 'task'} aria-controls="pi-task-panel"
            tabIndex={inputMode === 'task' ? 0 : -1}
            onClick={() => setInputMode('task')} className="pi-tab"><Database aria-hidden="true" />任务记录</button>
        </div>}
        <div className="pi-quick-actions" aria-label="快速测试帧">
          {quickFrames.map(frame => <button key={frame.label} type="button" className="pi-quick-frame" onClick={() => updateHex(frame.hex)}><span>{frame.icon}</span>{frame.label}</button>)}
        </div>
        <button type="button" className="pi-collapse" onClick={() => setEditorOpen(open => !open)} aria-expanded={editorOpen}>{editorOpen ? '收起准备区' : '展开准备区'}<ArrowRight aria-hidden="true" /></button>
      </div>
      {!editorOpen && <div className="pi-source-line pi-source-collapsed"><span className="pi-source-tag">{selectedProtocol?.title || draft.protocol}</span>
        <span title={draft.source}>{byteCount} B · {draft.source}</span><button type="submit" disabled={!canParse} className="pi-button pi-button--quiet">重新解析<ArrowRight aria-hidden="true" /></button></div>}
      <div id="pi-preparation" className="pi-preparation" hidden={!editorOpen}>
        <div className="pi-input-top">
          <div><label htmlFor="protocol-select" className="pi-label">解析协议</label>
            <select id="protocol-select" aria-label="业务协议" className={inputClass} value={draft.protocol} disabled={!protocols.data?.length}
              onChange={event => setDraft({ ...draft, protocol: event.target.value })}>
              {protocols.data?.length ? protocols.data.map(item => <option key={item.id} value={item.id}>{item.title}</option>) : <option value={draft.protocol}>{protocols.isError ? '协议列表加载失败' : '正在加载协议…'}</option>}
            </select></div>
          <div className="pi-protocol-note"><span>协议说明</span><p>{selectedProtocol?.description || '选择业务协议，输入完整物理载荷，即可查看封装结构与业务字段。'}</p></div>
        </div>
        <div className="pi-topology" aria-label="协议帧结构拓扑"><div className="pi-topology-label"><Activity aria-hidden="true" />FRAME TOPOLOGY</div><div className="pi-topology-chain">{topology.map(([offset, name, size, tone], index) => <React.Fragment key={name}><div className={'pi-topology-chip pi-topology-chip--' + tone}><small>[{offset}]</small><strong>{name}</strong><span>{size}</span></div>{index < topology.length - 1 && <span className="pi-topology-arrow">→</span>}</React.Fragment>)}</div></div>
        {protocols.error && <div role="alert" className="pi-error">{protocols.error.message} <button type="button" onClick={() => void protocols.refetch()} className="underline">重新加载</button></div>}
        <div id="pi-hex-panel" role="tabpanel" aria-labelledby="pi-tab-hex" hidden={inputMode !== 'hex'}>
          <label htmlFor="protocol-payload" className="sr-only">完整物理载荷 Hex（包括软件长度和业务 sum8，不含同步字、硬件头和物理 CRC）</label>
          <div className="pi-editor" data-invalid={Boolean(inputIssue)}><div className="pi-editor-caption"><span><Braces aria-hidden="true" />PAYLOAD / <b>HEX</b></span><span className="pi-editor-status"><CheckCircle2 aria-hidden="true" />SUM8 待校验 <em aria-live="polite">{byteCount} B</em></span></div>
            <div className="pi-editor-grid"><div className="pi-offsets" aria-hidden="true">{Array.from({ length: Math.max(2, Math.ceil(byteCount / 8)) }, (_, index) => <span key={index}>{(index * 8).toString(16).padStart(4, '0').toUpperCase()}</span>)}</div><textarea id="protocol-payload" rows={4} maxLength={8192} spellCheck={false} value={hexText(draft.payloadHex)} onChange={event => updateHex(event.target.value)}
              placeholder="粘贴完整物理载荷 Hex，支持空格和换行…" aria-describedby="protocol-input-help" aria-invalid={Boolean(inputIssue)} /><div className="pi-byte-preview" aria-label="语义着色字节矩阵">{(compactHex.match(/../g) || []).slice(0, 64).map((byte, index) => <span key={index} data-tone={index === 0 ? 'cyan' : index < 5 ? 'indigo' : index < 7 ? 'amber' : index === byteCount - 1 ? 'rose' : 'green'} title={'偏移 ' + index}>{byte}</span>)}</div></div>
          </div>
          <p id="protocol-input-help" className="pi-help">保留软件长度、时间戳与 sum8；不含同步字、硬件头和物理 CRC。每字节两位，最多 1024 B。</p>
        </div>
        <section id="pi-task-panel" role="tabpanel" aria-labelledby="pi-tab-task" aria-label="从任务载入报文" hidden={inputMode !== 'task'} className="pi-task-source">
          <div className="pi-section-title" style={{ padding: '6px 0 14px' }}><h2 className="text-xs text-slate-300">从任务载入报文</h2>
            <button type="button" onClick={refreshSources} className="pi-button pi-button--quiet" aria-label="刷新任务报文"><RefreshCw aria-hidden="true" />刷新记录</button></div>
          <div className="pi-source-controls">
            <div><label htmlFor="protocol-source-run" className="pi-label">最近任务（最多 100 个）</label>
              <select id="protocol-source-run" value={sourceRunId} onChange={event => { setSourceRunId(event.target.value); setSourceAttempt(undefined); }} className={inputClass}>
                <option value="">选择解调任务或同步收发任务</option>
                {sourceRunId && !listedRun && <option value={sourceRunId}>{sourceRun ? runOption(sourceRun) : sourceRunId}</option>}
                {runs.data?.map(run => <option key={run.id} value={run.id}>{runOption(run)}</option>)}
              </select></div>
            {isDuplex && <div><label htmlFor="protocol-source-attempt" className="pi-label">同步收发执行轮次</label>
              <select id="protocol-source-attempt" value={attempt} onChange={event => setSourceAttempt(Number(event.target.value))} className={inputClass}>
                {!attempts.data?.some(run => run.attempt === attempt) && <option value={attempt}>第 {attempt} 次执行</option>}
                {attempts.data?.map(run => <option key={run.attempt} value={run.attempt}>第 {run.attempt} 次执行 · {run.status}</option>)}
              </select></div>}
            {isDuplex && sessionReadable && <div className="pi-source-wide"><label htmlFor="protocol-source-interaction" className="pi-label">同步收发交互报文（最近 50 条交互）</label>
              <select id="protocol-source-interaction" value="" disabled={!interactionPayloads.length} className={inputClass}
                onChange={event => { if (event.target.value !== '') { const item = interactionPayloads[Number(event.target.value)]; if (item) setDraft(item.input); } }}>
                <option value="">{session.isPending ? '正在读取交互记录…' : '选择 TX 或 RX 载入'}</option>
                {interactionPayloads.map((item, index) => <option key={index} value={index}>{item.label}</option>)}
              </select></div>}
            {!isDuplex && source && framesPath && fileReadable(source.file_states?.frames) && <div><label htmlFor="protocol-source-frame" className="pi-label">解调帧（本次最多读取 500 帧）</label>
              <select id="protocol-source-frame" className={inputClass} value="" disabled={!frames.data?.frames.length}
                onChange={event => {
                  if (event.target.value === '') return;
                  const frame = frames.data?.frames[Number(event.target.value)];
                  if (!frame || typeof frame.payload_hex !== 'string') return;
                  const protocol = frame.application?.rc_mode === 'multi' ? 'dk32s-multi' : frame.application?.protocol;
                  setDraft({ payloadHex: frame.payload_hex, protocol: protocols.data?.some(item => item.id === protocol) ? protocol : draft.protocol,
                    source: sourceRunId + ' · 第 ' + (Number(event.target.value) + 1) + ' 帧' });
                }}>
                <option value="">{frames.isPending ? '正在读取报文…' : '选择一帧载入'}</option>
                {frames.data?.frames.map((frame, index) => <option key={index} value={index}>{index + 1} · {Number.isFinite(frame.time_s) ? frame.time_s.toFixed(4) + ' s' : '未知时间'} · {frame.application?.command_name || hexText(frame.payload_hex).slice(0, 28) || '无载荷'}</option>)}
              </select></div>}
          </div>
          {runs.error && <p role="alert">{runs.error.message}</p>}
          {sourceRunId && !listedRun && runLookup.error && <p role="alert">{runLookup.error.message}</p>}
          {sourceRunId && !listedRun && runLookup.isPending && <p role="status">正在读取任务…</p>}
          {isDuplex && attempts.error && <p role="alert">{attempts.error.message}</p>}
          {isDuplex && !sessionRun && attempts.isPending && <p role="status">正在读取执行记录…</p>}
          {isDuplex && sessionRun && !sessionReadable && <p>该轮不是常驻同步会话，没有可读取的会话交互记录；可手动输入 Hex。</p>}
          {sessionReadable && session.error && <p role="alert">{session.error.message}</p>}
          {sessionReadable && session.isSuccess && !interactionPayloads.length && <p>此轮没有已保存的 TX/RX 载荷；超时记录不会生成虚构 RX。</p>}
          {isDuplex && <p>交互记录来自任务数据库，无需 JSONL。运行中可点击刷新读取新记录。</p>}
          {sourceRun && !isDuplex && context.isPending && <p role="status">正在读取任务…</p>}
          {!isDuplex && sourceRunId && context.error && <p role="alert">{context.error.message}</p>}
          {!isDuplex && source && (!framesPath || !fileReadable(source.file_states?.frames)) && <p>{source.file_states?.frames?.message || '此任务没有可读取的解调 JSONL；仍可手动输入 Hex。'}</p>}
          {!isDuplex && framesPath && frames.error && <p role="alert">{frames.error.message}</p>}
          {!isDuplex && framesPath && frames.isSuccess && !frames.data.frames.length && <p>尚无解调帧，请刷新或手动输入。</p>}
          {!sourceRunId && <p>载入解调帧或同步收发的 TX / RX，随后使用同一解析器查看业务内容。</p>}
        </section>
        {inputIssue && <p role="alert" className="pi-error">{inputIssue}</p>}
        {mutation.isError && currentResult && <p role="alert" className="pi-error">{mutation.error.message}</p>}
        <div className="pi-composer-foot"><div className="pi-source-line"><span className="pi-source-tag">输入来源</span><span title={draft.source}>{draft.source}</span></div>
          <div className="pi-actions"><span className="hidden sm:inline text-[10px] text-slate-500">Ctrl / ⌘ + Enter</span>
            <button type="button" onClick={() => { updateHex(''); mutation.reset(); setInputMode('hex'); }} className="pi-button pi-button--quiet">清空</button>
            <button type="submit" disabled={!canParse} className="pi-button pi-button--primary">
              {mutation.isPending ? <Loader2 className="animate-spin" aria-hidden="true" /> : <ArrowRight aria-hidden="true" />}{mutation.isPending ? '正在解析…' : '解析报文'}</button>
          </div></div>
      </div>
    </form>

    {result ? <ProtocolInspectionResult key={result.protocol + ':' + result.payload_hex} result={result} /> :
      <section className="pi-awaiting" aria-label="解析结果引导">
        <div className="pi-awaiting-title"><span className="pi-number">02</span><h2>解析结果</h2><span>等待报文</span></div>
        <div className="pi-awaiting-body"><div className="pi-awaiting-icon"><Braces aria-hidden="true" /></div>
          <div><h3>输入一段 Hex，展开报文全貌</h3><p>解析后可查看业务字段、封装结构与原始字节，点击字段即可双向定位。</p></div>
        </div>
        <div className="pi-awaiting-features"><span>封装与 sum8 校验</span><span>字段 / 字节联动</span><span>JSON 复制与导出</span></div>
      </section>}

  </div>;
}
