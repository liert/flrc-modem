import { create } from 'zustand';

export type ActiveTab = 'dashboard' | 'commands' | 'workbench' | 'signals' | 'tasks';

interface WorkstationState {
  activeTab: ActiveTab;
  selectedCommandId: string;
  selectedRunId: string | null;
  selectedPipelineId: string;
  selectedNodeId: string | null;
  selectedArtifactPath: string | null;

  setActiveTab: (tab: ActiveTab) => void;
  setSelectedCommandId: (id: string) => void;
  setSelectedRunId: (id: string | null) => void;
  setSelectedPipelineId: (id: string) => void;
  setSelectedNodeId: (id: string | null) => void;
  setSelectedArtifactPath: (path: string | null) => void;
}

export const useWorkstationStore = create<WorkstationState>((set) => ({
  activeTab: 'dashboard',
  selectedCommandId: 'demod',
  selectedRunId: null,
  selectedPipelineId: 'pipe_demod',
  selectedNodeId: null,
  selectedArtifactPath: 'outputs/e2e_test.iq',

  setActiveTab: (tab) => set({ activeTab: tab }),
  setSelectedCommandId: (id) => set({ selectedCommandId: id }),
  setSelectedRunId: (id) => set({ selectedRunId: id }),
  setSelectedPipelineId: (id) => set({ selectedPipelineId: id }),
  setSelectedNodeId: (id) => set({ selectedNodeId: id }),
  setSelectedArtifactPath: (path) => set({ selectedArtifactPath: path }),
}));
