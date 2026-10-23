import React, { useState } from 'react';
import { Download } from 'lucide-react';
import type { ProtocolInspection } from '../types';
import { CopyButton } from './CopyButton';

const fieldLabels: Record<string, string> = {
  kind: '报文类型', raw_hex: '未解释的原始字节', rc_mode: '遥控布局',
  channels_raw: '通道原始值（11 bit）', channels_output: '固件输出值（原值 +500，单位未确认）',
  target_device_mask: '目标设备位图', device_number: '设备编号', data_batch_id: '数据批次号',
  stream_data_hex: '透传数据 Hex', voltage_raw: '电压原始值（单位未确认）',
  timestamp_ticks: '时间戳计数', correction_ticks: '时间修正计数（100 μs/计数）',
  correction_sec: '时间修正（秒）', transfer_delay_sec: '传输延时（秒）', sync_time_sec: '同步时间（秒）',
  filter_address: '过滤地址', channel_indices: '频道索引表', mode: '模式原值', sync_count: '同步计数',
  channel_mask: '通道掩码', channel_enabled: '通道启用状态', status: '状态原值',
  input_voltage_mv: '校准输入电压（mV）', channel_mapping: 'PWM 通道映射', unused_tail_hex: '未定义尾部 Hex',
  version_hex: '固件版本标识', auto_pairing: '自动配对原值', signal_mode: '输出模式原值',
  fcu_type: '飞控类型原值', baud_index: '波特率索引', baud_rate: '波特率（bit/s）',
  voltage_reporting: '电压回传配置', vice_pairing: '接力/主副配置原值', tx_power_level: '发射功率档位原值',
  antenna_mode: '天线配置原值', operation: '操作号', data1_function: '端口 1 功能原值',
  data2_function: '端口 2 功能原值', value: '通知原值', prefix_hex: '前缀 Hex', enable: '启用原值',
  stage: '配对阶段', rc_address: '遥控器地址', receiver_address: '接收机地址',
  parameters: '原始参数', control_channel: '控制通道', brightness_level: '亮度档位',
  is_busy: '配对忙状态', key: '查询项', config_value: '配置原值', magic_hex: '标识 Hex',
  index: '索引', params_u32: '原始 uint32 参数',
};

const kindLabels: Record<string, string> = {
  ack: '确认报文', query: '查询', unknown: '业务内容尚未解释', config_set: '配置设置',
  config_value: '配置回复', rc_channels: '遥控通道', extended_rc_channels: '扩展遥控通道',
  pairing_offer: '配对参数', pairing_ack: '配对确认', pairing_handshake: '配对握手',
  switch_master_slave: '主副切换', telemetry: '数据回传', mode_sync: '模式同步',
  time_correction: '时间修正', channel_table: '频道表', transfer_delay: '传输延时',
  firmware_version: '固件版本', voltage_calibration: '电压校准', failsafe: '失控保护',
};

function showValue(value: unknown): string {
  if (value === null || value === undefined) return '未确认';
  if (typeof value === 'boolean') return value ? '是' : '否';
  return typeof value === 'object' ? JSON.stringify(value) : String(value);
}

function BusinessFields({ fields }: { fields: Record<string, unknown> }) {
  return <dl className="divide-y divide-rf-border/50">
    {Object.entries(fields).map(([key, value]) => <div key={key} className="py-2 space-y-1">
      <dt className="text-xs text-zinc-400">{fieldLabels[key] || key} <span className="text-zinc-500 font-mono">{key}</span></dt>
      <dd className="text-sm text-rf-text break-all select-text">
        {Array.isArray(value) ? <ol className="grid grid-cols-2 sm:grid-cols-4 gap-1 font-mono text-xs">
          {value.map((item, index) => <li key={index} className="bg-rf-bg rounded px-2 py-1">
            <span className="text-zinc-500">{key.startsWith('channels_') || key === 'channel_enabled' ? 'CH' + (index + 1) : '[' + index + ']'}: </span>{showValue(item)}
          </li>)}
        </ol> : key === 'kind' ? kindLabels[String(value)] || showValue(value) : showValue(value)}
      </dd>
    </div>)}
  </dl>;
}

