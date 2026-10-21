import { create } from 'zustand';
import { TemplateItem } from '../types';

export type ActiveTab = 'dashboard' | 'commands' | 'templates' | 'workbench' | 'signals' | 'tasks';

export interface CommandDraftState {
  formData: Record<string, any>; taskName: string; manualOutputs: Record<string, boolean>;
}

interface WorkstationState {
  activeTab: ActiveTab;
  selectedCommandId: string;
  selectedRunId: string | null;
  selectedPipelineId: string;
  selectedNodeId: string | null;
  selectedArtifactPath: string | null;
  selectedTemplate: TemplateItem | null;
  commandDrafts: Record<string, CommandDraftState>;
  saveCommandDraft: (id: string, draft: CommandDraftState) => void;

  setActiveTab: (tab: ActiveTab) => void;
  setSelectedCommandId: (id: string) => void;
  setSelectedRunId: (id: string | null) => void;
  setSelectedPipelineId: (id: string) => void;
  setSelectedNodeId: (id: string | null) => void;
  setSelectedArtifactPath: (path: string | null) => void;
  setSelectedTemplate: (tpl: TemplateItem | null) => void;
}

export const useWorkstationStore = create<WorkstationState>((set) => ({
  activeTab: 'dashboard',
  selectedCommandId: 'demod',
  selectedRunId: null,
  selectedPipelineId: 'pipe_demod',
  selectedNodeId: null,
  selectedArtifactPath: null,
  selectedTemplate: null,
  commandDrafts: {},
  saveCommandDraft: (id, draft) => set(state => ({ commandDrafts: { ...state.commandDrafts, [id]: draft } })),

  setActiveTab: (tab) => set({ activeTab: tab }),
  setSelectedCommandId: (id) => set({ selectedCommandId: id }),
  setSelectedRunId: (id) => set({ selectedRunId: id }),
  setSelectedPipelineId: (id) => set({ selectedPipelineId: id }),
  setSelectedNodeId: (id) => set({ selectedNodeId: id }),
  setSelectedArtifactPath: (path) => set({ selectedArtifactPath: path }),
  setSelectedTemplate: (tpl) => set({ selectedTemplate: tpl }),
}));
