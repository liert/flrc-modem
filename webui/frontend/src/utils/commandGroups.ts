import type { CommandDefinition } from '../types';

export interface CommandGroup {
  id: string;
  name: string;
  category: string;
  description: string;
  modes: CommandDefinition[];
}

export function commandGroups(commands: CommandDefinition[]): CommandGroup[] {
  const groups = new Map<string, CommandGroup>();
  for (const command of commands) {
    const id = command.group_id || command.id;
    if (!groups.has(id)) groups.set(id, {
      id, name: command.group_name || command.name, category: command.category,
      description: command.group_description || command.description, modes: [],
    });
    groups.get(id)!.modes.push(command);
  }
  for (const group of groups.values()) group.modes.sort((a, b) => (a.mode_order || 0) - (b.mode_order || 0));
  return [...groups.values()];
}

export function commandGroupMatches(group: CommandGroup, search: string, category: string): boolean {
  const text = [group.name, group.description, ...group.modes.flatMap(mode => [mode.name, mode.description, mode.subcommand])].join(' ').toLowerCase();
  return (category === '全部' || group.category === category) && text.includes(search.trim().toLowerCase());
}
