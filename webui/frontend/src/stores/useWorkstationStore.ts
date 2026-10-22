import { create } from 'zustand';
import { TemplateItem } from '../types';

export type ActiveTab = 'dashboard' | 'commands' | 'templates' | 'workbench' | 'signals' | 'tasks' | 'spectrum' | 'scan' | 'sync' | 'transmit';

export interface CommandDraftState {
  formData: Record<string, any>; taskName: string; manualOutputs: Record<string, boolean>;
}

const getInitialSidebarCollapsed = (): boolean => {
  try {
    if (typeof window !== 'undefined' && window.localStorage) {
      return window.localStorage.getItem('flrc_sidebar_collapsed') === 'true';
    }
  } catch {
    // Ignore storage errors in restricted contexts
  }
  return false;
};

interface WorkstationState {
  activeTab: ActiveTab;
  sidebarCollapsed: boolean;
  selectedCommandId: string;
  selectedRunId: string | null;
  selectedPipelineId: string;
  selectedNodeId: string | null;
  selectedArtifactPath: string | null;
  selectedTemplate: TemplateItem | null;
  commandDrafts: Record<string, CommandDraftState>;
  saveCommandDraft: (id: string, draft: CommandDraftState) => void;

  setActiveTab: (tab: ActiveTab) => void;
  setSidebarCollapsed: (collapsed: boolean) => void;
  toggleSidebar: () => void;
  setSelectedCommandId: (id: string) => void;
  setSelectedRunId: (id: string | null) => void;
  setSelectedPipelineId: (id: string) => void;
  setSelectedNodeId: (id: string | null) => void;
  setSelectedArtifactPath: (path: string | null) => void;
  setSelectedTemplate: (tpl: TemplateItem | null) => void;
  spectrumTargetFreqHz: number | null;
  setSpectrumTargetFreqHz: (freq: number | null) => void;
}

export const useWorkstationStore = create<WorkstationState>((set) => ({
  activeTab: 'dashboard',
  sidebarCollapsed: getInitialSidebarCollapsed(),
  selectedCommandId: 'demod',
  selectedRunId: null,
  selectedPipelineId: 'pipe_demod',
  selectedNodeId: null,
  selectedArtifactPath: null,
  selectedTemplate: null,
  commandDrafts: {},
  saveCommandDraft: (id, draft) => set(state => ({ commandDrafts: { ...state.commandDrafts, [id]: draft } })),

  setActiveTab: (tab) => set({ activeTab: tab }),
  setSidebarCollapsed: (collapsed) => {
    try {
      if (typeof window !== 'undefined' && window.localStorage) {
        window.localStorage.setItem('flrc_sidebar_collapsed', String(collapsed));
      }
    } catch {
      // Ignore storage errors
    }
    set({ sidebarCollapsed: collapsed });
  },
  toggleSidebar: () => {
    set((state) => {
      const next = !state.sidebarCollapsed;
      try {
        if (typeof window !== 'undefined' && window.localStorage) {
          window.localStorage.setItem('flrc_sidebar_collapsed', String(next));
        }
      } catch {
        // Ignore storage errors
      }
      return { sidebarCollapsed: next };
    });
  },
  setSelectedCommandId: (id) => set({ selectedCommandId: id }),
  setSelectedRunId: (id) => set({ selectedRunId: id }),
  setSelectedPipelineId: (id) => set({ selectedPipelineId: id }),
  setSelectedNodeId: (id) => set({ selectedNodeId: id }),
  setSelectedArtifactPath: (path) => set({ selectedArtifactPath: path }),
  setSelectedTemplate: (tpl) => set({ selectedTemplate: tpl }),
  spectrumTargetFreqHz: null,
  setSpectrumTargetFreqHz: (freq) => set({ spectrumTargetFreqHz: freq }),
}));
