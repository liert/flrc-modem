import assert from 'node:assert/strict';
import { test } from 'node:test';
import { build } from 'esbuild';

const bundle = await build({
  stdin: {
    contents: `
      export { useScanStore } from './src/stores/useScanStore';
      export { useWorkstationStore } from './src/stores/useWorkstationStore';
    `,
    resolveDir: process.cwd(),
  },
  bundle: true, write: false, format: 'esm', platform: 'node',
});
const { useScanStore, useWorkstationStore } = await import(
  `data:text/javascript;base64,${Buffer.from(bundle.outputFiles[0].text).toString('base64')}`
);

const channel = frequencyHz => ({
  frequency_hz: frequencyHz, frequency_mhz: frequencyHz / 1e6,
  bandwidth_hz: 350000, bandwidth_khz: 350,
  peak_power_dbfs: -30, snr_db: 40, noise_floor_dbfs: -70,
  burst_count: 2, duty_cycle: 0.2, is_flrc: true, flrc_confidence: 1,
  sync_words: ['4a65b21c'], bit_rate: 325000, protocol_hint: 'FLRC',
});
const report = channels => ({
  timestamp: '2026-10-22T14:00:00', scan_mode: 'live_sdr_hackrf',
  start_freq_mhz: 2400, stop_freq_mhz: 2483.5,
  sample_rate_sps: 10000000, center_frequency_mhz: 2441.75,
  noise_floor_dbfs: -70, threshold_db: 10,
  total_channels: channels.length, flrc_channels_count: channels.length,
  channels, device_info: { sdr: 'hackrf' },
});

test('切换到其他页面并返回盲扫后，完整报告和所选信道仍然可用', () => {
  useScanStore.setState(useScanStore.getInitialState());
  const result = report([channel(2405000000), channel(2440000000)]);
  useWorkstationStore.getState().setActiveTab('scan');
  useScanStore.getState().setReport(result);
  useScanStore.getState().selectChannel(result.channels[1].frequency_hz);

  for (const tab of ['commands', 'spectrum', 'signals', 'scan']) {
    useWorkstationStore.getState().setActiveTab(tab);
  }

  const restored = useScanStore.getState();
  assert.deepEqual(restored.report, result);
  assert.equal(restored.selectedChannelFrequencyHz, 2440000000);
  assert.deepEqual(restored.report.channels.find(
    candidate => candidate.frequency_hz === restored.selectedChannelFrequencyHz,
  ).sync_words, ['4a65b21c']);
});

test('新的成功扫描替换旧报告并选择新报告首个信道', () => {
  useScanStore.getState().setReport(report([channel(2405000000), channel(2440000000)]));
  useScanStore.getState().selectChannel(2440000000);
  const replacement = report([channel(2470000000)]);
  useScanStore.getState().setReport(replacement);
  assert.deepEqual(useScanStore.getState().report, replacement);
  assert.equal(useScanStore.getState().selectedChannelFrequencyHz, 2470000000);
});

test('无信道的新结果替换旧报告，返回页面不会复活上次的信道', () => {
  useScanStore.getState().setReport(report([channel(2440000000)]));
  const empty = report([]);
  useScanStore.getState().setReport(empty);
  useWorkstationStore.getState().setActiveTab('tasks');
  useWorkstationStore.getState().setActiveTab('scan');
  assert.deepEqual(useScanStore.getState().report, empty);
  assert.equal(useScanStore.getState().selectedChannelFrequencyHz, null);
});

test('未启动扫描的输入草稿在切换页面和扫描模式后仍完整保留', () => {
  useScanStore.setState(useScanStore.getInitialState());
  const draft = {
    ...useScanStore.getInitialState().draft,
    scanMode: 'offline', iqPath: 'captures/custom.cf32', sampleRate: 8000000,
    centerFreqMhz: 2462.25, iqFormat: 'cf32', duration: 1.25,
    startFreqMhz: 2412.5, stopFreqMhz: 2472.5, stepBwMhz: 8,
    dwell: 0.15, gain: 0, thresholdDb: 0, minSnr: 0,
    selectedBitrate: 650000, noFlrc: true, showAdvanced: true,
  };
  useScanStore.getState().updateDraft(draft);
  for (const mode of ['hackrf', 'usrp', 'offline']) {
    useScanStore.getState().updateDraft({ scanMode: mode });
    useWorkstationStore.getState().setActiveTab('commands');
    useWorkstationStore.getState().setActiveTab('scan');
    assert.deepEqual(useScanStore.getState().draft, { ...draft, scanMode: mode });
  }
  assert.equal(useScanStore.getState().report, null);
});

