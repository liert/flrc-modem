import type { DuplexProbeResult } from '../types';

const outcomes: Record<string, string> = {
  candidate_found: '发现达到确认帧数的候选同步字', insufficient_frames: '发现候选，但有效帧数不足',
  not_observed: '监听范围内未捕获新同步字', error: '探测异常',
};
const statuses: Record<string, string> = {
  running: '监听中', finished: '观察结束', stopped: '已停止', interrupted: '已中断',
  failed: '编排失败，探测已停止', error: '探测异常', limit_reached: '执行上限，探测已停止',
};

export function DuplexProbePanel({ probe }: { probe?: DuplexProbeResult | null }) {
  if (!probe) return null;
  return <section aria-label="同步字探测结果" className="space-y-3 rounded-lg border border-cyan-900/60 bg-rf-bg/50 p-3">
    <h5 className="font-medium text-cyan-200">同步字探测 · {statuses[probe.status] || probe.status}</h5>
    <p className="text-zinc-400">{outcomes[probe.outcome || ''] || '与指令发送并行监听，等待有效 DK32S 地面帧。'}</p>
    <p className="font-mono text-zinc-500">中心频率 {probe.center_frequency_hz} Hz · 采样率 {probe.sample_rate} Sps · 确认门槛 {probe.min_frames} 帧</p>
    <p className="break-all text-zinc-500">监听频点：{probe.frequencies_hz.join(' / ')} Hz</p>
    {probe.candidates.map(item => <div key={item.sync_word_hex} className="space-y-1 rounded border border-rf-border p-2 font-mono text-zinc-300 select-text">
      <p>同步字 <strong className="text-cyan-300">{item.sync_word_hex}</strong> · {item.valid_frames} 个有效帧 · {item.confirmed ? '候选达标' : '待确认'}</p>
      <p className="text-zinc-500">硬件时间 {item.first_hw_time.toFixed(6)}–{item.last_hw_time.toFixed(6)} s · {item.frequencies_hz.join(' / ')} Hz</p>
    </div>)}
    <p className={probe.capture_degraded ? 'text-amber-400' : 'text-zinc-500'}>
      探测丢窗 {probe.dropped_windows} · 陈旧窗口 {probe.stale_windows} · 接收溢出 {probe.rx_overflows ?? 0} · 接收断流 {probe.rx_discontinuities ?? 0} · 接收跳窗 {probe.rx_windows_skipped ?? 0}
    </p>
    {probe.capture_degraded && <p className="text-amber-400">采集或处理存在缺口，本次可能漏检。</p>}
    {probe.error && <p role="alert" className="text-amber-400">{probe.error}</p>}
    <p className="text-zinc-500">候选经过物理层 CRC、DK32S 校验和与多帧确认；归属需结合设备验证。未捕获不代表切换失败。</p>
    <details className="text-zinc-500"><summary className="cursor-pointer">完整探测记录</summary>
      <pre className="mt-2 max-h-64 overflow-auto whitespace-pre-wrap break-all select-text">{JSON.stringify(probe, null, 2)}</pre>
    </details>
  </section>;
}
