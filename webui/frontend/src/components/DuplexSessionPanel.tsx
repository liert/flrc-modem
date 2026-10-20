import React, { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../api/client';
import type { RunRecord } from '../types';

const statusLabels: Record<string, string> = {
  reply_matched: '匹配回复', reply_timeout: '已发送 · 回复超时', tx_completed: '发送完成',
  send_failed: '发送失败', rejected: '请求被拒绝', cancelled: '交互已取消', interrupted: '会话中断 · 结果未确认',
};
const syncLabels: Record<string, string> = {
  searching: '搜索同步', acquiring: '捕获中', locked: '已锁定', holdover: '同步保持', lost: '同步失锁',
};

export function DuplexSessionPanel({ run }: { run: RunRecord }) {
  const client = useQueryClient();
  const active = run.status === 'RUNNING';
  const [source, setSource] = useState<'message' | 'payload_hex'>('message');
  const [content, setContent] = useState('');
  const [waitReply, setWaitReply] = useState(!run.command_args.no_wait);
  const [accepted, setAccepted] = useState('');
  const state = useQuery({ queryKey: ['duplexSession', run.id], queryFn: () => api.getSession(run.id),
    refetchInterval: active ? 1000 : false });
  const templates = useQuery({ queryKey: ['templates'], queryFn: () => api.getTemplates(), staleTime: 60000 });
  const ready = active && !!state.data?.ready && !state.data?.status?.error;
  const send = useMutation({ mutationFn: async () => {
    const requestId = crypto.randomUUID();
    const payload = source === 'message' ? JSON.parse(content) : content;
    return api.sendMessage(run.id, { request_id: requestId, [source]: payload, wait_reply: waitReply });
  }, onSuccess: result => {
    setAccepted(result.request_id);
    client.invalidateQueries({ queryKey: ['duplexSession', run.id] });
  } });
  const info = state.data?.status;
  const results = state.data?.results || [];
  const resolved = results.some(result => result.request_id === accepted);
  const templateOptions = templates.data?.filter(template => template.format === 'json' && template.applicable_commands.includes('duplex')) || [];
  return <section className="flex-none max-h-[50vh] overflow-y-auto p-4 bg-rf-surface border-b border-rf-border space-y-3 text-xs">
    <div className="flex flex-wrap items-center justify-between gap-2">
      <h3 className="font-semibold text-cyan-300">常驻同步收发会话</h3>
      <span className={active && ['locked', 'holdover'].includes(info?.state || '') ? 'text-emerald-400' : 'text-amber-400'}>
        {active ? syncLabels[info?.state || ''] || '启动中' : '会话已结束'}
        {active && info?.slot !== undefined && ` · Slot ${info.slot}`}
        {active && info?.frequency_hz && ` · ${(info.frequency_hz / 1e6).toFixed(3)} MHz`}
      </span>
    </div>
    <p className="text-zinc-400 break-all font-mono">
      {state.data?.ready?.device_args || run.command_args.args || 'addr=192.168.10.2'} · 通道 {state.data?.ready?.channel ?? run.command_args.channel ?? 0}
      {' · 实际 RX 采样率 '}{(info?.sample_rate || state.data?.ready?.sample_rate)?.toLocaleString() || '待打开设备'} S/s
      {' · CRC 有效帧 '}{info?.decoded_frames ?? 0}
    </p>
    {(info?.error || info?.loss_reason || state.error) && <p className="text-amber-400 break-all">{info?.error || info?.loss_reason || state.error?.message}</p>}
    <div className="grid sm:grid-cols-2 gap-2">
      <select aria-label="会话报文输入格式" value={source} onChange={event => { setSource(event.target.value as typeof source); setContent(''); send.reset(); }}
        className="bg-rf-bg border border-rf-border rounded p-2 text-zinc-200">
        <option value="message">协议 JSON 报文</option><option value="payload_hex">原始物理载荷 Hex</option>
      </select>
      <select aria-label="载入会话报文模板" value="" onChange={event => {
        const template = templateOptions.find(item => item.name === event.target.value);
        if (template) { setSource('message'); setContent(template.raw_content); send.reset(); }
      }} className="bg-rf-bg border border-rf-border rounded p-2 text-zinc-200">
        <option value="">从报文模板载入…</option>
        {templateOptions.map(item => <option key={item.name} value={item.name}>{item.title}</option>)}
      </select>
    </div>
    <textarea aria-label="待投递报文" value={content} onChange={event => setContent(event.target.value)} rows={4}
      placeholder={source === 'message' ? '选择模板或输入协议 JSON 对象' : '输入 6..127 字节十六进制载荷'}
      className="w-full bg-rf-bg border border-rf-border rounded p-3 font-mono text-cyan-200 resize-y select-text" />
    <div className="flex flex-wrap items-center justify-between gap-3">
      <label className="flex items-center gap-2 text-zinc-300"><input type="checkbox" checked={waitReply} onChange={event => setWaitReply(event.target.checked)} />等待匹配回复</label>
      <button disabled={!ready || !content.trim() || send.isPending} onClick={() => send.mutate()}
        className="bg-cyan-500 hover:bg-cyan-400 disabled:opacity-40 text-black font-semibold px-4 py-2 rounded-lg">
        {send.isPending ? '正在提交…' : '投递一条报文'}
      </button>
    </div>
    <p className="text-zinc-400">失锁后引擎先等待重新锁定；超时则拒绝本次发送。结束会话请使用上方“终止任务”。</p>
    {send.error && <p role="alert" className="text-red-400">{send.error.message}</p>}
    {accepted && !resolved && active && <p className="text-cyan-300 break-all font-mono">已接收投递请求，等待执行结果：{accepted}</p>}
    {!!results.length && <div className="space-y-2 border-t border-rf-border pt-3">
      <p className="text-zinc-400">最近 50 条交互结果 · 任务结束后仍保留</p>
      {results.map((result, index) => <details key={`${result.request_id}-${index}`} className="bg-rf-bg rounded p-2 border border-rf-border">
        <summary className="cursor-pointer flex flex-wrap gap-x-3 gap-y-1 text-zinc-300">
          <span className={['reply_matched', 'tx_completed'].includes(result.status) ? 'text-emerald-400' : 'text-amber-400'}>{statusLabels[result.status] || result.status}</span>
          <span>{result.timestamp && new Date(result.timestamp).toLocaleTimeString()}</span>
          <span className="font-mono break-all">{result.request_id}</span>
        </summary>
        <div className="mt-2 space-y-1 font-mono text-zinc-400 select-text break-all">
          <p>TX 完成：{result.tx_completed === undefined ? '未确认' : result.tx_completed ? '是' : '否'} · 回复匹配：{result.reply_matched === undefined ? '未确认' : result.reply_matched ? '是' : '否'}</p>
          {result.tx_completed && <p>t={result.tx_hw_time?.toFixed(6)} s · Slot {result.tx_slot} · {result.tx_frequency_hz} Hz · RTT={result.rtt_ms?.toFixed(3) ?? '—'} ms</p>}
          {result.tx_payload_hex && <p>TX：{result.tx_payload_hex}</p>}
          {result.rx_reply && <p>RX：{result.rx_reply.payload_hex}</p>}
          {result.error && <p className="text-amber-400">{result.error}</p>}
        </div>
      </details>)}
    </div>}
  </section>;
}
