import React, { useState, useEffect, useRef } from 'react';
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
  Radio,
  FileCode,
  X,
  RotateCcw,
  Tag,
  Sparkles,
  Info,
  CornerDownRight,
  Loader2,
} from 'lucide-react';
import { HardwareDeviceSelector } from '../components/HardwareDeviceSelector';
import { MessageIOConfiguration } from '../components/MessageIOConfiguration';
import { api } from '../api/client';
import { CommandDefinition, CommandParameter, TemplateItem } from '../types';
import { useWorkstationStore } from '../stores/useWorkstationStore';
import { cliPreview } from '../utils/runtime';
import { commandDraftParameters, isOutputParameter } from '../utils/commandDraft';
import { commandGroups, commandGroupMatches } from '../utils/commandGroups';

export const CommandLibraryView: React.FC = () => {
  const queryClient = useQueryClient();
  const {
    selectedCommandId,
    setSelectedCommandId,
    setSelectedRunId,
    setSelectedArtifactPath,
    setActiveTab,
    selectedTemplate,
    setSelectedTemplate,
  } = useWorkstationStore();

  const [searchTerm, setSearchTerm] = useState('');
  const [selectedCategory, setSelectedCategory] = useState<string>('全部');
  const [formData, setFormData] = useState<Record<string, any>>({});
  const [taskName, setTaskName] = useState('');
  const [manualOutputs, setManualOutputs] = useState<Record<string, boolean>>({});
  const [copied, setCopied] = useState(false);
  const [runError, setRunError] = useState<string | null>(null);
  const previousGroup = useRef('');

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
  const groups = commandGroups(commands || []);
  const activeGroup = groups.find(group => group.modes.some(mode => mode.id === selectedCommandId));

  // 初始化参数表单默认值
  useEffect(() => {
    if (activeCommand) {
      const initial: Record<string, any> = {};
      activeCommand.parameters.forEach((param) => {
        initial[param.name] = param.default ?? '';
      });
      setFormData(initial);
      const groupId = activeCommand.group_id || activeCommand.id;
      if (previousGroup.current !== groupId) setTaskName('');
      previousGroup.current = groupId;
      setManualOutputs({});
      setRunError(null);
      setActiveTemplateName(null);
      setShowJsonEditor(false);
    }
  }, [activeCommand?.id]);

  const executionParameters = activeCommand
    ? commandDraftParameters(activeCommand, formData, taskName, manualOutputs) : formData;
  const updateParameter = (parameter: CommandParameter, value: any) => {
    setFormData(previous => ({ ...previous, [parameter.name]: value }));
    if (isOutputParameter(parameter)) setManualOutputs(previous => ({ ...previous, [parameter.name]: true }));
  };

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
        const physicalKeys = ['sample_rate', 'frequency', 'bit_rate', 'cr', 'bt', 'sync', 'iq_format', 'header_type', 'crc_bytes', 'crc_initial', 'payload_length'];
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
      const physicalKeys = ['sample_rate', 'frequency', 'bit_rate', 'cr', 'bt', 'sync', 'iq_format', 'header_type', 'crc_bytes', 'crc_initial', 'payload_length', 'max_seconds'];
      physicalKeys.forEach((k) => {
        if (k in next) next[k] = '';
      });
      return next;
    });
  };

  // 从当前激活的模板中提取对应物理参数的值
  const getTemplateValue = (paramName: string) => {
    if (!currentActiveTemplate?.parsed) return undefined;
    const physical = ['transmit', 'duplex', 'modulate'].includes(activeCommand?.id || '');
    const demodCfg = { ...(currentActiveTemplate.parsed.demod || currentActiveTemplate.parsed),
      ...(physical ? currentActiveTemplate.parsed.transmit || {} : {}) };
    if (!demodCfg) return undefined;
    if (paramName === 'sample_rate') return demodCfg.sample_rate;
    if (paramName === 'frequency') return physical ? currentActiveTemplate.parsed.transmit?.frequency : demodCfg.frequencies?.[0] ?? demodCfg.center_frequency;
    if (paramName === 'bit_rate') return demodCfg.bit_rate;
    if (paramName === 'cr') return demodCfg.coding_rate;
    if (paramName === 'bt') return demodCfg.bt;
    if (paramName === 'sync') return demodCfg.sync_word;
    if (paramName === 'iq_format') return demodCfg.iq_format;
    if (['header_type', 'crc_bytes', 'crc_initial'].includes(paramName)) return demodCfg[paramName];
    if (paramName === 'payload_length') return demodCfg.payload_lengths?.join(', ');
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
    if (selectedTemplate && activeCommand && selectedTemplate.applicable_commands.includes(activeCommand.id)) {
      handleApplyTemplate(selectedTemplate);
    }
  }, [selectedTemplate, activeCommand]);

  const categories = ['全部', ...Array.from(new Set(groups.map(group => group.category)))];
  const filteredCommands = groups.filter(group => commandGroupMatches(group, searchTerm, selectedCategory));

  // 适用于当前命令的模板
  const applicableTemplates =
    templates?.filter((t) => t.applicable_commands.includes(selectedCommandId)) || [];

  // 当前激活的模板元数据（如果选中了的话）
  const currentActiveTemplate =
    templates?.find(
      (t) => t.applicable_commands.includes(selectedCommandId) && (
        t.name === activeTemplateName ||
        (t.format === 'json' && formData.message_file && t.path === formData.message_file) ||
        (t.format === 'toml' && formData.config && t.path === formData.config))
    ) || null;

  // 动态构建即将执行的 CLI 命令预览
  const generateCLIPreview = () => {
    if (!activeCommand) return '';
    return cliPreview(activeCommand, executionParameters);
  };

  const handleCopyCLI = () => {
    navigator.clipboard.writeText(generateCLIPreview());
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const runMutation = useMutation({
    mutationFn: () => api.createRun(selectedCommandId, executionParameters, true, taskName.trim()),
    onSuccess: (run) => {
      queryClient.invalidateQueries({ queryKey: ['runs'] });
      setSelectedRunId(run.id);
      setSelectedArtifactPath(run.command_id === 'demod' ? run.command_args.iq || null
        : run.command_id === 'modulate' || run.command_id === 'rx' ? run.command_args.output || null : null);
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
    onSuccess: (template) => {
      queryClient.setQueryData<TemplateItem[]>(['templates'], previous =>
        previous?.map(item => item.name === template.name ? template : item));
      useWorkstationStore.setState({ selectedTemplate: null });
      refetchTemplates();
      setSaveTplSuccess(true);
      setTimeout(() => setSaveTplSuccess(false), 2500);
    },
  });

  return (
    <div className="flex h-[calc(100vh-3.5rem)] overflow-hidden select-none">
      {/* 左侧：命令目录检索 */}
      <div className="w-80 bg-rf-surface/95 border-r border-rf-border flex flex-col select-none shrink-0 shadow-lg">
        {/* 顶部检索卡片 */}
        <div className="p-3.5 border-b border-rf-border/80 bg-rf-panel/30 space-y-2.5">
          <div className="relative">
            <Search className="w-4 h-4 text-zinc-400 absolute left-2.5 top-2.5" />
            <input
              type="text"
              placeholder="搜索命令或功能..."
              value={searchTerm}
              onChange={(e) => setSearchTerm(e.target.value)}
              className="w-full bg-rf-bg/90 border border-rf-border rounded-lg pl-8 pr-3 py-1.5 text-xs text-rf-text placeholder-zinc-500 focus:outline-none focus:border-cyan-400 focus:ring-1 focus:ring-cyan-400/40 transition-all font-mono"
            />
          </div>

          {/* 分类过滤芯片 */}
          <div className="flex flex-wrap gap-1.5">
            {categories.map((cat) => (
              <button
                key={cat}
                onClick={() => setSelectedCategory(cat)}
                className={`px-2.5 py-1 rounded-md text-[11px] font-mono tracking-wide transition-all ${
                  selectedCategory === cat
                    ? 'bg-cyan-500/20 text-cyan-300 border border-cyan-400/50 shadow-sm shadow-cyan-500/20 font-semibold'
                    : 'bg-rf-panel/70 text-zinc-400 hover:text-rf-text hover:bg-rf-panel border border-rf-border/50'
                }`}
              >
                {cat}
              </button>
            ))}
          </div>
        </div>

        {/* 命令列表 */}
        <div className="flex-1 overflow-y-auto divide-y divide-rf-border/30">
          {filteredCommands?.map((cmd) => {
            const isSelected = cmd.modes.some(mode => mode.id === selectedCommandId);
            return (
              <div
                key={cmd.id}
                onClick={() => {
                  if (!isSelected) setSelectedCommandId(cmd.modes[0].id);
                  setActiveTemplateName(null);
                }}
                className={`p-3.5 cursor-pointer transition-all text-left relative ${
                  isSelected
                    ? 'bg-gradient-to-r from-cyan-500/15 via-cyan-500/5 to-transparent border-l-2 border-cyan-400'
                    : 'hover:bg-rf-panel/40 border-l-2 border-transparent'
                }`}
              >
                <div className="flex items-center justify-between mb-1">
                  <span
                    className={`text-xs font-bold tracking-wide ${
                      isSelected ? 'text-cyan-300' : 'text-rf-text'
                    }`}
                  >
                    {cmd.name}
                  </span>
                  {cmd.modes.some(mode => mode.requires_sdr) && (
                    <span className="text-[10px] px-1.5 py-0.5 rounded bg-amber-500/15 text-amber-300 border border-amber-500/40 font-mono font-semibold flex items-center space-x-1 shadow-sm">
                      <Cpu className="w-2.5 h-2.5" />
                      <span>SDR</span>
                    </span>
                  )}
                </div>
                <div className="text-[11px] font-mono text-zinc-400 mb-1 flex items-center space-x-1">
                  <span className="text-cyan-500/70">$</span>
                  <span className="truncate">
                    {cmd.modes.length > 1 ? cmd.modes.map(mode => mode.name).join(' / ') : `flrc ${cmd.modes[0].subcommand}`}
                  </span>
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
      <div className="flex-1 overflow-y-auto p-6 space-y-6 bg-rf-bg/60">
        {activeCommand ? (
          <div className="max-w-4xl mx-auto space-y-5">
            {/* 命令基本信息头部卡片 */}
            <div className="bg-gradient-to-r from-rf-surface via-rf-panel/40 to-rf-surface border border-rf-border p-5 rounded-xl flex items-start justify-between shadow-md relative overflow-hidden">
              <div className="absolute top-0 left-0 right-0 h-[2px] bg-gradient-to-r from-cyan-500/60 via-cyan-400/30 to-transparent" />
              <div className="space-y-2">
                <div className="flex flex-wrap items-center gap-2">
                  <h2 className="text-base font-bold text-rf-text tracking-wide">
                    {activeGroup?.name || activeCommand.name}
                  </h2>
                  <span className="text-xs font-mono px-2.5 py-0.5 rounded-md bg-cyan-950/40 text-cyan-300 border border-cyan-800/60 font-semibold shadow-sm">
                    flrc {activeCommand.subcommand}
                  </span>
                  {activeCommand.requires_sdr && (
                    <span className="text-xs px-2.5 py-0.5 rounded-md bg-amber-950/60 text-amber-300 border border-amber-800/80 font-mono flex items-center space-x-1.5 shadow-sm">
                      <Cpu className="w-3.5 h-3.5" />
                      <span>需要独占 SDR 硬件</span>
                    </span>
                  )}
                </div>
                <p className="text-xs text-zinc-300 leading-relaxed max-w-2xl">
                  {activeCommand.description}
                </p>
              </div>

              {activeCommand.pipeline_id && (
                <button
                  onClick={() => {
                    setActiveTab('workbench');
                  }}
                  className="px-3.5 py-2 rounded-lg bg-rf-panel/80 hover:bg-rf-border text-xs text-cyan-300 border border-cyan-500/30 font-mono flex items-center space-x-2 transition-all shadow-sm hover:shadow-cyan-500/10 shrink-0"
                >
                  <Layers className="w-4 h-4 text-cyan-400" />
                  <span>在工作台中查看数据流</span>
                </button>
              )}
            </div>

            {/* 工作模式切换器 */}
            {activeGroup && activeGroup.modes.length > 1 && (
              <div className="bg-rf-surface border border-rf-border p-4 rounded-xl space-y-3 shadow-sm">
                <div className="text-xs font-semibold text-zinc-300 uppercase tracking-wider font-mono flex items-center space-x-2">
                  <Radio className="w-3.5 h-3.5 text-cyan-400" />
                  <span>工作模式选择 (Operation Mode)</span>
                </div>
                <div className="flex flex-wrap gap-2.5" role="group" aria-label="报文收发模式">
                  {activeGroup.modes.map(mode => {
                    const isModeActive = mode.id === selectedCommandId;
                    return (
                      <button
                        key={mode.id}
                        type="button"
                        aria-pressed={isModeActive}
                        disabled={runMutation.isPending}
                        onClick={() => {
                          if (selectedTemplate && !selectedTemplate.applicable_commands.includes(mode.id)) setSelectedTemplate(null);
                          setSelectedCommandId(mode.id);
                        }}
                        className={`px-4 py-2 rounded-lg border text-xs font-mono font-semibold transition-all flex items-center space-x-2 ${
                          isModeActive
                            ? 'border-cyan-400 bg-cyan-500/20 text-cyan-300 shadow-md shadow-cyan-500/20'
                            : 'border-rf-border/80 bg-rf-panel/60 text-zinc-400 hover:text-rf-text hover:bg-rf-panel'
                        }`}
                      >
                        <span className={`w-2 h-2 rounded-full ${isModeActive ? 'bg-cyan-400 shadow-sm shadow-cyan-400' : 'bg-zinc-600'}`} />
                        <span>{mode.name}</span>
                        {mode.requires_sdr && (
                          <span className="text-[10px] px-1.5 py-0.2 rounded bg-amber-500/20 text-amber-300 border border-amber-500/30">
                            SDR
                          </span>
                        )}
                      </button>
                    );
                  })}
                </div>
                <p className="text-[11px] text-zinc-400 font-mono leading-relaxed bg-rf-bg/40 p-2 rounded-lg border border-rf-border/40">
                  {activeCommand.description}
                </p>
              </div>
            )}

            {/* 任务名称草稿卡片 */}
            <div className="bg-rf-surface border border-rf-border p-4 rounded-xl space-y-2.5 shadow-sm">
              <div className="flex items-center justify-between">
                <label htmlFor="command-task-name" className="text-xs font-semibold text-zinc-300 flex items-center space-x-1.5">
                  <Tag className="w-3.5 h-3.5 text-cyan-400" />
                  <span>任务名称 (Task Identifier)</span>
                </label>
                {taskName.trim() && (
                  <span className="text-[10px] font-mono text-cyan-400 bg-cyan-950/40 px-2 py-0.5 rounded border border-cyan-800/40">
                    输出文件名将自动跟随
                  </span>
                )}
              </div>
              <div className="relative">
                <input
                  id="command-task-name"
                  value={taskName}
                  maxLength={120}
                  onChange={event => setTaskName(event.target.value)}
                  placeholder="可选，例如: H12首次对频 / 盲发测试01"
                  className="w-full bg-rf-bg border border-rf-border rounded-lg px-3.5 py-2 text-sm text-rf-text placeholder-zinc-500 focus:outline-none focus:border-cyan-400 focus:ring-1 focus:ring-cyan-400/40 transition-all font-mono"
                />
                {taskName && (
                  <button
                    type="button"
                    onClick={() => setTaskName('')}
                    className="absolute right-3 top-1/2 -translate-y-1/2 text-zinc-400 hover:text-zinc-200 p-1"
                    title="清空任务名称"
                  >
                    <X className="w-3.5 h-3.5" />
                  </button>
                )}
              </div>
              <p className="text-[11px] text-zinc-400 leading-relaxed">
                {activeCommand.parameters.some(isOutputParameter)
                  ? '填写后输出文件名自动跟随任务名称；手动修改的路径会保持你的设置。名称留空时使用自动任务名称，未手动指定的输出使用默认路径。'
                  : '用于区分本次任务与历史记录；留空时自动生成任务名称。本模式结果显示在任务日志中。'}
              </p>
            </div>

            {/* 模板选择栏 (Templates: TOML Physical Presets & JSON Message Payloads) */}
            <div className="bg-rf-surface border border-rf-border p-4 rounded-xl space-y-3.5 shadow-sm">
              <div className="flex items-center justify-between">
                <div className="flex items-center space-x-2 text-xs font-bold font-mono uppercase text-rf-text">
                  <FileText className="w-4 h-4 text-cyan-400" />
                  <span>模板库 (Templates / 物理参数与业务报文)</span>
                  <span className="text-[10px] px-2 py-0.5 rounded bg-rf-panel text-zinc-400 font-mono border border-rf-border/50">
                    templates/ 动态加载
                  </span>
                </div>
                {currentActiveTemplate && (
                  <button
                    onClick={() => {
                      setEditorContent(currentActiveTemplate.raw_content);
                      setShowJsonEditor(!showJsonEditor);
                    }}
                    className="px-2.5 py-1 rounded-md bg-rf-panel hover:bg-rf-border text-xs text-cyan-300 font-mono flex items-center space-x-1.5 transition-colors border border-rf-border/60"
                  >
                    <Code2 className="w-3.5 h-3.5 text-cyan-400" />
                    <span>{showJsonEditor ? '收起源码' : '查看/编辑模板源码'}</span>
                  </button>
                )}
              </div>

              {/* 适用模板芯片列表 */}
              <div className="flex flex-wrap gap-2.5 pt-1 items-center">
                {applicableTemplates.map((tpl) => {
                  const isActive =
                    tpl.name === activeTemplateName ||
                    (tpl.format === 'json' && formData.message_file === tpl.path) ||
                    (tpl.format === 'toml' && formData.config === tpl.path);
                  return (
                    <button
                      key={tpl.name}
                      onClick={() => handleApplyTemplate(tpl)}
                      className={`px-3.5 py-2 rounded-lg text-xs font-mono flex items-center space-x-2 transition-all shadow-sm ${
                        isActive
                          ? 'bg-cyan-500/20 text-cyan-300 border border-cyan-400 shadow-md shadow-cyan-500/20 font-bold'
                          : 'bg-rf-panel/70 hover:bg-rf-panel text-zinc-300 border border-rf-border/80 hover:border-zinc-500'
                      }`}
                    >
                      {tpl.format === 'toml' ? (
                        <SlidersHorizontal className="w-3.5 h-3.5 text-cyan-400" />
                      ) : (
                        <FileCode className="w-3.5 h-3.5 text-amber-400" />
                      )}
                      <span>{tpl.title}</span>
                      <span className={`text-[10px] px-1.5 py-0.2 rounded uppercase font-semibold ${
                        tpl.format === 'toml' ? 'bg-cyan-950/60 text-cyan-300 border border-cyan-800/50' : 'bg-amber-950/60 text-amber-300 border border-amber-800/50'
                      }`}>
                        {tpl.format}
                      </span>
                    </button>
                  );
                })}
                {currentActiveTemplate && (
                  <button
                    onClick={handleClearTemplate}
                    className="px-3 py-2 rounded-lg text-xs font-mono text-zinc-400 hover:text-red-400 hover:bg-red-950/30 border border-dashed border-rf-border hover:border-red-800/60 flex items-center space-x-1.5 transition-colors"
                    title="清除选中的模板，恢复纯手动参数配置"
                  >
                    <X className="w-3.5 h-3.5" />
                    <span>清除模板</span>
                  </button>
                )}
                {!applicableTemplates.length && (
                  <span className="text-xs text-zinc-500 font-mono py-1">
                    当前命令暂无专属 templates 模板，可直接配置下方参数
                  </span>
                )}
              </div>

              {/* 激活模板描述说明 */}
              {currentActiveTemplate && (
                <div className="text-xs text-zinc-300 font-mono bg-rf-bg/80 p-3 rounded-lg border border-rf-border/70 flex items-center justify-between shadow-inner">
                  <div className="space-y-1.5">
                    <div>
                      <span className="text-cyan-300 font-bold">{currentActiveTemplate.name}: </span>
                      <span>{currentActiveTemplate.description}</span>
                    </div>
                    {currentActiveTemplate.format === 'toml' && (
                      <div className="text-[11px] text-zinc-400 flex items-center space-x-3">
                        <span className="text-cyan-300/90 font-sans flex items-center space-x-1">
                          <Info className="w-3 h-3 text-cyan-400 inline" />
                          <span>物理参数完全由本模板 TOML 提供，下方可选表单项留空即可。仅在需临时单项覆盖时填写。</span>
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
                  <span className="text-zinc-500 text-[11px] shrink-0 ml-4 font-mono">{currentActiveTemplate.path}</span>
                </div>
              )}
              {saveTemplateMutation.error && (
                <p role="alert" className="text-xs text-red-400">{saveTemplateMutation.error.message}</p>
              )}

              {/* 模板源码展开与修改保存 */}
              {showJsonEditor && currentActiveTemplate && (
                <div className="pt-3 space-y-2.5 border-t border-rf-border/60">
                  <div className="flex items-center justify-between">
                    <span className="text-xs font-mono text-zinc-300 flex items-center space-x-1.5">
                      <Code2 className="w-3.5 h-3.5 text-cyan-400" />
                      <span>编辑并实时更新 templates/{currentActiveTemplate.name}:</span>
                    </span>
                    <button
                      onClick={() => saveTemplateMutation.mutate()}
                      disabled={saveTemplateMutation.isPending}
                      className="px-3.5 py-1.5 rounded-lg bg-cyan-500 hover:bg-cyan-400 text-black text-xs font-bold flex items-center space-x-1.5 disabled:opacity-50 transition-all shadow-md shadow-cyan-500/20"
                    >
                      {saveTplSuccess ? (
                        <Check className="w-3.5 h-3.5 text-black" />
                      ) : (
                        <Save className="w-3.5 h-3.5" />
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
                    className="w-full bg-rf-bg border border-rf-border rounded-lg p-3.5 font-mono text-xs text-cyan-200 focus:outline-none focus:border-cyan-400 focus:ring-1 focus:ring-cyan-400/40 resize-y leading-relaxed"
                  />
                </div>
              )}
            </div>

            {/* 报文载荷结构化监视器 (仅在选中 JSON 模板或含有 message_file 时高亮呈现) */}
            {currentActiveTemplate?.format === 'json' && currentActiveTemplate.parsed && (
              <div className="bg-rf-surface border border-rf-border rounded-xl overflow-hidden space-y-0 shadow-md">
                <div className="p-3.5 border-b border-rf-border bg-rf-panel/50 flex items-center justify-between">
                  <div className="flex items-center space-x-2">
                    <Radio className="w-4 h-4 text-amber-400" />
                    <h3 className="text-xs font-bold font-mono uppercase text-rf-text">
                      报文载荷结构化监视器 (Payload Inspector)
                    </h3>
                  </div>
                  <div className="flex items-center space-x-2 font-mono text-[11px]">
                    <span className="px-2 py-0.5 rounded-md bg-rf-panel text-zinc-300 border border-rf-border">
                      指令: {currentActiveTemplate.parsed.command || '0x00'}
                    </span>
                    <span className="px-2 py-0.5 rounded-md bg-rf-panel text-zinc-300 border border-rf-border">
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
                    <div className="space-y-2.5">
                      <div className="flex items-center justify-between">
                        <span className="text-xs font-mono text-zinc-300 font-semibold flex items-center space-x-1.5">
                          <Sliders className="w-3.5 h-3.5 text-cyan-400" />
                          <span>16 通道遥控摇杆量 (Channels 1-16, 标准中立值 1024):</span>
                        </span>
                        <span className="text-[11px] font-mono text-zinc-500">
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
                                className="bg-rf-bg border border-rf-border/80 rounded-lg p-2.5 space-y-1.5 shadow-inner"
                              >
                                <div className="flex justify-between items-center text-[11px] font-mono">
                                  <span className="text-zinc-400 font-bold">CH {idx + 1}</span>
                                  <span
                                    className={`font-semibold ${
                                      isNeutral ? 'text-cyan-300' : 'text-amber-400'
                                    }`}
                                  >
                                    {rawVal}
                                  </span>
                                </div>
                                <div className="relative w-full h-1.5 bg-zinc-800 rounded-full overflow-hidden">
                                  <div
                                    className={`h-full transition-all duration-300 ${
                                      isNeutral ? 'bg-cyan-400' : 'bg-amber-400'
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
                      <span className="text-xs font-mono text-zinc-300 font-semibold block mb-2">
                        动态自动化更新规则 (Sequence Automation):
                      </span>
                      <div className="flex flex-wrap gap-2">
                        {Object.entries(currentActiveTemplate.parsed.updates).map(
                          ([key, rule]: [string, any]) => (
                            <span
                              key={key}
                              className="text-xs font-mono px-2.5 py-1 rounded-md bg-zinc-900 border border-zinc-700 text-zinc-300 shadow-sm"
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
            <div className="bg-rf-surface border border-rf-border rounded-xl overflow-hidden shadow-sm">
              <div className="p-3.5 border-b border-rf-border bg-rf-panel/40 flex items-center justify-between">
                <h3 className="text-xs font-bold font-mono uppercase text-rf-text flex items-center space-x-2">
                  <Sliders className="w-4 h-4 text-cyan-400" />
                  <span>执行参数表单 (Structured Parameters)</span>
                </h3>
                <span className="text-[11px] font-mono text-zinc-500">
                  共 {activeCommand.parameters.length} 个配置参数
                </span>
              </div>

              <div className="p-5 grid grid-cols-1 md:grid-cols-2 gap-4">
                {activeCommand.parameters.filter(param => !(executionParameters.session && ['message_file', 'payload_hex'].includes(param.name))).map((param) => {
                  const val = executionParameters[param.name];
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
                            <span className="text-[10px] px-1.5 py-0.2 rounded bg-red-950/70 text-red-400 border border-red-800/60 font-mono font-semibold">
                              * 必选
                            </span>
                          ) : (
                            <span className="text-[10px] px-1.5 py-0.2 rounded bg-zinc-800/80 text-zinc-400 font-mono">
                              可选
                            </span>
                          )}
                        </label>
                        <span className="text-[11px] font-mono text-zinc-500">
                          {param.cli_flag || '位置参数'}
                        </span>
                      </div>

                      {/* 控件渲染 */}
                      {param.type === 'choice' && param.choices ? (
                        <div className="relative">
                          <select
                            value={val ?? ''}
                            onChange={(e) =>
                              updateParameter(param, e.target.value)
                            }
                            className={`w-full bg-rf-bg border rounded-lg px-3.5 py-2 text-xs font-mono focus:outline-none focus:border-cyan-400 focus:ring-1 focus:ring-cyan-400/40 transition-all ${
                              val ? 'text-cyan-200 border-cyan-500/50' : 'text-zinc-400 border-rf-border'
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
                        <label className="flex items-center space-x-2 text-xs text-zinc-300 cursor-pointer pt-1.5 font-mono">
                          <input
                            type="checkbox"
                            checked={Boolean(val)}
                            onChange={(e) =>
                              updateParameter(param, e.target.checked)
                            }
                            className="w-4 h-4 rounded bg-rf-bg border-rf-border text-cyan-400 focus:ring-0 focus:ring-offset-0"
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
                              updateParameter(param,
                                  e.target.value === ''
                                    ? ''
                                    : param.type === 'number'
                                    ? Number(e.target.value)
                                    : param.type === 'integer'
                                    ? Number(e.target.value)
                                    : e.target.value)
                            }
                            className={`w-full bg-rf-bg border rounded-lg px-3.5 py-2 text-xs text-rf-text font-mono focus:outline-none focus:border-cyan-400 focus:ring-1 focus:ring-cyan-400/40 transition-all ${
                              val !== '' && val !== undefined && val !== null && !param.required
                                ? 'border-cyan-500/60 pr-8 text-cyan-200'
                                : 'border-rf-border'
                            }`}
                          />
                          {val !== '' && val !== undefined && val !== null && !param.required && (
                            <button
                              type="button"
                              onClick={() => updateParameter(param, '')}
                              className="absolute right-2.5 top-1/2 -translate-y-1/2 text-zinc-400 hover:text-zinc-200 p-0.5 rounded transition-colors"
                              title="清空此覆盖项"
                            >
                              <X className="w-3.5 h-3.5" />
                            </button>
                          )}
                        </div>
                      )}

                      {isOutputParameter(param) && (
                        <div className="flex items-center justify-between gap-2 text-[11px] font-mono pt-0.5">
                          <span className={manualOutputs[param.name] ? 'text-amber-400 flex items-center space-x-1' : 'text-cyan-400 flex items-center space-x-1'}>
                            <CornerDownRight className="w-3 h-3 inline" />
                            <span>{manualOutputs[param.name] ? '手动指定路径' : taskName.trim() ? '跟随任务名称' : '默认路径（填写任务名称后自动生成）'}</span>
                          </span>
                          {manualOutputs[param.name] && (
                            <button
                              type="button"
                              className="text-cyan-400 hover:underline hover:text-cyan-300 font-mono"
                              onClick={() => {
                                setManualOutputs(previous => ({ ...previous, [param.name]: false }));
                                setFormData(previous => ({ ...previous, [param.name]: param.default ?? '' }));
                              }}
                            >
                              恢复跟随任务名称
                            </button>
                          )}
                        </div>
                      )}

                      <div className="text-[11px] text-zinc-400 leading-relaxed font-sans">
                        {param.description}
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>

            {['transmit', 'duplex'].includes(activeCommand.id) && <MessageIOConfiguration
              commandId={activeCommand.id} parameters={executionParameters}
              onChange={values => setFormData(previous => ({ ...previous, ...values }))} />}

            {['live', 'rx', 'tx', 'sync', 'usrp'].includes(activeCommand.id) && <div className="bg-rf-surface border border-rf-border rounded-xl p-4 text-xs">
              <HardwareDeviceSelector commandId={activeCommand.id} parameters={executionParameters}
                onChange={values => setFormData(previous => ({ ...previous, ...values }))} />
            </div>}

            {/* CLI 预览与执行区 */}
            <div className="bg-rf-surface border border-rf-border rounded-xl p-4 space-y-3.5 shadow-md">
              <div className="flex items-center justify-between">
                <div className="flex items-center space-x-2 text-xs font-mono font-bold uppercase text-rf-text">
                  <Terminal className="w-4 h-4 text-cyan-400" />
                  <span>最终生成的 CLI 指令预览 (Command Line Preview)</span>
                </div>
                <button
                  onClick={handleCopyCLI}
                  className="px-3 py-1.5 rounded-lg bg-rf-panel hover:bg-rf-border text-zinc-300 text-xs font-mono flex items-center space-x-1.5 transition-colors border border-rf-border/60"
                >
                  {copied ? (
                    <Check className="w-3.5 h-3.5 text-emerald-400" />
                  ) : (
                    <Copy className="w-3.5 h-3.5 text-cyan-400" />
                  )}
                  <span>{copied ? '已复制' : '复制命令'}</span>
                </button>
              </div>

              {/* 终端代码预览区 */}
              <div className="bg-black/60 p-3.5 rounded-lg font-mono text-xs text-cyan-300 break-all select-all border border-rf-border shadow-inner leading-relaxed">
                <span className="text-zinc-500 mr-2 select-none">$</span>
                {generateCLIPreview()}
              </div>

              {runError && (
                <div className="p-3 rounded-lg bg-red-950/60 border border-red-800 text-red-300 text-xs flex items-center space-x-2 font-mono">
                  <AlertCircle className="w-4 h-4 flex-shrink-0 text-red-400" />
                  <span>{runError}</span>
                </div>
              )}

              <div className="flex justify-end pt-1">
                <button
                  onClick={() => { setRunError(null); runMutation.mutate(); }}
                  disabled={runMutation.isPending}
                  className="px-6 py-2.5 rounded-xl bg-gradient-to-r from-cyan-500 to-cyan-400 hover:from-cyan-400 hover:to-cyan-300 text-black font-bold text-xs uppercase tracking-wider flex items-center space-x-2 shadow-lg shadow-cyan-500/25 active:scale-[0.98] transition-all disabled:opacity-50"
                >
                  {runMutation.isPending ? (
                    <Loader2 className="w-4 h-4 animate-spin text-black" />
                  ) : (
                    <Play className="w-4 h-4 fill-current" />
                  )}
                  <span>{runMutation.isPending ? '正在创建子进程...' : executionParameters.session ? '建立常驻会话（仅接收与同步）' : '执行该命令 (Run Task)'}</span>
                </button>
              </div>
            </div>
          </div>
        ) : (
          <div className="p-16 text-center text-zinc-500 font-mono">
            请在左侧选择要配置和执行的 Modem 命令
          </div>
        )}
      </div>
    </div>
  );
};
