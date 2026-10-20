import assert from 'node:assert/strict';
import { test } from 'node:test';
import { build } from 'esbuild';
import { QueryClient } from '@tanstack/react-query';

async function loadModule(path) {
  const result = await build({ entryPoints: [path], bundle: true, write: false, format: 'esm', platform: 'node' });
  return import(`data:text/javascript;base64,${Buffer.from(result.outputFiles[0].text).toString('base64')}`);
}

const { mergeLogs, cliPreview } = await loadModule('src/utils/runtime.ts');
const { useWebSocketStore } = await loadModule('src/stores/useWebSocketStore.ts');
const { analysisFiles, fileKey, refreshForEvent, fileReadable, fileStateLabel } = await loadModule('src/utils/analysis.ts');
const { runTitle, runTime, runOption, runDeletable, deletedRunIds, purgeRunCaches } = await loadModule('src/utils/runs.ts');
const { commandDraftParameters, namedOutputPath, taskFileStem } = await loadModule('src/utils/commandDraft.ts');
const { commandGroups, commandGroupMatches } = await loadModule('src/utils/commandGroups.ts');

test('报文收发只合并同步收发和盲发，保留 sync 独立入口并可从两种模式搜索', () => {
  const sync = { id: 'sync', name: '同步分析', description: '监听同步', category: '同步与协议分析', subcommand: 'sync' };
  const group = { group_id: 'message_io', group_name: '报文收发', group_description: '按模式收发报文', category: '调制与发射' };
  const blind = { ...group, id: 'transmit', name: '盲发（HackRF）', description: '不等待回复', subcommand: 'transmit', mode_order: 1 };
  const duplex = { ...group, id: 'duplex', name: '同步时隙收发（USRP）', description: '锁定时隙并等待回复', subcommand: 'duplex', mode_order: 0 };
  const groups = commandGroups([sync, blind, duplex]);
  assert.equal(groups.length, 2);
  assert.deepEqual(groups[0].modes, [sync]);
  const entry = groups[1];
  assert.equal(entry.name, '报文收发');
  assert.deepEqual(entry.modes.map(mode => mode.id), ['duplex', 'transmit']);
  for (const word of ['报文收发', 'duplex', 'transmit', 'USRP', 'HackRF', '盲发', '同步时隙']) {
    assert.equal(commandGroupMatches(entry, word, '全部'), true);
  }
  assert.equal(commandGroupMatches(entry, '', '同步与协议分析'), false);
  assert.equal(commandGroupMatches(entry, '不存在', '全部'), false);
});

test('报文收发切换模式后 CLI 仅使用对应模式参数', () => {
  const message = { name: 'message_file', type: 'path', cli_flag: '--message-file' };
  const duplex = { id: 'duplex', subcommand: 'duplex', parameters: [message,
    { name: 'sync_timeout', type: 'number', cli_flag: '--sync-timeout' }] };
  const blind = { id: 'transmit', subcommand: 'transmit', parameters: [message,
    { name: 'repeat', type: 'integer', cli_flag: '--repeat' }] };
  assert.equal(cliPreview(duplex, { message_file: 'payload.json', sync_timeout: 30 }),
    'python -m flrc_modem duplex --message-file payload.json --sync-timeout 30');
  assert.equal(cliPreview(blind, { message_file: 'payload.json', repeat: 2 }),
    'python -m flrc_modem transmit --message-file payload.json --repeat 2');
});

const outputParameter = { name: 'output', type: 'path', cli_flag: '--output', default: 'outputs/decoded_frames.jsonl' };

test('命令任务名称驱动输出路径，CLI 预览与提交参数使用相同结果', () => {
  const command = { id: 'demod', subcommand: 'demod', parameters: [
    { name: 'iq', type: 'path', cli_flag: '--iq' }, outputParameter,
  ] };
  const original = { iq: 'outputs/input.iq', output: outputParameter.default };
  const values = commandDraftParameters(command, original, ' H12首次对频 ');
  assert.equal(values.output, 'outputs/H12首次对频.jsonl');
  assert.equal(values.iq, original.iq);
  assert.equal(original.output, outputParameter.default);
  assert.equal(cliPreview(command, values), "python -m flrc_modem demod --iq outputs/input.iq --output 'outputs/H12首次对频.jsonl' --ui-events ndjson");
  assert.deepEqual(commandDraftParameters(command, original, ' '), original);
});

