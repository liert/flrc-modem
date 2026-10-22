import assert from 'node:assert/strict';
import { test } from 'node:test';
import { build } from 'esbuild';

const result = await build({ entryPoints: ['src/utils/outputDock.ts'], bundle: true, write: false, format: 'esm', platform: 'node' });
const { outputDockBounds, outputDockHeight, placeOutputDock } = await import(`data:text/javascript;base64,${Buffer.from(result.outputFiles[0].text).toString('base64')}`);

test('自由移动保持松手位置，只有靠近底边才吸附', () => {
  assert.deepEqual(placeOutputDock(173, 320, 800), { top: 173, docked: false });
  assert.deepEqual(placeOutputDock(459, 320, 800), { top: 459, docked: false });
  assert.deepEqual(placeOutputDock(460, 320, 800), { top: 480, docked: true });
  assert.deepEqual(placeOutputDock(475, 320, 800), { top: 480, docked: true });
});

test('拖动过程中不提前吸附，松手后才判断底部距离', () => {
  assert.deepEqual(placeOutputDock(475, 320, 800, false), { top: 475, docked: false });
  assert.deepEqual(placeOutputDock(475, 320, 800), { top: 480, docked: true });
});

test('普通高度不跳档，保存的比例恢复到用户选定高度', () => {
  for (const height of [221, 317, 373, 499, 681]) assert.ok(Math.abs(outputDockHeight(height / 800, 800) - height) < .001);
  assert.equal(outputDockHeight(0, 800), outputDockBounds(800).collapsed);
});

test('面板不越出工作区，缩小窗口后仍能操作', () => {
  assert.deepEqual(placeOutputDock(-90, 320, 800), { top: 0, docked: false });
  assert.deepEqual(placeOutputDock(1000, 320, 800), { top: 480, docked: true });
  for (const available of [0, 40, 160, 400, 1000]) {
    const bounds = outputDockBounds(available);
    const height = outputDockHeight(.9, available);
    const location = placeOutputDock(1000, height, available);
    assert.ok(height >= 0 && height <= available);
    assert.ok(location.top >= 0 && location.top + height <= available);
    assert.ok(bounds.collapsed <= bounds.minimum && bounds.minimum <= bounds.maximum);
  }
});
