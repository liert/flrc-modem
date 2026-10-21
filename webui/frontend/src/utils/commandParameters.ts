import type { CommandParameter, TemplateItem } from '../types';

const physicalKeys = [
  'sample_rate', 'center_frequency', 'frequency', 'bit_rate', 'cr', 'bt', 'sync', 'iq_format',
  'header_type', 'crc_bytes', 'crc_initial', 'crc_seed', 'payload_length', 'max_seconds',
  'filter_cutoff', 'chunk_seconds', 'overlap_ms', 'allow_frequency_wrap',
];

const CORE_BASIC_PARAMS = new Set([
  'frequency',
  'freq',
  'sample_rate',
  'rate',
  'duration',
  'repeat',
  'gain',
  'tx_gain',
  'bit_rate',
  'cr',
  'bt',
  'sync',
  'iq',
  'output',
  'output_dir',
  'iq_format',
  'protocol',
  'payload_hex',
  'payload_file',
  'sdr',
  'timed_hopping',
  'session',
  'action',
  'tx_serial',
  'rx_serial',
]);

export function isBasicParameter(commandId: string, param: CommandParameter): boolean {
  if (commandId === 'transmit' && ['bit_rate', 'cr', 'bt'].includes(param.name)) {
    return false;
  }
  if (param.required) return true;
  if (CORE_BASIC_PARAMS.has(param.name)) return true;
  if (['live', 'exchange'].includes(commandId) && ['lna_gain', 'vga_gain', 'amp'].includes(param.name)) {
    return true;
  }
  if (commandId === 'exchange' && param.name === 'gap_ms') {
    return true;
  }
  if (commandId === 'usrp' && param.name === 'args') {
    return true;
  }
  return false;
}

export function partitionCommandParameters(
  commandId: string,
  parameters: CommandParameter[]
): { basic: CommandParameter[]; advanced: CommandParameter[] } {
  const basic: CommandParameter[] = [];
  const advanced: CommandParameter[] = [];
  for (const param of parameters) {
    if (isBasicParameter(commandId, param)) {
      basic.push(param);
    } else {
      advanced.push(param);
    }
  }
  return { basic, advanced };
}

export function isParameterCustomized(param: CommandParameter, val: any): boolean {
  if (val === undefined || val === null || val === '') return false;
  if (param.type === 'boolean') {
    return Boolean(val) !== Boolean(param.default);
  }
  if (Array.isArray(val)) {
    return val.length > 0;
  }
  if (param.default !== undefined && param.default !== null && param.default !== '') {
    return String(val) !== String(param.default);
  }
  return true;
}

export function templateSlot(template: TemplateItem): 'config' | 'message_file' | 'rf_config' {
  return template.category === 'rf_config' ? 'rf_config' : template.format === 'toml' ? 'config' : 'message_file';
}

export function applyCommandTemplate(values: Record<string, any>, template: TemplateItem): Record<string, any> {
  const slot = templateSlot(template);
  const next = slot === 'config' ? resetPhysicalOverrides(values) : { ...values };
  next[slot] = template.path;
  if (slot === 'message_file') {
    if ('payload_hex' in next) next.payload_hex = '';
    if ('payload_file' in next) next.payload_file = '';
  }
  return next;
}

export function resetPhysicalOverrides(values: Record<string, any>): Record<string, any> {
  const next = { ...values };
  for (const key of physicalKeys) if (key in next) next[key] = '';
  return next;
}

export function repeatedValues(value: unknown): unknown[] {
  if (Array.isArray(value)) return value;
  if (typeof value === 'string') return value.trim().split(/[,，\s]+/).filter(Boolean);
  return value === null || value === undefined ? [] : [value];
}

export function templateParameterValue(commandId: string, parsed: Record<string, any>, name: string): any {
  const physical = ['transmit', 'duplex', 'modulate'].includes(commandId);
  const config = { ...(parsed.demod || parsed), ...(physical ? parsed.transmit || {} : {}) };
  if (name === 'frequency') return physical ? parsed.transmit?.frequency : config.frequencies?.join(', ');
  if (name === 'cr') return config.coding_rate;
  if (name === 'sync') return config.sync_word;
  if (name === 'payload_length') return config.payload_lengths?.join(', ');
  if (name === 'crc_seed') return config.crc_initials?.map((seed: number) => `0x${seed.toString(16)}`).join(', ');
  if (name === 'overlap_ms') return config.overlap_seconds === undefined ? undefined : config.overlap_seconds * 1000;
  return physicalKeys.includes(name) ? config[name] : undefined;
}
