import assert from 'node:assert/strict';
import { test } from 'node:test';
import { build } from 'esbuild';

const bundle = await build({ entryPoints: ['src/utils/transmit.ts'], bundle: true, write: false, format: 'esm', platform: 'node' });
const { transmitParameters, transmitCli, transmitRequest, submitTransmit } = await import(`data:text/javascript;base64,${Buffer.from(bundle.outputFiles[0].text).toString('base64')}`);
const draft = {
  mode: 'export', payloadHex: '112233445566', syncWord: '2bd36a07', codingRate: '1/2', headerType: 'fixed',
  sampleRate: 4000000, bitRate: 650000, amplitude: 0.5, rampTimeUs: 20,
  repeat: 2, gapMs: 5, outputPath: 'outputs/my export.iq', centerFreq: 2440000000, gain: 0, sdr: 'hackrf',
};

test('导出 IQ 采用与预览一致的头类型、采样率和爬升时间，CLI 路径正确引用', () => {
  const parameters = transmitParameters(draft);
  assert.equal(parameters.header_type, 'fixed');
  assert.equal(parameters.sample_rate, 4000000);
  assert.equal(parameters.ramp_time_us, 20);
  assert.equal(parameters.output, draft.outputPath);
  assert.equal(parameters.frequency, undefined);
  assert.equal(parameters.tx_gain, undefined);
  const cli = transmitCli(draft);
  assert.ok(cli.includes('--header-type fixed'));
  assert.ok(cli.includes('--sample-rate 4000000'));
  assert.ok(cli.includes('--ramp-time-us 20'));
  assert.ok(cli.includes("--output 'outputs/my export.iq'"));
});

test('实时发射使用实际支持的 HackRF 增益参数，禁止把 USRP 参数交给 HackRF 命令', () => {
  const parameters = transmitParameters({ ...draft, mode: 'live' });
  assert.equal(parameters.tx_gain, 0);
  assert.equal(parameters.frequency, draft.centerFreq);
  assert.equal(parameters.output, undefined);
  assert.equal(parameters.sdr, undefined);
  assert.throws(() => transmitParameters({ ...draft, mode: 'live', sdr: 'usrp' }), /USRP/);
});

const syncDraft = { ...draft, mode: 'preview', synchronized: true, protocol: 'dk32s' };
const syncRun = { id: 'active-sync', attempt: 2, command_id: 'duplex', status: 'RUNNING', command_args: {
  session: true, sdr: 'usrp', protocol: 'dk32s', args: 'addr=192.168.10.2', sample_rate: 2500000,
  bit_rate: 325000, sync: '2bd36a07', header_type: 'variable', tx_invert_sync: true, reply_invert_sync: false,
} };
const session = { ready: { sample_rate: 2500000 }, status: { state: 'locked' }, results: [] };
const context = { runId: syncRun.id, run: syncRun, session };

test('同步只推送当前载荷，沿用同步工作台的会话参数和极性，不发送预览草稿的射频设置', () => {
  const request = transmitRequest(syncDraft, context);
  assert.equal(request.commandId, 'duplex');
  assert.equal(request.runId, syncRun.id);
  assert.deepEqual(request.message, { payload_hex: draft.payloadHex, wait_reply: true, invert_sync: true, reply_invert_sync: false });
  assert.equal(request.parameters, syncRun.command_args);
  assert.equal(request.parameters.sample_rate, 2500000);
  assert.equal(request.message.repeat, undefined);
  assert.equal(request.message.tx_gain, undefined);
  assert.match(transmitCli(syncDraft, context), /flrc duplex .*--session/);
  assert.doesNotMatch(transmitCli(syncDraft, context), /--session true|--repeat|--gap-ms/);
  assert.equal(transmitRequest({ ...syncDraft, mode: 'export' }).commandId, 'modulate');
});

test('同步未开启、仅监听、任务结束、状态未就绪及失锁时均禁止推送', () => {
  assert.throws(() => transmitRequest(syncDraft), /先.*开启同步收发/);
  for (const status of ['SUCCEEDED', 'FAILED', 'CANCELLED', 'QUEUED']) {
    assert.throws(() => transmitRequest(syncDraft, { ...context, run: { ...syncRun, status } }), /未运行或已经停止/);
  }
  assert.throws(() => transmitRequest(syncDraft, { ...context, runId: 'other' }), /未运行/);
  assert.throws(() => transmitRequest(syncDraft, { ...context, run: { ...syncRun, command_id: 'sync' } }), /仅监听/);
  assert.throws(() => transmitRequest(syncDraft, { ...context, run: { ...syncRun, command_args: { session: false } } }), /仅监听/);
  for (const status of ['searching', 'acquiring', 'lost']) {
    assert.throws(() => transmitRequest(syncDraft, { ...context, session: { ...session, status: { state: status } } }), /尚未锁定或已经失锁/);
  }
  assert.throws(() => transmitRequest(syncDraft, { ...context, session: { ...session, ready: null } }), /尚未锁定/);
  assert.throws(() => transmitRequest(syncDraft, { ...context, session: { ...session, status: { state: 'locked', reacquiring: true } } }), /失锁/);
  assert.throws(() => transmitRequest({ ...syncDraft, protocol: 'datalink' }, context), /协议不一致/);
  assert.equal(transmitRequest(syncDraft, { ...context, session: { ...session, status: { state: 'holdover' } } }).runId, syncRun.id);
});

test('同步发送重新确认当前会话并推送一次，不创建第二个 duplex 进程；失锁与停止会阻止实际投递', async () => {
  const calls = [];
  let currentRun = syncRun;
  let currentSession = session;
  const api = {
    async getRun(id) { calls.push(['getRun', id]); return currentRun; },
    async getSession(id, attempt) { calls.push(['getSession', id, attempt]); return currentSession; },
    async sendMessage(id, message) { calls.push(['send', id, message]); return { request_id: message.request_id, status: 'accepted' }; },
    async createRun(...args) { calls.push(['create', ...args]); return { id: 'plain-tx' }; },
  };
  const receipt = await submitTransmit(syncDraft, api, syncRun.id, () => 'unique-message');
  assert.deepEqual(receipt, { kind: 'session', runId: syncRun.id, requestId: 'unique-message' });
  assert.deepEqual(calls.map(call => call[0]), ['getRun', 'getSession', 'send']);
  assert.equal(calls[1][2], 2);
  assert.equal(calls[2][2].request_id, 'unique-message');
  calls.length = 0;
  await assert.rejects(submitTransmit(syncDraft, api, null), /开启同步收发/);
  assert.deepEqual(calls, []);
  currentSession = { ...session, status: { state: 'lost' } };
  await assert.rejects(submitTransmit(syncDraft, api, syncRun.id), /失锁/);
  assert.equal(calls.some(call => call[0] === 'send' || call[0] === 'create'), false);
  calls.length = 0;
  currentRun = { ...syncRun, status: 'CANCELLED' };
  await assert.rejects(submitTransmit(syncDraft, api, syncRun.id), /停止/);
  assert.deepEqual(calls.map(call => call[0]), ['getRun']);
  calls.length = 0;
  await submitTransmit({ ...syncDraft, synchronized: false }, api, null);
  assert.deepEqual(calls.map(call => call[0]), ['create']);
  assert.equal(calls[0][1], 'transmit');
});
