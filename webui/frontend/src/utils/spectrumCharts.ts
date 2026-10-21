/**
 * SDR 实时频谱与瀑布图渲染工具：
 * 提供仪器级 ECharts 频谱配置、峰值保持、高对比度热图色标 LUT 及高性能瀑布图 Canvas 绘制。
 */

// Both plots reserve the same space for time/power ticks and the color scale.
export const SPECTRUM_PLOT_INSETS = { left: 64, right: 56 };

export interface SpectrumPlotConfig {
  freqMin: number;
  freqMax: number;
  rbwHz: number;
  powers: number[];
  peakHoldPowers?: number[] | null;
  historyPowers?: number[] | null;
  historyLabel?: string;
  windowAnalysisPowers?: { maxPowers?: number[]; avgPowers?: number[] } | null;
  refLevelDb: number;
  dynamicRangeDb: number;
  peakFreq?: number;
  peakPower?: number;
}

/**
 * 构建实时频谱 ECharts Option。
 * 横轴显示真实 RF 频率 (MHz)，纵轴标明相对功率 dBFS。
 */
export function buildRealtimeSpectrumOption(config: SpectrumPlotConfig) {
  const {
    freqMin,
    freqMax,
    rbwHz,
    powers,
    peakHoldPowers,
    historyPowers,
    historyLabel,
    windowAnalysisPowers,
    refLevelDb,
    dynamicRangeDb,
    peakFreq,
    peakPower,
  } = config;

  const nFft = powers.length;
  const minFreqMhz = freqMin / 1e6;
  const maxFreqMhz = freqMax / 1e6;
  const rbwMhz = rbwHz / 1e6;

  // 构造 [freqMhz, powerDbfs] 点阵
  const spectrumPoints: [number, number][] = new Array(nFft);
  for (let i = 0; i < nFft; i++) {
    spectrumPoints[i] = [minFreqMhz + i * rbwMhz, powers[i]];
  }

  const series: any[] = [
    {
      name: '当前频谱',
      type: 'line',
      data: spectrumPoints,
      sampling: 'minmax',
      smooth: false,
      showSymbol: false,
      emphasis: { disabled: true },
      lineStyle: { color: '#00e5ff', width: 1.8 },
      areaStyle: {
        color: {
          type: 'linear',
          x: 0,
          y: 0,
          x2: 0,
          y2: 1,
          colorStops: [
            { offset: 0, color: 'rgba(0, 229, 255, 0.40)' },
            { offset: 0.6, color: 'rgba(0, 229, 255, 0.10)' },
            { offset: 1, color: 'rgba(0, 229, 255, 0.0)' },
          ],
        },
      },
    },
  ];

  // 叠加峰值保持曲线 (Amber 色)
  if (peakHoldPowers && peakHoldPowers.length === nFft) {
    const holdPoints: [number, number][] = new Array(nFft);
    for (let i = 0; i < nFft; i++) {
      holdPoints[i] = [minFreqMhz + i * rbwMhz, peakHoldPowers[i]];
    }
    series.push({
      name: '峰值保持',
      type: 'line',
      data: holdPoints,
      sampling: 'max',
      smooth: false,
      showSymbol: false,
      emphasis: { disabled: true },
      lineStyle: { color: '#f59e0b', width: 1.2, type: 'dashed' },
    });
  }

  // 叠加历史回溯切片曲线 (荧光紫)
  if (historyPowers && historyPowers.length === nFft) {
    const historyPoints: [number, number][] = new Array(nFft);
    for (let i = 0; i < nFft; i++) {
      historyPoints[i] = [minFreqMhz + i * rbwMhz, historyPowers[i]];
    }
    series.push({
      name: historyLabel || '历史切片',
      type: 'line',
      data: historyPoints,
      sampling: 'minmax',
      smooth: false,
      showSymbol: false,
      emphasis: { disabled: true },
      lineStyle: { color: '#c084fc', width: 2, type: 'solid' },
      areaStyle: {
        color: {
          type: 'linear',
          x: 0,
          y: 0,
          x2: 0,
          y2: 1,
          colorStops: [
            { offset: 0, color: 'rgba(192, 132, 252, 0.35)' },
            { offset: 0.7, color: 'rgba(192, 132, 252, 0.08)' },
            { offset: 1, color: 'rgba(192, 132, 252, 0.0)' },
          ],
        },
      },
    });
  }

  // 叠加选区多帧分析包络 (选区峰值包络与平均能量)
  if (windowAnalysisPowers?.maxPowers && windowAnalysisPowers.maxPowers.length === nFft) {
    const maxPoints: [number, number][] = new Array(nFft);
    for (let i = 0; i < nFft; i++) {
      maxPoints[i] = [minFreqMhz + i * rbwMhz, windowAnalysisPowers.maxPowers[i]];
    }
    series.push({
      name: '选区峰值包络',
      type: 'line',
      data: maxPoints,
      sampling: 'max',
      smooth: false,
      showSymbol: false,
      emphasis: { disabled: true },
      lineStyle: { color: '#34d399', width: 1.6, type: 'dashed' },
    });
  }

  if (windowAnalysisPowers?.avgPowers && windowAnalysisPowers.avgPowers.length === nFft) {
    const avgPoints: [number, number][] = new Array(nFft);
    for (let i = 0; i < nFft; i++) {
      avgPoints[i] = [minFreqMhz + i * rbwMhz, windowAnalysisPowers.avgPowers[i]];
    }
    series.push({
      name: '选区平均能量',
      type: 'line',
      data: avgPoints,
      sampling: 'minmax',
      smooth: false,
      showSymbol: false,
      emphasis: { disabled: true },
      lineStyle: { color: '#60a5fa', width: 1.4, type: 'solid' },
    });
  }

  // 标记当前可见频段内的最高峰值
  if (powers.length && peakFreq !== undefined && peakPower !== undefined) {
    series[0].markPoint = {
      symbol: 'pin',
      symbolSize: 28,
      itemStyle: { color: '#00e5ff' },
      label: {
        formatter: () => `${peakPower.toFixed(1)}`,
        fontSize: 10,
        fontFamily: 'monospace',
        color: '#000',
        fontWeight: 'bold',
      },
      data: [
        {
          name: '峰值',
          coord: [peakFreq / 1e6, peakPower],
        },
      ],
    };
  }

  const yMin = Math.floor(refLevelDb - dynamicRangeDb);
  const yMax = Math.ceil(refLevelDb);

  return {
    backgroundColor: 'transparent',
    animation: false,
    tooltip: {
      trigger: 'axis',
      confine: true,
      transitionDuration: 0,
      axisPointer: {
        type: 'cross',
        animation: false,
        lineStyle: { color: '#38bdf8', type: 'dashed', width: 1 },
      },
      backgroundColor: '#161b22',
      borderColor: '#30363d',
      textStyle: { color: '#f0f6fc', fontFamily: 'monospace', fontSize: 11 },
      formatter: (params: any) => {
        if (!params || !params.length) return '';
        const [f, p] = params[0].value;
        let html = `<div class="font-mono text-xs leading-relaxed">
          <div class="text-zinc-400">RF 频率: <span class="text-cyan-400 font-bold">${f.toFixed(4)} MHz</span></div>
          <div class="text-zinc-400">实时功率: <span class="text-emerald-400 font-bold">${p.toFixed(1)} dBFS</span></div>`;
        for (let i = 1; i < params.length; i++) {
          const item = params[i];
          const val = item.value?.[1];
          if (typeof val !== 'number' || !Number.isFinite(val)) continue;
          if (item.seriesName === '峰值保持') {
            html += `<div class="text-zinc-400">峰值保持: <span class="text-amber-400 font-bold">${val.toFixed(1)} dBFS</span></div>`;
          } else if (item.seriesName && item.seriesName.includes('历史切片')) {
            html += `<div class="text-zinc-400">${item.seriesName}: <span class="text-purple-400 font-bold">${val.toFixed(1)} dBFS</span></div>`;
          } else if (item.seriesName === '选区峰值包络') {
            html += `<div class="text-zinc-400">选区峰值: <span class="text-emerald-300 font-bold">${val.toFixed(1)} dBFS</span></div>`;
          } else if (item.seriesName === '选区平均能量') {
            html += `<div class="text-zinc-400">选区均值: <span class="text-blue-400 font-bold">${val.toFixed(1)} dBFS</span></div>`;
          }
        }
        html += `</div>`;
        return html;
      },
    },
    legend: {
      show: series.length > 1,
      top: 4,
      right: 12,
      textStyle: { color: '#8b949e', fontSize: 10, fontFamily: 'monospace' },
      icon: 'roundRect',
      itemWidth: 12,
      itemHeight: 6,
    },
    grid: {
      ...SPECTRUM_PLOT_INSETS,
      top: 28,
      bottom: 50,
    },
    xAxis: {
      type: 'value',
      min: minFreqMhz,
      max: maxFreqMhz,
      splitNumber: 8,
      axisLine: { lineStyle: { color: '#30363d' } },
      axisTick: { lineStyle: { color: '#30363d' } },
      splitLine: { lineStyle: { color: '#1f242c', type: 'dashed' } },
      axisLabel: {
        color: '#8b949e',
        fontSize: 10,
        fontFamily: 'monospace',
        hideOverlap: true,
        formatter: (val: number) => val.toFixed(3),
      },
      name: 'MHz',
      nameTextStyle: { color: '#8b949e', fontSize: 10, fontFamily: 'monospace' },
    },
    yAxis: {
      type: 'value',
      min: yMin,
      max: yMax,
      splitNumber: 6,
      axisLine: { lineStyle: { color: '#30363d' } },
      axisTick: { lineStyle: { color: '#30363d' } },
      splitLine: { lineStyle: { color: '#1f242c', type: 'dashed' } },
      axisLabel: {
        color: '#8b949e',
        fontSize: 10,
        fontFamily: 'monospace',
        formatter: '{value}',
      },
      name: 'dBFS',
      nameTextStyle: { color: '#8b949e', fontSize: 10, fontFamily: 'monospace' },
    },
    dataZoom: [
      {
        type: 'inside',
        xAxisIndex: 0,
        zoomOnMouseWheel: true,
        moveOnMouseMove: true,
      },
      {
        type: 'slider',
        xAxisIndex: 0,
        height: 16,
        ...SPECTRUM_PLOT_INSETS,
        bottom: 8,
        borderColor: '#30363d',
        backgroundColor: '#0d1117',
        fillerColor: 'rgba(0, 229, 255, 0.15)',
        handleStyle: { color: '#00e5ff', borderColor: '#38bdf8' },
        textStyle: { color: '#8b949e', fontSize: 9, fontFamily: 'monospace' },
        labelFormatter: (val: number) => val.toFixed(2),
      },
    ],
    series,
  };
}

