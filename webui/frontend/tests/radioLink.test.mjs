import assert from 'node:assert/strict';
import { test } from 'node:test';
import { build } from 'esbuild';

const bundle = await build({ stdin: { resolveDir: process.cwd(), contents: `
  export { useRadioWorkbenchStore } from './src/stores/useRadioWorkbenchStore';
  export * from './src/utils/radioLink';
` }, bundle: true, write: false, format: 'esm', platform: 'node' });
const { useRadioWorkbenchStore: store, linkedFrameRequest, linkedSessionParameters, linkedSessionMatches, syncWorkbenchRequest, syncRequestMatches } = await import(
  `data:text/javascript;base64,${Buffer.from(bundle.outputFiles[0].text).toString('base64')}`,
);

test('调制和同步共享同一份帧，往返切换与编辑同步字不丢失载荷或硬件设置', () => {
  store.setState(store.getInitialState());
  store.getState().setFrame({ payloadHex: '0x11 22 33 44 55 66', syncWord: 'b42b2bb4',
    headerType: 'fixed', codingRate: '1/2', amplitude: 0.4, rampTimeUs: 20 });
  store.getState().setTransmit({ centerFreq: 2440000000, gain: 25, outputPath: 'outputs/custom.iq' });
  store.getState().linkToSync();
  assert.equal(store.getState().sync.operation, 'duplex');
  assert.equal(store.getState().sync.initialFreq, 2440000000);
  store.getState().setFrame({ syncWord: 'ecee6698' });
  const request = linkedFrameRequest(store.getState().frame);
  assert.equal(request.payload_hex, '112233445566');
  assert.equal(request.sync_word, 'ecee6698');
  assert.equal(request.header_type, 'fixed');
  assert.equal(request.ramp_time_us, 20);
  assert.equal(store.getState().transmit.outputPath, 'outputs/custom.iq');
});

test('联动启动的是仅监听的常驻会话，载荷等待用户投递，全部物理参数正确带入', () => {
  store.getState().setFrame({ headerType: 'variable' });
  store.getState().setSync({ rfConfig: 'outputs/current radio.json' });
  store.getState().linkToSync();
  const { frame, sync } = store.getState();
  const args = linkedSessionParameters(frame, sync);
  assert.equal(args.session, true);
  assert.equal(args.payload_hex, undefined);
  assert.equal(args.sdr, 'usrp');
  assert.equal(args.tx_gain, 25);
  assert.equal(args.header_type, 'variable');
  assert.equal(args.payload_length, undefined);
  assert.equal(args.rf_config, 'outputs/current radio.json');
  assert.equal(args.amplitude, 0.4);
  assert.equal(args.ramp_time_us, 20);
  assert.equal(args.sync, 'ecee6698');
  assert.equal(linkedSessionMatches(frame, sync, args), true);
  assert.equal(linkedSessionMatches({ ...frame, amplitude: 0.8 }, sync, args), false);
  assert.equal(linkedSessionMatches({ ...frame, payloadHex: '11'.repeat(7) }, sync, args), true);
  assert.equal(linkedSessionMatches(frame, { ...sync, rfConfig: 'another.json' }, args), false);
  assert.equal(linkedSessionMatches({ ...frame, headerType: 'fixed' }, sync, args), false);
});

test('物理帧编辑保持独立，同步收发明确限制为 USRP 和 DK32S 模型', () => {
  const { frame, sync } = store.getState();
  assert.equal(linkedFrameRequest({ ...frame, protocol: 'datalink' }).payload_hex, '112233445566');
  assert.throws(() => linkedSessionParameters({ ...frame, protocol: 'datalink' }, sync), /DK32S/);
  assert.throws(() => linkedSessionParameters({ ...frame, headerType: 'fixed' }, sync), /变长头/);
  assert.throws(() => linkedSessionParameters(frame, { ...sync, sdr: 'hackrf' }), /USRP/);
});


test('同步监听参数完整传递，零增益和持续运行不会被省略', () => {
  const initial = store.getInitialState();
  const settings = { ...initial.sync, operation: 'listen', masterClockRate: 122880000,
    sampleRate: 3840000, channel: 2, antenna: 'RX2', gain: 0, duration: 0, bt: 0.5,
    timedHopping: true, outputPath: 'outputs/custom-sync.jsonl' };
  const frame = { ...initial.frame, bitRate: 650000, codingRate: '1/2' };
  const request = syncWorkbenchRequest(frame, settings);
  assert.equal(request.commandId, 'sync');
  assert.equal(request.parameters.master_clock_rate, 122880000);
  assert.equal(request.parameters.channel, 2);
  assert.equal(request.parameters.sample_rate, 3840000);
  assert.equal(request.parameters.bit_rate, 650000);
  assert.equal(request.parameters.cr, '1/2');
  assert.equal(request.parameters.bt, 0.5);
  assert.equal(request.parameters.gain, 0);
  assert.equal(request.parameters.duration, 0);
  assert.equal(request.parameters.output, 'outputs/custom-sync.jsonl');
  assert.equal(syncRequestMatches(request, { command_id: 'sync', command_args: { ...request.parameters,
    sample_rate: '3840000', master_clock_rate: '122880000' } }), true);
  assert.equal(syncRequestMatches(request, { command_id: 'sync', command_args: { ...request.parameters, channel: 0 } }), false);
});

test('同步收发传递硬件和极性草稿，并拒绝无效参数', () => {
  const initial = store.getInitialState();
  const settings = { ...initial.sync, operation: 'duplex', channel: 1, masterClockRate: 125000000,
    bt: 0.5, syncTimeout: 30, replyTimeout: 2, txInvertSync: true, replyInvertSync: true };
  const request = syncWorkbenchRequest(initial.frame, settings);
  assert.equal(request.parameters.channel, 1);
  assert.equal(request.parameters.master_clock_rate, 125000000);
  assert.equal(request.parameters.sync_timeout, 30);
  assert.equal(request.parameters.timeout, 2);
  assert.equal(request.parameters.tx_invert_sync, true);
  assert.equal(request.parameters.reply_invert_sync, true);
  assert.throws(() => syncWorkbenchRequest(initial.frame, { ...settings, sampleRate: 0 }), /采样率/);
  assert.throws(() => syncWorkbenchRequest(initial.frame, { ...settings, channel: -1 }), /通道/);
});
