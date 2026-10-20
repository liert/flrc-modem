import { create } from 'zustand';
import { LogLine, UIEvent } from '../types';

interface WebSocketState {
  connected: boolean;
  activeRunId: string | null;
  logs: Record<string, LogLine[]>;
  stageStates: Record<string, Record<string, 'waiting' | 'running' | 'succeeded' | 'failed'>>;
  stageMetrics: Record<string, Record<string, any>>;
  stageProgress: Record<string, Record<string, number>>;
  connect: (runId?: string) => void;
  disconnect: () => void;
  addLog: (runId: string, log: LogLine) => void;
  handleEvent: (event: UIEvent) => void;
  clearRunData: (runId: string) => void;
}

let socket: WebSocket | null = null;
let pingInterval: number | null = null;

export const useWebSocketStore = create<WebSocketState>((set, get) => ({
  connected: false,
  activeRunId: null,
  logs: {},
  stageStates: {},
  stageMetrics: {},
  stageProgress: {},

  connect: (runId?: string) => {
    if (socket) {
      socket.close();
      socket = null;
    }
    if (pingInterval) {
      clearInterval(pingInterval);
      pingInterval = null;
    }

    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const host = window.location.host;
    const url = runId
      ? `${protocol}//${host}/ws/runs/${runId}`
      : `${protocol}//${host}/ws/events`;

    try {
      socket = new WebSocket(url);

      socket.onopen = () => {
        set({ connected: true, activeRunId: runId || null });
        pingInterval = window.setInterval(() => {
          if (socket && socket.readyState === WebSocket.OPEN) {
            socket.send('ping');
          }
        }, 10000);
      };

      socket.onmessage = (event) => {
        if (event.data === 'pong') return;
        try {
          const data = JSON.parse(event.data);
          if (data.type === 'task.log') {
            get().addLog(data.run_id, {
              stream: data.stream,
              line: data.line,
              timestamp: data.timestamp,
            });
          } else {
            get().handleEvent(data as UIEvent);
          }
        } catch {
          // ignore
        }
      };

      socket.onclose = () => {
        set({ connected: false });
        if (pingInterval) {
          clearInterval(pingInterval);
          pingInterval = null;
        }
      };

      socket.onerror = () => {
        set({ connected: false });
      };
    } catch {
      set({ connected: false });
    }
  },

  disconnect: () => {
    if (socket) {
      socket.close();
      socket = null;
    }
    if (pingInterval) {
      clearInterval(pingInterval);
      pingInterval = null;
    }
    set({ connected: false, activeRunId: null });
  },

  addLog: (runId: string, log: LogLine) => {
    set((state) => {
      const current = state.logs[runId] || [];
      const updated = current.length > 3000 ? [...current.slice(-2500), log] : [...current, log];
      return {
        logs: { ...state.logs, [runId]: updated },
      };
    });
  },

  handleEvent: (event: UIEvent) => {
    const { run_id, type, stage_id, payload } = event;
    if (!run_id) return;

    set((state) => {
      const currentStates = state.stageStates[run_id] || {};
      const currentMetrics = state.stageMetrics[run_id] || {};
      const currentProgress = state.stageProgress[run_id] || {};

      let newStates = { ...currentStates };
      let newMetrics = { ...currentMetrics };
      let newProgress = { ...currentProgress };

      if (stage_id) {
        if (type === 'stage.started') {
          newStates[stage_id] = 'running';
        } else if (type === 'stage.completed') {
          newStates[stage_id] = 'succeeded';
        } else if (type === 'stage.failed') {
          newStates[stage_id] = 'failed';
        } else if (type === 'stage.progress') {
          if (typeof payload?.progress === 'number') {
            newProgress[stage_id] = payload.progress;
          }
        } else if (type === 'metric.sample') {
          newMetrics[stage_id] = { ...(newMetrics[stage_id] || {}), ...payload };
        }
      }

      return {
        stageStates: { ...state.stageStates, [run_id]: newStates },
        stageMetrics: { ...state.stageMetrics, [run_id]: newMetrics },
        stageProgress: { ...state.stageProgress, [run_id]: newProgress },
      };
    });
  },

  clearRunData: (runId: string) => {
    set((state) => {
      const logs = { ...state.logs };
      const stageStates = { ...state.stageStates };
      const stageMetrics = { ...state.stageMetrics };
      const stageProgress = { ...state.stageProgress };
      delete logs[runId];
      delete stageStates[runId];
      delete stageMetrics[runId];
      delete stageProgress[runId];
      return { logs, stageStates, stageMetrics, stageProgress };
    });
  },
}));