/** Fit uncalibrated float-IQ levels without changing RF gain or samples. */
export function fitSpectrumLevels(powers: number[], dynamicRangeDb = 80) {
  const finite = powers.filter(Number.isFinite).sort((a, b) => a - b);
  if (!finite.length) return null;
  const peak = finite[finite.length - 1];
  const floor = finite[Math.floor((finite.length - 1) * .05)];
  const refLevelDb = Math.min(20, Math.max(-220, Math.ceil((peak + 10) / 10) * 10));
  const range = Math.min(140, Math.max(20, dynamicRangeDb, Math.ceil((refLevelDb - floor + 10) / 10) * 10));
  return { refLevelDb, dynamicRangeDb: range };
}

/**
 * 经典高对比度热图色标查找表 (Turbo / RF Heat Colormap LUT)
 * 将 0..255 的归一化电平平滑映射为 RGB 颜色
 */
export function createTurboColormapLUT(): Uint8Array {
  const lut = new Uint8Array(256 * 3);
  // 控制锚点：[位移 0..1, R, G, B]
  const stops: [number, number, number, number][] = [
    [0.0, 13, 17, 23],     // 深暗底噪 (#0d1117)
    [0.2, 20, 60, 180],    // 幽蓝
    [0.4, 0, 180, 230],    // 高亮青
    [0.6, 20, 220, 90],    // 荧光绿
    [0.8, 250, 210, 0],    // 亮黄
    [1.0, 239, 68, 68],    // 强信号鲜红
  ];

  for (let i = 0; i < 256; i++) {
    const x = i / 255.0;
    let stopIdx = 0;
    while (stopIdx < stops.length - 1 && stops[stopIdx + 1][0] < x) {
      stopIdx++;
    }
    const [x0, r0, g0, b0] = stops[stopIdx];
    const [x1, r1, g1, b1] = stops[Math.min(stopIdx + 1, stops.length - 1)];
    const t = x1 > x0 ? (x - x0) / (x1 - x0) : 0.0;

    lut[i * 3 + 0] = Math.round(r0 + t * (r1 - r0));
    lut[i * 3 + 1] = Math.round(g0 + t * (g1 - g0));
    lut[i * 3 + 2] = Math.round(b0 + t * (b1 - b0));
  }
  return lut;
}

