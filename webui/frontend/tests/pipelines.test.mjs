import assert from 'node:assert/strict';
import { test } from 'node:test';
import { build } from 'esbuild';

const bundle = await build({
  stdin: {
    resolveDir: process.cwd(),
    contents: `
      export { useWorkstationStore } from './src/stores/useWorkstationStore';
    `,
  },
  bundle: true,
  write: false,
  format: 'esm',
  platform: 'node',
});

const { useWorkstationStore } = await import(
  `data:text/javascript;base64,${Buffer.from(bundle.outputFiles[0].text).toString('base64')}`
);

test('Pipeline 状态管理支持选定、切换与回放关联', () => {
  const store = useWorkstationStore;
  assert.equal(typeof store.getState().selectedPipelineId, 'string');
  store.getState().setSelectedPipelineId('pipe_scan');
  assert.equal(store.getState().selectedPipelineId, 'pipe_scan');
  store.getState().setSelectedPipelineId('pipe_duplex');
  assert.equal(store.getState().selectedPipelineId, 'pipe_duplex');
});
