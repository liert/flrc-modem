import assert from 'node:assert/strict';
import { test } from 'node:test';
import { build } from 'esbuild';

const bundle = await build({ entryPoints: ['src/utils/transmit.ts'], bundle: true, write: false, format: 'esm', platform: 'node' });
const { transmitParameters, transmitCli } = await import(`data:text/javascript;base64,${Buffer.from(bundle.outputFiles[0].text).toString('base64')}`);
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
