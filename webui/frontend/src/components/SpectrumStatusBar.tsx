import type { SpectrumFrame, SpectrumStatusResponse } from '../types';

interface SpectrumStatusBarProps {
  status: SpectrumStatusResponse;
  frame: SpectrumFrame | null;
  sdr: string;
  centerFreqMhz: number;
  sampleRate: number;
  nFft: number;
}

const metricClass = 'flex min-w-0 items-center gap-1.5 whitespace-nowrap leading-6';
const valueClass = 'min-w-0 truncate font-bold';

export function SpectrumStatusBar({ status, frame, sdr, centerFreqMhz, sampleRate, nFft }: SpectrumStatusBarProps) {
  const device = [status.device_name || sdr.toUpperCase(), status.device_id].filter(Boolean).join(' · ');
  const center = `${((status.actual_center_freq ?? centerFreqMhz * 1e6) / 1e6).toFixed(4)} MHz`;
  const rate = `${((status.actual_sample_rate ?? sampleRate) / 1e6).toFixed(3)} MSps`;
  const clock = status.actual_master_clock == null ? '—' : `${(status.actual_master_clock / 1e6).toFixed(3)} MHz`;
  const rbw = `${((status.rbw_hz ?? sampleRate / nFft) / 1e3).toFixed(2)} kHz`;

  return (
    <header aria-label="频谱运行信息" className="shrink-0 overflow-x-auto border-b border-rf-border bg-rf-surface/80 px-3 py-2 text-xs font-mono tabular-nums text-zinc-400">
      <div className="min-w-[800px] space-y-1">
        <div className="grid h-6 grid-cols-[minmax(0,1.4fr)_repeat(4,minmax(0,1fr))] gap-4">
          <div className={metricClass} title={device}>
            <span className="shrink-0">设备:</span><span className={`${valueClass} text-cyan-300`}>{device}</span>
          </div>
          <div className={metricClass} title={`实际 RF 中心: ${center}`}>
            <span className="shrink-0">中心:</span><span className={`${valueClass} text-rf-text`}>{center}</span>
          </div>
          <div className={metricClass} title={`实际采样率: ${rate}`}>
            <span className="shrink-0">采样率:</span><span className={`${valueClass} text-rf-text`}>{rate}</span>
          </div>
          <div className={metricClass} title={`主时钟 MCR: ${clock}`}>
            <span className="shrink-0">MCR:</span><span className={`${valueClass} text-cyan-400`}>{clock}</span>
          </div>
          <div className={metricClass} title={`RBW 分辨率: ${rbw}`}>
            <span className="shrink-0">RBW:</span><span className={`${valueClass} text-emerald-400`}>{rbw}</span>
          </div>
        </div>
        <div className="grid h-6 grid-cols-[minmax(0,2fr)_repeat(4,minmax(0,1fr))] gap-4">
          <div className={metricClass}>
            <span className="shrink-0">峰值:</span>
            <span className="min-w-0 truncate font-bold text-cyan-300">{frame ? `${(frame.peak_freq / 1e6).toFixed(3)} MHz` : '— MHz'}</span>
            <span className="min-w-0 truncate font-bold text-emerald-400">{frame ? `${frame.peak_power.toFixed(1)} dBFS` : '— dBFS'}</span>
          </div>
          <div className={metricClass}><span className="shrink-0">刷新率:</span><strong className={`${valueClass} text-rf-text`}>{frame?.fps ?? status.fps} FPS</strong></div>
          <div className={metricClass}><span className="shrink-0">频谱帧:</span><strong className={`${valueClass} text-rf-text`}>{frame?.sequence ?? 0}</strong></div>
          <div className={metricClass}><span className="shrink-0">溢出:</span><strong className={`${valueClass} ${status.hw_overflows > 0 ? 'text-amber-400' : 'text-rf-text'}`}>{status.hw_overflows}</strong></div>
          <div className={metricClass}><span className="shrink-0">丢帧:</span><strong className={`${valueClass} ${status.client_dropped_frames > 0 ? 'text-amber-400' : 'text-rf-text'}`}>{status.client_dropped_frames}</strong></div>
        </div>
      </div>
    </header>
  );
}
