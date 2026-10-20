import React from 'react';
import { useQuery } from '@tanstack/react-query';
import { api } from '../api/client';

export function HardwareDeviceSelector({ commandId, parameters, onChange }: {
  commandId: string; parameters: Record<string, any>; onChange: (values: Record<string, any>) => void;
}) {
  const kind = ['transmit', 'live'].includes(commandId) ? 'hackrf' : ['duplex', 'usrp'].includes(commandId) ? 'usrp' : parameters.sdr || 'usrp';
  const devices = useQuery({ queryKey: ['devices'], queryFn: api.getDevices, staleTime: 5000 });
  const info = devices.data?.find(item => item.device_type === kind);
  const options: { value: string; label: string }[] = kind === 'hackrf'
    ? (info?.details.serials || []).filter(Boolean).map((serial: string) => ({ value: serial, label: serial }))
    : (info?.details.devices || []).filter((item: Record<string, string>) => item.addr || item.serial).map((item: Record<string, string>) => ({
        value: item.addr ? `addr=${item.addr}` : `serial=${item.serial}`,
        label: `${item.product || item.type || 'USRP'} · ${item.addr || item.serial} ${item.serial ? `(${item.serial})` : ''}`,
      }));
  const selection = kind === 'hackrf' ? parameters.serial || '' : parameters.args || '';
  return <div className="space-y-3">
    <div className="flex flex-wrap items-center justify-between gap-2">
      <h3 className="font-semibold text-cyan-300">物理设备选择</h3>
      <span className="text-zinc-400">{kind.toUpperCase()} · {info?.status || '探测中'}</span>
    </div>
    <label className="block space-y-1.5">
      <span className="text-zinc-300">从已探测设备选择（也可在下方参数表填写定位参数）</span>
      <select aria-label="物理设备选择" value={selection} onChange={event => onChange(kind === 'hackrf'
        ? { serial: event.target.value, device_index: '' } : { args: event.target.value })}
        className="w-full bg-rf-bg border border-rf-border rounded-lg p-2 font-mono text-rf-text">
        <option value="">{kind === 'hackrf' ? '自动选择首个可打开设备' : commandId === 'usrp' ? '默认设备过滤条件' : '默认 addr=192.168.10.2'}</option>
        {selection && !options.some(option => option.value === selection) && <option value={selection}>{selection}（手动定位）</option>}
        {options.map(option => <option key={option.value} value={option.value}>{option.label}</option>)}
      </select>
    </label>
    {info?.details.active_locks?.map((lock: { run_id: string; selector?: string }) =>
      <p key={lock.run_id} className="text-amber-400 break-all font-mono">占用：{lock.selector || '自动 / 类型独占'} · {lock.run_id}</p>)}
    {info?.error_message && <p className="text-amber-400 break-all">{info.error_message}</p>}
    <p className="text-zinc-500">已知设备按物理身份独占；自动或未知定位按设备类型互斥。占用期间保留上次探测结果。</p>
  </div>;
}
