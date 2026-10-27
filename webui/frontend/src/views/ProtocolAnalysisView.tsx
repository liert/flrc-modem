import { hexText } from '../utils/hex';
import React, { useEffect, useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import {
  ArrowRight,
  Braces,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  ChevronUp,
  Database,
  Layers,
  Loader2,
  RefreshCw,
  Sparkles,
  X,
} from 'lucide-react';
import { api } from '../api/client';
import { useWorkstationStore } from '../stores/useWorkstationStore';
import { fileReadable } from '../utils/analysis';
import { runOption } from '../utils/runs';
import { ProtocolInspectionResult } from '../components/ProtocolInspectionResult';
import { PayloadTemplateDialog } from '../components/PayloadTemplateDialog';
import { CopyButton } from '../components/CopyButton';
import { duplexProtocolDraft } from '../utils/protocolSources';

const inputClass = 'pi-input';

const PROTOCOL_SAMPLES = [
  {
    id: 'dk32s-rc',
    label: 'DK32S 0x11 遥控通道',
    protocol: 'dk32s',
    payloadHex: '20e0d50e00173e3c11db431ffaec873ef4a10f7de8431f7de8431ffad0873efc47',
    source: '内置示例 · DK32S 0x11 遥控通道帧',
  },
  {
    id: 'dk32s-status',
    label: 'DK32S 0x13 状态回传',
    protocol: 'dk32s',
    payloadHex: '212a72040098f00613e40a2b3fc2bf8d212d4f9c8102e157af66ce3c34126e90e846',
    source: '内置示例 · DK32S 0x13 状态回传帧',
  },
  {
    id: 'datalink-baud',
    label: 'DataLink 0xF7 波特率',
    protocol: 'datalink',
    payloadHex: '06010100f705fe',
    source: '内置示例 · DataLink 0xF7 波特率配置帧',
  },
] as const;

export function ProtocolAnalysisView() {
  const draft = useWorkstationStore(state => state.protocolDraft);
  const setDraft = useWorkstationStore(state => state.setProtocolDraft);
  const [editorOpen, setEditorOpen] = useState(true);
  const [taskDrawerOpen, setTaskDrawerOpen] = useState(false);
  const [templateDialogOpen, setTemplateDialogOpen] = useState(false);
  const drawerRef = useRef<HTMLElement>(null);
  const [sourceRunId, setSourceRunId] = useState(draft.origin?.runId || '');
  const [sourceAttempt, setSourceAttempt] = useState<number | undefined>(draft.origin?.attempt);
  const [recordFilter, setRecordFilter] = useState('');
  const [selectedInteractionIndex, setSelectedInteractionIndex] = useState<number | null>(null);
  const [selectedFrameIndex, setSelectedFrameIndex] = useState<number | null>(null);

  const lastOriginRef = useRef(draft.origin);
  useEffect(() => {
    const prev = lastOriginRef.current;
    const next = draft.origin;
    if (next && (next.runId !== prev?.runId || next.attempt !== prev?.attempt)) {
      setPendingTyping(false);
      setSourceRunId(next.runId);
      setSourceAttempt(next.attempt);
      setRecordFilter('');
    }
    lastOriginRef.current = next;
  }, [draft.origin]);

  useEffect(() => {
    if (!taskDrawerOpen || typeof window === 'undefined') return;
    const drawerEl = drawerRef.current;
    const opener = typeof document !== 'undefined' && document.activeElement instanceof HTMLElement
      ? document.activeElement
      : null;
    drawerEl?.querySelector<HTMLElement>('[data-drawer-close]')?.focus?.({ preventScroll: true });
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault();
        setTaskDrawerOpen(false);
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => {
      window.removeEventListener('keydown', handleKeyDown);
      if (typeof document !== 'undefined' && drawerEl?.contains(document.activeElement) && opener?.isConnected) {
        opener.focus?.({ preventScroll: true });
      }
    };
  }, [taskDrawerOpen]);

  const protocols = useQuery({ queryKey: ['protocols'], queryFn: api.getProtocols, staleTime: Infinity });
  const runs = useQuery({
    queryKey: ['runs', 100, 0],
    queryFn: () => api.getRuns(100, 0),
    enabled: taskDrawerOpen || Boolean(sourceRunId),
  });
  const listedRun = runs.data?.find(run => run.id === sourceRunId);
  const runLookup = useQuery({
    queryKey: ['run', sourceRunId],
    queryFn: () => api.getRun(sourceRunId),
    enabled: Boolean(sourceRunId && !listedRun),
  });
  const sourceRun = listedRun || runLookup.data;
  const isDuplex = sourceRun?.command_id === 'duplex';
  const attempts = useQuery({
    queryKey: ['runAttempts', sourceRunId],
    queryFn: () => api.getRunAttempts(sourceRunId),
    enabled: isDuplex,
  });
  const attempt = sourceAttempt ?? sourceRun?.attempt ?? 1;
  const sessionRun = sourceRun && (sourceRun.attempt ?? 1) === attempt
    ? sourceRun
    : attempts.data?.find(run => run.attempt === attempt);
  const sessionReadable = Boolean(isDuplex && sessionRun?.command_args.session);
  const session = useQuery({
    queryKey: ['duplexSession', sourceRunId, attempt],
    queryFn: () => api.getSession(sourceRunId, attempt),
    enabled: sessionReadable,
  });
  const context = useQuery({
    queryKey: ['analysisContext', sourceRunId],
    queryFn: () => api.getRunAnalysis(sourceRunId),
    enabled: Boolean(sourceRun && !isDuplex),
  });
  const source = context.data?.run_id === sourceRunId ? context.data : undefined;
  const framesPath = source?.frames_path || '';
  const frames = useQuery({
    queryKey: ['frames', sourceRunId, framesPath, source?.file_states?.frames?.revision],
    queryFn: () => api.getDecodedFrames(framesPath, 500, sourceRunId),
    enabled: Boolean(sourceRunId && framesPath && fileReadable(source?.file_states?.frames)),
  });

  const compactHex = draft.payloadHex.replace(/\s/g, '');
  const normalizedHex = compactHex.toLowerCase();
  const validHex = compactHex.length > 0 && compactHex.length % 2 === 0 && /^[0-9a-f]+$/i.test(compactHex);
  const byteCount = validHex ? compactHex.length / 2 : 0;
  const inputIssue = !compactHex ? '' : !/^[0-9a-f]+$/i.test(compactHex) ? '只允许 Hex 字符、空格和换行。'
    : compactHex.length % 2 ? '还缺半个字节：每字节需要两位 Hex。' : byteCount > 1024 ? '单次最多解析 1024 字节。' : '';

  const [pendingTyping, setPendingTyping] = useState(false);
  const [debouncedTypingHex, setDebouncedTypingHex] = useState(normalizedHex);

  useEffect(() => {
    if (!pendingTyping || !validHex || inputIssue) return;
    const timer = setTimeout(() => {
      setDebouncedTypingHex(normalizedHex);
      setPendingTyping(false);
    }, 220);
    return () => clearTimeout(timer);
  }, [pendingTyping, normalizedHex, validHex, inputIssue]);

  const activeHex = !validHex || inputIssue
    ? ''
    : pendingTyping
      ? debouncedTypingHex
      : normalizedHex;

  const selectedProtocol = protocols.data?.find(item => item.id === draft.protocol);
  const protocolValid = selectedProtocol
    ? true
    : !protocols.isError && !protocols.data && Boolean(draft.protocol);

  const inspection = useQuery({
    queryKey: ['protocolInspection', draft.protocol, activeHex],
    queryFn: ({ signal }) => api.decodeProtocol(draft.protocol, activeHex, signal),
    enabled: Boolean(protocolValid && activeHex),
    placeholderData: previous => previous,
    staleTime: 60000,
    retry: false,
  });

  const result = protocolValid && validHex && !inputIssue && activeHex ? inspection.data : undefined;
  const decodeError = protocolValid && validHex && !inputIssue && activeHex && inspection.isError
    ? inspection.error
    : null;

  const updateHexFromTyping = (text: string) => {
    if (!pendingTyping) setDebouncedTypingHex(activeHex);
    setPendingTyping(true);
    setDraft({ protocol: draft.protocol, payloadHex: text, source: '手动输入' });
  };

  const loadSample = (sample: (typeof PROTOCOL_SAMPLES)[number]) => {
    setPendingTyping(false);
    const nextProtocol = !protocols.data?.length || protocols.data.some(item => item.id === sample.protocol)
      ? sample.protocol
      : draft.protocol;
    setDraft({ protocol: nextProtocol, payloadHex: sample.payloadHex, source: sample.source });
  };

  const hasWhitespaceInHex = /\s/.test(draft.payloadHex.trim());
  const toggleHexGrouping = () => {
    if (!validHex) return;
    setPendingTyping(false);
    const formatted = hasWhitespaceInHex
      ? compactHex.toUpperCase()
      : (compactHex.toUpperCase().match(/.{1,2}/g)?.join(' ') ?? compactHex.toUpperCase());
    setDraft({ ...draft, payloadHex: formatted });
  };

  const allInteractions = sessionRun
    ? (session.data?.results || []).flatMap(item =>
        (['TX', 'RX'] as const).flatMap(direction => {
          const input = duplexProtocolDraft(item, direction, sessionRun);
          return input ? [{ input, label: [direction, item.request_id, item.timestamp, item.status].filter(Boolean).join(' · ') }] : [];
        })
      ).map((entry, index) => ({ ...entry, index }))
    : [];

  const allFrames = (frames.data?.frames || []).map((frame, index) => ({
    index,
    frame,
    sourceLabel: `${sourceRunId} · 第 ${index + 1} 帧`,
    label: `${index + 1} · ${Number.isFinite(frame.time_s) ? frame.time_s.toFixed(4) + ' s' : '未知时间'} · ${frame.application?.command_name || hexText(frame.payload_hex).slice(0, 28) || '无载荷'}`,
  }));

  const filterTerm = recordFilter.trim().toLowerCase();
  const filteredInteractions = filterTerm
    ? allInteractions.filter(item =>
        item.label.toLowerCase().includes(filterTerm) ||
        item.input.payloadHex.replace(/\s/g, '').toLowerCase().includes(filterTerm))
    : allInteractions;
  const filteredFrames = filterTerm
    ? allFrames.filter(item =>
        item.label.toLowerCase().includes(filterTerm) ||
        String(item.frame.payload_hex ?? '').replace(/\s/g, '').toLowerCase().includes(filterTerm))
    : allFrames;

  const activeInteractionIndex =
    selectedInteractionIndex !== null && allInteractions[selectedInteractionIndex]?.input.source === draft.source
      ? selectedInteractionIndex
      : (allInteractions.find(item => item.input.source === draft.source)?.index ?? null);
  const activeInteractionPos = activeInteractionIndex !== null
    ? filteredInteractions.findIndex(item => item.index === activeInteractionIndex)
    : -1;

  const activeFrameIndex =
    selectedFrameIndex !== null && allFrames[selectedFrameIndex]?.sourceLabel === draft.source
      ? selectedFrameIndex
      : (allFrames.find(item => item.sourceLabel === draft.source)?.index ?? null);
  const activeFramePos = activeFrameIndex !== null
    ? filteredFrames.findIndex(item => item.index === activeFrameIndex)
    : -1;

  const selectInteraction = (index: number) => {
    const item = allInteractions[index];
    if (!item) return;
    setPendingTyping(false);
    setSelectedInteractionIndex(index);
    setDraft(item.input);
  };

  const selectFrame = (index: number) => {
    const entry = allFrames[index];
    if (!entry || typeof entry.frame.payload_hex !== 'string') return;
    setPendingTyping(false);
    const candidate = entry.frame.application?.rc_mode === 'multi' ? 'dk32s-multi' : entry.frame.application?.protocol;
    const nextProtocol = protocols.data?.some(item => item.id === candidate)
      ? candidate
      : (candidate === 'dk32s' || candidate === 'dk32s-multi' || candidate === 'datalink' ? candidate : draft.protocol);
    setSelectedFrameIndex(index);
    setDraft({
      payloadHex: entry.frame.payload_hex,
      protocol: nextProtocol,
      source: entry.sourceLabel,
      origin: { runId: sourceRunId, attempt: sourceRun?.attempt ?? 1 },
    });
  };

  const canParse = Boolean(protocolValid && validHex && !inputIssue && !inspection.isFetching);
  const parse = () => {
    if (!protocolValid || !validHex || inputIssue) return;
    if (pendingTyping) {
      setPendingTyping(false);
    } else {
      void inspection.refetch();
    }
  };

  const refreshSources = () => {
    void runs.refetch();
    if (sourceRunId && !listedRun) void runLookup.refetch();
    if (isDuplex) {
      void attempts.refetch();
      if (sessionReadable) void session.refetch();
    } else if (sourceRunId) {
      void context.refetch();
      if (framesPath) void frames.refetch();
    }
  };

  const showTaskFilter = (isDuplex && sessionReadable && allInteractions.length > 0)
    || (!isDuplex && Boolean(source && framesPath && fileReadable(source.file_states?.frames)) && allFrames.length > 0);

  return (
    <div className="pi-studio">
      <header className="flex flex-wrap items-center justify-between gap-4 border-b border-rf-border pb-4">
        <div className="flex items-center gap-3 min-w-0">
          <div className="p-2.5 bg-cyan-500/15 border border-cyan-500/40 rounded-xl text-cyan-400 shadow-lg shadow-cyan-950/20 shrink-0">
            <Braces className="w-6 h-6" aria-hidden="true" />
          </div>
          <div className="min-w-0">
            <h1 className="text-xl font-bold text-zinc-100 flex flex-wrap items-center gap-2">
              <span>独立业务协议解析工作台</span>
              <span className="text-xs px-2 py-0.5 rounded bg-cyan-950/60 border border-cyan-500/30 text-cyan-300 font-mono">
                dk32s / dk32s-multi / datalink
              </span>
            </h1>
            <p className="text-xs text-zinc-400 mt-0.5">
              实时剖析完整物理载荷中的帧头、业务位域与校验和，与空口解调及同步收发共用同一解析引擎
            </p>
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <button
            type="button"
            onClick={() => setTemplateDialogOpen(true)}
            className="pi-button"
          >
            <Layers aria-hidden="true" />
            从模板库载入
          </button>
          <button
            type="button"
            aria-expanded={taskDrawerOpen}
            aria-controls="pi-task-drawer"
            onClick={() => setTaskDrawerOpen(open => !open)}
            className="pi-button"
          >
            <Database className={sourceRunId ? 'text-cyan-400' : ''} aria-hidden="true" />
            <span>任务记录</span>
            {sourceRunId && <span className="pi-dot" aria-hidden="true" />}
          </button>
        </div>
      </header>

      <form
        className="pi-composer"
        aria-label="报文解析准备区"
        onSubmit={event => { event.preventDefault(); parse(); }}
        onKeyDown={event => {
          if ((event.ctrlKey || event.metaKey) && event.key === 'Enter') {
            event.preventDefault();
            parse();
          }
        }}
      >
        <div className="pi-toolbar">
          <div className="flex items-center gap-2 min-w-0">
            <Braces className="w-3.5 h-3.5 text-cyan-400 shrink-0" aria-hidden="true" />
            <span className="pi-eyebrow">HEX PAYLOAD COMPOSER · 物理载荷输入</span>
          </div>
          <div className="flex items-center gap-2 ml-auto">
            {inspection.isFetching && activeHex && (
              <span role="status" className="inline-flex items-center gap-1.5 text-[11px] font-mono text-cyan-300">
                <Loader2 className="w-3.5 h-3.5 animate-spin" aria-hidden="true" />
                正在同步解析…
              </span>
            )}
            <button
              type="button"
              className="pi-button pi-button--quiet"
              aria-expanded={editorOpen}
              aria-controls="pi-preparation"
              onClick={() => setEditorOpen(open => !open)}
            >
              {editorOpen ? <ChevronUp aria-hidden="true" /> : <ChevronDown aria-hidden="true" />}
              {editorOpen ? '收起准备区' : '展开准备区'}
            </button>
          </div>
        </div>

        {!editorOpen && (
          <div className="pi-source-line pi-source-collapsed">
            <span className="pi-source-tag">{selectedProtocol?.title || draft.protocol}</span>
            <span title={draft.source}>{byteCount} B · {draft.source}</span>
            <button type="submit" disabled={!canParse} className="pi-button pi-button--quiet">
              重新解析<ArrowRight aria-hidden="true" />
            </button>
          </div>
        )}

        <div id="pi-preparation" className="pi-preparation" hidden={!editorOpen}>
          <div className="pi-input-top">
            <div>
              <label htmlFor="protocol-select" className="pi-label">解析协议</label>
              <select
                id="protocol-select"
                aria-label="业务协议"
                className={inputClass}
                value={draft.protocol}
                disabled={!protocols.data?.length}
                onChange={event => {
                  setPendingTyping(false);
                  setDraft({ ...draft, protocol: event.target.value });
                }}
              >
                {protocols.data?.length
                  ? protocols.data.map(item => <option key={item.id} value={item.id}>{item.title}</option>)
                  : <option value={draft.protocol}>{protocols.isError ? '协议列表加载失败' : '正在加载协议…'}</option>}
              </select>
            </div>
            <p>{selectedProtocol?.description || '与 CLI 共用协议解析器，输入或切换帧后自动同步解析完整物理载荷。'}</p>
          </div>

          {protocols.error && (
            <div role="alert" className="pi-error">
              {protocols.error.message}{' '}
              <button type="button" onClick={() => void protocols.refetch()} className="underline">重新加载</button>
            </div>
          )}

          <div id="pi-hex-panel">
            <label htmlFor="protocol-payload" className="sr-only">
              完整物理载荷 Hex（包括软件长度和业务 sum8，不含同步字、硬件头和物理 CRC）
            </label>
            <div className="pi-editor">
              <div className="pi-editor-caption">
                <div className="pi-editor-meta">
                  <span>PAYLOAD / HEX</span>
                  <span className="text-cyan-300">{byteCount ? `${byteCount} BYTES` : '00 BYTES'}</span>
                </div>
                <div className="pi-editor-tools">
                  <button
                    type="button"
                    disabled={!validHex}
                    onClick={toggleHexGrouping}
                    className="pi-chip"
                  >
                    {hasWhitespaceInHex ? '紧凑连接' : '按字节分组'}
                  </button>
                  <CopyButton text={hexText(compactHex)} label="复制 Hex" />
                </div>
              </div>
              <textarea
                id="protocol-payload"
                rows={3}
                maxLength={8192}
                spellCheck={false}
                value={hexText(draft.payloadHex)}
                onChange={event => updateHexFromTyping(event.target.value)}
                placeholder="粘贴完整物理载荷 Hex，支持空格和换行（输入有效字节后自动实时解析）…"
                aria-describedby="protocol-input-help"
                aria-invalid={Boolean(inputIssue)}
              />
            </div>
            <p id="protocol-input-help" className="pi-help">
              保留软件长度、时间戳与 sum8；不含同步字、硬件头和物理 CRC。每字节两位，最多 1024 B。
            </p>
            <div className="pi-sample-bar">
              <span>快速载入示例：</span>
              {PROTOCOL_SAMPLES.map(sample => (
                <button
                  key={sample.id}
                  type="button"
                  onClick={() => loadSample(sample)}
                  className="pi-chip"
                >
                  {sample.label}
                </button>
              ))}
            </div>
          </div>

          {inputIssue && <p role="alert" className="pi-error">{inputIssue}</p>}
          {decodeError && <p role="alert" className="pi-error">{decodeError.message}</p>}

          <div className="pi-composer-foot">
            <div className="pi-source-line">
              <span className="pi-source-tag">SOURCE</span>
              <span title={draft.source}>{draft.source}</span>
              {sourceRunId && (
                <button
                  type="button"
                  onClick={() => setTaskDrawerOpen(true)}
                  className="pi-chip shrink-0"
                >
                  <Database className="w-3 h-3 text-cyan-400" aria-hidden="true" />
                  切换同任务报文
                </button>
              )}
            </div>
            <div className="pi-actions">
              <span className="hidden sm:inline text-[10px] font-mono text-zinc-500">自动实时解析 · Ctrl / ⌘ + Enter 刷新</span>
              <button
                type="button"
                onClick={() => {
                  setPendingTyping(false);
                  setSelectedInteractionIndex(null);
                  setSelectedFrameIndex(null);
                  setDraft({ protocol: draft.protocol, payloadHex: '', source: '手动输入' });
                }}
                className="pi-button pi-button--quiet"
              >
                清空
              </button>
              <button type="submit" disabled={!canParse} className="pi-button pi-button--primary">
                {inspection.isFetching
                  ? <Loader2 className="animate-spin" aria-hidden="true" />
                  : <ArrowRight aria-hidden="true" />}
                {inspection.isFetching ? '正在解析…' : '解析报文'}
              </button>
            </div>
          </div>
        </div>
      </form>

      {!compactHex && (
        <section aria-label="协议解析快速开始" className="pi-empty-card">
          <div>
            <p className="pi-eyebrow">WIRE PAYLOAD FORMAT · 物理载荷结构说明</p>
            <h2 className="mt-1 text-sm font-semibold text-zinc-100">
              粘贴空口物理载荷、从右侧任务抽屉载入，或点击下方标准示例立即剖析字段与位域
            </h2>
            <p className="mt-1 text-xs text-zinc-400">
              本工作台接收去除前导码、同步字、FLRC 硬件头与物理 CRC16 后的完整软件载荷字节流，输入有效偶数字节后将自动完成解码。
            </p>
          </div>
          <div className="pi-wire-guide">
            <div className="pi-wire-guide-step">
              <strong>01 · Length (1B)</strong>
              <span>首字节软件帧长，声明后续载荷总字节数</span>
            </div>
            <div className="pi-wire-guide-step">
              <strong>02 · Timestamp (0/4B)</strong>
              <span>DK32S 可选 32-bit 小端微秒级时间戳前缀</span>
            </div>
            <div className="pi-wire-guide-step">
              <strong>03 · Header (3B)</strong>
              <span>业务长度与 ACK 标志、包序号、指令字 Command</span>
            </div>
            <div className="pi-wire-guide-step" data-tone="business">
              <strong>04 · Fields (N B)</strong>
              <span>结构化业务参数或 11-bit 紧缩遥控通道数组</span>
            </div>
            <div className="pi-wire-guide-step" data-tone="checksum">
              <strong>05 · SUM8 (1B)</strong>
              <span>尾字节 8-bit 业务累加和校验，实时验证完整性</span>
            </div>
          </div>
          <div className="flex flex-wrap items-center gap-2 pt-1">
            {PROTOCOL_SAMPLES.map(sample => (
              <button
                key={sample.id}
                type="button"
                onClick={() => loadSample(sample)}
                className="pi-button"
              >
                <Sparkles className="text-cyan-400" aria-hidden="true" />
                载入 {sample.label}
              </button>
            ))}
            <button
              type="button"
              onClick={() => setTaskDrawerOpen(true)}
              className="pi-button pi-button--quiet"
            >
              <Database aria-hidden="true" />
              打开任务记录抽屉…
            </button>
            <button
              type="button"
              onClick={() => setTemplateDialogOpen(true)}
              className="pi-button pi-button--quiet"
            >
              <Layers aria-hidden="true" />
              从模板库选择报文…
            </button>
          </div>
        </section>
      )}

      {compactHex && !inputIssue && inspection.isPending && !result && (
        <div role="status" className="pi-loading-card">
          <Loader2 className="w-4 h-4 animate-spin text-cyan-400 shrink-0" aria-hidden="true" />
          <span>正在解析物理载荷字节与位域结构…</span>
        </div>
      )}

      {result && <ProtocolInspectionResult key={result.protocol + ':' + result.payload_hex} result={result} />}

      {!taskDrawerOpen && (
        <button
          type="button"
          className="pi-drawer-edge-trigger"
          aria-label="展开任务记录抽屉"
          aria-expanded={false}
          aria-controls="pi-task-drawer"
          onClick={() => setTaskDrawerOpen(true)}
        >
          <ChevronLeft aria-hidden="true" />
          <Database className={sourceRunId ? 'text-cyan-400' : ''} aria-hidden="true" />
          <span className="pi-drawer-edge-label">任务记录</span>
          {sourceRunId && <span className="pi-dot" aria-hidden="true" />}
        </button>
      )}

      <aside
        ref={drawerRef}
        id="pi-task-drawer"
        aria-label="从任务载入报文"
        hidden={!taskDrawerOpen}
        className="pi-task-drawer"
      >
        <div className="pi-drawer-header">
          <div className="flex items-center gap-2.5 min-w-0">
            <div className="p-2 rounded-lg bg-cyan-500/15 border border-cyan-500/35 text-cyan-400 shrink-0">
              <Database className="w-4 h-4" aria-hidden="true" />
            </div>
            <div className="min-w-0">
              <h2 className="text-sm font-semibold text-zinc-100">从任务载入报文</h2>
              <p className="text-[11px] text-zinc-400 truncate">选择解调帧或同步收发记录，实时联动主工作台</p>
            </div>
          </div>
          <div className="flex items-center gap-1 shrink-0">
            <button
              type="button"
              onClick={refreshSources}
              className="pi-button pi-button--quiet"
              aria-label="刷新任务报文"
              title="刷新任务记录"
            >
              <RefreshCw aria-hidden="true" />
            </button>
            <button
              type="button"
              data-drawer-close
              onClick={() => setTaskDrawerOpen(false)}
              className="pi-button pi-button--quiet"
              aria-label="关闭任务记录抽屉"
              title="收起抽屉 (Esc)"
            >
              <X aria-hidden="true" />
            </button>
          </div>
        </div>

        <div className="pi-drawer-body">
          <div>
            <label htmlFor="protocol-source-run" className="pi-label">最近任务（最多 100 个）</label>
            <select
              id="protocol-source-run"
              value={sourceRunId}
              onChange={event => {
                setSourceRunId(event.target.value);
                setSourceAttempt(undefined);
                setRecordFilter('');
                setSelectedInteractionIndex(null);
                setSelectedFrameIndex(null);
              }}
              className={inputClass}
            >
              <option value="">选择解调任务或同步收发任务</option>
              {sourceRunId && !listedRun && <option value={sourceRunId}>{sourceRun ? runOption(sourceRun) : sourceRunId}</option>}
              {runs.data?.map(run => <option key={run.id} value={run.id}>{runOption(run)}</option>)}
            </select>
          </div>

          {isDuplex && (
            <div>
              <label htmlFor="protocol-source-attempt" className="pi-label">同步收发执行轮次</label>
              <select
                id="protocol-source-attempt"
                value={attempt}
                onChange={event => {
                  setSourceAttempt(Number(event.target.value));
                  setRecordFilter('');
                  setSelectedInteractionIndex(null);
                }}
                className={inputClass}
              >
                {!attempts.data?.some(run => run.attempt === attempt) && <option value={attempt}>第 {attempt} 次执行</option>}
                {attempts.data?.map(run => <option key={run.attempt} value={run.attempt}>第 {run.attempt} 次执行 · {run.status}</option>)}
              </select>
            </div>
          )}

          {showTaskFilter && (
            <div>
              <label htmlFor="protocol-source-filter" className="pi-label">
                筛选记录（{isDuplex ? `${filteredInteractions.length} / ${allInteractions.length}` : `${filteredFrames.length} / ${allFrames.length}`}）
              </label>
              <input
                id="protocol-source-filter"
                type="search"
                aria-label="筛选任务报文"
                value={recordFilter}
                onChange={event => setRecordFilter(event.target.value)}
                placeholder={isDuplex ? '按 TX/RX、请求 ID、状态或 Hex 筛选…' : '按帧号、命令名、时间或 Hex 筛选…'}
                className={inputClass}
              />
            </div>
          )}

          {isDuplex && sessionReadable && (
            <div className="space-y-2.5">
              <label htmlFor="protocol-source-interaction" className="pi-label">
                同步收发交互报文（最近 50 条交互）
              </label>
              <div className="pi-stepper-row">
                <select
                  id="protocol-source-interaction"
                  value={activeInteractionPos >= 0 && activeInteractionIndex !== null ? String(activeInteractionIndex) : ''}
                  disabled={!filteredInteractions.length}
                  className={inputClass}
                  onChange={event => {
                    if (event.target.value !== '') selectInteraction(Number(event.target.value));
                  }}
                >
                  <option value="">
                    {session.isPending
                      ? '正在读取交互记录…'
                      : filteredInteractions.length
                        ? '选择 TX 或 RX 载入'
                        : '没有匹配的交互报文'}
                  </option>
                  {filteredInteractions.map(item => (
                    <option key={item.index} value={item.index}>{item.label}</option>
                  ))}
                </select>
                <button
                  type="button"
                  aria-label="上一条交互报文"
                  disabled={filteredInteractions.length === 0 || activeInteractionPos <= 0}
                  onClick={() => {
                    if (activeInteractionPos > 0) selectInteraction(filteredInteractions[activeInteractionPos - 1].index);
                  }}
                  className="pi-button shrink-0"
                >
                  <ChevronLeft aria-hidden="true" />上一条
                </button>
                <button
                  type="button"
                  aria-label="下一条交互报文"
                  disabled={filteredInteractions.length === 0 || (activeInteractionPos >= 0 && activeInteractionPos >= filteredInteractions.length - 1)}
                  onClick={() => {
                    const target = filteredInteractions[activeInteractionPos >= 0 ? activeInteractionPos + 1 : 0];
                    if (target) selectInteraction(target.index);
                  }}
                  className="pi-button shrink-0"
                >
                  下一条<ChevronRight aria-hidden="true" />
                </button>
              </div>
              {filteredInteractions.length > 0 && (
                <div className="pi-drawer-list" role="list" aria-label="交互报文列表">
                  {filteredInteractions.map(item => {
                    const active = item.index === activeInteractionIndex;
                    return (
                      <button
                        key={item.index}
                        type="button"
                        data-active={active}
                        onClick={() => selectInteraction(item.index)}
                        className="pi-drawer-item"
                      >
                        <span className="text-xs font-medium text-zinc-200 truncate w-full">{item.label}</span>
                        <span className="text-[11px] font-mono text-cyan-300/90 truncate w-full">
                          {hexText(item.input.payloadHex)}
                        </span>
                      </button>
                    );
                  })}
                </div>
              )}
            </div>
          )}

          {!isDuplex && source && framesPath && fileReadable(source.file_states?.frames) && (
            <div className="space-y-2.5">
              <label htmlFor="protocol-source-frame" className="pi-label">
                解调帧（本次最多读取 500 帧）
              </label>
              <div className="pi-stepper-row">
                <select
                  id="protocol-source-frame"
                  className={inputClass}
                  value={activeFramePos >= 0 && activeFrameIndex !== null ? String(activeFrameIndex) : ''}
                  disabled={!filteredFrames.length}
                  onChange={event => {
                    if (event.target.value !== '') selectFrame(Number(event.target.value));
                  }}
                >
                  <option value="">
                    {frames.isPending
                      ? '正在读取报文…'
                      : filteredFrames.length
                        ? '选择一帧载入'
                        : '没有匹配的解调帧'}
                  </option>
                  {filteredFrames.map(entry => (
                    <option key={entry.index} value={entry.index}>{entry.label}</option>
                  ))}
                </select>
                <button
                  type="button"
                  aria-label="上一帧报文"
                  disabled={filteredFrames.length === 0 || activeFramePos <= 0}
                  onClick={() => {
                    if (activeFramePos > 0) selectFrame(filteredFrames[activeFramePos - 1].index);
                  }}
                  className="pi-button shrink-0"
                >
                  <ChevronLeft aria-hidden="true" />上一帧
                </button>
                <button
                  type="button"
                  aria-label="下一帧报文"
                  disabled={filteredFrames.length === 0 || (activeFramePos >= 0 && activeFramePos >= filteredFrames.length - 1)}
                  onClick={() => {
                    const target = filteredFrames[activeFramePos >= 0 ? activeFramePos + 1 : 0];
                    if (target) selectFrame(target.index);
                  }}
                  className="pi-button shrink-0"
                >
                  下一帧<ChevronRight aria-hidden="true" />
                </button>
              </div>
              {filteredFrames.length > 0 && (
                <div className="pi-drawer-list" role="list" aria-label="解调帧列表">
                  {filteredFrames.slice(0, 80).map(entry => {
                    const active = entry.index === activeFrameIndex;
                    return (
                      <button
                        key={entry.index}
                        type="button"
                        data-active={active}
                        onClick={() => selectFrame(entry.index)}
                        className="pi-drawer-item"
                      >
                        <span className="text-xs font-medium text-zinc-200 truncate w-full">{entry.label}</span>
                        <span className="text-[11px] font-mono text-cyan-300/90 truncate w-full">
                          {hexText(entry.frame.payload_hex)}
                        </span>
                      </button>
                    );
                  })}
                </div>
              )}
            </div>
          )}

          {runs.error && <p role="alert">{runs.error.message}</p>}
          {sourceRunId && !listedRun && runLookup.error && <p role="alert">{runLookup.error.message}</p>}
          {sourceRunId && !listedRun && runLookup.isPending && <p role="status">正在读取任务…</p>}
          {isDuplex && attempts.error && <p role="alert">{attempts.error.message}</p>}
          {isDuplex && !sessionRun && attempts.isPending && <p role="status">正在读取执行记录…</p>}
          {isDuplex && sessionRun && !sessionReadable && <p>该轮不是常驻同步会话，没有可读取的会话交互记录；可手动输入 Hex。</p>}
          {sessionReadable && session.error && <p role="alert">{session.error.message}</p>}
          {sessionReadable && session.isSuccess && !allInteractions.length && <p>此轮没有已保存的 TX/RX 载荷；超时记录不会生成虚构 RX。</p>}
          {isDuplex && <p>交互记录来自任务数据库，无需 JSONL。运行中可点击刷新读取新记录。</p>}
          {sourceRun && !isDuplex && context.isPending && <p role="status">正在读取任务…</p>}
          {!isDuplex && sourceRunId && context.error && <p role="alert">{context.error.message}</p>}
          {!isDuplex && source && (!framesPath || !fileReadable(source.file_states?.frames)) && (
            <p>{source.file_states?.frames?.message || '此任务没有可读取的解调 JSONL；仍可手动输入 Hex。'}</p>
          )}
          {!isDuplex && framesPath && frames.error && <p role="alert">{frames.error.message}</p>}
          {!isDuplex && framesPath && frames.isSuccess && !allFrames.length && <p>尚无解调帧，请刷新或手动输入。</p>}
          {!sourceRunId && <p>载入解调帧或同步收发的 TX / RX，点击列表项或上下步进即可在左侧主界面实时查看解析结果。</p>}
        </div>

        <div className="pi-drawer-footer">
          <span className="text-[11px] font-mono text-zinc-400 truncate" title={draft.source}>
            {byteCount ? `${byteCount} B · ${draft.source}` : '尚未载入载荷'}
          </span>
          <button
            type="button"
            onClick={() => setTaskDrawerOpen(false)}
            className="pi-button shrink-0"
          >
            收起抽屉
          </button>
        </div>
      </aside>

      {templateDialogOpen && (
        <PayloadTemplateDialog
          sampleRate={2500000}
          onClose={() => setTemplateDialogOpen(false)}
          onSelect={payload => {
            setPendingTyping(false);
            setDraft({
              protocol: payload.protocol,
              payloadHex: payload.payload_hex,
              source: `模板库 · ${payload.protocol.toUpperCase()}`,
            });
            setTemplateDialogOpen(false);
          }}
        />
      )}
    </div>
  );
}
