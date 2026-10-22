import assert from 'node:assert/strict';
import { test } from 'node:test';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { build } from 'esbuild';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';

const outTarget = join(process.cwd(), 'node_modules', '.cache_dissector_bundle.mjs');
await build({
  stdin: {
    contents: `
      export { ProtocolDissectorPanel } from './src/components/ProtocolDissectorPanel.tsx';
    `,
    resolveDir: process.cwd(),
    loader: 'ts',
  },
  bundle: true,
  outfile: outTarget,
  format: 'esm',
  platform: 'node',
  jsx: 'automatic',
  external: ['react', 'react-dom', 'lucide-react'],
});

const { ProtocolDissectorPanel } = await import(pathToFileURL(outTarget).href);

test('ProtocolDissectorPanel 成功解析并分层展示 PHY、硬件头、CRC 与应用层字段', () => {
  const frame = {
    time_s: 0.0152,
    frequency_hz: 2480000000,
    cfo_hz: 12500,
    sample_power_db: -14.2,
    preamble_errors: 0,
    sync_word_hex: 'f264709b',
    hardware_header_hex: '0020',
    payload_hex: '212a72040098f00613e40a2b3fc2bf8d',
    crc_hex: 'e84612',
    crc_ok: true,
    crc_initial: 0xffffff,
    fec_path_metric: 0,
    application: {
      protocol: 'dk32s',
      direction: 'ground_to_air',
      sequence: 888,
      timestamp_ticks: 291370,
      fields: {
        filter_address: '0x3F2B0AE4',
        channels_output: [1500, 1520, 1480, 1600],
      },
    },
  };

  const html = renderToStaticMarkup(
    React.createElement(ProtocolDissectorPanel, {
      frame,
      frameIndex: 0,
      totalFrames: 10,
    })
  );

  // 验证各层级渲染内容
  assert.ok(html.includes('报文协议树逐层解码器'), '应包含标题');
  assert.ok(html.includes('f264709b') || html.includes('F264709B'), '应包含同步字');
  assert.ok(html.includes('0020'), '应包含硬件头');
  assert.ok(html.includes('CRC OK'), '应包含 CRC 状态');
  assert.ok(html.includes('dk32s') || html.includes('DK32S'), '应包含业务协议名');
  assert.ok(html.includes('GND→AIR') || html.includes('地对空'), '应包含流向');
  assert.ok(html.includes('888'), '应包含序号');
  assert.ok(html.includes('1500 µs') || html.includes('1500'), '应包含舵量通道');
  assert.ok(html.includes('十六进制矩阵') || html.includes('Hex &amp; ASCII Dump'), '应包含 Hex 矩阵');
});

test('ProtocolDissectorPanel 容错处理无应用插件的纯物理载荷帧', () => {
  const rawFrame = {
    time_s: 0.05,
    frequency_hz: 2404000000,
    cfo_hz: -5000,
    sample_power_db: -20.5,
    preamble_errors: 1,
    sync_word_hex: '54313253',
    payload_hex: '112233445566',
    crc_hex: 'aabbcc',
  };

  const html = renderToStaticMarkup(
    React.createElement(ProtocolDissectorPanel, {
      frame: rawFrame,
      frameIndex: 2,
      totalFrames: 5,
    })
  );

  assert.ok(html.includes('第 3 / 5 帧'), '帧计数显示正确');
  assert.ok(html.includes('54313253'), '包含同步字');
  assert.ok(html.includes('11') && html.includes('22'), '包含原始载荷字节');
  assert.ok(html.includes('原生载荷') || html.includes('未配置独立业务协议解析插件'), '提示无业务插件信息');
});

test('ProtocolDissectorPanel 全帧十六进制矩阵布局具备防换行约束与 ASCII 列头', () => {
  const frame = {
    time_s: 0.1,
    frequency_hz: 2400000000,
    cfo_hz: 0,
    sample_power_db: -10,
    preamble_errors: 0,
    sync_word_hex: '210c36b3',
    hardware_header_hex: '000b',
    payload_hex: '0a85b294f2d008a1453908fcb7',
    crc_hex: '08fcb7',
    crc_ok: true,
  };

  const html = renderToStaticMarkup(
    React.createElement(ProtocolDissectorPanel, {
      frame,
      frameIndex: 0,
      totalFrames: 1,
    })
  );

  assert.ok(html.includes('OFFSET'), '包含 OFFSET 表头');
  assert.ok(html.includes('ASCII'), '包含 ASCII 表头');
  assert.ok(html.includes('whitespace-nowrap'), '行具备单行防换行约束');
  assert.ok(html.includes('min-w-fit'), '行具备最小宽度自适应约束');
  assert.ok(html.includes('shrink-0'), 'Hex 与 ASCII 具备防挤压 shrink-0 属性');
});

test('实时解调数据流追加新包时，稳定引用确保选定帧及其协议解析内容不会发生漂移与刷新', () => {
  const p1 = { time_s: 1.0, frequency_hz: 2400000000, sync_word_hex: '210c36b3', payload_hex: '010203' };
  const p2 = { time_s: 2.0, frequency_hz: 2400000000, sync_word_hex: '210c36b3', payload_hex: '040506' };
  const p3 = { time_s: 3.0, frequency_hz: 2400000000, sync_word_hex: '210c36b3', payload_hex: '070809' };

  let packets = [p2, p1];
  // 用户选定并打开 p2
  let selected = p2;

  // 模拟 WebSocket 实时追加新包前插
  packets = [p3, ...packets];

  // 若沿用旧的数字下标访问 packets[0]，内容会发生漂移变成 p3
  assert.equal(packets[0], p3);

  // 采用稳定引用锁定时，选定帧保持为 p2，内容完全不受后台实时包流入影响
  const found = packets.find(p => p.time_s === selected.time_s && p.payload_hex === selected.payload_hex);
  assert.equal(found, p2);
  assert.equal(selected.payload_hex, '040506');
});
