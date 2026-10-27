import React from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Activity,
  ArrowDownToLine,
  ArrowRight,
  ArrowUpRight,
  AudioLines,
  Check,
  CheckCircle2,
  ChevronRight,
  CircleDashed,
  Clock3,
  Cpu,
  FileCode2,
  FolderOpen,
  Layers3,
  Radio,
  Radar,
  RefreshCw,
  SlidersHorizontal,
  Terminal,
  Workflow,
  XCircle,
  Zap,
} from "lucide-react";
import { api } from "../api/client";
import type { DeviceInfo, JobStatus, TemplateItem } from "../types";
import { useWorkstationStore } from "../stores/useWorkstationStore";
import { fileStateLabel } from "../utils/analysis";
import { runTitle, runTime } from "../utils/runs";
import "../styles/dashboard.css";

const QUICK_ACTIONS = [
  {
    id: "modulate",
    title: "信号调制",
    code: "MODULATE",
    description: "从原始载荷生成 FLRC IQ 信号",
    icon: Zap,
    tone: "cyan",
  },
  {
    id: "demod",
    title: "离线解调",
    code: "DEMODULATE",
    description: "解析 IQ 采样，提取与校验数据帧",
    icon: AudioLines,
    tone: "blue",
  },
  {
    id: "scan",
    title: "信道盲扫",
    code: "CHANNEL SCAN",
    description: "发现 2.4 GHz 频段中的活跃信道",
    icon: Radar,
    tone: "violet",
  },
  {
    id: "sync",
    title: "跳频同步",
    code: "FHSS / TDD",
    description: "跟踪跳频序列与硬件时隙",
    icon: Clock3,
    tone: "amber",
  },
  {
    id: "rx",
    title: "射频采集",
    code: "IQ CAPTURE",
    description: "连接 SDR，将空口信号保存为文件",
    icon: ArrowDownToLine,
    tone: "blue",
  },
  {
    id: "duplex",
    title: "双工收发",
    code: "DUPLEX",
    description: "在同步时隙中完成指令与响应交互",
    icon: Layers3,
    tone: "cyan",
  },
  {
    id: "transmit",
    title: "调制发射",
    code: "TRANSMIT",
    description: "配置载荷与射频参数，进入发射工作台",
    icon: Radio,
    tone: "amber",
  },
  {
    id: "devices",
    title: "设备与诊断",
    code: "DIAGNOSTICS",
    description: "查看设备连接、占用与诊断信息",
    icon: Cpu,
    tone: "violet",
  },
] as const;

const RUN_STATUS: Record<
  JobStatus,
  { label: string; tone: string; icon: typeof Check }
> = {
  SUCCEEDED: { label: "已完成", tone: "success", icon: CheckCircle2 },
  RUNNING: { label: "运行中", tone: "blue", icon: Activity },
  QUEUED: { label: "排队中", tone: "amber", icon: Clock3 },
  FAILED: { label: "失败", tone: "red", icon: XCircle },
  CANCELLED: { label: "已取消", tone: "muted", icon: CircleDashed },
};

