import React, { useMemo, useState } from 'react';
import { Download, Search, ListTree, Braces, ArrowDownUp, ChevronDown } from 'lucide-react';
import type { ProtocolByteRange, ProtocolInspection } from '../types';
import { CopyButton } from './CopyButton';
import { byteRangeAt, packetSegments, rangeContains, selectedBits } from '../utils/protocolPresentation';

const fieldLabels: Record<string, string> = {
  kind: '报文类型', raw_hex: '未解释的原始字节', rc_mode: '遥控布局',
  channels_raw: '通道原始值（11 bit）', channels_output: '固件输出值（原值 +500，单位未确认）',
  target_device_mask: '目标设备位图', device_number: '设备编号', data_batch_id: '数据批次号',
  stream_data_hex: '透传数据 Hex', voltage_raw: '电压原始值（单位未确认）',
  timestamp_ticks: '时间戳计数', correction_ticks: '时间修正计数（100 μs/计数）',
  correction_sec: '时间修正（秒）', transfer_delay_sec: '传输延时（秒）', sync_time_sec: '同步时间（秒）',
  filter_address: '过滤地址', channel_indices: '频道索引表', mode: '模式原值', sync_count: '同步计数',
  channel_mask: '通道掩码', channel_enabled: '通道启用状态', status: '状态原值',
  input_voltage_mv: '校准输入电压（mV）', channel_mapping: 'PWM 通道映射', unused_tail_hex: '未定义尾部 Hex',
  version_hex: '固件版本标识', auto_pairing: '自动配对原值', signal_mode: '输出模式原值',
  fcu_type: '飞控类型原值', baud_index: '波特率索引', baud_rate: '波特率（bit/s）',
  voltage_reporting: '电压回传配置', vice_pairing: '接力/主副配置原值', tx_power_level: '发射功率档位原值',
  antenna_mode: '天线配置原值', operation: '操作号', data1_function: '端口 1 功能原值',
  data2_function: '端口 2 功能原值', value: '通知原值', prefix_hex: '前缀 Hex', enable: '启用原值',
  stage: '配对阶段', rc_address: '遥控器地址', receiver_address: '接收机地址',
  parameters: '原始参数', control_channel: '控制通道', brightness_level: '亮度档位',
  is_busy: '配对忙状态', key: '查询项', config_value: '配置原值', magic_hex: '标识 Hex',
  index: '索引', params_u32: '原始 uint32 参数', parameters_u32: '原始 uint32 参数',
};

const kindLabels: Record<string, string> = {
  ack: '确认报文', query: '查询', unknown: '业务内容尚未解释', config_set: '配置设置',
  config_value: '配置回复', rc_channels: '遥控通道', extended_rc_channels: '扩展遥控通道',
  pairing_offer: '配对参数', pairing_ack: '配对确认', pairing_handshake: '配对握手',
  switch_master_slave: '主副切换', telemetry: '数据回传', mode_sync: '模式同步',
  time_correction: '时间修正', channel_table: '频道表', transfer_delay: '传输延时',
  firmware_version: '固件版本', voltage_calibration: '电压校准', failsafe: '失控保护',
};

function showValue(value: unknown): string {
  if (value === null || value === undefined) return '未确认';
  if (typeof value === 'boolean') return value ? '是' : '否';
  return typeof value === 'object' ? JSON.stringify(value) : String(value);
}

interface RangeInteraction {
  selectedPath: string | null;
  activePath?: string;
  onSelect: (path: string) => void;
  onHover: (path: string | null) => void;
}

function rangeLabel(range: ProtocolByteRange): string {
  if (range.section !== 'business') return range.label;
  const [, key, index] = range.path.split('.');
  return (fieldLabels[key] || key) + (index === undefined ? '' : ' [' + index + ']');
}

function rangeDescription(range: ProtocolByteRange): string {
  return '偏移 ' + range.offset + ' · ' + range.length + ' B' +
    (range.bit_length === undefined ? '' : ' · bit ' + range.bit_offset + ' 起，共 ' + range.bit_length + ' 位') +
    (range.derived ? ' · 解释/换算值，显示来源字节' : '');
}

