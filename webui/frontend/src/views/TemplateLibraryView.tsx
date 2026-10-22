import React, { useState, useEffect, useMemo } from 'react';
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
  AlertTriangle,
  Plus,
  Trash2,
  X,
  FolderPlus,
} from 'lucide-react';
import { api } from '../api/client';
import { TemplateItem } from '../types';
import { useWorkstationStore } from '../stores/useWorkstationStore';
import { CopyButton } from '../components/CopyButton';
import { MasterDetailBackButton } from '../components/MasterDetailBackButton';
import { useMasterDetail } from '../hooks/useMasterDetail';

const STARTER_TEMPLATES: Record<
  TemplateItem['category'],
  (title: string, desc: string) => { ext: string; folder: string; content: string }
> = {
  physical_config: (title, desc) => ({
    ext: '.toml',
    folder: 'physical',
    content: `[template]
schema_version = 1
kind = "physical_config"
title = "${title || '自定义物理层参数预设'}"
description = "${desc || 'Semtech SX1280 物理层参数配置'}"
protocol = "dk32s"

# 物理层参数定义
[demod]
sample_rate = 2500000.0
bit_rate = 325000.0
coding_rate = "3/4"
bt = 1.0
chunk_seconds = 0.1
header_type = "variable"
crc_bytes = 3
crc_initial = 0xFFFFFF
`,
  }),
  rf_config: (title, desc) => ({
    ext: '.json',
    folder: 'rf',
    content: JSON.stringify(
      {
        template: {
          schema_version: 1,
          kind: 'rf_config',
          title: title || '自定义射频跳频配置',
          description: desc || '跳频频点列表与物理差分同步字',
          protocol: 'dk32s',
        },
        sync_word: 'f264709b',
        hopping_table: [
          2419260000,
          2424260000,
          2429260000,
          2434260000,
          2439260000,
        ],
      },
      null,
      2
    ),
  }),
  message_payload: (title, desc) => ({
    ext: '.json',
    folder: 'messages',
    content: JSON.stringify(
      {
        template: {
          schema_version: 1,
          kind: 'message_payload',
          title: title || '自定义业务控制报文',
          description: desc || '协议业务控制指令',
          protocol: 'dk32s',
        },
        command: '0x23',
        direction: 'request',
        fields: {},
      },
      null,
      2
    ),
  }),
};

