import type { CommandDefinition, CommandParameter } from '../types';
import { repeatedValues } from './commandParameters';

export function isOutputParameter(parameter: CommandParameter): boolean {
  return parameter.type === 'path' && /^--output(?:-|$)/.test(parameter.cli_flag);
}

export function taskFileStem(name: string): string {
  let stem = name.normalize('NFC').trim().replace(/[<>:"/\\|?*\x00-\x1f]/g, '_').replace(/[. ]+$/g, '');
  if (!stem) stem = 'task';
  if (/^(con|prn|aux|nul|com[1-9]|lpt[1-9]|conin\$|conout\$)(\.|$)/i.test(stem)) stem = `_${stem}`;
  return stem;
}

export function namedOutputPath(parameter: CommandParameter, name: string, iqFormat?: string): string {
  const fallback = typeof parameter.default === 'string' && parameter.default ? parameter.default : 'outputs/result';
  const boundary = Math.max(fallback.lastIndexOf('/'), fallback.lastIndexOf('\\'));
  const directory = fallback.slice(0, boundary + 1);
  const filename = fallback.slice(boundary + 1);
  const dot = filename.lastIndexOf('.');
  let extension = dot > 0 ? filename.slice(dot) : '';
  if (/^\.(cs8|cu8|cf32)$/i.test(extension) && ['cs8', 'cu8', 'cf32'].includes(iqFormat || '')) {
    extension = `.${iqFormat}`;
  }
  const stem = taskFileStem(name);
  return directory + stem + (extension && !stem.toLowerCase().endsWith(extension.toLowerCase()) ? extension : '');
}

export function commandDraftParameters(command: CommandDefinition, values: Record<string, any>, name: string,
                                       manualOutputs: Record<string, boolean> = {}): Record<string, any> {
  const parameters = { ...values };
  for (const definition of command.parameters) {
    if (['list_number', 'list_string'].includes(definition.type) && parameters[definition.name] !== '') {
      const items = repeatedValues(parameters[definition.name]);
      parameters[definition.name] = items.length ? items : '';
    }
  }
  if (['demod', 'live'].includes(command.id) && parameters.parser) delete parameters.protocol;
  if (command.id === 'live' && parameters.max_seconds !== undefined && parameters.max_seconds !== null && parameters.max_seconds !== '') {
    delete parameters.duration;
  }
  if (['transmit', 'duplex', 'modulate', 'exchange'].includes(command.id)) {
    if (parameters.session) { delete parameters.message_file; delete parameters.payload_hex; delete parameters.payload_file; }
    else if (parameters.payload_file) {
      delete parameters.message_file;
      delete parameters.payload_hex;
      if (command.id === 'transmit') delete parameters.protocol;
    }
    else if (parameters.payload_hex) {
      delete parameters.message_file;
      if (command.id === 'transmit') delete parameters.protocol;
    }
  }
  if (!name.trim()) return parameters;
  for (const definition of command.parameters) {
    if (isOutputParameter(definition) && !manualOutputs[definition.name]) {
      parameters[definition.name] = namedOutputPath(definition, name, values.iq_format);
    }
  }
  return parameters;
}
