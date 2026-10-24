import { hexText } from '../utils/hex';
import React, { useCallback, useEffect, useRef, useState } from 'react';
import { useRadioWorkbenchStore, type SyncSettings } from '../stores/useRadioWorkbenchStore';
import { recommendedSampleRate, sampleRateCompatible } from '../utils/sampleRates';
import { SyncTemplateSelector } from './SyncTemplateSelector';
import { UsrpSampleRateSelect } from './UsrpSampleRateSelect';

const inputStyle = 'h-9 w-full min-w-0 rounded-lg border border-rf-border bg-rf-bg px-2.5 text-xs font-mono text-zinc-200 focus:border-indigo-400 focus:outline-none';

function NumericParameter({ name, label, value, scale = 1, min = 0, integer = false, optional = false, onChange, onError }: {
  name: string; label: string; value: number | null; scale?: number; min?: number; integer?: boolean; optional?: boolean;
  onChange: (value: number | null) => void; onError: (name: string, message: string) => void;
}) {
  const [text, setText] = useState(value === null ? '' : String(value / scale));
  const focused = useRef(false);
  useEffect(() => {
    if (!focused.current) {
      setText(value === null ? '' : String(value / scale));
      onError(name, '');
    }
  }, [value, scale, name, onError]);
  return <label className="min-w-0 space-y-1 text-xs text-zinc-400" htmlFor={`sync-${name}`}>
    <span className="block">{label}</span>
    <input id={`sync-${name}`} type="number" step={integer ? 1 : 'any'} min={min / scale} value={text}
      className={inputStyle} onFocus={() => { focused.current = true; }} onBlur={() => { focused.current = false; }}
      onChange={event => {
        const raw = event.target.value;
        setText(raw);
        if (raw === '' && optional) { onChange(null); onError(name, ''); return; }
        const numeric = event.target.valueAsNumber * scale;
        if (raw === '' || !Number.isFinite(numeric) || numeric < min || (integer && !Number.isInteger(numeric))) {
          onError(name, `${label}需要填写${integer ? '整数' : '有效数值'}，且不小于 ${min / scale}`);
          return;
        }
        onError(name, '');
        onChange(numeric);
      }} />
  </label>;
}

