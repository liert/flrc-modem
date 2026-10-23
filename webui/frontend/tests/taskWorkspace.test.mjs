import assert from 'node:assert/strict';
import { test } from 'node:test';
import { build } from 'esbuild';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';
import { readFile, readdir, writeFile, unlink } from 'node:fs/promises';

const bundle = await build({
  stdin: { contents: `
    export { DuplexSessionPanel } from './src/components/DuplexSessionPanel.tsx';
    export { DuplexPlanPanel } from './src/components/DuplexPlanPanel.tsx';
    export { DuplexResultsPanel } from './src/components/DuplexResultsPanel.tsx';
    export { TaskEventsPanel } from './src/components/TaskEventsPanel.tsx';
    export * from './src/utils/duplexDraft.ts';
    export { argvPreview } from './src/utils/runtime.ts';
  `, resolveDir: process.cwd(), loader: 'ts' },
  bundle: true, write: false, format: 'esm', platform: 'node',
  external: ['react', 'react-dom', '@tanstack/react-query', 'lucide-react'],
});
const path = join(process.cwd(), 'node_modules', `.task-workspace-${process.pid}.mjs`);
let modules;
try { await writeFile(path, bundle.outputFiles[0].text); modules = await import(pathToFileURL(path).href); }
finally { await unlink(path); }
const { parseDuplexPayload, parseDuplexPlan, replaceDuplexPayload, duplexPayloadText, argvPreview } = modules;

test('编排文件和原有导出格式均可载入，编辑草稿不会修改共享模板缓存', async () => {
  const folder = join(process.cwd(), '../../templates/plans');
  for (const name of (await readdir(folder)).filter(name => name.endsWith('.json'))) {
    const template = JSON.parse(await readFile(join(folder, name), 'utf8'));
    const before = structuredClone(template);
    const draft = parseDuplexPlan(template);
    assert.deepEqual(draft, parseDuplexPlan(template.plan));
    assert.ok(draft.periodic.length || draft.steps.length);
    const item = draft.periodic[0] || draft.steps[0];
    if (item.message) item.message.command = 'changed'; else item.payload_hex = 'changed';
    assert.deepEqual(template, before);
  }
  for (const invalid of [null, [], {}, { plan: [] }, { steps: 'bad' }, { periodic: [], steps: [] }]) {
    assert.throws(() => parseDuplexPlan(invalid));
  }
  assert.deepEqual(parseDuplexPlan({ steps: [{ id: 'one', payload_hex: '010203040506' }] }), {
    plan_id: 'draft', periodic: [], steps: [{ id: 'one', payload_hex: '010203040506' }], cycles: 1,
  });
});

function render(Component, props, cache = []) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  for (const [key, value] of cache) client.setQueryData(key, value);
  try { return renderToStaticMarkup(React.createElement(QueryClientProvider, { client }, React.createElement(Component, props))); }
  finally { client.clear(); }
}

test('切换报文格式只替换载荷，保留周期、等待回复与身份，避免同时提交两种载荷', () => {
  const original = { id: 'rc', interval_ms: 500, count: 4, wait_reply: false, message: { command: '0x11' } };
  const hex = replaceDuplexPayload(original, parseDuplexPayload('payload_hex', '01 02\n03\t04 05 06'));
  assert.deepEqual(hex, { id: 'rc', interval_ms: 500, count: 4, wait_reply: false, payload_hex: '010203040506' });
  assert.equal('message' in hex, false);
  const json = replaceDuplexPayload(hex, parseDuplexPayload('message', '{"command":"0x23"}'));
  assert.equal('payload_hex' in json, false);
  assert.equal(json.message.command, '0x23');
  assert.deepEqual(original.message, { command: '0x11' });
  assert.equal(duplexPayloadText(json), '{\n  "command": "0x23"\n}');
});

test('编辑器拒绝空值、非对象 JSON、半字节和越界载荷，保留合法边界', () => {
  for (const value of ['', '{} broken', '[]', 'null', 'true', '1', '"text"']) assert.throws(() => parseDuplexPayload('message', value));
  for (const value of ['', '00'.repeat(5), '00'.repeat(128), '123456789ab', '00'.repeat(5) + 'zz']) assert.throws(() => parseDuplexPayload('payload_hex', value));
  for (const bytes of [6, 127]) assert.equal(parseDuplexPayload('payload_hex', 'aB'.repeat(bytes)).payload_hex.length, bytes * 2);
});

