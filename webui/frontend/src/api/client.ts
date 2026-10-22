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
  QuickScanRequest,
  ScanReport,
  ExportPlanRequest,
  SyncSimulateRequest,
  SyncSimulateResponse,
  SyncParsedTimelineResponse,
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
  getSession: (id: string, attempt?: number) => request<import('../types').DuplexSessionState>(`/runs/${encodeURIComponent(id)}/session${attempt === undefined ? '' : `?attempt=${attempt}`}`),
  sendMessage: (id: string, payload: Record<string, any>) => request<{ request_id: string; status: string }>(`/runs/${encodeURIComponent(id)}/messages`, {
    method: 'POST', body: JSON.stringify(payload),
  }),
  controlPlan: (id: string, payload: { action: 'start_plan'; plan: import('../types').DuplexPlanDefinition } | { action: 'stop_plan'; plan_id: string }) =>
    request<{ plan_id: string; status: string }>(`/runs/${encodeURIComponent(id)}/plan`, {
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
  getRun: (id: string, attempt?: number) => request<RunRecord>(`/runs/${id}${attempt === undefined ? '' : `?attempt=${attempt}`}`),
  getRunAttempts: (id: string) => request<RunRecord[]>(`/runs/${id}/attempts`),
  getRunAnalysis: (id: string) => request<AnalysisContext>(`/runs/${id}/analysis`),
  createRun: (command_id: string, parameters: Record<string, any>, enable_ui_events = true, name = '') =>
    request<RunRecord>('/runs', {
      method: 'POST',
      body: JSON.stringify({ command_id, parameters, enable_ui_events, name }),
    }),
  stopRun: (id: string) =>
    request<{ run_id: string; stopped: boolean }>(`/runs/${id}/stop`, { method: 'POST' }),
  rerunRun: (id: string) =>
    request<RunRecord>(`/runs/${encodeURIComponent(id)}/rerun`, { method: 'POST' }),
  getRunLogs: (id: string, limit = 2000, attempt?: number) => request<LogLine[]>(`/runs/${id}/logs?limit=${limit}${attempt === undefined ? '' : `&attempt=${attempt}`}`),
  getRunEvents: (id: string, attempt?: number) => request<UIEvent[]>(`/runs/${id}/events${attempt === undefined ? '' : `?attempt=${attempt}`}`),
  getRunArtifacts: (id: string, attempt?: number) => request<ArtifactItem[]>(`/runs/${id}/artifacts${attempt === undefined ? '' : `?attempt=${attempt}`}`),

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

  // 实时频谱 (Spectrum)
  startSpectrum: (req: import('../types').SpectrumStartRequest) =>
    request<import('../types').SpectrumStatusResponse>('/spectrum/start', {
      method: 'POST',
      body: JSON.stringify(req),
    }),
  stopSpectrum: (session_id?: string) =>
    request<import('../types').SpectrumStatusResponse>(`/spectrum/stop${session_id ? `?session_id=${encodeURIComponent(session_id)}` : ''}`, {
      method: 'POST',
    }),
  pauseSpectrum: (session_id: string) =>
    request<import('../types').SpectrumStatusResponse>(`/spectrum/pause?session_id=${encodeURIComponent(session_id)}`, {
      method: 'POST',
    }),
  resumeSpectrum: (session_id: string) =>
    request<import('../types').SpectrumStatusResponse>(`/spectrum/resume?session_id=${encodeURIComponent(session_id)}`, {
      method: 'POST',
    }),
  updateSpectrum: (session_id: string, req: import('../types').SpectrumUpdateRequest) =>
    request<Record<string, any>>(`/spectrum/update?session_id=${encodeURIComponent(session_id)}`, {
      method: 'POST',
      body: JSON.stringify(req),
    }),
  getSpectrumStatus: () =>
    request<import('../types').SpectrumStatusResponse>('/spectrum/status'),
  startSpectrumRecord: (sessionId: string, durationS?: number | null, prefix?: string) =>
    request<import('../types').SpectrumRecordStatus>(`/spectrum/record/start?session_id=${encodeURIComponent(sessionId)}`, {
      method: 'POST',
      body: JSON.stringify({ duration_s: durationS, filename_prefix: prefix }),
    }),
  stopSpectrumRecord: (sessionId: string) =>
    request<import('../types').SpectrumRecordStatus>(`/spectrum/record/stop?session_id=${encodeURIComponent(sessionId)}`, {
      method: 'POST',
    }),
  getSpectrumRecordStatus: (sessionId?: string) =>
    request<import('../types').SpectrumRecordStatus>(`/spectrum/record/status${sessionId ? `?session_id=${encodeURIComponent(sessionId)}` : ''}`),
// 盲扫与信道感知 (Scan)
  quickScan: (req: QuickScanRequest) =>
    request<ScanReport>('/scan/quick', {
      method: 'POST',
      body: JSON.stringify(req),
    }),
  exportScanPlan: (req: ExportPlanRequest) =>
    request<{ path: string; content: string }>('/scan/export-plan', {
      method: 'POST',
      body: JSON.stringify(req),
    }),
  // FHSS + TDD 同步 (Sync)
  simulateSyncSchedule: (req: SyncSimulateRequest) =>
    request<SyncSimulateResponse>('/sync/preview-schedule', {
      method: 'POST',
      body: JSON.stringify(req),
    }),
  parseSyncTimeline: (req: { run_id?: string; file_path?: string }) =>
    request<SyncParsedTimelineResponse>('/sync/parse-timeline', {
      method: 'POST',
      body: JSON.stringify(req),
    }),
};