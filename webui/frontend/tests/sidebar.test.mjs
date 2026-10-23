import assert from 'node:assert/strict';
import { test } from 'node:test';
import { build } from 'esbuild';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';

// Mock localStorage for node environment
const storage = new Map();
globalThis.localStorage = {
  getItem: (key) => storage.get(key) ?? null,
  setItem: (key, val) => storage.set(key, String(val)),
  removeItem: (key) => storage.delete(key),
  clear: () => storage.clear(),
};
globalThis.window = { localStorage: globalThis.localStorage };

const outTarget = join(process.cwd(), 'node_modules', '.cache_sidebar_bundle.mjs');
await build({
  stdin: {
    contents: `
      export { useWorkstationStore } from './src/stores/useWorkstationStore.ts';
      export { Sidebar } from './src/components/layout/Sidebar.tsx';
    `,
    resolveDir: process.cwd(),
    loader: 'ts',
  },
  bundle: true,
  outfile: outTarget,
  format: 'esm',
  platform: 'node',
  jsx: 'automatic',
  external: ['react', 'react-dom', 'lucide-react', 'zustand'],
});

const { useWorkstationStore, Sidebar } = await import(pathToFileURL(outTarget).href);

test('useWorkstationStore 管理侧边栏折叠与展开状态并持久化', () => {
  storage.clear();

  // 1. 默认展开状态 (sidebarCollapsed: false)
  useWorkstationStore.getState().setSidebarCollapsed(false);
  assert.equal(useWorkstationStore.getState().sidebarCollapsed, false);

  // 2. 触发 toggleSidebar 切换至折叠
  useWorkstationStore.getState().toggleSidebar();
  assert.equal(useWorkstationStore.getState().sidebarCollapsed, true);
  assert.equal(storage.get('flrc_sidebar_collapsed'), 'true');

  // 3. 再次触发 toggleSidebar 切换回展开
  useWorkstationStore.getState().toggleSidebar();
  assert.equal(useWorkstationStore.getState().sidebarCollapsed, false);
  assert.equal(storage.get('flrc_sidebar_collapsed'), 'false');

  // 4. 显式设置 setSidebarCollapsed
  useWorkstationStore.getState().setSidebarCollapsed(true);
  assert.equal(useWorkstationStore.getState().sidebarCollapsed, true);
  assert.equal(storage.get('flrc_sidebar_collapsed'), 'true');

  useWorkstationStore.getState().setSidebarCollapsed(false);
  assert.equal(useWorkstationStore.getState().sidebarCollapsed, false);
  assert.equal(storage.get('flrc_sidebar_collapsed'), 'false');
});

test('Sidebar 组件在展开和折叠模式下正确呈现结构与 Tooltip', () => {
  // 1. 展开状态 (默认未折叠)
  useWorkstationStore.getState().setSidebarCollapsed(false);
  const expandedHtml = renderToStaticMarkup(React.createElement(Sidebar, { collapsed: false }));
  assert.match(expandedHtml, /w-56/);
  assert.match(expandedHtml, /总览仪表盘/);
  assert.match(expandedHtml, /命令控制库/);
  assert.match(expandedHtml, /模板预设库/);
  assert.match(expandedHtml, /aria-label="协议解析"/);
  assert.doesNotMatch(expandedHtml, /SDR 硬件监控|Hardware &amp; Health/);
  assert.match(expandedHtml, /PHY MODEM/);
  assert.match(expandedHtml, /折叠侧边栏/);

  // 2. 折叠状态
  useWorkstationStore.getState().setSidebarCollapsed(true);
  const collapsedHtml = renderToStaticMarkup(React.createElement(Sidebar, { collapsed: true }));
  assert.match(collapsedHtml, /w-16/);
  assert.match(collapsedHtml, /role="tooltip"/);
  assert.match(collapsedHtml, /展开侧边栏/);
  assert.match(collapsedHtml, /FLRC/);
  assert.match(collapsedHtml, /SDR/);
  assert.match(collapsedHtml, /<button[^>]*aria-label="任务执行中心"/);
  assert.equal((collapsedHtml.match(/aria-current="page"/g) || []).length, 1);
});
