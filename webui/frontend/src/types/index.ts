export type JobStatus = 'QUEUED' | 'RUNNING' | 'SUCCEEDED' | 'FAILED' | 'CANCELLED';

export type DeviceStatus = 'online' | 'offline' | 'busy' | 'error';

export interface CommandParameter {
  name: string;
  type: 'string' | 'number' | 'integer' | 'boolean' | 'choice' | 'path' | 'list_number' | 'list_string';
  description: string;
  cli_flag: string;
  required: boolean;
  default?: any;
  choices?: any[];
  min_value?: number;
  max_value?: number;
  category: string;
}

export interface CommandDefinition {
  id: string;
  name: string;
  category: string;
  description: string;
  subcommand: string;
  requires_usrp: boolean;
  requires_sdr: boolean;
  pipeline_id?: string;
  parameters: CommandParameter[];
  group_id?: string;
  group_name?: string;
  group_description?: string;
  mode_order?: number;
}

export interface RunRecord {
  id: string;
  attempt?: number;
  attempt_created_at?: string;
  name?: string;
  command_id: string;
  command_args: Record<string, any>;
  cli_argv: string[];
  status: JobStatus;
  created_at: string;
  started_at?: string;
  completed_at?: string;
  exit_code?: number;
  error?: string;
  requires_usrp: boolean;
  sdr_type?: string;
  pipeline_id?: string;
  summary: Record<string, any>;
}

export interface DeviceInfo {
  device_type: 'usrp' | 'hackrf';
  id: string;
  name: string;
  details: Record<string, any>;
  status: DeviceStatus;
  locked_by_run_id?: string;
  error_message?: string;
}

export interface MessageIOProfile {
  frequency: number;
  sample_rate: number;
  bit_rate: number;
  coding_rate: string;
  bt: number;
  sync: string | null;
  tx_invert_sync?: boolean;
  reply_invert_sync?: boolean;
  tx_sync_word?: string | null;
  reply_sync_word?: string | null;
  bandwidth_hz: number;
  payload_bytes: number | null;
  packet: { header_type: string; crc_bytes: number; crc_initial: number; payload_lengths: number[] };
  device: { type: string; selection: string; channel?: number; antenna?: string };
  timing_model: string | null;
  rate_status: string;
  rf_config?: { source: string; hopping_table: number[]; sync: string | null; frequency: number; warnings: string[] } | null;
}

export interface DuplexResult {
  request_id: string;
  status: string;
  timestamp?: string;
  tx_completed?: boolean;
  reply_matched?: boolean;
  tx_payload_hex?: string;
  invert_sync?: boolean;
  reply_invert_sync?: boolean;
  tx_sync_word_hex?: string | null;
  reply_sync_word_hex?: string | null;
  tx_hw_time?: number;
  tx_slot?: number;
  tx_frequency_hz?: number;
  rtt_ms?: number | null;
  rx_reply?: { payload_hex: string };
  error?: string;
  plan_id?: string;
  source?: 'periodic' | 'step';
  item_id?: string;
  cycle?: number;
  scheduled_offset_ms?: number;
  started_offset_ms?: number;
  lateness_ms?: number;
  skipped_periods?: number;
}

export interface DuplexPlanItem {
  id: string;
  message?: Record<string, unknown>;
  payload_hex?: string;
  wait_reply: boolean;
  invert_sync?: boolean;
  reply_invert_sync?: boolean;
}

export interface DuplexPlanDefinition {
  plan_id: string;
  periodic: (DuplexPlanItem & { interval_ms: number; count: number })[];
  steps: (DuplexPlanItem & { delay_ms: number; on_failure: 'stop' | 'continue' })[];
  cycles: number;
}

export interface DuplexPlanState {
  plan_id: string;
  status: string;
  executed: number;
  succeeded: number;
  failed: number;
  skipped: number;
  step_index?: number;
  cycles_completed?: number;
  elapsed_ms?: number;
  error?: string;
}

export interface DuplexSessionState {
  status: { state: string; slot?: number; frequency_hz?: number; sample_rate?: number;
    sync_word?: string | null; base_sync_word?: string | null; accepted_sync_words?: string[] | null;
    tx_invert_sync?: boolean; reply_invert_sync?: boolean; reply_sync_word?: string | null;
    receive_frequency_hz?: number; reacquiring?: boolean; reacquisition_count?: number;
    decoded_frames?: number; loss_reason?: string; error?: string } | null;
  config: MessageIOProfile | null;
  ready: { sample_rate: number; device_args: string; channel: number } | null;
  results: DuplexResult[];
  plan?: DuplexPlanState | null;
  plan_definition?: DuplexPlanDefinition | null;
  plan_rejection?: { plan_id: string; action: string; error: string } | null;
}

export interface PipelinePort {
  id: string;
  label: string;
  data_type: string;
}

export interface PipelineNode {
  id: string;
  type: 'source' | 'processor' | 'sink';
  label: string;
  description: string;
  stage_id?: string;
  inputs: PipelinePort[];
  outputs: PipelinePort[];
  position: { x: number; y: number };
  properties?: Record<string, any>;
}

