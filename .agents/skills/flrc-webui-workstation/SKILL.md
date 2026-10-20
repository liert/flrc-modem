---
name: flrc-webui-workstation
description: Architectural and operational guide for maintaining, extending, and designing the FLRC Modem WebUI Workstation views, components, real-time WebSocket state, and hardware SDR workflows.
---

# FLRC Modem WebUI Workstation Guide

This skill documents the structure, view roles, and UX conventions for the SX1280 FLRC Modem WebUI Workstation.

## Core Views & Layout Roles

1. **Header Navigation (`Header.tsx`)**:
   - System title: `FLRC Modem WebUI` with waveform icon and pulse indicator.
   - Tab switcher: Dashboard, Command Library, Dataflow Workbench, Signal Analysis, Task Center.
   - System Status Bar: Backend connectivity pill, WebSocket real-time indicator, USRP / HackRF hardware availability indicators.

2. **Dashboard (`DashboardView.tsx`)**:
   - Hardware SDR status cards with active locks and status badges.
   - Quick launch templates for common setups (e.g. H12 8MSps/1.3Mbps, DK32S 2MSps, Drone Telemetry).
   - Recent jobs summary table with one-click jump to Task Center or Signal Analysis.

3. **Command Library (`CommandLibraryView.tsx`)**:
   - Left sidebar: Search & category chips, command grouping (e.g. "报文收发" grouping `duplex` and `transmit`).
   - Mode switcher tabs inside grouped commands.
   - Task draft name input with automatic output filename synchronization.
   - Template library chips with active template description and clear override actions.
   - Structured parameter form with clear required/optional badges and unsetting support.
   - CLI preview bar with PowerShell-safe escaping and one-click copy / execute.

4. **Task Execution Center (`TaskCenterView.tsx`)**:
   - Left sidebar: Search tasks by name, IQ, output, config, or date; status chip filters; inline renaming with pencil icon; batch select and delete.
   - Right main view: Task header bar with status, duration, SDR exclusivity, and kill button; tabs for Live Terminal, Structured Events, Task Parameters, and Output Artifacts.

5. **Signal & Frame Analysis (`SignalAnalysisView.tsx`)**:
   - Task context banner: Shows active task ID, task name, IQ source file, frames output file, and file lifecycle state (Available, Missing, Overwritten, Modified).
   - High-level RF metric cards: Peak PSD, CFO offset, EVM envelope estimate, decoded frame counter.
   - ECharts dual panels: PSD Spectrum with frequency coordinates + I/Q Constellation scatter.
   - Decoded frames table: Hex payload inspector, multi-candidate sync word display, hardware CRC width indicators.
