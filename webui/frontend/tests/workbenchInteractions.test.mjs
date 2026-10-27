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
      export { ProtocolAnalysisView } from './src/views/ProtocolAnalysisView.tsx';
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
const { TransmitWorkbenchPanel, TemplateLibraryView, ProtocolAnalysisView, useWorkstationStore, api } = modules;

function nodeText(node) {
  return typeof node === 'string' ? node : (node.children || []).map(nodeText).join('');
}

const flushQuery = () => new Promise(resolve => setTimeout(resolve, 0));

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

test('协议解析工作台：载入示例立即自动解码且不自动收起准备区，字节分组不重复请求，清空恢复快速引导', async () => {
  const previousWorkstation = useWorkstationStore.getState();
  const previousDecode = api.decodeProtocol;
  const decodeCalls = [];
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  client.setQueryData(['protocols'], [
    { id: 'dk32s', title: 'DK32S', description: 'DK32S 遥控协议' },
    { id: 'dk32s-multi', title: 'DK32S Multi', description: 'DK32S 多机协议' },
    { id: 'datalink', title: 'DataLink', description: 'DataLink 数传协议' },
  ]);
  client.setQueryData(['runs', 100, 0], []);
  let renderer;
  try {
    api.decodeProtocol = async (protocol, payloadHex) => {
      decodeCalls.push({ protocol, payloadHex });
      return {
        schema_version: 1,
        parser_version: 'test',
        protocol,
        payload_hex: payloadHex,
        payload_length: payloadHex.length / 2,
        physical_crc_status: 'not_checked',
        status: 'parsed',
        error: null,
        application: {
          protocol,
          command_hex: '0xF7',
          command_name: '串口波特率',
          direction: 'request',
          direction_basis: 'command_table',
          sequence: 1,
          need_ack: true,
          is_ack: false,
          sum8_ok: true,
          wire_layout: 'compact',
          fields: { baud_rate: 115200 },
        },
        ranges: [{
          path: 'fields.baud_rate',
          label: '波特率',
          section: 'business',
          derived: true,
          offset: 5,
          length: 1,
          raw_hex: '05',
          value: 115200,
        }],
      };
    };
    useWorkstationStore.setState({
      ...useWorkstationStore.getInitialState(),
      protocolDraft: { protocol: 'dk32s', payloadHex: '', source: '手动输入' },
    });

    await act(async () => {
      renderer = create(
        React.createElement(QueryClientProvider, { client }, React.createElement(ProtocolAnalysisView))
      );
    });

    // 初始空状态展示物理载荷结构快速引导，且不触发解码请求
    assert.equal(renderer.root.findAllByProps({ 'aria-label': '协议解析快速开始' }).length, 1);
    assert.equal(decodeCalls.length, 0);

    // 点击内置 DataLink 示例 -> 无需手动点“解析报文”，自动切换协议并解码，且准备区保持展开
    const sampleBtn = renderer.root.findAllByType('button').find(b => nodeText(b).includes('载入 DataLink 0xF7 波特率'));
    assert.ok(sampleBtn);
    await act(async () => {
      sampleBtn.props.onClick();
    });
    await act(async () => {
      await flushQuery();
    });

    assert.deepEqual(decodeCalls, [{ protocol: 'datalink', payloadHex: '06010100f705fe' }]);
    assert.equal(renderer.root.findByProps({ id: 'pi-preparation' }).props.hidden, false);
    assert.ok(nodeText(renderer.root).includes('0xF7 · 串口波特率'));

    // 点击“按字节分组”仅格式化显示，归一化字节未变不触发多余请求
    const groupBtn = renderer.root.findAllByType('button').find(b => nodeText(b).includes('按字节分组'));
    assert.ok(groupBtn);
    await act(async () => {
      groupBtn.props.onClick();
    });
    await act(async () => {
      await flushQuery();
    });
    assert.equal(useWorkstationStore.getState().protocolDraft.payloadHex, '06 01 01 00 F7 05 FE');
    assert.equal(decodeCalls.length, 1);

    // 切换协议下拉框 -> 立即以新协议重新自动解码
    const protocolSelect = renderer.root.findByProps({ id: 'protocol-select' });
    await act(async () => {
      protocolSelect.props.onChange({ target: { value: 'dk32s' } });
    });
    await act(async () => {
      await flushQuery();
    });
    assert.equal(decodeCalls.length, 2);
    assert.deepEqual(decodeCalls[1], { protocol: 'dk32s', payloadHex: '06010100f705fe' });

    // 点击“清空” -> 清除解析结果并回到快速引导卡片
    const clearBtn = renderer.root.findAllByType('button').find(b => nodeText(b) === '清空');
    await act(async () => {
      clearBtn.props.onClick();
    });
    assert.equal(renderer.root.findAllByProps({ 'aria-label': '协议解析快速开始' }).length, 1);
    assert.equal(nodeText(renderer.root).includes('0xF7 · 串口波特率'), false);
  } finally {
    if (renderer) await act(async () => renderer.unmount());
    client.clear();
    api.decodeProtocol = previousDecode;
    useWorkstationStore.setState(previousWorkstation, true);
  }
});

