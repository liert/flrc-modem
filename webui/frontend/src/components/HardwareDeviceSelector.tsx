import React from 'react';
import { useQuery } from '@tanstack/react-query';
import { Cpu } from 'lucide-react';
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
  const indexSelection = parameters.device_index !== undefined && parameters.device_index !== null && parameters.device_index !== ''
    ? `device_index:${parameters.device_index}` : '';
  const selection = kind === 'hackrf' ? parameters.serial || indexSelection : parameters.args || '';
  return <div className="min-w-0 space-y-2.5 text-xs">
    <div className="flex min-h-5 items-center justify-between gap-2">
      <label htmlFor="command-hardware-device" className="flex items-center gap-1.5 font-semibold text-zinc-300 leading-5">
        <Cpu className="w-3.5 h-3.5 shrink-0 text-cyan-400" />
        <span>物理设备选择</span>
      </label>
      <span className="shrink-0 text-zinc-400">{kind.toUpperCase()} · {info?.status || '探测中'}</span>
    </div>
    <select id="command-hardware-device" aria-label="物理设备选择" value={selection} onChange={event => onChange(kind === 'hackrf'
      ? event.target.value.startsWith('device_index:')
        ? { serial: '', device_index: Number(event.target.value.slice('device_index:'.length)) }
        : { serial: event.target.value, device_index: '' }
      : { args: event.target.value, serial: '', device_index: '' })}
      className="h-9 w-full min-w-0 bg-rf-bg border border-rf-border rounded-lg px-3 text-xs font-mono text-rf-text focus:outline-none focus:border-cyan-400 focus:ring-1 focus:ring-cyan-400/40 transition-colors">
      <option value="">{kind === 'hackrf' ? '自动选择首个可打开设备' : commandId === 'usrp' ? '默认设备过滤条件' : '默认 addr=192.168.10.2'}</option>
      {selection && !options.some(option => option.value === selection) && <option value={selection}>
        {kind === 'hackrf' && !parameters.serial && indexSelection ? `已保存的设备索引 ${parameters.device_index}` : `${selection}（手动定位）`}
      </option>}
      {options.map(option => <option key={option.value} value={option.value}>{option.label}</option>)}
    </select>
    <p className="text-zinc-400 leading-relaxed">{kind === 'hackrf'
      ? '统一在此选择设备；留空自动选择首个可打开设备。'
      : '从已探测设备选择，也可在下方 args 参数中填写 USRP 定位条件。'}</p>
    {info?.details.active_locks?.map((lock: { run_id: string; selector?: string }) =>
      <p key={lock.run_id} className="text-amber-400 break-all font-mono">占用：{lock.selector || '自动 / 类型独占'} · {lock.run_id}</p>)}
    {info?.error_message && <p className="text-amber-400 break-all">{info.error_message}</p>}
  </div>;
}
