import React from 'react';
import { ChevronLeft } from 'lucide-react';

export const MasterDetailBackButton: React.FC<{ label: string; onClick: () => void }> = ({ label, onClick }) => (
  <button
    type="button"
    onClick={onClick}
    className="master-detail-back xl:hidden shrink-0 flex items-center gap-1.5 px-3 py-2 text-xs text-cyan-300 bg-rf-surface border-b border-rf-border hover:bg-rf-panel"
  >
    <ChevronLeft className="w-4 h-4" aria-hidden="true" />
    返回{label}
  </button>
);
