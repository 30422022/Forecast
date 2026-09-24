import {
  Activity,
  ArrowRight,
  BarChart3,
  CheckCircle2,
  Cpu,
  Database,
  ExternalLink,
  FileText,
  GitBranch,
  Info,
  LockKeyhole,
  Play,
  RefreshCw,
  RotateCcw,
  ServerCog,
  ShieldCheck,
  Waves,
  Workflow,
  XCircle
} from "lucide-react";
import { FormEvent, useEffect, useMemo, useState } from "react";

import { ApiError, api } from "./api";
import type {
  BacktestResponse,
  CapabilityResponse,
  DurableStatus,
  TraceStep,
  WorkflowReceipt,
  WorkflowResult,
  WorkflowSuccess
} from "./types";

type Page = "workbench" | "backtest" | "recovery" | "architecture";

const PAGE_LABELS: Record<Page, string> = {
  workbench: "预测工作台",
  backtest: "滚动回测",
  recovery: "执行与恢复",
  architecture: "架构说明"
};

const STAGE_LABELS: Record<string, string> = {
  quality: "数据质量",
  forecast: "预测执行",
  review: "结果评审",
  decision: "决策适配",
  audit: "执行审计"
};

const STATUS_LABELS: Record<string, string> = {
  queued: "待执行",
  running: "运行中",
  recoverable: "可恢复",
  succeeded: "已完成",
  skipped: "已跳过",
  failed: "已失败",
  demo_only: "演示完成"
};

function initialPage(): Page {
  const value = window.location.hash.slice(1);
  return value === "backtest" || value === "recovery" || value === "architecture"
    ? value
    : "workbench";
}

function parseHistory(text: string): number[] {
  const tokens = text.trim().split(/[\s,，;；]+/).filter(Boolean);
  const values = tokens.map(Number);
  if (values.some((value) => !Number.isFinite(value))) {
    throw new Error("历史序列只能包含有限数字，请用逗号、空格或换行分隔。");
  }
  if (values.length < 2 || values.length > 10000) {
    throw new Error("历史序列需包含 2 至 10000 个观测点。");
  }
  return values;
}

function explainError(error: unknown): string {
  if (error instanceof ApiError) {
    return error.message + (error.requestId ? "；任务 ID：" + error.requestId : "");
  }
  return error instanceof Error ? error.message : "操作失败，请稍后重试。";
}

function isSuccess(result: WorkflowResult | null): result is WorkflowSuccess {
  return result?.status === "demo_only";
}

function formatTime(value?: string | null): string {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString("zh-CN", { hour12: false });
}

function numberLabel(value: number): string {
  return new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 2 }).format(value);
}

function metricLabel(value: number | null, percent = false): string {
  return value === null ? "—" : numberLabel(percent ? value * 100 : value) + (percent ? "%" : "");
}

function StatusPill({ status }: { status: string }) {
  const tone =
    status === "failed"
      ? "danger"
      : status === "succeeded" || status === "demo_only"
        ? "success"
        : "neutral";
  return <span className={"status-pill status-pill--" + tone}>{STATUS_LABELS[status] || status}</span>;
}

