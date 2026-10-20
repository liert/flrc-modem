import React, { useState, useEffect, useRef } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  ListOrdered,
  Terminal,
  StopCircle,
  Clock,
  CheckCircle2,
  XCircle,
  AlertTriangle,
  RotateCcw,
  Copy,
  Check,
  Download,
  FileCode,
  Layers,
  Search,
  Pencil,
  Trash2,
} from 'lucide-react';
import { api } from '../api/client';
import { useWorkstationStore } from '../stores/useWorkstationStore';
import { useWebSocketStore } from '../stores/useWebSocketStore';
import { mergeLogs } from '../utils/runtime';
import { fileStateLabel, fileReadable } from '../utils/analysis';
import { runTitle, runTime, runDeletable, purgeRunCaches } from '../utils/runs';

export const TaskCenterView: React.FC = () => {
  const queryClient = useQueryClient();
  const { selectedRunId, setSelectedRunId, setSelectedArtifactPath } = useWorkstationStore();

  const [filterStatus, setFilterStatus] = useState<string>('ALL');
  const [searchTerm, setSearchTerm] = useState<string>('');
  const [activeTab, setActiveTab] = useState<'logs' | 'events' | 'params' | 'artifacts'>('logs');
  const [logFilter, setLogFilter] = useState<'all' | 'stdout' | 'stderr'>('all');
  const [copied, setCopied] = useState(false);
  const [page, setPage] = useState(0);
  const [checkedIds, setCheckedIds] = useState<string[]>([]);
  const [deleteIds, setDeleteIds] = useState<string[]>([]);
  const [editingName, setEditingName] = useState(false);
  const [draftName, setDraftName] = useState('');
  const pageSize = 50;

  const terminalRef = useRef<HTMLDivElement>(null);

  const { data: runPage, error: runsError, isFetching: fetchingRuns } = useQuery({
    queryKey: ['runs', pageSize + 1, page * pageSize, filterStatus, searchTerm],
    queryFn: () => api.getRuns(pageSize + 1, page * pageSize, filterStatus === 'ALL' ? '' : filterStatus, searchTerm),
    refetchInterval: 3000,
  });
  const runs = runPage?.slice(0, pageSize);
  const hasMore = (runPage?.length || 0) > pageSize;
  useEffect(() => {
    if (runPage?.length === 0 && page > 0) setPage(value => value - 1);
  }, [runPage, page]);

  const activeRunId = selectedRunId || runs?.[0]?.id || '';
  const liveLogs = useWebSocketStore(state => state.logs[activeRunId]);
  const { data: currentRun, error: runError } = useQuery({
    queryKey: ['run', activeRunId],
    queryFn: () => api.getRun(activeRunId),
    enabled: Boolean(activeRunId),
    retry: false,
    refetchInterval: query => ['RUNNING', 'QUEUED'].includes(query.state.data?.status) ? 2000 : false,
  });

  // 历史日志与事件查询
  const { data: historyLogs } = useQuery({
    queryKey: ['runLogs', activeRunId, currentRun?.status],
    queryFn: () => api.getRunLogs(activeRunId, 2000),
    enabled: Boolean(activeRunId),
    refetchInterval: ['RUNNING', 'QUEUED'].includes(currentRun?.status) ? 3000 : false,
  });

  const { data: runEvents } = useQuery({
    queryKey: ['runEvents', activeRunId, currentRun?.status],
    queryFn: () => api.getRunEvents(activeRunId),
    enabled: Boolean(activeRunId),
    refetchInterval: currentRun?.status === 'RUNNING' ? 2000 : false,
  });

  const { data: runArtifacts } = useQuery({
    queryKey: ['runArtifacts', activeRunId, currentRun?.status],
    queryFn: () => api.getRunArtifacts(activeRunId),
    enabled: Boolean(activeRunId),
    refetchInterval: activeTab === 'artifacts' ? 3000 : false,
  });

  // 合并实时推送的日志与历史持久化日志
  const combinedLogs = React.useMemo(() => {
    if (!activeRunId) return [];
    return mergeLogs(historyLogs, liveLogs);
  }, [activeRunId, liveLogs, historyLogs]);

  // 自动滚至终端底部
  useEffect(() => {
    if (terminalRef.current && currentRun?.status === 'RUNNING') {
      terminalRef.current.scrollTop = terminalRef.current.scrollHeight;
    }
  }, [combinedLogs, currentRun]);

  const stopMutation = useMutation({
    mutationFn: (id: string) => api.stopRun(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['runs'] });
      queryClient.invalidateQueries({ queryKey: ['run'] });
    },
  });

  useEffect(() => { setEditingName(false); }, [activeRunId]);
  const renameMutation = useMutation({
    mutationFn: ({ id, name }: { id: string; name: string }) => api.renameRun(id, name),
    onSuccess: () => {
      setEditingName(false);
      void queryClient.invalidateQueries({ queryKey: ['runs'] });
      void queryClient.invalidateQueries({ queryKey: ['run'] });
    },
  });
  const deleteMutation = useMutation({
    mutationFn: (ids: string[]) => api.deleteRuns(ids),
    onSuccess: async ({ deleted_run_ids: ids }) => {
      const selectedId = useWorkstationStore.getState().selectedRunId;
      if (selectedId && ids.includes(selectedId)) {
        setSelectedRunId(null);
        setSelectedArtifactPath(null);
      }
      await purgeRunCaches(queryClient, ids);
      for (const id of ids) useWebSocketStore.getState().clearRunData(id);
      setCheckedIds([]);
      setDeleteIds([]);
      void queryClient.invalidateQueries({ queryKey: ['runs'] });
      void queryClient.invalidateQueries({ queryKey: ['artifacts'] });
      void queryClient.invalidateQueries({ queryKey: ['analysisContext'] });
    },
    onError: () => { void queryClient.invalidateQueries({ queryKey: ['runs'] }); },
  });
  const deletableIds = runs?.filter(runDeletable).map(run => run.id) || [];
  const selectedIds = checkedIds.filter(id => deletableIds.includes(id));
  const changePage = (next: number) => { setPage(next); setCheckedIds([]); };

  const handleCopyLogs = () => {
    const text = combinedLogs.map((l) => `[${l.stream.toUpperCase()}] ${l.line}`).join('\n');
    navigator.clipboard.writeText(text);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <div className="flex h-[calc(100vh-3.5rem)] overflow-hidden select-none">
      {/* 左侧：任务列表 */}
      <div className="w-96 shrink-0 bg-rf-surface border-r border-rf-border flex flex-col">
        <div className="p-3 border-b border-rf-border space-y-2">
          <div className="relative">
            <Search className="w-4 h-4 text-zinc-500 absolute left-2.5 top-2.5" />
            <input
              type="text"
              placeholder="搜索名称 / 文件 / 配置 / 日期 / ID…"
              maxLength={200}
              value={searchTerm}
              onChange={(e) => { setSearchTerm(e.target.value); changePage(0); }}
              className="w-full bg-rf-bg border border-rf-border rounded pl-8 pr-3 py-1.5 text-xs text-rf-text placeholder-zinc-500 focus:outline-none focus:border-rf-accent"
            />
          </div>

          <div className="flex flex-wrap gap-1 font-mono text-[10px]">
            {['ALL', 'QUEUED', 'RUNNING', 'SUCCEEDED', 'FAILED', 'CANCELLED'].map((st) => (
              <button
                key={st}
                onClick={() => { setFilterStatus(st); changePage(0); }}
                className={`px-2 py-0.5 rounded transition-colors ${
                  filterStatus === st
                    ? 'bg-cyan-500/20 text-rf-accent border border-cyan-500/40'
                    : 'bg-rf-panel text-zinc-400 hover:text-rf-text'
                }`}
              >
                {st}
              </button>
            ))}
          </div>
          <div className="flex items-center justify-between text-xs text-zinc-400">
            <label className="flex items-center gap-2">
              <input type="checkbox" aria-label="选择本页已结束任务" disabled={!deletableIds.length || deleteMutation.isPending}
                checked={deletableIds.length > 0 && selectedIds.length === deletableIds.length}
                onChange={event => setCheckedIds(event.target.checked ? deletableIds : [])} />
              本页已结束任务
            </label>
            <button disabled={!selectedIds.length || deleteMutation.isPending} onClick={() => { deleteMutation.reset(); setDeleteIds(selectedIds); }}
              className="text-red-400 disabled:opacity-40">删除选中 ({selectedIds.length})</button>
          </div>
          {runsError && <p role="alert" className="text-xs text-red-400">{runsError.message}</p>}
        </div>

        {/* 任务项 */}
        <div className="flex-1 overflow-y-auto divide-y divide-rf-border/40 font-mono text-xs">
          {runs?.map((run) => {
            const isSelected = run.id === activeRunId;
            return (
              <div
                key={run.id}
                onClick={() => setSelectedRunId(run.id)}
                className={`p-3 cursor-pointer transition-colors ${
                  isSelected ? 'bg-cyan-500/10 border-l-2 border-rf-accent' : 'hover:bg-rf-panel/60'
                }`}
              >
                <div className="flex items-center justify-between mb-1">
                  <div className="flex items-center gap-2 min-w-0 pr-2">
                    <input type="checkbox" aria-label={`选择任务 ${runTitle(run)}`} disabled={!runDeletable(run) || deleteMutation.isPending}
                      checked={selectedIds.includes(run.id)} onClick={event => event.stopPropagation()}
                      onChange={event => setCheckedIds(ids => event.target.checked ? [...ids, run.id] : ids.filter(id => id !== run.id))} />
                    <span title={runTitle(run)} className="font-semibold text-rf-text truncate">{runTitle(run)}</span>
                  </div>
                  <span
                    className={`px-1.5 py-0.2 rounded text-[9px] font-bold ${
                      run.status === 'SUCCEEDED'
                        ? 'bg-emerald-950 text-emerald-400 border border-emerald-800'
                        : run.status === 'RUNNING'
                        ? 'bg-cyan-950 text-cyan-400 border border-cyan-800 animate-pulse'
                        : run.status === 'FAILED'
                        ? 'bg-red-950 text-red-400 border border-red-800'
                        : 'bg-zinc-800 text-zinc-400'
                    }`}
                  >
                    {run.status}
                  </span>
                </div>
                <div className="text-[10px] text-zinc-500 truncate">{run.id}</div>
                <div className="text-[10px] text-zinc-400 truncate" title={run.command_args.iq || run.command_args.input || run.command_args.file || run.command_args.output}>
                  {run.command_id} · {run.command_args.iq || run.command_args.input || run.command_args.file || run.command_args.output || '无关联文件'}
                </div>
                <div className="text-[10px] text-zinc-400 mt-1 flex justify-between">
                  <span>退出码: {run.exit_code ?? '-'}</span>
                  <span>{runTime(run.created_at)}</span>
                </div>
              </div>
            );
          })}
          {!runs?.length && !fetchingRuns && <p className="p-6 text-zinc-500 text-center">没有符合条件的任务</p>}
        </div>
        <div className="p-3 border-t border-rf-border flex justify-between text-xs text-zinc-400">
          <button disabled={page === 0 || fetchingRuns} className="disabled:opacity-30" onClick={() => changePage(page - 1)}>上一页</button>
          <span>第 {page + 1} 页 · 本页 {runs?.length || 0} 项</span>
          <button disabled={!hasMore || fetchingRuns} className="disabled:opacity-30" onClick={() => changePage(page + 1)}>下一页</button>
        </div>
      </div>

      {/* 右侧：任务详情与终端 */}
      <div className="flex-1 flex flex-col overflow-hidden bg-rf-bg">
        {currentRun ? (
          <>
            {/* 任务头部摘要栏 */}
            <div className="p-4 bg-rf-surface border-b border-rf-border flex flex-wrap items-center justify-between gap-3">
              <div className="space-y-1 min-w-0">
                <div className="flex flex-wrap items-center gap-2">
                  {editingName ? <form className="flex gap-2" onSubmit={event => {
                    event.preventDefault(); renameMutation.mutate({ id: currentRun.id, name: draftName });
                  }}>
                    <input autoFocus aria-label="任务名称" maxLength={120} value={draftName}
                      placeholder="留空恢复自动名称" onChange={event => setDraftName(event.target.value)}
                      className="bg-rf-bg border border-rf-border rounded px-2 py-1 text-xs w-64" />
                    <button disabled={renameMutation.isPending} className="text-xs text-cyan-400">保存</button>
                    <button type="button" disabled={renameMutation.isPending} onClick={() => setEditingName(false)} className="text-xs text-zinc-400">取消</button>
                  </form> : <>
                    <h2 className="text-sm font-bold text-rf-text break-all">{runTitle(currentRun)}</h2>
                    <button title="重命名任务" aria-label="重命名任务" onClick={() => {
                      renameMutation.reset(); setDraftName(currentRun.name || ''); setEditingName(true);
                    }}><Pencil className="w-3.5 h-3.5 text-zinc-400" /></button>
                  </>}
                  <span className="text-xs font-mono text-zinc-400">{currentRun.id}</span>
                  <span
                    className={`px-2 py-0.5 rounded text-[10px] font-bold font-mono ${
                      currentRun.status === 'SUCCEEDED'
                        ? 'bg-emerald-950 text-emerald-400 border border-emerald-800'
                        : currentRun.status === 'RUNNING'
                        ? 'bg-cyan-950 text-cyan-400 border border-cyan-800 animate-pulse'
                        : 'bg-red-950 text-red-400 border border-red-800'
                    }`}
                  >
                    {currentRun.status}
                  </span>
                </div>
                <div className="text-[11px] font-mono text-zinc-500 flex items-center space-x-4">
                  <span>创建: {runTime(currentRun.created_at)}</span>
                  {currentRun.completed_at && <span>结束: {runTime(currentRun.completed_at)}</span>}
                  {currentRun.requires_usrp && <span className="text-amber-400">USRP 独占</span>}
                </div>
              </div>

              {runDeletable(currentRun) && <button disabled={deleteMutation.isPending} onClick={() => {
                deleteMutation.reset(); setDeleteIds([currentRun.id]);
              }} className="flex gap-1.5 items-center text-xs text-red-400 px-3 py-1.5 rounded border border-red-900">
                <Trash2 className="w-3.5 h-3.5" />删除任务
              </button>}
              {['RUNNING', 'QUEUED'].includes(currentRun.status) && (
                <button
                  onClick={() => stopMutation.mutate(currentRun.id)}
                  disabled={stopMutation.isPending}
                  className="px-4 py-1.5 rounded bg-red-600 hover:bg-red-500 text-white font-bold text-xs flex items-center space-x-1.5 shadow-md shadow-red-600/20"
                >
                  <StopCircle className="w-4 h-4" />
                  <span>终止任务 (Stop Process)</span>
                </button>
              )}
            </div>
            {(renameMutation.error || stopMutation.error) && <p role="alert" className="p-3 text-sm text-red-400">{(renameMutation.error || stopMutation.error)?.message}</p>}

            {/* 选项卡栏 */}
            <div className="flex border-b border-rf-border bg-rf-panel/50 px-4">
              {[
                { id: 'logs', label: '实时终端日志 (Logs)' },
                { id: 'events', label: '结构化事件 (Events)' },
                { id: 'params', label: '参数快照 (Params)' },
                { id: 'artifacts', label: `生成产物 (${runArtifacts?.length || 0})` },
              ].map((tab) => (
                <button
                  key={tab.id}
                  onClick={() => setActiveTab(tab.id as any)}
                  className={`px-4 py-2.5 text-xs font-mono font-medium border-b-2 transition-colors ${
                    activeTab === tab.id
                      ? 'border-rf-accent text-rf-accent bg-cyan-500/5'
                      : 'border-transparent text-zinc-400 hover:text-rf-text'
                  }`}
                >
                  {tab.label}
                </button>
              ))}
            </div>

            {/* 选项卡内容区 */}
            <div className="flex-1 overflow-hidden p-4">
              {activeTab === 'logs' && (
                <div className="h-full flex flex-col bg-rf-bg border border-rf-border rounded-lg overflow-hidden">
                  <div className="p-2 border-b border-rf-border/80 bg-rf-panel/60 flex items-center justify-between text-xs font-mono">
                    <div className="flex items-center space-x-2">
                      <Terminal className="w-3.5 h-3.5 text-cyan-400" />
                      <span className="text-zinc-400">输出流:</span>
                      {(['all', 'stdout', 'stderr'] as const).map((filter) => (
                        <button
                          key={filter}
                          onClick={() => setLogFilter(filter)}
                          className={`px-2 py-0.5 rounded text-[10px] uppercase ${
                            logFilter === filter
                              ? 'bg-cyan-500/20 text-rf-accent border border-cyan-500/40'
                              : 'text-zinc-500 hover:text-zinc-300'
                          }`}
                        >
                          {filter}
                        </button>
                      ))}
                    </div>

                    <button
                      onClick={handleCopyLogs}
                      className="px-2 py-1 rounded bg-rf-panel hover:bg-rf-border text-zinc-300 text-[11px] flex items-center space-x-1"
                    >
                      {copied ? <Check className="w-3 h-3 text-emerald-400" /> : <Copy className="w-3 h-3" />}
                      <span>{copied ? '已复制' : '复制日志'}</span>
                    </button>
                  </div>

                  <div
                    ref={terminalRef}
                    className="flex-1 p-3 overflow-y-auto font-mono text-xs leading-relaxed space-y-0.5 select-text"
                  >
                    {combinedLogs
                      .filter((l) => logFilter === 'all' || l.stream === logFilter)
                      .map((log, idx) => (
                        <div
                          key={idx}
                          className={`flex space-x-2 ${
                            log.stream === 'stderr' ? 'text-amber-300/90' : 'text-zinc-300'
                          }`}
                        >
                          <span className="text-zinc-600 select-none">
                            {log.timestamp ? log.timestamp.split('T')[1].split('.')[0] : ''}
                          </span>
                          <span
                            className={`select-none uppercase text-[10px] px-1 rounded ${
                              log.stream === 'stderr'
                                ? 'bg-amber-950/40 text-amber-400'
                                : 'bg-zinc-800 text-zinc-400'
                            }`}
                          >
                            {log.stream}
                          </span>
                          <span className="break-all whitespace-pre-wrap">{log.line}</span>
                        </div>
                      ))}
                    {!combinedLogs.length && (
                      <div className="text-zinc-600 text-center py-12">暂无日志输出</div>
                    )}
                  </div>
                </div>
              )}

              {activeTab === 'events' && (
                <div className="h-full bg-rf-bg border border-rf-border rounded-lg overflow-y-auto font-mono text-xs">
                  <table className="w-full text-left">
                    <thead className="bg-rf-panel/60 text-zinc-400 text-[11px]">
                      <tr>
                        <th className="p-2.5">序号</th>
                        <th className="p-2.5">事件类型</th>
                        <th className="p-2.5">阶段</th>
                        <th className="p-2.5">Payload 负载详情</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-rf-border/40">
                      {runEvents?.map((ev) => (
                        <tr key={ev.seq} className="hover:bg-rf-panel/40">
                          <td className="p-2.5 text-zinc-500">#{ev.seq}</td>
                          <td className="p-2.5 text-cyan-400 font-semibold">{ev.type}</td>
                          <td className="p-2.5 text-zinc-300">{ev.stage_id || '-'}</td>
                          <td className="p-2.5 text-zinc-300 break-all select-all">
                            {JSON.stringify(ev.payload)}
                          </td>
                        </tr>
                      ))}
                      {!runEvents?.length && (
                        <tr>
                          <td colSpan={4} className="p-8 text-center text-zinc-600">
                            暂无结构化事件
                          </td>
                        </tr>
                      )}
                    </tbody>
                  </table>
                </div>
              )}

              {activeTab === 'params' && (
                <div className="h-full bg-rf-bg border border-rf-border rounded-lg p-4 overflow-y-auto space-y-4 font-mono text-xs">
                  <div>
                    <h4 className="text-zinc-400 font-bold mb-2">CLI 启动参数快照 (Parameters)</h4>
                    <pre className="p-3 bg-rf-panel/60 rounded border border-rf-border text-cyan-300 overflow-x-auto">
                      {JSON.stringify(currentRun.command_args, null, 2)}
                    </pre>
                  </div>
                  <div>
                    <h4 className="text-zinc-400 font-bold mb-2">执行参数列表 (Argv Array)</h4>
                    <pre className="p-3 bg-rf-panel/60 rounded border border-rf-border text-zinc-300 overflow-x-auto">
                      {JSON.stringify(currentRun.cli_argv, null, 2)}
                    </pre>
                  </div>
                </div>
              )}

              {activeTab === 'artifacts' && (
                <div className="h-full bg-rf-bg border border-rf-border rounded-lg overflow-y-auto font-mono text-xs divide-y divide-rf-border/40">
                  {runArtifacts?.map((art) => (
                    <div
                      key={art.id}
                      className="p-3 flex items-center justify-between hover:bg-rf-panel/40"
                    >
                      <div className="space-y-1">
                        <div className="flex items-center space-x-2 text-rf-text font-bold">
                          <FileCode className="w-4 h-4 text-cyan-400" />
                          <span>{art.path.split(/[\\/]/).pop()}</span>
                          <span className="text-[10px] px-1 rounded bg-rf-panel text-zinc-400">
                            {art.kind}
                          </span>
                        </div>
                        <div className="text-[11px] text-zinc-500">{art.path}</div>
                        {art.file_state && <div className={art.file_state.readable ? 'text-zinc-500' : 'text-red-400'}>
                          {fileStateLabel(art.file_state)}{art.file_state.message ? `：${art.file_state.message}` : ''}
                        </div>}
                      </div>

                      <div className="flex items-center space-x-3">
                        <a
                          href={fileReadable(art.file_state) ? `/api/v1/artifacts/${art.id}` : undefined}
                          aria-disabled={!fileReadable(art.file_state)}
                          download
                          className="px-3 py-1 rounded bg-rf-panel hover:bg-rf-border border border-rf-border text-rf-accent text-xs flex items-center space-x-1"
                        >
                          <Download className="w-3.5 h-3.5" />
                          <span>{fileReadable(art.file_state) ? '下载文件' : fileStateLabel(art.file_state)}</span>
                        </a>
                      </div>
                    </div>
                  ))}
                  {!runArtifacts?.length && (
                    <div className="p-12 text-center text-zinc-600">
                      该任务尚未产生或记录文件产物
                    </div>
                  )}
                </div>
              )}
            </div>
          </>
        ) : (
          <div className="p-12 text-center text-zinc-600 font-mono">{runError?.message || '请在左侧选择要查看的任务'}</div>
        )}
      </div>
      {deleteIds.length > 0 && <div className="fixed inset-0 z-50 bg-black/70 flex items-center justify-center p-6">
        <div role="alertdialog" aria-modal="true" aria-labelledby="delete-runs-title" aria-describedby="delete-runs-description"
          className="bg-rf-surface border border-rf-border rounded-lg p-5 max-w-lg w-full space-y-4">
          <h3 id="delete-runs-title" className="font-bold">删除 {deleteIds.length} 个任务？</h3>
          <p id="delete-runs-description" className="text-sm text-zinc-400">将移除任务历史、日志、事件和产物列表关联。磁盘上的 IQ、JSONL 等文件会保留。任务记录删除后无法在界面恢复。</p>
          <ul className="text-xs text-zinc-500 space-y-1 break-all">{deleteIds.slice(0, 3).map(id => <li key={id}>{runTitle(runs?.find(run => run.id === id) || (currentRun?.id === id ? currentRun : { command_id: id, command_args: {} }))} · {id}</li>)}</ul>
          {deleteMutation.error && <p role="alert" className="text-sm text-red-400">{deleteMutation.error.message}</p>}
          <div className="flex justify-end gap-3 text-sm">
            <button disabled={deleteMutation.isPending} onClick={() => setDeleteIds([])} className="px-3 py-1.5 text-zinc-400">取消</button>
            <button autoFocus disabled={deleteMutation.isPending} onClick={() => deleteMutation.mutate(deleteIds)}
              className="px-3 py-1.5 bg-red-700 hover:bg-red-600 rounded text-white disabled:opacity-50">{deleteMutation.isPending ? '正在删除…' : '确认删除记录'}</button>
          </div>
        </div>
      </div>}
    </div>
  );
};