export interface PipelineEdge {
  id: string;
  source: string;
  target: string;
  source_handle?: string;
  target_handle?: string;
  data_type?: string;
  label?: string;
}

export interface PipelineDefinition {
  id: string;
  name: string;
  description: string;
  command_id: string;
  nodes: PipelineNode[];
  edges: PipelineEdge[];
}

export interface ArtifactItem {
  id: number;
  run_id: string;
  attempt?: number;
  stage_id?: string;
  path: string;
  kind: string;
  metadata: Record<string, any>;
  created_at: string;
  file_state?: FileState;
}

export interface FileState {
  status: 'available' | 'missing' | 'overwritten' | 'modified' | 'pending' | 'unverified' | 'not_configured';
  readable: boolean;
  message: string | null;
  revision: string;
  current_run_id: string | null;
}

export interface AnalysisContext {
  run_id: string;
  command_id: string;
  status: JobStatus;
  iq_path: string | null;
  frames_path: string | null;
  sample_rate: number | null;
  center_frequency: number | null;
  iq_format: string | null;
  metadata_error: string | null;
  frame_time_reference?: 'iq_start' | null;
  file_states?: { iq: FileState; frames: FileState };
}

export interface DecodedFrameRecord {
  time_s: number;
  frequency_hz: number;
  payload_hex: string;
  [key: string]: any;
}

export interface SignalWindow {
  offset_samples: number;
  sample_count: number;
  total_samples: number;
  sample_rate: number;
  start_time_s: number;
  end_time_s: number;
  frame_time_s: number | null;
  frame_sample: number | null;
  fft_offset_samples: number;
  fft_sample_count: number;
  requested_n_fft: number;
  truncated: boolean;
  spectrum: {
    points: [number, number][];
    n_fft: number;
    peak_frequency_hz?: number;
    peak_power_db?: number;
    error?: string;
  };
  constellation: {
    points: [number, number][];
    evm_estimate_percent: number | null;
  };
  waveform: { points: [number, number, number, number][] };
}

export interface TemplateItem {
  name: string;
  path: string;
  format: 'toml' | 'json';
  category: 'physical_config' | 'message_payload' | 'rf_config';
  title: string;
  description: string;
  applicable_commands: string[];
  raw_content: string;
  parsed: Record<string, any>;
}

export interface LogLine {
  attempt?: number;
  stream: 'stdout' | 'stderr';
  line: string;
  timestamp: string;
}

export interface UIEvent {
  attempt?: number;
  type: string;
  run_id: string;
  seq: number;
  stage_id?: string;
  timestamp: string;
  payload: Record<string, any>;
}

export interface SpectrumStatusResponse {
  session_id: string | null;
  state: 'idle' | 'running' | 'paused' | 'stopped' | 'error';
  device_type: 'usrp' | 'hackrf' | 'sim' | null;
  device_id: string | null;
  device_name: string | null;
  actual_center_freq: number | null;
  actual_sample_rate: number | null;
  actual_master_clock: number | null;
  n_fft: number;
  rbw_hz: number | null;
  fps: number;
  hw_overflows: number;
  dsp_dropped_frames: number;
  client_dropped_frames: number;
  demod_enabled?: boolean;
  demod_center_freq?: number | null;
  demod_protocol?: string | null;
  demod_physical_template?: string | null;
  demod_packets_count?: number;
  error_code: string | null;
  error_message: string | null;
}

export interface SpectrumStartRequest {
  sdr: 'usrp' | 'hackrf' | 'sim';
  center_freq: number;
  sample_rate: number;
  n_fft?: number;
  window?: 'hann' | 'hamming' | 'blackman' | 'rectangular';
  fft_average?: number;
  fps_limit?: number;
  dynamic_range_db?: number;
  ref_level_dbfs?: number;
  args?: string;
  master_clock_rate?: number;
  gain?: number;
  antenna?: string;
  channel?: number;
  serial?: string;
  device_index?: number;
  lna_gain?: number;
  vga_gain?: number;
  amp?: boolean;
  bias_tee?: boolean;
  demod_enabled?: boolean;
  physical_template?: string | null;
  demod_center_freq?: number;
  bit_rate?: number | null;
  coding_rate?: '1/2' | '3/4' | '1' | null;
  sync_word?: string | null;
  crc_initial?: string | null;
  protocol?: string;
}

export interface SpectrumUpdateRequest {
  center_freq?: number;
  gain?: number;
  lna_gain?: number;
  vga_gain?: number;
  amp?: boolean;
  fft_average?: number;
  fps_limit?: number;
  dynamic_range_db?: number;
  ref_level_dbfs?: number;
  demod_enabled?: boolean;
  physical_template?: string | null;
  demod_center_freq?: number;
  bit_rate?: number | null;
  coding_rate?: '1/2' | '3/4' | '1' | null;
  sync_word?: string | null;
  crc_initial?: string | null;
  protocol?: string;
}

