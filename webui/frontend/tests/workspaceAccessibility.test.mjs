import assert from 'node:assert/strict';
import { test } from 'node:test';
import { build } from 'esbuild';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';
import { writeFile, unlink } from 'node:fs/promises';

const result = await build({
  stdin: {
    contents: `
      export { CommandLibraryView } from './src/views/CommandLibraryView.tsx';
      export { TemplateLibraryView } from './src/views/TemplateLibraryView.tsx';
      export { CopyButton } from './src/components/CopyButton.tsx';
      export { ConfirmDialog } from './src/components/ConfirmDialog.tsx';
      export { SyncWorkbenchPanel } from './src/components/SyncWorkbenchPanel.tsx';
      export { useWorkstationStore } from './src/stores/useWorkstationStore.ts';
      export { Header } from './src/components/layout/Header.tsx';
      export { DeviceDiagnosticsDrawer, DeviceDiagnosticsDialog } from './src/components/DeviceDiagnosticsDrawer.tsx';
    `,
    resolveDir: process.cwd(), loader: 'ts',
  },
  bundle: true, write: false, format: 'esm', platform: 'node',
  external: ['react', 'react-dom', '@tanstack/react-query', 'lucide-react', 'zustand'],
});
const target = join(process.cwd(), 'node_modules', `.workspace-accessibility-${process.pid}.mjs`);
let modules;
try {
  await writeFile(target, result.outputFiles[0].text);
  modules = await import(pathToFileURL(target).href);
} finally { await unlink(target); }
const { CommandLibraryView, TemplateLibraryView, CopyButton, ConfirmDialog, SyncWorkbenchPanel, useWorkstationStore, Header, DeviceDiagnosticsDrawer, DeviceDiagnosticsDialog } = modules;

function renderView(View, data) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  for (const [key, value] of Object.entries(data)) client.setQueryData([key], value);
  try { return renderToStaticMarkup(React.createElement(QueryClientProvider, { client }, React.createElement(View))); }
  finally { client.clear(); }
}

test('设备诊断打开和关闭保持当前工作台、任务选择和待用频点', () => {
  const state = useWorkstationStore.getState();
  const previous = { ...state };
  const changes = [];
  const unsubscribe = useWorkstationStore.subscribe((next, prev) => {
    if (next.activeTab !== prev.activeTab) changes.push(next.activeTab);
  });
  try {
    state.setActiveTab('spectrum');
    state.setSelectedRunId('active-capture');
    state.setSpectrumTargetFreqHz(2480000000);
    changes.length = 0;
    state.openDeviceDiagnostics();
    assert.equal(useWorkstationStore.getState().deviceDiagnosticsOpen, true);
    state.closeDeviceDiagnostics();
    assert.equal(useWorkstationStore.getState().deviceDiagnosticsOpen, false);
    assert.equal(useWorkstationStore.getState().activeTab, 'spectrum');
    assert.equal(useWorkstationStore.getState().selectedRunId, 'active-capture');
    assert.equal(useWorkstationStore.getState().spectrumTargetFreqHz, 2480000000);
    assert.deepEqual(changes, []);
    state.openDeviceDiagnostics();
    state.setActiveTab('sync');
    assert.equal(useWorkstationStore.getState().deviceDiagnosticsOpen, false);
    assert.equal(useWorkstationStore.getState().activeTab, 'sync');
  } finally {
    unsubscribe();
    useWorkstationStore.setState(previous, true);
  }
});

test('顶部设备状态使用支持键盘的诊断按钮，抽屉提供名称与关闭入口', () => {
  const header = renderView(Header, { devices: [], runs: [] });
  assert.equal((header.match(/<button[^>]*aria-haspopup="dialog"[^>]*aria-controls="device-diagnostics-drawer"/g) || []).length, 2);
  assert.equal(renderView(DeviceDiagnosticsDrawer, {}), '');
  const drawer = renderView(() => React.createElement(DeviceDiagnosticsDialog, { onClose() {} }), {});
  assert.match(drawer, /<dialog[^>]*aria-labelledby="device-diagnostics-title"[^>]*aria-modal="true"/);
  assert.match(drawer, /<h2[^>]*id="device-diagnostics-title"[^>]*>设备与诊断/);
  assert.match(drawer, /<button[^>]*aria-label="关闭设备与诊断"/);
});

