import React from 'react';
import { changePhysicalParameter, readPhysicalParameters, PHYSICAL_BIT_RATES, PHYSICAL_CODING_RATES,
  PHYSICAL_BT, PHYSICAL_HEADERS, PHYSICAL_CRC_BYTES, type PhysicalParameter } from '../utils/physicalTemplate';

export function PhysicalTemplateFields({ content, onChange, label = '物理参数设置' }: {
  content: string; onChange: (content: string) => void; label?: string;
}) {
  let values: ReturnType<typeof readPhysicalParameters>;
  try { values = readPhysicalParameters(content); } catch (error) {
    return <p role="status" className="text-xs text-amber-300">参数选择暂不可用：{(error as Error).message}。可继续在源码中编辑。</p>;
  }
  const options: { key: PhysicalParameter; title: string; choices: (number | string)[]; text: (value: number | string) => string }[] = [
    { key: 'bit_rate', title: '码率', choices: PHYSICAL_BIT_RATES, text: value => `${Number(value) / 1000} kbps` },
    { key: 'coding_rate', title: '编码率', choices: PHYSICAL_CODING_RATES, text: value => value === '1' ? '1/1（无 FEC）' : String(value) },
    { key: 'bt', title: 'BT', choices: PHYSICAL_BT, text: value => Number(value) === 0 ? '0（关闭高斯成形）' : String(value) },
    { key: 'header_type', title: '包头', choices: PHYSICAL_HEADERS, text: value => ({ variable: '变长头', fixed: '固定头', auto: '自动检测（解调）' }[String(value)] || String(value)) },
    { key: 'crc_bytes', title: 'CRC', choices: PHYSICAL_CRC_BYTES, text: value => `CRC${Number(value) * 8}` },
  ];
  return <fieldset aria-label={label} className="shrink-0 space-y-3 rounded-xl border border-rf-border/90 bg-rf-panel/35 p-4 shadow-xs">
    <legend className="px-2.5 py-0.5 rounded-md border border-cyan-500/30 bg-rf-surface text-xs font-bold font-mono text-cyan-300 shadow-2xs">{label}</legend>
    <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
      {options.map(option => <label key={option.key} className="flex flex-col gap-1.5 rounded-lg border border-rf-border/70 bg-rf-bg/70 p-2.5 text-xs text-zinc-300 transition-colors hover:border-zinc-600 focus-within:border-cyan-400/70">
        <span className="font-mono text-[11px] font-semibold text-zinc-400">{option.title}</span>
        <select aria-label={option.title} value={String(values[option.key])}
          onChange={event => onChange(changePhysicalParameter(content, option.key, event.target.value))}
          className="h-8 w-full rounded-md border border-rf-border bg-rf-surface px-2 text-xs font-mono text-cyan-200 focus:border-cyan-400 focus:outline-none transition-colors">
          {!option.choices.some(choice => String(choice) === String(values[option.key])) &&
            <option value={String(values[option.key])}>源码值：{String(values[option.key])}</option>}
          {option.choices.map(choice => <option key={choice} value={String(choice)}>{option.text(choice)}</option>)}
        </select>
      </label>)}
    </div>
    <div className="space-y-1 rounded-lg border border-rf-border/60 bg-rf-bg/60 px-3 py-2 text-[11px] leading-relaxed text-zinc-400">
      <p>选择与源码双向同步。实际采样率需 ≥ <span className="font-mono font-semibold text-cyan-300">{(values.bit_rate * 4 / 1e6).toLocaleString('en-US')} MSps</span>；切换码率会调整模板的滤波预设，并提高不足的模板采样率。</p>
      <p className="text-zinc-500">更改 CRC 位宽会将初值设为全 1，并清除旧的候选初值。自定义初值、同步字等仍可在源码中修改。</p>
    </div>
    {values.header_type !== 'variable' && <p className="rounded-lg border border-amber-800/60 bg-amber-950/40 px-3 py-2 text-[11px] text-amber-300">已知固定包长时请在源码设置 payload_lengths；未设置时会尝试 6..127 B，实时解调开销较大。</p>}
  </fieldset>;
}
