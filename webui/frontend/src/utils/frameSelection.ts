import type { DecodedFrameRecord } from '../types';

export interface FrameSelection { runId: string; key: string }

export function frameKey(frame: DecodedFrameRecord): string {
  return JSON.stringify([frame.time_s, frame.frequency_hz, frame.sync_word_hex, frame.payload_hex]);
}

export function frameLinkable(frame: DecodedFrameRecord): boolean {
  return Number.isFinite(frame.time_s) && frame.time_s >= 0;
}

export function linkedFrameIndex(selection: FrameSelection | null, runId: string, frames: DecodedFrameRecord[] = []): number {
  if (!selection || selection.runId !== runId) return -1;
  return frames.findIndex(frame => frameLinkable(frame) && frameKey(frame) === selection.key);
}