// Static signal-flow illustration, separate from live RF measurements.
const SignalIllustration = () => (
  <div className="dash-signal" aria-hidden="true">
    <svg viewBox="0 0 420 260" fill="none">
      <defs>
        <linearGradient
          id="dash-wave"
          x1="0"
          y1="130"
          x2="420"
          y2="130"
          gradientUnits="userSpaceOnUse"
        >
          <stop stopColor="var(--rf-cyan)" stopOpacity="0" />
          <stop offset=".25" stopColor="var(--rf-cyan)" />
          <stop offset=".7" stopColor="var(--rf-cyan)" />
          <stop offset="1" stopColor="var(--rf-cyan)" stopOpacity="0" />
        </linearGradient>
        <radialGradient id="dash-signal-glow">
          <stop stopColor="var(--rf-cyan)" stopOpacity=".1" />
          <stop offset="1" stopColor="var(--rf-cyan)" stopOpacity="0" />
        </radialGradient>
        <pattern
          id="dash-grid"
          width="24"
          height="24"
          patternUnits="userSpaceOnUse"
        >
          <path d="M24 0H0V24" stroke="var(--rf-cyan)" strokeOpacity=".045" />
        </pattern>
      </defs>
      <path fill="url(#dash-grid)" d="M0 0h420v260H0z" />
      <circle cx="220" cy="130" r="126" fill="url(#dash-signal-glow)" />
      <g stroke="var(--rf-cyan)">
        <circle cx="220" cy="130" r="104" strokeOpacity=".1" />
        <circle
          cx="220"
          cy="130"
          r="78"
          strokeOpacity=".15"
          strokeDasharray="3 7"
        />
        <circle cx="220" cy="130" r="51" strokeOpacity=".2" />
      </g>
      <path
        d="M0 130h58l5-8 6 16 8-25 7 34 8-17h18l7-27 8 53 8-79 9 106 9-120 9 103 8-62 8 40 7-17h128l7-12 8 31 8-54 8 68 8-52 7 19h39"
        stroke="url(#dash-wave)"
        strokeWidth="1.8"
        strokeLinejoin="round"
      />
      <rect
        x="188"
        y="98"
        width="64"
        height="64"
        rx="17"
        fill="var(--rf-panel)"
        stroke="var(--rf-cyan)"
        strokeOpacity=".6"
      />
      <rect
        x="196"
        y="106"
        width="48"
        height="48"
        rx="11"
        stroke="var(--rf-cyan)"
        strokeOpacity=".15"
      />
      <path
        d="M205 130h6l5-12 8 24 5-12h6"
        stroke="var(--rf-cyan)"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <circle cx="220" cy="26" r="4" fill="var(--rf-cyan)" />
      <circle cx="316" cy="170" r="4" fill="var(--rf-surface)" stroke="var(--rf-cyan)" />
      <circle cx="152" cy="209" r="3" fill="var(--rf-cyan)" fillOpacity=".65" />
      <g
        fontFamily="var(--rf-font-mono)"
        fontSize="10"
        letterSpacing="1.5"
        fill="var(--rf-muted)"
      >
        <text x="234" y="30">
          CAPTURE
        </text>
        <text x="326" y="174">
          DECODE
        </text>
        <text x="164" y="213">
          ANALYZE
        </text>
      </g>
      <path
        d="M18 30V18h12M390 18h12v12M18 230v12h12M390 242h12v-12"
        stroke="var(--rf-cyan)"
        strokeOpacity=".25"
      />
    </svg>
    <span className="dash-signal-caption">
      SIGNAL WORKFLOW <span>· 流程示意</span>
    </span>
  </div>
);

function DeviceRow({
  device,
  name,
  type,
  loading,
  error,
  onClick,
}: {
  device?: DeviceInfo;
  name: string;
  type: "usrp" | "hackrf";
  loading: boolean;
  error: boolean;
  onClick: () => void;
}) {
  const Icon = type === "usrp" ? Cpu : Radio;
  const status = device?.status;
  const tone =
    error || status === "error"
      ? "red"
      : status === "online"
        ? "success"
        : status === "busy"
          ? "amber"
          : "muted";
  const label = loading
    ? "检测中"
    : error
      ? "获取失败"
      : status === "online"
        ? "已就绪"
        : status === "busy"
          ? "占用中"
          : status === "error"
            ? "异常"
            : "未连接";
  return (
    <button type="button" className="dash-device" onClick={onClick}>
      <span
        className={`dash-icon ${type === "usrp" ? "tone-cyan" : "tone-violet"}`}
      >
        <Icon size={20} aria-hidden="true" />
      </span>
      <span className="dash-device-copy">
        <strong>{name}</strong>
        <small>{type === "usrp" ? "NETWORK · UHD" : "USB · SDR"}</small>
      </span>
      <span className={`dash-badge tone-${tone}`}>
        <i />
        {label}
      </span>
      <ChevronRight
        size={14}
        className="dash-device-arrow"
        aria-hidden="true"
      />
    </button>
  );
}

