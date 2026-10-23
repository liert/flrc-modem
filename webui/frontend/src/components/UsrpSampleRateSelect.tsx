import React from 'react';
import { recommendedSampleRate, sampleRateOptionsForClock } from '../utils/sampleRates';

const clocks = [125000000, 122880000, 153600000];
const formatRate = (rate: number) => (rate / 1e6).toLocaleString('en-US', { maximumFractionDigits: 6 });

export function UsrpSampleRateSelect({ id, className, masterClockRate, sampleRate, minRate = 1000,
  maxRate = 64000000, recommendedDivisor, onChange }: {
  id: string; className: string; masterClockRate: number | null; sampleRate: number;
  minRate?: number; maxRate?: number; recommendedDivisor?: number;
  onChange: (value: { masterClockRate: number; sampleRate: number }) => void;
}) {
  const availableClocks = masterClockRate !== null && !clocks.includes(masterClockRate)
    ? [masterClockRate, ...clocks] : clocks;
  const recommendedRate = (clock: number) => recommendedDivisor ? clock / recommendedDivisor
    : recommendedSampleRate(clock) >= minRate ? recommendedSampleRate(clock) : clock / 10;
  const groups = availableClocks.map(clock => ({ clock, options: sampleRateOptionsForClock(clock,
    clock === masterClockRate ? sampleRate : undefined,
    recommendedRate(clock))
    .filter(option => option.value <= maxRate) }));
  const value = `${masterClockRate ?? 'default'}:${sampleRate}`;
  const listed = groups.some(group => group.clock === masterClockRate && group.options.some(option => option.value === sampleRate));
  return <select id={id} className={className} value={value} onChange={event => {
    const [clock, rate] = event.target.value.split(':').map(Number);
    if (Number.isFinite(clock) && Number.isFinite(rate) && rate >= minRate && rate <= maxRate)
      onChange({ masterClockRate: clock, sampleRate: rate });
  }}>
    {!listed && <option value={value} disabled>{formatRate(sampleRate)} MSps · {masterClockRate === null
      ? '设备默认主时钟（选择分组以指定时钟）' : '当前值（请重新选择分频）'}</option>}
    {groups.map(group => <optgroup key={group.clock} label={`${formatRate(group.clock)} MHz 主时钟${group.clock === masterClockRate ? '（当前）' : ''}`}>
      {group.options.map(option => {
        const recommended = recommendedRate(group.clock);
        return <option key={option.value} value={`${group.clock}:${option.value}`} disabled={option.value < minRate}>
          {formatRate(option.value)} MSps · {Math.round(group.clock / option.value)} 分频{option.value < minRate
            ? '（低于解调要求）' : option.value === recommended ? '（推荐）' : ''}
        </option>;
      })}
    </optgroup>)}
  </select>;
}
