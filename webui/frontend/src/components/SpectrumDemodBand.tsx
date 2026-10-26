import React, { useEffect, useRef, useState } from 'react';

interface Props {
  frequencyMhz: number;
  bandwidthHz: number;
  visibleMinMhz: number;
  visibleMaxMhz: number;
  captureMinMhz: number;
  captureMaxMhz: number;
  active: boolean;
  disabled?: boolean;
  onCommit: (frequencyMhz: number) => void;
}

export function SpectrumDemodBand({ frequencyMhz, bandwidthHz, visibleMinMhz, visibleMaxMhz,
  captureMinMhz, captureMaxMhz, active, disabled, onCommit }: Props) {
  const plotRef = useRef<HTMLDivElement>(null);
  const dragRef = useRef<{ pointerId: number; element: HTMLDivElement; startX: number;
    startFrequency: number; mhzPerPixel: number; frequency: number } | null>(null);
  const animationRef = useRef<number>();
  const [previewMhz, setPreviewMhz] = useState<number | null>(null);
  const halfBandMhz = bandwidthHz / 2e6;
  // Both edges must remain visible and inside the strict capture boundaries.
  const lower = (Math.ceil((Math.max(captureMinMhz, visibleMinMhz) + halfBandMhz) * 1e6) + 1) / 1e6;
  const upper = (Math.floor((Math.min(captureMaxMhz, visibleMaxMhz) - halfBandMhz) * 1e6) - 1) / 1e6;
  const span = visibleMaxMhz - visibleMinMhz;
  const fits = lower <= upper;
  const available = fits && span > 0 && !disabled;
  const constrain = (frequency: number) => Math.round(Math.max(lower, Math.min(upper, frequency)) * 1e6) / 1e6;
  const center = fits ? constrain(previewMhz ?? frequencyMhz) : (visibleMinMhz + visibleMaxMhz) / 2;
  const drawnHalfBandMhz = Math.min(halfBandMhz, span / 2);

  const cancelDrag = () => {
    if (animationRef.current !== undefined) cancelAnimationFrame(animationRef.current);
    animationRef.current = undefined;
    const drag = dragRef.current;
    dragRef.current = null;
    if (drag?.element.hasPointerCapture(drag.pointerId)) drag.element.releasePointerCapture(drag.pointerId);
  };

  useEffect(() => {
    cancelDrag();
    setPreviewMhz(null);
    return cancelDrag;
  }, [visibleMinMhz, visibleMaxMhz, captureMinMhz, captureMaxMhz, bandwidthHz, disabled]);

  if (!(span > 0)) return null;
  return <div ref={plotRef} className="absolute inset-0 overflow-hidden pointer-events-none" aria-label="解调通带位置">
    <div role="slider" tabIndex={available ? 0 : -1} aria-label="解调中心频率"
      aria-orientation="horizontal" aria-disabled={!available}
      aria-valuemin={Math.round((fits ? lower : center) * 1e6)} aria-valuemax={Math.round((fits ? upper : center) * 1e6)}
      aria-valuenow={Math.round(center * 1e6)} aria-valuetext={`${center.toFixed(6)} MHz，带宽 ${bandwidthHz / 1000} kHz`}
      title={fits ? '左右拖动调谐，松手应用；方向键微调 1 kHz，Shift 10 kHz，Alt 100 Hz' : '采样带宽不足以容纳解调通带，请提高采样率'}
      className={`absolute inset-y-0 border-x-2 outline-none select-none touch-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-amber-300 ${available ? 'pointer-events-auto cursor-grab active:cursor-grabbing' : 'pointer-events-none opacity-50'} ${active ? 'border-amber-400 bg-amber-400/10' : 'border-amber-600/80 bg-amber-400/5'}`}
      style={{ left: `${(center - drawnHalfBandMhz - visibleMinMhz) / span * 100}%`, width: `${2 * drawnHalfBandMhz / span * 100}%` }}
      onPointerDown={event => {
        if (!available || event.button !== 0) return;
        event.preventDefault(); event.stopPropagation();
        const bounds = plotRef.current?.getBoundingClientRect();
        if (!bounds?.width) return;
        event.currentTarget.focus();
        event.currentTarget.setPointerCapture(event.pointerId);
        dragRef.current = { pointerId: event.pointerId, element: event.currentTarget,
          startX: event.clientX, startFrequency: center, frequency: center, mhzPerPixel: span / bounds.width };
      }}
      onPointerMove={event => {
        const drag = dragRef.current;
        if (!drag || drag.pointerId !== event.pointerId) return;
        event.stopPropagation();
        drag.frequency = constrain(drag.startFrequency + (event.clientX - drag.startX) * drag.mhzPerPixel);
        if (animationRef.current !== undefined) return;
        animationRef.current = requestAnimationFrame(() => {
          animationRef.current = undefined;
          if (dragRef.current) setPreviewMhz(dragRef.current.frequency);
        });
      }}
      onPointerUp={event => {
        const drag = dragRef.current;
        if (!drag || drag.pointerId !== event.pointerId) return;
        event.stopPropagation();
        const frequency = constrain(drag.startFrequency + (event.clientX - drag.startX) * drag.mhzPerPixel);
        cancelDrag(); setPreviewMhz(null);
        if (frequency !== frequencyMhz) onCommit(frequency);
      }}
      onPointerCancel={() => { cancelDrag(); setPreviewMhz(null); }}
      onLostPointerCapture={() => { cancelDrag(); setPreviewMhz(null); }}
      onKeyDown={event => {
        if (!available || !['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
        event.preventDefault(); event.stopPropagation();
        const step = event.altKey ? .0001 : event.shiftKey ? .01 : .001;
        const next = event.key === 'Home' ? lower : event.key === 'End' ? upper
          : frequencyMhz + (event.key === 'ArrowLeft' ? -step : step);
        const frequency = constrain(next);
        if (frequency !== frequencyMhz) onCommit(frequency);
      }}>
      <div aria-hidden="true" className="absolute inset-y-0 left-1/2 border-l border-dashed border-amber-300/80 pointer-events-none" />
      <span className="absolute top-1 left-1/2 -translate-x-1/2 whitespace-nowrap rounded border border-amber-600 bg-zinc-950/95 px-2 py-1 font-mono text-xs text-amber-200 shadow-sm">
        {center.toFixed(6)} MHz{!fits ? ' · 带宽不足' : active ? '' : ' · 待启用'}
      </span>
    </div>
  </div>;
}
