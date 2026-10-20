import type { CommandDefinition, CommandParameter } from '../types';

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
  if (['transmit', 'duplex'].includes(command.id)) {
    if (parameters.session) { delete parameters.message_file; delete parameters.payload_hex; }
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