function ForecastChart({ history, forecast }: { history: number[] | null; forecast: number[] }) {
  const historic = history?.slice(-48) ?? [];
  const predicted = forecast.slice(0, 96);
  const values = [...historic, ...predicted];
  const min = values.reduce((smallest, value) => Math.min(smallest, value), Infinity);
  const max = values.reduce((largest, value) => Math.max(largest, value), -Infinity);
  const padding = Math.max((max - min) * 0.12, Math.abs(max) * 0.02, 1);
  const low = min - padding;
  const high = max + padding;
  const left = 55;
  const top = 22;
  const width = 700;
  const height = 210;
  const segments = Math.max(values.length - 1, 1);
  const x = (index: number) => left + (index / segments) * width;
  const y = (value: number) => top + ((high - value) / (high - low)) * height;
  const line = (points: number[], offset: number) =>
    points.map((value, index) => (index === 0 ? "M" : "L") + x(index + offset) + "," + y(value)).join(" ");
  const historyLine = historic.length ? line(historic, 0) : "";
  const futureLine = predicted.length
    ? line(historic.length ? [historic[historic.length - 1], ...predicted] : predicted, Math.max(historic.length - 1, 0))
    : "";
  const cutoff = historic.length ? x(historic.length - 1) : left;

  return (
    <div className="chart-wrap">
      <div className="chart-legend">
        <span><i className="legend-line legend-line--history" />本次输入</span>
        <span><i className="legend-line legend-line--forecast" />Mock 预测</span>
      </div>
      <svg viewBox="0 0 800 260" role="img" aria-label="历史负荷与公开 Mock 预测曲线">
        {[0, 0.5, 1].map((ratio) => (
          <g key={ratio}>
            <line x1={left} x2={left + width} y1={top + ratio * height} y2={top + ratio * height} className="chart-grid" />
            <text x="5" y={top + ratio * height + 4} className="chart-label">{numberLabel(high - ratio * (high - low))}</text>
          </g>
        ))}
        {historic.length > 0 && <line x1={cutoff} x2={cutoff} y1={top} y2={top + height} className="chart-cutoff" />}
        {historyLine && <path d={historyLine} className="chart-line chart-line--history" />}
        {futureLine && <path d={futureLine} className="chart-line chart-line--forecast" />}
        <text x={left} y="253" className="chart-label">历史窗口</text>
        <text x={left + width - 56} y="253" className="chart-label">预测步长</text>
      </svg>
      {forecast.length > predicted.length && (
        <p className="chart-footnote">图中展示前 96 个预测点；完整结果可在下方查看。</p>
      )}
      {!history && <p className="chart-footnote">此任务的历史输入未在当前浏览器会话中保存，仅展示返回的预测序列。</p>}
    </div>
  );
}

function TraceView({ trace }: { trace: TraceStep[] }) {
  return (
    <div className="trace-list">
      {trace.map((step, index) => (
        <div className="trace-item" key={step.node + "-" + index}>
          <div className={"trace-marker trace-marker--" + step.status}>
            {step.status === "failed" ? <XCircle size={16} /> : <CheckCircle2 size={16} />}
          </div>
          <div>
            <strong>{STAGE_LABELS[step.node] || step.node}</strong>
            <span>{step.detail || "阶段执行"}</span>
          </div>
          <StatusPill status={step.status} />
        </div>
      ))}
    </div>
  );
}