test('手动输出优先于名称变化，清空可选输出及恢复跟随均生效', () => {
  const command = { parameters: [outputParameter] };
  const manual = { output: 'G:\\我的结果\\keep.jsonl' };
  assert.equal(commandDraftParameters(command, manual, '新名称', { output: true }).output, manual.output);
  assert.equal(commandDraftParameters(command, { output: '' }, '新名称', { output: true }).output, '');
  assert.equal(commandDraftParameters(command, manual, '新名称', { output: false }).output, 'outputs/新名称.jsonl');
});

test('多输出分别跟随名称，保留目录及扩展名，输入路径不受影响', () => {
  const html = { name: 'output_html', type: 'path', cli_flag: '--output-html', default: 'reports/timeline.html' };
  const command = { parameters: [outputParameter, html, { name: 'file', type: 'path', cli_flag: '--file' }] };
  const values = { output: '', output_html: 'reports/custom.html', file: 'outputs/input.iq' };
  const generated = commandDraftParameters(command, values, '对频');
  assert.deepEqual(generated, { output: 'outputs/对频.jsonl', output_html: 'reports/对频.html', file: values.file });
  assert.equal(commandDraftParameters(command, values, '对频', { output_html: true }).output_html, values.output_html);
  assert.equal(namedOutputPath({ ...outputParameter, default: 'G:\\结果\\captured.cs8' }, '采集', 'cf32'), 'G:\\结果\\采集.cf32');
  assert.equal(namedOutputPath(outputParameter, '对频.jsonl'), 'outputs/对频.jsonl');
});

test('自动文件名保留中文，过滤路径分隔符及 Windows 保留名称', () => {
  assert.equal(taskFileStem('  H12对频  '), 'H12对频');
  assert.equal(taskFileStem('a/b\\c:d*e?f"g<h>i|j'), 'a_b_c_d_e_f_g_h_i_j');
  assert.equal(taskFileStem('CON'), '_CON');
  assert.equal(taskFileStem('LPT1.data'), '_LPT1.data');
  assert.equal(taskFileStem('...'), 'task');
  assert.equal(taskFileStem('对频. '), '对频');
});

test('任务自动名称使用配置和文件，自定义名称优先且不受后续参数变化影响', () => {
  const run = { command_id: 'demod', command_args: { config: 'templates/h12.toml', iq: 'G:\\采集\\对频.iq' },
    id: 'run_12345678', status: 'SUCCEEDED', created_at: '2026-10-20T01:00:00Z' };
  assert.equal(runTitle(run), '离线解调 · h12 · 对频.iq');
  assert.equal(runTitle({ ...run, name: '  第一次对频  ' }), '第一次对频');
  assert.equal(runTitle({ ...run, name: ' ' }), runTitle(run));
  assert.ok(runOption(run).includes('12345678'));
  assert.ok(runOption(run).includes('2026'));
  assert.ok(runTime(run.created_at).includes('2026'));
  assert.equal(runTime('invalid'), 'invalid');
});

test('只允许清理已结束任务，包含失败及取消状态', () => {
  for (const status of ['SUCCEEDED', 'FAILED', 'CANCELLED']) assert.equal(runDeletable({ status }), true);
  for (const status of ['RUNNING', 'QUEUED']) assert.equal(runDeletable({ status }), false);
});

test('删除任务清除各分页、详情及分析缓存，保留其他任务同名文件的缓存', async () => {
  const client = new QueryClient();
  client.setQueryData(['runs', 100, 0], [{ id: 'old' }, { id: 'keep' }]);
  client.setQueryData(['runs', 51, 0, 'FAILED', ''], [{ id: 'old' }]);
  const oldKeys = [['run', 'old'], ['runArtifacts', 'old', 'FAILED'], ['analysisContext', 'old'],
    ['frames', 'old', 'same.jsonl'], ['spectrum', 'same.iq', 'old'], ['constellation', 'same.iq', 'old']];
  for (const key of oldKeys) client.setQueryData(key, { stale: true });
  client.setQueryData(['spectrum', 'same.iq', 'keep'], { current: true });
  await purgeRunCaches(client, ['old']);
  assert.deepEqual(client.getQueryData(['runs', 100, 0]), [{ id: 'keep' }]);
  assert.deepEqual(client.getQueryData(['runs', 51, 0, 'FAILED', '']), []);
  for (const key of oldKeys) assert.equal(client.getQueryData(key), undefined);
  assert.deepEqual(client.getQueryData(['spectrum', 'same.iq', 'keep']), { current: true });
  client.clear();
});

