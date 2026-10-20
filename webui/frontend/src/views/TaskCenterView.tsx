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
} from 'lucide-react';
import { api } from '../api/client';
import { useWorkstationStore } from '../stores/useWorkstationStore';
import { useWebSocketStore } from '../stores/useWebSocketStore';

export const TaskCenterView: React.FC = () => {
  const queryClient = useQueryClient();
  const { selectedRunId, setSelectedRunId } = useWorkstationStore();
  const { logs: liveLogs } = useWebSocketStore();

  const [filterStatus, setFilterStatus] = useState<string>('ALL');
  const [searchTerm, setSearchTerm] = useState<string>('');
  const [activeTab, setActiveTab] = useState<'logs' | 'events' | 'params' | 'artifacts'>('logs');
  const [logFilter, setLogFilter] = useState<'all' | 'stdout' | 'stderr'>('all');
  const [copied, setCopied] = useState(false);

  const terminalRef = useRef<HTMLDivElement>(null);

  const { data: runs, refetch: refetchRuns } = useQuery({
    queryKey: ['runs'],
    queryFn: () => api.getRuns(100, 0),
    refetchInterval: 3000,
  });

  const activeRunId = selectedRunId || runs?.[0]?.id || '';
  const currentRun = runs?.find((r) => r.id === activeRunId);

  // 历史日志与事件查询
  const { data: historyLogs } = useQuery({
    queryKey: ['runLogs', activeRunId],
    queryFn: () => api.getRunLogs(activeRunId, 2000),
    enabled: Boolean(activeRunId),
  });

  const { data: runEvents } = useQuery({
    queryKey: ['runEvents', activeRunId],
    queryFn: () => api.getRunEvents(activeRunId),
    enabled: Boolean(activeRunId),
    refetchInterval: currentRun?.status === 'RUNNING' ? 2000 : false,
  });

  const { data: runArtifacts } = useQuery({
    queryKey: ['runArtifacts', activeRunId],
    queryFn: () => api.getRunArtifacts(activeRunId),
    enabled: Boolean(activeRunId),
  });

  // 合并实时推送的日志与历史持久化日志
  const combinedLogs = React.useMemo(() => {
    if (!activeRunId) return [];
    const streamLogs = liveLogs[activeRunId] || [];
    if (streamLogs.length > 0) return streamLogs;
    return historyLogs || [];
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
    },
  });

  const filteredRuns = runs?.filter((r) => {
    const matchesFilter = filterStatus === 'ALL' || r.status === filterStatus;
    const matchesSearch =
      r.id.toLowerCase().includes(searchTerm.toLowerCase()) ||
      r.command_id.toLowerCase().includes(searchTerm.toLowerCase());
    return matchesFilter && matchesSearch;
  });

  const handleCopyLogs = () => {
    const text = combinedLogs.map((l) => `[${l.stream.toUpperCase()}] ${l.line}`).join('\n');
    navigator.clipboard.writeText(text);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <div className="flex h-[calc(100vh-3.5rem)] overflow-hidden select-none">
      {/* 左侧：任务列表 */}
      <div className="w-80 bg-rf-surface border-r border-rf-border flex flex-col">
        <div className="p-3 border-b border-rf-border space-y-2">
          <div className="relative">
            <Search className="w-4 h-4 text-zinc-500 absolute left-2.5 top-2.5" />
            <input
              type="text"
              placeholder="搜索任务 ID / 命令..."
              value={searchTerm}
              onChange={(e) => setSearchTerm(e.target.value)}
              className="w-full bg-rf-bg border border-rf-border rounded pl-8 pr-3 py-1.5 text-xs text-rf-text placeholder-zinc-500 focus:outline-none focus:border-rf-accent"
            />
          </div>

          <div className="flex space-x-1 font-mono text-[10px]">
            {['ALL', 'RUNNING', 'SUCCEEDED', 'FAILED'].map((st) => (
              <button
                key={st}
                onClick={() => setFilterStatus(st)}
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
        </div>

        {/* 任务项 */}
        <div className="flex-1 overflow-y-auto divide-y divide-rf-border/40 font-mono text-xs">
          {filteredRuns?.map((run) => {
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
                  <span className="font-semibold text-rf-text">{run.command_id}</span>
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
                <div className="text-[10px] text-zinc-400 mt-1 flex justify-between">
                  <span>退出码: {run.exit_code ?? '-'}</span>
                  <span>{run.created_at.split('T')[1].split('.')[0]}</span>
                </div>
              </div>
            );
          })}
        </div>
      </div>

      {/* 右侧：任务详情与终端 */}
      <div className="flex-1 flex flex-col overflow-hidden bg-rf-bg">
        {currentRun ? (
          <>
            {/* 任务头部摘要栏 */}
            <div className="p-4 bg-rf-surface border-b border-rf-border flex items-center justify-between">
              <div className="space-y-1">
                <div className="flex items-center space-x-2">
                  <h2 className="text-sm font-bold text-rf-text">{currentRun.command_id}</h2>
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
                  <span>创建: {currentRun.created_at}</span>
                  {currentRun.completed_at && <span>结束: {currentRun.completed_at}</span>}
                  {currentRun.requires_usrp && <span className="text-amber-400">USRP 独占</span>}
                </div>
              </div>

              {currentRun.status === 'RUNNING' && (
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
                      </div>

                      <div className="flex items-center space-x-3">
                        <a
                          href={`/api/v1/artifacts/${art.id}`}
                          download
                          className="px-3 py-1 rounded bg-rf-panel hover:bg-rf-border border border-rf-border text-rf-accent text-xs flex items-center space-x-1"
                        >
                          <Download className="w-3.5 h-3.5" />
                          <span>下载文件</span>
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
          <div className="p-12 text-center text-zinc-600 font-mono">请在左侧选择要查看的任务</div>
        )}
      </div>
    </div>
  );
};
