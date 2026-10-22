import React from 'react';
import { useQuery } from '@tanstack/react-query';
import { api } from '../api/client';

export function MessageIOConfiguration({ commandId, parameters }: {
  commandId: string; parameters: Record<string, any>;
}) {
  const check = useQuery({ queryKey: ['messageIOProfile', commandId, parameters],
    queryFn: () => api.preflight(commandId, parameters), enabled: false, retry: false, gcTime: 60000 });
  const profile = check.data;
  return <section className="bg-rf-surface border border-rf-border rounded-xl p-4 space-y-3 text-xs">
    <p className="text-zinc-400 leading-relaxed">CLI 覆盖值 → TOML [transmit] → [demod] → 默认值。发送需唯一 CRC 初值和实际 RF 频率；同步时隙收发当前使用 DK32S 时序模型。</p>
    <button type="button" onClick={() => check.refetch()} disabled={check.isFetching}
      className="px-3 py-2 border border-cyan-700 rounded-lg text-cyan-300 hover:bg-cyan-500/10 disabled:opacity-50">
      {check.isFetching ? '正在解析…' : '检查并预览生效参数'}
    </button>
    {check.error && <p role="alert" className="text-red-400 break-all">{check.error.message}</p>}
    {profile && <div className="space-y-2 bg-rf-bg border border-rf-border rounded-lg p-3">
      <p className="text-zinc-400">上次检查：{new Date(check.dataUpdatedAt).toLocaleTimeString()} · 启动任务时会重新校验</p>
      <dl className="grid sm:grid-cols-2 gap-2 font-mono text-zinc-200">
        <div>RF：{(profile.frequency / 1e6).toFixed(6)} MHz</div>
        <div>采样率：{profile.sample_rate.toLocaleString()} S/s</div>
        <div>码率：{profile.bit_rate.toLocaleString()} · CR {profile.coding_rate} · BT {profile.bt}</div>
        <div>头：{profile.packet.header_type} · CRC{profile.packet.crc_bytes * 8} / 0x{profile.packet.crc_initial.toString(16).toUpperCase()}</div>
        <div>同步字：{profile.sync || '从接收恢复'}</div>
        {profile.tx_sync_word !== undefined && <div className="sm:col-span-2 break-all">
          同步字极性：发送 {profile.tx_invert_sync ? '反转' : '不反转'}（{profile.tx_sync_word || '自动恢复'}） · 回复 {profile.reply_invert_sync ? '反转' : '不反转'}（{profile.reply_sync_word || '与基准字相同'}）。接收解码同时接受原字与反字，回复极性可按每条报文覆盖
        </div>}
        {profile.rf_config && <div className="sm:col-span-2 break-all">跳频配置：{profile.rf_config.source === 'builtin_default' ? '内置历史表（未确认当前设备）' : profile.rf_config.source} · {profile.rf_config.hopping_table.length} 个时隙</div>}
        <div>载荷：{profile.payload_bytes ?? '按每次投递校验'} B · 固定包长：{profile.packet.payload_lengths.join(', ') || '未限制'}</div>
        <div className="sm:col-span-2 break-all">设备：{profile.device.selection}{profile.device.channel !== undefined && ` · 通道 ${profile.device.channel} · ${profile.device.antenna}`}</div>
      </dl>
      {profile.rf_config?.warnings.map(warning => <p key={warning} className="text-amber-400 leading-relaxed">{warning}</p>)}
      <p className="text-amber-400">{profile.rate_status}</p>
    </div>}
  </section>;
}