test('协议解析工作台：任务记录保留当前选中帧，支持关键词筛选、上下帧连续步进自动解码与切回 Hex 微调', async () => {
  const previousWorkstation = useWorkstationStore.getState();
  const previousDecode = api.decodeProtocol;
  const decodeCalls = [];
  const runId = 'demod-step-run';
  const framesPath = 'outputs/demod-step.jsonl';
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  client.setQueryData(['protocols'], [
    { id: 'dk32s', title: 'DK32S', description: 'DK32S' },
    { id: 'dk32s-multi', title: 'DK32S Multi', description: 'DK32S Multi' },
    { id: 'datalink', title: 'DataLink', description: 'DataLink' },
  ]);
  client.setQueryData(['runs', 100, 0], [{
    id: runId,
    attempt: 1,
    command_id: 'demod',
    status: 'SUCCEEDED',
    command_args: {},
    created_at: '2026-10-27T03:00:00Z',
  }]);
  client.setQueryData(['analysisContext', runId], {
    run_id: runId,
    command_id: 'demod',
    status: 'SUCCEEDED',
    iq_path: null,
    frames_path: framesPath,
    sample_rate: 2500000,
    center_frequency: 2440000000,
    iq_format: 'cf32',
    metadata_error: null,
    file_states: {
      iq: { status: 'not_configured', readable: false, message: null, revision: '0', current_run_id: null },
      frames: { status: 'available', readable: true, message: null, revision: 'r1', current_run_id: runId },
    },
  });
  client.setQueryData(['frames', runId, framesPath, 'r1'], {
    frames: [
      { time_s: 0.01, frequency_hz: 2440000000, payload_hex: '11223344', application: { protocol: 'dk32s', command_name: '遥测心跳' } },
      { time_s: 0.02, frequency_hz: 2440000000, payload_hex: '06010100f705fe', application: { protocol: 'datalink', command_name: '串口波特率' } },
      { time_s: 0.03, frequency_hz: 2440000000, payload_hex: 'aabbccdd', application: { protocol: 'dk32s', rc_mode: 'multi', command_name: '多机通道' } },
    ],
  });

  let renderer;
  try {
    api.decodeProtocol = async (protocol, payloadHex) => {
      decodeCalls.push({ protocol, payloadHex });
      return {
        schema_version: 1,
        parser_version: 'test',
        protocol,
        payload_hex: payloadHex,
        payload_length: payloadHex.length / 2,
        physical_crc_status: 'not_checked',
        status: 'parsed',
        error: null,
        application: {
          protocol,
          command_hex: '0x10',
          command_name: `解析-${payloadHex}`,
          direction: 'request',
          direction_basis: 'command_table',
          sequence: 1,
          need_ack: false,
          is_ack: false,
          sum8_ok: true,
          wire_layout: 'compact',
          fields: {},
        },
        ranges: [],
      };
    };

    useWorkstationStore.setState({
      ...useWorkstationStore.getInitialState(),
      protocolDraft: {
        protocol: 'dk32s',
        payloadHex: '11223344',
        source: `${runId} · 第 1 帧`,
        origin: { runId, attempt: 1 },
      },
    });

    await act(async () => {
      renderer = create(
        React.createElement(QueryClientProvider, { client }, React.createElement(ProtocolAnalysisView))
      );
      await flushQuery();
    });

    // 进入时主界面直接展示 Hex 编辑器并自动解码第 1 帧；右侧任务抽屉默认隐藏，侧边悬浮手柄可见
    assert.deepEqual(decodeCalls, [{ protocol: 'dk32s', payloadHex: '11223344' }]);
    assert.equal(renderer.root.findByProps({ id: 'protocol-payload' }).props.value, '11223344');
    assert.equal(renderer.root.findByProps({ id: 'pi-task-drawer' }).props.hidden, true);
    const edgeTrigger = renderer.root.findByProps({ 'aria-label': '展开任务记录抽屉' });
    assert.equal(edgeTrigger.props['aria-expanded'], false);

    // 鼠标悬浮右侧抽屉手柄 -> 自动展开纯浮动抽屉（默认不钉住，且移除底部收起抽屉行）
    await act(async () => {
      edgeTrigger.props.onMouseEnter();
    });
    const drawerNode = () => renderer.root.findByProps({ id: 'pi-task-drawer' });
    assert.equal(drawerNode().props.hidden, false);
    assert.equal(drawerNode().props['data-pinned'], false);
    assert.equal(renderer.root.findByProps({ className: 'pi-studio' }).props.style, undefined);
    assert.equal(renderer.root.findAll(node => node.props?.className === 'pi-drawer-backdrop').length, 0);
    assert.equal(renderer.root.findAll(node => node.props?.className === 'pi-drawer-footer').length, 0);
    assert.equal(renderer.root.findAllByProps({ 'aria-label': '展开任务记录抽屉' }).length, 0);

    // 默认未钉住时，鼠标移出抽屉自动隐藏；再次悬浮展开后点击钉子固定，鼠标移出不再自动隐藏
    await act(async () => {
      drawerNode().props.onMouseLeave();
    });
    assert.equal(drawerNode().props.hidden, true);
    await act(async () => {
      renderer.root.findByProps({ 'aria-label': '展开任务记录抽屉' }).props.onMouseEnter();
    });
    assert.equal(drawerNode().props.hidden, false);
    const pinBtn = renderer.root.findByProps({ 'aria-label': '固定任务记录抽屉' });
    assert.equal(pinBtn.props['aria-pressed'], false);
    await act(async () => {
      pinBtn.props.onClick();
    });
    assert.equal(renderer.root.findByProps({ 'aria-label': '固定任务记录抽屉' }).props['aria-pressed'], true);
    assert.equal(drawerNode().props['data-pinned'], true);
    await act(async () => {
      drawerNode().props.onMouseLeave();
    });
    assert.equal(drawerNode().props.hidden, false);

    // 抽屉展开状态下仍可直接操作主界面控件（如按字节分组）而不关闭抽屉
    const groupMainBtn = renderer.root.findAllByType('button').find(b => nodeText(b).includes('按字节分组'));
    await act(async () => {
      groupMainBtn.props.onClick();
    });
    assert.equal(renderer.root.findByProps({ id: 'protocol-payload' }).props.value, '11 22 33 44');
    assert.equal(renderer.root.findByProps({ id: 'pi-task-drawer' }).props.hidden, false);

    const frameSelect = renderer.root.findByProps({ id: 'protocol-source-frame' });
    assert.equal(frameSelect.props.value, '0');
    const prevBtn = renderer.root.findByProps({ 'aria-label': '上一帧报文' });
    const nextBtn = renderer.root.findByProps({ 'aria-label': '下一帧报文' });
    assert.equal(prevBtn.props.disabled, true);
    assert.equal(nextBtn.props.disabled, false);

    // 点击“下一帧” -> 连续步进到第 2 帧并自动切换 datalink 解码，主界面 Hex 编辑器实时同步且准备区不折叠
    await act(async () => {
      nextBtn.props.onClick();
    });
    await act(async () => {
      await flushQuery();
    });
    assert.equal(renderer.root.findByProps({ id: 'protocol-source-frame' }).props.value, '1');
    assert.equal(decodeCalls.length, 2);
    assert.deepEqual(decodeCalls[1], { protocol: 'datalink', payloadHex: '06010100f705fe' });
    assert.equal(renderer.root.findByProps({ id: 'protocol-payload' }).props.value, '06010100F705FE');
    assert.equal(renderer.root.findByProps({ id: 'pi-preparation' }).props.hidden, false);

    // 输入筛选词过滤出第 3 帧（多机模式）并点击抽屉内列表项载入
    const filterInput = renderer.root.findByProps({ 'aria-label': '筛选任务报文' });
    await act(async () => {
      filterInput.props.onChange({ target: { value: '多机通道' } });
    });
    const drawerList = renderer.root.findByProps({ 'aria-label': '解调帧列表' });
    await act(async () => {
      drawerList.findByType('button').props.onClick();
    });
    await act(async () => {
      await flushQuery();
    });
    assert.equal(renderer.root.findByProps({ id: 'protocol-source-frame' }).props.value, '2');
    assert.equal(decodeCalls.length, 3);
    assert.deepEqual(decodeCalls[2], { protocol: 'dk32s-multi', payloadHex: 'aabbccdd' });

    // 点击关闭抽屉按钮 -> 右侧浮动抽屉重新隐藏，主界面保留当前 Hex 载荷与解析结果
    const closeDrawerBtn = renderer.root.findByProps({ 'aria-label': '关闭任务记录抽屉' });
    await act(async () => {
      closeDrawerBtn.props.onClick();
    });
    assert.equal(renderer.root.findByProps({ id: 'pi-task-drawer' }).props.hidden, true);
    assert.equal(renderer.root.findAllByProps({ 'aria-label': '展开任务记录抽屉' }).length, 1);
    assert.equal(renderer.root.findByProps({ id: 'protocol-payload' }).props.value, 'AABBCCDD');
  } finally {
    if (renderer) await act(async () => renderer.unmount());
    client.clear();
    api.decodeProtocol = previousDecode;
    useWorkstationStore.setState(previousWorkstation, true);
  }
});