const TURBO_LUT = createTurboColormapLUT();

/**
 * 将单行功率值 (dBFS) 渲染为 ImageData 并绘制到瀑布图 Canvas 顶部。
 * 支持滚动画布、断流/丢帧隔离红线渲染。
 */
export function renderWaterfallRow(
  canvas: HTMLCanvasElement,
  powers: number[],
  refLevelDb: number,
  dynamicRangeDb: number,
  isGap = false,
  rowHeight = 2,
  advanceRows = rowHeight
) {
  const ctx = canvas.getContext('2d');
  if (!ctx) return;

  const width = canvas.width;
  const height = canvas.height;
  if (width <= 0 || height <= 0) return;

  rowHeight = Math.min(height, Math.max(1, Math.round(rowHeight)));
  advanceRows = Math.min(height, Math.max(rowHeight, Math.round(advanceRows)));
  // Fixed time scale: skipped intervals occupy blank space rather than being
  // compressed into a visually continuous signal.
  if (advanceRows < height) ctx.drawImage(canvas, 0, 0, width, height - advanceRows, 0, advanceRows, width, height - advanceRows);
  if (advanceRows > rowHeight) {
    ctx.fillStyle = '#0d1117';
    ctx.fillRect(0, 0, width, advanceRows);
  }

  // 2. 若发生断流、丢帧或频率改变，绘制明显隔离线
  if (isGap) {
    ctx.fillStyle = '#ef4444';
    ctx.fillRect(0, rowHeight, width, rowHeight);
  }

  // 3. 构建当前行 ImageData
  const rowImg = ctx.createImageData(width, rowHeight);
  const data = rowImg.data;
  const nFft = powers.length;
  const minDb = refLevelDb - dynamicRangeDb;

  for (let x = 0; x < width; x++) {
    // 将屏幕像素 x 线性映射到 FFT bin
    const first = Math.floor((x / width) * nFft);
    const end = Math.max(first + 1, Math.ceil(((x + 1) / width) * nFft));
    let p = minDb;
    for (let bin = first; bin < end; bin++) if (Number.isFinite(powers[bin])) p = Math.max(p, powers[bin]);
    // 归一化到 0..255
    const norm = Math.max(0, Math.min(255, Math.round(((p - minDb) / dynamicRangeDb) * 255)));

    const r = TURBO_LUT[norm * 3 + 0];
    const g = TURBO_LUT[norm * 3 + 1];
    const b = TURBO_LUT[norm * 3 + 2];

    for (let dy = 0; dy < rowHeight; dy++) {
      const idx = (dy * width + x) * 4;
      data[idx + 0] = r;
      data[idx + 1] = g;
      data[idx + 2] = b;
      data[idx + 3] = 255;
    }
  }

  ctx.putImageData(rowImg, 0, 0);
}

