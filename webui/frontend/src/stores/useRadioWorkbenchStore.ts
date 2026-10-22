import { useCallback } from 'react';
import { create } from 'zustand';

export interface RadioFrameDraft {
  protocol: 'dk32s' | 'datalink';
  payloadHex: string; syncWord: string; codingRate: '1/2' | '3/4' | '1';
  headerType: 'variable' | 'fixed'; bitRate: number; sampleRate: number;
  rampTimeUs: number; amplitude: number;
}
interface TransmitSettings {
  mode: 'preview' | 'live' | 'export'; sdr: 'usrp' | 'hackrf'; centerFreq: number;
  gain: number; repeat: number; gapMs: number; outputPath: string;
}
interface SyncSettings {
  mode: 'live' | 'simulate' | 'playback'; operation: 'listen' | 'duplex';
  sdr: 'usrp' | 'hackrf'; usrpArgs: string; rfConfig: string; initialFreq: number; sampleRate: number;
  gain: number; txGain: number; antenna: string; timedHopping: boolean;
  duration: number; outputPath: string; dwellMs: number; payloadBytes: number;
  simDriftPpm: number; simCycles: number;
}
interface RadioWorkbenchState {
  frame: RadioFrameDraft;
  transmit: TransmitSettings;
  sync: SyncSettings;
  linked: boolean;
  duplexRunId: string | null;
  setDuplexRunId: (id: string) => void;
  setFrame: (patch: Partial<RadioFrameDraft>) => void;
  setTransmit: (patch: Partial<TransmitSettings>) => void;
  setSync: (patch: Partial<SyncSettings>) => void;
  linkToSync: (target: 'simulate' | 'duplex') => void;
  unlink: () => void;
}

export const useRadioWorkbenchStore = create<RadioWorkbenchState>((set) => ({
  frame: { protocol: 'dk32s', payloadHex: '0102030405060708090a', syncWord: '2bd36a07', codingRate: '3/4',
    headerType: 'variable', bitRate: 325000, sampleRate: 2000000, rampTimeUs: 12, amplitude: 0.7 },
  transmit: { mode: 'preview', sdr: 'hackrf', centerFreq: 2471660000, gain: 30,
    repeat: 1, gapMs: 10, outputPath: 'outputs/generated.iq' },
  sync: { mode: 'simulate', operation: 'listen', sdr: 'usrp', usrpArgs: 'addr=192.168.10.2',
    rfConfig: '',
    initialFreq: 2471660000, sampleRate: 2500000, gain: 30, txGain: 30, antenna: 'RX2',
    timedHopping: false, duration: 0, outputPath: 'outputs/sync_frames.jsonl',
    dwellMs: 50, payloadBytes: 33, simDriftPpm: 12.5, simCycles: 2 },
  linked: false,
  duplexRunId: null,
  setDuplexRunId: id => set({ duplexRunId: id }),
  setFrame: patch => set(state => ({ frame: { ...state.frame, ...patch } })),
  setTransmit: patch => set(state => ({ transmit: { ...state.transmit, ...patch } })),
  setSync: patch => set(state => ({ sync: { ...state.sync, ...patch } })),
  linkToSync: target => set(state => ({
    linked: true,
    sync: { ...state.sync, mode: target === 'duplex' ? 'live' : 'simulate',
      operation: target === 'duplex' ? 'duplex' : state.sync.operation,
      sdr: target === 'duplex' ? 'usrp' : state.sync.sdr,
      initialFreq: state.transmit.centerFreq, sampleRate: state.frame.sampleRate, txGain: state.transmit.gain },
  })),
  unlink: () => set({ linked: false }),
}));

export function useFrameField<K extends keyof RadioFrameDraft>(key: K) {
  const value = useRadioWorkbenchStore(state => state.frame[key]);
  const update = useCallback((value: RadioFrameDraft[K]) => {
    useRadioWorkbenchStore.getState().setFrame({ [key]: value } as Pick<RadioFrameDraft, K>);
  }, [key]);
  return [value, update] as const;
}
export function useTransmitField<K extends keyof TransmitSettings>(key: K) {
  const value = useRadioWorkbenchStore(state => state.transmit[key]);
  const update = useCallback((value: TransmitSettings[K]) => {
    useRadioWorkbenchStore.getState().setTransmit({ [key]: value } as Pick<TransmitSettings, K>);
  }, [key]);
  return [value, update] as const;
}
export function useSyncField<K extends keyof SyncSettings>(key: K) {
  const value = useRadioWorkbenchStore(state => state.sync[key]);
  const update = useCallback((value: SyncSettings[K]) => {
    useRadioWorkbenchStore.getState().setSync({ [key]: value } as Pick<SyncSettings, K>);
  }, [key]);
  return [value, update] as const;
}