test('运行中显示实际执行配置并锁定编辑，保留单独停止编排入口', () => {
  const draft = { plan_id: 'draft', periodic: [], steps: [], cycles: 1 };
  const session = {
    plan: { plan_id: 'active', status: 'running', executed: 7, succeeded: 6, failed: 1, skipped: 2 },
    plan_definition: { plan_id: 'active', periodic: [{ id: 'rc', interval_ms: 500, count: 0, wait_reply: false, message: { command: '0x11' } }], steps: [], cycles: 1 },
  };
  const html = render(modules.DuplexPlanPanel, { runId: 'run', ready: true, session, draft, setDraft() {}, selectedItem: 'rc', onSelectItem() {}, onCompose() {} });
  assert.match(html, /正在查看本次执行配置/);
  assert.match(html, /0x11/);
  assert.match(html, /<fieldset disabled=""/);
  assert.match(html, /<textarea[^>]*disabled=""/);
  assert.match(html, /<button disabled=""[^>]*>[\s\S]*?开始编排<\/button>/);
  const stop = html.match(/<button\b[^>]*>[\s\S]*?<\/button>/g).find(button => button.includes('停止编排'));
  assert.ok(stop);
  assert.doesNotMatch(stop, /disabled=""/);
  assert.match(html, /aria-label="编排运行统计"/);
});

test('运行配置的折叠卡片包含周期、报文编辑、复制和步骤双向排序', () => {
  const draft = { plan_id: 'draft', periodic: [], steps: [1, 2].map(index => ({ id: `s${index}`, message: { command: '0x23' }, delay_ms: 100, wait_reply: true, on_failure: 'stop' })), cycles: 2 };
  const html = render(modules.DuplexPlanPanel, { runId: 'run', ready: true, draft, setDraft() {}, selectedItem: '', onSelectItem() {}, onCompose() {} });
  assert.match(html, /aria-label="展开编辑步骤 1"/);
  assert.match(html, /aria-label="步骤 1报文"/);
  assert.match(html, /aria-label="复制步骤 1"/);
  assert.match(html, /aria-label="上移步骤 1"[^>]*disabled=""/);
  assert.match(html, /aria-label="下移步骤 2"[^>]*disabled=""/);
});

test('会话按工作区分离操作，隐藏编排区仍挂载，错误不随折叠内容隐藏', () => {
  const run = { id: 'session', attempt: 1, status: 'RUNNING', command_args: { session: true } };
  const state = { status: { state: 'locked', error: 'receiver failed' }, ready: { sample_rate: 2500000 }, results: [] };
  const html = render(modules.DuplexSessionPanel, { run }, [[['duplexSession', run.id, 1], state], [['templates'], []]]);
  assert.match(html, /aria-label="同步会话工作区"/);
  assert.match(html, /id="[^"]*-plan" hidden=""/);
  assert.match(html, /id="[^"]*-results" hidden=""/);
  assert.match(html, /aria-expanded="true"[^>]*>[\s\S]*?收起会话/);
  assert.match(html, /aria-label="放大会话工作区"/);
  assert.ok(html.indexOf('receiver failed') < html.indexOf('同步会话工作区'));
});

test('记录摘要限制首屏数量，完整报文和请求编号保留在可展开详情', () => {
  const results = Array.from({ length: 50 }, (_, index) => ({ request_id: `req-${index}-unique`, status: 'tx_completed', tx_completed: true, tx_payload_hex: '010203040506' }));
  const html = render(modules.DuplexResultsPanel, { results });
  assert.equal((html.match(/req-\d+-unique/g) || []).length, 8);
  assert.match(html, /最近 50 条交互/);
  assert.match(html, /显示全部记录/);
});

test('事件记录按批次渲染并折叠 JSON，重复序号不丢失事件', () => {
  const events = Array.from({ length: 120 }, (_, index) => ({ seq: 0, type: 'duplex.result', payload: { item: index } }));
  const html = render(modules.TaskEventsPanel, { events, loading: false });
  assert.equal((html.match(/<details/g) || []).length, 50);
  assert.match(html, /120 \/ 120 条/);
  assert.match(html, /再显示 50 条事件/);
  assert.match(html, /aria-label="搜索结构化事件"/);
  assert.ok(html.indexOf('&quot;item&quot;: 119') < html.indexOf('&quot;item&quot;: 118'));
});

test('执行快照中的可复制命令保护 PowerShell 特殊字符和带空格的解释器路径', () => {
  assert.equal(argvPreview(['C:\\Program Files\\Python\\python.exe', '-m', 'flrc_modem', "two's $HOME; x"]), "& 'C:\\Program Files\\Python\\python.exe' -m flrc_modem 'two''s $HOME; x'");
  assert.equal(argvPreview([]), '');
  assert.equal(argvPreview(['python', '-m', 'flrc_modem']), 'python -m flrc_modem');
});
