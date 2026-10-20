export type JobStatus = 'QUEUED' | 'RUNNING' | 'SUCCEEDED' | 'FAILED' | 'CANCELLED';

export type DeviceStatus = 'online' | 'offline' | 'busy' | 'error';

export interface CommandParameter {
  name: string;
  type: 'string' | 'number' | 'integer' | 'boolean' | 'choice' | 'path' | 'list_number';
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
}

export interface RunRecord {
  id: string;
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
  stage_id?: string;
  path: string;
  kind: string;
  metadata: Record<string, any>;
  created_at: string;
}

export interface TemplateItem {
  name: string;
  path: string;
  format: 'toml' | 'json';
  category: 'physical_config' | 'message_payload';
  title: string;
  description: string;
  applicable_commands: string[];
  raw_content: string;
  parsed: Record<string, any>;
}

export interface LogLine {
  stream: 'stdout' | 'stderr';
  line: string;
  timestamp: string;
}

export interface UIEvent {
  type: string;
  run_id: string;
  seq: number;
  stage_id?: string;
  timestamp: string;
  payload: Record<string, any>;
}
