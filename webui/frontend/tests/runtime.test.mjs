import assert from 'node:assert/strict';
import { test } from 'node:test';
import { build } from 'esbuild';

async function loadModule(path) {
  const result = await build({ entryPoints: [path], bundle: true, write: false, format: 'esm', platform: 'node' });
  return import(`data:text/javascript;base64,${Buffer.from(result.outputFiles[0].text).toString('base64')}`);
}

const { mergeLogs, cliPreview } = await loadModule('src/utils/runtime.ts');
const { useWebSocketStore } = await loadModule('src/stores/useWebSocketStore.ts');

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
