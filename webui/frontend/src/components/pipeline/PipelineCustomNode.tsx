import React from 'react';
import { Handle, Position, NodeProps } from '@xyflow/react';
import {
  FileText,
  Cpu,
  ArrowDownToLine,
  CheckCircle2,
  Clock,
  Loader2,
  XCircle,
  Database,
  Radio,
} from 'lucide-react';

export interface CustomNodeData {
  label: string;
  description: string;
  type: 'source' | 'processor' | 'sink';
  stage_id?: string;
  status?: 'waiting' | 'running' | 'succeeded' | 'failed';
  progress?: number;
  metrics?: Record<string, any>;
  inputs?: Array<{ id: string; label: string; data_type: string }>;
  outputs?: Array<{ id: string; label: string; data_type: string }>;
}

export const PipelineCustomNode: React.FC<NodeProps> = ({ data, selected }) => {
  const nodeData = data as unknown as CustomNodeData;
  const status = nodeData.status || 'waiting';

  const getBorderColor = () => {
    if (selected) return 'border-rf-accent ring-2 ring-rf-accent/30';
    if (status === 'running') return 'border-cyan-400 shadow-lg shadow-cyan-500/20';
    if (status === 'succeeded') return 'border-emerald-500/80';
    if (status === 'failed') return 'border-red-500/80';
    return 'border-rf-border';
  };

  const getIcon = () => {
    if (nodeData.type === 'source') return <Database className="w-3.5 h-3.5 text-blue-400" />;
    if (nodeData.type === 'processor') return <Cpu className="w-3.5 h-3.5 text-cyan-400" />;
    return <ArrowDownToLine className="w-3.5 h-3.5 text-emerald-400" />;
  };

  const getStatusBadge = () => {
    switch (status) {
      case 'running':
        return (
          <span className="flex items-center space-x-1 text-[10px] text-cyan-400 bg-cyan-950/60 px-1.5 py-0.5 rounded border border-cyan-800 animate-pulse">
            <Loader2 className="w-2.5 h-2.5 animate-spin" />
            <span>执行中</span>
          </span>
        );
      case 'succeeded':
        return (
          <span className="flex items-center space-x-1 text-[10px] text-emerald-400 bg-emerald-950/60 px-1.5 py-0.5 rounded border border-emerald-800">
            <CheckCircle2 className="w-2.5 h-2.5" />
            <span>完成</span>
          </span>
        );
      case 'failed':
        return (
          <span className="flex items-center space-x-1 text-[10px] text-red-400 bg-red-950/60 px-1.5 py-0.5 rounded border border-red-800">
            <XCircle className="w-2.5 h-2.5" />
            <span>失败</span>
          </span>
        );
      default:
        return (
          <span className="flex items-center space-x-1 text-[10px] text-zinc-500 bg-zinc-900 px-1.5 py-0.5 rounded border border-zinc-800">
            <Clock className="w-2.5 h-2.5" />
            <span>待命</span>
          </span>
        );
    }
  };

  return (
    <div
      className={`w-52 bg-rf-surface rounded-lg border text-left transition-all relative ${getBorderColor()}`}
    >
      {/* 顶部标题栏 */}
      <div className="p-2.5 border-b border-rf-border/60 flex items-center justify-between bg-rf-panel/50 rounded-t-lg">
        <div className="flex items-center space-x-1.5 truncate">
          {getIcon()}
          <span className="text-xs font-bold text-rf-text truncate">{nodeData.label}</span>
        </div>
        {getStatusBadge()}
      </div>

      {/* 描述与阶段 */}
      <div className="p-2.5 space-y-2">
        <p className="text-[10px] text-zinc-400 line-clamp-2 leading-relaxed">
          {nodeData.description}
        </p>

        {nodeData.stage_id && (
          <div className="text-[9px] font-mono text-zinc-500 flex items-center justify-between">
            <span>阶段:</span>
            <span className="text-cyan-400 truncate max-w-[110px]">{nodeData.stage_id}</span>
          </div>
        )}

        {/* 实时进度条 */}
        {status === 'running' && typeof nodeData.progress === 'number' && (
          <div className="w-full bg-zinc-800 h-1 rounded-full overflow-hidden">
            <div
              className="bg-cyan-400 h-full transition-all duration-300"
              style={{ width: `${Math.round(nodeData.progress * 100)}%` }}
            />
          </div>
        )}
      </div>

      {/* 端口连接 Handle */}
      {nodeData.inputs?.map((input, idx) => (
        <Handle
          key={input.id}
          type="target"
          position={Position.Left}
          id={input.id}
          style={{ top: `${(idx + 1) * 20 + 20}px` }}
          className="w-2.5 h-2.5 !bg-cyan-400 !border-2 !border-rf-bg"
        />
      ))}

      {nodeData.outputs?.map((output, idx) => (
        <Handle
          key={output.id}
          type="source"
          position={Position.Right}
          id={output.id}
          style={{ top: `${(idx + 1) * 20 + 20}px` }}
          className="w-2.5 h-2.5 !bg-emerald-400 !border-2 !border-rf-bg"
        />
      ))}
    </div>
  );
};