export function waterfallTransition(previous: { center_freq: number; sample_rate: number; n_fft: number; timestamp: number } | null,
  frame: { center_freq: number; sample_rate: number; n_fft: number; timestamp: number; discontinuity?: boolean }) {
  const parametersChanged = !!previous && (frame.center_freq !== previous.center_freq || frame.sample_rate !== previous.sample_rate || frame.n_fft !== previous.n_fft);
  const delta = previous ? frame.timestamp - previous.timestamp : .1;
  return { parametersChanged, gap: parametersChanged || !!frame.discontinuity || delta > .5 || delta <= 0,
    advanceRows: Math.min(400, Math.max(0, delta) * 20) };
}

/**
 * 瀑布图二维时空选区模型 (Space-Time Selection Box)
 */
export interface WaterfallSelection {
  freqStartMhz: number;
  freqEndMhz: number;
  centerFreqMhz: number;
  bandwidthKhz: number;
  timeStart: number; // 绝对时间戳 (秒)
  timeEnd: number;   // 绝对时间戳 (秒)
  durationS: number; // 持续时间 (秒)
  xStartPct: number; // 选区在容器中的百分比坐标 (0..100)
  xEndPct: number;
  yStartPct: number;
  yEndPct: number;
}

/**
 * 选区时间窗内多帧能量统计模型
 */
export interface WindowAnalysisResult {
  maxPowers: number[];
  avgPowers: number[];
  peakFreqMhz: number;
  peakPowerDb: number;
  frameCount: number;
}

/**
 * 从历史帧缓冲中找到距离目标时间戳最近的一帧索引
 */
export function findNearestHistoryFrame(
  frames: Array<{ timestamp: number }>,
  targetTimestamp: number
): number {
  if (!frames || !frames.length) return -1;
  let closestIdx = 0;
  let minDiff = Math.abs(frames[0].timestamp - targetTimestamp);
  for (let i = 1; i < frames.length; i++) {
    const diff = Math.abs(frames[i].timestamp - targetTimestamp);
    if (diff < minDiff) {
      minDiff = diff;
      closestIdx = i;
    }
  }
  return closestIdx;
}

/**
 * 根据瀑布图视口相对百分比坐标与缩放状态计算真实的物理频段与时间窗口
 */
