import { create } from 'zustand';
import type { ScanReport } from '../types';

export interface ScanDraft {
  scanMode: 'offline' | 'hackrf' | 'usrp';
  iqPath: string;
  sampleRate: number;
  usrpSampleRate: number;
  masterClockRate: number | null;
  usrpArgs: string;
  usrpChannel: number;
  usrpAntenna: string;
  centerFreqMhz: number;
  iqFormat: 'cs8' | 'cu8' | 'cf32';
  duration: number;
  startFreqMhz: number;
  stopFreqMhz: number;
  stepBwMhz: number;
  dwell: number;
  passes: number;
  refineDwell: number;
  settleSeconds: number;
  discardSeconds: number;
  gain: number;
  thresholdDb: number;
  minSnr: number;
  selectedBitrate: number | undefined;
  noFlrc: boolean;
  showAdvanced: boolean;
}

export interface ScanSnapshot {
  id: string;
  name: string;
  timestamp: string;
  timestampMs: number;
  report: ScanReport;
  mode: 'offline' | 'hackrf' | 'usrp';
  noiseFloorDbfs: number;
  totalChannels: number;
  flrcCount: number;
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
  // 快照持久化与历史热力走势
  snapshots: ScanSnapshot[];
  activeSnapshotId: string | null;
  saveSnapshot: (name?: string) => void;
  loadSnapshot: (id: string) => void;
  deleteSnapshot: (id: string) => void;
  clearSnapshots: () => void;
}

const SNAPSHOT_STORAGE_KEY = 'flrc_scan_snapshots';
const MAX_SNAPSHOTS = 20;

const loadStoredSnapshots = (): ScanSnapshot[] => {
  try {
    if (typeof window !== 'undefined' && window.localStorage) {
      const raw = window.localStorage.getItem(SNAPSHOT_STORAGE_KEY);
      if (raw) return JSON.parse(raw);
    }
  } catch {
    // Ignore storage parse errors
  }
  return [];
};

const persistSnapshots = (snapshots: ScanSnapshot[]) => {
  try {
    if (typeof window !== 'undefined' && window.localStorage) {
      window.localStorage.setItem(SNAPSHOT_STORAGE_KEY, JSON.stringify(snapshots.slice(0, MAX_SNAPSHOTS)));
    }
  } catch {
    // Ignore storage quota errors
  }
};

// Keep inputs and the last successful result across page unmounts in this session.
export const useScanStore = create<ScanState>((set) => ({
  draft: {
    scanMode: 'offline',
    iqPath: 'outputs/rx.cs8',
    sampleRate: 10_000_000,
    usrpSampleRate: 12_500_000,
    masterClockRate: 125_000_000,
    usrpArgs: '',
    usrpChannel: 0,
    usrpAntenna: '',
    centerFreqMhz: 2405.0,
    iqFormat: 'cs8',
    duration: 0.5,
    startFreqMhz: 2400.0,
    stopFreqMhz: 2483.5,
    stepBwMhz: 10.0,
    dwell: 0.05,
    passes: 2,
    refineDwell: 0.25,
    settleSeconds: 0.01,
    discardSeconds: 0.002,
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
  snapshots: loadStoredSnapshots(),
  activeSnapshotId: null,
  saveSnapshot: (customName) => set((state) => {
    if (!state.report) return state;
    const now = new Date();
    const timeStr = now.toLocaleTimeString();
    const snapshot: ScanSnapshot = {
      id: typeof crypto !== 'undefined' && crypto.randomUUID ? crypto.randomUUID() : `snap_${Date.now()}`,
      name: customName || `盲扫快照 ${timeStr} (${state.report.channels.length}信道)`,
      timestamp: now.toISOString(),
      timestampMs: now.getTime(),
      report: state.report,
      mode: state.draft.scanMode,
      noiseFloorDbfs: state.report.noise_floor_dbfs,
      totalChannels: state.report.total_channels,
      flrcCount: state.report.flrc_channels_count,
    };
    const nextSnapshots = [snapshot, ...state.snapshots.filter(s => s.id !== snapshot.id)].slice(0, MAX_SNAPSHOTS);
    persistSnapshots(nextSnapshots);
    return { snapshots: nextSnapshots, activeSnapshotId: snapshot.id };
  }),
  loadSnapshot: (id) => set((state) => {
    const found = state.snapshots.find(s => s.id === id);
    if (!found) return state;
    return {
      report: found.report,
      selectedChannelFrequencyHz: found.report.channels[0]?.frequency_hz ?? null,
      activeSnapshotId: id,
    };
  }),
  deleteSnapshot: (id) => set((state) => {
    const next = state.snapshots.filter(s => s.id !== id);
    persistSnapshots(next);
    return {
      snapshots: next,
      activeSnapshotId: state.activeSnapshotId === id ? null : state.activeSnapshotId,
    };
  }),
  clearSnapshots: () => set(() => {
    persistSnapshots([]);
    return { snapshots: [], activeSnapshotId: null };
  }),
}));
