import React, { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { ChevronDown, ArrowUp, ArrowDown, Copy, Trash2, Play, Square, Plus, Download, Upload, Sparkles } from 'lucide-react';
import { api } from '../api/client';
import type { DuplexPlanDefinition, DuplexSessionState, TemplateItem } from '../types';
import { duplexItemSummary, duplexPayloadText, parseDuplexPlan, replaceDuplexPayload } from '../utils/duplexDraft';
import { DuplexItemPayloadEditor, duplexButtonClass, duplexInputClass } from './DuplexPayloadEditor';

type Lane = 'periodic' | 'steps';
type PeriodicItem = DuplexPlanDefinition['periodic'][number];
type StepItem = DuplexPlanDefinition['steps'][number];
const planLabels: Record<string, string> = {
  accepted: '等待启动确认', running: '运行中', stopping: '停止中', stopped: '已停止',
  completed: '已完成', failed: '步骤失败 · 已停止', interrupted: '会话中断', limit_reached: '已达执行上限',
};

export function DuplexPlanPanel({ runId, ready, session, draft, setDraft, selectedItem, onSelectItem, onCompose }: {
  runId: string; ready: boolean; session?: DuplexSessionState; draft: DuplexPlanDefinition;
  setDraft: React.Dispatch<React.SetStateAction<DuplexPlanDefinition>>;
  selectedItem: string; onSelectItem: React.Dispatch<React.SetStateAction<string>>; onCompose: () => void;
}) {
  const client = useQueryClient();
  const [acceptedId, setAcceptedId] = useState('');
  const [dirtyItems, setDirtyItems] = useState<Set<string>>(() => new Set());
  const current = session?.plan;
  const running = !!current && ['accepted', 'running', 'stopping'].includes(current.status);
  const control = useMutation({ mutationFn: (request: Parameters<typeof api.controlPlan>[1]) => api.controlPlan(runId, request),
    onSuccess: result => { setAcceptedId(result.plan_id); void client.invalidateQueries({ queryKey: ['duplexSession', runId] }); },
  });
  const rejection = session?.plan_rejection?.plan_id === acceptedId ? session.plan_rejection : null;
  const awaiting = !!acceptedId && current?.plan_id !== acceptedId && !rejection;
  const locked = running || awaiting || control.isPending;
  const visibleDraft = running && session?.plan_definition?.plan_id === current?.plan_id ? session.plan_definition : draft;
  const setDirty = (id: string, dirty: boolean) => setDirtyItems(previous => {
    const next = new Set(previous); if (dirty) next.add(id); else next.delete(id); return next;
  });
  const changeItem = (lane: Lane, id: string, values: Partial<PeriodicItem & StepItem>) => {
    setDraft(previous => ({ ...previous, [lane]: previous[lane].map(item => item.id === id ? { ...item, ...values } : item) }));
  };
  const remove = (lane: Lane, id: string) => {
    setDraft(previous => ({ ...previous, [lane]: previous[lane].filter(item => item.id !== id) })); setDirty(id, false);
  };
  const move = (index: number, offset: number) => setDraft(previous => {
    const steps = [...previous.steps]; [steps[index], steps[index + offset]] = [steps[index + offset], steps[index]];
    return { ...previous, steps };
  });

  const [showRecipes, setShowRecipes] = useState(false);

  const templates = useQuery({ queryKey: ['templates'], queryFn: () => api.getTemplates(), staleTime: 60000 });
  const recipes = templates.data?.filter(template => template.category === 'duplex_plan' && template.applicable_commands.includes('duplex')) || [];
  const [planError, setPlanError] = useState('');

  const applyRecipe = (template: TemplateItem) => {
    try {
      setDraft(parseDuplexPlan(template.parsed));
      setDirtyItems(new Set());
      setPlanError('');
      setShowRecipes(false);
      onSelectItem('');
    } catch (error) {
      setPlanError(error instanceof Error ? error.message : '加载编排模板失败');
    }
  };

  const handleExportPlan = () => {
    const blob = new Blob([JSON.stringify(draft, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `duplex_plan_${Date.now()}.json`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const handleImportPlan = (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = (e) => {
      try {
        setDraft(parseDuplexPlan(JSON.parse(e.target?.result as string)));
        setDirtyItems(new Set());
        setPlanError('');
        onSelectItem('');
      } catch (error) {
        setPlanError(error instanceof Error ? error.message : '导入编排方案失败');
      }
    };
    reader.readAsText(file);
    event.target.value = '';
  };
  return <div className="space-y-4">
    <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-rf-border bg-rf-panel/20 p-3">
      <div className="space-y-1">
        <h4 className="font-semibold text-zinc-200">发送编排 <span className="ml-2 font-normal text-cyan-300">{current ? planLabels[current.status] || current.status : '待配置'}</span></h4>
        <p className="text-zinc-500">{visibleDraft.periodic.length} 个周期任务 · {visibleDraft.steps.length} 个流程步骤{running ? ' · 正在查看本次执行配置' : ''}</p>
      </div>
      <div className="flex flex-wrap gap-2">
        <button disabled={!ready || locked || dirtyItems.size > 0 || !(draft.periodic.length || draft.steps.length)}
          className={`${duplexButtonClass} border-cyan-600 bg-cyan-500/15 text-cyan-200 hover:bg-cyan-500/25`} onClick={() => {
            control.mutate({ action: 'start_plan', plan: { ...draft, plan_id: crypto.randomUUID() } });
          }}><Play aria-hidden="true" className="h-3.5 w-3.5" />{awaiting ? '等待启动…' : '开始编排'}</button>
        <button disabled={!ready || !running || current?.status === 'stopping' || control.isPending} className={duplexButtonClass}
          onClick={() => { if (current) control.mutate({ action: 'stop_plan', plan_id: current.plan_id }); }}>
          <Square aria-hidden="true" className="h-3.5 w-3.5" />停止编排</button>
      </div>
    </div>
    {current && <dl className="grid grid-cols-2 gap-2 sm:grid-cols-4" aria-label="编排运行统计">
      {[['已执行', current.executed], ['成功', current.succeeded], ['失败 / 取消', current.failed], ['跳过周期', current.skipped]].map(([label, value]) =>
        <div key={label} className="rounded-md border border-rf-border/70 bg-rf-bg/50 px-3 py-2"><dt className="text-zinc-500">{label}</dt><dd className="mt-1 font-mono text-base text-zinc-200">{value}</dd></div>)}
    </dl>}
    {(control.error || rejection || current?.error) && <p role="alert" className="rounded-md border border-amber-900/50 bg-amber-500/5 p-3 text-amber-400 break-all">{control.error?.message || rejection?.error || current?.error}</p>}
    {planError && <p role="alert" className="text-amber-400">{planError}</p>}
    {dirtyItems.size > 0 && <p role="status" className="text-amber-400">有 {dirtyItems.size} 条报文尚未保存，请保存或取消修改后再启动编排。</p>}
    <div className="flex flex-wrap items-center justify-between gap-2">
      <button className={duplexButtonClass} disabled={locked} onClick={onCompose}><Plus aria-hidden="true" className="h-3.5 w-3.5" />添加报文</button>
      <div className="flex flex-wrap gap-2 items-center">
        {/* 预设方案下拉菜单 */}
        <div className="relative">
          <button
            type="button"
            disabled={locked}
            onClick={() => {
              if (!showRecipes) void templates.refetch();
              setShowRecipes(!showRecipes);
            }}
            aria-expanded={showRecipes}
            className={`${duplexButtonClass} text-cyan-300 border-cyan-700/60`}
            title="选择编排模板"
          >
            <Sparkles className="w-3.5 h-3.5 text-cyan-400" />
            <span>预设方案</span>
            <ChevronDown className="w-3 h-3 text-zinc-400" />
          </button>
          {showRecipes && (
            <div
              className="absolute left-0 top-full mt-1 w-56 bg-rf-surface border border-rf-border rounded-lg shadow-xl z-50 py-1 font-mono text-[11px]"
              onMouseLeave={() => setShowRecipes(false)}
            >
              {templates.isPending ? <p role="status" className="px-3 py-2 text-zinc-400">正在加载编排模板…</p>
                : templates.isError ? <button type="button" className="px-3 py-2 text-amber-400" onClick={() => void templates.refetch()}>加载失败，点击重试</button>
                : recipes.length === 0 ? <p className="px-3 py-2 text-zinc-400">暂无编排模板，可在模板库中添加。</p>
                : recipes.map(template => (
                  <button key={template.name} type="button" onClick={() => applyRecipe(template)}
                    title={template.description}
                    className="w-full text-left px-3 py-1.5 hover:bg-rf-panel text-zinc-200">
                    <span className="block">{template.title}</span>
                    <span className="block text-zinc-500 text-[10px]">{template.description}</span>
                  </button>
                ))}
            </div>
          )}
        </div>

        {/* 导出与导入方案 */}
        {(visibleDraft.periodic.length > 0 || visibleDraft.steps.length > 0) && (
          <button
            type="button"
            onClick={handleExportPlan}
            className={duplexButtonClass}
            title="导出当前编排配置为 JSON 文件"
          >
            <Download className="w-3.5 h-3.5 text-cyan-400" />
            <span>导出方案</span>
          </button>
        )}
        <label className={`${duplexButtonClass} cursor-pointer`} title="从 JSON 文件导入编排配置">
          <Upload className="w-3.5 h-3.5 text-cyan-400" />
          <span>导入方案</span>
          <input type="file" accept=".json" onChange={handleImportPlan} className="hidden" />
        </label>

        {(visibleDraft.periodic.length > 0 || visibleDraft.steps.length > 0) && <button className={duplexButtonClass} onClick={() => onSelectItem('')}>收起所有任务</button>}
        {session?.plan_definition && <button className={duplexButtonClass} disabled={locked || dirtyItems.size > 0} onClick={() => {
          setDraft({ ...session.plan_definition!, plan_id: 'draft' }); onSelectItem('');
        }}>载入上次编排</button>}
      </div>
    </div>
    {!visibleDraft.periodic.length && !visibleDraft.steps.length && <div className="rounded-lg border border-dashed border-rf-border px-4 py-8 text-center text-zinc-400">
      <p className="font-medium text-zinc-300">先添加一条报文</p><p className="mt-2 leading-6">在“报文编辑”中选择模板，加入周期任务或流程步骤。每条任务可独立展开编辑。</p>
    </div>}
    {(['periodic', 'steps'] as const).map(lane => visibleDraft[lane].length > 0 && <section key={lane} className="space-y-2" aria-label={lane === 'periodic' ? '周期任务列表' : '交互流程列表'}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h5 className="font-medium text-zinc-300">{lane === 'periodic' ? '周期任务' : '交互流程'} <span className="text-zinc-500">{visibleDraft[lane].length}</span></h5>
        {lane === 'steps' && <label className="flex items-center gap-2 text-zinc-400">循环次数<input aria-label="流程循环次数" type="number" min={1} max={10000} disabled={locked} value={visibleDraft.cycles}
          onChange={event => setDraft(previous => ({ ...previous, cycles: Number(event.target.value) }))} className={`${duplexInputClass} max-w-24`} /></label>}
      </div>
      {visibleDraft[lane].map((item, index) => {
        const periodic = lane === 'periodic' ? item as PeriodicItem : null;
        const step = lane === 'steps' ? item as StepItem : null;
        const label = `${lane === 'periodic' ? '周期任务' : '步骤'} ${index + 1}`;
        return <details key={item.id} open={selectedItem === item.id} onToggle={event => {
          if (event.currentTarget.open) onSelectItem(item.id);
          else onSelectItem(previous => previous === item.id ? '' : previous);
        }} className="group/item overflow-hidden rounded-lg border border-rf-border bg-rf-bg/50 open:border-cyan-800/70">
          <summary aria-label={`展开编辑${label}`} className="flex cursor-pointer list-none items-center gap-3 px-3 py-3 [&::-webkit-details-marker]:hidden focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-inset focus-visible:ring-cyan-400">
            <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md border border-rf-border bg-rf-panel font-mono text-zinc-400">{index + 1}</span>
            <span className="min-w-0 flex-1"><span className="block truncate font-medium text-zinc-200" title={duplexItemSummary(item)}>{duplexItemSummary(item)}</span>
              <span className="mt-1 block text-zinc-500">{periodic ? `${periodic.interval_ms} ms · ${periodic.count ? `${periodic.count} 次` : '持续发送'}` : `等待 ${step!.delay_ms} ms · ${step!.on_failure === 'stop' ? '失败停止' : '失败继续'}`}
                {' · '}{item.wait_reply ? '等待回复' : '发送后继续'}
                {(item.invert_sync ?? !!session?.config?.tx_invert_sync) && <span className="ml-2 text-amber-400">发送同步字已反转</span>}
                {item.wait_reply && (item.reply_invert_sync ?? !!session?.config?.reply_invert_sync) && <span className="ml-2 text-amber-400">回复同步字已反转</span>}</span></span>
            {dirtyItems.has(item.id) && <span className="shrink-0 text-amber-400">未保存</span>}
            <ChevronDown aria-hidden="true" className="h-4 w-4 shrink-0 text-zinc-500 transition-transform group-open/item:rotate-180" />
          </summary>
          <div className="space-y-3 border-t border-rf-border px-3 py-3">
            <fieldset disabled={locked} className="grid min-w-0 grid-cols-1 items-end gap-3 sm:grid-cols-3">
              <label className="space-y-1.5 text-zinc-400"><span className="block">{periodic ? '目标周期 (ms)' : '步骤前等待 (ms)'}</span>
                <input aria-label={`${label} ${periodic ? '目标周期' : '执行前等待'}`} type="number" min={periodic ? 1 : 0} max={86400000} value={periodic ? periodic.interval_ms : step!.delay_ms}
                  onChange={event => changeItem(lane, item.id, periodic ? { interval_ms: Number(event.target.value) } : { delay_ms: Number(event.target.value) })} className={duplexInputClass} /></label>
              {periodic ? <label className="space-y-1.5 text-zinc-400"><span className="block">发送次数 · 0 为持续</span><input aria-label={`${label} 发送次数`} type="number" min={0} max={10000} value={periodic.count}
                onChange={event => changeItem(lane, item.id, { count: Number(event.target.value) })} className={duplexInputClass} /></label>
                : <label className="space-y-1.5 text-zinc-400"><span className="block">失败时</span><select aria-label={`${label} 失败策略`} value={step!.on_failure}
                  onChange={event => changeItem(lane, item.id, { on_failure: event.target.value as 'stop' | 'continue' })} className={duplexInputClass}>
                  <option value="stop">停止整个编排</option><option value="continue">继续下一步</option></select></label>}
              <div className="space-y-2">
                <label className="flex min-h-9 items-center gap-2 text-zinc-300"><input type="checkbox" checked={item.wait_reply}
                  onChange={event => changeItem(lane, item.id, { wait_reply: event.target.checked })} />等待匹配回复</label>
                <label className="flex min-h-9 items-center gap-2 text-zinc-300" title="本条发送的同步字逐位取反，CRC 随之重新计算">
                  <input type="checkbox" checked={item.invert_sync ?? !!session?.config?.tx_invert_sync}
                    onChange={event => changeItem(lane, item.id, { invert_sync: event.target.checked })} />反转发送同步字极性</label>
                <label className={`flex min-h-9 items-center gap-2 ${item.wait_reply ? 'text-zinc-300' : 'text-zinc-500'}`} title="本条等待的回复使用逐位取反的同步字；地面帧仍用基准字锁定，解码同时接受原字与反字">
                  <input type="checkbox" checked={item.reply_invert_sync ?? !!session?.config?.reply_invert_sync}
                    disabled={!item.wait_reply}
                    onChange={event => changeItem(lane, item.id, { reply_invert_sync: event.target.checked })} />反转接收回复同步字极性</label>
              </div>
            </fieldset>
            <DuplexItemPayloadEditor key={`${item.id}:${duplexPayloadText(item)}`} item={item} label={label} disabled={locked}
              onDirtyChange={dirty => setDirty(item.id, dirty)} onSave={payload => setDraft(previous => ({
                ...previous, [lane]: previous[lane].map(entry => entry.id === item.id ? replaceDuplexPayload(entry, payload) : entry),
              }))} />
            <div className="flex flex-wrap items-center justify-between gap-2 border-t border-rf-border/60 pt-3">
              <span className="font-mono text-zinc-500">{item.id}</span>
              <div className="flex flex-wrap gap-2">
                {lane === 'steps' && <><button aria-label={`上移步骤 ${index + 1}`} className={duplexButtonClass} disabled={locked || index === 0} onClick={() => move(index, -1)}><ArrowUp aria-hidden="true" className="h-3.5 w-3.5" />上移</button>
                  <button aria-label={`下移步骤 ${index + 1}`} className={duplexButtonClass} disabled={locked || index === visibleDraft.steps.length - 1} onClick={() => move(index, 1)}><ArrowDown aria-hidden="true" className="h-3.5 w-3.5" />下移</button></>}
                <button aria-label={`复制${label}`} className={duplexButtonClass} disabled={locked || dirtyItems.has(item.id) || visibleDraft[lane].length >= (periodic ? 8 : 64)} onClick={() => {
                  const id = crypto.randomUUID().slice(0, 8);
                  setDraft(previous => ({ ...previous, [lane]: [...previous[lane].slice(0, index + 1), { ...item, id }, ...previous[lane].slice(index + 1)] })); onSelectItem(id);
                }}><Copy aria-hidden="true" className="h-3.5 w-3.5" />复制</button>
                <button aria-label={`移除${label}`} className={`${duplexButtonClass} text-red-400`} disabled={locked} onClick={() => remove(lane, item.id)}><Trash2 aria-hidden="true" className="h-3.5 w-3.5" />移除</button>
              </div>
            </div>
          </div>
        </details>;
      })}
    </section>)}
    <details className="rounded-lg border border-rf-border/70 px-3 py-2.5 text-zinc-500">
      <summary className="cursor-pointer text-zinc-400">调度与停止说明</summary>
      <div className="mt-2 space-y-2 leading-6"><p>目标周期用于安排执行顺序，实际发送由可用同步时隙决定。等待回复会占用执行通道，错过的周期自动跳过；每个编排最多执行 10000 次。</p>
        <p>停止编排后，同步会话继续接收。已提交硬件的报文可能仍会发出。启动后由会话自动执行，离开此页面仍会继续。</p></div>
    </details>
  </div>;
}