export default function App() {
  const [page, setPage] = useState<Page>(initialPage);
  const [capabilities, setCapabilities] = useState<CapabilityResponse | null>(null);
  const [connectionError, setConnectionError] = useState("");
  const [siteId, setSiteId] = useState("demo-site");
  const [historyText, setHistoryText] = useState("");
  const [backtestHistoryText, setBacktestHistoryText] = useState("");
  const [backtestHorizon, setBacktestHorizon] = useState(8);
  const [backtestFolds, setBacktestFolds] = useState(5);
  const [backtestResult, setBacktestResult] = useState<BacktestResponse | null>(null);
  const [backtestBusy, setBacktestBusy] = useState(false);
  const [horizon, setHorizon] = useState(8);
  const [durable, setDurable] = useState(false);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<WorkflowResult | null>(null);
  const [resultHistory, setResultHistory] = useState<number[] | null>(null);
  const [requestId, setRequestId] = useState("");
  const [lookupId, setLookupId] = useState("");
  const [durableStatus, setDurableStatus] = useState<DurableStatus | null>(null);
  const [receipt, setReceipt] = useState<WorkflowReceipt | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  useEffect(() => {
    api.capabilities()
      .then((value) => {
        setCapabilities(value);
        setConnectionError("");
      })
      .catch((reason: unknown) => setConnectionError(explainError(reason)));
  }, []);

  const parsedCount = useMemo(
    () => historyText.trim().split(/[\s,，;；]+/).filter(Boolean).length,
    [historyText]
  );

  function navigate(next: Page) {
    setPage(next);
    window.history.replaceState(null, "", "#" + next);
    setError("");
  }

  async function loadStatus(id: string) {
    const [statusResponse, receiptResponse] = await Promise.allSettled([
      api.status(id),
      api.receipt(id)
    ]);
    setDurableStatus(statusResponse.status === "fulfilled" ? statusResponse.value : null);
    setReceipt(receiptResponse.status === "fulfilled" ? receiptResponse.value : null);
    if (statusResponse.status === "rejected" && receiptResponse.status === "rejected") {
      throw statusResponse.reason;
    }
    if (statusResponse.status === "rejected" && receiptResponse.status === "fulfilled") {
      setNotice("已找到进程内收据；该任务没有可查询的持久检查点。");
    }
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    setNotice("");
    let history: number[];
    try {
      history = parseHistory(historyText);
      if (!siteId.trim() || siteId.trim().length > 128) {
        throw new Error("站点标识需为 1 至 128 个字符。");
      }
      if (!Number.isInteger(horizon) || horizon < 1 || horizon > 672) {
        throw new Error("预测步长需为 1 至 672 的整数。");
      }
      if (durable && !capabilities?.recovery_available) {
        throw new Error("当前后端未配置外部检查点密钥，不能启用持久模式。");
      }
    } catch (reason) {
      setError(explainError(reason));
      return;
    }

    setBusy(true);
    setResult(null);
    setDurableStatus(null);
    setReceipt(null);
    setRequestId("");
    try {
      const response = await api.run({ site_id: siteId.trim(), history, horizon }, durable);
      setResult(response);
      setResultHistory(history);
      setRequestId(response.request_id);
      setLookupId(response.request_id);
      if (durable) await loadStatus(response.request_id);
      if (response.status === "failed") setError("工作流失败：" + response.error_code);
      if (response.status === "recoverable") {
        setNotice("任务已保存，但本次执行中断；可在“执行与恢复”中继续。");
      }
    } catch (reason) {
      setError(explainError(reason));
    } finally {
      setBusy(false);
    }
  }

  async function lookup() {
    const id = lookupId.trim();
    if (!id) {
      setError("请填写任务 ID。");
      return;
    }
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await loadStatus(id);
      setRequestId(id);
      setNotice("任务状态已更新。");
    } catch (reason) {
      setError(explainError(reason));
    } finally {
      setBusy(false);
    }
  }

  async function resume() {
    const id = lookupId.trim();
    if (!id) {
      setError("请先填写任务 ID。");
      return;
    }
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const response = await api.resume(id);
      setResult(response);
      setResultHistory(id === requestId ? resultHistory : null);
      setRequestId(response.request_id);
      await loadStatus(id);
      setNotice(response.status === "demo_only" ? "已读取或完成该任务。" : "恢复请求已提交。");
      if (response.status === "failed") setError("工作流失败：" + response.error_code);
    } catch (reason) {
      setError(explainError(reason));
    } finally {
      setBusy(false);
    }
  }

  async function runBacktest(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    setBacktestResult(null);
    let history: number[];
    try {
      history = parseHistory(backtestHistoryText);
      if (history.some((value) => Math.abs(value) > 1e12)) {
        throw new Error("回测输入的绝对值不能超过 1e12。");
      }
      if (!Number.isInteger(backtestHorizon) || backtestHorizon < 1 || backtestHorizon > 672) {
        throw new Error("预测步长需为 1 至 672 的整数。");
      }
      if (!Number.isInteger(backtestFolds) || backtestFolds < 1 || backtestFolds > 12) {
        throw new Error("回测折数需为 1 至 12 的整数。");
      }
    } catch (reason) {
      setError(explainError(reason));
      return;
    }
    setBacktestBusy(true);
    try {
      setBacktestResult(await api.backtest({
        history,
        horizon: backtestHorizon,
        max_folds: backtestFolds
      }));
    } catch (reason) {
      setError(explainError(reason));
    } finally {
      setBacktestBusy(false);
    }
  }

  function makeSyntheticSample() {
    const values = Array.from({ length: 144 }, (_, index) => {
      const daily = 18 * Math.sin((2 * Math.PI * index) / 24);
      const shorter = 5 * Math.sin((2 * Math.PI * index) / 8);
      return Math.round((110 + daily + shorter + index * 0.04) * 100) / 100;
    });
    setBacktestHistoryText(values.join(", "));
    setBacktestResult(null);
  }

  const online = Boolean(capabilities);
  const activeTrace = result && "trace" in result ? result.trace : [];

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-mark"><Waves size={23} /></div>
          <div><strong>GridCast</strong><span>PUBLIC ENGINEERING DEMO</span></div>
        </div>
        <p className="nav-caption">工作空间</p>
        <nav className="side-nav" aria-label="主导航">
          <button className={page === "workbench" ? "active" : ""} onClick={() => navigate("workbench")}>
            <Activity size={18} /><span>预测工作台</span><ArrowRight size={14} />
          </button>
          <button className={page === "backtest" ? "active" : ""} onClick={() => navigate("backtest")}>
            <BarChart3 size={18} /><span>滚动回测</span><ArrowRight size={14} />
          </button>
          <button className={page === "recovery" ? "active" : ""} onClick={() => navigate("recovery")}>
            <RotateCcw size={18} /><span>执行与恢复</span><ArrowRight size={14} />
          </button>
          <button className={page === "architecture" ? "active" : ""} onClick={() => navigate("architecture")}>
            <GitBranch size={18} /><span>架构说明</span><ArrowRight size={14} />
          </button>
        </nav>
        <div className="sidebar-bottom">
          <div className="sidebar-status">
            <span className={"online-dot" + (online ? " online" : "")} />
            <div><strong>{online ? "API 已连接" : "API 未连接"}</strong><small>{capabilities?.storage || "检查后端服务"}</small></div>
          </div>
          <p>只展示公开工作流。真实模型、业务数据与优化策略未包含在本仓库。</p>
        </div>
      </aside>

      <div className="main-shell">
        <header className="topbar">
          <div className="breadcrumb"><span>GRIDCAST</span><ArrowRight size={13} /><strong>{PAGE_LABELS[page]}</strong></div>
          <div className="topbar-actions">
            <span className="topbar-badge"><span className={"online-dot" + (online ? " online" : "")} />{online ? "服务在线" : "服务离线"}</span>
            <a href="/docs" target="_blank" rel="noreferrer">API 文档 <ExternalLink size={14} /></a>
          </div>
        </header>

        <main className="page-content">
          {connectionError && (
            <div className="alert alert--warning"><Info size={17} />后端暂不可用：{connectionError}。先启动 FastAPI 服务，再刷新页面。</div>
          )}
          {error && <div className="alert alert--error"><XCircle size={17} />{error}</div>}
          {notice && <div className="alert alert--success"><CheckCircle2 size={17} />{notice}</div>}

          {page === "workbench" && (
            <>
              <section className="hero">
                <div>
                  <span className="eyebrow eyebrow--light">FORECAST WORKBENCH / DEMO ONLY</span>
                  <h1>让负荷预测的执行过程<br /><em>可观察、可恢复、可审计</em></h1>
                  <p>输入自己的测试序列，体验公开版多 Agent 工作流。预测值来自简单 Mock，不用于业务决策。</p>
                </div>
                <div className="hero-visual" aria-hidden="true">
                  <div className="hero-orbit hero-orbit--outer" />
                  <div className="hero-orbit hero-orbit--inner" />
                  <div className="hero-core"><Workflow size={34} /></div>
                  <span className="hero-node hero-node--one">QUALITY</span>
                  <span className="hero-node hero-node--two">REVIEW</span>
                  <span className="hero-node hero-node--three">AUDIT</span>
                </div>
              </section>

              <div className="metric-grid">
                <article className="metric-card"><div className="metric-icon"><Workflow size={21} /></div><div><span>公开 Agent 链路</span><strong>5 个阶段</strong><small>质量 → 预测 → 评审 → 决策/跳过 → 审计</small></div></article>
                <article className="metric-card"><div className="metric-icon"><LockKeyhole size={21} /></div><div><span>当前状态存储</span><strong>{capabilities?.storage === "postgresql_aes_gcm" ? "加密持久" : "进程内存"}</strong><small>{capabilities?.recovery_available ? "跨进程恢复可用" : "配置 PostgreSQL 和外部密钥后可启用恢复"}</small></div></article>
                <article className="metric-card"><div className="metric-icon"><ShieldCheck size={21} /></div><div><span>公开能力边界</span><strong>Mock Provider</strong><small>无真实模型、交易或设备控制</small></div></article>
              </div>

              <div className="workspace-grid">
                <section className="panel task-panel">
                  <div className="panel-title"><div><span className="eyebrow">01 / INPUT</span><h2>创建预测任务</h2></div><span className="panel-tag">结构化输入</span></div>
                  <form onSubmit={submit}>
                    <div className="form-row">
                      <label><span>站点标识</span><input value={siteId} maxLength={128} onChange={(event) => setSiteId(event.target.value)} placeholder="例如 demo-site" /></label>
                      <label><span>预测步长</span><input type="number" min={1} max={672} value={horizon} onChange={(event) => setHorizon(Number(event.target.value))} /></label>
                    </div>
                    <label className="wide-label">
                      <span>历史负荷序列 <small>{parsedCount} 个观测点</small></span>
                      <textarea value={historyText} onChange={(event) => setHistoryText(event.target.value)} rows={6} placeholder="输入至少两个数值，用逗号、空格或换行分隔。不会在浏览器中长期保存。" spellCheck={false} />
                    </label>
                    <label className={"mode-card" + (durable ? " mode-card--selected" : "")}>
                      <input type="checkbox" checked={durable} onChange={(event) => setDurable(event.target.checked)} disabled={!capabilities?.recovery_available} />
                      <span className="mode-card-icon"><Database size={19} /></span>
                      <span><strong>加密持久执行</strong><small>保存同一任务的检查点和 Agent 消息；需后端配置 PostgreSQL 与外部密钥。</small></span>
                      <span className="mode-switch" aria-hidden="true" />
                    </label>
                    <div className="form-footer"><span>示例返回值仅验证工作流，不代表预测精度。</span><button type="submit" className="primary-button" disabled={busy || !online}><Play size={17} />{busy ? "运行中…" : "运行工作流"}</button></div>
                  </form>
                </section>

                <aside className="panel guide-panel">
                  <div className="panel-title"><div><span className="eyebrow">02 / HOW IT WORKS</span><h2>一次任务的流转</h2></div></div>
                  <div className="flow-list">
                    {[
                      ["01", "质量检查", "校验输入长度与有限数值"],
                      ["02", "预测执行", "调用可替换 Provider；公开版为 Mock"],
                      ["03", "结果评审", "检查输出契约，最多允许一次修订"],
                      ["04", "决策适配", "未配置私有实现时明确跳过"],
                      ["05", "执行审计", "保存状态与有限执行元数据"]
                    ].map(([number, title, detail]) => (
                      <div className="flow-step" key={number}><b>{number}</b><div><strong>{title}</strong><p>{detail}</p></div></div>
                    ))}
                  </div>
                  <div className="guide-note"><Info size={16} /><p>这些 Agent 是确定性契约处理器，用来展示协作边界；不是自治 LLM Agent。</p></div>
                </aside>
              </div>

              {result && (
                <section className="result-stack">
                  <div className="section-heading"><div><span className="eyebrow">03 / OUTPUT</span><h2>执行结果</h2></div><StatusPill status={result.status} /></div>
                  <div className="result-summary">
                    <article><span>任务 ID</span><strong className="mono">{result.request_id}</strong></article>
                    <article><span>执行阶段</span><strong>{activeTrace.length} 条轨迹</strong></article>
                    <article><span>预测值</span><strong>{isSuccess(result) ? result.forecast.length + " 步" : "未生成"}</strong></article>
                  </div>
                  <div className="result-grid">
                    <div className="panel result-panel">
                      <div className="panel-title"><div><span className="eyebrow">FORECAST TRACE</span><h2>预测曲线</h2></div><span className="panel-tag">MOCK</span></div>
                      {isSuccess(result) ? (
                        <>
                          <ForecastChart history={resultHistory} forecast={result.forecast} />
                          <div className="values-strip"><span>返回数值</span><p>{result.forecast.slice(0, 16).map(numberLabel).join("  ·  ")}{result.forecast.length > 16 ? "  …" : ""}</p></div>
                        </>
                      ) : <div className="empty-panel">任务尚未生成可展示的预测曲线。</div>}
                    </div>
                    <div className="panel result-panel">
                      <div className="panel-title"><div><span className="eyebrow">AGENT EXECUTION</span><h2>阶段轨迹</h2></div></div>
                      {activeTrace.length ? <TraceView trace={activeTrace} /> : <div className="empty-panel">任务执行中，稍后查询状态。</div>}
                    </div>
                  </div>
                  {isSuccess(result) && (
                    <div className="result-disclaimer"><ShieldCheck size={17} /><span>决策适配器：{result.decision.status}。{result.decision.message || "公开版不生成可执行的市场或设备建议。"} 审计指纹只覆盖执行元数据，不是数字签名。</span></div>
                  )}
                  <button className="text-button" onClick={() => navigate("recovery")}>查看收据与恢复状态 <ArrowRight size={16} /></button>
                </section>
              )}
            </>
          )}

          {page === "backtest" && (
            <>
              <div className="page-intro">
                <span className="eyebrow">ROLLING ORIGIN BACKTEST / DEMO ONLY</span>
                <h1>滚动回测实验室</h1>
                <p>用历史序列的较早部分预测后续片段，逐次向前移动截点。每次 Provider 只看到截点之前的数据；结果仅返回误差汇总，不保存输入序列。</p>
              </div>
              <div className="workspace-grid">
                <section className="panel task-panel">
                  <div className="panel-title"><div><span className="eyebrow">01 / EVALUATE</span><h2>配置回测</h2></div><span className="panel-tag">公开 Mock</span></div>
                  <form onSubmit={runBacktest}>
                    <div className="form-row">
                      <label><span>每折预测步长</span><input type="number" min={1} max={672} value={backtestHorizon} onChange={(event) => setBacktestHorizon(Number(event.target.value))} /></label>
                      <label><span>最近回测折数</span><input type="number" min={1} max={12} value={backtestFolds} onChange={(event) => setBacktestFolds(Number(event.target.value))} /></label>
                    </div>
                    <label className="wide-label">
                      <span>观测序列</span>
                      <textarea value={backtestHistoryText} onChange={(event) => setBacktestHistoryText(event.target.value)} rows={7} placeholder="输入连续观测值，或生成一个合成示例。" spellCheck={false} />
                    </label>
                    <p className="muted-note">默认使用至少 max(8, 2 × 预测步长) 个训练点；截点间隔等于预测步长。序列仅用于本次请求，不写入数据库。</p>
                    <div className="form-footer backtest-actions">
                      <button type="button" className="secondary-button" onClick={makeSyntheticSample}>生成合成序列</button>
                      <button type="submit" className="primary-button" disabled={backtestBusy || !online}><Play size={17} />{backtestBusy ? "计算中…" : "运行回测"}</button>
                    </div>
                  </form>
                </section>
                <aside className="panel guide-panel">
                  <div className="panel-title"><div><span className="eyebrow">02 / VALIDATION</span><h2>防泄漏与指标</h2></div></div>
                  <div className="flow-list">
                    {[
                      ["01", "选取最近截点", "从历史尾部向前取最多 12 折"],
                      ["02", "截断训练窗口", "Provider 仅接收截点之前的数据"],
                      ["03", "验证输出契约", "长度和有限数值不合规时记录固定错误码"],
                      ["04", "计算误差", "逐折 MAE、RMSE、WAPE、sMAPE"],
                      ["05", "汇总结果", "只返回指标与折数，不返回原始值"]
                    ].map(([number, title, detail]) => (
                      <div className="flow-step" key={number}><b>{number}</b><div><strong>{title}</strong><p>{detail}</p></div></div>
                    ))}
                  </div>
                  <div className="guide-note"><Info size={16} /><p>这里仅评估重复最后观测值的 Mock。低误差不代表 MPWNet 或其他模型效果，也不能用于电力市场决策。</p></div>
                </aside>
              </div>
              {backtestResult && (
                <section className="result-stack">
                  <div className="section-heading"><div><span className="eyebrow">03 / METRICS</span><h2>回测结果</h2></div><span className="panel-tag">{backtestResult.completed_folds} / {backtestResult.fold_count} 折完成</span></div>
                  <div className="backtest-metrics">
                    <article><span>平均 MAE</span><strong>{metricLabel(backtestResult.mean_mae)}</strong></article>
                    <article><span>平均 RMSE</span><strong>{metricLabel(backtestResult.mean_rmse)}</strong></article>
                    <article><span>平均 WAPE</span><strong>{metricLabel(backtestResult.mean_wape, true)}</strong></article>
                    <article><span>平均 sMAPE</span><strong>{metricLabel(backtestResult.mean_smape, true)}</strong></article>
                  </div>
                  <section className="panel result-panel">
                    <div className="panel-title"><div><span className="eyebrow">FOLD DETAILS</span><h2>逐折结果</h2></div><span className="panel-tag">{backtestResult.provider}</span></div>
                    <div className="backtest-table-wrap">
                      <table className="backtest-table"><thead><tr><th>截点</th><th>训练点数</th><th>测试点数</th><th>MAE</th><th>RMSE</th><th>WAPE</th><th>sMAPE</th></tr></thead>
                        <tbody>{backtestResult.folds.map((fold) => (
                          <tr key={fold.cutoff_index}><td>{fold.cutoff_index}</td><td>{fold.train_rows}</td><td>{fold.test_rows}</td><td>{metricLabel(fold.mae)}</td><td>{metricLabel(fold.rmse)}</td><td>{metricLabel(fold.wape, true)}</td><td>{metricLabel(fold.smape, true)}</td></tr>
                        ))}</tbody>
                      </table>
                    </div>
                    {backtestResult.failures.length > 0 && <p className="muted-note">{backtestResult.failures.length} 折输出未通过契约检查；详细原因仅以固定错误码返回。</p>}
                    <p className="muted-note">WAPE 在实际值绝对值之和近零时记为“—”。均值按有效折数计算，未作业务效果承诺。</p>
                  </section>
                </section>
              )}
            </>
          )}

          {page === "recovery" && (
            <>
              <div className="page-intro"><span className="eyebrow">DURABLE EXECUTION</span><h1>执行与恢复</h1><p>按任务 ID 查看安全状态、Agent 消息和执行收据。持久模式需要 PostgreSQL 连接和外部检查点密钥。</p></div>
              <section className="panel lookup-panel">
                <div className="panel-title"><div><span className="eyebrow">REQUEST LOOKUP</span><h2>查询任务</h2></div><span className="panel-tag">{capabilities?.recovery_available ? "恢复可用" : "仅内存收据"}</span></div>
                <div className="lookup-row"><input aria-label="任务 ID" value={lookupId} onChange={(event) => setLookupId(event.target.value)} placeholder="粘贴 request_id" /><button className="secondary-button" onClick={lookup} disabled={busy}><RefreshCw size={16} />查询</button><button className="primary-button" onClick={resume} disabled={busy || !capabilities?.recovery_available}><RotateCcw size={16} />继续执行</button></div>
                <p className="muted-note">已提交阶段不会重复执行；若进程在 Provider 返回后、提交检查点前退出，该阶段可能重跑。这里没有后台自动恢复。</p>
              </section>

              <div className="recovery-grid">
                <section className="panel detail-panel">
                  <div className="panel-title"><div><span className="eyebrow">CHECKPOINT</span><h2>持久任务状态</h2></div>{durableStatus && <StatusPill status={durableStatus.status} />}</div>
                  {durableStatus ? (
                    <div className="detail-list">
                      <div><span>任务 ID</span><strong className="mono">{durableStatus.request_id}</strong></div>
                      <div><span>下一阶段</span><strong>{durableStatus.next_stage ? STAGE_LABELS[durableStatus.next_stage] || durableStatus.next_stage : "终态"}</strong></div>
                      <div><span>修订次数</span><strong>{durableStatus.attempt}</strong></div>
                      <div><span>更新时间</span><strong>{formatTime(durableStatus.updated_at)}</strong></div>
                      <div><span>固定错误码</span><strong>{durableStatus.error_code || "—"}</strong></div>
                    </div>
                  ) : <div className="empty-panel"><Database size={25} /><p>输入任务 ID 后查询。内存任务只提供短期收据。</p></div>}
                </section>
                <section className="panel detail-panel">
                  <div className="panel-title"><div><span className="eyebrow">EXECUTION RECEIPT</span><h2>执行收据</h2></div>{receipt && <StatusPill status={receipt.status} />}</div>
                  {receipt ? (
                    <div className="detail-list">
                      <div><span>最后节点</span><strong>{STAGE_LABELS[receipt.last_node] || receipt.last_node}</strong></div>
                      <div><span>创建时间</span><strong>{formatTime(receipt.created_at)}</strong></div>
                      <div><span>过期时间</span><strong>{receipt.expires_at ? formatTime(receipt.expires_at) : "无自动 TTL"}</strong></div>
                      <div><span>错误码</span><strong>{receipt.error_code || "—"}</strong></div>
                    </div>
                  ) : <div className="empty-panel"><FileText size={25} /><p>查询后展示收据；进程内收据会随重启失效。</p></div>}
                </section>
              </div>
              <section className="panel messages-panel">
                <div className="panel-title"><div><span className="eyebrow">AGENT MESSAGES</span><h2>协作消息</h2></div><span className="panel-tag">{durableStatus?.messages.length || 0} 条</span></div>
                {durableStatus?.messages.length ? (
                  <div className="message-list">
                    {durableStatus.messages.map((message) => (
                      <div className="message-row" key={message.message_id}>
                        <span className="message-kind">{message.kind}</span>
                        <strong>{STAGE_LABELS[message.sender] || message.sender} <ArrowRight size={13} /> {STAGE_LABELS[message.recipient] || message.recipient}</strong>
                        <span>{message.verdict || message.reason_code || "—"}</span>
                        <time>{formatTime(message.created_at)}</time>
                      </div>
                    ))}
                  </div>
                ) : <div className="empty-panel"><p>消息只包含阶段、状态和固定原因码，不展示原始输入或预测结果。</p></div>}
              </section>
            </>
          )}

          {page === "architecture" && (
            <>
              <div className="page-intro"><span className="eyebrow">PROJECT POSITIONING</span><h1>从预测到决策的工程边界</h1><p>GridCast 面向园区及售电侧的负荷预测与现货辅助决策场景。公开仓库仅展示可验证的编排与恢复骨架，完整业务能力不在此版本发布。</p></div>
              <div className="architecture-cards">
                <article className="panel architecture-card"><div className="architecture-icon"><Activity size={22} /></div><span>问题背景</span><h2>工况波动与预测偏差</h2><p>天气、节假日和新能源出力会改变负荷模式。日前预测偏差可能放大实时结算风险，需要持续验证与反馈。</p></article>
                <article className="panel architecture-card"><div className="architecture-icon"><Cpu size={22} /></div><span>研究模型</span><h2>MPWNet</h2><p>研究多周期模式与非平稳残差：显式周期表示、小波低高频分支和归一化共同建模。模型代码、权重与训练数据不公开。</p></article>
                <article className="panel architecture-card"><div className="architecture-icon"><ServerCog size={22} /></div><span>公开切片</span><h2>编排与可恢复执行</h2><p>FastAPI、结构化 Agent 消息、AES-GCM 检查点、PostgreSQL 租约与 fencing 展示工程机制。API 只使用 Mock；另有离线、未训练的 PatchTST/FTMixer 参考代码。</p></article>
              </div>
              <section className="panel architecture-flow">
                <div className="panel-title"><div><span className="eyebrow">SYSTEM BLUEPRINT</span><h2>完整平台的目标链路</h2></div><span className="panel-tag">架构说明 · 非公开实现</span></div>
                <div className="blueprint">
                  {["数据治理", "任务画像与选模", "负荷预测与校准", "规则检索", "辅助决策", "反馈记忆"].map((item, index) => (
                    <div className="blueprint-item" key={item}><b>{String(index + 1).padStart(2, "0")}</b><strong>{item}</strong>{index < 5 && <ArrowRight size={18} />}</div>
                  ))}
                </div>
                <p className="muted-note">上图描述完整系统的业务目标；本仓库只实现公开演示所需的工作流、Mock Provider、持久状态和前端查看能力。</p>
              </section>
              <section className="panel boundary-panel">
                <div className="panel-title"><div><span className="eyebrow">OPEN SOURCE BOUNDARY</span><h2>哪些内容在仓库中</h2></div></div>
                <div className="boundary-grid">
                  <div><CheckCircle2 size={19} /><strong>可运行示例</strong><p>API 契约、两种执行模式、Agent 阶段轨迹、加密检查点、显式恢复、公开前端，以及离线基线参考模型。</p></div>
                  <div><LockKeyhole size={19} /><strong>保留在私有环境</strong><p>MPWNet 源码和权重、训练与业务数据、知识库、模型路由策略及储能优化实现。</p></div>
                </div>
              </section>
            </>
          )}
        </main>
        <footer className="site-footer"><span>GridCast-Agent · Public Engineering Preview</span><span>演示代码不用于交易、负荷申报或设备控制</span></footer>
      </div>
    </div>
  );
}
