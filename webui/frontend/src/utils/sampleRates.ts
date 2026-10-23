import type { CommandDefinition } from '../types';

// 常用偶数分频候选；实际硬件速率仍由 UHD 在打开设备后确认。
const commonDivisors = [2, 4, 8, 10, 16, 20, 32, 40, 50, 64, 80, 100, 128, 160, 200, 256, 320, 400, 512];

export function selectedMasterClock(command: CommandDefinition, values: Record<string, any>): number | null {
  if (values.sdr === 'hackrf' || !command.parameters.some(parameter => parameter.name === 'master_clock_rate')) return null;
  if (!['number', 'string'].includes(typeof values.master_clock_rate)) return null;
  const clock = Number(values.master_clock_rate);
  return Number.isFinite(clock) && clock > 0 ? clock : null;
}

export function sampleRateCompatible(clock: number, rate: unknown): boolean {
  if (rate === '' || rate === null || rate === undefined) return false;
  if (!['number', 'string'].includes(typeof rate)) return false;
  const value = Number(rate);
  const factor = clock / value;
  return Number.isFinite(value) && value > 0 && factor >= 1 && Math.abs(factor - Math.round(factor)) < 1e-4;
}

export function recommendedSampleRate(clock: number): number {
  return clock / (clock === 125000000 ? 50 : 64);
}

export function normalizeClockedSampleRate(command: CommandDefinition, values: Record<string, any>, templateRate?: unknown): Record<string, any> {
  const clock = selectedMasterClock(command, values);
  if (clock === null) return values;
  const current = values.sample_rate;
  const inherited = templateRate ?? command.parameters.find(parameter => parameter.name === 'sample_rate')?.default;
  const effective = current === '' || current === null || current === undefined ? inherited : current;
  const rate = sampleRateCompatible(clock, effective) ? Number(effective) : recommendedSampleRate(clock);
  return current === rate ? values : { ...values, sample_rate: rate };
}

export function clockedSampleRateOptions(command: CommandDefinition, values: Record<string, any>) {
  const clock = selectedMasterClock(command, values);
  if (clock === null) return null;
  return sampleRateOptionsForClock(clock, values.sample_rate);
}

export function sampleRateOptionsForClock(clock: number, currentRate: unknown, recommended = recommendedSampleRate(clock)) {
  const rates = new Set(commonDivisors.map(factor => clock / factor));
  if (sampleRateCompatible(clock, currentRate)) rates.add(Number(currentRate));
  return [...rates].sort((a, b) => a === recommended ? -1 : b === recommended ? 1 : b - a).map(value => {
    const divisor = Math.round(clock / value);
    const rateLabel = value.toLocaleString('en-US', { maximumFractionDigits: 6 });
    return { value, label: `${rateLabel} S/s · ${divisor} 分频${value === recommended ? '（推荐）' : divisor % 2 ? '（当前奇数分频）' : ''}` };
  });
}
