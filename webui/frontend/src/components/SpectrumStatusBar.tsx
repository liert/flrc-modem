import type { SpectrumFrame, SpectrumStatusResponse } from '../types';

interface SpectrumStatusBarProps {
  status: SpectrumStatusResponse;
  frame: SpectrumFrame | null;
  sampleRate: number;
  nFft: number;
}

const metricClass = 'flex min-w-0 items-center gap-1.5 whitespace-nowrap leading-6';
const valueClass = 'min-w-0 truncate font-bold';

export function SpectrumStatusBar({ status, frame, sampleRate, nFft }: SpectrumStatusBarProps) {
  const clock = status.actual_master_clock == null ? '—' : `${(status.actual_master_clock / 1e6).toFixed(3)} MHz`;
  const rbw = `${((status.rbw_hz ?? sampleRate / nFft) / 1e3).toFixed(2)} kHz`;

  return (
    <header aria-label="频谱运行信息" className="spec-metrics shrink-0 text-xs tabular-nums text-zinc-400">
      <div className="spec-metrics-row flex h-6">
          <div className={metricClass} title={`主时钟 MCR: ${clock}`}>
            <span className="shrink-0">MCR:</span><span className={`${valueClass} text-cyan-400`}>{clock}</span>
          </div>
          <div className={metricClass} title={`RBW 分辨率: ${rbw}`}>
            <span className="shrink-0">RBW:</span><span className={`${valueClass} text-emerald-400`}>{rbw}</span>
          </div>
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
    </header>
  );
}