export const TemplateLibraryView: React.FC = () => {
  const queryClient = useQueryClient();
  const {
    selectedTemplate,
    setSelectedTemplate,
  } = useWorkstationStore();

  const [searchTerm, setSearchTerm] = useState('');
  const [formatFilter, setFormatFilter] = useState<'all' | TemplateItem['category']>('all');
  const [activeTemplateName, setActiveTemplateName] = useState<string | null>(
    selectedTemplate?.name || null
  );
  const [editorContent, setEditorContent] = useState<string>('');
  const [activeTabMode, setActiveTabMode] = useState<'editor' | 'inspector'>('editor');
  const [saveSuccess, setSaveSuccess] = useState<boolean>(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const { detailOpen, workspaceRef, openDetail, showList } = useMasterDetail(Boolean(selectedTemplate));

  // 删除确认弹窗状态
  const [templateToDelete, setTemplateToDelete] = useState<TemplateItem | null>(null);
  const [deleteError, setDeleteError] = useState<string | null>(null);

  // 新建模板弹窗状态
  const [isCreateOpen, setIsCreateOpen] = useState(false);
  const [createCategory, setCreateCategory] = useState<TemplateItem['category']>('physical_config');
  const [createFilename, setCreateFilename] = useState('');
  const [createTitle, setCreateTitle] = useState('');
  const [createDescription, setCreateDescription] = useState('');
  const [createContent, setCreateContent] = useState('');
  const [createError, setCreateError] = useState<string | null>(null);

  const { data: templates, refetch: refetchTemplates, isLoading, error: templatesError } = useQuery({
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

  const jsonSyntaxError = useMemo(() => {
    if (activeTemplate?.format !== 'json' || !editorContent.trim()) return null;
    try {
      JSON.parse(editorContent);
      return null;
    } catch (err: any) {
      return err.message || 'JSON 语法错误';
    }
  }, [activeTemplate?.format, editorContent]);

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
      setSaveError(null);
    }
  }, [activeTemplate?.name, activeTemplate?.raw_content]);

  // 模板保存 Mutation
  const saveTemplateMutation = useMutation({
    mutationFn: () => {
      if (!activeTemplate) throw new Error('未选定模板');
      setSaveError(null);
      return api.saveTemplate(activeTemplate.name, editorContent);
    },
    onSuccess: (updatedTemplate) => {
      queryClient.setQueryData<TemplateItem[]>(['templates'], (prev) =>
        prev?.map((item) => (item.name === updatedTemplate.name ? updatedTemplate : item))
      );
      setSelectedTemplate(updatedTemplate);
      refetchTemplates();
      setSaveSuccess(true);
      setSaveError(null);
      setTimeout(() => setSaveSuccess(false), 2500);
    },
    onError: (err: any) => {
      setSaveError(err.message || '保存模板失败，请检查文件语法或元数据格式');
    },
  });

  // 删除模板 Mutation
  const deleteTemplateMutation = useMutation({
    mutationFn: (name: string) => {
      setDeleteError(null);
      return api.deleteTemplate(name);
    },
    onSuccess: (_, deletedName) => {
      queryClient.setQueryData<TemplateItem[]>(['templates'], (prev) =>
        prev?.filter((item) => item.name !== deletedName)
      );
      refetchTemplates();
      if (selectedTemplate?.name === deletedName) {
        setSelectedTemplate(null);
      }
      if (activeTemplateName === deletedName) {
        const remaining = (templates || []).filter((t) => t.name !== deletedName);
        setActiveTemplateName(remaining[0]?.name || null);
      }
      setTemplateToDelete(null);
    },
    onError: (err: any) => {
      setDeleteError(err.message || '删除模板失败，请稍后重试');
    },
  });

  // 打开新建弹窗并填充默认模板
  const handleOpenCreateModal = () => {
    const defaultCategory: TemplateItem['category'] =
      formatFilter !== 'all' ? formatFilter : 'physical_config';
    setCreateCategory(defaultCategory);
    setCreateFilename('');
    setCreateTitle('');
    setCreateDescription('');
    const starter = STARTER_TEMPLATES[defaultCategory]('', '');
    setCreateContent(starter.content);
    setCreateError(null);
    setIsCreateOpen(true);
  };

  // 切换新建模板的分类时更新初始骨架
  const handleCategoryChange = (category: TemplateItem['category']) => {
    setCreateCategory(category);
    const starter = STARTER_TEMPLATES[category](createTitle, createDescription);
    setCreateContent(starter.content);
    setCreateError(null);
  };

  // 新建模板 Mutation
  const createTemplateMutation = useMutation({
    mutationFn: async () => {
      setCreateError(null);
      const starter = STARTER_TEMPLATES[createCategory](createTitle, createDescription);
      let rawName = createFilename.trim();
      if (!rawName) throw new Error('请输入模板文件名');
      if (rawName.endsWith(starter.ext)) {
        rawName = rawName.slice(0, -starter.ext.length);
      }
      if (!rawName) throw new Error('请输入有效的文件名');
      if (/[\/\\:*?"<>|]/.test(rawName)) {
        throw new Error('文件名包含非法字符，仅允许使用字母、数字、下划线及中划线');
      }
      const finalFullName = `${starter.folder}/${rawName}${starter.ext}`;
      const exists = (templates || []).some(
        (t) => t.name.toLowerCase() === finalFullName.toLowerCase()
      );
      if (exists) {
        throw new Error(`模板 ${finalFullName} 已存在，请更换名称`);
      }
      return api.saveTemplate(finalFullName, createContent);
    },
    onSuccess: (newTemplate) => {
      queryClient.setQueryData<TemplateItem[]>(['templates'], (prev) =>
        prev ? [...prev, newTemplate] : [newTemplate]
      );
      refetchTemplates();
      setActiveTemplateName(newTemplate.name);
      setSelectedTemplate(newTemplate);
      setIsCreateOpen(false);
      openDetail();
    },
    onError: (err: any) => {
      setCreateError(err.message || '创建模板失败，请检查文件语法');
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

  const isDirty = activeTemplate ? editorContent !== activeTemplate.raw_content : false;

  return (
    <div ref={workspaceRef} data-detail-open={detailOpen} className="master-detail flex h-full overflow-hidden select-none relative">
      {/* 左侧：模板库目录检索 */}
      <div className="master-detail-list w-80 bg-rf-surface/95 border-r border-rf-border flex flex-col select-none shrink-0 shadow-lg">
        {/* 顶部检索与新建操作栏 */}
        <div className="p-4 border-b border-rf-border space-y-3 bg-rf-bg/30">
          <div className="flex items-center justify-between">
            <h2 className="text-xs font-bold font-mono uppercase text-rf-text flex items-center space-x-2">
              <FileCode className="w-4 h-4 text-cyan-400" />
              <span>模板预设库</span>
              <span className="text-[10px] px-1.5 py-0.2 rounded bg-cyan-950/60 text-cyan-400 font-mono border border-cyan-800/60">
                templates/
              </span>
            </h2>
            <div className="flex items-center space-x-2">
              <span className="text-[11px] font-mono text-zinc-500">
                共 {filteredTemplates.length} 项
              </span>
              <button
                type="button"
                onClick={handleOpenCreateModal}
                className="px-2.5 py-1 rounded-lg bg-cyan-500/15 hover:bg-cyan-500/25 text-cyan-300 text-xs font-mono font-medium border border-cyan-500/40 hover:border-cyan-400 flex items-center space-x-1 transition-all shadow-xs"
                title="新建预设模板"
              >
                <Plus className="w-3.5 h-3.5" />
                <span>新建</span>
              </button>
            </div>
          </div>

          {/* 搜索框 */}
          <div className="relative">
            <Search className="w-3.5 h-3.5 absolute left-3 top-2.5 text-zinc-500" />
            <input
              type="search"
              aria-label="搜索模板"
              autoComplete="off"
              placeholder="搜索模板名称 / 描述 / 命令…"
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
                aria-pressed={formatFilter === filter.id}
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
            <div role="status" className="p-8 text-center text-xs text-zinc-400 font-mono">正在加载模板列表…</div>
          ) : templatesError ? (
            <div role="alert" className="p-4 space-y-2 text-xs text-red-400"><p>模板加载失败：{templatesError.message}</p><button type="button" onClick={() => refetchTemplates()} className="px-3 py-1.5 rounded bg-rf-panel hover:bg-rf-border text-cyan-300">重新加载</button></div>
          ) : filteredTemplates.length === 0 ? (
            <div className="p-8 text-center text-xs text-zinc-500 font-mono space-y-3">
              <p>未找到匹配的模板</p>
              <button
                type="button"
                onClick={handleOpenCreateModal}
                className="px-3 py-1.5 rounded-lg bg-rf-panel hover:bg-rf-border text-cyan-300 text-xs font-mono inline-flex items-center space-x-1.5 border border-rf-border"
              >
                <Plus className="w-3.5 h-3.5" />
                <span>新建模板</span>
              </button>
            </div>
          ) : (
            filteredTemplates.map((tpl) => {
              const isActive = activeTemplate?.name === tpl.name;
              return (
                <div
                  key={tpl.name}
                  className={`p-3 rounded-xl border transition-all cursor-pointer text-left relative group ${
                    isActive
                      ? 'bg-gradient-to-r from-cyan-950/40 via-rf-surface to-rf-surface border-cyan-400/80 shadow-md shadow-cyan-500/10'
                      : 'bg-rf-surface/80 hover:bg-rf-panel/70 border-rf-border/80 hover:border-zinc-500'
                  }`}
                >
                  <div className="flex items-center justify-between mb-1.5">
                    <div className="flex items-center space-x-2 min-w-0 pr-2">
                      {tpl.format === 'toml' ? (
                        <SlidersHorizontal className="w-3.5 h-3.5 text-cyan-400 shrink-0" />
                      ) : (
                        <FileCode className="w-3.5 h-3.5 text-amber-400 shrink-0" />
                      )}
                      <button
                        type="button"
                        onClick={() => { setActiveTemplateName(tpl.name); openDetail(); }}
                        aria-pressed={isActive}
                        aria-label={`编辑模板 ${tpl.title}`}
                        className={`text-left after:absolute after:inset-0 text-xs font-bold font-mono truncate ${
                          isActive ? 'text-cyan-300' : 'text-zinc-200'
                        }`}
                      >
                        {tpl.title}
                      </button>
                    </div>
                    <div className="flex items-center space-x-1.5 shrink-0">
                      <span
                        className={`text-[10px] px-1.5 py-0.2 rounded uppercase font-semibold font-mono ${
                          tpl.format === 'toml'
                            ? 'bg-cyan-950/60 text-cyan-300 border border-cyan-800/50'
                            : 'bg-amber-950/60 text-amber-300 border border-amber-800/50'
                        }`}
                      >
                        {tpl.format}
                      </span>
                      <button
                        type="button"
                        onClick={(e) => {
                          e.stopPropagation();
                          setTemplateToDelete(tpl);
                        }}
                        className="relative z-10 sm:opacity-0 group-hover:opacity-100 focus-visible:opacity-100 p-1 rounded hover:bg-rose-950/60 text-zinc-400 hover:text-rose-400 transition-colors"
                        aria-label={`删除模板 ${tpl.title}`}
                        title="删除该模板"
                      >
                        <Trash2 className="w-3.5 h-3.5" />
                      </button>
                    </div>
                  </div>

                  <p className="text-[11px] text-zinc-400 line-clamp-2 leading-relaxed mb-2 font-sans">
                    {tpl.description}
                  </p>

                  <div className="flex items-center justify-between text-[10px] text-zinc-500 font-mono">
                    <span className="truncate max-w-[150px]">{tpl.name}</span>
                    <div className="flex space-x-1 shrink-0">
                      {tpl.applicable_commands.slice(0, 2).map((cmd) => (
                        <span
                          key={cmd}
                          className="px-1.5 py-0.2 rounded bg-rf-bg/80 text-zinc-400 border border-rf-border/60"
                        >
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
      <div className="master-detail-content flex-1 flex flex-col bg-rf-bg overflow-y-auto">
        <MasterDetailBackButton label="模板列表" onClick={showList} />
        {activeTemplate ? (
          <>
            {/* 顶部工具栏 */}
            <div className="shrink-0 p-4 border-b border-rf-border bg-rf-surface flex flex-wrap gap-3 items-center justify-between shadow-xs">
              <div className="space-y-1 min-w-0">
                <div className="flex flex-wrap items-center gap-2.5">
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
                  <span className="text-xs font-mono text-zinc-400 bg-rf-panel px-2 py-0.5 rounded border border-rf-border/60 break-all">
                    {activeTemplate.path}
                  </span>
                </div>
                <p className="text-xs text-zinc-400 font-sans">{activeTemplate.description}</p>
              </div>

              {/* 动作按钮组 */}
              <div className="flex flex-wrap items-center gap-2">
                <CopyButton text={editorContent} label="复制源码" />


                {isDirty && (
                  <button
                    type="button"
                    onClick={() => {
                      setEditorContent(activeTemplate.raw_content);
                      setSaveError(null);
                    }}
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
                  onClick={() => setTemplateToDelete(activeTemplate)}
                  className="px-3 py-1.5 rounded-lg bg-rose-950/40 hover:bg-rose-900/60 text-rose-300 hover:text-white text-xs font-mono flex items-center space-x-1.5 border border-rose-800/60 hover:border-rose-600 transition-colors"
                  title="从磁盘永久删除当前模板"
                >
                  <Trash2 className="w-3.5 h-3.5" />
                  <span>删除</span>
                </button>
              </div>
            </div>

            {/* 保存错误提示条 */}
            {saveError && (
              <div className="px-4 py-2 bg-rose-950/70 border-b border-rose-800 text-rose-200 text-xs font-mono flex items-center justify-between">
                <div className="flex items-center space-x-2">
                  <AlertCircle className="w-4 h-4 text-rose-400 shrink-0" />
                  <span>{saveError}</span>
                </div>
                <button
                  type="button"
                  onClick={() => setSaveError(null)}
                  className="text-rose-400 hover:text-white"
                >
                  <X className="w-3.5 h-3.5" />
                </button>
              </div>
            )}

            {/* 模式切换选项卡 */}
            <div className="shrink-0 flex flex-wrap gap-2 items-center justify-between px-4 py-2 border-b border-rf-border/80 bg-rf-surface/60">
              <div className="flex flex-wrap items-center gap-2">
                <button
                  type="button"
                  onClick={() => setActiveTabMode('editor')}
                  aria-pressed={activeTabMode === 'editor'}
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
                  aria-pressed={activeTabMode === 'inspector'}
                  className={`px-3 py-1.5 rounded-lg text-xs font-mono flex items-center space-x-1.5 transition-all ${
                    activeTabMode === 'inspector'
                      ? 'bg-cyan-500/20 text-cyan-300 border border-cyan-400/80 font-bold'
                      : 'text-zinc-400 hover:text-zinc-200'
                  }`}
                >
                  <Sliders className="w-3.5 h-3.5" />
                  <span>结构化属性 (Inspector)</span>
                </button>
              </div>

              <div className="flex items-center space-x-3 text-xs font-mono text-zinc-500">
                <span>字符数: {editorContent.length}</span>
                <span>行数: {editorContent.split('\n').length}</span>
              </div>
            </div>

            {/* 主内容区域 */}
            <div className="flex-1 min-h-64 overflow-hidden p-3 sm:p-4">
              {activeTabMode === 'editor' ? (
                <div className="h-full flex flex-col space-y-2">
                  <div className="flex flex-wrap gap-2 items-center justify-between text-xs font-mono text-zinc-400 pb-1 break-all">
                    <span className="flex items-center space-x-1.5">
                      <Tag className="w-3.5 h-3.5 text-cyan-400" />
                      <span>正在编辑: {activeTemplate.path}</span>
                    </span>
                    <div className="flex items-center space-x-3">
                      {jsonSyntaxError && (
                        <span className="text-amber-400 flex items-center space-x-1 text-[11px] bg-amber-950/40 px-2 py-0.5 rounded border border-amber-800/60 font-mono">
                          <AlertTriangle className="w-3 h-3 text-amber-400 shrink-0" />
                          <span className="truncate max-w-xs">{jsonSyntaxError}</span>
                        </span>
                      )}
                      <span>快捷键: Ctrl+S 保存</span>
                    </div>
                  </div>
                  <textarea
                    aria-label={`模板源码 ${activeTemplate.name}`}
                    value={editorContent}
                    onChange={(e) => setEditorContent(e.target.value)}
                    spellCheck={false}
                    className="flex-1 w-full bg-rf-surface border border-rf-border rounded-xl p-4 font-mono text-xs text-cyan-200 focus:outline-none focus:border-cyan-400 focus:ring-1 focus:ring-cyan-400/40 resize-none leading-relaxed shadow-inner"
                  />
                </div>
              ) : (
                <div className="h-full overflow-y-auto space-y-4 pr-1">
                  {/* 核心元数据卡片 */}
                  <div className="bg-rf-surface border border-rf-border rounded-xl p-4 space-y-3 shadow-md">
                    <div className="flex items-center space-x-2 text-xs font-bold font-mono uppercase text-rf-text border-b border-rf-border pb-2.5">
                      <Info className="w-4 h-4 text-cyan-400" />
                      <span>模板元信息</span>
                    </div>
                    <div className="grid grid-cols-2 gap-4 text-xs font-mono">
                      <div className="space-y-1">
                        <span className="text-zinc-500">模板唯一标识 (Name):</span>
                        <div className="p-2 rounded-lg bg-rf-bg border border-rf-border text-cyan-300 font-bold">
                          {activeTemplate.name}
                        </div>
                      </div>
                      <div className="space-y-1">
                        <span className="text-zinc-500">磁盘相对路径 (Path):</span>
                        <div className="p-2 rounded-lg bg-rf-bg border border-rf-border text-zinc-300 truncate">
                          {activeTemplate.path}
                        </div>
                      </div>
                      <div className="space-y-1">
                        <span className="text-zinc-500">分类 (Category):</span>
                        <div className="p-2 rounded-lg bg-rf-bg border border-rf-border text-zinc-300">
                          {activeTemplate.category === 'physical_config'
                            ? '物理参数 (physical_config)'
                            : activeTemplate.category === 'rf_config'
                            ? '射频跳频 (rf_config)'
                            : '业务报文 (message_payload)'}
                        </div>
                      </div>
                      <div className="space-y-1">
                        <span className="text-zinc-500">文件语法格式 (Format):</span>
                        <div className="p-2 rounded-lg bg-rf-bg border border-rf-border text-zinc-300 uppercase font-semibold">
                          {activeTemplate.format}
                        </div>
                      </div>
                    </div>
                  </div>

                  {/* 适用命令徽章 */}
                  <div className="bg-rf-surface border border-rf-border rounded-xl p-4 space-y-3 shadow-md">
                    <div className="flex items-center space-x-2 text-xs font-bold font-mono uppercase text-rf-text border-b border-rf-border pb-2.5">
                      <Terminal className="w-4 h-4 text-emerald-400" />
                      <span>适用命令入口 (Applicable Commands)</span>
                    </div>
                    <div className="flex flex-wrap gap-2">
                      {activeTemplate.applicable_commands.map((cmd) => (
                        <span
                          key={cmd}
                          className="px-3 py-1.5 rounded-lg bg-rf-bg border border-rf-border text-cyan-300 font-mono text-xs flex items-center space-x-1.5"
                        >
                          <Play className="w-3 h-3 fill-cyan-400 text-cyan-400" />
                          <span>flrc {cmd}</span>
                        </span>
                      ))}
                    </div>
                  </div>

                  {/* 射频跳频专用结构预览 */}
                  {activeTemplate.category === 'rf_config' && activeTemplate.parsed && (
                    <div className="bg-rf-surface border border-rf-border rounded-xl overflow-hidden shadow-md space-y-4 p-4">
                      <div className="flex items-center space-x-2 text-xs font-bold font-mono uppercase text-rf-text border-b border-rf-border pb-3">
                        <Radio className="w-4 h-4 text-amber-400" />
                        <span>射频跳频配置详情</span>
                      </div>
                      <div className="space-y-3 text-xs font-mono">
                        <div className="flex items-center space-x-4">
                          <span className="text-zinc-400">差分物理同步字:</span>
                          <span className="px-2 py-0.5 rounded bg-amber-950/60 border border-amber-800 text-amber-300 font-bold">
                            {activeTemplate.parsed.sync_word || activeTemplate.parsed.sync || '空口恢复'}
                          </span>
                        </div>
                        {Array.isArray(activeTemplate.parsed.hopping_table) && (
                          <div className="space-y-1.5">
                            <span className="text-zinc-400">
                              跳频表 ({activeTemplate.parsed.hopping_table.length} 个频点):
                            </span>
                            <div className="grid grid-cols-2 md:grid-cols-4 gap-2 max-h-48 overflow-y-auto p-2 rounded-lg bg-rf-bg border border-rf-border">
                              {activeTemplate.parsed.hopping_table.map((freq: any, idx: number) => {
                                const val = typeof freq === 'object' && freq !== null ? freq.frequency_hz : freq;
                                return (
                                  <div key={idx} className="flex justify-between items-center px-2 py-1 rounded bg-rf-panel/60 text-[11px]">
                                    <span className="text-zinc-500">#{idx}</span>
                                    <span className="text-cyan-300 font-bold">{val} Hz</span>
                                  </div>
                                );
                              })}
                            </div>
                          </div>
                        )}
                      </div>
                    </div>
                  )}

                  {/* 业务报文专用结构预览 */}
                  {activeTemplate.category === 'message_payload' && activeTemplate.parsed && (
                    <div className="bg-rf-surface border border-rf-border rounded-xl overflow-hidden shadow-md space-y-4 p-4">
                      <div className="flex items-center space-x-2 text-xs font-bold font-mono uppercase text-rf-text border-b border-rf-border pb-3">
                        <Radio className="w-4 h-4 text-amber-400" />
                        <span>协议字段与动态生成规则</span>
                      </div>

                      <div className="space-y-3">
                        {activeTemplate.parsed.command && (
                          <div className="flex items-center space-x-3 text-xs font-mono">
                            <span className="text-zinc-400">协议功能码 (Command):</span>
                            <span className="px-2 py-0.5 rounded bg-amber-950/60 border border-amber-800 text-amber-300 font-bold">
                              {String(activeTemplate.parsed.command)}
                            </span>
                          </div>
                        )}

                        {activeTemplate.parsed.updates && (
                          <div className="space-y-1.5">
                            <span className="text-xs font-mono text-zinc-400">动态字段更新序列:</span>
                            <div className="grid grid-cols-2 gap-2">
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
          <div className="flex-1 flex flex-col items-center justify-center text-zinc-500 font-mono space-y-4">
            <FileCode className="w-14 h-14 text-zinc-600 stroke-[1.5]" />
            <p className="text-sm">暂无选中的模板，请在左侧列表中选择或直接新建</p>
            <button
              type="button"
              onClick={handleOpenCreateModal}
              className="px-4 py-2 rounded-lg bg-cyan-500 hover:bg-cyan-400 text-black font-mono font-bold text-xs flex items-center space-x-1.5 shadow-md shadow-cyan-500/20"
            >
              <Plus className="w-4 h-4" />
              <span>新建模板预设</span>
            </button>
          </div>
        )}
      </div>

      {/* 新建模板弹窗 (Create Template Modal) */}
      {isCreateOpen && (
        <div className="fixed inset-0 z-50 bg-black/80 backdrop-blur-sm flex items-center justify-center p-4">
          <div className="bg-rf-surface border border-rf-border rounded-2xl shadow-2xl max-w-2xl w-full flex flex-col max-h-[90vh] overflow-hidden">
            {/* 弹窗头部 */}
            <div className="px-5 py-4 border-b border-rf-border flex items-center justify-between bg-rf-panel/60">
              <div className="flex items-center space-x-2.5">
                <FolderPlus className="w-5 h-5 text-cyan-400" />
                <h3 className="text-sm font-bold font-mono text-white">新建预设模板 (Create Template)</h3>
              </div>
              <button
                type="button"
                onClick={() => setIsCreateOpen(false)}
                className="text-zinc-400 hover:text-white p-1 rounded-lg hover:bg-rf-border/60 transition-colors"
              >
                <X className="w-4 h-4" />
              </button>
            </div>

            {/* 弹窗表单主体 */}
            <div className="p-5 overflow-y-auto space-y-4 flex-1">
              {/* 错误提示 */}
              {createError && (
                <div className="p-3 rounded-lg bg-rose-950/70 border border-rose-800 text-rose-300 text-xs font-mono flex items-center space-x-2">
                  <AlertCircle className="w-4 h-4 shrink-0 text-rose-400" />
                  <span>{createError}</span>
                </div>
              )}

              {/* 模板分类 */}
              <div className="space-y-1.5">
                <label className="text-xs font-mono text-zinc-300 flex items-center space-x-1.5">
                  <span>模板分类 (Category):</span>
                </label>
                <div className="grid grid-cols-1 md:grid-cols-3 gap-2">
                  {(
                    [
                      { id: 'physical_config', label: '物理参数', desc: 'templates/physical/*.toml' },
                      { id: 'rf_config', label: '射频跳频', desc: 'templates/rf/*.json' },
                      { id: 'message_payload', label: '业务报文', desc: 'templates/messages/*.json' },
                                          ] as const
                  ).map((cat) => (
                    <button
                      key={cat.id}
                      type="button"
                      onClick={() => handleCategoryChange(cat.id)}
                      className={`p-2.5 rounded-xl border text-left transition-all ${
                        createCategory === cat.id
                          ? 'bg-cyan-950/40 border-cyan-400 text-cyan-300 shadow-xs'
                          : 'bg-rf-bg border-rf-border text-zinc-400 hover:border-zinc-500'
                      }`}
                    >
                      <div className="text-xs font-bold font-mono">{cat.label}</div>
                      <div className="text-[10px] text-zinc-500 font-mono truncate">{cat.desc}</div>
                    </button>
                  ))}
                </div>
              </div>

              {/* 文件名输入与路径预览 */}
              <div className="space-y-1.5">
                <label className="text-xs font-mono text-zinc-300">
                  文件名 (File Name):
                </label>
                <div className="flex items-center space-x-2">
                  <span className="text-xs font-mono text-zinc-500 bg-rf-bg px-2.5 py-2 rounded-lg border border-rf-border shrink-0">
                    templates/{STARTER_TEMPLATES[createCategory]('', '').folder}/
                  </span>
                  <input
                    type="text"
                    placeholder="例如: custom_profile"
                    value={createFilename}
                    onChange={(e) => setCreateFilename(e.target.value)}
                    className="flex-1 bg-rf-bg border border-rf-border rounded-lg px-3 py-2 text-xs font-mono text-cyan-200 placeholder-zinc-500 focus:outline-none focus:border-cyan-400"
                  />
                  <span className="text-xs font-mono text-zinc-400 bg-rf-bg px-2.5 py-2 rounded-lg border border-rf-border shrink-0 font-bold">
                    {STARTER_TEMPLATES[createCategory]('', '').ext}
                  </span>
                </div>
              </div>

              {/* 标题与描述 */}
              <div className="grid grid-cols-2 gap-3">
                <div className="space-y-1">
                  <label className="text-xs font-mono text-zinc-400">模板标题 (可选):</label>
                  <input
                    type="text"
                    placeholder="例如: 自定义测试参数"
                    value={createTitle}
                    onChange={(e) => {
                      setCreateTitle(e.target.value);
                      const starter = STARTER_TEMPLATES[createCategory](e.target.value, createDescription);
                      setCreateContent(starter.content);
                    }}
                    className="w-full bg-rf-bg border border-rf-border rounded-lg px-3 py-1.5 text-xs text-rf-text placeholder-zinc-500 focus:outline-none focus:border-cyan-400"
                  />
                </div>
                <div className="space-y-1">
                  <label className="text-xs font-mono text-zinc-400">功能简述 (可选):</label>
                  <input
                    type="text"
                    placeholder="例如: 用于实验室双向基带测试"
                    value={createDescription}
                    onChange={(e) => {
                      setCreateDescription(e.target.value);
                      const starter = STARTER_TEMPLATES[createCategory](createTitle, e.target.value);
                      setCreateContent(starter.content);
                    }}
                    className="w-full bg-rf-bg border border-rf-border rounded-lg px-3 py-1.5 text-xs text-rf-text placeholder-zinc-500 focus:outline-none focus:border-cyan-400"
                  />
                </div>
              </div>

              {/* 初始源码内容编辑 */}
              <div className="space-y-1.5">
                <div className="flex items-center justify-between text-xs font-mono text-zinc-400">
                  <span>初始文件内容 (Initial Content):</span>
                  <span className="text-[11px] text-zinc-500">可在此预设参数，创建后亦可随时修改</span>
                </div>
                <textarea
                  value={createContent}
                  onChange={(e) => setCreateContent(e.target.value)}
                  rows={8}
                  spellCheck={false}
                  className="w-full bg-rf-bg border border-rf-border rounded-xl p-3 font-mono text-xs text-cyan-200 focus:outline-none focus:border-cyan-400 resize-none leading-relaxed"
                />
              </div>
            </div>

            {/* 弹窗底部操作按钮 */}
            <div className="px-5 py-3 border-t border-rf-border bg-rf-panel/60 flex items-center justify-end space-x-2.5">
              <button
                type="button"
                onClick={() => setIsCreateOpen(false)}
                className="px-4 py-2 rounded-lg bg-rf-bg hover:bg-rf-border text-zinc-400 hover:text-zinc-200 text-xs font-mono transition-colors border border-rf-border"
              >
                取消
              </button>
              <button
                type="button"
                onClick={() => createTemplateMutation.mutate()}
                disabled={createTemplateMutation.isPending || !createFilename.trim()}
                className={`px-4 py-2 rounded-lg font-mono font-bold text-xs flex items-center space-x-1.5 transition-all shadow-md ${
                  createFilename.trim() && !createTemplateMutation.isPending
                    ? 'bg-cyan-500 hover:bg-cyan-400 text-black shadow-cyan-500/20'
                    : 'bg-rf-bg text-zinc-600 border border-rf-border cursor-not-allowed'
                }`}
              >
                {createTemplateMutation.isPending ? (
                  <span>创建中...</span>
                ) : (
                  <>
                    <Plus className="w-3.5 h-3.5" />
                    <span>创建模板</span>
                  </>
                )}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* 删除确认弹窗 (Delete Confirmation Modal) */}
      {templateToDelete && (
        <div className="fixed inset-0 z-50 bg-black/80 backdrop-blur-sm flex items-center justify-center p-4">
          <div className="bg-rf-surface border border-rose-900/60 rounded-2xl shadow-2xl max-w-md w-full p-6 space-y-4">
            <div className="flex items-center space-x-3 text-rose-400">
              <div className="p-2.5 rounded-full bg-rose-950/70 border border-rose-800/80">
                <AlertTriangle className="w-6 h-6 text-rose-500" />
              </div>
              <div>
                <h3 className="text-sm font-bold font-mono text-white">永久删除模板预设</h3>
                <p className="text-xs text-rose-400/90 font-mono">从磁盘移除模板文件</p>
              </div>
            </div>

            {deleteError && (
              <div className="p-2.5 rounded-lg bg-rose-950/80 border border-rose-800 text-rose-200 text-xs font-mono">
                {deleteError}
              </div>
            )}

            <div className="space-y-2 text-xs font-sans text-zinc-300 bg-rf-bg/70 p-3.5 rounded-xl border border-rf-border/80">
              <p>您即将从工作台永久删除以下模板文件：</p>
              <div className="font-mono text-xs text-white font-bold bg-rf-panel p-2 rounded border border-rf-border">
                {templateToDelete.title}
              </div>
              <div className="font-mono text-[11px] text-zinc-400 break-all">
                路径: {templateToDelete.path}
              </div>
              <p className="text-[11px] text-rose-400 font-mono pt-1">
                ⚠️ 注意：此操作不可撤销，已保存的物理参数或报文字段将彻底删除。
              </p>
            </div>

            <div className="flex items-center justify-end space-x-2.5 pt-1">
              <button
                type="button"
                onClick={() => {
                  setTemplateToDelete(null);
                  setDeleteError(null);
                }}
                disabled={deleteTemplateMutation.isPending}
                className="px-4 py-2 rounded-lg bg-rf-bg hover:bg-rf-border text-zinc-300 text-xs font-mono border border-rf-border transition-colors"
              >
                取消
              </button>
              <button
                type="button"
                onClick={() => deleteTemplateMutation.mutate(templateToDelete.name)}
                disabled={deleteTemplateMutation.isPending}
                className="px-4 py-2 rounded-lg bg-rose-600 hover:bg-rose-500 text-white font-mono font-bold text-xs flex items-center space-x-1.5 shadow-md shadow-rose-900/40 transition-colors"
              >
                {deleteTemplateMutation.isPending ? (
                  <span>正在删除...</span>
                ) : (
                  <>
                    <Trash2 className="w-3.5 h-3.5" />
                    <span>确认删除</span>
                  </>
                )}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};