function RangeValue({ range, label, interaction, children }: {
  range?: ProtocolByteRange; label: string; interaction: RangeInteraction; children: React.ReactNode;
}) {
  if (!range) return <span className="pi-range-value" title="无独立字节（模式配置或空值）">{children}<span className="sr-only">无独立字节（模式配置或空值）</span></span>;
  return <button type="button" data-field-path={range.path} aria-label={label + '，高亮来源字节'} title={rangeDescription(range)}
    aria-pressed={interaction.selectedPath === range.path} data-active={interaction.activePath === range.path}
    onClick={() => interaction.onSelect(range.path)}
    onMouseEnter={() => interaction.onHover(range.path)} onMouseLeave={() => interaction.onHover(null)}
    onFocus={() => interaction.onHover(range.path)} onBlur={() => interaction.onHover(null)} className="pi-range-value">
    {children}{range.derived && <span className="pi-derived" aria-hidden="true">↳</span>}
    <span className="sr-only">{rangeDescription(range)}</span>
  </button>;
}

function BusinessFields({ fields, ranges, interaction, query }: {
  fields: Record<string, unknown>; ranges: Map<string, ProtocolByteRange>; interaction: RangeInteraction; query: string;
}) {
  const needle = query.trim().toLowerCase();
  const entries = Object.entries(fields).filter(([key, value]) =>
    !needle || [key, fieldLabels[key], showValue(value), key === 'kind' ? kindLabels[String(value)] : ''].join(' ').toLowerCase().includes(needle));
  if (!entries.length) return <p className="pi-field-empty">没有匹配的业务字段，试试中文名称、字段名或原始值。</p>;
  return <dl>
    {entries.filter(([, value]) => !Array.isArray(value)).map(([key, value]) => <div key={key} className="pi-field-row">
      <dt>{fieldLabels[key] || key}<span className="pi-field-key">{key}</span></dt>
      <dd><RangeValue range={ranges.get('fields.' + key)} label={fieldLabels[key] || key} interaction={interaction}>
        {key === 'kind' ? kindLabels[String(value)] || showValue(value) : showValue(value)}
      </RangeValue></dd>
    </div>)}
    {entries.filter(([, value]) => Array.isArray(value)).map(([key, value]) => {
      const items = value as unknown[];
      return <div key={key} className="pi-array-field"><dt>
        <span>{fieldLabels[key] || key}<span className="pi-field-key">{key}</span></span>
        <RangeValue range={ranges.get('fields.' + key)} label={fieldLabels[key] || key} interaction={interaction}>
          <span aria-hidden="true">整组 · {items.length}</span><span className="sr-only">高亮整组（{items.length} 项）</span>
        </RangeValue>
      </dt><dd><ol className="pi-array-values" style={{ gridTemplateColumns: 'repeat(' + Math.min(4, Math.max(1, items.length)) + ', minmax(0, 1fr))' }}>
        {items.map((item, index) => <li key={index}>
          <RangeValue range={ranges.get('fields.' + key + '.' + index)} label={(fieldLabels[key] || key) + ' [' + index + ']'} interaction={interaction}>
            <span className="pi-array-index">{key.startsWith('channels_') || key === 'channel_enabled' ? 'CH ' + String(index + 1).padStart(2, '0') : String(index).padStart(2, '0')}</span>
            <strong>{showValue(item)}</strong>
          </RangeValue>
        </li>)}
      </ol></dd></div>;
    })}
  </dl>;
}

const segmentLabels: Record<string, string> = {
  software_length: '帧长', timestamp_ticks: '时间戳', payload_length: '长度 / ACK', sequence: '序号 / ACK',
  command: '命令', payload_hex: '业务载荷', sum8: 'SUM8',
};

