import React, { useState, useEffect } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  Search,
  Terminal,
  Play,
  Save,
  AlertCircle,
  Copy,
  Check,
  Cpu,
  Layers,
  FileText,
  Sliders,
  SlidersHorizontal,
  Code2,
  Eye,
  Radio,
  FileCode,
  X,
  RotateCcw,
} from 'lucide-react';
import { api } from '../api/client';
import { CommandDefinition, TemplateItem } from '../types';
import { useWorkstationStore } from '../stores/useWorkstationStore';

export const CommandLibraryView: React.FC = () => {
  const queryClient = useQueryClient();
  const {
    selectedCommandId,
    setSelectedCommandId,
    setSelectedRunId,
    setActiveTab,
    selectedTemplate,
    setSelectedTemplate,
  } = useWorkstationStore();

  const [searchTerm, setSearchTerm] = useState('');
  const [selectedCategory, setSelectedCategory] = useState<string>('全部');
  const [formData, setFormData] = useState<Record<string, any>>({});
  const [copied, setCopied] = useState(false);
  const [runError, setRunError] = useState<string | null>(null);

  // 模板交互状态
  const [activeTemplateName, setActiveTemplateName] = useState<string | null>(null);
  const [showJsonEditor, setShowJsonEditor] = useState<boolean>(false);
  const [editorContent, setEditorContent] = useState<string>('');
  const [saveTplSuccess, setSaveTplSuccess] = useState<boolean>(false);

  const { data: commands } = useQuery({
    queryKey: ['commands'],
    queryFn: api.getCommands,
  });

  const { data: templates, refetch: refetchTemplates } = useQuery({
    queryKey: ['templates'],
    queryFn: () => api.getTemplates(),
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

  // 应用模板预设函数
  const handleApplyTemplate = (tpl: TemplateItem) => {
    setActiveTemplateName(tpl.name);
    setSelectedTemplate(tpl);
    setEditorContent(tpl.raw_content);

    setFormData((prev) => {
      const next = { ...prev };
      if (tpl.format === 'toml') {
        if ('config' in next || activeCommand?.parameters.some((p) => p.name === 'config')) {
          next.config = tpl.path;
        }
        // 清空表单中被 TOML 模板托管的物理层可选参数，使 --config 成为唯一真理源，避免残留旧参数覆盖配置文件
        const physicalKeys = ['sample_rate', 'frequency', 'bit_rate', 'cr', 'bt', 'sync', 'iq_format'];
        physicalKeys.forEach((k) => {
          if (k in next) next[k] = '';
        });
      } else if (tpl.format === 'json') {
        if (
          'message_file' in next ||
          activeCommand?.parameters.some((p) => p.name === 'message_file')
        ) {
          next.message_file = tpl.path;
        }
      }
      return next;
    });
  };

  // 清除选中的模板，恢复纯手动输入模式
  const handleClearTemplate = () => {
    setActiveTemplateName(null);
    setSelectedTemplate(null);
    setShowJsonEditor(false);
    setFormData((prev) => {
      const next = { ...prev };
      delete next.config;
      delete next.message_file;
      return next;
    });
  };

  // 一键清空单项覆盖，完全信任并由当前模板驱动
  const handleResetOverrides = () => {
    setFormData((prev) => {
      const next = { ...prev };
      const physicalKeys = ['sample_rate', 'frequency', 'bit_rate', 'cr', 'bt', 'sync', 'iq_format', 'max_seconds'];
      physicalKeys.forEach((k) => {
        if (k in next) next[k] = '';
      });
      return next;
    });
  };

  // 从当前激活的模板中提取对应物理参数的值
  const getTemplateValue = (paramName: string) => {
    if (!currentActiveTemplate?.parsed) return undefined;
    const demodCfg = currentActiveTemplate.parsed.demod || currentActiveTemplate.parsed;
    if (!demodCfg) return undefined;
    if (paramName === 'sample_rate') return demodCfg.sample_rate;
    if (paramName === 'frequency') return demodCfg.frequencies?.[0] ?? demodCfg.center_frequency;
    if (paramName === 'bit_rate') return demodCfg.bit_rate;
    if (paramName === 'cr') return demodCfg.coding_rate;
    if (paramName === 'bt') return demodCfg.bt;
    if (paramName === 'sync') return demodCfg.sync_word;
    if (paramName === 'iq_format') return demodCfg.iq_format;
    return undefined;
  };

  // 动态占位提示文字
  const getParamPlaceholder = (param: any) => {
    if (param.required) {
      return '(必选参数)';
    }
    const tplVal = getTemplateValue(param.name);
    if (tplVal !== undefined) {
      return `来自模板: ${tplVal} (留空使用模板设定)`;
    }
    if (param.default !== undefined && param.default !== null && param.default !== '') {
      return `默认: ${param.default} (留空使用)`;
    }
    return '(可选，留空由配置推断)';
  };

  // 监听全局 store 的 selectedTemplate 注入
  useEffect(() => {
    if (selectedTemplate) {
      handleApplyTemplate(selectedTemplate);
    }
  }, [selectedTemplate]);

  const categories = ['全部', ...Array.from(new Set(commands?.map((c) => c.category) || []))];

  const filteredCommands = commands?.filter((cmd) => {
    const matchesSearch =
      cmd.name.toLowerCase().includes(searchTerm.toLowerCase()) ||
      cmd.description.toLowerCase().includes(searchTerm.toLowerCase()) ||
      cmd.subcommand.toLowerCase().includes(searchTerm.toLowerCase());
    const matchesCategory = selectedCategory === '全部' || cmd.category === selectedCategory;
    return matchesSearch && matchesCategory;
  });

  // 适用于当前命令的模板
  const applicableTemplates =
    templates?.filter((t) => t.applicable_commands.includes(selectedCommandId)) || [];

  // 当前激活的模板元数据（如果选中了的话）
  const currentActiveTemplate =
    templates?.find(
      (t) =>
        t.name === activeTemplateName ||
        (t.format === 'json' && formData.message_file && t.path === formData.message_file) ||
        (t.format === 'toml' && formData.config && t.path === formData.config)
    ) || null;

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

  const saveTemplateMutation = useMutation({
    mutationFn: () => {
      if (!currentActiveTemplate) throw new Error('未选定模板');
      return api.saveTemplate(currentActiveTemplate.name, editorContent);
    },
    onSuccess: () => {
      refetchTemplates();
      setSaveTplSuccess(true);
      setTimeout(() => setSaveTplSuccess(false), 2500);
    },
  });

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
                onClick={() => {
                  setSelectedCommandId(cmd.id);
                  setActiveTemplateName(null);
                }}
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

      {/* 右侧：参数表单生成、模板选择与 CLI 预览调试 */}
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

            {/* 模板选择栏 (Templates: TOML Physical Presets & JSON Message Payloads) */}
            <div className="bg-rf-surface border border-rf-border p-4 rounded-lg space-y-3">
              <div className="flex items-center justify-between">
                <div className="flex items-center space-x-2 text-xs font-bold font-mono uppercase text-rf-text">
                  <FileText className="w-4 h-4 text-cyan-400" />
                  <span>模板库 (Templates / 物理参数与业务报文)</span>
                  <span className="text-[10px] px-1.5 py-0.5 rounded bg-rf-panel text-zinc-400 font-mono">
                    templates/ 动态加载
                  </span>
                </div>
                {currentActiveTemplate && (
                  <button
                    onClick={() => {
                      setEditorContent(currentActiveTemplate.raw_content);
                      setShowJsonEditor(!showJsonEditor);
                    }}
                    className="px-2 py-1 rounded bg-rf-panel hover:bg-rf-border text-xs text-cyan-400 font-mono flex items-center space-x-1"
                  >
                    <Code2 className="w-3.5 h-3.5" />
                    <span>{showJsonEditor ? '收起源码' : '查看/编辑模板源码'}</span>
                  </button>
                )}
              </div>

              {/* 适用模板芯片列表 */}
              <div className="flex flex-wrap gap-2 pt-1 items-center">
                {applicableTemplates.map((tpl) => {
                  const isActive =
                    tpl.name === activeTemplateName ||
                    (tpl.format === 'json' && formData.message_file === tpl.path) ||
                    (tpl.format === 'toml' && formData.config === tpl.path);
                  return (
                    <button
                      key={tpl.name}
                      onClick={() => handleApplyTemplate(tpl)}
                      className={`px-3 py-1.5 rounded text-xs font-mono flex items-center space-x-1.5 transition-all ${
                        isActive
                          ? 'bg-cyan-500/20 text-rf-accent border border-cyan-400 shadow-sm shadow-cyan-500/20'
                          : 'bg-rf-panel hover:bg-rf-border text-zinc-300 border border-rf-border/80'
                      }`}
                    >
                      {tpl.format === 'toml' ? (
                        <SlidersHorizontal className="w-3.5 h-3.5 text-cyan-400" />
                      ) : (
                        <FileCode className="w-3.5 h-3.5 text-amber-400" />
                      )}
                      <span>{tpl.title}</span>
                      <span className="text-[9px] px-1 rounded bg-black/40 text-zinc-400 uppercase">
                        {tpl.format}
                      </span>
                    </button>
                  );
                })}
                {currentActiveTemplate && (
                  <button
                    onClick={handleClearTemplate}
                    className="px-2.5 py-1.5 rounded text-xs font-mono text-zinc-400 hover:text-red-400 hover:bg-red-950/30 border border-dashed border-rf-border flex items-center space-x-1 transition-colors"
                    title="清除选中的模板，恢复纯手动参数配置"
                  >
                    <X className="w-3.5 h-3.5" />
                    <span>清除模板</span>
                  </button>
                )}
                {!applicableTemplates.length && (
                  <span className="text-xs text-zinc-500 font-mono">
                    当前命令暂无专属 templates 模板，可直接配置下方参数
                  </span>
                )}
              </div>

              {/* 激活模板描述说明 */}
              {currentActiveTemplate && (
                <div className="text-[11px] text-zinc-400 font-mono bg-rf-bg/70 p-2.5 rounded border border-rf-border/60 flex items-center justify-between">
                  <div className="space-y-1">
                    <div>
                      <span className="text-cyan-300 font-bold">{currentActiveTemplate.name}: </span>
                      <span>{currentActiveTemplate.description}</span>
                    </div>
                    {currentActiveTemplate.format === 'toml' && (
                      <div className="text-[10px] text-zinc-400 flex items-center space-x-3">
                        <span className="text-cyan-400/90 font-sans">
                          💡 物理参数完全由本模板 TOML 提供，下方可选表单项留空即可。仅在需临时单项覆盖时填写。
                        </span>
                        <button
                          type="button"
                          onClick={handleResetOverrides}
                          className="text-amber-400 hover:underline hover:text-amber-300 flex items-center space-x-1 shrink-0 font-mono"
                          title="将表单中的单项覆盖清空，完全按模板运行"
                        >
                          <RotateCcw className="w-3 h-3" />
                          <span>清空表单覆盖项</span>
                        </button>
                      </div>
                    )}
                  </div>
                  <span className="text-zinc-500 text-[10px] shrink-0 ml-4">{currentActiveTemplate.path}</span>
                </div>
              )}

              {/* 模板源码展开与修改保存 */}
              {showJsonEditor && currentActiveTemplate && (
                <div className="pt-2 space-y-2 border-t border-rf-border/60">
                  <div className="flex items-center justify-between">
                    <span className="text-[11px] font-mono text-zinc-400">
                      编辑并实时更新 templates/{currentActiveTemplate.name}:
                    </span>
                    <button
                      onClick={() => saveTemplateMutation.mutate()}
                      disabled={saveTemplateMutation.isPending}
                      className="px-3 py-1 rounded bg-cyan-600 hover:bg-cyan-500 text-black text-xs font-semibold flex items-center space-x-1 disabled:opacity-50"
                    >
                      {saveTplSuccess ? (
                        <Check className="w-3 h-3 text-black" />
                      ) : (
                        <Save className="w-3 h-3" />
                      )}
                      <span>
                        {saveTplSuccess
                          ? '已保存至磁盘！'
                          : saveTemplateMutation.isPending
                          ? '保存中...'
                          : '保存修改'}
                      </span>
                    </button>
                  </div>
                  <textarea
                    rows={8}
                    value={editorContent}
                    onChange={(e) => setEditorContent(e.target.value)}
                    className="w-full bg-rf-bg border border-rf-border rounded p-3 font-mono text-xs text-cyan-200 focus:outline-none focus:border-rf-accent resize-y"
                  />
                </div>
              )}
            </div>

            {/* 报文载荷结构化监视器 (仅在选中 JSON 模板或含有 message_file 时高亮呈现) */}
            {currentActiveTemplate?.format === 'json' && currentActiveTemplate.parsed && (
              <div className="bg-rf-surface border border-rf-border rounded-lg overflow-hidden space-y-0">
                <div className="p-3.5 border-b border-rf-border bg-rf-panel/40 flex items-center justify-between">
                  <div className="flex items-center space-x-2">
                    <Radio className="w-4 h-4 text-amber-400" />
                    <h3 className="text-xs font-bold font-mono uppercase text-rf-text">
                      报文载荷结构化监视器 (Payload Inspector)
                    </h3>
                  </div>
                  <div className="flex items-center space-x-2 font-mono text-[11px]">
                    <span className="px-2 py-0.5 rounded bg-rf-panel text-zinc-300 border border-rf-border">
                      指令: {currentActiveTemplate.parsed.command || '0x00'}
                    </span>
                    <span className="px-2 py-0.5 rounded bg-rf-panel text-zinc-300 border border-rf-border">
                      方向:{' '}
                      {currentActiveTemplate.parsed.direction === 'ground_to_air'
                        ? '地面端 ➔ 机载端'
                        : '机载端 ➔ 地面端'}
                    </span>
                  </div>
                </div>

                <div className="p-4 space-y-4">
                  {/* 16 通道遥控量仪表盘视图 */}
                  {Array.isArray(currentActiveTemplate.parsed?.fields?.channels_raw) && (
                    <div className="space-y-2">
                      <div className="flex items-center justify-between">
                        <span className="text-[11px] font-mono text-zinc-400 font-semibold">
                          16 通道遥控摇杆量 (Channels 1-16, 标准中立值 1024):
                        </span>
                        <span className="text-[10px] font-mono text-zinc-500">
                          通道范围 0 .. 2048
                        </span>
                      </div>
                      <div className="grid grid-cols-2 sm:grid-cols-4 gap-2.5">
                        {currentActiveTemplate.parsed.fields.channels_raw.map(
                          (rawVal: number, idx: number) => {
                            const percent = Math.min(
                              100,
                              Math.max(0, ((rawVal - 0) / 2048) * 100)
                            );
                            const isNeutral = Math.abs(rawVal - 1024) <= 20;
                            return (
                              <div
                                key={idx}
                                className="bg-rf-bg border border-rf-border/80 rounded p-2 space-y-1.5"
                              >
                                <div className="flex justify-between items-center text-[10px] font-mono">
                                  <span className="text-zinc-400 font-bold">CH {idx + 1}</span>
                                  <span
                                    className={`font-semibold ${
                                      isNeutral ? 'text-zinc-300' : 'text-amber-400'
                                    }`}
                                  >
                                    {rawVal}
                                  </span>
                                </div>
                                <div className="relative w-full h-1.5 bg-zinc-800 rounded-full overflow-hidden">
                                  <div
                                    className={`h-full ${
                                      isNeutral ? 'bg-cyan-500' : 'bg-amber-400'
                                    }`}
                                    style={{ width: `${percent}%` }}
                                  />
                                </div>
                              </div>
                            );
                          }
                        )}
                      </div>
                    </div>
                  )}

                  {/* 动态序列规则提示 (Updates) */}
                  {currentActiveTemplate.parsed?.updates && (
                    <div className="pt-2 border-t border-rf-border/40">
                      <span className="text-[11px] font-mono text-zinc-400 font-semibold block mb-1.5">
                        动态自动化更新规则 (Sequence Automation):
                      </span>
                      <div className="flex flex-wrap gap-2">
                        {Object.entries(currentActiveTemplate.parsed.updates).map(
                          ([key, rule]: [string, any]) => (
                            <span
                              key={key}
                              className="text-[10px] font-mono px-2 py-0.5 rounded bg-zinc-900 border border-zinc-700 text-zinc-300"
                            >
                              <strong className="text-cyan-400">{key}</strong>: {rule.type} (
                              {rule.type === 'counter'
                                ? `起 ${rule.start}, 步长 ${rule.step}, 模 ${rule.modulo}`
                                : `循环 [${rule.values?.join(', ')}]`}
                              )
                            </span>
                          )
                        )}
                      </div>
                    </div>
                  )}
                </div>
              </div>
            )}

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
                        <label className="text-xs font-mono font-medium text-zinc-300 flex items-center space-x-1.5">
                          <span>{param.name}</span>
                          {param.required ? (
                            <span className="text-[10px] px-1.5 py-0.2 rounded bg-red-950/70 text-red-400 border border-red-800/60 font-mono">
                              * 必选
                            </span>
                          ) : (
                            <span className="text-[10px] px-1 py-0.2 rounded bg-zinc-800 text-zinc-400 font-mono">
                              可选
                            </span>
                          )}
                        </label>
                        <span className="text-[10px] font-mono text-zinc-500">
                          {param.cli_flag || '位置参数'}
                        </span>
                      </div>

                      {/* 控件渲染 */}
                      {param.type === 'choice' && param.choices ? (
                        <div className="relative">
                          <select
                            value={val ?? ''}
                            onChange={(e) =>
                              setFormData({ ...formData, [param.name]: e.target.value })
                            }
                            className={`w-full bg-rf-bg border rounded px-3 py-1.5 text-xs font-mono focus:outline-none focus:border-rf-accent ${
                              val ? 'text-rf-text border-cyan-500/50' : 'text-zinc-400 border-rf-border'
                            }`}
                          >
                            {!param.required && (
                              <option value="" className="text-zinc-500 bg-rf-surface">
                                {(() => {
                                  const tplVal = getTemplateValue(param.name);
                                  if (tplVal !== undefined) {
                                    return `(未指定 / 默认使用模板设定: ${tplVal})`;
                                  }
                                  if (param.default) {
                                    return `(未指定 / 默认: ${param.default})`;
                                  }
                                  return '(未指定 / 留空不传参)';
                                })()}
                              </option>
                            )}
                            {param.choices.map((c: any) => (
                              <option key={String(c)} value={String(c)} className="text-rf-text bg-rf-surface">
                                {String(c)}
                              </option>
                            ))}
                          </select>
                        </div>
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
                        <div className="relative">
                          <input
                            type={
                              param.type === 'number' || param.type === 'integer' ? 'number' : 'text'
                            }
                            value={val ?? ''}
                            placeholder={getParamPlaceholder(param)}
                            onChange={(e) =>
                              setFormData({
                                ...formData,
                                [param.name]:
                                  e.target.value === ''
                                    ? ''
                                    : param.type === 'number'
                                    ? parseFloat(e.target.value) || e.target.value
                                    : param.type === 'integer'
                                    ? parseInt(e.target.value, 10) || e.target.value
                                    : e.target.value,
                              })
                            }
                            className={`w-full bg-rf-bg border rounded px-3 py-1.5 text-xs text-rf-text font-mono focus:outline-none focus:border-rf-accent ${
                              val !== '' && val !== undefined && val !== null && !param.required
                                ? 'border-cyan-500/50 pr-7'
                                : 'border-rf-border'
                            }`}
                          />
                          {val !== '' && val !== undefined && val !== null && !param.required && (
                            <button
                              type="button"
                              onClick={() => setFormData({ ...formData, [param.name]: '' })}
                              className="absolute right-2 top-1/2 -translate-y-1/2 text-zinc-500 hover:text-zinc-200 p-0.5 rounded"
                              title="清空此覆盖项"
                            >
                              <X className="w-3.5 h-3.5" />
                            </button>
                          )}
                        </div>
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
                  {copied ? (
                    <Check className="w-3.5 h-3.5 text-emerald-400" />
                  ) : (
                    <Copy className="w-3.5 h-3.5" />
                  )}
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
