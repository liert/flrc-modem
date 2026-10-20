import React, { useState, useEffect } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  Search,
  Terminal,
  Play,
  Bookmark,
  Save,
  CheckCircle2,
  AlertCircle,
  Copy,
  Check,
  Cpu,
  Layers,
  ArrowRight,
} from 'lucide-react';
import { api } from '../api/client';
import { CommandDefinition, CommandParameter, PresetConfig } from '../types';
import { useWorkstationStore } from '../stores/useWorkstationStore';

export const CommandLibraryView: React.FC = () => {
  const queryClient = useQueryClient();
  const { selectedCommandId, setSelectedCommandId, setSelectedRunId, setActiveTab } =
    useWorkstationStore();

  const [searchTerm, setSearchTerm] = useState('');
  const [selectedCategory, setSelectedCategory] = useState<string>('全部');
  const [formData, setFormData] = useState<Record<string, any>>({});
  const [presetName, setPresetName] = useState('');
  const [copied, setCopied] = useState(false);
  const [runError, setRunError] = useState<string | null>(null);

  const { data: commands } = useQuery({
    queryKey: ['commands'],
    queryFn: api.getCommands,
  });

  const { data: presets, refetch: refetchPresets } = useQuery({
    queryKey: ['presets', selectedCommandId],
    queryFn: () => api.getPresets(selectedCommandId),
  });

  const activeCommand: CommandDefinition | undefined = commands?.find(
    (c) => c.id === selectedCommandId
  );

  // 初始化参数表单默认值
  useEffect(() => {
    if (activeCommand) {
      const initial: Record<string, any> = {};
      activeCommand.parameters.forEach((param) => {
        initial[param.name] = param.default ?? '';
      });
      setFormData(initial);
      setRunError(null);
    }
  }, [activeCommand]);

  const categories = ['全部', ...Array.from(new Set(commands?.map((c) => c.category) || []))];

  const filteredCommands = commands?.filter((cmd) => {
    const matchesSearch =
      cmd.name.toLowerCase().includes(searchTerm.toLowerCase()) ||
      cmd.description.toLowerCase().includes(searchTerm.toLowerCase()) ||
      cmd.subcommand.toLowerCase().includes(searchTerm.toLowerCase());
    const matchesCategory = selectedCategory === '全部' || cmd.category === selectedCategory;
    return matchesSearch && matchesCategory;
  });

  // 动态构建即将执行的 CLI 命令预览
  const generateCLIPreview = () => {
    if (!activeCommand) return '';
    const parts = ['python', '-m', 'flrc_modem', activeCommand.subcommand];
    activeCommand.parameters.forEach((param) => {
      const val = formData[param.name];
      if (val !== undefined && val !== '' && val !== null) {
        if (param.type === 'boolean') {
          if (val) parts.push(param.cli_flag);
        } else if (param.cli_flag) {
          parts.push(param.cli_flag, String(val));
        } else {
          // positional
          parts.push(String(val));
        }
      }
    });
    parts.push('--ui-events', 'ndjson');
    return parts.join(' ');
  };

  const handleCopyCLI = () => {
    navigator.clipboard.writeText(generateCLIPreview());
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const runMutation = useMutation({
    mutationFn: () => api.createRun(selectedCommandId, formData, true),
    onSuccess: (run) => {
      queryClient.invalidateQueries({ queryKey: ['runs'] });
      setSelectedRunId(run.id);
      setActiveTab('tasks');
    },
    onError: (err: any) => {
      setRunError(err.message || '任务启动失败');
    },
  });

  const savePresetMutation = useMutation({
    mutationFn: () => api.savePreset(presetName, selectedCommandId, formData),
    onSuccess: () => {
      refetchPresets();
      setPresetName('');
    },
  });

  const handleApplyPreset = (preset: PresetConfig) => {
    setFormData((prev) => ({ ...prev, ...preset.values }));
  };

  return (
    <div className="flex h-[calc(100vh-3.5rem)] overflow-hidden">
      {/* 左侧：命令目录检索 */}
      <div className="w-80 bg-rf-surface border-r border-rf-border flex flex-col select-none">
        <div className="p-3 border-b border-rf-border space-y-2">
          <div className="relative">
            <Search className="w-4 h-4 text-zinc-500 absolute left-2.5 top-2.5" />
            <input
              type="text"
              placeholder="搜索命令或功能..."
              value={searchTerm}
              onChange={(e) => setSearchTerm(e.target.value)}
              className="w-full bg-rf-bg border border-rf-border rounded pl-8 pr-3 py-1.5 text-xs text-rf-text placeholder-zinc-500 focus:outline-none focus:border-rf-accent"
            />
          </div>

          {/* 分类过滤芯片 */}
          <div className="flex flex-wrap gap-1">
            {categories.map((cat) => (
              <button
                key={cat}
                onClick={() => setSelectedCategory(cat)}
                className={`px-2 py-0.5 rounded text-[10px] font-mono transition-colors ${
                  selectedCategory === cat
                    ? 'bg-cyan-500/20 text-rf-accent border border-cyan-500/40'
                    : 'bg-rf-panel text-zinc-400 hover:text-rf-text'
                }`}
              >
                {cat}
              </button>
            ))}
          </div>
        </div>

        {/* 命令列表 */}
        <div className="flex-1 overflow-y-auto divide-y divide-rf-border/40">
          {filteredCommands?.map((cmd) => {
            const isSelected = cmd.id === selectedCommandId;
            return (
              <div
                key={cmd.id}
                onClick={() => setSelectedCommandId(cmd.id)}
                className={`p-3.5 cursor-pointer transition-colors text-left ${
                  isSelected ? 'bg-cyan-500/10 border-l-2 border-rf-accent' : 'hover:bg-rf-panel/60'
                }`}
              >
                <div className="flex items-center justify-between mb-1">
                  <span
                    className={`text-xs font-semibold ${
                      isSelected ? 'text-rf-accent' : 'text-rf-text'
                    }`}
                  >
                    {cmd.name}
                  </span>
                  {cmd.requires_sdr && (
                    <span className="text-[9px] px-1.5 py-0.2 rounded bg-amber-950/60 text-amber-400 border border-amber-800/50 font-mono">
                      SDR
                    </span>
                  )}
                </div>
                <div className="text-[11px] font-mono text-zinc-500 mb-1">
                  flrc {cmd.subcommand}
                </div>
                <div className="text-[11px] text-zinc-400 line-clamp-2 leading-relaxed">
                  {cmd.description}
                </div>
              </div>
            );
          })}
        </div>
      </div>

      {/* 右侧：参数表单生成与 CLI 预览调试 */}
      <div className="flex-1 overflow-y-auto p-6 space-y-6">
        {activeCommand ? (
          <div className="max-w-4xl mx-auto space-y-6">
            {/* 命令基本信息头部 */}
            <div className="bg-rf-surface border border-rf-border p-5 rounded-lg flex items-start justify-between">
              <div>
                <div className="flex items-center space-x-2">
                  <h2 className="text-base font-bold text-rf-text">{activeCommand.name}</h2>
                  <span className="text-xs font-mono px-2 py-0.5 rounded bg-rf-panel text-rf-accent border border-rf-border">
                    flrc {activeCommand.subcommand}
                  </span>
                  {activeCommand.requires_sdr && (
                    <span className="text-xs px-2 py-0.5 rounded bg-amber-950 text-amber-300 border border-amber-800 font-mono flex items-center space-x-1">
                      <Cpu className="w-3 h-3" />
                      <span>需要独占 SDR</span>
                    </span>
                  )}
                </div>
                <p className="text-xs text-zinc-400 mt-2 leading-relaxed">
                  {activeCommand.description}
                </p>
              </div>

              {activeCommand.pipeline_id && (
                <button
                  onClick={() => {
                    setActiveTab('workbench');
                  }}
                  className="px-3 py-1.5 rounded bg-rf-panel hover:bg-rf-border text-xs text-rf-accent border border-rf-border font-mono flex items-center space-x-1.5"
                >
                  <Layers className="w-3.5 h-3.5" />
                  <span>在工作台中查看数据流</span>
                </button>
              )}
            </div>

            {/* 预设管理栏 */}
            <div className="bg-rf-surface border border-rf-border p-3.5 rounded-lg flex items-center justify-between">
              <div className="flex items-center space-x-2">
                <Bookmark className="w-4 h-4 text-cyan-400" />
                <span className="text-xs font-medium text-rf-text">参数预设 (Presets):</span>
                {presets?.map((p) => (
                  <button
                    key={p.id}
                    onClick={() => handleApplyPreset(p)}
                    className="px-2.5 py-1 rounded bg-rf-panel hover:bg-cyan-950/40 border border-rf-border hover:border-cyan-500/50 text-[11px] font-mono text-zinc-300 transition-colors"
                  >
                    {p.name}
                  </button>
                ))}
                {!presets?.length && (
                  <span className="text-xs text-zinc-500 font-mono">暂无保存的预设</span>
                )}
              </div>

              <div className="flex items-center space-x-2">
                <input
                  type="text"
                  placeholder="新预设名称..."
                  value={presetName}
                  onChange={(e) => setPresetName(e.target.value)}
                  className="bg-rf-bg border border-rf-border rounded px-2.5 py-1 text-xs text-rf-text focus:outline-none focus:border-rf-accent font-mono w-32"
                />
                <button
                  onClick={() => savePresetMutation.mutate()}
                  disabled={!presetName}
                  className="px-2.5 py-1 rounded bg-cyan-600 hover:bg-cyan-500 disabled:opacity-40 text-black text-xs font-semibold flex items-center space-x-1"
                >
                  <Save className="w-3 h-3" />
                  <span>保存当前配置</span>
                </button>
              </div>
            </div>

            {/* 动态表单组件 */}
            <div className="bg-rf-surface border border-rf-border rounded-lg overflow-hidden">
              <div className="p-3.5 border-b border-rf-border bg-rf-panel/40">
                <h3 className="text-xs font-bold font-mono uppercase text-rf-text">
                  执行参数表单 (Structured Parameters)
                </h3>
              </div>

              <div className="p-5 grid grid-cols-1 md:grid-cols-2 gap-4">
                {activeCommand.parameters.map((param) => {
                  const val = formData[param.name];
                  return (
                    <div
                      key={param.name}
                      className={`space-y-1.5 ${
                        param.type === 'string' && param.name.includes('payload')
                          ? 'md:col-span-2'
                          : ''
                      }`}
                    >
                      <div className="flex items-center justify-between">
                        <label className="text-xs font-mono font-medium text-zinc-300 flex items-center space-x-1">
                          <span>{param.name}</span>
                          {param.required && <span className="text-red-400">*</span>}
                        </label>
                        <span className="text-[10px] font-mono text-zinc-500">
                          {param.cli_flag || '位置参数'}
                        </span>
                      </div>

                      {/* 控件渲染 */}
                      {param.type === 'choice' && param.choices ? (
                        <select
                          value={val}
                          onChange={(e) =>
                            setFormData({ ...formData, [param.name]: e.target.value })
                          }
                          className="w-full bg-rf-bg border border-rf-border rounded px-3 py-1.5 text-xs text-rf-text font-mono focus:outline-none focus:border-rf-accent"
                        >
                          {param.choices.map((c: any) => (
                            <option key={String(c)} value={String(c)}>
                              {String(c)}
                            </option>
                          ))}
                        </select>
                      ) : param.type === 'boolean' ? (
                        <label className="flex items-center space-x-2 text-xs text-zinc-300 cursor-pointer pt-1">
                          <input
                            type="checkbox"
                            checked={Boolean(val)}
                            onChange={(e) =>
                              setFormData({ ...formData, [param.name]: e.target.checked })
                            }
                            className="rounded bg-rf-bg border-rf-border text-cyan-500 focus:ring-0"
                          />
                          <span>启用此选项 ({param.cli_flag})</span>
                        </label>
                      ) : (
                        <input
                          type={param.type === 'number' || param.type === 'integer' ? 'number' : 'text'}
                          value={val}
                          placeholder={param.default !== undefined ? String(param.default) : ''}
                          onChange={(e) =>
                            setFormData({
                              ...formData,
                              [param.name]:
                                param.type === 'number'
                                  ? parseFloat(e.target.value) || e.target.value
                                  : param.type === 'integer'
                                  ? parseInt(e.target.value, 10) || e.target.value
                                  : e.target.value,
                            })
                          }
                          className="w-full bg-rf-bg border border-rf-border rounded px-3 py-1.5 text-xs text-rf-text font-mono focus:outline-none focus:border-rf-accent"
                        />
                      )}

                      <div className="text-[10px] text-zinc-500 leading-tight">
                        {param.description}
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>

            {/* CLI 预览与执行区 */}
            <div className="bg-rf-surface border border-rf-border rounded-lg p-4 space-y-3">
              <div className="flex items-center justify-between">
                <div className="flex items-center space-x-2 text-xs font-mono font-bold uppercase text-rf-text">
                  <Terminal className="w-4 h-4 text-cyan-400" />
                  <span>最终生成的 CLI 指令预览</span>
                </div>
                <button
                  onClick={handleCopyCLI}
                  className="px-2 py-1 rounded bg-rf-panel hover:bg-rf-border text-zinc-300 text-xs font-mono flex items-center space-x-1"
                >
                  {copied ? <Check className="w-3.5 h-3.5 text-emerald-400" /> : <Copy className="w-3.5 h-3.5" />}
                  <span>{copied ? '已复制' : '复制命令'}</span>
                </button>
              </div>

              <div className="bg-rf-bg p-3 rounded font-mono text-xs text-cyan-300 break-all select-all border border-rf-border">
                {generateCLIPreview()}
              </div>

              {runError && (
                <div className="p-3 rounded bg-red-950/50 border border-red-800 text-red-300 text-xs flex items-center space-x-2 font-mono">
                  <AlertCircle className="w-4 h-4 flex-shrink-0" />
                  <span>{runError}</span>
                </div>
              )}

              <div className="flex justify-end pt-2">
                <button
                  onClick={() => runMutation.mutate()}
                  disabled={runMutation.isPending}
                  className="px-6 py-2.5 rounded bg-cyan-500 hover:bg-cyan-400 text-black font-bold text-xs uppercase tracking-wider flex items-center space-x-2 shadow-lg shadow-cyan-500/20 transition-all disabled:opacity-50"
                >
                  <Play className="w-4 h-4 fill-current" />
                  <span>{runMutation.isPending ? '正在创建子进程...' : '执行该命令 (Run Task)'}</span>
                </button>
              </div>
            </div>
          </div>
        ) : (
          <div className="p-12 text-center text-zinc-500 font-mono">请在左侧选择命令</div>
        )}
      </div>
    </div>
  );
};
