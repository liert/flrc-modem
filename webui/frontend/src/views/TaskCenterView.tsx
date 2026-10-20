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
  ChevronLeft,
  ChevronRight,
  Cpu,
  AlertCircle,
  Radio,
  FileText,
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
      <div className="w-96 shrink-0 bg-rf-surface/95 border-r border-rf-border flex flex-col shadow-lg">
        {/* 顶部检索与批量工具栏 */}
        <div className="p-3.5 border-b border-rf-border/80 bg-rf-panel/30 space-y-2.5">
          <div className="relative">
            <Search className="w-4 h-4 text-zinc-400 absolute left-2.5 top-2.5" />
            <input
              type="text"
              placeholder="搜索名称 / 文件 / 配置 / 日期 / ID…"
              maxLength={200}
              value={searchTerm}
              onChange={(e) => { setSearchTerm(e.target.value); changePage(0); }}
              className="w-full bg-rf-bg/90 border border-rf-border rounded-lg pl-8 pr-3 py-1.5 text-xs text-rf-text placeholder-zinc-500 focus:outline-none focus:border-cyan-400 focus:ring-1 focus:ring-cyan-400/40 transition-all font-mono"
            />
          </div>

          {/* 状态过滤胶囊 */}
          <div className="flex flex-wrap gap-1 font-mono text-[10px]">
            {['ALL', 'QUEUED', 'RUNNING', 'SUCCEEDED', 'FAILED', 'CANCELLED'].map((st) => (
              <button
                key={st}
                onClick={() => { setFilterStatus(st); changePage(0); }}
                className={`px-2 py-0.5 rounded-md transition-all font-semibold ${
                  filterStatus === st
                    ? 'bg-cyan-500/20 text-cyan-300 border border-cyan-400/50 shadow-sm shadow-cyan-500/20'
                    : 'bg-rf-panel/60 text-zinc-400 hover:text-rf-text hover:bg-rf-panel border border-rf-border/50'
                }`}
              >
                {st}
              </button>
            ))}
          </div>

          {/* 批量操作工具栏 */}
          <div className="flex items-center justify-between text-xs text-zinc-300 pt-0.5 px-0.5">
            <label className="flex items-center gap-2 cursor-pointer font-mono text-[11px] select-none hover:text-zinc-200">
              <input
                type="checkbox"
                aria-label="选择本页已结束任务"
                disabled={!deletableIds.length || deleteMutation.isPending}
                checked={deletableIds.length > 0 && selectedIds.length === deletableIds.length}
                onChange={event => setCheckedIds(event.target.checked ? deletableIds : [])}
                className="w-3.5 h-3.5 rounded bg-rf-bg border-rf-border text-cyan-400 focus:ring-0"
              />
              <span>本页已结束任务</span>
            </label>
            <button
              disabled={!selectedIds.length || deleteMutation.isPending}
              onClick={() => { deleteMutation.reset(); setDeleteIds(selectedIds); }}
              className="text-red-400 hover:text-red-300 disabled:opacity-40 font-mono text-[11px] flex items-center space-x-1 transition-colors px-2 py-0.5 rounded hover:bg-red-950/30"
            >
              <Trash2 className="w-3 h-3" />
              <span>删除选中 ({selectedIds.length})</span>
            </button>
          </div>
          {runsError && <p role="alert" className="text-xs text-red-400 font-mono">{runsError.message}</p>}
        </div>

        {/* 任务项列表 */}
        <div className="flex-1 overflow-y-auto divide-y divide-rf-border/30 font-mono text-xs">
          {runs?.map((run) => {
            const isSelected = run.id === activeRunId;
            return (
              <div
                key={run.id}
                onClick={() => setSelectedRunId(run.id)}
                className={`p-3.5 cursor-pointer transition-all relative ${
                  isSelected
                    ? 'bg-gradient-to-r from-cyan-500/15 via-cyan-500/5 to-transparent border-l-2 border-cyan-400'
                    : 'hover:bg-rf-panel/40 border-l-2 border-transparent'
                }`}
              >
                <div className="flex items-center justify-between mb-1.5">
                  <div className="flex items-center gap-2 min-w-0 pr-2">
                    <input
                      type="checkbox"
                      aria-label={`选择任务 ${runTitle(run)}`}
                      disabled={!runDeletable(run) || deleteMutation.isPending}
                      checked={selectedIds.includes(run.id)}
                      onClick={event => event.stopPropagation()}
                      onChange={event => setCheckedIds(ids => event.target.checked ? [...ids, run.id] : ids.filter(id => id !== run.id))}
                      className="w-3.5 h-3.5 rounded bg-rf-bg border-rf-border text-cyan-400 focus:ring-0 shrink-0"
                    />
                    <span title={runTitle(run)} className="font-bold text-rf-text truncate text-xs tracking-tight">
                      {runTitle(run)}
                    </span>
                  </div>
                  <span
                    className={`px-1.5 py-0.2 rounded text-[10px] font-bold shrink-0 ${
                      run.status === 'SUCCEEDED'
                        ? 'bg-emerald-950/70 text-emerald-400 border border-emerald-800/80 shadow-sm'
                        : run.status === 'RUNNING'
                        ? 'bg-cyan-950/70 text-cyan-400 border border-cyan-800/80 animate-pulse shadow-sm shadow-cyan-500/20'
                        : run.status === 'FAILED'
                        ? 'bg-red-950/70 text-red-400 border border-red-800/80 shadow-sm'
                        : 'bg-zinc-800/80 text-zinc-400 border border-zinc-700/60'
                    }`}
                  >
                    {run.status}
                  </span>
                </div>
                <div className="text-[11px] text-zinc-500 truncate mb-1">
                  ID: {run.id}
                </div>
                <div className="text-[11px] text-zinc-400 truncate flex items-center space-x-1" title={run.command_args.iq || run.command_args.input || run.command_args.file || run.command_args.output}>
                  <span className="text-cyan-500/80 font-bold">{run.command_id}</span>
                  <span className="text-zinc-600">·</span>
                  <span className="truncate">{run.command_args.iq || run.command_args.input || run.command_args.file || run.command_args.output || '无关联文件'}</span>
                </div>
                <div className="text-[11px] text-zinc-500 mt-1.5 flex justify-between items-center pt-1 border-t border-rf-border/20">
                  <span className={run.exit_code === 0 ? 'text-emerald-400/80' : run.exit_code != null ? 'text-red-400' : 'text-zinc-500'}>
                    退出码: {run.exit_code ?? '-'}
                  </span>
                  <span>{runTime(run.created_at)}</span>
                </div>
              </div>
            );
          })}
          {!runs?.length && !fetchingRuns && (
            <div className="p-10 text-zinc-500 text-center font-mono text-xs">
              没有符合条件的任务
            </div>
          )}
        </div>

        {/* 分页控制栏 */}
        <div className="p-3 border-t border-rf-border bg-rf-panel/20 flex items-center justify-between text-xs text-zinc-300 font-mono">
          <button
            disabled={page === 0 || fetchingRuns}
            className="px-2.5 py-1 rounded bg-rf-panel hover:bg-rf-border text-zinc-300 disabled:opacity-30 disabled:hover:bg-rf-panel flex items-center space-x-1 transition-all"
            onClick={() => changePage(page - 1)}
          >
            <ChevronLeft className="w-3.5 h-3.5" />
            <span>上一页</span>
          </button>
          <span className="text-[11px] text-zinc-400">
            第 {page + 1} 页 · 本页 {runs?.length || 0} 项
          </span>
          <button
            disabled={!hasMore || fetchingRuns}
            className="px-2.5 py-1 rounded bg-rf-panel hover:bg-rf-border text-zinc-300 disabled:opacity-30 disabled:hover:bg-rf-panel flex items-center space-x-1 transition-all"
            onClick={() => changePage(page + 1)}
          >
            <span>下一页</span>
            <ChevronRight className="w-3.5 h-3.5" />
          </button>
        </div>
      </div>

      {/* 右侧：任务详情与终端 */}
      <div className="flex-1 flex flex-col overflow-hidden bg-rf-bg">
        {currentRun ? (
          <>
            {/* 任务头部摘要栏 */}
            <div className="p-4 bg-rf-surface border-b border-rf-border flex flex-wrap items-center justify-between gap-3 shadow-sm relative overflow-hidden">
              <div className="space-y-1.5 min-w-0">
                <div className="flex flex-wrap items-center gap-2.5">
                  {editingName ? (
                    <form className="flex items-center gap-2" onSubmit={event => {
                      event.preventDefault(); renameMutation.mutate({ id: currentRun.id, name: draftName });
                    }}>
                      <input
                        autoFocus
                        aria-label="任务名称"
                        maxLength={120}
                        value={draftName}
                        placeholder="留空恢复自动名称"
                        onChange={event => setDraftName(event.target.value)}
                        className="bg-rf-bg border border-cyan-500/50 rounded-lg px-2.5 py-1 text-xs w-72 text-rf-text font-mono focus:outline-none focus:ring-1 focus:ring-cyan-400"
                      />
                      <button disabled={renameMutation.isPending} className="px-2.5 py-1 rounded bg-cyan-500 hover:bg-cyan-400 text-black text-xs font-bold transition-all">保存</button>
                      <button type="button" disabled={renameMutation.isPending} onClick={() => setEditingName(false)} className="px-2.5 py-1 rounded bg-rf-panel hover:bg-rf-border text-xs text-zinc-300 transition-all">取消</button>
                    </form>
                  ) : (
                    <>
                      <h2 className="text-base font-bold text-rf-text break-all tracking-wide flex items-center space-x-2">
                        <span>{runTitle(currentRun)}</span>
                      </h2>
                      <button
                        title="重命名任务"
                        aria-label="重命名任务"
                        onClick={() => {
                          renameMutation.reset(); setDraftName(currentRun.name || ''); setEditingName(true);
                        }}
                        className="p-1 rounded hover:bg-rf-panel text-zinc-400 hover:text-cyan-300 transition-colors"
                      >
                        <Pencil className="w-3.5 h-3.5" />
                      </button>
                    </>
                  )}
                  <span className="text-xs font-mono text-zinc-400 bg-rf-panel/80 px-2 py-0.5 rounded border border-rf-border/60">
                    {currentRun.id}
                  </span>
                  <span
                    className={`px-2 py-0.5 rounded text-[10px] font-bold font-mono ${
                      currentRun.status === 'SUCCEEDED'
                        ? 'bg-emerald-950/70 text-emerald-400 border border-emerald-800/80 shadow-sm'
                        : currentRun.status === 'RUNNING'
                        ? 'bg-cyan-950/70 text-cyan-400 border border-cyan-800/80 animate-pulse shadow-sm shadow-cyan-500/20'
                        : 'bg-red-950/70 text-red-400 border border-red-800/80 shadow-sm'
                    }`}
                  >
                    {currentRun.status}
                  </span>
                </div>
                <div className="text-xs font-mono text-zinc-400 flex items-center space-x-4">
                  <span className="flex items-center space-x-1">
                    <Clock className="w-3.5 h-3.5 text-zinc-500" />
                    <span>创建: {runTime(currentRun.created_at)}</span>
                  </span>
                  {currentRun.completed_at && <span>结束: {runTime(currentRun.completed_at)}</span>}
                  {currentRun.requires_usrp && (
                    <span className="text-amber-400 flex items-center space-x-1 font-semibold">
                      <Cpu className="w-3.5 h-3.5" />
                      <span>USRP 硬件独占</span>
                    </span>
                  )}
                </div>
              </div>

              <div className="flex items-center gap-2.5">
                {runDeletable(currentRun) && (
                  <button
                    disabled={deleteMutation.isPending}
                    onClick={() => {
                      deleteMutation.reset(); setDeleteIds([currentRun.id]);
                    }}
                    className="flex gap-1.5 items-center text-xs font-mono text-red-400 hover:text-red-300 hover:bg-red-950/30 px-3 py-1.5 rounded-lg border border-red-900/60 transition-colors"
                  >
                    <Trash2 className="w-3.5 h-3.5" />
                    <span>删除任务</span>
                  </button>
                )}
                {['RUNNING', 'QUEUED'].includes(currentRun.status) && (
                  <button
                    onClick={() => stopMutation.mutate(currentRun.id)}
                    disabled={stopMutation.isPending}
                    className="px-4 py-2 rounded-lg bg-gradient-to-r from-red-600 to-red-500 hover:from-red-500 hover:to-red-400 text-white font-bold text-xs flex items-center space-x-1.5 shadow-lg shadow-red-600/30 active:scale-[0.98] transition-all"
                  >
                    <StopCircle className="w-4 h-4" />
                    <span>终止任务 (Stop Process)</span>
                  </button>
                )}
              </div>
            </div>
            {(renameMutation.error || stopMutation.error) && (
              <p role="alert" className="p-3 text-xs text-red-400 font-mono bg-red-950/30 border-b border-red-800/40">
                {(renameMutation.error || stopMutation.error)?.message}
              </p>
            )}

            {/* 选项卡栏 */}
            <div className="flex border-b border-rf-border bg-rf-panel/40 px-4">
              {[
                { id: 'logs', label: '实时终端日志 (Logs)' },
                { id: 'events', label: '结构化事件 (Events)' },
                { id: 'params', label: '参数快照 (Params)' },
                { id: 'artifacts', label: `生成产物 (${runArtifacts?.length || 0})` },
              ].map((tab) => (
                <button
                  key={tab.id}
                  onClick={() => setActiveTab(tab.id as any)}
                  className={`px-4 py-2.5 text-xs font-mono font-medium border-b-2 transition-all ${
                    activeTab === tab.id
                      ? 'border-cyan-400 text-cyan-300 bg-cyan-500/10 font-bold'
                      : 'border-transparent text-zinc-400 hover:text-rf-text hover:bg-rf-panel/30'
                  }`}
                >
                  {tab.label}
                </button>
              ))}
            </div>

            {/* 选项卡内容区 */}
            <div className="flex-1 overflow-hidden p-4">
              {activeTab === 'logs' && (
                <div className="h-full flex flex-col bg-black/70 border border-rf-border rounded-xl overflow-hidden shadow-inner">
                  <div className="p-2.5 border-b border-rf-border/80 bg-rf-panel/60 flex items-center justify-between text-xs font-mono">
                    <div className="flex items-center space-x-2">
                      <Terminal className="w-3.5 h-3.5 text-cyan-400" />
                      <span className="text-zinc-400">输出流:</span>
                      {(['all', 'stdout', 'stderr'] as const).map((filter) => (
                        <button
                          key={filter}
                          onClick={() => setLogFilter(filter)}
                          className={`px-2 py-0.5 rounded text-[10px] uppercase font-semibold transition-all ${
                            logFilter === filter
                              ? 'bg-cyan-500/20 text-cyan-300 border border-cyan-400/50 shadow-sm'
                              : 'text-zinc-400 hover:text-zinc-200 hover:bg-rf-panel/80'
                          }`}
                        >
                          {filter}
                        </button>
                      ))}
                    </div>

                    <button
                      onClick={handleCopyLogs}
                      className="px-2.5 py-1 rounded bg-rf-panel hover:bg-rf-border text-zinc-300 text-xs flex items-center space-x-1.5 transition-colors border border-rf-border/60"
                    >
                      {copied ? <Check className="w-3.5 h-3.5 text-emerald-400" /> : <Copy className="w-3.5 h-3.5 text-cyan-400" />}
                      <span>{copied ? '已复制' : '复制日志'}</span>
                    </button>
                  </div>

                  <div
                    ref={terminalRef}
                    className="flex-1 p-3.5 overflow-y-auto font-mono text-xs leading-relaxed space-y-1 select-text scroll-smooth"
                  >
                    {combinedLogs
                      .filter((l) => logFilter === 'all' || l.stream === logFilter)
                      .map((log, idx) => (
                        <div
                          key={idx}
                          className={`flex space-x-2.5 ${
                            log.stream === 'stderr' ? 'text-amber-300/90' : 'text-zinc-300'
                          }`}
                        >
                          <span className="text-zinc-600 select-none shrink-0 font-mono text-[11px]">
                            {log.timestamp ? log.timestamp.split('T')[1].split('.')[0] : ''}
                          </span>
                          <span
                            className={`select-none uppercase text-[10px] px-1.5 py-0.2 rounded shrink-0 font-semibold ${
                              log.stream === 'stderr'
                                ? 'bg-amber-950/60 text-amber-300 border border-amber-800/60'
                                : 'bg-zinc-800/80 text-zinc-400'
                            }`}
                          >
                            {log.stream}
                          </span>
                          <span className="break-all whitespace-pre-wrap">{log.line}</span>
                        </div>
                      ))}
                    {!combinedLogs.length && (
                      <div className="text-zinc-600 text-center py-16 flex flex-col items-center space-y-2">
                        <Terminal className="w-8 h-8 text-zinc-700" />
                        <span>暂无日志输出</span>
                      </div>
                    )}
                  </div>
                </div>
              )}

              {activeTab === 'events' && (
                <div className="h-full bg-rf-surface border border-rf-border rounded-xl overflow-y-auto font-mono text-xs shadow-inner">
                  <table className="w-full text-left">
                    <thead className="bg-rf-panel/70 text-zinc-400 text-[11px] sticky top-0 border-b border-rf-border">
                      <tr>
                        <th className="p-3">序号</th>
                        <th className="p-3">事件类型</th>
                        <th className="p-3">阶段</th>
                        <th className="p-3">Payload 负载详情</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-rf-border/30">
                      {runEvents?.map((ev) => (
                        <tr key={ev.seq} className="hover:bg-rf-panel/40 transition-colors">
                          <td className="p-3 text-zinc-500 font-semibold">#{ev.seq}</td>
                          <td className="p-3 text-cyan-300 font-semibold">{ev.type}</td>
                          <td className="p-3 text-zinc-300">{ev.stage_id || '-'}</td>
                          <td className="p-3 text-zinc-300 break-all select-all font-mono">
                            {JSON.stringify(ev.payload)}
                          </td>
                        </tr>
                      ))}
                      {!runEvents?.length && (
                        <tr>
                          <td colSpan={4} className="p-12 text-center text-zinc-600">
                            暂无结构化事件
                          </td>
                        </tr>
                      )}
                    </tbody>
                  </table>
                </div>
              )}

              {activeTab === 'params' && (
                <div className="h-full bg-rf-surface border border-rf-border rounded-xl p-5 overflow-y-auto space-y-5 font-mono text-xs">
                  <div>
                    <h4 className="text-zinc-300 font-bold mb-2 flex items-center space-x-1.5">
                      <FileText className="w-4 h-4 text-cyan-400" />
                      <span>CLI 启动参数快照 (Parameters)</span>
                    </h4>
                    <pre className="p-3.5 bg-black/60 rounded-lg border border-rf-border text-cyan-300 overflow-x-auto shadow-inner leading-relaxed">
                      {JSON.stringify(currentRun.command_args, null, 2)}
                    </pre>
                  </div>
                  <div>
                    <h4 className="text-zinc-300 font-bold mb-2 flex items-center space-x-1.5">
                      <Terminal className="w-4 h-4 text-cyan-400" />
                      <span>执行参数列表 (Argv Array)</span>
                    </h4>
                    <pre className="p-3.5 bg-black/60 rounded-lg border border-rf-border text-zinc-300 overflow-x-auto shadow-inner leading-relaxed">
                      {JSON.stringify(currentRun.cli_argv, null, 2)}
                    </pre>
                  </div>
                </div>
              )}

              {activeTab === 'artifacts' && (
                <div className="h-full bg-rf-surface border border-rf-border rounded-xl overflow-y-auto font-mono text-xs divide-y divide-rf-border/30">
                  {runArtifacts?.map((art) => (
                    <div
                      key={art.id}
                      className="p-3.5 flex items-center justify-between hover:bg-rf-panel/40 transition-colors"
                    >
                      <div className="space-y-1">
                        <div className="flex items-center space-x-2 text-rf-text font-bold text-xs">
                          <FileCode className="w-4 h-4 text-cyan-400" />
                          <span>{art.path.split(/[\\/]/).pop()}</span>
                          <span className="text-[10px] px-1.5 py-0.2 rounded bg-rf-panel text-zinc-400 border border-rf-border/50 uppercase">
                            {art.kind}
                          </span>
                        </div>
                        <div className="text-[11px] text-zinc-500 font-mono">{art.path}</div>
                        {art.file_state && (
                          <div className={art.file_state.readable ? 'text-zinc-400 text-[11px]' : 'text-red-400 text-[11px]'}>
                            {fileStateLabel(art.file_state)}{art.file_state.message ? `：${art.file_state.message}` : ''}
                          </div>
                        )}
                      </div>

                      <div className="flex items-center space-x-3">
                        <a
                          href={fileReadable(art.file_state) ? `/api/v1/artifacts/${art.id}` : undefined}
                          aria-disabled={!fileReadable(art.file_state)}
                          download
                          className={`px-3 py-1.5 rounded-lg border text-xs flex items-center space-x-1.5 font-mono transition-all ${
                            fileReadable(art.file_state)
                              ? 'bg-cyan-500/10 hover:bg-cyan-500/20 text-cyan-300 border-cyan-500/30'
                              : 'bg-rf-panel text-zinc-500 border-rf-border opacity-50 cursor-not-allowed'
                          }`}
                        >
                          <Download className="w-3.5 h-3.5" />
                          <span>{fileReadable(art.file_state) ? '下载文件' : fileStateLabel(art.file_state)}</span>
                        </a>
                      </div>
                    </div>
                  ))}
                  {!runArtifacts?.length && (
                    <div className="p-16 text-center text-zinc-600 font-mono">
                      该任务尚未产生或记录文件产物
                    </div>
                  )}
                </div>
              )}
            </div>
          </>
        ) : (
          <div className="p-16 text-center text-zinc-500 font-mono">
            {runError?.message || '请在左侧选择要查看的任务'}
          </div>
        )}
      </div>

      {/* 删除确认弹窗 */}
      {deleteIds.length > 0 && (
        <div className="fixed inset-0 z-50 bg-black/75 backdrop-blur-sm flex items-center justify-center p-6 select-text">
          <div
            role="alertdialog"
            aria-modal="true"
            aria-labelledby="delete-runs-title"
            aria-describedby="delete-runs-description"
            className="bg-rf-surface border border-rf-border rounded-xl p-6 max-w-lg w-full space-y-4 shadow-2xl relative overflow-hidden"
          >
            <div className="absolute top-0 left-0 right-0 h-1 bg-red-500" />
            <div className="flex items-center space-x-2 text-red-400">
              <AlertTriangle className="w-5 h-5 shrink-0" />
              <h3 id="delete-runs-title" className="font-bold text-base text-rf-text">
                删除 {deleteIds.length} 个任务？
              </h3>
            </div>
            <p id="delete-runs-description" className="text-xs text-zinc-300 leading-relaxed font-sans">
              将移除任务历史、日志、事件和产物列表关联。磁盘上的 IQ、JSONL 等文件会保留。任务记录删除后无法在界面恢复。
            </p>
            <ul className="text-xs text-zinc-400 font-mono space-y-1.5 break-all max-h-36 overflow-y-auto bg-black/40 p-3 rounded-lg border border-rf-border">
              {deleteIds.slice(0, 3).map(id => (
                <li key={id} className="flex items-center space-x-1.5">
                  <span className="text-red-400">•</span>
                  <span>{runTitle(runs?.find(run => run.id === id) || (currentRun?.id === id ? currentRun : { command_id: id, command_args: {} }))} · {id}</span>
                </li>
              ))}
            </ul>
            {deleteMutation.error && (
              <p role="alert" className="text-xs text-red-400 font-mono bg-red-950/40 p-2 rounded border border-red-800/60">
                {deleteMutation.error.message}
              </p>
            )}
            <div className="flex justify-end gap-3 text-xs font-mono pt-2">
              <button
                disabled={deleteMutation.isPending}
                onClick={() => setDeleteIds([])}
                className="px-4 py-2 rounded-lg bg-rf-panel hover:bg-rf-border text-zinc-300 transition-colors"
              >
                取消
              </button>
              <button
                autoFocus
                disabled={deleteMutation.isPending}
                onClick={() => deleteMutation.mutate(deleteIds)}
                className="px-4 py-2 bg-red-600 hover:bg-red-500 rounded-lg text-white font-bold transition-all shadow-md shadow-red-600/30 disabled:opacity-50"
              >
                {deleteMutation.isPending ? '正在删除…' : '确认删除记录'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};
