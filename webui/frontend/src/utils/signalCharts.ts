export function buildSpectrumOption(points?: [number, number][]) {
  if (!points?.length) return { animation: false };

  return {
    backgroundColor: 'transparent',
    animation: false,
    tooltip: {
      trigger: 'axis',
      confine: true,
      transitionDuration: 0,
      axisPointer: { animation: false },
      backgroundColor: '#161b22',
      borderColor: '#30363d',
      textStyle: { color: '#f0f6fc' },
      formatter: (params: any) => {
        const p = params[0];
        const [frequency, power] = p.value;
        return `<div class="font-mono text-xs"><span class="text-cyan-400">频率:</span> ${(frequency / 1e6).toFixed(6)} MHz<br/><span class="text-emerald-400">功率密度:</span> ${power} dB/Hz</div>`;
      },
    },
    grid: {
      left: 55,
      right: 35,
      top: 32,
      bottom: 44,
    },
    xAxis: {
      type: 'value',
      // 频带边界取原始 FFT 数据，防止像素抽样后最后一个 bin 被省略而缩窄横轴。
      min: points[0][0],
      max: points[points.length - 1][0],
      splitNumber: 6,
      axisLine: { lineStyle: { color: '#30363d' } },
      axisTick: { lineStyle: { color: '#30363d' } },
      axisLabel: {
        color: '#8b949e',
        fontSize: 10,
        fontFamily: 'monospace',
        hideOverlap: true,
        formatter: (frequency: number) => (frequency / 1e6).toFixed(3),
      },
      name: 'MHz',
      nameTextStyle: { color: '#8b949e', fontSize: 10, fontFamily: 'monospace' },
    },
    yAxis: {
      type: 'value',
      axisLine: { lineStyle: { color: '#30363d' } },
      splitLine: { lineStyle: { color: '#1f242c', type: 'dashed' } },
      axisLabel: { color: '#8b949e', fontSize: 10, fontFamily: 'monospace' },
      name: 'dB/Hz',
      nameTextStyle: { color: '#8b949e', fontSize: 10, fontFamily: 'monospace' },
    },
    series: [
      {
        name: 'PSD',
        type: 'line',
        data: points,
        encode: { x: 0, y: 1 },
        // 按屏幕像素保留峰谷，避免高点数绘制成本及平均抽样丢失窄峰。
        sampling: 'minmax',
        smooth: false,
        showSymbol: false,
        emphasis: { disabled: true },
        lineStyle: { color: '#00e5ff', width: 2 },
        areaStyle: {
          color: {
            type: 'linear',
            x: 0,
            y: 0,
            x2: 0,
            y2: 1,
            colorStops: [
              { offset: 0, color: 'rgba(0, 229, 255, 0.45)' },
              { offset: 0.6, color: 'rgba(0, 229, 255, 0.12)' },
              { offset: 1, color: 'rgba(0, 229, 255, 0.0)' },
            ],
          },
        },
      },
    ],
  };
}

export function buildWaveformOption(points?: [number, number, number, number][], frameTime?: number | null) {
  if (!points?.length) return { animation: false };
  return {
    animation: false,
    backgroundColor: 'transparent',
    legend: { top: 4, textStyle: { color: '#8b949e', fontFamily: 'monospace' }, data: ['I', 'Q', '|IQ|'] },
    tooltip: { trigger: 'axis', confine: true, transitionDuration: 0, axisPointer: { animation: false },
      backgroundColor: '#161b22', borderColor: '#30363d', textStyle: { color: '#f0f6fc' },
      formatter: (items: any[]) => `${items[0].value[0].toFixed(6)} ms<br/>${items.map(item => `${item.seriesName}: ${item.value[1].toFixed(4)}`).join('<br/>')}` },
    grid: { left: 55, right: 40, top: 40, bottom: 44 },
    xAxis: { type: 'value', min: points[0][0], max: points[points.length - 1][0], name: 'ms',
      axisLabel: { color: '#8b949e', hideOverlap: true, formatter: (value: number) => value.toFixed(3) },
      axisLine: { lineStyle: { color: '#30363d' } }, splitLine: { show: false },
      nameTextStyle: { color: '#8b949e' } },
    yAxis: { type: 'value', name: '幅度', axisLabel: { color: '#8b949e' },
      nameTextStyle: { color: '#8b949e' }, splitLine: { lineStyle: { color: '#1f242c', type: 'dashed' } } },
    series: ['I', 'Q', '|IQ|'].map((name, index) => ({
      name, type: 'line', showSymbol: false, smooth: false, emphasis: { disabled: true },
      data: points.map(point => [point[0], point[index + 1]]),
      lineStyle: { width: index === 2 ? 1.5 : 1, color: ['#00e5ff', '#a78bfa', '#00ff66'][index] },
      ...(index === 2 && frameTime != null ? { markLine: { silent: true, symbol: 'none',
        lineStyle: { color: '#fbbf24', type: 'dashed' }, label: { formatter: '帧起点', color: '#fbbf24' },
        data: [{ xAxis: frameTime * 1000 }] } } : {}),
    })),
  };
}
