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
    export { ProtocolAnalysisView } from './src/views/ProtocolAnalysisView.tsx';
    export { useWorkstationStore } from './src/stores/useWorkstationStore.ts';
    export { duplexProtocolDraft } from './src/utils/protocolSources.ts';
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
    if (draft.sync_probe) {
      assert.deepEqual(draft.sync_probe, before.plan.sync_probe);
      draft.sync_probe.observe_ms = 123;
      draft.sync_probe.frequency_offsets_hz.push(500000);
    }
    assert.deepEqual(template, before);
  }
  for (const invalid of [null, [], {}, { plan: [] }, { steps: 'bad' }, { periodic: [], steps: [] }]) {
    assert.throws(() => parseDuplexPlan(invalid));
  }
  assert.deepEqual(parseDuplexPlan({ steps: [{ id: 'one', payload_hex: '010203040506' }] }), {
    plan_id: 'draft', periodic: [], steps: [{ id: 'one', payload_hex: '010203040506' }], cycles: 1,
  });
});

test('观察阶段保持编排锁定，显示同步字候选和采集缺口并区分未捕获与切换失败', () => {
  const draft = { plan_id: 'draft', periodic: [], steps: [], cycles: 1 };
  const definition = { plan_id: 'probe-plan', periodic: [], steps: [{ id: 'switch', payload_hex: '010203040506',
    delay_ms: 0, wait_reply: false, on_failure: 'stop' }], cycles: 1,
    sync_probe: { observe_ms: 5000, min_frames: 3, frequency_offsets_hz: [0] } };
  const session = { plan: { plan_id: definition.plan_id, status: 'observing', executed: 2, succeeded: 2, failed: 0, skipped: 0 },
    plan_definition: definition, probe: { plan_id: definition.plan_id, status: 'running', center_frequency_hz: 2471660000,
      sample_rate: 2500000, frequencies_hz: [2471660000], min_frames: 3, dropped_windows: 2, stale_windows: 1,
      capture_degraded: true, candidates: [{ sync_word_hex: '12345678', valid_frames: 3, confirmed: true,
        first_hw_time: 100, last_hw_time: 101, frequencies_hz: [2471660000] }] } };
  const html = render(modules.DuplexPlanPanel, { runId: 'run', ready: true, session, draft, setDraft() {},
    selectedItem: '', onSelectItem() {}, onCompose() {} });
  assert.match(html, /指令已执行 · 继续监听/);
  assert.match(html, /<button disabled=""[^>]*>[\s\S]*?开始编排<\/button>/);
  assert.match(html, /固定频段同步字探测/);
  assert.match(html, /12345678/);
  assert.match(html, /候选达标/);
  assert.match(html, /采集或处理存在缺口/);
  assert.match(html, /未捕获不代表切换失败/);
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

test('同步收发 TX/RX 使用实际保存载荷进入协议解析，保留轮次且不虚构超时回复', () => {
  const run = { id: 'session-history', attempt: 2, command_args: { session: true, protocol: 'dk32s' } };
  const result = { request_id: 'request-17', status: 'reply_matched', timestamp: '2026-10-23T10:00:00Z',
    tx_completed: true, tx_payload_hex: '0a0100000081440054112a', rx_reply: { payload_hex: '050045005499' } };
  const tx = modules.duplexProtocolDraft(result, 'TX', run);
  const rx = modules.duplexProtocolDraft(result, 'RX', run);
  assert.equal(tx.payloadHex, result.tx_payload_hex);
  assert.equal(rx.payloadHex, result.rx_reply.payload_hex);
  assert.deepEqual(rx.origin, { runId: run.id, attempt: 2 });
  assert.match(tx.source, /session-history · 第 2 次执行 · request-17 · TX/);
  assert.match(rx.source, /request-17 · RX/);
  assert.equal(rx.protocol, 'dk32s');
  assert.equal(modules.duplexProtocolDraft({ ...result, rx_reply: undefined, status: 'reply_timeout' }, 'RX', run), null);
  assert.equal(modules.duplexProtocolDraft({ ...result, tx_payload_hex: '' }, 'TX', run), null);
  assert.match(modules.duplexProtocolDraft({ ...result, tx_completed: false }, 'TX', run).source, /未确认完成/);
  modules.useWorkstationStore.getState().openProtocol(rx);
  assert.equal(modules.useWorkstationStore.getState().activeTab, 'protocols');
  assert.deepEqual(modules.useWorkstationStore.getState().protocolDraft, rx);
  const html = render(modules.DuplexResultsPanel, { run, results: [result] });
  assert.match(html, /解析 TX/);
  assert.match(html, /解析 RX/);
  assert.match(html, /无需 JSONL 文件/);
  const timeout = render(modules.DuplexResultsPanel, { run, results: [{ ...result, rx_reply: undefined, status: 'reply_timeout' }] });
  assert.match(timeout, /解析 TX/);
  assert.doesNotMatch(timeout, /解析 RX/);
  modules.useWorkstationStore.setState({ protocolDraft: { protocol: 'dk32s', payloadHex: '', source: '手动输入' } });
});

test('协议面板从数据库载入同步交互，按历史轮次选择且不要求 JSONL', () => {
  const latest = { id: 'session-source', attempt: 3, command_id: 'duplex', status: 'RUNNING',
    command_args: { session: true }, created_at: '2026-10-23T10:00:00Z' };
  const history = { ...latest, attempt: 2, status: 'SUCCEEDED' };
  const draft = modules.duplexProtocolDraft({ request_id: 'history-request', tx_completed: true,
    tx_payload_hex: '112233', rx_reply: { payload_hex: '445566' } }, 'RX', history);
  modules.useWorkstationStore.setState({ protocolDraft: draft });
  // React SSR reads Zustand's initial snapshot rather than the live client state.
  const initialState = modules.useWorkstationStore.getInitialState();
  const originalDraft = initialState.protocolDraft;
  initialState.protocolDraft = draft;
  try {
    const html = render(modules.ProtocolAnalysisView, {}, [
      [['protocols'], [{ id: 'dk32s', title: 'DK32S', description: '协议' }]],
      [['runs', 100, 0], [latest]],
      [['runAttempts', latest.id], [latest, history]],
      [['duplexSession', latest.id, 2], { results: [{ request_id: 'history-request', status: 'reply_matched',
        tx_completed: true, tx_payload_hex: '112233', rx_reply: { payload_hex: '445566' } }] }],
      [['duplexSession', latest.id, 3], { results: [{ request_id: 'latest-request', tx_payload_hex: 'aabbcc' }] }],
    ]);
    assert.match(html, /同步收发交互报文（最近 50 条交互）/);
    assert.match(html, /<option value="2" selected="">第 2 次执行/);
    assert.match(html, /TX · history-request/);
    assert.match(html, /RX · history-request/);
    assert.doesNotMatch(html, /latest-request|此任务没有可读取的解调 JSONL/);
    assert.match(html, /交互记录来自任务数据库，无需 JSONL/);
    assert.match(html, /aria-label="报文解析准备区"/);
    assert.match(html, /id="pi-tab-task" role="tab" aria-selected="true" aria-controls="pi-task-panel" tabindex="0"/);
    assert.match(html, /id="pi-hex-panel" role="tabpanel" aria-labelledby="pi-tab-hex" hidden=""/);
    assert.match(html, /aria-expanded="true" aria-controls="pi-preparation"/);
    assert.match(html, /445566/);
  } finally {
    initialState.protocolDraft = originalDraft;
    modules.useWorkstationStore.setState({ protocolDraft: { protocol: 'dk32s', payloadHex: '', source: '手动输入' } });
  }
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
