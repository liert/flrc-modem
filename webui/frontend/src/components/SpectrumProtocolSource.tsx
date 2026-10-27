import React, { useMemo, useState } from 'react';
import { ChevronLeft, ChevronRight, Radio } from 'lucide-react';
import { useSpectrumDemodStore } from '../stores/useSpectrumDemodStore';
import { useWorkstationStore, type ProtocolDraft } from '../stores/useWorkstationStore';
import { spectrumProtocolDraft } from '../utils/protocolSources';
import { hexText } from '../utils/hex';

export function SpectrumProtocolSource({ onSelect }: { onSelect: (draft: ProtocolDraft) => void }) {
  const records = useSpectrumDemodStore(state => state.records);
  const draft = useWorkstationStore(state => state.protocolDraft);
  const [filter, setFilter] = useState('');
  const entries = useMemo(() => records.map(record => ({
    record,
    label: [
      Number.isFinite(record.frame.time_s) ? `${record.frame.time_s.toFixed(4)} s` : '未知时间',
      Number.isFinite(record.frame.frequency_hz) ? `${(record.frame.frequency_hz / 1e6).toFixed(3)} MHz` : '',
      record.frame.application?.protocol || record.protocol,
      record.frame.application?.command_name,
      record.frame.crc_ok === true ? 'CRC OK' : record.frame.crc_ok === false ? 'CRC ERR' : '',
    ].filter(Boolean).join(' · '),
  })), [records]);
  const term = filter.trim().toLowerCase();
  const filtered = entries.filter(({ record, label }) =>
    !term || `${label} ${record.sessionId} ${record.id}`.toLowerCase().includes(term)
    || record.frame.payload_hex?.replace(/\s/g, '').toLowerCase().includes(term));
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
          {records.length} / 500 帧
        </span>
      </div>
      <p>与频谱页同步更新，最新记录在前；暂停列表更新、清空和保留上限与频谱页一致。</p>
      {records.length > 0 ? (
        <>
          <div>
            <label htmlFor="protocol-spectrum-filter" className="pi-label">
              筛选实时解调记录（{filtered.length} / {records.length}）
            </label>
            <input
              id="protocol-spectrum-filter"
              type="search"
              className="pi-input"
              value={filter}
              placeholder="按协议、命令、时间、频率、CRC 或 Hex 筛选…"
              onChange={event => setFilter(event.target.value)}
            />
          </div>
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
            {filtered.slice(0, 80).map(({ record, label }, index) => {
              const rawHex = record.frame.payload_hex?.replace(/\s/g, '') || '';
              return (
                <button
                  key={record.id}
                  type="button"
                  className="pi-drawer-item"
                  data-active={index === active}
                  disabled={!rawHex}
                  onClick={() => select(index)}
                >
                  <span className="flex items-center justify-between gap-2 w-full min-w-0">
                    <span className="text-xs font-medium text-zinc-200 truncate">{label}</span>
                    {rawHex && (
                      <span className="text-[10px] font-mono text-zinc-500 shrink-0">
                        {Math.floor(rawHex.length / 2)} B
                      </span>
                    )}
                  </span>
                  <span className="text-[11px] font-mono text-cyan-300/90 truncate w-full">
                    {hexText(record.frame.payload_hex)}
                  </span>
                </button>
              );
            })}
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
