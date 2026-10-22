import React from 'react';
import { ScanWorkbenchPanel } from '../components/ScanWorkbenchPanel';

export const ScanView: React.FC = () => {
  return (
    <div className="h-full w-full bg-rf-bg">
      <ScanWorkbenchPanel />
    </div>
  );
};

export default ScanView;