test('扫描完成和选择信道不会覆盖正在编辑的参数，自动码率和关闭开关也会保存', () => {
  useScanStore.getState().updateDraft({ scanMode: 'usrp', gain: 20, noFlrc: true, selectedBitrate: 650000 });
  useScanStore.getState().updateDraft({ noFlrc: false, selectedBitrate: undefined, showAdvanced: false });
  const edited = { ...useScanStore.getState().draft };
  useScanStore.getState().setReport(report([channel(2440000000)]));
  useScanStore.getState().selectChannel(2440000000);
  useWorkstationStore.getState().setActiveTab('spectrum');
  useWorkstationStore.getState().setActiveTab('scan');
  assert.deepEqual(useScanStore.getState().draft, edited);
  assert.equal(useScanStore.getState().draft.noFlrc, false);
  assert.equal(useScanStore.getState().draft.selectedBitrate, undefined);
});

test('实时结果更新保留选中信道及输入参数，并刷新检验结果', () => {
  useScanStore.setState(useScanStore.getInitialState());
  const first = channel(2405000000), selected = channel(2440000000);
  useScanStore.getState().updateDraft({ scanMode: 'hackrf', gain: 20 });
  useScanStore.getState().beginScan('current');
  useScanStore.getState().updateReport(report([first, { ...selected, is_flrc: false, sync_words: [], protocol_hint: '待 FLRC 检验' }]), 'current');
  useScanStore.getState().selectChannel(selected.frequency_hz);
  const updated = report([first, selected, channel(2470000000)]);
  useScanStore.getState().updateReport(updated, 'current');
  assert.deepEqual(useScanStore.getState().report, updated);
  assert.equal(useScanStore.getState().selectedChannelFrequencyHz, selected.frequency_hz);
  assert.equal(useScanStore.getState().draft.gain, 20);
});

test('重新扫描后迟到的旧进度和停止结果不会覆盖新扫描', () => {
  useScanStore.getState().beginScan('old');
  useScanStore.getState().beginScan('new');
  const newest = report([channel(2470000000)]);
  useScanStore.getState().updateReport(newest, 'new');
  useScanStore.getState().updateReport(report([channel(2405000000)]), 'old');
  assert.deepEqual(useScanStore.getState().report, newest);
});

test('重叠频点合并修正载频时，继续选择原信道', () => {
  useScanStore.getState().beginScan('merge');
  useScanStore.getState().updateReport(report([channel(2405000000), channel(2440000000)]), 'merge');
  useScanStore.getState().selectChannel(2440000000);
  useScanStore.getState().updateReport(report([channel(2405000000), channel(2440030000)]), 'merge');
  assert.equal(useScanStore.getState().selectedChannelFrequencyHz, 2440030000);
});

test('快照管理支持保存、载入历史、删除与清空', () => {
  useScanStore.getState().clearSnapshots();
  assert.equal(useScanStore.getState().snapshots.length, 0);

  const r1 = report([channel(2405000000)]);
  useScanStore.getState().setReport(r1);
  useScanStore.getState().saveSnapshot('测试快照 1');

  assert.equal(useScanStore.getState().snapshots.length, 1);
  const snap1 = useScanStore.getState().snapshots[0];
  assert.equal(snap1.name, '测试快照 1');
  assert.equal(snap1.totalChannels, 1);

  const r2 = report([channel(2440000000), channel(2460000000)]);
  useScanStore.getState().setReport(r2);
  useScanStore.getState().saveSnapshot('测试快照 2');
  assert.equal(useScanStore.getState().snapshots.length, 2);

  // 载入快照 1
  useScanStore.getState().loadSnapshot(snap1.id);
  assert.equal(useScanStore.getState().report.channels[0].frequency_hz, 2405000000);
  assert.equal(useScanStore.getState().activeSnapshotId, snap1.id);

  // 删除快照 1
  useScanStore.getState().deleteSnapshot(snap1.id);
  assert.equal(useScanStore.getState().snapshots.length, 1);
  assert.equal(useScanStore.getState().activeSnapshotId, null);

  // 清空
  useScanStore.getState().clearSnapshots();
  assert.equal(useScanStore.getState().snapshots.length, 0);
});
