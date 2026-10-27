import { recommendedFilterCutoff } from './physicalTemplate';

/** Match the live receiver's template defaults and explicit filter overrides. */
export function resolveDemodFilterCutoff(template: { bit_rate?: number; filter_cutoff?: number } | undefined,
  protocol: string, bitRate?: number): number {
  const cutoff = Number(template?.filter_cutoff ?? (!template && protocol === 'h12'
    ? 750000 : recommendedFilterCutoff(bitRate ?? template?.bit_rate ?? 325000)));
  return Number.isFinite(cutoff) && cutoff > 0 ? cutoff : 350000;
}

/** Keep a relative demodulator offset and its full passband inside the viewport. */
export function fitDemodBand(offsetHz: number, sampleRate: number, bandwidthHz: number,
  zoom: { start: number; end: number },
  view = { minOffsetHz: -sampleRate / 2, maxOffsetHz: sampleRate / 2 }) {
  const minOffsetHz = Math.max(-sampleRate / 2, view.minOffsetHz);
  const maxOffsetHz = Math.min(sampleRate / 2, view.maxOffsetHz);
  const viewSpanHz = maxOffsetHz - minOffsetHz;
  const available = Number.isFinite(viewSpanHz) && viewSpanHz > bandwidthHz + 2;
  if (!available) return { offsetHz: 0, zoom: { start: 0, end: 100 }, available: false };

  // Widen an overly narrow zoom instead of changing the physical filter width.
  const minSpan = Math.min(100, (bandwidthHz + 4) / viewSpanHz * 100);
  const span = Math.min(100, Math.max(minSpan, zoom.end - zoom.start));
  const middle = (zoom.start + zoom.end) / 2;
  const keepZoom = span === zoom.end - zoom.start && zoom.start >= 0 && zoom.end <= 100;
  const start = keepZoom ? zoom.start : Math.max(0, Math.min(100 - span, middle - span / 2));
  const end = keepZoom ? zoom.end : start + span;
  const lower = Math.ceil(minOffsetHz + viewSpanHz * start / 100 + bandwidthHz / 2) + 1;
  const upper = Math.floor(minOffsetHz + viewSpanHz * end / 100 - bandwidthHz / 2) - 1;
  return {
    offsetHz: Math.max(lower, Math.min(upper, Math.round(offsetHz))),
    zoom: { start, end },
    available: true,
  };
}
