import type { DuplexResult, RunRecord } from '../types';
import type { ProtocolDraft } from '../stores/useWorkstationStore';

export type ProtocolRunSource = Pick<RunRecord, 'id' | 'attempt' | 'command_args'>;

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
