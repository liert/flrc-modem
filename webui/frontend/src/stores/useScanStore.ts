import { create } from 'zustand';
import type { ScanReport } from '../types';

export interface ScanDraft {
  scanMode: 'offline' | 'hackrf' | 'usrp';
  iqPath: string;
  sampleRate: number;
  centerFreqMhz: number;
  iqFormat: 'cs8' | 'cu8' | 'cf32';
  duration: number;
  startFreqMhz: number;
  stopFreqMhz: number;
  stepBwMhz: number;
  dwell: number;
  gain: number;
  thresholdDb: number;
  minSnr: number;
  selectedBitrate: number | undefined;
  noFlrc: boolean;
  showAdvanced: boolean;
}

interface ScanState {
  draft: ScanDraft;
  updateDraft: (patch: Partial<ScanDraft>) => void;
  report: ScanReport | null;
  reportScanId: string | null;
  beginScan: (scanId: string) => void;
  selectedChannelFrequencyHz: number | null;
  setReport: (report: ScanReport) => void;
  updateReport: (report: ScanReport, scanId?: string) => void;
  selectChannel: (frequencyHz: number) => void;
}

// Keep inputs and the last successful result across page unmounts in this session.
export const useScanStore = create<ScanState>((set) => ({
  draft: {
    scanMode: 'offline',
    iqPath: 'outputs/rx.cs8',
    sampleRate: 10_000_000,
    centerFreqMhz: 2405.0,
    iqFormat: 'cs8',
    duration: 0.5,
    startFreqMhz: 2400.0,
    stopFreqMhz: 2483.5,
    stepBwMhz: 10.0,
    dwell: 0.05,
    gain: 30.0,
    thresholdDb: 10.0,
    minSnr: 6.0,
    selectedBitrate: undefined,
    noFlrc: false,
    showAdvanced: false,
  },
  updateDraft: (patch) => set(state => ({ draft: { ...state.draft, ...patch } })),
  report: null,
  reportScanId: null,
  beginScan: (scanId) => set({ reportScanId: scanId }),
  selectedChannelFrequencyHz: null,
  setReport: (report) => set({
    report,
    selectedChannelFrequencyHz: report.channels[0]?.frequency_hz ?? null,
  }),
  updateReport: (report, scanId) => set(state => {
    if (scanId && scanId !== state.reportScanId) return state;
    const previous = state.selectedChannelFrequencyHz;
    const selected = previous === null ? null : report.channels.find(channel => channel.frequency_hz === previous)
      ?? report.channels.filter(channel => Math.abs(channel.frequency_hz - previous) <= 120000)
        .sort((a, b) => Math.abs(a.frequency_hz - previous) - Math.abs(b.frequency_hz - previous))[0];
    return { report, selectedChannelFrequencyHz: selected?.frequency_hz ?? report.channels[0]?.frequency_hz ?? null };
  }),
  selectChannel: (frequencyHz) => set((state) => (
    state.report?.channels.some(channel => channel.frequency_hz === frequencyHz)
      ? { selectedChannelFrequencyHz: frequencyHz }
      : state
  )),
}));
