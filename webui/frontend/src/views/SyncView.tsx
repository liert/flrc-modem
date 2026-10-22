import React from 'react';
import { SyncWorkbenchPanel } from '../components/SyncWorkbenchPanel';

export const SyncView: React.FC = () => {
  return (
    <div className="h-full w-full bg-rf-bg">
      <SyncWorkbenchPanel />
    </div>
  );
};

export default SyncView;
