import React, { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { ExternalLink, RefreshCw } from 'lucide-react';
import { api } from '../api/client';
import { useRadioWorkbenchStore } from '../stores/useRadioWorkbenchStore';
import { useWorkstationStore } from '../stores/useWorkstationStore';
import { supportsSyncTemplate, syncTemplatePatch, templateReference, type SyncTemplateKind } from '../utils/syncTemplates';

export function SyncTemplateSelector({ kind }: { kind: SyncTemplateKind }) {
  const path = useRadioWorkbenchStore(state => kind === 'rf' ? state.sync.rfConfig : state.sync.physicalTemplate);
  const templates = useQuery({ queryKey: ['templates'], queryFn: () => api.getTemplates(), staleTime: 60000 });
  const [error, setError] = useState('');
  const options = (templates.data ?? []).filter(template => supportsSyncTemplate(template, kind));
  const active = options.find(template => templateReference(template.path) === templateReference(path)
    || templateReference(template.name) === templateReference(path));
  const label = kind === 'rf' ? '射频跳频模板' : '物理参数预设';
  return <div className="min-w-0 space-y-2 text-xs">
    <div className="flex items-center justify-between gap-2">
      <label htmlFor={`sync-template-${kind}`} className="font-medium text-zinc-300">{label}</label>
      <button type="button" className="flex items-center gap-1 text-cyan-400" onClick={() => {
        const store = useWorkstationStore.getState();
        store.setSelectedTemplate(active ?? null);
        store.setActiveTab('templates');
      }}><ExternalLink className="h-3 w-3" />模板库</button>
    </div>
    <div className="flex gap-2">
      <select id={`sync-template-${kind}`} value={active?.path ?? path} disabled={templates.isPending}
        className="h-9 min-w-0 flex-1 rounded-lg border border-rf-border bg-rf-bg px-2.5 text-zinc-200 focus:border-indigo-400 focus:outline-none disabled:opacity-50"
        onChange={event => {
          try {
            const selected = options.find(template => template.path === event.target.value) ?? null;
            const patch = syncTemplatePatch(selected, kind);
            useRadioWorkbenchStore.setState(state => ({ frame: { ...state.frame, ...patch.frame }, sync: { ...state.sync, ...patch.sync } }));
            setError('');
          } catch (failure) { setError((failure as Error).message); }
        }}>
        <option value="">{templates.isPending ? '正在加载模板…' : kind === 'rf' ? '内置跳频表（未选择设备模板）' : '手动参数（不载入预设）'}</option>
        {path && !active && <option value={path} disabled>已保存的配置不在当前模板列表，请重新选择</option>}
        {options.map(template => <option key={template.path} value={template.path}>{template.title} · {template.name}</option>)}
      </select>
      <button type="button" onClick={() => void templates.refetch()} aria-label={`刷新${label}`} disabled={templates.isFetching}
        className="rounded-lg border border-rf-border px-2 text-zinc-400 hover:text-cyan-300 disabled:opacity-50"><RefreshCw className={`h-3.5 w-3.5 ${templates.isFetching ? 'animate-spin' : ''}`} /></button>
    </div>
    {active && <p className="leading-5 text-zinc-400">{active.description}</p>}
    <p className="leading-5 text-zinc-500">{kind === 'rf'
      ? '选择后填入模板的同步字和首个监听频点，可继续手动修改。'
      : '选择后载入码率、CR、BT、同步字及帧头；采样率和监听频率沿用采集设置。清除预设会保留当前参数。'}</p>
    {!templates.isPending && !templates.isError && options.length === 0 && <p className="text-zinc-400">暂无兼容模板，可在模板库中新建。</p>}
    {(error || templates.isError) && <p role="alert" className="text-amber-300">{error || `模板加载失败：${templates.error?.message}`}</p>}
  </div>;
}
