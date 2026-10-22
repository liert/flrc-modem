import React from 'react';
import { TransmitWorkbenchPanel } from '../components/TransmitWorkbenchPanel';

export const TransmitView: React.FC = () => {
  return (
    <div className="h-full w-full bg-rf-bg">
      <TransmitWorkbenchPanel />
    </div>
  );
};

export default TransmitView;
