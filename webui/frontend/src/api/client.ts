import {
  ArtifactItem,
  CommandDefinition,
  DeviceInfo,
  LogLine,
  PipelineDefinition,
  PresetConfig,
  RunRecord,
  UIEvent,
} from '../types';

const API_BASE = '/api/v1';

async function request<T>(endpoint: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${endpoint}`, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      ...options?.headers,
    },
  });
  if (!res.ok) {
    let errMsg = `HTTP ${res.status}`;
    try {
      const errData = await res.json();
      errMsg = errData.detail || errMsg;
    } catch {
      // ignore
    }
    throw new Error(errMsg);
  }
  return res.json();
}

export const api = {
  // 命令
  getCommands: () => request<CommandDefinition[]>('/commands'),
  getCommand: (id: string) => request<CommandDefinition>(`/commands/${id}`),

  // 流水线
  getPipelines: () => request<PipelineDefinition[]>('/pipelines'),
  getPipeline: (id: string) => request<PipelineDefinition>(`/pipelines/${id}`),

  // 任务
  getRuns: (limit = 100, offset = 0) => request<RunRecord[]>(`/runs?limit=${limit}&offset=${offset}`),
  getRun: (id: string) => request<RunRecord>(`/runs/${id}`),
  createRun: (command_id: string, parameters: Record<string, any>, enable_ui_events = true) =>
    request<RunRecord>('/runs', {
      method: 'POST',
      body: JSON.stringify({ command_id, parameters, enable_ui_events }),
    }),
  stopRun: (id: string) =>
    request<{ run_id: string; stopped: boolean }>(`/runs/${id}/stop`, { method: 'POST' }),
  getRunLogs: (id: string, limit = 2000) => request<LogLine[]>(`/runs/${id}/logs?limit=${limit}`),
  getRunEvents: (id: string) => request<UIEvent[]>(`/runs/${id}/events`),
  getRunArtifacts: (id: string) => request<ArtifactItem[]>(`/runs/${id}/artifacts`),

  // 设备
  getDevices: () => request<DeviceInfo[]>('/devices'),

  // 预设
  getPresets: (command_id?: string) =>
    request<PresetConfig[]>(`/configs${command_id ? `?command_id=${command_id}` : ''}`),
  savePreset: (name: string, command_id: string, values: Record<string, any>, id?: string) =>
    request<PresetConfig>('/configs', {
      method: 'POST',
      body: JSON.stringify({ name, command_id, values, id }),
    }),

  // 产物与信号分析
  getAllArtifacts: () => request<ArtifactItem[]>('/artifacts'),
  getSpectrum: (
    path: string,
    sample_rate = 2500000,
    center_freq = 2480000000,
    iq_format = 'cs8',
    n_fft = 1024,
    offset_samples = 0
  ) =>
    request<{
      center_frequency: number;
      sample_rate: number;
      n_fft: number;
      peak_frequency_hz: number;
      peak_power_db: number;
      points: [number, number][];
    }>(
      `/analysis/spectrum?path=${encodeURIComponent(path)}&sample_rate=${sample_rate}&center_freq=${center_freq}&iq_format=${iq_format}&n_fft=${n_fft}&offset_samples=${offset_samples}`
    ),
  getConstellation: (path: string, iq_format = 'cs8', max_points = 2000, offset_samples = 0) =>
    request<{
      total_samples: number;
      rendered_points: number;
      mean_amplitude: number;
      evm_estimate_percent: number;
      points: [number, number][];
    }>(
      `/analysis/constellation?path=${encodeURIComponent(path)}&iq_format=${iq_format}&max_points=${max_points}&offset_samples=${offset_samples}`
    ),
  getDecodedFrames: (path: string, limit = 500) =>
    request<{ total: number; frames: any[] }>(
      `/analysis/frames?path=${encodeURIComponent(path)}&limit=${limit}`
    ),
};
