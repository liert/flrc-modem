import { create } from 'zustand';
import type { DecodedFrameRecord } from '../types';

export interface SpectrumDemodRecord {
  id: string;
  sessionId: string;
  frame: DecodedFrameRecord;
  protocol?: string;
}

interface SpectrumDemodState {
  records: SpectrumDemodRecord[];
  addRecords: (records: SpectrumDemodRecord[]) => void;
  clearRecords: () => void;
}

// Shared by the live spectrum and the standalone parser. Survives navigation,
// but stays bounded like the spectrum packet list (no browser persistence).
export const useSpectrumDemodStore = create<SpectrumDemodState>(set => ({
  records: [],
  addRecords: records => set(state => ({ records: [...records, ...state.records].slice(0, 500) })),
  clearRecords: () => set({ records: [] }),
}));
