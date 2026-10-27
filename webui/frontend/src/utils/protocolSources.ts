import type { DuplexResult, RunRecord } from '../types';
import type { ProtocolDraft } from '../stores/useWorkstationStore';
import type { SpectrumDemodRecord } from '../stores/useSpectrumDemodStore';

export type ProtocolRunSource = Pick<RunRecord, 'id' | 'attempt' | 'command_args'>;

export function spectrumProtocolDraft(record: SpectrumDemodRecord, fallbackProtocol = 'dk32s'): ProtocolDraft | null {
  const { frame } = record;
  if (typeof frame.payload_hex !== 'string' || !frame.payload_hex.trim()) return null;
  const candidate = frame.application?.rc_mode === 'multi' ? 'dk32s-multi' : frame.application?.protocol;
  const supported = (value?: string): value is string => Boolean(value && ['dk32s', 'dk32s-multi', 'datalink'].includes(value));
  const protocol = supported(candidate) ? candidate
    : supported(record.protocol) ? record.protocol : fallbackProtocol;
  return {
    protocol,
    payloadHex: frame.payload_hex,
    source: ['SDR 实时解调', record.sessionId, record.id,
      Number.isFinite(frame.time_s) ? `${frame.time_s.toFixed(4)} s` : '',
      Number.isFinite(frame.frequency_hz) ? `${(frame.frequency_hz / 1e6).toFixed(3)} MHz` : '',
    ].filter(Boolean).join(' · '),
    spectrumOrigin: { sessionId: record.sessionId, packetId: record.id },
  };
}

export function duplexProtocolDraft(result: DuplexResult, direction: 'TX' | 'RX', run?: ProtocolRunSource): ProtocolDraft | null {
  const payloadHex = direction === 'TX' ? result.tx_payload_hex : result.rx_reply?.payload_hex;
  if (typeof payloadHex !== 'string' || !payloadHex.trim()) return null;
  const candidate = run?.command_args.protocol;
  const protocol = candidate === 'dk32s-multi' || candidate === 'datalink' ? candidate : 'dk32s';
  const completion = direction === 'TX' && !result.tx_completed ? '（未确认完成）' : '';
  return {
    protocol, payloadHex,
    source: [run ? run.id + ' · 第 ' + (run.attempt ?? 1) + ' 次执行' : '同步收发交互',
      result.request_id, direction + completion, result.timestamp].filter(Boolean).join(' · '),
    ...(run ? { origin: { runId: run.id, attempt: run.attempt ?? 1 } } : {}),
  };
}