export function ProtocolInspectionResult({ result, layout = 'standalone', showHexHeading = true }: {
  result: ProtocolInspection; layout?: 'standalone' | 'slice'; showHexHeading?: boolean;
}) {
  const [selectedPath, setSelectedPath] = useState<string | null>(null);
  const [hoveredPath, setHoveredPath] = useState<string | null>(null);
  const [query, setQuery] = useState('');
  const { ranges, envelopeRanges, segments, report } = useMemo(() => ({
    ranges: new Map(result.ranges.map(range => [range.path, range])),
    envelopeRanges: result.ranges.filter(range => range.section !== 'business'),
    segments: packetSegments(result.ranges), report: JSON.stringify(result, null, 2),
  }), [result]);
  const activeRange = ranges.get(hoveredPath || selectedPath || '');
  const interaction: RangeInteraction = {
    selectedPath, activePath: activeRange?.path, onHover: setHoveredPath,
    onSelect: path => setSelectedPath(current => current === path ? null : path),
  };
  const app = result.application;
  const exportReport = () => {
    const url = URL.createObjectURL(new Blob([report], { type: 'application/json' }));
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = 'protocol-' + result.protocol + '.json';
    anchor.click();
    setTimeout(() => URL.revokeObjectURL(url), 0);
  };
  const matchedEnvelope = envelopeRanges.filter(range => !query.trim() ||
    (range.label + ' ' + range.path + ' ' + showValue(range.value)).toLowerCase().includes(query.trim().toLowerCase()));

  const decodedPane = <section className="pi-decoded" aria-label="解码字段">
        <div className="pi-section-title"><h3><ListTree aria-hidden="true" />解码字段</h3><span>{app ? Object.keys(app.fields).length : 0} BUSINESS FIELDS</span></div>
        <label className="pi-field-search"><Search aria-hidden="true" /><input type="search" aria-label="搜索协议字段" value={query} onChange={event => setQuery(event.target.value)} placeholder="搜索中文名称、字段或原始值…" /></label>
        {app && <section className="pi-field-group" aria-label="业务内容"><h4 className="pi-group-label"><span>APPLICATION / 业务内容</span><span>点击字段定位</span></h4>
          <BusinessFields fields={app.fields} ranges={ranges} interaction={interaction} query={query} /></section>}
        <details className="pi-field-group" open={layout === 'slice' ? true : undefined}><summary className="pi-group-label cursor-pointer"><span>ENVELOPE / 封装字段</span><ChevronDown className="h-3 w-3" aria-hidden="true" /></summary>
          {envelopeRanges.length ? <dl>{matchedEnvelope.map(range => <div key={range.path} className="pi-field-row">
            <dt>{range.label}<span className="pi-field-key">{range.path}</span></dt><dd><RangeValue range={range} label={range.label} interaction={interaction}>{showValue(range.value)}</RangeValue></dd>
          </div>)}</dl> : <p className="pi-field-empty">结构检查未通过，不猜测字段偏移。</p>}
        </details>
      </section>;
  const bytePane = <ProtocolHexView payloadHex={result.payload_hex} ranges={result.ranges} interaction={interaction}
    showHeading={showHexHeading}
    title={layout === 'slice' ? '十六进制视图' : 'Hex / ASCII'} />;

  if (layout === 'slice') return <div className="pi-result pi-result--slice">
    {bytePane}
    <div className="space-y-4 border-t border-rf-border pt-4">
      <div className="flex flex-wrap items-center justify-between gap-2 text-xs">
        <span className="font-semibold text-zinc-200">{app ? app.command_hex + ' · ' + app.command_name : '未能解析当前载荷'}</span>
        <span className="font-mono text-zinc-400">{result.protocol.toUpperCase()} · {result.payload_length} B</span>
      </div>
      {result.error && <p role="alert" className="pi-alert pi-alert--error">{result.error}</p>}
      {result.status === 'partial' && <p className="pi-alert">封装与 sum8 校验通过，业务字段尚未解释。</p>}
      {decodedPane}
    </div>
  </div>;

  return <section aria-label="协议解析结果" className="pi-result">
    <header className="pi-result-heading"><div><div className="pi-eyebrow">02 / DECODED PACKET</div>
      <h2 className="mt-2">{app ? app.command_hex + ' · ' + app.command_name : '业务解析失败'}</h2>
      <p>{result.protocol.toUpperCase()} · {result.payload_length} B · 解析器 v{result.parser_version}</p></div>
      <div className="pi-actions"><span className="pi-state" data-state={result.status}><span className="pi-dot" />{result.status === 'parsed' ? '已解析' : result.status === 'partial' ? '部分解析' : '解析失败'}</span>
        <CopyButton text={report} label="复制解析 JSON" />
        <button type="button" onClick={exportReport} className="pi-button"><Download aria-hidden="true" />导出 JSON</button>
      </div></header>
    {result.error && <p role="alert" className="pi-alert pi-alert--error">{result.error}。原始载荷仍保留在下方。</p>}
    {result.status === 'partial' && <p className="pi-alert">封装与 sum8 校验通过，但业务字段尚未解释。请查看命令号及原始字节。</p>}
    {app && <dl className="pi-summary">
      <div><dt>业务校验</dt><dd><span className="text-emerald-300">sum8 通过</span></dd><small>物理 CRC 未验证</small></div>
      <div><dt>方向 · 依据固件布局推断</dt><dd>{app.direction === 'ground_to_air' ? '地面 → 天空' : app.direction === 'air_to_ground' ? '天空 → 地面' : '未知方向'}</dd><small>{app.wire_layout === 'timestamp_prefix' ? '带时间戳封装' : '紧凑帧封装'}</small></div>
      <div><dt>报文序号</dt><dd>#{app.sequence}</dd><small>请求 ACK {showValue(app.need_ack)} · 确认 {showValue(app.is_ack)}</small></div>
      <div><dt>时间戳</dt><dd>{app.wire_layout === 'timestamp_prefix' ? app.timestamp_sec + ' s' : '—'}</dd><small>{app.wire_layout === 'timestamp_prefix' ? '设备时间 · 100 μs / 计数' : '当前封装无外层时间戳'}</small></div>
    </dl>}
    {segments.length > 0 && <section aria-label="报文结构图"><div className="pi-map-header"><span>WIRE MAP / 报文结构</span><span>0 → {result.payload_length - 1} · BYTE OFFSET</span></div>
      <div className="pi-wire-map">{segments.map(segment => <button key={segment.path} type="button" className="pi-wire-segment"
        style={{ flexGrow: segment.length }} data-tone={segment.path === 'payload_hex' ? 'business' : segment.path === 'sum8' ? 'checksum' : 'envelope'}
        aria-label={(segmentLabels[segment.path] || segment.label) + '，定位来源字节'} aria-pressed={activeRange?.path === segment.path}
        onClick={() => interaction.onSelect(segment.path)} onMouseEnter={() => setHoveredPath(segment.path)} onMouseLeave={() => setHoveredPath(null)}
        onFocus={() => setHoveredPath(segment.path)} onBlur={() => setHoveredPath(null)}>
        <strong>{segmentLabels[segment.path] || segment.label}</strong><span>{segment.length} B</span>
      </button>)}</div>
    </section>}
    <div className="pi-result-grid">
      {decodedPane}
      {bytePane}
    </div>
  </section>;
}


