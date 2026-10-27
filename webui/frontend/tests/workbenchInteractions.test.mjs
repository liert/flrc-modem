import assert from 'node:assert/strict';
import { test } from 'node:test';
import { build } from 'esbuild';
import React from 'react';
import { act, create } from 'react-test-renderer';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';
import { writeFile, unlink } from 'node:fs/promises';

const bundle = await build({
  stdin: {
    contents: `
      export { TransmitWorkbenchPanel } from './src/components/TransmitWorkbenchPanel.tsx';
      export { TemplateLibraryView } from './src/views/TemplateLibraryView.tsx';
      export { useWorkstationStore } from './src/stores/useWorkstationStore.ts';
      export { api } from './src/api/client.ts';
    `,
    resolveDir: process.cwd(),
    loader: 'ts',
  },
  bundle: true,
  write: false,
  format: 'esm',
  platform: 'node',
  external: ['react', 'react-dom', 'react-test-renderer', '@tanstack/react-query', 'lucide-react', 'zustand'],
});

const target = join(process.cwd(), 'node_modules', `.workbench-interactions-${process.pid}.mjs`);
let modules;
try {
  await writeFile(target, bundle.outputFiles[0].text);
  modules = await import(pathToFileURL(target).href);
} finally {
  await unlink(target);
}
const { TransmitWorkbenchPanel, TemplateLibraryView, useWorkstationStore, api } = modules;

function nodeText(node) {
  return typeof node === 'string' ? node : (node.children || []).map(nodeText).join('');
}

test('发射工作台停止失败时显示内联告警且不调用阻塞式 alert()', async () => {
  const previousWorkstation = useWorkstationStore.getState();
  const previousAlert = globalThis.alert;
  const previousStopRun = api.stopRun;
  const previousPreview = api.previewFrame;
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  let alertCalled = false;
  let renderer;
  try {
    globalThis.alert = () => { alertCalled = true; };
    api.previewFrame = async () => ({
      slices: [], envelope: [], inst_freq_khz: [], sample_rate: 4000000,
      airtime_ms: 0.5, total_bits: 128, total_samples: 2000,
    });
    api.stopRun = async () => { throw new Error('设备响应超时'); };
    useWorkstationStore.setState({ ...useWorkstationStore.getInitialState(), transmitRunId: 'active-tx-run' });

    await act(async () => {
      renderer = create(
        React.createElement(QueryClientProvider, { client }, React.createElement(TransmitWorkbenchPanel))
      );
    });

    const stopButton = renderer.root.findAllByType('button').find(b => nodeText(b).includes('停止发射'));
    assert.ok(stopButton);
    await act(async () => {
      await stopButton.props.onClick();
    });

    assert.equal(alertCalled, false);
    assert.equal(useWorkstationStore.getState().transmitRunId, 'active-tx-run');
    const alerts = renderer.root.findAllByProps({ role: 'alert' });
    assert.ok(alerts.some(node => nodeText(node).includes('停止失败: 设备响应超时')));
  } finally {
    if (renderer) await act(async () => renderer.unmount());
    client.clear();
    globalThis.alert = previousAlert;
    api.stopRun = previousStopRun;
    api.previewFrame = previousPreview;
    useWorkstationStore.setState(previousWorkstation, true);
  }
});

