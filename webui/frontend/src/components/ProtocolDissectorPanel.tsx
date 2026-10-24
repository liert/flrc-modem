import { hexText } from '../utils/hex';
import React, { useState, useMemo } from 'react';
import {
  ChevronDown,
  ChevronRight,
  Layers,
  FileCode,
  Radio,
  ShieldCheck,
  Cpu,
  Activity,
  ArrowRight,
  ArrowLeft,
  X,
  Copy,
  Check,
  Download,
  Info,
  Sliders,
  GitCompare,
  Code2,
} from 'lucide-react';
import type { DecodedFrameRecord } from '../types';
import { CopyButton } from './CopyButton';
import { useWorkstationStore } from '../stores/useWorkstationStore';

export interface ProtocolDissectorPanelProps {
  frame: DecodedFrameRecord;
  frameIndex: number;
  totalFrames: number;
  allFrames?: DecodedFrameRecord[];
  onClose?: () => void;
  onSelectNext?: () => void;
  onSelectPrev?: () => void;
}

interface HighlightSpan {
  start: number; // 字节起始偏移（在全帧中的字节偏移）
  end: number;   // 字节结束偏移（inclusive）
  label: string;
  layer: 'phy' | 'header' | 'payload' | 'crc' | 'app';
}

export const ProtocolDissectorPanel: React.FC<ProtocolDissectorPanelProps> = ({
  frame,
  frameIndex,
  totalFrames,
  allFrames,
  onClose,
  onSelectNext,
  onSelectPrev,
}) => {
  // 折叠状态
  const [openSections, setOpenSections] = useState<Record<string, boolean>>({
    phy: true,
    header: true,
    crc: true,
    app: true,
    payload: true,
  });

  const toggleSection = (key: string) => {
    setOpenSections(prev => ({ ...prev, [key]: !prev[key] }));
  };

  // 差分对比状态
  const [diffMode, setDiffMode] = useState<boolean>(false);
  const [compareFrameIndex, setCompareFrameIndex] = useState<number>(
    frameIndex > 0 ? frameIndex - 1 : (totalFrames > 1 ? 1 : 0)
  );
  const [showExportMenu, setShowExportMenu] = useState<boolean>(false);
  const [copiedKind, setCopiedKind] = useState<string | null>(null);

  // 高亮区间（由协议树节点 hover 或选定驱动）
  const [activeHighlight, setActiveHighlight] = useState<HighlightSpan | null>(null);
  // 鼠标悬停 Hex 字节索引
  const [hoveredByteIndex, setHoveredByteIndex] = useState<number | null>(null);

  // 1. 构建全帧字节序列 (Sync Word + Header + Payload + CRC)
  const { fullBytes, spans } = useMemo(() => {
    const bytes: number[] = [];
    const spanList: HighlightSpan[] = [];

    const hexToBytes = (hex?: string): number[] => {
      if (!hex) return [];
      const clean = hex.replace(/[^0-9a-fA-F]/g, '');
      const result: number[] = [];
      for (let i = 0; i < clean.length; i += 2) {
        result.push(parseInt(clean.slice(i, i + 2), 16));
      }
      return result;
    };

    // 1) 同步字 (Sync Word)
    const syncBytes = hexToBytes(frame.sync_word_hex);
    if (syncBytes.length > 0) {
      const start = bytes.length;
      bytes.push(...syncBytes);
      spanList.push({
        start,
        end: bytes.length - 1,
        label: '同步字 (Sync Word)',
        layer: 'phy',
      });
    }

    // 2) 硬件包头 (Hardware Header)
    const headerBytes = hexToBytes(frame.hardware_header_hex);
    if (headerBytes.length > 0) {
      const start = bytes.length;
      bytes.push(...headerBytes);
      spanList.push({
        start,
        end: bytes.length - 1,
        label: '硬件包头 (Header)',
        layer: 'header',
      });
    }

    // 3) 有效载荷 (Payload)
    const payloadBytes = hexToBytes(frame.payload_hex);
    const payloadStart = bytes.length;
    if (payloadBytes.length > 0) {
      bytes.push(...payloadBytes);
      spanList.push({
        start: payloadStart,
        end: bytes.length - 1,
        label: '物理层载荷 (Payload)',
        layer: 'payload',
      });
    }

    // 4) 硬件校验码 (CRC)
    const crcBytes = hexToBytes(frame.crc_hex);
    if (crcBytes.length > 0) {
      const start = bytes.length;
      bytes.push(...crcBytes);
      spanList.push({
        start,
        end: bytes.length - 1,
        label: `硬件 CRC${crcBytes.length * 8}`,
        layer: 'crc',
      });
    }

    // 5) 应用层细分子跨度（若有）
    if (payloadBytes.length >= 4) {
      // 常见 SX1280 协议格式：[0]长度, [1..2]seq/flags, [3]cmd
      spanList.push({
        start: payloadStart,
        end: payloadStart,
        label: '应用层: 报文体长度',
        layer: 'app',
      });
      spanList.push({
        start: payloadStart + 1,
        end: payloadStart + 2,
        label: '应用层: 序号与标志 (Seq & Flags)',
        layer: 'app',
      });
      spanList.push({
        start: payloadStart + 3,
        end: payloadStart + 3,
        label: '应用层: 指令字 (Command ID)',
        layer: 'app',
      });
      if (payloadBytes.length > 4) {
        spanList.push({
          start: payloadStart + 4,
          end: payloadStart + payloadBytes.length - 2,
          label: '应用层: 业务字段载荷 (Payload Body)',
          layer: 'app',
        });
        spanList.push({
          start: payloadStart + payloadBytes.length - 1,
          end: payloadStart + payloadBytes.length - 1,
          label: '应用层: 校验和 (Checksum)',
          layer: 'app',
        });
      }
    }

    return { fullBytes: bytes, spans: spanList };
  }, [frame]);

  // 根据当前字节获取所属跨度说明
  const currentByteSpan = useMemo(() => {
    if (hoveredByteIndex === null) return null;
    // 从后往前找最细分的 span (app 优先于 payload)
    const matches = spans.filter(s => hoveredByteIndex >= s.start && hoveredByteIndex <= s.end);
    return matches[matches.length - 1] || null;
  }, [hoveredByteIndex, spans]);

  const hexToBytesHelper = (hex?: string): number[] => {
    if (!hex) return [];
    const clean = hex.replace(/[^0-9a-fA-F]/g, '');
    const result: number[] = [];
    for (let i = 0; i < clean.length; i += 2) {
      result.push(parseInt(clean.slice(i, i + 2), 16));
    }
    return result;
  };

  const compareFrame = useMemo(() => {
    if (!allFrames || compareFrameIndex < 0 || compareFrameIndex >= allFrames.length) return null;
    return allFrames[compareFrameIndex] ?? null;
  }, [allFrames, compareFrameIndex]);

  const diffInfo = useMemo(() => {
    if (!diffMode || !compareFrame) return null;
    const refSync = hexToBytesHelper(compareFrame.sync_word_hex);
    const refHdr = hexToBytesHelper(compareFrame.hardware_header_hex);
    const refPay = hexToBytesHelper(compareFrame.payload_hex);
    const refCrc = hexToBytesHelper(compareFrame.crc_hex);
    const refBytes = [...refSync, ...refHdr, ...refPay, ...refCrc];

    let modifiedCount = 0;
    let bitFlipCount = 0;
    const byteDiffs: Record<number, { refByte: number | undefined; curByte: number; changed: boolean; bitFlips: number }> = {};

    const maxLen = Math.max(fullBytes.length, refBytes.length);
    for (let i = 0; i < maxLen; i++) {
      const cur = fullBytes[i];
      const ref = refBytes[i];
      const changed = cur !== ref;
      let bits = 0;
      if (cur !== undefined && ref !== undefined && changed) {
        let xor = cur ^ ref;
        while (xor > 0) {
          bits += xor & 1;
          xor >>= 1;
        }
      }
      if (changed) {
        modifiedCount++;
        bitFlipCount += bits;
      }
      if (cur !== undefined) {
        byteDiffs[i] = { refByte: ref, curByte: cur, changed, bitFlips: bits };
      }
    }

    const timeDeltaMs = Number.isFinite(frame.time_s) && Number.isFinite(compareFrame.time_s)
      ? (frame.time_s - compareFrame.time_s) * 1000
      : null;
    const cfoDeltaHz = Number.isFinite(frame.cfo_hz) && Number.isFinite(compareFrame.cfo_hz)
      ? frame.cfo_hz - compareFrame.cfo_hz
      : null;
    const powerDeltaDb = Number.isFinite(frame.sample_power_db) && Number.isFinite(compareFrame.sample_power_db)
      ? frame.sample_power_db - compareFrame.sample_power_db
      : null;

    return {
      refBytes,
      byteDiffs,
      modifiedCount,
      bitFlipCount,
      timeDeltaMs,
      cfoDeltaHz,
      powerDeltaDb,
      refIndex: compareFrameIndex,
    };
  }, [diffMode, compareFrame, compareFrameIndex, fullBytes, frame]);

  const fullHex = useMemo(() => {
    return fullBytes.map(b => b.toString(16).padStart(2, '0')).join('').toUpperCase();
  }, [fullBytes]);

  const handleExportBinary = () => {
    const arr = new Uint8Array(fullBytes);
    const blob = new Blob([arr], { type: 'application/octet-stream' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `frame_${frameIndex + 1}_${frame.sync_word_hex || 'flrc'}.bin`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const copyToClipboard = (text: string, kind: string) => {
    if (typeof navigator !== 'undefined' && navigator.clipboard) {
      navigator.clipboard.writeText(text);
      setCopiedKind(kind);
      setTimeout(() => setCopiedKind(null), 2000);
    }
  };

  const getCArrayText = () => {
    const hexPairs = fullBytes.map(b => `0x${b.toString(16).padStart(2, '0').toUpperCase()}`);
    return `const uint8_t frame_${frameIndex + 1}[] = { ${hexPairs.join(', ')} };\nconst size_t frame_${frameIndex + 1}_len = ${fullBytes.length};`;
  };

  const getPythonBytesText = () => {
    return `frame_${frameIndex + 1} = bytes.fromhex("${fullHex}")`;
  };

  // 格式化输出 JSON
  const handleExportJson = () => {
    const blob = new Blob([JSON.stringify(frame, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `frame_${frameIndex + 1}_${(frame.time_s * 1000).toFixed(0)}ms.json`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const app = frame.application;
  const fields = app?.fields || {};
  const openStandaloneParser = () => {
    const workstation = useWorkstationStore.getState();
    const candidate = app?.rc_mode === 'multi' ? 'dk32s-multi' : app?.protocol;
    workstation.openProtocol({
      protocol: ['dk32s', 'dk32s-multi', 'datalink'].includes(candidate) ? candidate : 'dk32s',
      payloadHex: frame.payload_hex,
      source: '报文详情 · 第 ' + (frameIndex + 1) + ' 帧',
    });
  };

  return (
    <div className="bg-rf-surface border border-rf-border rounded-xl overflow-hidden shadow-2xl flex flex-col font-mono text-xs select-none h-full min-h-0 min-w-0">
      {/* 1. 顶部控制栏 */}
      <div className="shrink-0 p-3.5 bg-rf-panel/80 border-b border-rf-border flex flex-wrap items-center justify-between gap-3">
        <div className="flex min-w-0 max-w-full items-center gap-2.5">
          <div className="w-7 h-7 shrink-0 rounded-lg bg-cyan-500/15 border border-cyan-500/40 flex items-center justify-center text-cyan-400">
            <Layers className="w-4 h-4" />
          </div>
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
              <h4 className="font-bold text-sm text-rf-text">报文协议树逐层解码器</h4>
              <span className="px-2 py-0.2 rounded-full bg-cyan-950/80 text-cyan-300 border border-cyan-800/80 text-[10px] font-bold">
                第 {frameIndex + 1} / {totalFrames} 帧
              </span>
              <span className="text-[11px] text-zinc-400 font-normal">
                t = {(frame.time_s * 1000).toFixed(3)} ms
              </span>
            </div>
            <p className="text-[11px] text-zinc-400 font-sans">
              Wireshark 风格分层透视 · 悬停字段高亮 Hex 字节区间 · 业务字段结构化解析
            </p>
          </div>
        </div>

        <div className="flex min-w-0 max-w-full flex-wrap items-center gap-2">
          <button type="button" onClick={openStandaloneParser} className="px-2.5 py-1.5 rounded-lg border border-cyan-700 text-cyan-300 hover:bg-cyan-950/50">
            在独立面板解析
          </button>
          {onSelectPrev && (
            <button
              type="button"
              onClick={onSelectPrev}
              disabled={frameIndex <= 0}
              className="p-1.5 rounded-lg bg-rf-panel hover:bg-rf-border text-zinc-300 border border-rf-border/80 disabled:opacity-40 transition-colors"
              title="上一帧 (P)"
            >
              <ArrowLeft className="w-3.5 h-3.5" />
            </button>
          )}
          {onSelectNext && (
            <button
              type="button"
              onClick={onSelectNext}
              disabled={frameIndex >= totalFrames - 1}
              className="p-1.5 rounded-lg bg-rf-panel hover:bg-rf-border text-zinc-300 border border-rf-border/80 disabled:opacity-40 transition-colors"
              title="下一帧 (N)"
            >
              <ArrowRight className="w-3.5 h-3.5" />
            </button>
          )}
          {/* 差分对比控制 */}
          {allFrames && allFrames.length > 1 && (
            <div className="flex items-center space-x-1.5 bg-rf-bg px-2 py-1 rounded-lg border border-rf-border">
              <button
                type="button"
                onClick={() => setDiffMode(!diffMode)}
                className={`px-2 py-0.5 rounded flex items-center space-x-1.5 text-xs transition-colors ${
                  diffMode
                    ? 'bg-amber-500/20 text-amber-300 border border-amber-500/60 font-bold'
                    : 'text-zinc-400 hover:text-zinc-200'
                }`}
                title="与其它帧进行逐字节与射频指标差分对比"
              >
                <GitCompare className="w-3.5 h-3.5" />
                <span>帧对比 {diffMode ? '开' : '关'}</span>
              </button>
              {diffMode && (
                <select
                  value={compareFrameIndex}
                  onChange={(e) => setCompareFrameIndex(Number(e.target.value))}
                  className="bg-rf-surface border border-rf-border rounded px-2 py-0.5 text-[11px] text-zinc-300 font-mono focus:outline-none focus:border-amber-400 max-w-[160px] truncate"
                >
                  {allFrames.map((f, i) => {
                    if (i === frameIndex) return null;
                    return (
                      <option key={i} value={i}>
                        基准: 第 {i + 1} 帧 (t={(f.time_s * 1000).toFixed(1)}ms)
                      </option>
                    );
                  })}
                </select>
              )}
            </div>
          )}

          {/* 导出与向量菜单 */}
          <div className="relative">
            <button
              type="button"
              onClick={() => setShowExportMenu(!showExportMenu)}
              className="px-2.5 py-1.5 rounded-lg bg-rf-panel hover:bg-rf-border text-zinc-300 border border-rf-border/80 flex items-center space-x-1.5 transition-colors"
              title="导出当前帧或复制不同格式测试向量"
            >
              <Download className="w-3.5 h-3.5 text-cyan-400" />
              <span>导出与向量</span>
              <ChevronDown className="w-3 h-3 text-zinc-400" />
            </button>
            {showExportMenu && (
              <div
                className="absolute right-0 top-full mt-1.5 w-52 bg-rf-surface border border-rf-border rounded-lg shadow-xl z-50 py-1 font-mono text-[11px]"
                onMouseLeave={() => setShowExportMenu(false)}
              >
                <button
                  type="button"
                  onClick={() => {
                    handleExportJson();
                    setShowExportMenu(false);
                  }}
                  className="w-full text-left px-3 py-1.5 hover:bg-rf-panel text-zinc-300 flex items-center justify-between"
                >
                  <span>下载 JSON 报告</span>
                  <span className="text-zinc-500 text-[10px]">.json</span>
                </button>
                <button
                  type="button"
                  onClick={() => {
                    handleExportBinary();
                    setShowExportMenu(false);
                  }}
                  className="w-full text-left px-3 py-1.5 hover:bg-rf-panel text-zinc-300 flex items-center justify-between"
                >
                  <span>下载原始二进制</span>
                  <span className="text-zinc-500 text-[10px]">.bin</span>
                </button>
                <div className="h-px bg-rf-border/60 my-1" />
                <button
                  type="button"
                  onClick={() => {
                    copyToClipboard(fullHex, 'fullHex');
                    setShowExportMenu(false);
                  }}
                  className="w-full text-left px-3 py-1.5 hover:bg-rf-panel text-zinc-300 flex items-center justify-between"
                >
                  <span>{copiedKind === 'fullHex' ? '已复制全帧 Hex' : '复制全帧 Hex'}</span>
                  <span className="text-zinc-500 text-[10px]">{fullBytes.length}B</span>
                </button>
                <button
                  type="button"
                  onClick={() => {
                    copyToClipboard(getCArrayText(), 'carray');
                    setShowExportMenu(false);
                  }}
                  className="w-full text-left px-3 py-1.5 hover:bg-rf-panel text-zinc-300 flex items-center justify-between"
                >
                  <span>{copiedKind === 'carray' ? '已复制 C 数组' : '复制 C 语言数组'}</span>
                  <span className="text-zinc-500 text-[10px]">uint8_t[]</span>
                </button>
                <button
                  type="button"
                  onClick={() => {
                    copyToClipboard(getPythonBytesText(), 'pybytes');
                    setShowExportMenu(false);
                  }}
                  className="w-full text-left px-3 py-1.5 hover:bg-rf-panel text-zinc-300 flex items-center justify-between"
                >
                  <span>{copiedKind === 'pybytes' ? '已复制 Python 向量' : '复制 Python 向量'}</span>
                  <span className="text-zinc-500 text-[10px]">bytes</span>
                </button>
              </div>
            )}
          </div>
          <CopyButton text={hexText(frame.payload_hex)} label="复制载荷 Hex" />
          {onClose && (
            <button
              type="button"
              onClick={onClose}
              className="p-1.5 rounded-lg hover:bg-rf-panel text-zinc-400 hover:text-rf-text transition-colors"
              title="收起解码器"
            >
              <X className="w-4 h-4" />
            </button>
          )}
        </div>
      </div>

      {/* 差分对比摘要胶囊栏 */}
      {diffMode && diffInfo && (
        <div className="bg-amber-950/40 border-b border-amber-800/60 px-4 py-1.5 text-[11px] font-mono text-amber-300 flex flex-wrap items-center justify-between gap-2 shrink-0">
          <div className="flex items-center space-x-3">
            <span className="font-bold flex items-center space-x-1.5 text-amber-400">
              <GitCompare className="w-3.5 h-3.5" />
              <span>基准: 第 {diffInfo.refIndex + 1} 帧</span>
            </span>
            <span className="text-zinc-300">
              载荷差异: <strong className="text-amber-200">{diffInfo.modifiedCount}</strong> 字节 ({diffInfo.bitFlipCount} bit 翻转)
            </span>
          </div>
          <div className="flex items-center space-x-3 text-zinc-300 text-[10px]">
            {diffInfo.timeDeltaMs !== null && (
              <span>Δt: <strong className="text-amber-200">{diffInfo.timeDeltaMs >= 0 ? '+' : ''}{diffInfo.timeDeltaMs.toFixed(3)} ms</strong></span>
            )}
            {diffInfo.cfoDeltaHz !== null && (
              <span>ΔCFO: <strong className="text-amber-200">{diffInfo.cfoDeltaHz >= 0 ? '+' : ''}{(diffInfo.cfoDeltaHz / 1000).toFixed(2)} kHz</strong></span>
            )}
            {diffInfo.powerDeltaDb !== null && (
              <span>ΔP: <strong className="text-amber-200">{diffInfo.powerDeltaDb >= 0 ? '+' : ''}{diffInfo.powerDeltaDb.toFixed(1)} dB</strong></span>
            )}
          </div>
        </div>
      )}

      {/* 2. 主体：左侧可折叠协议树 + 右侧 Hex & ASCII Dump 矩阵 */}
      <div className="grid grid-cols-1 grid-rows-[minmax(0,1fr)_minmax(0,1fr)] lg:grid-cols-12 lg:grid-rows-[minmax(0,1fr)] divide-y lg:divide-y-0 lg:divide-x divide-rf-border/60 max-h-[560px] lg:max-h-[620px] flex-1 overflow-hidden min-h-0">
        {/* 左侧：结构化分层协议树 (5 栏) */}
        <div role="region" aria-label="报文协议树" tabIndex={0}
          className="protocol-dissector-tree lg:col-span-5 overflow-y-auto overflow-x-hidden overscroll-contain p-3.5 space-y-3 select-text bg-rf-bg/40 min-h-0 min-w-0">
          {/* ================= Layer 1: 物理层与空口定界 ================= */}
          <div className="rounded-lg border border-rf-border/80 bg-rf-surface/90 overflow-hidden">
            <button
              type="button"
              onClick={() => toggleSection('phy')}
              onMouseEnter={() => {
                const s = spans.find(span => span.layer === 'phy');
                if (s) setActiveHighlight(s);
              }}
              onMouseLeave={() => setActiveHighlight(null)}
              className="w-full px-3 py-2 bg-rf-panel/60 border-b border-rf-border/50 flex items-center justify-between text-left hover:bg-rf-panel transition-colors"
            >
              <div className="flex items-center space-x-2">
                {openSections.phy ? <ChevronDown className="w-3.5 h-3.5 text-cyan-400" /> : <ChevronRight className="w-3.5 h-3.5 text-zinc-400" />}
                <Radio className="w-3.5 h-3.5 text-cyan-400" />
                <span className="font-bold text-xs text-rf-text">物理层 (PHY Layer & Synchronization)</span>
              </div>
              <span className="text-[10px] text-zinc-400 bg-rf-panel px-1.5 py-0.5 rounded border border-rf-border/40">
                SX1280 2.4GHz FLRC
              </span>
            </button>

            {openSections.phy && (
              <div className="p-3 space-y-1.5 text-xs">
                <div
                  className="flex items-center justify-between py-1 px-1.5 rounded hover:bg-cyan-500/10 cursor-pointer"
                  onMouseEnter={() => {
                    const s = spans.find(span => span.label.includes('同步字'));
                    if (s) setActiveHighlight(s);
                  }}
                  onMouseLeave={() => setActiveHighlight(null)}
                >
                  <span className="text-zinc-400">同步字 (Sync Word):</span>
                  <span className="font-bold text-emerald-400">
                    0x{hexText(frame.sync_word_hex)} {frame.sync_word_ambiguous ? '(多候选匹配)' : '(精确定位)'}
                  </span>
                </div>
                <div className="flex items-center justify-between py-1 px-1.5 rounded hover:bg-cyan-500/10">
                  <span className="text-zinc-400">中心频率 (Carrier Frequency):</span>
                  <span className="text-zinc-200">{(frame.frequency_hz / 1e6).toFixed(6)} MHz</span>
                </div>
                <div className="flex items-center justify-between py-1 px-1.5 rounded hover:bg-cyan-500/10">
                  <span className="text-zinc-400">载波频偏 (CFO Offset):</span>
                  <span className={Number.isFinite(frame.cfo_hz) && Math.abs(frame.cfo_hz) > 20000 ? 'text-amber-300' : 'text-zinc-200'}>
                    {Number.isFinite(frame.cfo_hz) ? `${(frame.cfo_hz / 1000).toFixed(2)} kHz` : '-'}
                  </span>
                </div>
                <div className="flex items-center justify-between py-1 px-1.5 rounded hover:bg-cyan-500/10">
                  <span className="text-zinc-400">接收谱功率 (Estimated Power):</span>
                  <span className="text-zinc-200">
                    {Number.isFinite(frame.sample_power_db) ? `${frame.sample_power_db.toFixed(1)} dB` : '-'}
                  </span>
                </div>
                <div className="flex items-center justify-between py-1 px-1.5 rounded hover:bg-cyan-500/10">
                  <span className="text-zinc-400">前导码纠错状态 (Preamble Errors):</span>
                  <span className={frame.preamble_errors ? 'text-amber-300' : 'text-emerald-400'}>
                    {frame.preamble_errors === 0 ? '0 纠错 (无符号误差)' : `${frame.preamble_errors} 符号误差纠正`}
                  </span>
                </div>
              </div>
            )}
          </div>

          {/* ================= Layer 2: 硬件包头与变长控制 ================= */}
          <div className="rounded-lg border border-rf-border/80 bg-rf-surface/90 overflow-hidden">
            <button
              type="button"
              onClick={() => toggleSection('header')}
              onMouseEnter={() => {
                const s = spans.find(span => span.layer === 'header');
                if (s) setActiveHighlight(s);
              }}
              onMouseLeave={() => setActiveHighlight(null)}
              className="w-full px-3 py-2 bg-rf-panel/60 border-b border-rf-border/50 flex items-center justify-between text-left hover:bg-rf-panel transition-colors"
            >
              <div className="flex items-center space-x-2">
                {openSections.header ? <ChevronDown className="w-3.5 h-3.5 text-cyan-400" /> : <ChevronRight className="w-3.5 h-3.5 text-zinc-400" />}
                <Cpu className="w-3.5 h-3.5 text-cyan-400" />
                <span className="font-bold text-xs text-rf-text">硬件传输头与链路控制 (Hardware Header & FEC)</span>
              </div>
              <span className="text-[10px] text-zinc-400 bg-rf-panel px-1.5 py-0.5 rounded border border-rf-border/40">
                {frame.hardware_header_hex ? '变长包头 2B' : '固定长度模式'}
              </span>
            </button>

            {openSections.header && (
              <div className="p-3 space-y-1.5 text-xs">
                <div
                  className="flex items-center justify-between py-1 px-1.5 rounded hover:bg-cyan-500/10 cursor-pointer"
                  onMouseEnter={() => {
                    const s = spans.find(span => span.layer === 'header');
                    if (s) setActiveHighlight(s);
                  }}
                  onMouseLeave={() => setActiveHighlight(null)}
                >
                  <span className="text-zinc-400">硬件头原始字节:</span>
                  <span className="font-bold text-cyan-300">
                    {frame.hardware_header_hex ? `0x${hexText(frame.hardware_header_hex)}` : '无变长硬件头 (Fixed Header)'}
                  </span>
                </div>
                <div className="flex items-center justify-between py-1 px-1.5 rounded hover:bg-cyan-500/10">
                  <span className="text-zinc-400">物理层有效载荷长度:</span>
                  <span className="text-zinc-200">
                    {frame.payload_length ?? (frame.payload_hex ? frame.payload_hex.length / 2 : 0)} 字节
                  </span>
                </div>
                <div className="flex items-center justify-between py-1 px-1.5 rounded hover:bg-cyan-500/10">
                  <span className="text-zinc-400">卷积纠错路径度量 (FEC Path Metric):</span>
                  <span className="text-zinc-200">{frame.fec_path_metric ?? 0}</span>
                </div>
              </div>
            )}
          </div>

          {/* ================= Layer 3: 数据完整性校验 ================= */}
          <div className="rounded-lg border border-rf-border/80 bg-rf-surface/90 overflow-hidden">
            <button
              type="button"
              onClick={() => toggleSection('crc')}
              onMouseEnter={() => {
                const s = spans.find(span => span.layer === 'crc');
                if (s) setActiveHighlight(s);
              }}
              onMouseLeave={() => setActiveHighlight(null)}
              className="w-full px-3 py-2 bg-rf-panel/60 border-b border-rf-border/50 flex items-center justify-between text-left hover:bg-rf-panel transition-colors"
            >
              <div className="flex items-center space-x-2">
                {openSections.crc ? <ChevronDown className="w-3.5 h-3.5 text-cyan-400" /> : <ChevronRight className="w-3.5 h-3.5 text-zinc-400" />}
                <ShieldCheck className="w-3.5 h-3.5 text-emerald-400" />
                <span className="font-bold text-xs text-rf-text">数据完整性校验 (Hardware CRC)</span>
              </div>
              <span className="text-[10px] px-2 py-0.5 rounded bg-emerald-950/70 border border-emerald-800/80 text-emerald-400 font-bold">
                CRC OK
              </span>
            </button>

            {openSections.crc && (
              <div className="p-3 space-y-1.5 text-xs">
                <div
                  className="flex items-center justify-between py-1 px-1.5 rounded hover:bg-cyan-500/10 cursor-pointer"
                  onMouseEnter={() => {
                    const s = spans.find(span => span.layer === 'crc');
                    if (s) setActiveHighlight(s);
                  }}
                  onMouseLeave={() => setActiveHighlight(null)}
                >
                  <span className="text-zinc-400">硬件 CRC 校验值:</span>
                  <span className="font-bold text-emerald-400">0x{hexText(frame.crc_hex) || 'OK'}</span>
                </div>
                {frame.crc_initial !== undefined && (
                  <div className="flex items-center justify-between py-1 px-1.5 rounded hover:bg-cyan-500/10">
                    <span className="text-zinc-400">CRC 初始种子 (Initial Seed):</span>
                    <span className="text-zinc-300">
                      0x{frame.crc_initial.toString(16).toUpperCase().padStart(6, '0')}
                    </span>
                  </div>
                )}
              </div>
            )}
          </div>

          {/* ================= Layer 4: 应用层业务协议解析 ================= */}
          <div className="rounded-lg border border-cyan-500/40 bg-rf-surface/95 overflow-hidden shadow-sm">
            <button
              type="button"
              onClick={() => toggleSection('app')}
              onMouseEnter={() => {
                const s = spans.find(span => span.layer === 'app');
                if (s) setActiveHighlight(s);
              }}
              onMouseLeave={() => setActiveHighlight(null)}
              className="w-full px-3 py-2 bg-cyan-950/30 border-b border-cyan-800/40 flex items-center justify-between text-left hover:bg-cyan-950/50 transition-colors"
            >
              <div className="flex items-center space-x-2">
                {openSections.app ? <ChevronDown className="w-3.5 h-3.5 text-cyan-300" /> : <ChevronRight className="w-3.5 h-3.5 text-zinc-400" />}
                <Activity className="w-3.5 h-3.5 text-cyan-300" />
                <span className="font-bold text-xs text-cyan-200">
                  应用层业务解析 (Application Dissector: {app?.protocol ? app.protocol.toUpperCase() : '原生载荷'})
                </span>
              </div>
              <span className="text-[10px] text-cyan-300 bg-cyan-950/60 px-1.5 py-0.5 rounded border border-cyan-700/60">
                {app?.direction === 'ground_to_air' ? '地对空 GND→AIR' : app?.direction === 'air_to_ground' ? '空对地 AIR→GND' : '通用格式'}
              </span>
            </button>

            {openSections.app && (
              <div className="p-3 space-y-2 text-xs">
                {app ? (
                  <>
                    <div className="grid grid-cols-2 sm:grid-cols-3 gap-2 pb-2 border-b border-rf-border/40 font-mono text-[11px]">
                      <div className="bg-rf-bg/70 p-2 rounded border border-rf-border/50">
                        <span className="text-zinc-500 block text-[10px]">协议类型</span>
                        <span className="text-cyan-300 font-bold">{app.protocol}</span>
                      </div>
                      <div className="bg-rf-bg/70 p-2 rounded border border-rf-border/50">
                        <span className="text-zinc-500 block text-[10px]">报文序列号 (Seq)</span>
                        <span className="text-zinc-200 font-bold">#{app.sequence ?? '-'}</span>
                      </div>
                      <div className="bg-rf-bg/70 p-2 rounded border border-rf-border/50">
                        <span className="text-zinc-500 block text-[10px]">时间戳滴答 (Ticks)</span>
                        <span className="text-zinc-200 font-bold">{app.timestamp_ticks ?? '-'}</span>
                      </div>
                    </div>

                    {/* 遥控舵量可视化 (若为 DK32S 通道数据) */}
                    {Array.isArray(fields.channels_output) && fields.channels_output.length > 0 && (
                      <div className="space-y-1.5 pt-1">
                        <span className="text-[11px] font-bold text-zinc-400 flex items-center space-x-1.5">
                          <Sliders className="w-3 h-3 text-cyan-400" />
                          <span>遥控 16 通道舵量 (PWM 1000~2000 µs)</span>
                        </span>
                        <div className="grid grid-cols-2 sm:grid-cols-4 gap-1.5 font-mono text-[10px]">
                          {fields.channels_output.slice(0, 16).map((val: number, chIdx: number) => {
                            const percent = Math.max(0, Math.min(100, ((val - 1000) / 1000) * 100));
                            return (
                              <div key={chIdx} className="bg-rf-bg p-1.5 rounded border border-rf-border/50">
                                <div className="flex justify-between text-zinc-400 mb-0.5">
                                  <span>CH{chIdx + 1}</span>
                                  <span className="text-cyan-300 font-bold">{val} µs</span>
                                </div>
                                <div className="w-full bg-zinc-800 h-1.5 rounded-full overflow-hidden">
                                  <div
                                    className="bg-cyan-400 h-full transition-all"
                                    style={{ width: `${percent}%` }}
                                  />
                                </div>
                              </div>
                            );
                          })}
                        </div>
                      </div>
                    )}

                    {/* 其它业务字段列表 */}
                    <div className="space-y-1 pt-1 font-mono text-xs">
                      {Object.entries(fields)
                        .filter(([k]) => !['channels_output', 'channels_raw'].includes(k))
                        .map(([key, val]) => (
                          <div
                            key={key}
                            className="flex items-center justify-between py-1 px-1.5 rounded hover:bg-cyan-500/10"
                          >
                            <span className="text-zinc-400">{key}:</span>
                            <span className="text-zinc-200 font-semibold break-all text-right max-w-xs">
                              {typeof val === 'object' ? JSON.stringify(val) : key.endsWith('_hex') && typeof val === 'string' ? hexText(val) : String(val)}
                            </span>
                          </div>
                        ))}
                    </div>
                  </>
                ) : (
                  <div className="text-zinc-400 space-y-1 py-1 text-xs">
                    <p>当前任务未配置独立业务协议解析插件（如 DK32S 或 Datalink）。</p>
                    <p className="text-zinc-500 text-[11px]">
                      右侧显示完整的原始物理层十六进制载荷，可通过“模板预设库”加载对应的协议配置文件以激活自动解析。
                    </p>
                  </div>
                )}
              </div>
            )}
          </div>
        </div>

        {/* 右侧：交互式 Hex & ASCII Dump 矩阵 (7 栏) */}
        <div className="lg:col-span-7 flex flex-col bg-black/60 p-3 select-text overflow-hidden min-h-0 min-w-0">
          <div className="flex items-center justify-between pb-2 border-b border-rf-border/60 text-xs shrink-0">
            <span className="font-bold text-zinc-300 flex items-center space-x-1.5">
              <FileCode className="w-3.5 h-3.5 text-cyan-400" />
              <span>全帧十六进制矩阵 (Hex & ASCII Dump)</span>
            </span>
            <span className="text-[10px] text-zinc-400">共 {fullBytes.length} 字节</span>
          </div>

          {/* 矩阵滚动区 */}
          <div className="flex-1 overflow-auto font-mono text-[11px] leading-relaxed py-2 select-text min-h-0">
            {/* 表头偏移与 16 字节列标 */}
            <div className="flex items-center space-x-2 px-1 pb-1.5 border-b border-rf-border/40 text-[10px] text-zinc-500 font-mono select-none min-w-fit whitespace-nowrap">
              <span className="w-10 shrink-0 text-zinc-500 font-semibold">OFFSET</span>
              <div className="flex items-center space-x-1 shrink-0">
                {Array.from({ length: 16 }).map((__, idx) => (
                  <span
                    key={idx}
                    className={`w-5 text-center shrink-0 ${idx === 7 ? 'mr-1.5 text-cyan-500/80 font-bold' : ''}`}
                  >
                    {idx.toString(16).toUpperCase()}
                  </span>
                ))}
              </div>
              <div className="pl-3 border-l border-rf-border/40 shrink-0 text-zinc-500 font-semibold">
                <span>ASCII</span>
              </div>
            </div>

            {/* 矩阵内容行 */}
            <div className="pt-1 space-y-0.5">
              {Array.from({ length: Math.ceil(fullBytes.length / 16) }).map((_, rowIdx) => {
                const rowOffset = rowIdx * 16;
                const rowBytes = fullBytes.slice(rowOffset, rowOffset + 16);

                return (
                  <div
                    key={rowIdx}
                    className="flex items-center space-x-2 hover:bg-rf-panel/40 px-1 py-0.5 rounded min-w-fit whitespace-nowrap"
                  >
                    {/* 偏移量 */}
                    <span className="text-zinc-500 select-none w-10 text-[10px] shrink-0 font-mono">
                      {rowOffset.toString(16).padStart(4, '0').toUpperCase()}
                    </span>

                    {/* 16 字节 Hex */}
                    <div className="flex items-center space-x-1 shrink-0">
                      {Array.from({ length: 16 }).map((__, byteIdx) => {
                        const bytePos = rowOffset + byteIdx;
                        const byteVal = fullBytes[bytePos];
                        const isMidGap = byteIdx === 7;
                        if (byteVal === undefined) {
                          return (
                            <span
                              key={byteIdx}
                              className={`w-5 text-center text-transparent shrink-0 select-none ${isMidGap ? 'mr-1.5' : ''}`}
                            >
                              ..
                            </span>
                          );
                        }

                        // 判断高亮与差分状态
                        const isTreeHighlighted =
                          activeHighlight &&
                          bytePos >= activeHighlight.start &&
                          bytePos <= activeHighlight.end;
                        const isHovered = hoveredByteIndex === bytePos;
                        const isDiffChanged = Boolean(diffInfo?.byteDiffs[bytePos]?.changed);
                        const diffByteData = isDiffChanged ? diffInfo?.byteDiffs[bytePos] : undefined;

                        return (
                          <span
                            key={byteIdx}
                            onMouseEnter={() => setHoveredByteIndex(bytePos)}
                            onMouseLeave={() => setHoveredByteIndex(null)}
                            title={
                              isDiffChanged
                                ? `[差异] 偏移 0x${bytePos.toString(16).padStart(2, '0').toUpperCase()}: 原 0x${diffByteData?.refByte !== undefined ? diffByteData.refByte.toString(16).padStart(2, '0').toUpperCase() : '--'} → 现 0x${byteVal.toString(16).padStart(2, '0').toUpperCase()} (${diffByteData?.bitFlips} bit 翻转)`
                                : undefined
                            }
                            className={`w-5 text-center px-0.5 rounded cursor-pointer shrink-0 transition-all ${
                              isHovered
                                ? 'bg-cyan-400 text-black font-bold ring-1 ring-cyan-300 scale-105 z-10'
                                : isDiffChanged
                                ? 'bg-amber-500/35 text-amber-200 border-b-2 border-amber-400 font-bold ring-1 ring-amber-400/80 shadow-xs'
                                : isTreeHighlighted
                                ? 'bg-cyan-500/30 text-cyan-200 border-b border-cyan-400 font-bold'
                                : 'text-zinc-300 hover:text-white hover:bg-zinc-800'
                            } ${isMidGap ? 'mr-1.5' : ''}`}
                          >
                            {byteVal.toString(16).padStart(2, '0').toUpperCase()}
                          </span>
                        );
                      })}
                    </div>

                    {/* ASCII 字符映射（强制单行、防折叠防换行、与 Hex 联动高亮） */}
                    <div className="text-zinc-400 pl-3 border-l border-rf-border/40 select-none flex items-center shrink-0 whitespace-nowrap font-mono">
                      {rowBytes.map((b, i) => {
                        const bytePos = rowOffset + i;
                        const isTreeHighlighted =
                          activeHighlight &&
                          bytePos >= activeHighlight.start &&
                          bytePos <= activeHighlight.end;
                        const isHovered = hoveredByteIndex === bytePos;

                        const isDiffChanged = Boolean(diffInfo?.byteDiffs[bytePos]?.changed);
                        return (
                          <span
                            key={i}
                            onMouseEnter={() => setHoveredByteIndex(bytePos)}
                            onMouseLeave={() => setHoveredByteIndex(null)}
                            className={`inline-block w-2.5 text-center text-[10px] cursor-pointer rounded-xs transition-colors shrink-0 ${
                              isHovered
                                ? 'bg-cyan-400 text-black font-bold ring-1 ring-cyan-300'
                                : isDiffChanged
                                ? 'text-amber-200 font-bold bg-amber-500/30 ring-1 ring-amber-400/60'
                                : isTreeHighlighted
                                ? 'text-cyan-200 font-bold bg-cyan-500/25'
                                : 'text-zinc-400 hover:text-white hover:bg-zinc-800'
                            }`}
                            title={`偏移 0x${bytePos.toString(16).toUpperCase()}: ${b >= 32 && b <= 126 ? String.fromCharCode(b) : '·'}`}
                          >
                            {b >= 32 && b <= 126 ? String.fromCharCode(b) : '·'}
                          </span>
                        );
                      })}
                    </div>
                  </div>
                );
              })}
            </div>
          </div>

          {/* 底部悬停字节状态指示条 */}
          <div className="shrink-0 pt-2 border-t border-rf-border/60 text-[11px] text-zinc-400 flex items-center justify-between">
            {hoveredByteIndex !== null && fullBytes[hoveredByteIndex] !== undefined ? (
              <div className="flex items-center space-x-2 text-cyan-300 font-mono">
                <Info className="w-3.5 h-3.5" />
                <span>
                  偏移 0x{hoveredByteIndex.toString(16).padStart(4, '0').toUpperCase()} ({hoveredByteIndex}) ·
                  值 0x{fullBytes[hoveredByteIndex].toString(16).padStart(2, '0').toUpperCase()} ({fullBytes[hoveredByteIndex]})
                  {diffInfo?.byteDiffs[hoveredByteIndex]?.changed && (
                    <span className="text-amber-300 font-bold ml-2">
                      [与基准差异: 原值 0x{diffInfo.byteDiffs[hoveredByteIndex].refByte !== undefined ? diffInfo.byteDiffs[hoveredByteIndex].refByte!.toString(16).padStart(2, '0').toUpperCase() : '--'}, 翻转 {diffInfo.byteDiffs[hoveredByteIndex].bitFlips} bit]
                    </span>
                  )}
                  {currentByteSpan && ` · 所属: ${currentByteSpan.label}`}
                </span>
              </div>
            ) : activeHighlight ? (
              <div className="text-cyan-300 flex items-center space-x-1.5">
                <span className="w-2 h-2 rounded-full bg-cyan-400 animate-pulse" />
                <span>高亮区间: {activeHighlight.label} (字节 {activeHighlight.start}..{activeHighlight.end})</span>
              </div>
            ) : (
              <span className="text-zinc-500">将鼠标悬停在左侧协议树或上方 Hex 字节上可联动高亮</span>
            )}
          </div>
        </div>
      </div>
    </div>
  );
};
