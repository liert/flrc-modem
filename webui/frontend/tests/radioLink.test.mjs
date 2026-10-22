import assert from 'node:assert/strict';
import { test } from 'node:test';
import { build } from 'esbuild';

const bundle = await build({ stdin: { resolveDir: process.cwd(), contents: `
  export { useRadioWorkbenchStore } from './src/stores/useRadioWorkbenchStore';
  export * from './src/utils/radioLink';
` }, bundle: true, write: false, format: 'esm', platform: 'node' });
const { useRadioWorkbenchStore: store, linkedFrameRequest, linkedSessionParameters, linkedSessionMatches } = await import(
  `data:text/javascript;base64,${Buffer.from(bundle.outputFiles[0].text).toString('base64')}`,
);

test('调制和同步共享同一份帧，往返切换与编辑同步字不丢失载荷或硬件设置', () => {
  store.setState(store.getInitialState());
  store.getState().setFrame({ payloadHex: '0x11 22 33 44 55 66', syncWord: 'b42b2bb4',
    headerType: 'fixed', codingRate: '1/2', amplitude: 0.4, rampTimeUs: 20 });
  store.getState().setTransmit({ centerFreq: 2440000000, gain: 25, outputPath: 'outputs/custom.iq' });
  store.getState().linkToSync('simulate');
  assert.equal(store.getState().sync.mode, 'simulate');
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
  store.getState().linkToSync('duplex');
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

test('DataLink 可带入物理时序验证，同步收发明确限制为 USRP 和 DK32S 模型', () => {
  const { frame, sync } = store.getState();
  assert.equal(linkedFrameRequest({ ...frame, protocol: 'datalink' }).payload_hex, '112233445566');
  assert.throws(() => linkedSessionParameters({ ...frame, protocol: 'datalink' }, sync), /DK32S/);
  assert.throws(() => linkedSessionParameters({ ...frame, headerType: 'fixed' }, sync), /变长头/);
  assert.throws(() => linkedSessionParameters(frame, { ...sync, sdr: 'hackrf' }), /USRP/);
});