test('模板编辑器存在未保存修改时，切换模板或新建前弹出确认对话框并支持继续编辑、保存并继续与放弃修改', async () => {
  const tplA = {
    name: 'physical/a.toml', path: 'templates/physical/a.toml', title: '模板 A', description: '',
    category: 'physical_config', format: 'toml', raw_content: 'sample_rate = 2500000', parsed: {}, applicable_commands: ['demod'],
  };
  const tplB = {
    name: 'physical/b.toml', path: 'templates/physical/b.toml', title: '模板 B', description: '',
    category: 'physical_config', format: 'toml', raw_content: 'sample_rate = 8000000', parsed: {}, applicable_commands: ['demod'],
  };
  const previousWindow = globalThis.window;
  const previousDocument = globalThis.document;
  const previousSaveTemplate = api.saveTemplate;
  const previousGetTemplates = api.getTemplates;
  const savedCalls = [];
  let diskTemplates = [tplA, tplB];
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  client.setQueryData(['templates'], diskTemplates);
  let renderer;
  try {
    globalThis.window = {
      addEventListener() {},
      removeEventListener() {},
      matchMedia: () => ({ matches: false }),
    };
    globalThis.document = { activeElement: null };
    api.getTemplates = async () => diskTemplates;
    api.saveTemplate = async (name, content) => {
      savedCalls.push({ name, content });
      const updated = { ...(name === tplA.name ? tplA : tplB), name, raw_content: content };
      diskTemplates = diskTemplates.map(item => (item.name === name ? updated : item));
      client.setQueryData(['templates'], diskTemplates);
      return updated;
    };
    useWorkstationStore.getState().setSelectedTemplate(tplA);

    await act(async () => {
      renderer = create(
        React.createElement(QueryClientProvider, { client }, React.createElement(TemplateLibraryView))
      );
    });

    const textarea = renderer.root.findByType('textarea');
    await act(async () => {
      textarea.props.onChange({ target: { value: 'sample_rate = 5000000' } });
    });

    // 点击切换到模板 B -> 拦截并弹出确认对话框
    const selectB = renderer.root.findByProps({ 'aria-label': '编辑模板 模板 B' });
    await act(async () => {
      selectB.props.onClick();
    });
    assert.equal(renderer.root.findByType('textarea').props.value, 'sample_rate = 5000000');
    const dialog = renderer.root.findByProps({ role: 'alertdialog' });
    assert.equal(dialog.props['aria-labelledby'], 'template-unsaved-title');

    // 1. 选择“继续编辑” -> 关闭对话框并保持未保存修改
    const keepEditingBtn = dialog.findAllByType('button').find(b => nodeText(b).includes('继续编辑'));
    await act(async () => {
      keepEditingBtn.props.onClick();
    });
    assert.equal(renderer.root.findAllByProps({ role: 'alertdialog' }).length, 0);
    assert.equal(renderer.root.findByType('textarea').props.value, 'sample_rate = 5000000');

    // 2. 点击“新建”同样触发未保存确认拦截
    const newBtn = renderer.root.findAllByType('button').find(b => b.props.title === '新建预设模板');
    await act(async () => {
      newBtn.props.onClick();
    });
    assert.equal(renderer.root.findAllByProps({ role: 'alertdialog' }).length, 1);
    await act(async () => {
      renderer.root.findByProps({ role: 'alertdialog' })
        .findAllByType('button').find(b => nodeText(b).includes('继续编辑')).props.onClick();
    });

    // 3. 再次点击切换模板 B，选择“保存并继续” -> 先保存模板 A 再切换至模板 B
    await act(async () => {
      selectB.props.onClick();
    });
    const saveAndContinueBtn = renderer.root.findByProps({ role: 'alertdialog' })
      .findAllByType('button').find(b => nodeText(b).includes('保存并继续'));
    await act(async () => {
      saveAndContinueBtn.props.onClick();
      await Promise.resolve();
    });
    assert.deepEqual(savedCalls, [{ name: 'physical/a.toml', content: 'sample_rate = 5000000' }]);
    assert.equal(renderer.root.findByType('textarea').props.value, 'sample_rate = 8000000');

    // 4. 在模板 B 中修改后点击切换回模板 A，选择“放弃修改” -> 不触发保存直接切换
    await act(async () => {
      renderer.root.findByType('textarea').props.onChange({ target: { value: 'sample_rate = 10000000' } });
    });
    const selectA = renderer.root.findByProps({ 'aria-label': '编辑模板 模板 A' });
    await act(async () => {
      selectA.props.onClick();
    });
    const discardBtn = renderer.root.findByProps({ role: 'alertdialog' })
      .findAllByType('button').find(b => nodeText(b).includes('放弃修改'));
    await act(async () => {
      discardBtn.props.onClick();
    });
    assert.equal(savedCalls.length, 1);
    assert.equal(renderer.root.findByType('textarea').props.value, 'sample_rate = 5000000');
  } finally {
    if (renderer) await act(async () => renderer.unmount());
    client.clear();
    useWorkstationStore.getState().setSelectedTemplate(null);
    globalThis.window = previousWindow;
    globalThis.document = previousDocument;
    api.saveTemplate = previousSaveTemplate;
    api.getTemplates = previousGetTemplates;
  }
});
