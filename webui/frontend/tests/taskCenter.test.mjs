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
      export { TaskCenterView } from './src/views/TaskCenterView.tsx';
      export { useWorkstationStore } from './src/stores/useWorkstationStore.ts';
      export { api } from './src/api/client.ts';
      export { ExecutionHistory } from './src/components/ExecutionHistory.tsx';
    `,
    resolveDir: process.cwd(), loader: 'ts',
  },
  bundle: true, write: false, format: 'esm', platform: 'node',
  external: ['react', 'react-dom', '@tanstack/react-query', 'lucide-react', 'zustand'],
});
const target = join(process.cwd(), 'node_modules', `.task-center-test-${process.pid}.mjs`);
let TaskCenterView, useWorkstationStore, api, ExecutionHistory;
try {
  await writeFile(target, result.outputFiles[0].text);
  ({ TaskCenterView, useWorkstationStore, api, ExecutionHistory } = await import(pathToFileURL(target).href));
} finally { await unlink(target); }

test('任务中心仅对已结束任务提供重新执行，并说明同名输出覆盖', () => {
  for (const status of ['SUCCEEDED', 'FAILED', 'CANCELLED', 'RUNNING', 'QUEUED']) {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const run = { id: 'selected', command_id: 'demod', command_args: {}, cli_argv: [], status, created_at: '2026-10-21T00:00:00Z' };
    useWorkstationStore.getState().setSelectedRunId(run.id);
    client.setQueryData(['runs', 51, 0, 'ALL', ''], [run]);
    client.setQueryData(['run', run.id], run);
    try {
      const html = renderToStaticMarkup(React.createElement(QueryClientProvider, { client }, React.createElement(TaskCenterView)));
      assert.match(html, /<button[^>]*aria-label="查看任务 [^"]+"/);
      assert.match(html, /aria-label="搜索任务"/);
      assert.match(html, /tabindex="0" role="region" aria-label="任务日志，可滚动查看"/);
      assert.match(html, /返回任务列表/);
      if (['SUCCEEDED', 'FAILED', 'CANCELLED'].includes(status)) {
        assert.match(html, /<span>重新执行<\/span>/);
        assert.match(html, /同名结果会被覆盖/);
      } else {
        assert.doesNotMatch(html, /<span>重新执行<\/span>/);
        assert.match(html, /终止任务/);
      }
    } finally { client.clear(); }
  }
  useWorkstationStore.getState().setSelectedRunId(null);
});

test('重新执行 API 保持任务 ID 并返回新执行轮次，冲突显示后端原因', async () => {
  const original = globalThis.fetch;
  try {
    globalThis.fetch = async (url, options) => {
      assert.equal(url, '/api/v1/runs/history%2Fid/rerun');
      assert.equal(options.method, 'POST');
      return new Response(JSON.stringify({ id: 'history/id', attempt: 2, status: 'QUEUED' }));
    };
    const run = await api.rerunRun('history/id');
    assert.equal(run.id, 'history/id');
    assert.equal(run.attempt, 2);
    globalThis.fetch = async () => new Response(JSON.stringify({ detail: '设备已被占用' }), { status: 409 });
    await assert.rejects(api.rerunRun('history/id'), /设备已被占用/);
  } finally { globalThis.fetch = original; }
});

test('同一任务的执行历史以时间线呈现，并明确选中轮次及错误', () => {
  const executions = [
    { id: 'same', attempt: 3, status: 'RUNNING', created_at: '2026-10-21T00:00:00Z', attempt_created_at: '2026-10-21T02:00:00Z' },
    { id: 'same', attempt: 2, status: 'FAILED', created_at: '2026-10-21T00:00:00Z', error: 'previous failure', exit_code: 2 },
    { id: 'same', attempt: 1, status: 'SUCCEEDED', created_at: '2026-10-21T00:00:00Z', exit_code: 0 },
  ];
  const html = renderToStaticMarkup(React.createElement(ExecutionHistory, { executions, selectedAttempt: 2, onSelect() {} }));
  assert.match(html, /aria-label="执行时间线"/);
  assert.equal((html.match(/aria-label="查看第/g) || []).length, 3);
  assert.match(html, /aria-label="查看第 2 次执行" aria-pressed="true"/);
  assert.match(html, /正在查看第 2 次执行/);
  assert.match(html, /previous failure/);
  assert.match(html, /第 3 次.*最新/);
});