export interface SpectrumRecordStatus {
  is_recording: boolean;
  duration_s?: number | null;
  elapsed_s: number;
  recorded_bytes: number;
  filepath?: string | null;
  run_id?: string | null;
  iq_format?: string | null;
  sample_rate?: number | null;
  center_freq?: number | null;
  samples_count: number;
  completed: boolean;
  error?: string | null;
}

export interface SpectrumFrame {
  session_id: string;
  sequence?: number;
  discontinuity?: boolean;
  timestamp_source?: 'uhd' | 'host_estimate';
  timestamp: number;
  center_freq: number;
  sample_rate: number;
  n_fft: number;
  rbw_hz: number;
  freq_min: number;
  freq_max: number;
  powers: number[];
  peak_freq: number;
  peak_power: number;
  fps: number;
  hw_overflows: number;
  dsp_dropped_frames: number;
  client_dropped_frames: number;
  paused?: boolean;
}

export interface SpectrumDemodPacket {
  type: 'demod_packet';
  session_id: string;
  packet: DecodedFrameRecord;
}

export interface ChannelCandidate {
  frequency_hz: number;
  frequency_mhz: number;
  bandwidth_hz: number;
  bandwidth_khz: number;
  peak_power_dbfs: number;
  snr_db: number;
  noise_floor_dbfs: number;
  burst_count: number;
  duty_cycle: number;
  is_flrc: boolean;
  flrc_confidence: number;
  sync_words: string[];
  bit_rate: number | null;
  protocol_hint: string;
  details?: Record<string, any>;
}

export interface ScanReport {
  timestamp: string;
  scan_mode: string;
  start_freq_mhz: number;
  stop_freq_mhz: number;
  sample_rate_sps: number;
  center_frequency_mhz: number;
  noise_floor_dbfs: number;
  threshold_db: number;
  total_channels: number;
  flrc_channels_count: number;
  channels: ChannelCandidate[];
  device_info: Record<string, any>;
}

export interface QuickScanRequest {
  iq?: string;
  sdr?: 'hackrf' | 'usrp';
  sample_rate?: number;
  center_frequency?: number;
  start_freq?: number;
  stop_freq?: number;
  step_bw?: number;
  dwell?: number;
  duration?: number;
  threshold_db?: number;
  min_snr?: number;
  bit_rate?: number;
  no_flrc?: boolean;
  gain?: number;
  iq_format?: 'cs8' | 'cu8' | 'cf32';
  serial?: string;
  device_index?: number;
  amp?: boolean;
  bias_tee?: boolean;
  args?: string;
  channel?: number;
  antenna?: string;
}

export interface ExportPlanRequest {
  frequencies: number[];
  sample_rate?: number;
  center_frequency?: number;
  bit_rate?: number;
  sync_words?: string[];
  output_path?: string;
}

export interface SyncLiveMetric {
  time_s: number;
  state: 'searching' | 'acquiring' | 'locked' | 'holdover' | 'lost';
  confidence: number;
  slot_index: number;
  frequency_hz: number;
  next_slot_index?: number;
  next_frequency_hz?: number;
  slot_remaining_s?: number;
  tdd_phase: string;
  clock_drift_ppm: number;
  decoded_frames?: number;
  overflow_count?: number;
  anchor_count?: number;
  lock_duration_s?: number;
  residual_s?: number;
  uncertainty_s?: number;
}

export interface SyncSimulateRequest {
  frequencies?: number[];
  dwell_ms?: number;
  tdd_mode?: 'reception_triggered' | 'fixed_periodic';
  payload_bytes?: number;
  bit_rate?: number;
  coding_rate?: '1/2' | '3/4' | '1';
  clock_drift_ppm?: number;
  cycles?: number;
}

export interface SyncSimulateResponse {
  slot_dwell_ms: number;
  total_slots: number;
  cycle_duration_ms: number;
  frequencies: number[];
  timing_breakdown: {
    ground_tx_ms: number;
    turnaround_guard_ms: number;
    air_rx_ms: number;
    slot_guard_ms: number;
    dwell_ms: number;
  };
  simulated_slots: Array<{
    slot_index: number;
    frequency_hz: number;
    start_time_s: number;
    end_time_s: number;
    duration_s: number;
    next_slot_index: number;
    next_frequency_hz: number;
    ground_tx_end_s: number;
    turnaround_end_s: number;
    air_rx_end_s: number;
    state: string;
    confidence: number;
    clock_drift_ppm: number;
  }>;
}

export interface SyncParsedTimelineResponse {
  file_path: string;
  total_frames: number;
  state_counts: Record<string, number>;
  tdd_counts: Record<string, number>;
  slot_counts: Record<number, number>;
  drift_series: Array<{
    time_s: number;
    drift_ppm: number;
    confidence: number;
    state: string;
    slot_index: number;
    frequency_hz: number;
  }>;
  frames: Array<{
    time_s: number;
    cfo_hz: number;
    sample_power_db: number;
    sync?: any;
    application?: any;
  }>;
  latest_snapshot?: any;
}

