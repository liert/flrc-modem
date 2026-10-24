import { argvPreview } from './runtime';
import type { DuplexSessionState, RunRecord } from '../types';

export interface TransmitDraft {
  mode: 'preview' | 'live' | 'export';
  payloadHex: string; syncWord: string; headerType: string; codingRate: string;
  sampleRate: number; bitRate: number; amplitude: number; rampTimeUs: number;
  repeat: number; gapMs: number; outputPath: string; centerFreq: number; gain: number;
  sdr: 'usrp' | 'hackrf';
  synchronized?: boolean;
  protocol?: 'dk32s' | 'datalink';
}

export function transmitParameters(draft: TransmitDraft): Record<string, string | number | boolean> {
  if (draft.mode !== 'export' && draft.sdr !== 'hackrf') {
    throw new Error('普通发射使用 HackRF；USRP 请勾选“同步”并使用同步工作台的会话');
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

export interface SyncTransmitContext {
  runId: string | null;
  run?: RunRecord;
  session?: DuplexSessionState;
}

export function synchronizedTransmitRequest(draft: TransmitDraft, context?: SyncTransmitContext) {
  if (!context?.runId) throw new Error('无法使用同步发送：请先在 FHSS 跳频与 TDD 时序同步工作台开启同步收发。');
  const { run, session } = context;
  if (!run) throw new Error('正在读取同步工作台的运行状态，请稍候。');
  if (run.id !== context.runId || run.status !== 'RUNNING') throw new Error('无法使用同步发送：同步工作台的任务未运行或已经停止。');
  if (run.command_id !== 'duplex' || !run.command_args.session) {
    throw new Error('无法使用同步发送：当前为仅监听任务，请在同步工作台选择“同步收发会话（USRP）”并启动。');
  }
  if (!session?.ready || session.status?.error || session.status?.reacquiring
      || !['locked', 'holdover'].includes(session.status?.state ?? '')) {
    throw new Error('无法使用同步发送：同步会话尚未锁定或已经失锁，请等待同步恢复。');
  }
  if ((draft.protocol ?? 'dk32s') !== (run.command_args.protocol || 'dk32s')) {
    throw new Error('无法使用同步发送：当前载荷协议与同步工作台的会话协议不一致。');
  }
  const payload = draft.payloadHex.trim().replace(/\s+/g, '').replace(/^0x/i, '');
  if (!/^(?:[a-f\d]{2}){6,127}$/i.test(payload)) throw new Error('载荷必须为 6..127 字节十六进制数据');
  return {
    commandId: 'duplex' as const, runId: run.id, parameters: run.command_args,
    message: { payload_hex: payload, wait_reply: !run.command_args.no_wait,
      invert_sync: !!run.command_args.tx_invert_sync, reply_invert_sync: !!run.command_args.reply_invert_sync },
  };
}

export function transmitRequest(draft: TransmitDraft, context?: SyncTransmitContext) {
  if (draft.mode !== 'export' && draft.synchronized) return synchronizedTransmitRequest(draft, context);
  return {
    commandId: draft.mode === 'export' ? 'modulate' as const : 'transmit' as const,
    parameters: transmitParameters(draft),
  };
}

export function transmitCli(draft: TransmitDraft, context?: SyncTransmitContext): string {
  return transmitRequestCli(transmitRequest(draft, context));
}

export function transmitRequestCli(request: ReturnType<typeof transmitRequest>): string {
  const args = ['flrc', request.commandId];
  for (const [name, value] of Object.entries(request.parameters)) {
    if (value === false || value === null || value === undefined || value === '') continue;
    const flag = `--${name.replace(/_/g, '-')}`;
    if (value === true) args.push(flag);
    else args.push(flag, String(value));
  }
  const command = argvPreview(args);
  return 'message' in request
    ? `# 复用同步工作台已开启的 duplex 会话：${request.runId}\n# 运行中的命令（无需再次启动）：${command}\n# 向现有会话的标准输入投递 JSONL：\n${JSON.stringify({ request_id: '<request-id>', ...request.message })}`
    : command;
}

interface TransmitApi {
  getRun: (id: string) => Promise<RunRecord>;
  getSession: (id: string, attempt?: number) => Promise<DuplexSessionState>;
  sendMessage: (id: string, message: Record<string, unknown>) => Promise<{ request_id: string; status: string }>;
  createRun: (id: string, parameters: Record<string, unknown>, events: boolean, name: string) => Promise<RunRecord>;
}

export async function submitTransmit(draft: TransmitDraft, api: TransmitApi, syncRunId: string | null,
  requestId: () => string = () => crypto.randomUUID()) {
  if (draft.mode !== 'export' && draft.synchronized) {
    if (!syncRunId) synchronizedTransmitRequest(draft, { runId: null });
    const run = await api.getRun(syncRunId!);
    if (run.id !== syncRunId || run.status !== 'RUNNING' || run.command_id !== 'duplex' || !run.command_args.session) {
      synchronizedTransmitRequest(draft, { runId: syncRunId, run });
    }
    const session = await api.getSession(run.id, run.attempt);
    const request = synchronizedTransmitRequest(draft, { runId: syncRunId, run, session });
    const receipt = await api.sendMessage(run.id, { request_id: requestId(), ...request.message });
    return { kind: 'session' as const, runId: run.id, requestId: receipt.request_id };
  }
  const request = transmitRequest(draft);
  const run = await api.createRun(request.commandId, request.parameters, true,
    draft.mode === 'export' ? 'Export Modulated IQ' : 'Live SDR Transmit (HACKRF)');
  return { kind: 'run' as const, runId: run.id };
}
