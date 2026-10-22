import type { ModulatePreviewRequest } from '../types';
import type { RadioFrameDraft } from '../stores/useRadioWorkbenchStore';

export function linkedFrameRequest(frame: RadioFrameDraft): ModulatePreviewRequest {
  return { payload_hex: frame.payloadHex.trim().replace(/\s+/g, '').replace(/^0x/i, ''),
    sync_word: frame.syncWord, coding_rate: frame.codingRate, header_type: frame.headerType,
    bit_rate: frame.bitRate, sample_rate: frame.sampleRate, ramp_time_us: frame.rampTimeUs,
    amplitude: frame.amplitude };
}

export function linkedSessionParameters(frame: RadioFrameDraft, settings: {
  sdr: 'usrp' | 'hackrf'; usrpArgs: string; rfConfig?: string; initialFreq: number; sampleRate: number;
  gain: number; txGain: number; antenna: string; timedHopping: boolean;
}) {
  if (settings.sdr !== 'usrp') throw new Error('同步时隙收发需要 USRP，请选择 USRP 硬件');
  if (frame.protocol !== 'dk32s') throw new Error('当前联动收发使用 DK32S FHSS/TDD 同步模型');
  if (frame.headerType !== 'variable') throw new Error('实时同步收发使用 DK32S 变长头；固定头可进行物理时序验证');
  const payload = linkedFrameRequest(frame).payload_hex;
  if (!/^(?:[a-f\d]{2}){6,127}$/i.test(payload)) throw new Error('关联载荷必须为 6..127 字节十六进制数据');
  return { session: true, sdr: 'usrp', args: settings.usrpArgs, frequency: settings.initialFreq,
    sample_rate: settings.sampleRate, gain: settings.gain, tx_gain: settings.txGain,
    antenna: settings.antenna, timed_hopping: settings.timedHopping, protocol: 'dk32s',
    sync: frame.syncWord, bit_rate: frame.bitRate, cr: frame.codingRate, header_type: frame.headerType,
    amplitude: frame.amplitude, ramp_time_us: frame.rampTimeUs,
    ...(settings.rfConfig?.trim() ? { rf_config: settings.rfConfig.trim() } : {}), json: true };
}

export function linkedSessionMatches(frame: RadioFrameDraft, settings: Parameters<typeof linkedSessionParameters>[1], args: Record<string, unknown>): boolean {
  try {
    return Object.entries(linkedSessionParameters(frame, settings))
      .every(([key, value]) => (args[key] ?? false) === value);
  } catch { return false; }
}