test('管理事件刷新列表，批量删除通知包含所有任务 ID', () => {
  const event = { type: 'task.deleted', run_id: 'old', payload: { run_ids: ['old', 'other'] } };
  assert.deepEqual(deletedRunIds(event), ['old', 'other']);
  assert.deepEqual(deletedRunIds({ type: 'task.updated', run_id: 'old' }), []);
  for (const type of ['task.updated', 'task.deleted']) {
    const keys = [];
    const change = { ...event, type };
    refreshForEvent({ invalidateQueries: query => keys.push(query.queryKey) }, change);
    useWebSocketStore.getState().handleEvent(change);
    assert.equal(useWebSocketStore.getState().latestEvent, change);
    assert.ok(keys.some(key => key[0] === 'runs'));
    assert.ok(keys.some(key => key[0] === (type === 'task.updated' ? 'run' : 'artifacts')));
  }
});

test('解调分析关联任务的 IQ 和不同文件名 JSONL，不需要输入产物记录', () => {
  const context = { run_id: 'h12_run', iq_path: 'outputs/fresh_pairing_2403_8.iq', frames_path: 'outputs/decoded_frames.jsonl' };
  assert.deepEqual(analysisFiles(null, context, []), {
    path: context.iq_path, framesPath: context.frames_path,
  });
  // 先前选中的任务文件不能覆盖新启动的解调任务。
  assert.deepEqual(analysisFiles('outputs/old.iq', context, []), {
    path: context.iq_path, framesPath: context.frames_path,
  });
  assert.deepEqual(analysisFiles(context.frames_path, context, []), {
    path: context.iq_path, framesPath: context.frames_path,
  });
});

test('任务未配置的文件不借用其他任务的产物', () => {
  const artifacts = [{ run_id: 'other', path: 'outputs/other.iq' },
    { run_id: 'other', path: 'outputs/other.jsonl' }];
  assert.deepEqual(analysisFiles('outputs/other.iq', {
    run_id: 'current', iq_path: null, frames_path: 'outputs/current.jsonl',
  }, artifacts), { path: '', framesPath: 'outputs/current.jsonl' });
  assert.deepEqual(analysisFiles('outputs/other.jsonl', {
    run_id: 'current', iq_path: 'outputs/current.iq', frames_path: null,
  }, artifacts), { path: 'outputs/current.iq', framesPath: '' });
});

test('重复解调相同 IQ 时，只选择当前任务结果', () => {
  const artifacts = [{ run_id: 'old', path: 'outputs/data.iq' },
    { run_id: 'old', path: 'outputs/data.jsonl' }, { run_id: 'new', path: 'outputs/result.jsonl' }];
  const context = { run_id: 'new', iq_path: 'outputs/data.iq', frames_path: 'outputs/result.jsonl' };
  assert.equal(analysisFiles('outputs/data.iq', context, artifacts).framesPath, 'outputs/result.jsonl');
});

test('Windows 正反斜杠不会破坏任务文件关联', () => {
  const windowsPath = ['outputs', 'fresh.iq'].join(String.fromCharCode(92));
  assert.equal(fileKey(windowsPath), 'outputs/fresh.iq');
  const context = { run_id: 'new', iq_path: windowsPath, frames_path: 'outputs/result.jsonl' };
  assert.equal(analysisFiles('outputs/fresh.iq', context, []).framesPath, context.frames_path);
});

test('产物登记和任务完成事件触发分析缓存刷新，无阶段 ID 也生效', () => {
  for (const type of ['artifact.created', 'task.completed']) {
    const queries = [];
    const event = { type, run_id: 'h12', seq: 1, stage_id: null, payload: {} };
    refreshForEvent({ invalidateQueries: query => queries.push(query.queryKey) }, event);
    assert.ok(queries.some(key => key[0] === 'analysisContext' && (key.length === 1 || key[1] === 'h12')));
    assert.ok(queries.some(key => key[0] === 'frames' && (key.length === 1 || key[1] === 'h12')));
    assert.ok(queries.some(key => JSON.stringify(key) === '["artifacts"]'));
    useWebSocketStore.getState().handleEvent(event);
    assert.equal(useWebSocketStore.getState().latestEvent, event);
  }
});

