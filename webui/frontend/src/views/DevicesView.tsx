import React from 'react';
import { DeviceHealthDashboard } from '../components/DeviceHealthDashboard';

export const DevicesView: React.FC = () => {
  return (
    <div className="h-full w-full bg-rf-bg">
      <DeviceHealthDashboard />
    </div>
  );
};

export default DevicesView;
