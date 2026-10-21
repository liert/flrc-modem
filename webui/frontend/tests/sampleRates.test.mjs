import assert from 'node:assert/strict';
import { test } from 'node:test';
import { build } from 'esbuild';

async function loadModule(path) {
  const result = await build({ entryPoints: [path], bundle: true, write: false, format: 'esm', platform: 'node' });
  return import(`data:text/javascript;base64,${Buffer.from(result.outputFiles[0].text).toString('base64')}`);
}

const { clockedSampleRateOptions, normalizeClockedSampleRate, sampleRateCompatible } = await loadModule('src/utils/sampleRates.ts');
const { cliPreview } = await loadModule('src/utils/runtime.ts');
const { commandDraftParameters } = await loadModule('src/utils/commandDraft.ts');

const command = { id: 'duplex', subcommand: 'duplex', parameters: [
  { name: 'master_clock_rate', type: 'choice', cli_flag: '--master-clock-rate', choices: [125000000, 122880000, 153600000] },
  { name: 'sample_rate', type: 'number', cli_flag: '--sample-rate' },
] };

test('三个 N310 主时钟提供各自推荐采样率，候选均为偶数整数分频', () => {
  for (const [master_clock_rate, recommended] of [[125000000, 2500000], [122880000, 1920000], [153600000, 2400000]]) {
    const values = normalizeClockedSampleRate(command, { master_clock_rate, sample_rate: '' });
    assert.equal(values.sample_rate, recommended);
    const options = clockedSampleRateOptions(command, values);
    assert.equal(options[0].value, recommended);
    assert.ok(options[0].label.includes('推荐'));
    for (const option of options) {
      assert.ok(sampleRateCompatible(master_clock_rate, option.value));
      const factor = master_clock_rate / option.value;
      assert.ok(Math.abs(factor - Math.round(factor)) < 1e-4);
      assert.equal(Math.round(factor) % 2, 0);
    }
  }
});

test('更换主时钟纠正不兼容旧采样率，保留兼容的自定义整除值', () => {
  const previous = { master_clock_rate: '122880000', sample_rate: 2500000, rf_config: 'templates/rf/a.json' };
  const corrected = normalizeClockedSampleRate(command, previous);
  assert.equal(corrected.sample_rate, 1920000);
  assert.equal(corrected.rf_config, previous.rf_config);
  assert.equal(previous.sample_rate, 2500000);
  for (const factor of [62, 63]) {
    const valid = { master_clock_rate: 125000000, sample_rate: 125000000 / factor };
    assert.equal(normalizeClockedSampleRate(command, valid), valid);
    assert.ok(clockedSampleRateOptions(command, valid).some(option => option.value === valid.sample_rate));
  }
});

test('模板与命令默认采样率参与兼容检查，显式采样率优先', () => {
  assert.equal(normalizeClockedSampleRate(command, { master_clock_rate: 122880000, sample_rate: '' }, 2500000).sample_rate, 1920000);
  assert.equal(normalizeClockedSampleRate(command, { master_clock_rate: 122880000, sample_rate: '' }, 3840000).sample_rate, 3840000);
  assert.equal(normalizeClockedSampleRate(command, { master_clock_rate: 122880000, sample_rate: 1920000 }, 3840000).sample_rate, 1920000);
  const capture = { ...command, id: 'rx', parameters: [command.parameters[0], { ...command.parameters[1], default: 2500000 }] };
  assert.equal(normalizeClockedSampleRate(capture, { master_clock_rate: 153600000, sample_rate: '' }).sample_rate, 2400000);
});

test('未指定或清除主时钟、HackRF、无主时钟的命令均保留自由输入', () => {
  for (const master_clock_rate of ['', undefined, null, NaN, 0, true]) {
    const values = { master_clock_rate, sample_rate: 2000000 };
    assert.equal(clockedSampleRateOptions(command, values), null);
    assert.equal(normalizeClockedSampleRate(command, values), values);
  }
  const hackrf = { sdr: 'hackrf', master_clock_rate: 125000000, sample_rate: 2000000 };
  assert.equal(clockedSampleRateOptions(command, hackrf), null);
  assert.equal(normalizeClockedSampleRate(command, hackrf), hackrf);
  assert.equal(clockedSampleRateOptions({ parameters: [] }, { master_clock_rate: 125000000 }), null);
  for (const invalid of [0, -1, NaN, Infinity, true]) assert.equal(sampleRateCompatible(125000000, invalid), false);
});

test('自动修正后 CLI 预览和执行参数使用同一采样率', () => {
  const normalized = normalizeClockedSampleRate(command, { master_clock_rate: '122880000', sample_rate: 2500000 });
  const values = commandDraftParameters(command, normalized, '');
  assert.equal(values.sample_rate, 1920000);
  assert.equal(cliPreview(command, values), 'python -m flrc_modem duplex --master-clock-rate 122880000 --sample-rate 1920000 --ui-events ndjson');
});
