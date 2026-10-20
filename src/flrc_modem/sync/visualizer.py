"""FHSS + TDD 同步时间轴可视化组件。

支持输出 ASCII 终端字符画时间轴、独立矢量 SVG 与交互式 HTML 视图，
清晰呈现 50ms 跳频时隙、TDD 上下行窗口、保护间隔、到达参考点及预测误差。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence


@dataclass
class TimelineEvent:
    name: str
    category: str      # 'fhss_slot', 'ground_tx', 'turnaround', 'air_rx', 'slot_guard', 'actual_arrival', 'predicted_arrival'
    start_time_s: float
    end_time_s: float
    details: str = ""
    error_us: float | None = None


class TimelineVisualizer:
    """时间轴可视化生成器。"""

    def __init__(self, start_time_s: float = 0.0, end_time_s: float = 0.100):
        self.start_time_s = float(start_time_s)
        self.end_time_s = float(end_time_s)
        self.events: list[TimelineEvent] = []

    def add_event(
        self,
        name: str,
        category: str,
        start_time_s: float,
        end_time_s: float,
        details: str = "",
        error_us: float | None = None,
    ):
        self.events.append(TimelineEvent(name, category, start_time_s, end_time_s, details, error_us))

    def render_ascii(self, width: int = 80) -> str:
        """生成清晰的终端 ASCII 字符画时间轴。"""
        span = max(1e-6, self.end_time_s - self.start_time_s)
        lines = []

        # 标尺
        lines.append(f"=== FHSS + TDD 同步时间轴 [{self.start_time_s*1000:.1f}ms -> {self.end_time_s*1000:.1f}ms] ===")
        ruler_ticks = 5
        ruler_str = ""
        for i in range(ruler_ticks + 1):
            t_ms = (self.start_time_s + i * span / ruler_ticks) * 1000
            label = f"{t_ms:.1f}ms"
            pos = int(i * (width - 1) / ruler_ticks)
            ruler_str += label + " " * max(1, pos - len(ruler_str))
        lines.append(ruler_str[:width])
        lines.append("-" * width)

        # 1. FHSS 跳频槽轨道
        fhss_line = [" "] * width
        fhss_events = [e for e in self.events if e.category == "fhss_slot"]
        for e in fhss_events:
            x1 = max(0, min(width - 1, int((e.start_time_s - self.start_time_s) / span * (width - 1))))
            x2 = max(0, min(width - 1, int((e.end_time_s - self.start_time_s) / span * (width - 1))))
            for x in range(x1, x2 + 1):
                fhss_line[x] = "="
            label = f"[{e.name}]"
            for k, char in enumerate(label):
                if x1 + 1 + k < x2:
                    fhss_line[x1 + 1 + k] = char
        lines.append("FHSS: " + "".join(fhss_line))

        # 2. TDD 窗口轨道 (Ground TX, Turnaround, Air RX, Slot Guard)
        tdd_line = [" "] * width
        for e in self.events:
            if e.category in ("ground_tx", "turnaround", "air_rx", "slot_guard"):
                x1 = max(0, min(width - 1, int((e.start_time_s - self.start_time_s) / span * (width - 1))))
                x2 = max(0, min(width - 1, int((e.end_time_s - self.start_time_s) / span * (width - 1))))
                char = {"ground_tx": "T", "turnaround": "G", "air_rx": "R", "slot_guard": "#"}.get(e.category, ".")
                for x in range(x1, x2 + 1):
                    tdd_line[x] = char
        lines.append("TDD : " + "".join(tdd_line))

        # 3. 数据包到达与预测误差轨道
        pkt_line = [" "] * width
        for e in self.events:
            if e.category == "actual_arrival":
                x = max(0, min(width - 1, int((e.start_time_s - self.start_time_s) / span * (width - 1))))
                pkt_line[x] = "V"
            elif e.category == "predicted_arrival":
                x = max(0, min(width - 1, int((e.start_time_s - self.start_time_s) / span * (width - 1))))
                pkt_line[x] = "^"
        lines.append("PKT : " + "".join(pkt_line))
        lines.append("-" * width)
        lines.append("图例说明: [T] 地面TX (~1.5ms) | [G] 转换保护 (~810us) | [R] 空中RX (~0.5ms) | [#] 槽尾守候 (~11ms)")
        lines.append("         [V] 实测前导到达   | [^] 预测前导到达")

        # 打印详细误差指标
        error_events = [e for e in self.events if e.error_us is not None]
        if error_events:
            lines.append("预测误差详情:")
            for e in error_events[:6]:
                lines.append(f"  * {e.name}: 误差 = {e.error_us:+.2f} us ({e.details})")

        return "\n".join(lines)

    def render_svg(self, width: int = 900, height: int = 320) -> str:
        """生成独立轻量的矢量 SVG 图形。"""
        span = max(1e-6, self.end_time_s - self.start_time_s)
        pad_x = 70
        pad_y = 40
        plot_w = width - pad_x - 30
        plot_h = height - pad_y - 60

        svg_parts = [
            f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" width="100%" height="{height}" style="background:#1e1e24;font-family:sans-serif;font-size:12px;">',
            f'<text x="{pad_x}" y="25" fill="#f0f0f0" font-weight="bold" font-size="14">FHSS + TDD 同步时间轴与硬件到达预测</text>',
        ]

        def to_x(t: float) -> float:
            return pad_x + ((t - self.start_time_s) / span) * plot_w

        # 时间刻度与网格
        for i in range(6):
            t = self.start_time_s + i * span / 5
            x = to_x(t)
            svg_parts.append(f'<line x1="{x}" y1="{pad_y}" x2="{x}" y2="{pad_y+plot_h}" stroke="#33333e" stroke-dasharray="3,3"/>')
            svg_parts.append(f'<text x="{x}" y="{pad_y+plot_h+18}" fill="#888" text-anchor="middle">{t*1000:.1f}ms</text>')

        # 轨道背景
        track_h = 32
        y_fhss = pad_y + 10
        y_tdd = pad_y + 60
        y_pkt = pad_y + 115

        svg_parts.append(f'<text x="{pad_x-10}" y="{y_fhss+20}" fill="#aaa" text-anchor="end">FHSS</text>')
        svg_parts.append(f'<text x="{pad_x-10}" y="{y_tdd+20}" fill="#aaa" text-anchor="end">TDD</text>')
        svg_parts.append(f'<text x="{pad_x-10}" y="{y_pkt+20}" fill="#aaa" text-anchor="end">Packets</text>')

        colors = {
            "fhss_slot": "#2d5a88",
            "ground_tx": "#3b82f6",
            "turnaround": "#f59e0b",
            "air_rx": "#10b981",
            "slot_guard": "#6b7280",
            "actual_arrival": "#ef4444",
            "predicted_arrival": "#a855f7",
        }

        # 渲染事件块
        for e in self.events:
            x1 = to_x(e.start_time_s)
            x2 = to_x(e.end_time_s)
            w = max(2.0, x2 - x1)
            c = colors.get(e.category, "#4b5563")

            if e.category == "fhss_slot":
                svg_parts.append(f'<rect x="{x1}" y="{y_fhss}" width="{w}" height="{track_h}" fill="{c}" rx="4" stroke="#4875a6"/>')
                svg_parts.append(f'<text x="{x1+6}" y="{y_fhss+20}" fill="#fff" font-weight="bold">{e.name} {e.details}</text>')
            elif e.category in ("ground_tx", "turnaround", "air_rx", "slot_guard"):
                svg_parts.append(f'<rect x="{x1}" y="{y_tdd}" width="{w}" height="{track_h}" fill="{c}" rx="2"/>')
                if w > 20:
                    svg_parts.append(f'<text x="{x1+2}" y="{y_tdd+18}" fill="#fff" font-size="10">{e.name}</text>')
            elif e.category == "actual_arrival":
                svg_parts.append(f'<polygon points="{x1},{y_pkt} {x1-5},{y_pkt+12} {x1+5},{y_pkt+12}" fill="{c}"/>')
                svg_parts.append(f'<line x1="{x1}" y1="{y_pkt+12}" x2="{x1}" y2="{y_pkt+track_h}" stroke="{c}" stroke-width="2"/>')
            elif e.category == "predicted_arrival":
                svg_parts.append(f'<polygon points="{x1},{y_pkt+track_h} {x1-5},{y_pkt+track_h-12} {x1+5},{y_pkt+track_h-12}" fill="{c}"/>')
                if e.error_us is not None:
                    svg_parts.append(f'<text x="{x1}" y="{y_pkt+track_h+16}" fill="{c}" font-size="10" text-anchor="middle">{e.error_us:+.1f}µs</text>')

        # 图例
        svg_parts.append(f'<g transform="translate({pad_x}, {pad_y+plot_h+32})">')
        legend_items = [
            ("Ground TX", colors["ground_tx"]),
            ("Guard (810µs)", colors["turnaround"]),
            ("Air RX", colors["air_rx"]),
            ("Slot Guard", colors["slot_guard"]),
            ("Actual Preamble", colors["actual_arrival"]),
            ("Predicted", colors["predicted_arrival"]),
        ]
        cur_lx = 0
        for label, col in legend_items:
            svg_parts.append(f'<rect x="{cur_lx}" y="0" width="12" height="12" fill="{col}" rx="2"/>')
            svg_parts.append(f'<text x="{cur_lx+16}" y="10" fill="#bbb" font-size="11">{label}</text>')
            cur_lx += len(label) * 7 + 30
        svg_parts.append('</g>')

        svg_parts.append('</svg>')
        return "\n".join(svg_parts)

    def render_html(self, title: str = "FLRC FHSS + TDD 硬件时间轴") -> str:
        """生成可在浏览器内交互缩放的 HTML 页面。"""
        svg_content = self.render_svg()
        ascii_content = self.render_ascii()
        return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="utf-8">
    <title>{title}</title>
    <style>
        body {{ background: #121216; color: #e0e0e0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, monospace; margin: 20px; }}
        .card {{ background: #1a1a22; border-radius: 8px; padding: 20px; margin-bottom: 20px; box-shadow: 0 4px 12px rgba(0,0,0,0.4); }}
        h1, h2 {{ color: #60a5fa; }}
        pre {{ background: #0d0d11; padding: 15px; border-radius: 6px; overflow-x: auto; color: #34d399; font-size: 13px; }}
    </style>
</head>
<body>
    <div class="card">
        <h1>{title}</h1>
        <p>基于 USRP N310 硬件时间戳驱动的 FHSS 跳频时隙与 TDD 上下行交互时序视图。</p>
        <div>{svg_content}</div>
    </div>
    <div class="card">
        <h2>终端文本字符画 (ASCII Timeline)</h2>
        <pre>{ascii_content}</pre>
    </div>
</body>
</html>
"""