function EmptyState({
  icon: Icon,
  title,
  description,
  action,
  onAction,
}: {
  icon: typeof FolderOpen;
  title: string;
  description: string;
  action?: string;
  onAction?: () => void;
}) {
  return (
    <div className="dash-empty">
      <span className="dash-empty-icon">
        <Icon size={24} strokeWidth={1.4} aria-hidden="true" />
      </span>
      <strong>{title}</strong>
      <p>{description}</p>
      {action && (
        <button type="button" className="dash-text-button" onClick={onAction}>
          {action}
          <ArrowRight size={14} aria-hidden="true" />
        </button>
      )}
    </div>
  );
}

export const DashboardView: React.FC = () => {
  const setActiveTab = useWorkstationStore((s) => s.setActiveTab);
  const openDeviceDiagnostics = useWorkstationStore((s) => s.openDeviceDiagnostics);
  const setSelectedCommandId = useWorkstationStore(
    (s) => s.setSelectedCommandId,
  );
  const setSelectedRunId = useWorkstationStore((s) => s.setSelectedRunId);
  const setSelectedArtifactPath = useWorkstationStore(
    (s) => s.setSelectedArtifactPath,
  );
  const setSelectedTemplate = useWorkstationStore((s) => s.setSelectedTemplate);
  const queryClient = useQueryClient();
  const [refreshing, setRefreshing] = React.useState(false);
  const [lastRefresh, setLastRefresh] = React.useState<Date | null>(null);
  const devicesQuery = useQuery({
    queryKey: ["devices"],
    queryFn: api.getDevices,
    refetchInterval: 5000,
  });
  const runsQuery = useQuery({
    queryKey: ["runs", 10, 0],
    queryFn: () => api.getRuns(10, 0),
    refetchInterval: 3000,
  });
  const artifactsQuery = useQuery({
    queryKey: ["artifacts"],
    queryFn: api.getAllArtifacts,
    refetchInterval: 5000,
  });
  const templatesQuery = useQuery({
    queryKey: ["templates"],
    queryFn: () => api.getTemplates(),
  });
  const { data: devices } = devicesQuery;
  const { data: runs } = runsQuery;
  const { data: artifacts } = artifactsQuery;
  const { data: templates } = templatesQuery;
  const availableDevices =
    devices?.filter((d) => d.status === "online" || d.status === "busy")
      .length ?? 0;
  const activeRuns = runs?.filter((r) => r.status === "RUNNING") ?? [];
  const completedRuns =
    runs?.filter((r) => r.status === "SUCCEEDED" || r.status === "FAILED") ??
    [];
  const failedCount = completedRuns.filter((r) => r.status === "FAILED").length;
  const successRate = completedRuns.length
    ? Math.round(
        ((completedRuns.length - failedCount) / completedRuns.length) * 100,
      )
    : null;
  const recentArtifacts = artifacts?.slice(0, 4) ?? [];
  const featuredTemplates =
    templates?.filter((t) => t.category === "physical_config").slice(0, 3) ??
    [];
  const errors = [
    devicesQuery.isError && "设备",
    runsQuery.isError && "任务",
    artifactsQuery.isError && "文件",
    templatesQuery.isError && "模板",
  ].filter(Boolean);
  const now = new Date();
  const refresh = async () => {
    setRefreshing(true);
    try {
      await Promise.all(
        ["devices", "runs", "artifacts", "templates"].map((key) =>
          queryClient.invalidateQueries({ queryKey: [key] }),
        ),
      );
      setLastRefresh(new Date());
    } finally {
      setRefreshing(false);
    }
  };
  const launch = (target: string) => {
    if (target === "devices") {
      openDeviceDiagnostics();
      return;
    }
    if (
      target === "scan" ||
      target === "sync" ||
      target === "spectrum" ||
      target === "transmit"
    )
      setActiveTab(target);
    else {
      setSelectedCommandId(target);
      setActiveTab("commands");
    }
  };
  const inspectRun = (id: string) => {
    setSelectedRunId(id);
    setActiveTab("tasks");
  };
  const launchTemplate = (template: TemplateItem) => {
    setSelectedTemplate(template);
    if (template.category === "duplex_plan") setActiveTab("templates");
    else {
      setSelectedCommandId(template.applicable_commands[0] || "demod");
      setActiveTab("commands");
    }
  };

  return (
    <div className="dashboard">
      <div className="dash-page-heading">
        <div>
          <div className="dash-eyebrow">
            WORKSPACE <span>/</span> OVERVIEW
          </div>
          <h1>
            工作台总览<span className="dash-heading-dot">.</span>
          </h1>
          <p>连接设备，探索信号，让每一次实验井然有序。</p>
        </div>
        <div className="dash-page-tools">
          <time dateTime={now.toISOString()}>
            {new Intl.DateTimeFormat("zh-CN", {
              month: "long",
              day: "numeric",
              weekday: "long",
            }).format(now)}
          </time>
          <button
            type="button"
            className="dash-button dash-button-quiet"
            onClick={refresh}
            disabled={refreshing}
          >
            <RefreshCw
              size={14}
              className={refreshing ? "dash-spinning" : ""}
              aria-hidden="true"
            />
            {refreshing ? "刷新中…" : "刷新总览"}
          </button>
        </div>
      </div>
      <div className="dash-announcement" role="status" aria-live="polite">
        {errors.length ? (
          <span className="tone-red">
            {errors.join("、")}数据获取失败，请刷新重试。
          </span>
        ) : lastRefresh ? (
          `已更新总览 · ${lastRefresh.toLocaleTimeString("zh-CN", { hour12: false })}`
        ) : null}
      </div>
      <div className="dash-top-grid">
        <section className="dash-hero" aria-labelledby="dash-hero-title">
          <div className="dash-hero-content">
            <span className="dash-hero-tag">
              <span />
              FLRC SIGNAL LAB
            </span>
            <h2 id="dash-hero-title">
              从空口到协议，
              <br />
              <em>让信号清晰可见。</em>
            </h2>
            <p>
              采集、解调、分析与收发。
              <br />
              你的无线信号实验，在这里展开。
            </p>
            <div className="dash-hero-actions">
              <button
                type="button"
                className="dash-button dash-button-primary"
                onClick={() => launch("spectrum")}
              >
                <Activity size={16} aria-hidden="true" />
                打开实时频谱
                <ArrowUpRight size={16} aria-hidden="true" />
              </button>
              <button
                type="button"
                className="dash-hero-link"
                onClick={() => launch("demod")}
              >
                离线解调
                <ArrowRight size={14} aria-hidden="true" />
              </button>
            </div>
          </div>
          <SignalIllustration />
          <div className="dash-hero-footer">
            <span>
              <span className="dash-cross">+</span> SX1280 FLRC
            </span>
            <span>
              IQ CAPTURE <ChevronRight size={10} aria-hidden="true" /> FRAME DECODE{" "}
              <ChevronRight size={10} aria-hidden="true" /> PROTOCOL
            </span>
          </div>
        </section>
        <section
          className="dash-panel dash-hardware"
          aria-labelledby="dash-hardware-title"
        >
          <div className="dash-section-heading">
            <h2 id="dash-hardware-title">设备连接</h2>
            <span className="dash-caption">HARDWARE</span>
          </div>
          <DeviceRow
            device={devices?.find((d) => d.device_type === "usrp")}
            name="USRP N310"
            type="usrp"
            loading={devicesQuery.isPending}
            error={devicesQuery.isError}
            onClick={openDeviceDiagnostics}
          />
          <DeviceRow
            device={devices?.find((d) => d.device_type === "hackrf")}
            name="HackRF One"
            type="hackrf"
            loading={devicesQuery.isPending}
            error={devicesQuery.isError}
            onClick={openDeviceDiagnostics}
          />
          <div className="dash-hardware-note">
            <span
              className={`dash-status-dot ${availableDevices ? "tone-success" : "tone-amber"}`}
            />
            <p>
              {devicesQuery.isPending
                ? "正在检测射频设备…"
                : devicesQuery.isError
                  ? "设备状态暂不可用，请刷新重试。"
                  : availableDevices
                    ? `${availableDevices} 台设备已连接，可进入工作台配置。`
                    : "连接 SDR 设备以开始实时采集，离线分析随时可用。"}
            </p>
          </div>
          <button
            type="button"
            className="dash-text-button"
            onClick={openDeviceDiagnostics}
          >
            设备与诊断
            <ArrowRight size={14} aria-hidden="true" />
          </button>
        </section>
      </div>
      <section className="dash-stats" aria-label="工作台关键指标">
        <button
          type="button"
          className="dash-stat"
          onClick={openDeviceDiagnostics}
        >
          <div className="dash-stat-top">
            <span>已连接设备</span>
            <Cpu size={16} aria-hidden="true" />
          </div>
          <div className="dash-stat-value">
            {devices ? String(availableDevices).padStart(2, "0") : "—"}
            <small>
              / {devices ? String(devices.length).padStart(2, "0") : "—"}
            </small>
          </div>
          <span className="dash-stat-note">
            <span
              className={`dash-status-dot ${availableDevices ? "tone-success" : "tone-muted"}`}
            />
            {devicesQuery.isError
              ? "状态获取失败"
              : devicesQuery.isPending
                ? "正在检测设备"
                : availableDevices
                  ? "射频设备已连接"
                  : "等待硬件接入"}
          </span>
        </button>
        <button
          type="button"
          className="dash-stat"
          onClick={() => setActiveTab("tasks")}
        >
          <div className="dash-stat-top">
            <span>运行中任务</span>
            <Activity size={16} aria-hidden="true" />
          </div>
          <div className="dash-stat-value">
            {runs ? String(activeRuns.length).padStart(2, "0") : "—"}
            <small>个任务</small>
          </div>
          <span className="dash-stat-note">
            {runsQuery.isError
              ? "任务获取失败"
              : runsQuery.isPending
                ? "正在读取任务"
                : activeRuns[0]
                  ? runTitle(activeRuns[0])
                  : "工作台空闲，随时开始"}
          </span>
        </button>
        <button
          type="button"
          className="dash-stat"
          onClick={() => setActiveTab("tasks")}
        >
          <div className="dash-stat-top">
            <span>近期任务成功率</span>
            <CheckCircle2 size={16} aria-hidden="true" />
          </div>
          <div className="dash-stat-value">
            {successRate === null ? "—" : successRate}
            <small>{successRate === null ? "暂无统计" : "%"}</small>
          </div>
          <span className="dash-stat-note">
            {runsQuery.isError
              ? "任务获取失败"
              : runsQuery.isPending
                ? "正在读取任务"
                : completedRuns.length
                  ? `最近 ${runs?.length} 次任务 · ${failedCount} 次失败`
                  : "完成首次任务后生成统计"}
          </span>
        </button>
        <button
          type="button"
          className="dash-stat"
          onClick={() => setActiveTab("signals")}
        >
          <div className="dash-stat-top">
            <span>信号与文件产物</span>
            <FolderOpen size={16} aria-hidden="true" />
          </div>
          <div className="dash-stat-value">
            {artifacts ? String(artifacts.length).padStart(2, "0") : "—"}
            <small>个文件</small>
          </div>
          <span className="dash-stat-note">
            {artifactsQuery.isError
              ? "文件获取失败"
              : artifactsQuery.isPending
                ? "正在读取文件"
                : "采样数据、解调结果与报告"}
            <ArrowUpRight size={12} aria-hidden="true" />
          </span>
        </button>
      </section>
      <div className="dash-work-grid">
        <section
          className="dash-panel dash-actions"
          aria-labelledby="dash-actions-title"
        >
          <div className="dash-section-heading">
            <div>
              <h2 id="dash-actions-title">
                开始一项实验<span className="dash-caption">QUICK START</span>
              </h2>
              <p>从常用工具出发，快速进入工作状态。</p>
            </div>
            <button
              type="button"
              className="dash-text-button"
              onClick={() => setActiveTab("commands")}
            >
              全部命令
              <ArrowUpRight size={14} aria-hidden="true" />
            </button>
          </div>
          <div className="dash-action-grid">
            {QUICK_ACTIONS.map(
              ({ id, title, code, description, icon: Icon, tone }) => (
                <button
                  type="button"
                  key={id}
                  className="dash-action"
                  onClick={() => launch(id)}
                >
                  <span className={`dash-icon tone-${tone}`}>
                    <Icon size={19} strokeWidth={1.7} aria-hidden="true" />
                  </span>
                  <span className="dash-action-copy">
                    <strong>
                      {title}
                      <small>{code}</small>
                    </strong>
                    <span>{description}</span>
                  </span>
                  <ArrowUpRight
                    className="dash-action-arrow"
                    size={16}
                    aria-hidden="true"
                  />
                </button>
              ),
            )}
          </div>
          <button
            type="button"
            className="dash-pipeline-link"
            onClick={() => setActiveTab("workbench")}
          >
            <Workflow size={17} aria-hidden="true" />
            <span>
              需要组合多个处理步骤？<strong>打开数据流工作台</strong>
            </span>
            <ArrowRight size={15} aria-hidden="true" />
          </button>
        </section>
        <section
          className="dash-panel dash-recent"
          aria-labelledby="dash-recent-title"
        >
          <div className="dash-section-heading">
            <h2 id="dash-recent-title">
              最近任务<span className="dash-count">{runs?.length ?? "—"}</span>
            </h2>
            <button
              type="button"
              className="dash-text-button"
              onClick={() => setActiveTab("tasks")}
            >
              查看全部
              <ArrowUpRight size={14} aria-hidden="true" />
            </button>
          </div>
          {runsQuery.isPending ? (
            <div className="dash-loading" role="status">
              正在加载任务…
            </div>
          ) : runsQuery.isError && !runs ? (
            <EmptyState
              icon={XCircle}
              title="任务暂不可用"
              description="请检查服务连接后刷新总览。"
              action="重新加载"
              onAction={refresh}
            />
          ) : runs?.length ? (
            <div className="dash-run-list">
              {runs.slice(0, 4).map((run) => {
                const status = RUN_STATUS[run.status];
                const Icon = status.icon;
                return (
                  <button
                    type="button"
                    className="dash-run"
                    key={run.id}
                    onClick={() => inspectRun(run.id)}
                  >
                    <span className={`dash-run-icon tone-${status.tone}`}>
                      <Icon size={16} aria-hidden="true" />
                    </span>
                    <span className="dash-run-copy">
                      <strong>{runTitle(run)}</strong>
                      <small title={run.error || run.id}>
                        {run.error || `${run.command_id} · ${run.id.slice(-8)}`}
                      </small>
                      <time dateTime={run.created_at}>
                        {runTime(run.created_at)}
                      </time>
                    </span>
                    <span className={`dash-run-state tone-${status.tone}`}>
                      {status.label}
                    </span>
                  </button>
                );
              })}
            </div>
          ) : (
            <EmptyState
              icon={Terminal}
              title="下一次实验，从这里开始"
              description="运行调制、采集或解调后，进度与结果将显示在这里。"
              action="创建第一个任务"
              onAction={() => launch("modulate")}
            />
          )}
        </section>
      </div>
      <div className="dash-work-grid dash-bottom-grid">
        <section className="dash-panel" aria-labelledby="dash-templates-title">
          <div className="dash-section-heading">
            <h2 id="dash-templates-title">
              精选物理层预设<span className="dash-caption">PRESETS</span>
            </h2>
            <button
              type="button"
              className="dash-text-button"
              onClick={() => setActiveTab("templates")}
            >
              模板库
              <ArrowUpRight size={14} aria-hidden="true" />
            </button>
          </div>
          <div className="dash-template-grid">
            {featuredTemplates.map((template, index) => (
              <button
                type="button"
                className="dash-template"
                key={template.path}
                onClick={() => launchTemplate(template)}
              >
                <div>
                  <span className="dash-template-index">0{index + 1}</span>
                  <SlidersHorizontal size={16} aria-hidden="true" />
                  <span className="dash-template-format">
                    {template.format}
                  </span>
                </div>
                <strong>{template.title || template.name}</strong>
                <p>{template.description || template.name}</p>
                <span className="dash-template-load">
                  载入配置
                  <ArrowRight size={13} aria-hidden="true" />
                </span>
              </button>
            ))}
          </div>
          {!featuredTemplates.length && (
            <EmptyState
              icon={SlidersHorizontal}
              title={
                templatesQuery.isPending
                  ? "正在加载预设…"
                  : templatesQuery.isError
                    ? "预设暂不可用"
                    : "还没有物理层预设"
              }
              description="在模板库中管理实验参数，复用你的配置。"
              action="打开模板库"
              onAction={() => setActiveTab("templates")}
            />
          )}
        </section>
        <section className="dash-panel" aria-labelledby="dash-files-title">
          <div className="dash-section-heading">
            <h2 id="dash-files-title">
              最近文件<span className="dash-caption">OUTPUTS</span>
            </h2>
            <button
              type="button"
              className="dash-text-button"
              onClick={() => setActiveTab("signals")}
            >
              分析台
              <ArrowUpRight size={14} aria-hidden="true" />
            </button>
          </div>
          {recentArtifacts.length ? (
            <div className="dash-file-list">
              {recentArtifacts.map((item) => (
                <button
                  type="button"
                  className="dash-file"
                  key={item.id}
                  onClick={() => {
                    setSelectedRunId(item.run_id);
                    setSelectedArtifactPath(item.path);
                    setActiveTab("signals");
                  }}
                >
                  <span className="dash-file-icon">
                    <FileCode2 size={18} aria-hidden="true" />
                  </span>
                  <span className="dash-file-copy">
                    <strong title={item.path}>
                      {item.path.split(/[\\/]/).pop()}
                    </strong>
                    <small>
                      {item.kind}
                      {item.file_state && !item.file_state.readable
                        ? ` · ${fileStateLabel(item.file_state)}`
                        : item.metadata?.size_bytes != null
                          ? ` · ${(item.metadata.size_bytes / 1024).toFixed(1)} KB`
                          : ""}
                    </small>
                  </span>
                  <ArrowUpRight size={15} aria-hidden="true" />
                </button>
              ))}
            </div>
          ) : (
            <EmptyState
              icon={FolderOpen}
              title={
                artifactsQuery.isPending
                  ? "正在加载文件…"
                  : artifactsQuery.isError
                    ? "文件暂不可用"
                    : "为你的信号留下一份记录"
              }
              description="采集的 IQ 数据与解调结果会自动归集到这里。"
              action="开始离线调制"
              onAction={() => launch("modulate")}
            />
          )}
        </section>
      </div>
      <footer className="dash-footer">
        <span>
          <span className="dash-cross">+</span> FLRC MODEM <span>/</span> SIGNAL
          WORKSPACE
        </span>
        <span>每一个信号，都有迹可循。</span>
      </footer>
    </div>
  );
};
