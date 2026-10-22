import { argvPreview } from './runtime';

export interface TransmitDraft {
  mode: 'preview' | 'live' | 'export';
  payloadHex: string; syncWord: string; headerType: string; codingRate: string;
  sampleRate: number; bitRate: number; amplitude: number; rampTimeUs: number;
  repeat: number; gapMs: number; outputPath: string; centerFreq: number; gain: number;
  sdr: 'usrp' | 'hackrf';
}

export function transmitParameters(draft: TransmitDraft): Record<string, string | number> {
  if (draft.mode !== 'export' && draft.sdr !== 'hackrf') {
    throw new Error('实时载荷发射目前支持 HackRF；USRP 请先导出 IQ，再使用射频发射 (tx)');
  }
  const common = {
    payload_hex: draft.payloadHex, sync: draft.syncWord, header_type: draft.headerType,
    sample_rate: draft.sampleRate, bit_rate: draft.bitRate, cr: draft.codingRate,
    amplitude: draft.amplitude, ramp_time_us: draft.rampTimeUs,
    repeat: draft.repeat, gap_ms: draft.gapMs,
  };
  return draft.mode === 'export' ? { ...common, output: draft.outputPath }
    : { ...common, frequency: draft.centerFreq, tx_gain: Math.round(draft.gain) };
}

export function transmitCli(draft: TransmitDraft): string {
  const args = ['flrc', draft.mode === 'export' ? 'modulate' : 'transmit'];
  for (const [name, value] of Object.entries(transmitParameters(draft))) {
    args.push(`--${name.replace(/_/g, '-')}`, String(value));
  }
  return argvPreview(args);
}