export function ProtocolInspectionResult({ result }: { result: ProtocolInspection }) {
  const [selectedPath, setSelectedPath] = useState<string | null>(null);
  const [hoveredPath, setHoveredPath] = useState<string | null>(null);
  const activeRange = result.ranges.find(range => range.path === (hoveredPath || selectedPath));
  const bytes = result.payload_hex.match(/../g) || [];
  const app = result.application;
  const report = JSON.stringify(result, null, 2);
  const exportReport = () => {
    const url = URL.createObjectURL(new Blob([report], { type: 'application/json' }));
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = 'protocol-' + result.protocol + '.json';
    anchor.click();
    setTimeout(() => URL.revokeObjectURL(url), 0);
  };

  return <section aria-label="协议解析结果" className="space-y-4">
    <div className="flex flex-wrap items-center justify-between gap-3">
      <div>
        <h2 className="text-lg font-semibold text-rf-text">{app ? app.command_hex + ' · ' + app.command_name : '业务解析失败'}</h2>
        <p className="text-xs text-zinc-400">{result.protocol} · {result.payload_length} B · 解析器 v{result.parser_version}</p>
      </div>
      <div className="flex gap-2 items-center">
        <CopyButton text={report} label="复制解析 JSON" />
        <button type="button" onClick={exportReport} className="flex items-center gap-1 px-3 py-1.5 text-xs border border-rf-border rounded-lg hover:bg-rf-panel">
          <Download className="w-3.5 h-3.5" aria-hidden="true" />导出 JSON
        </button>
      </div>
    </div>
    {result.error && <p role="alert" className="p-3 rounded-lg border border-red-800 bg-red-950/30 text-red-300 text-sm">{result.error}。原始载荷仍保留在下方。</p>}
    {result.status === 'partial' && <p className="p-3 rounded-lg border border-amber-800 bg-amber-950/30 text-amber-300 text-sm">封装与 sum8 校验通过，但业务字段尚未解释。请查看命令号及原始字节。</p>}
    {app && <div className="grid grid-cols-2 xl:grid-cols-4 gap-2 text-sm">
      <div className="rounded-lg bg-rf-panel border border-rf-border p-3"><span className="block text-xs text-zinc-400">协议结构 / 业务校验</span><span className="text-emerald-300">sum8 通过</span></div>
      <div className="rounded-lg bg-rf-panel border border-rf-border p-3"><span className="block text-xs text-zinc-400">方向（依据固件布局推断）</span>{app.direction === 'ground_to_air' ? '地面 → 天空' : app.direction === 'air_to_ground' ? '天空 → 地面' : '未知方向'}</div>
      <div className="rounded-lg bg-rf-panel border border-rf-border p-3"><span className="block text-xs text-zinc-400">序号 / ACK</span>#{app.sequence} · 请求 {showValue(app.need_ack)} · 确认 {showValue(app.is_ack)}</div>
      <div className="rounded-lg bg-rf-panel border border-rf-border p-3"><span className="block text-xs text-zinc-400">封装 / 时间戳</span>{app.wire_layout === 'timestamp_prefix' ? '带时间戳 · ' + app.timestamp_sec + ' s' : '紧凑帧（无外层时间戳）'}</div>
    </div>}
    <div className="grid grid-cols-1 xl:grid-cols-2 gap-4 items-start">
      <div className="rounded-xl border border-rf-border bg-rf-surface p-4 space-y-4 min-w-0">
        <h3 className="font-semibold text-sm">封装字段 · 点击或悬停高亮字节</h3>
        {result.ranges.length ? <div className="space-y-1">{result.ranges.map(range => <button
          key={range.path} type="button" aria-pressed={selectedPath === range.path}
          onClick={() => setSelectedPath(current => current === range.path ? null : range.path)}
          onMouseEnter={() => setHoveredPath(range.path)} onMouseLeave={() => setHoveredPath(null)}
          onFocus={() => setHoveredPath(range.path)} onBlur={() => setHoveredPath(null)}
          className={'w-full text-left rounded-lg border p-2 focus-visible:outline focus-visible:outline-cyan-400 ' + (activeRange?.path === range.path ? 'border-cyan-500 bg-cyan-950/40' : 'border-transparent hover:bg-rf-panel')}
        >
          <span className="flex flex-wrap justify-between gap-2 text-xs"><span>{range.label}</span><strong className="font-mono break-all">{showValue(range.value)}</strong></span>
          <span className="text-[11px] text-zinc-500 font-mono">偏移 {range.offset} · {range.length} B{range.bit_length === undefined ? '' : ' · bit ' + range.bit_offset + ' 起，共 ' + range.bit_length + ' 位'}</span>
        </button>)}</div> : <p className="text-sm text-zinc-500">结构检查未通过，不猜测字段偏移。</p>}
        {app && <div><h3 className="font-semibold text-sm border-t border-rf-border pt-4">业务内容</h3><BusinessFields fields={app.fields} /></div>}
      </div>
      <div className="rounded-xl border border-rf-border bg-rf-surface p-4 space-y-3 min-w-0 xl:sticky xl:top-4">
        <div className="flex justify-between items-center gap-2"><h3 className="font-semibold text-sm">完整物理载荷 · Hex / ASCII</h3><CopyButton text={result.payload_hex} label="复制 Hex" /></div>
        <p className="text-xs text-zinc-400 min-h-8" aria-live="polite">{activeRange ? activeRange.label + '：偏移 ' + activeRange.offset + '，长度 ' + activeRange.length + ' B' : '偏移从物理载荷第 0 字节开始，不含同步字、硬件头或物理 CRC。'}</p>
        <div className="overflow-x-auto max-h-[32rem] overflow-y-auto font-mono text-xs select-text" tabIndex={0} aria-label="原始载荷字节">
          <div className="min-w-max space-y-1">
            {Array.from({ length: Math.ceil(bytes.length / 16) }, (_, row) => <div key={row} className="flex items-center gap-2 whitespace-nowrap">
              <span className="w-10 text-zinc-500">{(row * 16).toString(16).padStart(4, '0')}</span>
              <div className="grid grid-cols-16 gap-0.5" style={{ gridTemplateColumns: 'repeat(16, 1.5rem)' }}>
                {Array.from({ length: 16 }, (_, column) => {
                  const index = row * 16 + column;
                  const hex = bytes[index];
                  const highlighted = activeRange && index >= activeRange.offset && index < activeRange.offset + activeRange.length;
                  return <span key={column} title={'偏移 ' + index} className={'w-6 py-1 text-center rounded ' + (highlighted ? 'bg-cyan-400 text-black font-bold' : 'text-zinc-300')}>{hex || ' '}</span>;
                })}
              </div>
              <span className="pl-2 border-l border-rf-border text-zinc-500">{bytes.slice(row * 16, row * 16 + 16).map(hex => {
                const byte = parseInt(hex, 16);
                return byte >= 32 && byte <= 126 ? String.fromCharCode(byte) : '.';
              }).join('')}</span>
            </div>)}
          </div>
        </div>
      </div>
    </div>
  </section>;
}
