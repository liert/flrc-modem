import React, { useState, useMemo, useEffect } from 'react';
import { useQuery } from '@tanstack/react-query';
import {
  ReactFlow,
  Background,
  Controls,
  MiniMap,
  useNodesState,
  useEdgesState,
  MarkerType,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import {
  Play,
  Layers,
  Activity,
  X,
  ArrowRight,
  Workflow,
  Cpu,
} from 'lucide-react';
import { api } from '../api/client';
import { PipelineCustomNode } from '../components/pipeline/PipelineCustomNode';
import { useWorkstationStore } from '../stores/useWorkstationStore';
import { useWebSocketStore } from '../stores/useWebSocketStore';
import { runOption } from '../utils/runs';

const nodeTypes = {
  custom: PipelineCustomNode,
};

export const PipelineWorkbenchView: React.FC = () => {
  const {
    selectedPipelineId,
    setSelectedPipelineId,
    selectedRunId,
    setSelectedRunId,
    setActiveTab,
  } = useWorkstationStore();

  const { stageStates, stageMetrics, stageProgress, logs } = useWebSocketStore();

  const [selectedNode, setSelectedNode] = useState<any | null>(null);

  const { data: pipelines } = useQuery({
    queryKey: ['pipelines'],
    queryFn: api.getPipelines,
  });

  const { data: runs } = useQuery({
    queryKey: ['runs', 30, 0],
    queryFn: () => api.getRuns(30, 0),
    refetchInterval: 3000,
  });

  const activePipeline = pipelines?.find((p) => p.id === selectedPipelineId);

  // 初始化 React Flow 节点与连线
  const initialNodes = useMemo(() => {
    if (!activePipeline) return [];
    return activePipeline.nodes.map((node) => ({
      id: node.id,
      type: 'custom',
      position: node.position || { x: 100, y: 100 },
      data: {
        ...node,
        status: 'waiting',
      },
    }));
  }, [activePipeline]);

  const initialEdges = useMemo(() => {
    if (!activePipeline) return [];
    return activePipeline.edges.map((edge) => ({
      id: edge.id,
      source: edge.source,
      target: edge.target,
      sourceHandle: edge.source_handle,
      targetHandle: edge.target_handle,
      animated: false,
      style: { stroke: '#00e5ff', strokeWidth: 2 },
      label: edge.label,
      labelStyle: { fill: '#94a3b8', fontSize: 10, fontFamily: 'monospace' },
      labelBgStyle: { fill: '#161b22', fillOpacity: 0.85 },
      markerEnd: { type: MarkerType.ArrowClosed, color: '#00e5ff' },
    }));
  }, [activePipeline]);

  const [nodes, setNodes, onNodesChange] = useNodesState(initialNodes);
  const [edges, setEdges, onEdgesChange] = useEdgesState(initialEdges);

  // 同步流水线结构更新
  useEffect(() => {
    setNodes(initialNodes);
    setEdges(initialEdges);
    setSelectedNode(null);
  }, [initialNodes, initialEdges, setNodes, setEdges]);

  // 根据当前选定 run_id 的实时状态/指标动态着色节点与流动线
  useEffect(() => {
    if (!selectedRunId) return;
    const runStates = stageStates[selectedRunId] || {};
    const runMetrics = stageMetrics[selectedRunId] || {};
    const runProg = stageProgress[selectedRunId] || {};

    setNodes((prevNodes) =>
      prevNodes.map((node) => {
        const stageId = node.data.stage_id;
        const status = stageId ? runStates[stageId] || 'waiting' : 'waiting';
        const metrics = stageId ? runMetrics[stageId] : undefined;
        const progress = stageId ? runProg[stageId] : undefined;
        return {
          ...node,
          data: {
            ...node.data,
            status,
            metrics,
            progress,
          },
        };
      })
    );

    // 活跃执行阶段的高亮动画连线
    setEdges((prevEdges) =>
      prevEdges.map((edge) => {
        const sourceNode = nodes.find((n) => n.id === edge.source);
        const isStreaming = sourceNode?.data?.status === 'running';
        return {
          ...edge,
          animated: isStreaming,
          style: {
            stroke: isStreaming ? '#00e5ff' : '#0284c7',
            strokeWidth: isStreaming ? 3 : 2,
          },
        };
      })
    );
  }, [selectedRunId, stageStates, stageMetrics, stageProgress, setNodes, setEdges]);

  const onNodeClick = (_: React.MouseEvent, node: any) => {
    setSelectedNode(node);
  };

  const handleStartCommand = () => {
    if (activePipeline?.command_id) {
      useWorkstationStore.getState().setSelectedCommandId(activePipeline.command_id);
      setActiveTab('commands');
    }
  };

  return (
    <div className="flex flex-col h-[calc(100vh-3.5rem)] select-none">
      {/* 顶部工具控制栏 */}
      <div className="h-13 bg-rf-surface/95 border-b border-rf-border px-5 flex items-center justify-between shadow-sm">
        <div className="flex items-center space-x-3">
          <Workflow className="w-4 h-4 text-cyan-400" />
          <span className="text-xs font-bold font-mono text-rf-text uppercase tracking-wider">处理流水线:</span>
          <select
            value={selectedPipelineId}
            onChange={(e) => setSelectedPipelineId(e.target.value)}
            className="bg-rf-bg border border-rf-border rounded-lg px-3 py-1.5 text-xs text-rf-text font-mono focus:outline-none focus:border-cyan-400 focus:ring-1 focus:ring-cyan-400/40 transition-all"
          >
            {pipelines?.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name} ({p.id})
              </option>
            ))}
          </select>
        </div>

        {/* 关联任务回放与控制 */}
        <div className="flex items-center space-x-3">
          <div className="flex items-center space-x-2 text-xs font-mono">
            <span className="text-zinc-400 font-semibold">回放任务:</span>
            <select
              value={selectedRunId || ''}
              onChange={(e) => setSelectedRunId(e.target.value || null)}
              className="bg-rf-bg border border-rf-border rounded-lg px-3 py-1.5 text-xs text-rf-text font-mono focus:outline-none focus:border-cyan-400 focus:ring-1 focus:ring-cyan-400/40 transition-all max-w-xs truncate"
            >
              <option value="">-- 当前实时 / 无回放 --</option>
              {runs
                ?.filter((r) => !activePipeline || r.pipeline_id === activePipeline.id)
                .map((r) => (
                  <option key={r.id} value={r.id}>
                    {runOption(r)}
                  </option>
                ))}
            </select>
          </div>

          <button
            onClick={handleStartCommand}
            className="px-4 py-1.5 rounded-lg bg-gradient-to-r from-cyan-500 to-cyan-400 hover:from-cyan-400 hover:to-cyan-300 text-black font-bold text-xs flex items-center space-x-1.5 shadow-md shadow-cyan-500/20 active:scale-[0.98] transition-all"
          >
            <Play className="w-3.5 h-3.5 fill-current" />
            <span>配置并执行</span>
          </button>
        </div>
      </div>

      {/* 主画布与节点侧边栏 */}
      <div className="flex-1 flex overflow-hidden relative">
        <div className="flex-1 h-full">
          <ReactFlow
            nodes={nodes}
            edges={edges}
            onNodesChange={onNodesChange}
            onEdgesChange={onEdgesChange}
            onNodeClick={onNodeClick}
            nodeTypes={nodeTypes}
            fitView
            className="bg-rf-bg"
          >
            <Background color="#1f242c" gap={20} />
            <Controls className="!bg-rf-surface !border-rf-border !text-rf-text !rounded-lg !shadow-md" />
            <MiniMap
              nodeColor={(n: any) =>
                n.data?.status === 'running'
                  ? '#00e5ff'
                  : n.data?.status === 'succeeded'
                  ? '#10b981'
                  : '#334155'
              }
              maskColor="rgba(13, 17, 23, 0.75)"
              className="!bg-rf-surface !border-rf-border !rounded-lg"
            />
          </ReactFlow>
        </div>

        {/* 节点侧边详情栏 */}
        {selectedNode && (
          <div className="w-80 bg-rf-surface/95 border-l border-rf-border flex flex-col justify-between overflow-y-auto shadow-2xl">
            <div className="p-4 space-y-4">
              <div className="flex items-start justify-between border-b border-rf-border pb-3">
                <div>
                  <h3 className="text-sm font-bold text-rf-text tracking-wide">{selectedNode.data.label}</h3>
                  <div className="text-[11px] font-mono text-zinc-500 mt-0.5">
                    ID: {selectedNode.id} | 类型: {selectedNode.data.type}
                  </div>
                </div>
                <button
                  onClick={() => setSelectedNode(null)}
                  className="p-1 rounded-md text-zinc-400 hover:text-rf-text hover:bg-rf-panel transition-colors"
                >
                  <X className="w-4 h-4" />
                </button>
              </div>

              {/* 描述与阶段映射 */}
              <div className="space-y-1.5">
                <div className="text-xs font-semibold text-zinc-400 font-mono">处理步骤描述</div>
                <p className="text-xs text-zinc-300 leading-relaxed bg-rf-bg p-3 rounded-lg border border-rf-border font-sans">
                  {selectedNode.data.description}
                </p>
              </div>

              {/* 状态徽章 */}
              <div className="flex items-center justify-between text-xs font-mono p-2.5 rounded-lg bg-rf-panel border border-rf-border">
                <span className="text-zinc-400 font-semibold">阶段状态:</span>
                <span
                  className={`font-bold uppercase ${
                    selectedNode.data.status === 'succeeded'
                      ? 'text-emerald-400'
                      : selectedNode.data.status === 'running'
                      ? 'text-cyan-400 animate-pulse'
                      : selectedNode.data.status === 'failed'
                      ? 'text-red-400'
                      : 'text-zinc-500'
                  }`}
                >
                  {selectedNode.data.status || 'WAITING'}
                </span>
              </div>

              {/* 输入/输出端口定义 */}
              <div className="space-y-2">
                <div className="text-xs font-semibold text-zinc-400 font-mono">端口与数据契约</div>
                <div className="space-y-1.5 text-xs font-mono">
                  {selectedNode.data.inputs?.map((inPort: any) => (
                    <div
                      key={inPort.id}
                      className="p-2 rounded-lg bg-rf-bg border border-rf-border flex justify-between items-center"
                    >
                      <span className="text-cyan-300 font-medium">入: {inPort.label}</span>
                      <span className="text-zinc-500 text-[11px]">{inPort.data_type}</span>
                    </div>
                  ))}
                  {selectedNode.data.outputs?.map((outPort: any) => (
                    <div
                      key={outPort.id}
                      className="p-2 rounded-lg bg-rf-bg border border-rf-border flex justify-between items-center"
                    >
                      <span className="text-emerald-300 font-medium">出: {outPort.label}</span>
                      <span className="text-zinc-500 text-[11px]">{outPort.data_type}</span>
                    </div>
                  ))}
                </div>
              </div>

              {/* 实时采样指标 */}
              {selectedNode.data.metrics && (
                <div className="space-y-2">
                  <div className="text-xs font-semibold text-zinc-400 flex items-center space-x-1.5 font-mono">
                    <Activity className="w-3.5 h-3.5 text-cyan-400" />
                    <span>阶段实测指标 (Metrics)</span>
                  </div>
                  <div className="bg-rf-bg p-3 rounded-lg border border-rf-border text-xs font-mono space-y-1.5 max-h-40 overflow-y-auto">
                    {Object.entries(selectedNode.data.metrics).map(([k, v]) => (
                      <div key={k} className="flex justify-between text-zinc-300">
                        <span className="text-zinc-500">{k}:</span>
                        <span className="text-cyan-300 font-semibold truncate max-w-[140px]">
                          {typeof v === 'number' ? v.toFixed(3) : String(v)}
                        </span>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>

            {/* 底部跳转信号分析 */}
            <div className="p-3.5 border-t border-rf-border bg-rf-panel/40">
              <button
                onClick={() => setActiveTab('signals')}
                className="w-full py-2 rounded-lg bg-rf-panel hover:bg-rf-border border border-rf-border text-xs font-mono text-cyan-300 flex items-center justify-center space-x-1.5 transition-all shadow-sm"
              >
                <span>跳转至频谱与星座图分析</span>
                <ArrowRight className="w-3.5 h-3.5" />
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
};
