import {
  ArtifactItem,
  AnalysisContext,
  CommandDefinition,
  DeviceInfo,
  LogLine,
  PipelineDefinition,
  RunRecord,
  TemplateItem,
  UIEvent,
  DecodedFrameRecord,
  SignalWindow,
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
      const detail = errData.detail;
      errMsg = typeof detail === 'string' ? detail : Array.isArray(detail)
        ? detail.map(item => `${item.loc?.join('.') || '参数'}: ${item.msg}`).join('; ') : errMsg;
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
  preflight: (id: string, parameters: Record<string, any>) => request<import('../types').MessageIOProfile>(`/commands/${id}/preflight`, {
    method: 'POST', body: JSON.stringify({ command_id: id, parameters }),
  }),
  getSession: (id: string) => request<import('../types').DuplexSessionState>(`/runs/${encodeURIComponent(id)}/session`),
  sendMessage: (id: string, payload: Record<string, any>) => request<{ request_id: string; status: string }>(`/runs/${encodeURIComponent(id)}/messages`, {
    method: 'POST', body: JSON.stringify(payload),
  }),

  // 流水线
  getPipelines: () => request<PipelineDefinition[]>('/pipelines'),
  getPipeline: (id: string) => request<PipelineDefinition>(`/pipelines/${id}`),

  // 任务
  getRuns: (limit = 100, offset = 0, status = '', search = '') =>
    request<RunRecord[]>(`/runs?limit=${limit}&offset=${offset}${status ? `&status=${encodeURIComponent(status)}` : ''}${search ? `&search=${encodeURIComponent(search)}` : ''}`),
  renameRun: (id: string, name: string) => request<RunRecord>(`/runs/${encodeURIComponent(id)}`, {
    method: 'PATCH', body: JSON.stringify({ name }),
  }),
  deleteRuns: (run_ids: string[]) => request<{ deleted_run_ids: string[]; files_retained: boolean }>('/runs/delete', {
    method: 'POST', body: JSON.stringify({ run_ids }),
  }),
  getRun: (id: string) => request<RunRecord>(`/runs/${id}`),
  getRunAnalysis: (id: string) => request<AnalysisContext>(`/runs/${id}/analysis`),
  createRun: (command_id: string, parameters: Record<string, any>, enable_ui_events = true, name = '') =>
    request<RunRecord>('/runs', {
      method: 'POST',
      body: JSON.stringify({ command_id, parameters, enable_ui_events, name }),
    }),
  stopRun: (id: string) =>
    request<{ run_id: string; stopped: boolean }>(`/runs/${id}/stop`, { method: 'POST' }),
  getRunLogs: (id: string, limit = 2000) => request<LogLine[]>(`/runs/${id}/logs?limit=${limit}`),
  getRunEvents: (id: string) => request<UIEvent[]>(`/runs/${id}/events`),
  getRunArtifacts: (id: string) => request<ArtifactItem[]>(`/runs/${id}/artifacts`),

  // 设备
  getDevices: () => request<DeviceInfo[]>('/devices'),

  // 模板 (templates/ 目录下的 TOML 物理配置与 JSON 发送报文)
  getTemplates: (command_id?: string) =>
    request<TemplateItem[]>(`/templates${command_id ? `?command_id=${command_id}` : ''}`),
  getTemplate: (name: string) => request<TemplateItem>(`/templates/${encodeURIComponent(name)}`),
  saveTemplate: (name: string, content: string) =>
    request<TemplateItem>(`/templates/${encodeURIComponent(name)}`, {
      method: 'POST',
      body: JSON.stringify({ content }),
    }),
  deleteTemplate: (name: string) =>
    request<{ name: string; deleted: boolean }>(`/templates/${encodeURIComponent(name)}`, {
      method: 'DELETE',
    }),

  // 产物与信号分析
  getAllArtifacts: () => request<ArtifactItem[]>('/artifacts'),
  getSignalWindow: (runId: string, nFft: number, windowMs: number, frameTime?: number, signal?: AbortSignal) =>
    request<SignalWindow>(`/analysis/window?run_id=${encodeURIComponent(runId)}&n_fft=${nFft}&window_ms=${windowMs}${frameTime === undefined ? '' : `&frame_time_s=${frameTime}`}`, { signal }),
  getSpectrum: (
    path: string,
    sample_rate = 2500000,
    center_freq = 2480000000,
    iq_format = 'cs8',
    n_fft = 1024,
    offset_samples = 0,
    runId?: string
  ) =>
    request<{
      center_frequency: number;
      sample_rate: number;
      n_fft: number;
      peak_frequency_hz: number;
      peak_power_db: number;
      points: [number, number][];
    }>(
      `/analysis/spectrum?path=${encodeURIComponent(path)}&sample_rate=${sample_rate}&center_freq=${center_freq}&iq_format=${iq_format}&n_fft=${n_fft}&offset_samples=${offset_samples}${runId ? `&run_id=${encodeURIComponent(runId)}` : ''}`
    ),
  getConstellation: (path: string, iq_format = 'cs8', max_points = 2000, offset_samples = 0, runId?: string) =>
    request<{
      total_samples: number;
      rendered_points: number;
      mean_amplitude: number;
      evm_estimate_percent: number;
      points: [number, number][];
    }>(
      `/analysis/constellation?path=${encodeURIComponent(path)}&iq_format=${iq_format}&max_points=${max_points}&offset_samples=${offset_samples}${runId ? `&run_id=${encodeURIComponent(runId)}` : ''}`
    ),
  getDecodedFrames: (path: string, limit = 500, runId?: string) =>
    request<{ total: number; frames: DecodedFrameRecord[] }>(
      `/analysis/frames?path=${encodeURIComponent(path)}&limit=${limit}${runId ? `&run_id=${encodeURIComponent(runId)}` : ''}`
    ),
};
