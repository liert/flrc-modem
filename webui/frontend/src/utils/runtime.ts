import { CommandDefinition, LogLine } from '../types';

export function mergeLogs(history: LogLine[] = [], live: LogLine[] = [], limit = 3000): LogLine[] {
  const seen = new Set<string>();
  return [...history, ...live].filter(log => {
    const key = JSON.stringify([log.timestamp, log.stream, log.line]);
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  }).sort((a, b) => a.timestamp.localeCompare(b.timestamp)).slice(-limit);
}

export function cliPreview(command: CommandDefinition, values: Record<string, unknown>): string {
  const args = ['python', '-m', 'flrc_modem', command.subcommand];
  for (const parameter of command.parameters) {
    const value = values[parameter.name];
    if (value === undefined || value === null || value === '') continue;
    if (parameter.type === 'boolean') {
      if (value === true) args.push(parameter.cli_flag);
    } else {
      const items = parameter.type === 'list_number' && Array.isArray(value) ? value : [value];
      for (const item of items) {
        if (parameter.cli_flag) args.push(parameter.cli_flag);
        args.push(String(item));
      }
    }
  }
  if (['demod', 'modulate', 'rx', 'tx', 'sync', 'transmit'].includes(command.id)) {
    args.push('--ui-events', 'ndjson');
  }
  // 工作台面向 PowerShell；用单引号保护空格、引号、$ 和反引号。
  return args.map(arg => /^[\w./:\\=-]+$/.test(arg) ? arg : `'${arg.replace(/'/g, "''")}'`).join(' ');
}