test('同步工作台直接提供实机采集入口，保留实时状态与配置，移除仿真和历史回放', () => {
  const html = renderView(SyncWorkbenchPanel, { templates: [] });
  const controls = html.match(/<section aria-label="实机同步控制"[\s\S]*?<\/section>/)?.[0];
  assert.match(controls, /启动实时同步采集/);
  assert.doesNotMatch(controls, /<button[^>]*\sdisabled="/);
  assert.match(controls, /aria-controls="sync-parameter-drawer"/);
  assert.match(html, /待机（未启动）/);
  assert.match(html, /导出同步报告/);
  assert.match(html, /复制跳频表/);
  assert.match(html, /等效 CLI 命令行预览/);
  assert.doesNotMatch(html, /时序仿真|历史回放|历史重放|连续跳频|时分窗口分解/);
});

test('命令和模板列表提供键盘按钮、可命名搜索和返回入口', () => {
  const command = { id: 'demod', subcommand: 'demod', name: '解调 IQ', description: '离线解调', category: '分析', parameters: [] };
  useWorkstationStore.getState().setSelectedCommandId(command.id);
  const commandsHtml = renderView(CommandLibraryView, { commands: [command], templates: [] });
  assert.match(commandsHtml, /<button[^>]*aria-pressed="true"[^>]*>[\s\S]*?解调 IQ/);
  assert.match(commandsHtml, /aria-label="搜索命令"/);
  assert.match(commandsHtml, /返回命令列表/);

  const template = { name: 'config.toml', path: 'templates/config.toml', title: '测试模板', description: '', category: 'physical_config', format: 'toml', raw_content: 'sample_rate = 8000000', parsed: {}, applicable_commands: ['demod'] };
  useWorkstationStore.getState().setSelectedTemplate(template);
  try {
    const html = renderView(TemplateLibraryView, { templates: [template] });
    assert.match(html, /<button[^>]*aria-label="编辑模板 测试模板"/);
    assert.match(html, /<button[^>]*aria-label="删除模板 测试模板"/);
    assert.match(html, /aria-label="搜索模板"/);
    assert.match(html, /<textarea aria-label="模板源码 config.toml"/);
    assert.match(html, /返回模板列表/);
  } finally { useWorkstationStore.getState().setSelectedTemplate(null); }
});

test('命令加载中与空列表显示不同提示，空文本无法复制', () => {
  assert.match(renderView(CommandLibraryView, {}), /role="status"[^>]*>正在加载命令…/);
  const empty = renderView(CommandLibraryView, { commands: [], templates: [] });
  assert.match(empty, /没有匹配的命令/);
  assert.doesNotMatch(empty, /正在加载命令/);
  const copy = renderToStaticMarkup(React.createElement(CopyButton, { text: '' }));
  assert.match(copy, /<button[^>]*disabled=""/);
  assert.doesNotMatch(copy, /已复制/);
});

test('任务删除使用带名称和说明的原生模态对话框', () => {
  const html = renderToStaticMarkup(React.createElement(ConfirmDialog, { titleId: 'title', descriptionId: 'description', pending: true, onCancel() {} },
    React.createElement('h2', { id: 'title' }, '删除任务？'),
    React.createElement('p', { id: 'description' }, '删除记录无法恢复'),
    React.createElement('button', { 'data-dialog-cancel': true, disabled: true }, '取消')));
  assert.match(html, /<dialog[^>]*role="alertdialog"/);
  assert.match(html, /aria-labelledby="title" aria-describedby="description" aria-modal="true" aria-busy="true"/);
  assert.doesNotMatch(html, /autofocus/);
});
