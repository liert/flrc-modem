import assert from 'node:assert/strict';
import { test } from 'node:test';
import { build } from 'esbuild';

const bundle = await build({ entryPoints: ['src/utils/syncTemplates.ts'], bundle: true, write: false, format: 'esm', platform: 'node' });
const { supportsSyncTemplate, syncTemplatePatch, templateReference } = await import(
  `data:text/javascript;base64,${Buffer.from(bundle.outputFiles[0].text).toString('base64')}`,
);
const rf = {
  category: 'rf_config', applicable_commands: ['sync', 'duplex'], path: 'templates\\rf\\current.json',
  parsed: { hopping_table: [{ frequency_hz: 2450060000 }, { frequency_hz: 2471660000 }],
    sync_word: { raw_hex: '0xbdfb7f52', ota_sync_word_hex: '527ffbbd' } },
};

test('选择跳频模板时使用 OTA 同步字并更新首个监听频点', () => {
  assert.deepEqual(syncTemplatePatch(rf, 'rf'), {
    frame: { syncWord: '527ffbbd' }, sync: { rfConfig: rf.path, initialFreq: 2450060000 },
  });
  assert.deepEqual(syncTemplatePatch({ ...rf, parsed: { radio_config: {
    hopping_table: [2400060000], sync_word: '0x4A65B21C',
  } } }, 'rf').frame, { syncWord: '4a65b21c' });
  assert.equal(templateReference('templates\\\\rf\\\\current.json'), templateReference('rf/current.json'));
});

test('DK32S 物理预设只加载支持的物理参数，保留硬件频率与采样率', () => {
  const template = { category: 'physical_config', path: 'templates/physical/dk32s.toml',
    parsed: { template: { protocol: 'dk32s' }, demod: { bit_rate: 325000, coding_rate: '1/2', bt: 0.5,
      sample_rate: 2000000, frequencies: [2400000000], crc_bytes: 3, crc_initial: 0xFFFFFF } } };
  const patch = syncTemplatePatch(template, 'physical');
  assert.equal(patch.frame.codingRate, '1/2');
  assert.equal(patch.frame.syncWord, undefined);
  assert.equal(patch.sync.bt, 0.5);
  assert.equal(patch.sync.sampleRate, undefined);
  assert.equal(patch.sync.initialFreq, undefined);
  assert.equal(supportsSyncTemplate({ ...template, parsed: { ...template.parsed,
    template: { protocol: 'datalink' } } }, 'physical'), false);
  assert.equal(supportsSyncTemplate({ ...template, parsed: { demod: { header_type: 'fixed' } } }, 'physical'), false);
  assert.deepEqual(syncTemplatePatch(null, 'physical'), { sync: { physicalTemplate: '' } });
  assert.deepEqual(syncTemplatePatch(null, 'rf'), { sync: { rfConfig: '' } });
});