const NO_RANGES: ProtocolByteRange[] = [];
const NO_INTERACTION: RangeInteraction = { selectedPath: null, onSelect: () => {}, onHover: () => {} };
const BYTE_COLUMNS = 16;

export function ProtocolHexView({ payloadHex, ranges = NO_RANGES, interaction = NO_INTERACTION, title = '十六进制视图', showHeading = true }: {
  payloadHex: string; ranges?: ProtocolByteRange[]; interaction?: RangeInteraction; title?: string; showHeading?: boolean;
}) {
  const { bytes, byteSources, byteTones } = useMemo(() => {
    const bytes = payloadHex.match(/../g) || [];
    const segments = packetSegments(ranges);
    return { bytes, byteSources: bytes.map((_, index) => byteRangeAt(ranges, index)),
      byteTones: bytes.map((_, index) => {
        const segment = segments.find(span => rangeContains(span, index));
        return segment?.path === 'payload_hex' ? 'business' : segment?.path === 'sum8' ? 'checksum' : 'envelope';
      }) };
  }, [payloadHex, ranges]);
  const activeRange = ranges.find(range => range.path === interaction.activePath);
  const bits = activeRange ? selectedBits(activeRange) : [];
  return <section className="pi-byte-pane" aria-label="原始字节与字段定位">
        {showHeading && <div className="pi-section-title"><h3><Braces aria-hidden="true" />{title}</h3><CopyButton text={payloadHex} label="复制 Hex" /></div>}
        {(ranges.length > 0 || !showHeading) && <div className="pi-byte-toolbar">
          {ranges.length > 0 && <div className="pi-byte-legend"><span><i className="pi-legend-dot" />封装</span><span><i className="pi-legend-dot pi-legend-dot--business" />业务</span><span><i className="pi-legend-dot pi-legend-dot--checksum" />校验</span></div>}
          {!showHeading && <CopyButton text={payloadHex} label="复制 Hex" />}
        </div>}
        <div className="pi-byte-scroll" tabIndex={0} aria-label="十六进制字节">
          {bytes.length === 0 ? <p className="text-xs text-zinc-400">此切片没有独立的十六进制字节。</p> : <table className="pi-byte-table" aria-label="十六进制字节与 ASCII"><thead><tr><th scope="col">OFFSET</th>
            {Array.from({ length: BYTE_COLUMNS }, (_, index) => <th scope="col" key={index}>{index.toString(16).padStart(2, '0').toUpperCase()}</th>)}<th scope="col">ASCII</th>
          </tr></thead><tbody>
            {Array.from({ length: Math.ceil(bytes.length / BYTE_COLUMNS) }, (_, row) => <tr key={row}>
              <td>{(row * BYTE_COLUMNS).toString(16).padStart(4, '0').toUpperCase()}</td>
              {Array.from({ length: BYTE_COLUMNS }, (_, column) => {
                const index = row * BYTE_COLUMNS + column;
                const hex = bytes[index];
                const source = byteSources[index];
                const highlighted = activeRange ? rangeContains(activeRange, index) : false;
                return <td key={column}>{hex ? <button type="button" disabled={!source} className="pi-byte" data-byte-offset={index}
                  data-tone={byteTones[index]} data-selected={highlighted} aria-label={'偏移 ' + index + '，Hex ' + hex} aria-pressed={highlighted}
                  title={source ? rangeLabel(source) : '结构未确认，仅展示原始字节'}
                  onClick={() => { if (source) interaction.onSelect(source.path); }}
                  onMouseEnter={() => { if (source) interaction.onHover(source.path); }} onMouseLeave={() => interaction.onHover(null)}
                  onFocus={() => { if (source) interaction.onHover(source.path); }} onBlur={() => interaction.onHover(null)}>{hex}</button> : <span className="pi-byte" />}</td>;
              })}
              <td className="pi-byte-ascii">{bytes.slice(row * BYTE_COLUMNS, (row + 1) * BYTE_COLUMNS).map((hex, column) => {
                const byte = parseInt(hex, 16);
                const character = byte >= 32 && byte <= 126 ? String.fromCharCode(byte) : '·';
                return activeRange && rangeContains(activeRange, row * BYTE_COLUMNS + column) ? <mark key={column}>{character}</mark> : <span key={column}>{character}</span>;
              })}</td>
            </tr>)}
          </tbody></table>}
        </div>
        {ranges.length > 0 && <div className="pi-selection"><div className="pi-eyebrow">{activeRange ? 'SELECTED FIELD / 选中字段' : 'FIELD LOCATOR / 字段定位'}</div>
          <h4>{activeRange ? rangeLabel(activeRange) : <span className="inline-flex items-center gap-2"><ArrowDownUp className="h-3.5 w-3.5" aria-hidden="true" />字段与字节，双向定位</span>}</h4>
          <p aria-live="polite">{activeRange ? rangeDescription(activeRange) + '（小端位序）' : '点击字段高亮来源字节；点击字节反查对应字段。偏移从物理载荷第 0 字节开始。'}</p>
          {activeRange && <div className="pi-selected-hex">{activeRange.raw_hex.match(/../g)?.join(' ')}</div>}
          {bits.length > 0 && <div className="pi-bit-strip" aria-label="小端位域，位编号从低到高">{bits.map(bit => <span key={bit.index} data-active={bit.active}><small>{bit.index}</small>{bit.value}</span>)}</div>}
        </div>}
      </section>;
}
