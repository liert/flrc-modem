import type { ModulatePreviewRequest } from '../types';
import type { RadioFrameDraft, SyncSettings } from '../stores/useRadioWorkbenchStore';

export function linkedFrameRequest(frame: RadioFrameDraft): ModulatePreviewRequest {
  return { payload_hex: frame.payloadHex.trim().replace(/\s+/g, '').replace(/^0x/i, ''),
    sync_word: frame.syncWord, coding_rate: frame.codingRate, header_type: frame.headerType,
    bit_rate: frame.bitRate, sample_rate: frame.sampleRate, ramp_time_us: frame.rampTimeUs,
    amplitude: frame.amplitude };
}

export function linkedSessionParameters(frame: RadioFrameDraft, settings: {
  sdr: 'usrp' | 'hackrf'; usrpArgs: string; rfConfig?: string; initialFreq: number; sampleRate: number;
  gain: number; txGain: number; antenna: string; timedHopping: boolean;
  masterClockRate?: number | null; channel?: number; bt?: number;
  syncTimeout?: number; replyTimeout?: number; txInvertSync?: boolean; replyInvertSync?: boolean;
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
    ...(settings.masterClockRate != null ? { master_clock_rate: settings.masterClockRate } : {}),
    ...(settings.channel !== undefined ? { channel: settings.channel } : {}),
    ...(settings.bt !== undefined ? { bt: settings.bt } : {}),
    ...(settings.syncTimeout !== undefined ? { sync_timeout: settings.syncTimeout } : {}),
    ...(settings.replyTimeout !== undefined ? { timeout: settings.replyTimeout } : {}),
    tx_invert_sync: settings.txInvertSync ?? false, reply_invert_sync: settings.replyInvertSync ?? false,
    ...(settings.rfConfig?.trim() ? { rf_config: settings.rfConfig.trim() } : {}), json: true };
}

export function linkedSessionMatches(frame: RadioFrameDraft, settings: Parameters<typeof linkedSessionParameters>[1], args: Record<string, unknown>): boolean {
  try {
    return syncRequestMatches({ commandId: 'duplex', parameters: linkedSessionParameters(frame, settings) },
      { command_id: 'duplex', command_args: args });
  } catch { return false; }
}


// The editor, CLI preview and run creation all resolve the same parameter set.
export function syncWorkbenchRequest(frame: RadioFrameDraft, settings: SyncSettings) {
  const positive = (value: number, label: string, allowZero = false) => {
    if (!Number.isFinite(value) || (allowZero ? value < 0 : value <= 0)) throw new Error(`${label}必须为${allowZero ? '非负' : '正'}数`);
  };
  positive(settings.initialFreq, '监听频率');
  if (!Number.isInteger(settings.initialFreq)) throw new Error('监听频率必须为整数 Hz');
  positive(settings.sampleRate, '采样率');
  positive(frame.bitRate, '空口码率');
  if (![0, 0.5, 1].includes(settings.bt)) throw new Error('BT 只能为 0、0.5 或 1');
  positive(settings.gain, '接收增益', true);
  positive(settings.duration, '持续时间', true);
  if (!Number.isInteger(settings.channel) || settings.channel < 0) throw new Error('接收通道必须为非负整数');
  if (settings.sdr === 'usrp' && settings.masterClockRate !== null) positive(settings.masterClockRate, '主时钟');
  if (frame.protocol !== 'dk32s') throw new Error('FHSS/TDD 时间基准目前仅支持 DK32S，DataLink 不含所需地面时间戳');
  if (frame.headerType !== 'variable') throw new Error('当前同步工作台使用 DK32S 变长头，请将物理帧头设为 variable');
  if (frame.syncWord.trim() && !/^[a-f\d]{8}$/i.test(frame.syncWord.trim())) throw new Error('同步字必须为 8 位十六进制；也可留空自动捕获');
  if (settings.sampleRate < 4 * frame.bitRate) throw new Error('采样率至少应为空口码率的 4 倍');
  if (settings.operation === 'duplex') {
    positive(settings.txGain, '发送增益', true);
    positive(settings.syncTimeout, '同步等待时间');
    positive(settings.replyTimeout, '回复等待时间');
    return { commandId: 'duplex' as const, parameters: linkedSessionParameters(frame, settings) };
  }
  const parameters: Record<string, unknown> = {
    sdr: settings.sdr, protocol: frame.protocol, frequency: settings.initialFreq,
    sample_rate: settings.sampleRate, gain: settings.gain, sync: frame.syncWord.trim(),
    bit_rate: frame.bitRate, cr: frame.codingRate, bt: settings.bt,
    duration: settings.duration, output: settings.outputPath.trim(),
    rf_config: settings.rfConfig.trim(),
  };
  if (settings.sdr === 'usrp') Object.assign(parameters, {
    args: settings.usrpArgs.trim(), channel: settings.channel, antenna: settings.antenna.trim(),
    timed_hopping: settings.timedHopping,
    ...(settings.masterClockRate !== null ? { master_clock_rate: settings.masterClockRate } : {}),
  });
  else {
    if (settings.serial.trim() && settings.deviceIndex !== null) throw new Error('HackRF 序列号与设备索引只能选择一个');
    if (settings.deviceIndex !== null && (!Number.isInteger(settings.deviceIndex) || settings.deviceIndex < 0)) throw new Error('设备索引必须为非负整数');
    if (settings.serial.trim()) parameters.serial = settings.serial.trim();
    if (settings.deviceIndex !== null) parameters.device_index = settings.deviceIndex;
  }
  return { commandId: 'sync' as const, parameters };
}

export function syncRequestMatches(request: ReturnType<typeof syncWorkbenchRequest>, run: { command_id: string; command_args: Record<string, any> }) {
  if (request.commandId !== run.command_id) return false;
  const defaults: Record<string, unknown> = { channel: 0, bt: 1, sync_timeout: 5, timeout: 1,
    duration: 0, bit_rate: 325000, cr: '3/4', header_type: 'variable', master_clock_rate: null,
    args: 'addr=192.168.10.2', antenna: 'RX2', gain: 30, tx_gain: 30 };
  const keys = new Set([...Object.keys(request.parameters), 'master_clock_rate', 'rf_config', 'sync', 'serial', 'device_index']);
  return [...keys].every(key => {
    const resolve = (values: Record<string, unknown>) => {
      const value = values[key];
      return value === undefined || value === null || value === '' ? defaults[key] ?? false : value;
    };
    const expected = resolve(request.parameters), actual = resolve(run.command_args);
    return typeof expected === 'number' ? expected === Number(actual) : expected === actual;
  });
}
