import { create } from 'zustand';
import { LogLine, UIEvent } from '../types';

interface WebSocketState {
  connected: boolean;
  activeRunId: string | null;
  latestEvent: UIEvent | null;
  logs: Record<string, LogLine[]>;
  attempts: Record<string, number>;
  acceptAttempt: (runId: string, attempt?: number) => boolean;
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
let reconnectTimer: number | null = null;
let generation = 0;

export const useWebSocketStore = create<WebSocketState>((set, get) => ({
  connected: false,
  activeRunId: null,
  latestEvent: null,
  logs: {},
  attempts: {},
  stageStates: {},
  stageMetrics: {},
  stageProgress: {},

  connect: (runId?: string) => {
    get().disconnect();
    const connectionGeneration = generation;
    let attempts = 0;
    set({ activeRunId: runId || null });

    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const host = window.location.host;
    const url = runId
      ? `${protocol}//${host}/ws/runs/${encodeURIComponent(runId)}`
      : `${protocol}//${host}/ws/events`;

    const scheduleReconnect = () => {
      if (generation !== connectionGeneration) return;
      reconnectTimer = window.setTimeout(openSocket, Math.min(30000, 1000 * 2 ** attempts++));
    };
    const openSocket = () => {
      reconnectTimer = null;
      if (generation !== connectionGeneration) return;
      try {
      const current = new WebSocket(url);
      socket = current;

      current.onopen = () => {
        if (socket !== current || generation !== connectionGeneration) return;
        attempts = 0;
        set({ connected: true, activeRunId: runId || null });
        pingInterval = window.setInterval(() => {
          if (socket === current && current.readyState === WebSocket.OPEN) {
            current.send('ping');
          }
        }, 10000);
      };

      current.onmessage = (event) => {
        if (socket !== current || generation !== connectionGeneration) return;
        if (event.data === 'pong') return;
        try {
          const data = JSON.parse(event.data);
          if (!data || typeof data.run_id !== 'string') return;
          if (data.type === 'task.log' && typeof data.line === 'string') {
            get().addLog(data.run_id, {
              stream: data.stream,
              line: data.line,
              timestamp: data.timestamp,
              attempt: data.attempt,
            });
          } else {
            get().handleEvent(data as UIEvent);
          }
        } catch {
          // ignore
        }
      };

      current.onclose = () => {
        if (socket !== current || generation !== connectionGeneration) return;
        socket = null;
        set({ connected: false });
        if (pingInterval) {
          clearInterval(pingInterval);
          pingInterval = null;
        }
        scheduleReconnect();
      };

      current.onerror = () => {
        if (socket === current) current.close();
      };
    } catch {
      set({ connected: false });
      scheduleReconnect();
    }
    };
    openSocket();
  },

  disconnect: () => {
    generation++;
    if (reconnectTimer !== null) {
      clearTimeout(reconnectTimer);
      reconnectTimer = null;
    }
    if (socket) {
      const previous = socket;
      socket = null;
      previous.close();
    }
    if (pingInterval) {
      clearInterval(pingInterval);
      pingInterval = null;
    }
    set({ connected: false, activeRunId: null });
  },

  addLog: (runId: string, log: LogLine) => {
    if (!get().acceptAttempt(runId, log.attempt)) return;
    set((state) => {
      const current = state.logs[runId] || [];
      const updated = [...current.slice(-2999), log];
      const logs = { ...state.logs, [runId]: updated };
      const attempts = { ...state.attempts };
      // 全局订阅长期运行时，只保留最近 20 个任务的日志缓存。
      for (const id of Object.keys(logs).slice(0, -20)) {
        delete logs[id];
        delete attempts[id];
      }
      return {
        logs, attempts,
      };
    });
  },

  handleEvent: (event: UIEvent) => {
    const { run_id, type, stage_id, payload } = event;
    if (run_id && !get().acceptAttempt(run_id, event.attempt)) return;
    // Duplex status has no stage_id. Cache it alongside sync metrics so the
    // workbench can restore its current state after navigating away and back.
    if (run_id && type === 'duplex.status') {
      set(state => ({ stageMetrics: { ...state.stageMetrics,
        [run_id]: { ...state.stageMetrics[run_id], live_sync: payload } } }));
      return;
    }
    if (run_id && ['artifact.created', 'task.completed', 'task.state', 'task.updated', 'task.deleted'].includes(type)) {
      set({ latestEvent: event });
      return;
    }
    if (!run_id || !stage_id || !['stage.started', 'stage.completed', 'stage.failed', 'stage.progress', 'metric.sample'].includes(type)) return;

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
      const attempts = { ...state.attempts };
      delete logs[runId];
      delete stageStates[runId];
      delete stageMetrics[runId];
      delete stageProgress[runId];
      delete attempts[runId];
      return { logs, stageStates, stageMetrics, stageProgress, attempts };
    });
  },

  acceptAttempt: (runId: string, attempt = 1) => {
    const current = get().attempts[runId] || 1;
    if (attempt < current) return false;
    if (attempt > current) get().clearRunData(runId);
    if (attempt !== get().attempts[runId]) {
      set(state => ({ attempts: { ...state.attempts, [runId]: attempt } }));
    }
    return true;
  },
}));