test('缺失或覆盖的文件禁止展示缓存，历史版本未确认时有明确标记', () => {
  for (const status of ['missing', 'overwritten', 'modified', 'pending']) {
    const state = { status, readable: false };
    assert.equal(fileReadable(state), false);
    assert.ok(fileStateLabel(state));
  }
  assert.equal(fileReadable({ status: 'unverified', readable: true }), true);
  assert.equal(fileStateLabel({ status: 'overwritten', readable: false }), '已覆盖');
});

test('历史日志和实时日志合并，重叠记录去重并保留时间顺序', () => {
  const first = { timestamp: '2026-10-20T00:00:00Z', stream: 'stdout', line: '历史' };
  const second = { timestamp: '2026-10-20T00:00:01Z', stream: 'stderr', line: '后续' };
  assert.deepEqual(mergeLogs([first, second], [second]), [first, second]);
  assert.deepEqual(mergeLogs([first], [second], 1), [second]);
});

test('CLI 预览保护 PowerShell 路径和引号，硬件诊断不附加机器事件选项', () => {
  const command = { id: 'hackrf', subcommand: 'hackrf', parameters: [
    { name: 'action', type: 'choice', cli_flag: '' },
    { name: 'file', type: 'path', cli_flag: '--file' },
  ] };
  const preview = cliPreview(command, { action: 'info', file: "my folder/it's $test.iq" });
  assert.equal(preview, "python -m flrc_modem hackrf info --file 'my folder/it''s $test.iq'");
  assert.ok(!preview.includes('--ui-events'));
});

test('前端生成重复的数值选项并保留关闭值', () => {
  const command = { id: 'demod', subcommand: 'demod', parameters: [
    { name: 'frequency', type: 'list_number', cli_flag: '--frequency' },
    { name: 'bt', type: 'number', cli_flag: '--bt' },
  ] };
  assert.equal(cliPreview(command, { frequency: [100, 200], bt: 0 }),
    'python -m flrc_modem demod --frequency 100 --frequency 200 --bt 0 --ui-events ndjson');
});

test('WebSocket 忽略旧连接回调、自动重连，主动断开后停止重连', () => {
  const saved = { window: globalThis.window, WebSocket: globalThis.WebSocket,
    clearTimeout: globalThis.clearTimeout, clearInterval: globalThis.clearInterval };
  const timers = new Map();
  let nextId = 0;
  const sockets = [];
  class FakeSocket {
    static OPEN = 1;
    readyState = 0;
    constructor(url) { this.url = url; sockets.push(this); }
    send() {}
    close() { this.readyState = 3; this.onclose?.({}); }
    open() { this.readyState = 1; this.onopen?.({}); }
  }
  globalThis.WebSocket = FakeSocket;
  globalThis.window = { location: { protocol: 'http:', host: 'localhost:3000' },
    setTimeout: fn => { timers.set(++nextId, fn); return nextId; },
    setInterval: fn => { timers.set(++nextId, fn); return nextId; } };
  globalThis.clearTimeout = globalThis.clearInterval = id => timers.delete(id);
  try {
    const store = useWebSocketStore.getState();
    store.connect();
    sockets[0].open();
    const staleClose = sockets[0].onclose;
    store.connect('run_2');
    sockets[1].open();
    staleClose();
    assert.equal(useWebSocketStore.getState().connected, true);
    assert.equal(timers.size, 1);
    sockets[1].close();
    assert.equal(useWebSocketStore.getState().connected, false);
    const callback = [...timers.values()][0];
    timers.clear();
    callback();
    assert.equal(sockets.length, 3);
    assert.equal(sockets[2].url, 'ws://localhost:3000/ws/runs/run_2');
    sockets[2].open();
    store.disconnect();
    assert.equal(timers.size, 0);
    assert.equal(useWebSocketStore.getState().connected, false);
  } finally {
    Object.assign(globalThis, saved);
  }
});

test('实时日志缓存有行数及任务数上限', () => {
  useWebSocketStore.setState({ logs: {} });
  const store = useWebSocketStore.getState();
  for (let i = 0; i < 3010; i++) store.addLog('run_0', { stream: 'stdout', line: String(i), timestamp: String(i) });
  assert.equal(useWebSocketStore.getState().logs.run_0.length, 3000);
  for (let i = 1; i <= 25; i++) store.addLog(`run_${i}`, { stream: 'stdout', line: 'x', timestamp: 'x' });
  assert.equal(Object.keys(useWebSocketStore.getState().logs).length, 20);
});
