import React, { useMemo, useState } from 'react';
import { ChevronLeft, ChevronRight, Radio } from 'lucide-react';
import { useSpectrumDemodStore } from '../stores/useSpectrumDemodStore';
import { useWorkstationStore, type ProtocolDraft } from '../stores/useWorkstationStore';
import { spectrumProtocolDraft } from '../utils/protocolSources';
import { hexText } from '../utils/hex';

function formatPacketTime(timeS?: number): string {
  if (typeof timeS !== 'number' || !Number.isFinite(timeS)) return '未知时间';
  if (timeS > 1_000_000_000) {
    const d = new Date(timeS * 1000);
    const hh = String(d.getHours()).padStart(2, '0');
    const mm = String(d.getMinutes()).padStart(2, '0');
    const ss = String(d.getSeconds()).padStart(2, '0');
    const ms = String(Math.floor((timeS % 1) * 1000)).padStart(3, '0');
    return `${hh}:${mm}:${ss}.${ms}`;
  }
  return `${timeS.toFixed(3)} s`;
}

function formatSpacedHex(hex?: string): string {
  const clean = hexText(hex);
  return clean.match(/.{1,2}/g)?.join(' ') || '—';
}

export function SpectrumProtocolSource({ onSelect }: { onSelect: (draft: ProtocolDraft) => void }) {
  const records = useSpectrumDemodStore(state => state.records);
  const draft = useWorkstationStore(state => state.protocolDraft);
  const [filter, setFilter] = useState('');
  const entries = useMemo(() => records.map((record, idx) => {
    const seq = records.length - idx;
    const timeText = formatPacketTime(record.frame.time_s);
    const rawTimeText = Number.isFinite(record.frame.time_s) ? `${record.frame.time_s.toFixed(4)} s` : '';
    const freqText = Number.isFinite(record.frame.frequency_hz)
      ? `${(record.frame.frequency_hz! / 1e6).toFixed(3)} MHz`
      : '';
    const protoText = record.frame.application?.protocol || record.protocol || 'flrc';
    const cmdText = record.frame.application?.command_name || record.frame.application?.type || '原始载荷';
    const crcText = record.frame.crc_ok === true ? 'CRC OK' : record.frame.crc_ok === false ? 'CRC ERR' : '';
    const rawHex = record.frame.payload_hex?.replace(/\s/g, '') || '';
    const byteLen = record.frame.payload_length ?? Math.floor(rawHex.length / 2);
    const label = [
      `#${seq}`,
      protoText,
      cmdText,
      freqText,
      timeText,
      crcText,
    ].filter(Boolean).join(' · ');
    const searchBlob = `${label} ${rawTimeText} ${record.sessionId} ${record.id} ${rawHex}`.toLowerCase();
    return {
      record,
      seq,
      timeText,
      freqText,
      protoText,
      cmdText,
      crcOk: record.frame.crc_ok,
      crcText,
      rawHex,
      byteLen,
      spacedHex: formatSpacedHex(record.frame.payload_hex),
      label,
      searchBlob,
    };
  }), [records]);

  const term = filter.trim().toLowerCase();
  const filtered = entries.filter(entry => !term || entry.searchBlob.includes(term));
  const active = filtered.findIndex(({ record }) => record.id === draft.spectrumOrigin?.packetId
    && record.sessionId === draft.spectrumOrigin?.sessionId);
  const select = (index: number) => {
    const entry = filtered[index];
    const input = entry && spectrumProtocolDraft(entry.record, draft.protocol);
    if (input) onSelect(input);
  };

  return (
    <section className="pi-drawer-section" aria-label="SDR 实时解调记录">
      <div className="pi-drawer-section-head">
        <h3 className="pi-drawer-section-title">
          <Radio aria-hidden="true" />
          <span>SDR 实时解调</span>
        </h3>
        <span className="pi-drawer-badge" data-active={records.length > 0}>
          {term ? `${filtered.length} / ${records.length} 帧` : `${records.length} / 500 帧`}
        </span>
      </div>
      {records.length > 0 ? (
        <>
          <input
            id="protocol-spectrum-filter"
            type="search"
            aria-label="筛选实时解调记录"
            className="pi-input"
            value={filter}
            placeholder="按协议、命令、时间、频率、CRC 或 Hex 筛选…"
            onChange={event => setFilter(event.target.value)}
          />
          <div className="pi-stepper-row">
            <select
              id="protocol-spectrum-frame"
              aria-label="实时解调报文"
              className="pi-input"
              value={active >= 0 ? filtered[active].record.id : ''}
              disabled={!filtered.length}
              onChange={event => select(filtered.findIndex(entry => entry.record.id === event.target.value))}
            >
              <option value="">{filtered.length ? '选择实时解调帧载入' : '没有匹配的实时解调帧'}</option>
              {filtered.map(({ record, label }) => (
                <option key={record.id} value={record.id}>{label}</option>
              ))}
            </select>
            <button
              type="button"
              className="pi-button shrink-0"
              aria-label="上一条实时解调报文"
              disabled={active <= 0}
              onClick={() => select(active - 1)}
            >
              <ChevronLeft aria-hidden="true" />上一条
            </button>
            <button
              type="button"
              className="pi-button shrink-0"
              aria-label="下一条实时解调报文"
              disabled={!filtered.length || active === filtered.length - 1}
              onClick={() => select(active >= 0 ? active + 1 : 0)}
            >
              下一条<ChevronRight aria-hidden="true" />
            </button>
          </div>
          <div className="pi-drawer-list" role="list" aria-label="实时解调报文列表">
            {filtered.slice(0, 80).map((entry, index) => (
              <button
                key={entry.record.id}
                type="button"
                className="pi-drawer-item"
                data-active={index === active}
                disabled={!entry.rawHex}
                onClick={() => select(index)}
              >
                <span className="pi-item-head">
                  <span className="pi-item-identity">
                    <span className="pi-item-seq">#{entry.seq}</span>
                    <span className="pi-item-proto">{entry.protoText}</span>
                    <span className="pi-item-cmd" title={entry.cmdText}>{entry.cmdText}</span>
                  </span>
                  <span className="pi-item-rf">
                    {entry.freqText && <span className="pi-item-freq">{entry.freqText}</span>}
                    {entry.freqText && <span>·</span>}
                    <span>{entry.timeText}</span>
                  </span>
                </span>
                <span className="pi-item-hex-row">
                  <span className="pi-item-hex" title={entry.spacedHex}>
                    {entry.spacedHex}
                  </span>
                  <span className="pi-item-badges">
                    {typeof entry.crcOk === 'boolean' && (
                      <span className="pi-item-crc" data-ok={entry.crcOk}>
                        {entry.crcOk ? 'CRC OK' : 'CRC ERR'}
                      </span>
                    )}
                    {entry.rawHex && (
                      <span className="pi-item-len">{entry.byteLen} B</span>
                    )}
                  </span>
                </span>
              </button>
            ))}
          </div>
        </>
      ) : (
        <p className="pi-drawer-empty">
          尚无实时解调记录。在 SDR 实时频谱中开启实时解调后，报文将自动出现在这里。
        </p>
      )}
    </section>
  );
}
