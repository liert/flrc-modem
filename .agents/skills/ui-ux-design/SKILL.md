---
name: ui-ux-design
description: Standards and guidelines for professional RF and instrument-grade dark theme UX/UI layout, visual hierarchy, typography, form ergonomics, micro-interactions, and Tailwind CSS styling.
---

# Professional RF & Instrument UI/UX Design System

This skill provides design principles, layout patterns, and component guidelines for building and beautifying high-precision RF test & measurement workstation web applications.

## Design Philosophy: Modern Sci-Fi & Precision Instrument Aesthetic

1. **Deep Cybernetic Dark Palette**:
   - Backgrounds: Dark obsidian base (`#0d1117`), elevated surfaces (`#161b22`), interactive panels (`#1f242c`).
   - Borders: Subtle structural wireframe borders (`#30363d`), never harsh white lines. Hover states subtly illuminate (`#00e5ff33` or `#38bdf840`).
   - Neon Accents: High-frequency Cyan (`#00e5ff`) for primary accents, Neon Emerald (`#10b981` / `#00ff66`) for success & lock states, Amber (`#f59e0b`) for warnings & intermediate states, Coral Red (`#ef4444`) for errors & danger actions.
   - Text Hierarchy: Bright primary text (`#f0f6fc`), secondary text (`#cbd5e1`), muted hints (`#8b949e`), subtle placeholders (`#64748b`).

2. **Typography & Legibility Scale**:
   - Base text floor: Keep `text-xs` (12px) as the legible floor for general labels, descriptions, and inputs.
   - Micro-tags: Use `text-[10px]` or `text-[11px]` strictly for compact chips, status pills, or timestamp badges, accompanied by uppercase font-mono tracking (`tracking-wider`).
   - Headings: Clear size progression: Section titles `text-sm font-bold uppercase tracking-wider`, view headers `text-base font-bold`, page banners `text-lg font-semibold`.
   - Font Pairing: Clean Sans for primary UI labels (`Inter`, system-ui), paired with Monospace (`JetBrains Mono`, `Fira Code`) for frequencies, hex addresses, sync words, and CLI flags.

3. **Card & Surface Architecture**:
   - Card Header: A unified header banner with icon + uppercase title + status chips + right-aligned action toolbar.
   - Subtle Gradients & Shadows: Give cards slight depth using `shadow-sm shadow-black/40` and gentle gradient borders or top highlight borders.
   - Inner Inset Panels: Input areas, terminal viewports, and payload monitors should feel recessed using `bg-rf-bg/80 border border-rf-border/80 rounded-md`.

4. **Form Controls & Ergonomics**:
   - Form Labels: Distinct row layout with parameter name, badge (`* 必选` in red badge vs `可选` in dark badge), and CLI flag on the far right.
   - Input Styling: Focused inputs receive a cyan glow (`focus:border-cyan-400 focus:ring-1 focus:ring-cyan-500/30`).
   - Clear Actions: Optional fields must have clear `×` clear buttons when filled to allow unsetting.
   - Dropdown Selectors: First option clearly communicates default behavior (e.g., `(未指定 / 默认使用模板设定: ...)`).

5. **Master-Detail Two-Column Workspaces**:
   - Left Sidebar: Fixed width (w-80 to w-96), full vertical height (`calc(100vh - 3.5rem)`), integrated top search bar, chip filters, scrollable list with active left-accent border (`border-l-2 border-rf-accent bg-cyan-500/10`), and bottom pagination.
   - Right Main Panel: Scrollable viewport with `p-6 space-y-5 max-w-5xl mx-auto`.