export function SyncLiveParameters({ onValidationChange }: { onValidationChange: (message: string) => void }) {
  const settings = useRadioWorkbenchStore(state => state.sync);
  const frame = useRadioWorkbenchStore(state => state.frame);
  const setSync = useRadioWorkbenchStore(state => state.setSync);
  const setFrame = useRadioWorkbenchStore(state => state.setFrame);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const onError = useCallback((name: string, message: string) => setErrors(previous =>
    previous[name] === message ? previous : { ...previous, [name]: message }), []);
  useEffect(() => { setErrors({}); }, [settings.sdr, settings.operation]);
  useEffect(() => { onValidationChange(Object.values(errors).filter(Boolean).join('；')); }, [errors, onValidationChange]);
  const duplex = settings.operation === 'duplex';
  const number = (name: keyof SyncSettings, label: string, options: { scale?: number; min?: number; integer?: boolean; optional?: boolean } = {}) =>
    <NumericParameter key={`${settings.sdr}:${settings.operation}:${name}`} name={name} label={label} value={settings[name] as number | null} {...options} onError={onError}
      onChange={value => setSync({ [name]: name === 'initialFreq' && value !== null ? Math.round(value) : value })} />;
  const text = (name: keyof SyncSettings, label: string, placeholder = '') => <label className="min-w-0 space-y-1 text-xs text-zinc-400">
    <span className="block">{label}</span><input type="text" className={inputStyle} value={String(settings[name])} placeholder={placeholder}
      onChange={event => setSync({ [name]: event.target.value })} />
  </label>;

  return <div className="space-y-5">
    <fieldset className="space-y-3">
      <legend className="mb-2 text-sm font-semibold text-zinc-200">设备与采集</legend>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <label className="space-y-1 text-xs text-zinc-400"><span className="block">工作方式</span>
          <select className={inputStyle} value={settings.operation} onChange={event => setSync({ operation: event.target.value as 'listen' | 'duplex', ...(event.target.value === 'duplex' ? { sdr: 'usrp' as const } : {}) })}>
            <option value="listen">仅监听同步</option><option value="duplex">同步收发会话（USRP）</option>
          </select>
        </label>
        <label className="space-y-1 text-xs text-zinc-400"><span className="block">SDR 设备</span>
          <select className={inputStyle} value={settings.sdr} onChange={event => {
            const sdr = event.target.value as 'usrp' | 'hackrf';
            const clock = settings.masterClockRate;
            const sampleRate = sdr === 'hackrf' ? 2000000 : clock !== null && !sampleRateCompatible(clock, settings.sampleRate)
              ? recommendedSampleRate(clock) >= 4 * frame.bitRate ? recommendedSampleRate(clock) : clock / 10 : settings.sampleRate;
            setSync({ sdr, sampleRate });
          }}>
            <option value="usrp">USRP</option><option value="hackrf" disabled={duplex}>HackRF</option>
          </select>
        </label>
        {number('initialFreq', '初始监听频率 (MHz)', { scale: 1e6, min: 1 })}
        {settings.sdr === 'usrp' ? <div className="min-w-0 space-y-1 text-xs text-zinc-400">
          <label htmlFor="sync-sampleRate" className="block">采样率（按主时钟分组）</label>
          <UsrpSampleRateSelect id="sync-sampleRate" className={inputStyle} masterClockRate={settings.masterClockRate}
            sampleRate={settings.sampleRate} minRate={4 * frame.bitRate} onChange={value => {
              onError('sampleRate', '');
              setSync(value);
            }} />
          <p className="leading-5 text-zinc-500">跨组选择会同时更新主时钟；实际速率以设备回读为准。</p>
        </div> : number('sampleRate', '采样率 (MSps)', { scale: 1e6, min: 1 })}
        {number('gain', '接收增益 (dB)')}
        {settings.sdr === 'usrp' ? <>
          {text('usrpArgs', 'USRP 定位参数', '默认 addr=192.168.10.2')}
          {number('channel', '接收通道', { integer: true })}
          {text('antenna', '接收天线端口', 'RX2')}
          <label className="space-y-1 text-xs text-zinc-400"><span className="block">主时钟 (MHz)</span>
            <select className={inputStyle} value={settings.masterClockRate ?? ''} onChange={event => {
              const clock = event.target.value === '' ? null : Number(event.target.value);
              setSync({ masterClockRate: clock, ...(clock !== null && (!sampleRateCompatible(clock, settings.sampleRate) || settings.sampleRate < 4 * frame.bitRate)
                ? { sampleRate: recommendedSampleRate(clock) >= 4 * frame.bitRate ? recommendedSampleRate(clock) : clock / 10 } : {}) });
            }}>
              <option value="">设备默认</option><option value={125000000}>125</option><option value={122880000}>122.88</option><option value={153600000}>153.6</option>
            </select>
          </label>
          <label className="flex items-center gap-2 self-end py-2 text-xs text-zinc-300"><input type="checkbox" checked={settings.timedHopping}
            onChange={event => setSync({ timedHopping: event.target.checked })} />锁定后启用硬件定时跳频</label>
        </> : <>
          {text('serial', 'HackRF 序列号（可留空）')}
          {number('deviceIndex', 'HackRF 设备索引（可留空）', { integer: true, optional: true })}
        </>}
      </div>
    </fieldset>
    <fieldset className="space-y-3 border-t border-rf-border pt-3">
      <legend className="mb-2 text-sm font-semibold text-zinc-200">物理层与同步</legend>
      <SyncTemplateSelector kind="physical" />
      <SyncTemplateSelector kind="rf" />
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <label className="space-y-1 text-xs text-zinc-400"><span className="block">同步协议</span>
          <select className={inputStyle} value={frame.protocol} onChange={event => setFrame({ protocol: event.target.value as 'dk32s' | 'datalink' })}>
            <option value="dk32s">DK32S</option><option value="datalink" disabled>DataLink（尚无同步模型）</option>
          </select>
        </label>
        <label className="space-y-1 text-xs text-zinc-400"><span className="block">同步字 (Hex，可留空)</span>
          <input className={inputStyle} value={hexText(frame.syncWord)} placeholder="留空从射频配置读取或自动捕获" onChange={event => setFrame({ syncWord: event.target.value })} />
        </label>
        <label className="space-y-1 text-xs text-zinc-400"><span className="block">空口码率 (kbps)</span>
          <select className={inputStyle} value={frame.bitRate} onChange={event => setFrame({ bitRate: Number(event.target.value) })}>
            {[260000, 325000, 520000, 650000, 1040000, 1300000].map(rate => <option key={rate} value={rate}>{rate / 1000}</option>)}
          </select>
        </label>
        <label className="space-y-1 text-xs text-zinc-400"><span className="block">纠错编码率 (CR)</span>
          <select className={inputStyle} value={frame.codingRate} onChange={event => setFrame({ codingRate: event.target.value as typeof frame.codingRate })}>
            {['1/2', '3/4', '1'].map(rate => <option key={rate} value={rate}>{rate}</option>)}
          </select>
        </label>
        <label className="space-y-1 text-xs text-zinc-400"><span className="block">高斯成形 BT</span>
          <select className={inputStyle} value={settings.bt} onChange={event => setSync({ bt: Number(event.target.value) })}>
            {[0, 0.5, 1].map(bt => <option key={bt} value={bt}>{bt === 0 ? '关闭' : bt}</option>)}
          </select>
        </label>
        <label className="space-y-1 text-xs text-zinc-400"><span className="block">硬件帧头</span>
          <select className={inputStyle} value={frame.headerType} onChange={event => setFrame({ headerType: event.target.value as typeof frame.headerType })}>
            <option value="variable">变长头（DK32S）</option><option value="fixed" disabled>固定头（同步模型不支持）</option>
          </select>
        </label>
      </div>
      <p className="text-xs leading-5 text-zinc-500">同步字、码率、编码率与物理帧工作台共享。采样率及主时钟以硬件实际回读为准。</p>
    </fieldset>
    <fieldset className="space-y-3 border-t border-rf-border pt-3">
      <legend className="mb-2 text-sm font-semibold text-zinc-200">{duplex ? '收发会话' : '运行与记录'}</legend>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        {duplex ? <>
          {number('txGain', '发送增益 (dB)')}{number('syncTimeout', '同步等待上限 (秒)', { min: 0.001 })}
          {number('replyTimeout', '回复等待上限 (秒)', { min: 0.001 })}
          <label className="flex items-center gap-2 text-xs text-zinc-300"><input type="checkbox" checked={settings.txInvertSync} onChange={event => setSync({ txInvertSync: event.target.checked })} />发送同步字反转</label>
          <label className="flex items-center gap-2 text-xs text-zinc-300"><input type="checkbox" checked={settings.replyInvertSync} onChange={event => setSync({ replyInvertSync: event.target.checked })} />回复同步字反转</label>
        </> : <>{number('duration', '持续时间 (秒，0 表示手动停止)')}{text('outputPath', '解调帧输出路径')}</>}
      </div>
    </fieldset>
  </div>;
}
