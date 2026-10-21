import React, { useState, useEffect } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  FileText,
  FileCode,
  SlidersHorizontal,
  Search,
  Save,
  Check,
  RotateCcw,
  Terminal,
  Play,
  Layers,
  Code2,
  Radio,
  Sliders,
  Info,
  ExternalLink,
  Tag,
  CheckCircle2,
  AlertCircle,
  Copy,
  Plus,
} from 'lucide-react';
import { api } from '../api/client';
import { TemplateItem } from '../types';
import { useWorkstationStore } from '../stores/useWorkstationStore';

export const TemplateLibraryView: React.FC = () => {
  const queryClient = useQueryClient();
  const {
    selectedTemplate,
    setSelectedTemplate,
    selectedCommandId,
    setSelectedCommandId,
    setActiveTab,
  } = useWorkstationStore();

  const [searchTerm, setSearchTerm] = useState('');
  const [formatFilter, setFormatFilter] = useState<'all' | TemplateItem['category']>('all');
  const [activeTemplateName, setActiveTemplateName] = useState<string | null>(
    selectedTemplate?.name || null
  );
  const [editorContent, setEditorContent] = useState<string>('');
  const [activeTabMode, setActiveTabMode] = useState<'editor' | 'inspector'>('editor');
  const [saveSuccess, setSaveSuccess] = useState<boolean>(false);
  const [copied, setCopied] = useState<boolean>(false);

  const { data: templates, refetch: refetchTemplates, isLoading } = useQuery({
    queryKey: ['templates'],
    queryFn: () => api.getTemplates(),
  });

  // 筛选模板列表
  const filteredTemplates = (templates || []).filter((tpl) => {
    const matchesFormat = formatFilter === 'all' || tpl.category === formatFilter;
    const matchesSearch =
      searchTerm.trim() === '' ||
      tpl.title.toLowerCase().includes(searchTerm.toLowerCase()) ||
      tpl.name.toLowerCase().includes(searchTerm.toLowerCase()) ||
      tpl.description.toLowerCase().includes(searchTerm.toLowerCase()) ||
      tpl.path.toLowerCase().includes(searchTerm.toLowerCase()) ||
      tpl.applicable_commands.some((cmd) => cmd.toLowerCase().includes(searchTerm.toLowerCase()));
    return matchesFormat && matchesSearch;
  });

  // 当前激活选中的模板
  const activeTemplate =
    (templates || []).find((t) => t.name === activeTemplateName) ||
    filteredTemplates[0] ||
    null;

  // 当外部 store 变更或初始加载时同步 activeTemplateName
  useEffect(() => {
    if (selectedTemplate?.name) {
      setActiveTemplateName(selectedTemplate.name);
    } else if (!activeTemplateName && templates && templates.length > 0) {
      setActiveTemplateName(templates[0].name);
    }
  }, [selectedTemplate?.name, templates]);

  // 当选中的模板变化时，重置源码编辑器内容
  useEffect(() => {
    if (activeTemplate) {
      setEditorContent(activeTemplate.raw_content);
      setSelectedTemplate(activeTemplate);
    }
  }, [activeTemplate?.name, activeTemplate?.raw_content]);

  // 模板保存 Mutation
  const saveTemplateMutation = useMutation({
    mutationFn: () => {
      if (!activeTemplate) throw new Error('未选定模板');
      return api.saveTemplate(activeTemplate.name, editorContent);
    },
    onSuccess: (updatedTemplate) => {
      queryClient.setQueryData<TemplateItem[]>(['templates'], (prev) =>
        prev?.map((item) => (item.name === updatedTemplate.name ? updatedTemplate : item))
      );
      setSelectedTemplate(updatedTemplate);
      refetchTemplates();
      setSaveSuccess(true);
      setTimeout(() => setSaveSuccess(false), 2500);
    },
  });

  // 支持 Ctrl+S / Cmd+S 快捷保存
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key === 's') {
        e.preventDefault();
        if (activeTemplate && !saveTemplateMutation.isPending) {
          saveTemplateMutation.mutate();
        }
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [activeTemplate, editorContent, saveTemplateMutation]);

  // 跳转并在命令控制库中应用此模板
  const handleUseInCommands = (tpl: TemplateItem) => {
    setSelectedTemplate(tpl);
    const targetCommand = tpl.applicable_commands.includes(selectedCommandId) ? selectedCommandId : tpl.applicable_commands[0] || 'demod';
    setSelectedCommandId(targetCommand);
    setActiveTab('commands');
  };

  const handleCopyContent = () => {
    navigator.clipboard.writeText(editorContent);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const isDirty = activeTemplate ? editorContent !== activeTemplate.raw_content : false;

  return (
    <div className="flex h-[calc(100vh-3.5rem)] overflow-hidden select-none">
      {/* 左侧：模板库目录检索 */}
      <div className="w-84 bg-rf-surface/95 border-r border-rf-border flex flex-col select-none shrink-0 shadow-lg">
        {/* 顶部检索栏 */}
        <div className="p-4 border-b border-rf-border space-y-3 bg-rf-bg/30">
          <div className="flex items-center justify-between">
            <h2 className="text-xs font-bold font-mono uppercase text-rf-text flex items-center space-x-2">
              <FileCode className="w-4 h-4 text-cyan-400" />
              <span>模板预设库</span>
              <span className="text-[10px] px-1.5 py-0.2 rounded bg-cyan-950/60 text-cyan-400 font-mono border border-cyan-800/60">
                templates/
              </span>
            </h2>
            <span className="text-[11px] font-mono text-zinc-500">
              共 {filteredTemplates.length} 项
            </span>
          </div>

          {/* 搜索框 */}
          <div className="relative">
            <Search className="w-3.5 h-3.5 absolute left-3 top-2.5 text-zinc-500" />
            <input
              type="text"
              placeholder="搜索模板名称 / 描述 / 命令..."
              value={searchTerm}
              onChange={(e) => setSearchTerm(e.target.value)}
              className="w-full bg-rf-bg border border-rf-border rounded-lg pl-9 pr-3 py-1.5 text-xs text-rf-text placeholder-zinc-500 focus:outline-none focus:border-cyan-400/80 transition-colors"
            />
          </div>

          {/* 格式类型筛选 */}
          <div className="flex items-center space-x-1 p-0.5 bg-rf-bg/70 rounded-lg border border-rf-border/80">
            {(
              [
                { id: 'all', label: '全部' },
                { id: 'physical_config', label: '物理参数' },
                { id: 'rf_config', label: '射频跳频' },
                { id: 'message_payload', label: '业务报文' },
              ] as const
            ).map((filter) => (
              <button
                key={filter.id}
                onClick={() => setFormatFilter(filter.id)}
                className={`flex-1 py-1 text-[11px] font-medium rounded-md transition-all ${
                  formatFilter === filter.id
                    ? 'bg-rf-panel text-cyan-300 font-semibold shadow-xs'
                    : 'text-zinc-400 hover:text-zinc-200'
                }`}
              >
                {filter.label}
              </button>
            ))}
          </div>
        </div>

        {/* 模板卡片列表 */}
        <div className="flex-1 overflow-y-auto p-2.5 space-y-2">
          {isLoading ? (
            <div className="p-8 text-center text-xs text-zinc-500 font-mono">正在加载模板列表...</div>
          ) : filteredTemplates.length === 0 ? (
            <div className="p-8 text-center text-xs text-zinc-500 font-mono">
              未找到匹配的模板
            </div>
          ) : (
            filteredTemplates.map((tpl) => {
              const isActive = activeTemplate?.name === tpl.name;
              return (
                <div
                  key={tpl.name}
                  onClick={() => setActiveTemplateName(tpl.name)}
                  className={`p-3 rounded-xl border transition-all cursor-pointer text-left relative group ${
                    isActive
                      ? 'bg-gradient-to-r from-cyan-950/40 via-rf-surface to-rf-surface border-cyan-400/80 shadow-md shadow-cyan-500/10'
                      : 'bg-rf-surface/80 hover:bg-rf-panel/70 border-rf-border/80 hover:border-zinc-500'
                  }`}
                >
                  <div className="flex items-center justify-between mb-1.5">
                    <div className="flex items-center space-x-2 min-w-0">
                      {tpl.format === 'toml' ? (
                        <SlidersHorizontal className="w-3.5 h-3.5 text-cyan-400 shrink-0" />
                      ) : (
                        <FileCode className="w-3.5 h-3.5 text-amber-400 shrink-0" />
                      )}
                      <span className={`text-xs font-bold font-mono truncate ${isActive ? 'text-cyan-300' : 'text-zinc-200'}`}>
                        {tpl.title}
                      </span>
                    </div>
                    <span
                      className={`text-[10px] px-1.5 py-0.2 rounded uppercase font-semibold font-mono ${
                        tpl.format === 'toml'
                          ? 'bg-cyan-950/60 text-cyan-300 border border-cyan-800/50'
                          : 'bg-amber-950/60 text-amber-300 border border-amber-800/50'
                      }`}
                    >
                      {tpl.format}
                    </span>
                  </div>

                  <p className="text-[11px] text-zinc-400 line-clamp-2 leading-relaxed mb-2 font-sans">
                    {tpl.description}
                  </p>

                  <div className="flex items-center justify-between text-[10px] text-zinc-500 font-mono">
                    <span className="truncate max-w-[150px]">{tpl.name}</span>
                    <div className="flex space-x-1 shrink-0">
                      {tpl.applicable_commands.slice(0, 2).map((cmd) => (
                        <span key={cmd} className="px-1.5 py-0.2 rounded bg-rf-bg/80 text-zinc-400 border border-rf-border/60">
                          {cmd}
                        </span>
                      ))}
                      {tpl.applicable_commands.length > 2 && (
                        <span className="px-1 py-0.2 rounded bg-rf-bg/80 text-zinc-500">
                          +{tpl.applicable_commands.length - 2}
                        </span>
                      )}
                    </div>
                  </div>
                </div>
              );
            })
          )}
        </div>
      </div>

      {/* 右侧：模板详情、源码编辑与结构化解析 */}
      <div className="flex-1 flex flex-col bg-rf-bg overflow-hidden">
        {activeTemplate ? (
          <>
            {/* 顶部工具栏 */}
            <div className="p-4 border-b border-rf-border bg-rf-surface flex items-center justify-between shadow-xs">
              <div className="space-y-1 min-w-0">
                <div className="flex items-center space-x-2.5">
                  {activeTemplate.format === 'toml' ? (
                    <SlidersHorizontal className="w-5 h-5 text-cyan-400 shrink-0" />
                  ) : (
                    <FileCode className="w-5 h-5 text-amber-400 shrink-0" />
                  )}
                  <h1 className="text-base font-bold text-white font-mono tracking-tight truncate">
                    {activeTemplate.title}
                  </h1>
                  <span
                    className={`text-[10px] px-2 py-0.5 rounded font-mono uppercase font-semibold ${
                      activeTemplate.format === 'toml'
                        ? 'bg-cyan-950/70 text-cyan-300 border border-cyan-800/60'
                        : 'bg-amber-950/70 text-amber-300 border border-amber-800/60'
                    }`}
                  >
                    {activeTemplate.format}
                  </span>
                  <span className="text-xs font-mono text-zinc-500 bg-rf-panel px-2 py-0.5 rounded border border-rf-border/60">
                    {activeTemplate.path}
                  </span>
                </div>
                <p className="text-xs text-zinc-400 font-sans">{activeTemplate.description}</p>
              </div>

              {/* 动作按钮组 */}
              <div className="flex items-center space-x-2 shrink-0">
                <button
                  type="button"
                  onClick={handleCopyContent}
                  className="px-3 py-1.5 rounded-lg bg-rf-panel hover:bg-rf-border text-zinc-300 text-xs font-mono flex items-center space-x-1.5 border border-rf-border/80 transition-colors"
                  title="复制源码到剪贴板"
                >
                  {copied ? <Check className="w-3.5 h-3.5 text-cyan-400" /> : <Copy className="w-3.5 h-3.5" />}
                  <span>{copied ? '已复制' : '复制'}</span>
                </button>

                {isDirty && (
                  <button
                    type="button"
                    onClick={() => setEditorContent(activeTemplate.raw_content)}
                    className="px-3 py-1.5 rounded-lg bg-rf-panel hover:bg-rf-border text-zinc-400 hover:text-zinc-200 text-xs font-mono flex items-center space-x-1.5 border border-rf-border/80 transition-colors"
                    title="重置为文件初始内容"
                  >
                    <RotateCcw className="w-3.5 h-3.5" />
                    <span>放弃修改</span>
                  </button>
                )}

                <button
                  type="button"
                  onClick={() => saveTemplateMutation.mutate()}
                  disabled={saveTemplateMutation.isPending || !isDirty}
                  className={`px-3.5 py-1.5 rounded-lg text-xs font-mono font-bold flex items-center space-x-1.5 transition-all shadow-sm ${
                    saveSuccess
                      ? 'bg-emerald-600 text-white'
                      : isDirty
                      ? 'bg-cyan-500 hover:bg-cyan-400 text-black shadow-cyan-500/20'
                      : 'bg-rf-panel text-zinc-500 border border-rf-border cursor-not-allowed opacity-60'
                  }`}
                  title="保存对磁盘模板文件的修改 (Ctrl+S)"
                >
                  {saveSuccess ? (
                    <Check className="w-3.5 h-3.5 text-white" />
                  ) : (
                    <Save className="w-3.5 h-3.5" />
                  )}
                  <span>
                    {saveSuccess
                      ? '已保存至磁盘！'
                      : saveTemplateMutation.isPending
                      ? '保存中...'
                      : '保存修改'}
                  </span>
                </button>

                <button
                  type="button"
                  onClick={() => handleUseInCommands(activeTemplate)}
                  className="px-4 py-1.5 rounded-lg bg-gradient-to-r from-cyan-600 to-cyan-500 hover:from-cyan-500 hover:to-cyan-400 text-black font-bold text-xs font-mono flex items-center space-x-1.5 shadow-md shadow-cyan-500/20 transition-all ml-1"
                  title="在命令控制库中应用此模板"
                >
                  <Play className="w-3.5 h-3.5 fill-black" />
                  <span>在命令库中使用</span>
                </button>
              </div>
            </div>

            {/* 模式切换选项卡 */}
            <div className="flex items-center justify-between px-4 py-2 border-b border-rf-border/80 bg-rf-surface/60">
              <div className="flex items-center space-x-2">
                <button
                  type="button"
                  onClick={() => setActiveTabMode('editor')}
                  className={`px-3 py-1.5 rounded-lg text-xs font-mono flex items-center space-x-1.5 transition-all ${
                    activeTabMode === 'editor'
                      ? 'bg-cyan-500/20 text-cyan-300 border border-cyan-400/80 font-bold'
                      : 'text-zinc-400 hover:text-zinc-200'
                  }`}
                >
                  <Code2 className="w-3.5 h-3.5" />
                  <span>源码编辑 (Source Code)</span>
                  {isDirty && (
                    <span className="w-2 h-2 rounded-full bg-amber-400 animate-pulse ml-1" title="未保存更改" />
                  )}
                </button>

                <button
                  type="button"
                  onClick={() => setActiveTabMode('inspector')}
                  className={`px-3 py-1.5 rounded-lg text-xs font-mono flex items-center space-x-1.5 transition-all ${
                    activeTabMode === 'inspector'
                      ? 'bg-cyan-500/20 text-cyan-300 border border-cyan-400/80 font-bold'
                      : 'text-zinc-400 hover:text-zinc-200'
                  }`}
                >
                  <Layers className="w-3.5 h-3.5" />
                  <span>结构化解析 (Structured Inspector)</span>
                </button>
              </div>

              <div className="flex items-center space-x-2 text-[11px] font-mono text-zinc-500">
                <span>适用命令:</span>
                <div className="flex space-x-1">
                  {activeTemplate.applicable_commands.map((cmd) => (
                    <button
                      key={cmd}
                      type="button"
                      onClick={() => {
                        setSelectedTemplate(activeTemplate);
                        setSelectedCommandId(cmd);
                        setActiveTab('commands');
                      }}
                      className="px-2 py-0.5 rounded bg-rf-panel hover:bg-cyan-950/60 text-cyan-400/90 border border-rf-border/60 hover:border-cyan-800/80 transition-colors"
                      title={`在 ${cmd} 命令中使用`}
                    >
                      {cmd}
                    </button>
                  ))}
                </div>
              </div>
            </div>

            {/* 内容区 */}
            <div className="flex-1 overflow-y-auto p-4">
              {saveTemplateMutation.error && (
                <div className="mb-4 p-3 rounded-lg bg-red-950/40 border border-red-800/80 text-red-300 text-xs flex items-center space-x-2 font-mono">
                  <AlertCircle className="w-4 h-4 text-red-400 shrink-0" />
                  <span>保存失败: {saveTemplateMutation.error.message}</span>
                </div>
              )}

              {activeTabMode === 'editor' ? (
                /* 源码编辑视图 */
                <div className="flex flex-col h-full space-y-2">
                  <div className="flex items-center justify-between text-xs text-zinc-400 font-mono px-1">
                    <span>
                      编辑 templates/{activeTemplate.name} ({activeTemplate.format.toUpperCase()})
                    </span>
                    <span className="text-[11px] text-zinc-500">
                      支持快捷键 Ctrl+S 保存 | 共 {editorContent.split('\n').length} 行
                    </span>
                  </div>
                  <textarea
                    value={editorContent}
                    onChange={(e) => setEditorContent(e.target.value)}
                    className="flex-1 w-full bg-rf-surface border border-rf-border rounded-xl p-4 font-mono text-xs text-cyan-200 focus:outline-none focus:border-cyan-400 focus:ring-1 focus:ring-cyan-400/40 resize-none leading-relaxed shadow-inner"
                    spellCheck={false}
                  />
                </div>
              ) : (
                /* 结构化解析视图 */
                <div className="space-y-4">
                  {activeTemplate.category === 'rf_config' && activeTemplate.parsed && (
                    <div className="bg-rf-surface border border-rf-border rounded-xl p-4 space-y-3">
                      <h3 className="text-sm font-semibold text-cyan-300">射频跳频表 · 按时隙顺序</h3>
                      <p className="text-xs text-zinc-400">{activeTemplate.description}</p>
                      <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
                        {(activeTemplate.parsed.hopping_table || activeTemplate.parsed.radio_config?.hopping_table || []).map((entry: number | { frequency_hz: number }, index: number) => (
                          <div key={index} className="bg-rf-bg border border-rf-border rounded-lg p-2 text-xs font-mono">
                            <span className="text-zinc-500">Slot {index}</span>
                            <div className="text-cyan-300">{((typeof entry === 'number' ? entry : entry.frequency_hz) / 1e6).toFixed(6)} MHz</div>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}
                  {activeTemplate.category === 'message_payload' && activeTemplate.parsed && (
                    <div className="bg-rf-surface border border-rf-border rounded-xl overflow-hidden shadow-md">
                      <div className="p-3.5 border-b border-rf-border bg-rf-panel/50 flex items-center justify-between">
                        <div className="flex items-center space-x-2">
                          <Radio className="w-4 h-4 text-amber-400" />
                          <h3 className="text-xs font-bold font-mono uppercase text-rf-text">
                            JSON 报文载荷结构化监视器 (Payload Inspector)
                          </h3>
                        </div>
                        <div className="flex items-center space-x-3 text-[11px] font-mono">
                          <span className="text-zinc-400">
                            指令: <span className="text-cyan-300 font-bold">{activeTemplate.parsed.command || '0x00'}</span>
                          </span>
                          <span className="text-zinc-400">
                            传输方向:{' '}
                            <span className="text-amber-300">
                              {activeTemplate.parsed.direction === 'ground_to_air'
                                ? '地面端 -> 天空端 (下行)'
                                : '天空端 -> 地面端 (上行)'}
                            </span>
                          </span>
                        </div>
                      </div>

                      <div className="p-4 space-y-4">
                        {/* 16 通道遥控摇杆监视器 */}
                        {Array.isArray(activeTemplate.parsed?.fields?.channels_raw) && (
                          <div className="space-y-2">
                            <div className="flex items-center justify-between text-xs font-mono text-zinc-300">
                              <span className="flex items-center space-x-1.5">
                                <Sliders className="w-3.5 h-3.5 text-cyan-400" />
                                <span>16 通道遥控原始数值映射 (Raw Channel Values)</span>
                              </span>
                              <span className="text-[11px] text-zinc-500">单位: 0..4095 (中立 2048)</span>
                            </div>
                            <div className="grid grid-cols-2 sm:grid-cols-4 md:grid-cols-8 gap-2">
                              {activeTemplate.parsed.fields.channels_raw.map(
                                (chVal: number, idx: number) => {
                                  const pct = Math.min(
                                    100,
                                    Math.max(0, ((chVal - 1000) / (3000 - 1000)) * 100)
                                  );
                                  return (
                                    <div
                                      key={idx}
                                      className="p-2 rounded-lg bg-rf-bg border border-rf-border/80 flex flex-col space-y-1.5"
                                    >
                                      <div className="flex justify-between items-center text-[10px] font-mono">
                                        <span className="text-zinc-500">CH{idx + 1}</span>
                                        <span className="text-cyan-300 font-bold">{chVal}</span>
                                      </div>
                                      <div className="w-full bg-rf-panel h-1.5 rounded-full overflow-hidden">
                                        <div
                                          className="bg-cyan-500 h-full rounded-full transition-all"
                                          style={{ width: `${pct}%` }}
                                        />
                                      </div>
                                    </div>
                                  );
                                }
                              )}
                            </div>
                          </div>
                        )}

                        {/* 自定义字段属性 */}
                        {activeTemplate.parsed?.fields && (
                          <div className="space-y-2">
                            <span className="text-xs font-mono text-zinc-400">字段属性表 (Payload Fields):</span>
                            <div className="grid grid-cols-2 md:grid-cols-3 gap-2">
                              {Object.entries(activeTemplate.parsed.fields)
                                .filter(([k]) => k !== 'channels_raw')
                                .map(([key, val]) => (
                                  <div
                                    key={key}
                                    className="p-2.5 rounded-lg bg-rf-bg border border-rf-border flex justify-between items-center text-xs font-mono"
                                  >
                                    <span className="text-zinc-400">{key}:</span>
                                    <span className="text-amber-300 font-semibold truncate ml-2">
                                      {typeof val === 'object' ? JSON.stringify(val) : String(val)}
                                    </span>
                                  </div>
                                ))}
                            </div>
                          </div>
                        )}

                        {/* 动态增量更新规则 */}
                        {activeTemplate.parsed?.updates && (
                          <div className="space-y-2 pt-2 border-t border-rf-border/60">
                            <span className="text-xs font-mono text-zinc-400">
                              动态序号/字段变化规则 (Runtime Updates):
                            </span>
                            <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
                              {Object.entries(activeTemplate.parsed.updates).map(
                                ([field, rule]: [string, any]) => (
                                  <div
                                    key={field}
                                    className="p-2.5 rounded-lg bg-rf-bg border border-rf-border flex justify-between items-center text-xs font-mono"
                                  >
                                    <span className="text-cyan-300">{field}:</span>
                                    <span className="text-zinc-400 text-[11px]">
                                      {rule.type} (起始: {rule.start}, 步长: {rule.step}, 模: {rule.modulo})
                                    </span>
                                  </div>
                                )
                              )}
                            </div>
                          </div>
                        )}
                      </div>
                    </div>
                  )}

                  {activeTemplate.format === 'toml' && activeTemplate.parsed && (
                    <div className="bg-rf-surface border border-rf-border rounded-xl overflow-hidden shadow-md space-y-4 p-4">
                      <div className="flex items-center space-x-2 text-xs font-bold font-mono uppercase text-rf-text border-b border-rf-border pb-3">
                        <SlidersHorizontal className="w-4 h-4 text-cyan-400" />
                        <span>TOML 物理层参数解析预览</span>
                      </div>

                      {/* [demod] 区域参数 */}
                      {activeTemplate.parsed.demod && (
                        <div className="space-y-2">
                          <span className="text-xs font-mono text-cyan-300 font-semibold">[demod] 解调参数:</span>
                          <div className="grid grid-cols-2 md:grid-cols-3 gap-2 text-xs font-mono">
                            {Object.entries(activeTemplate.parsed.demod).map(([k, v]) => (
                              <div
                                key={k}
                                className="p-2 rounded-lg bg-rf-bg border border-rf-border flex justify-between items-center"
                              >
                                <span className="text-zinc-400">{k}:</span>
                                <span className="text-cyan-300 font-bold truncate ml-2">
                                  {Array.isArray(v) ? v.join(', ') : String(v)}
                                </span>
                              </div>
                            ))}
                          </div>
                        </div>
                      )}

                      {/* [transmit] 区域参数 */}
                      {activeTemplate.parsed.transmit && (
                        <div className="space-y-2 pt-2 border-t border-rf-border/60">
                          <span className="text-xs font-mono text-amber-300 font-semibold">[transmit] 发射参数:</span>
                          <div className="grid grid-cols-2 md:grid-cols-3 gap-2 text-xs font-mono">
                            {Object.entries(activeTemplate.parsed.transmit).map(([k, v]) => (
                              <div
                                key={k}
                                className="p-2 rounded-lg bg-rf-bg border border-rf-border flex justify-between items-center"
                              >
                                <span className="text-zinc-400">{k}:</span>
                                <span className="text-amber-300 font-bold truncate ml-2">
                                  {Array.isArray(v) ? v.join(', ') : String(v)}
                                </span>
                              </div>
                            ))}
                          </div>
                        </div>
                      )}
                    </div>
                  )}

                  {/* 原始 JSON 树形结构 */}
                  <div className="bg-rf-surface border border-rf-border rounded-xl p-4 space-y-2 shadow-md">
                    <span className="text-xs font-mono text-zinc-400">完整解析数据 (Parsed Data):</span>
                    <pre className="p-3.5 rounded-lg bg-rf-bg text-cyan-200/90 font-mono text-xs overflow-x-auto max-h-72 border border-rf-border/80 leading-relaxed">
                      {JSON.stringify(activeTemplate.parsed, null, 2)}
                    </pre>
                  </div>
                </div>
              )}
            </div>
          </>
        ) : (
          <div className="flex-1 flex flex-col items-center justify-center text-zinc-500 font-mono space-y-3">
            <FileCode className="w-12 h-12 text-zinc-600 stroke-[1.5]" />
            <p className="text-xs">请在左侧列表中选择一个模板查看、编辑或在命令库中使用</p>
          </div>
        )}
      </div>
    </div>
  );
};