export function calculateWaterfallSelection(
  x1Frac: number,
  x2Frac: number,
  y1Frac: number,
  y2Frac: number,
  zoom: { start: number; end: number },
  freqMinMhz: number,
  freqMaxMhz: number,
  newestTimestamp: number,
  timeSpanS = 20
): WaterfallSelection {
  const minXFrac = Math.max(0, Math.min(1, Math.min(x1Frac, x2Frac)));
  const maxXFrac = Math.max(0, Math.min(1, Math.max(x1Frac, x2Frac)));
  const minYFrac = Math.max(0, Math.min(1, Math.min(y1Frac, y2Frac)));
  const maxYFrac = Math.max(0, Math.min(1, Math.max(y1Frac, y2Frac)));

  const zoomSpan = Math.max(0.01, (zoom.end - zoom.start) / 100);
  const fullF1 = zoom.start / 100 + minXFrac * zoomSpan;
  const fullF2 = zoom.start / 100 + maxXFrac * zoomSpan;

  const totalFreqSpan = Math.max(0, freqMaxMhz - freqMinMhz);
  const freqStartMhz = Number((freqMinMhz + fullF1 * totalFreqSpan).toFixed(6));
  const freqEndMhz = Number((freqMinMhz + fullF2 * totalFreqSpan).toFixed(6));
  const centerFreqMhz = Number(((freqStartMhz + freqEndMhz) / 2).toFixed(6));
  const bandwidthKhz = Number(((freqEndMhz - freqStartMhz) * 1000).toFixed(3));

  // 瀑布图顶部 y=0 对应最新时刻，y=1 对应最久远时刻
  const timeEnd = Number((newestTimestamp - minYFrac * timeSpanS).toFixed(4));
  const timeStart = Number((newestTimestamp - maxYFrac * timeSpanS).toFixed(4));
  const durationS = Number(Math.max(0.01, timeEnd - timeStart).toFixed(3));

  return {
    freqStartMhz,
    freqEndMhz,
    centerFreqMhz,
    bandwidthKhz,
    timeStart,
    timeEnd,
    durationS,
    xStartPct: Number((minXFrac * 100).toFixed(2)),
    xEndPct: Number((maxXFrac * 100).toFixed(2)),
    yStartPct: Number((minYFrac * 100).toFixed(2)),
    yEndPct: Number((maxYFrac * 100).toFixed(2)),
  };
}

/**
 * 提取选区时间窗内的所有历史帧，计算峰值包络与物理平均功率谱
 */
export function analyzeWindowFrames(
  frames: Array<{ timestamp: number; powers: number[]; rbw_hz: number; freq_min: number }>,
  timeStart: number,
  timeEnd: number
): WindowAnalysisResult | null {
  if (!frames || !frames.length) return null;
  const tMin = Math.min(timeStart, timeEnd);
  const tMax = Math.max(timeStart, timeEnd);
  // 留出 50ms 宽容度
  const filtered = frames.filter(f => f.timestamp >= tMin - 0.05 && f.timestamp <= tMax + 0.05);
  if (!filtered.length || !filtered[0].powers?.length) return null;

  const nFft = filtered[0].powers.length;
  const maxPowers = new Array(nFft).fill(-Infinity);
  const linearSum = new Array(nFft).fill(0);
  let globalPeakPower = -Infinity;
  let globalPeakBin = 0;

  for (const f of filtered) {
    if (f.powers.length !== nFft) continue;
    for (let i = 0; i < nFft; i++) {
      const p = f.powers[i];
      if (p > maxPowers[i]) maxPowers[i] = p;
      linearSum[i] += Math.pow(10, p / 10);
      if (p > globalPeakPower) {
        globalPeakPower = p;
        globalPeakBin = i;
      }
    }
  }

  const count = filtered.length;
  const avgPowers = new Array(nFft);
  for (let i = 0; i < nFft; i++) {
    const meanLinear = linearSum[i] / count;
    avgPowers[i] = meanLinear > 1e-25 ? Number((10 * Math.log10(meanLinear)).toFixed(2)) : -140;
    maxPowers[i] = Number(maxPowers[i].toFixed(2));
  }

  const refFrame = filtered[0];
  const peakFreqMhz = Number(((refFrame.freq_min + globalPeakBin * refFrame.rbw_hz) / 1e6).toFixed(6));

  return {
    maxPowers,
    avgPowers,
    peakFreqMhz,
    peakPowerDb: Number(globalPeakPower.toFixed(1)),
    frameCount: count,
  };
}

