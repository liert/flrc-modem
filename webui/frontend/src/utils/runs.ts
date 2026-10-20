import type { RunRecord, UIEvent } from '../types';
import type { QueryClient } from '@tanstack/react-query';

const commandNames: Record<string, string> = {
  demod: '离线解调', modulate: '离线调制', rx: '信号采集', tx: '信号发送',
  live: '实时解调', sync: '同步分析', transmit: '盲发（HackRF）', duplex: '同步时隙收发（USRP）', hackrf: 'HackRF 诊断',
};

export function runTitle(run: Pick<RunRecord, 'name' | 'command_id' | 'command_args'>): string {
  if (run.name?.trim()) return run.name.trim();
  const args = run.command_args || {};
  const base = (value: unknown) => typeof value === 'string' ? value.split(/[\\/]/).pop() || '' : '';
  const config = base(args.config).replace(/\.(toml|json)$/i, '');
  const file = base(args.iq || args.file || args.input || args.output);
  return [commandNames[run.command_id] || run.command_id, config, file].filter(Boolean).join(' · ');
}

export function runTime(timestamp: string): string {
  const date = new Date(timestamp);
  return Number.isNaN(date.getTime()) ? timestamp : date.toLocaleString('zh-CN', { hour12: false });
}

export function runOption(run: RunRecord): string {
  return `${runTitle(run)} · ${runTime(run.created_at)} · ${run.id.slice(-8)} · ${run.status}`;
}

export function runDeletable(run: Pick<RunRecord, 'status'>): boolean {
  return ['SUCCEEDED', 'FAILED', 'CANCELLED'].includes(run.status);
}

export function deletedRunIds(event: UIEvent): string[] {
  if (event.type !== 'task.deleted') return [];
  const ids = event.payload?.run_ids;
  return Array.isArray(ids) ? ids.filter((id): id is string => typeof id === 'string') : [event.run_id];
}

export function replacementRunId(event: UIEvent): string | null {
  const id = event.type === 'task.deleted' ? event.payload?.replacement_run_id : null;
  return typeof id === 'string' && id && !deletedRunIds(event).includes(id) ? id : null;
}

export async function purgeRunCaches(client: QueryClient, ids: string[]): Promise<void> {
  client.setQueriesData<RunRecord[]>({ queryKey: ['runs'] }, items => items?.filter(run => !ids.includes(run.id)));
  const matches = (key: readonly unknown[]) =>
    (['run', 'runLogs', 'runEvents', 'runArtifacts', 'analysisContext', 'frames', 'signalWindow', 'duplexSession'].includes(String(key[0])) && ids.includes(String(key[1])))
    || (['spectrum', 'constellation'].includes(String(key[0])) && ids.includes(String(key[2])));
  await client.cancelQueries({ predicate: query => matches(query.queryKey) });
  client.removeQueries({ predicate: query => matches(query.queryKey) });
}
