import React from 'react';
import { ExternalLink, FileText, RotateCcw, X } from 'lucide-react';
import type { CommandDefinition, TemplateItem } from '../types';
import { templateSlot } from '../utils/commandParameters';

export function CommandTemplateConfiguration({ command, templates, parameters, onApply, onChange, onManage, onReset }: {
  command: CommandDefinition; templates: TemplateItem[]; parameters: Record<string, any>;
  onApply: (template: TemplateItem) => void; onChange: (values: Record<string, any>) => void;
  onManage: (template?: TemplateItem) => void; onReset: () => void;
}) {
  const slots = [
    { name: 'config', label: '物理参数模板', flag: '--config', empty: '不使用 TOML，使用表单或默认物理参数',
      description: '采样率、码率、CR、BT、包头与 CRC；表单覆盖值优先。' },
    { name: 'rf_config', label: '射频跳频配置', flag: '--rf-config', empty: '未指定当前射频配置（使用内置历史跳频表）',
      description: '当前设备的有序跳频表和 OTA 同步字；初始频率留空时从所选表选取。' },
    { name: 'message_file', label: '业务报文模板', flag: '--message-file', empty: parameters.session ? '常驻会话：启动后在任务中心投递报文' : '不使用报文模板，填写原始载荷',
      description: '本次发送的业务字段；与物理参数和跳频配置分别选择。' },
  ].filter(slot => command.parameters.some(parameter => parameter.name === slot.name));
  return <section className="bg-rf-surface border border-rf-border p-4 rounded-xl space-y-4 shadow-sm">
    <div className="flex items-center justify-between gap-3">
      <h3 className="text-xs font-bold text-rf-text flex items-center gap-2"><FileText className="w-4 h-4 text-cyan-400" />配置与报文模板</h3>
      <button type="button" onClick={() => onManage()} className="text-xs text-cyan-400 flex items-center gap-1.5"><ExternalLink className="w-3.5 h-3.5" />模板库管理</button>
    </div>
    <p className="text-xs text-zinc-400">所有配置文件来自 templates 文件夹。三类配置独立生效，选择或清除其中一项不会移除其他配置。</p>
    <div className="space-y-4">
      {slots.map(slot => {
        const options = templates.filter(template => templateSlot(template) === slot.name && template.applicable_commands.includes(command.id));
        const path = parameters[slot.name] || '';
        const active = options.find(template => template.path.replace(/\\/g, '/') === path.replace(/\\/g, '/'));
        const disabled = slot.name === 'message_file' && Boolean(parameters.session);
        return <div key={slot.name} className="space-y-2 min-w-0">
          <div className="flex items-center justify-between gap-2 text-xs">
            <label htmlFor={`template-${slot.name}`} className="font-semibold text-zinc-300">{slot.label}</label>
            <span className="text-zinc-500 font-mono">{slot.flag}</span>
          </div>
          <div className="flex items-center gap-2">
            <select id={`template-${slot.name}`} disabled={disabled} value={active?.path || path}
              onChange={event => {
                const template = options.find(item => item.path === event.target.value);
                if (template) onApply(template); else onChange({ [slot.name]: '' });
              }} className="h-9 min-w-0 flex-1 bg-rf-bg border border-rf-border rounded-lg px-3 text-xs font-mono text-zinc-200 focus:outline-none focus:border-cyan-400 focus:ring-1 focus:ring-cyan-400/40 disabled:opacity-50">
              <option value="">{slot.empty}</option>
              {path && !active && <option value={path}>{path}（手动填写）</option>}
              {options.map(template => <option key={template.path} value={template.path}>{template.title} · {template.name}</option>)}
            </select>
            {path && !disabled && <button type="button" onClick={() => onChange({ [slot.name]: '' })} aria-label={`清除${slot.label}`} className="p-2 text-zinc-400 hover:text-red-400"><X className="w-4 h-4" /></button>}
            {active && <button type="button" onClick={() => onManage(active)} aria-label={`查看${slot.label}`} className="p-2 text-cyan-400"><ExternalLink className="w-4 h-4" /></button>}
          </div>
          <p className="text-xs text-zinc-400 leading-relaxed">{active?.description || slot.description}</p>
          {slot.name === 'message_file' && !parameters.session && (parameters.payload_hex || parameters.payload_file) && <p className="text-xs text-amber-400">当前使用原始载荷，所选报文模板不会发送；重新选择报文模板可切回业务报文。</p>}
          {active && <p className="text-xs font-mono text-zinc-500 break-all">{active.path}</p>}
          {slot.name === 'config' && path && <button type="button" onClick={onReset} className="text-xs text-amber-400 flex items-center gap-1.5"><RotateCcw className="w-3.5 h-3.5" />清空物理参数覆盖</button>}
          {slot.name === 'rf_config' && !path && <p className="text-xs text-amber-400 leading-relaxed">内置表不代表当前设备。同步字可从有效接收帧恢复，完整跳频顺序需来自当前设备查询、配对数据或已验证的配置，无法由单个频率或码率推算。请将已有的射频 JSON 放入 templates 后选择。</p>}
        </div>;
      })}
    </div>
  </section>;
}
