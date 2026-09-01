import { useCallback, useEffect, useState, type ReactNode } from "react";
import { api } from "../api/client";
import type { AgentMap, MetricPoint, MetricsRange, NodeItem, OnlineMap } from "../types";
import { CountryFlag } from "./CountryFlag";
import { NodeMetricLineChart } from "./NodeMetricCharts";
import { NodeMetricSpark } from "./NodeMetricSpark";
import { OnlineBadge } from "./OnlineBadge";

const PING = "#22d3bb";
const CPU = "#e6a23c";
const DISK = "#6aa7d8";
const MEM = "#3dd68c";

const PERIODS: { id: MetricsRange; label: string }[] = [
  { id: "day", label: "сутки" },
  { id: "week", label: "неделя" },
  { id: "month", label: "месяц" },
  { id: "all", label: "всё время" },
];

type Props = {
  nodes: NodeItem[];
  statuses: OnlineMap;
  agentStatuses: AgentMap;
};

export function NodeMetricsGrid({ nodes, statuses, agentStatuses }: Props) {
  const [series, setSeries] = useState<Record<string, MetricPoint[]>>({});
  const [fromTs, setFromTs] = useState(() => Date.now() / 1000 - 86400);
  const [toTs, setToTs] = useState(() => Date.now() / 1000);
  const [openId, setOpenId] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const data = await api.nodeMetrics("day");
      setSeries(data.series ?? {});
      setFromTs(data.from_ts);
      setToTs(data.to_ts);
    } catch {
      /* keep last */
    }
  }, []);

  useEffect(() => {
    void load();
    const id = window.setInterval(() => void load(), 30000);
    return () => window.clearInterval(id);
  }, [load]);

  const openNode = nodes.find((n) => n.id === openId) ?? null;

  return (
    <>
      <div className="grid h-[calc(100dvh-11rem)] grid-cols-2 content-start gap-1.5 overflow-auto sm:grid-cols-3 md:grid-cols-4 xl:grid-cols-6 2xl:grid-cols-7 lg:h-[calc(100dvh-9.5rem)]">
        {nodes.map((node) => (
          <NodeTile
            key={node.id}
            node={node}
            points={series[node.id] ?? []}
            fromTs={fromTs}
            toTs={toTs}
            status={statuses[node.id]}
            agent={agentStatuses[node.id]}
            onOpen={() => setOpenId(node.id)}
          />
        ))}
      </div>
      {openNode && (
        <NodeMetricModal
          node={openNode}
          status={statuses[openNode.id]}
          agent={agentStatuses[openNode.id]}
          dayPoints={series[openNode.id] ?? []}
          dayFromTs={fromTs}
          dayToTs={toTs}
          onClose={() => setOpenId(null)}
        />
      )}
    </>
  );
}

function NodeTile({
  node,
  points,
  fromTs,
  toTs,
  status,
  agent,
  onOpen,
}: {
  node: NodeItem;
  points: MetricPoint[];
  fromTs: number;
  toTs: number;
  status: OnlineMap[string] | undefined;
  agent: AgentMap[string] | undefined;
  onOpen: () => void;
}) {
  const pingLive = status?.online ? status.latency_ms : null;
  const cpuLive = agent?.present ? agent.cpu_percent : null;
  const diskLive = agent?.present ? agent.disk_percent : null;
  const times = points.map((p) => p.t);
  return (
    <article className="relative flex min-h-[148px] flex-col rounded-md border border-[var(--border)] bg-[var(--bg-elevated)] px-1.5 py-1">
      <div className="flex min-w-0 items-center gap-1 pr-5">
        <CountryFlag code={node.country_code} size={10} />
        <div className="min-w-0 flex-1 truncate text-[11px] font-semibold leading-tight text-[var(--text)]">
          {node.name}
        </div>
        <OnlineBadge status={status} compact />
      </div>
      <button
        type="button"
        onClick={onOpen}
        className="absolute right-0.5 top-0.5 inline-flex h-5 w-5 items-center justify-center rounded text-[var(--muted)] hover:bg-[var(--bg-row)] hover:text-[var(--text)]"
        title="Подробно"
        aria-label={`Подробно ${node.name}`}
      >
        <ExpandIcon />
      </button>
      <div className="mt-0.5 grid min-h-0 flex-1 grid-rows-3 gap-0.5">
        <SparkRow
          label="пинг"
          value={fmtMs(pingLive)}
          color={PING}
          times={times}
          values={points.map((p) => p.ping_ms)}
          fromTs={fromTs}
          toTs={toTs}
        />
        <SparkRow
          label="CPU"
          value={fmtPct(cpuLive)}
          color={CPU}
          times={times}
          values={points.map((p) => p.cpu_percent)}
          fromTs={fromTs}
          toTs={toTs}
          maxY={100}
        />
        <SparkRow
          label="диск"
          value={fmtPct(diskLive)}
          color={DISK}
          times={times}
          values={points.map((p) => p.disk_percent)}
          fromTs={fromTs}
          toTs={toTs}
          maxY={100}
        />
      </div>
    </article>
  );
}

function SparkRow({
  label,
  value,
  color,
  times,
  values,
  fromTs,
  toTs,
  maxY,
}: {
  label: string;
  value: string;
  color: string;
  times: number[];
  values: Array<number | null>;
  fromTs: number;
  toTs: number;
  maxY?: number;
}) {
  return (
    <div className="flex min-h-0 items-stretch gap-1">
      <div className="w-[52px] shrink-0 leading-tight">
        <div className="text-[9px] uppercase tracking-wide text-[var(--muted)]">{label}</div>
        <div className="truncate text-[11px] font-semibold tabular-nums" style={{ color }}>
          {value}
        </div>
      </div>
      <div className="min-w-0 flex-1">
        <NodeMetricSpark
          times={times}
          values={values}
          color={color}
          maxY={maxY}
          fromTs={fromTs}
          toTs={toTs}
        />
      </div>
    </div>
  );
}

function NodeMetricModal({
  node,
  status,
  agent,
  dayPoints,
  dayFromTs,
  dayToTs,
  onClose,
}: {
  node: NodeItem;
  status: OnlineMap[string] | undefined;
  agent: AgentMap[string] | undefined;
  dayPoints: MetricPoint[];
  dayFromTs: number;
  dayToTs: number;
  onClose: () => void;
}) {
  const [range, setRange] = useState<MetricsRange>("day");
  const [points, setPoints] = useState<MetricPoint[]>(dayPoints);
  const [fromTs, setFromTs] = useState(dayFromTs);
  const [toTs, setToTs] = useState(dayToTs);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  useEffect(() => {
    let cancelled = false;
    if (range === "day") {
      setPoints(dayPoints);
      setFromTs(dayFromTs);
      setToTs(dayToTs);
      return;
    }
    setLoading(true);
    void api
      .nodeMetricsOne(node.id, range)
      .then((data) => {
        if (cancelled) return;
        setPoints(data.series[node.id] ?? []);
        setFromTs(data.from_ts);
        setToTs(data.to_ts);
      })
      .catch(() => {
        if (!cancelled) setPoints([]);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [node.id, range, dayPoints, dayFromTs, dayToTs]);

  const times = points.map((p) => p.t);
  const load = agent?.loadavg;
  const chart = {
    times,
    xMin: fromTs,
    xMax: toTs,
    height: 128,
    formatY: (n: number) => String(Math.round(n)),
  };

  return (
    <div
      className="fixed inset-0 z-[70] flex items-center justify-center bg-black/65 p-4 backdrop-blur-[2px]"
      onClick={onClose}
    >
      <div
        role="dialog"
        aria-modal="true"
        className="flex w-full max-w-[880px] max-h-[min(90dvh,760px)] flex-col overflow-hidden rounded-xl border border-[var(--border)] bg-[var(--bg-elevated)] shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <header className="flex shrink-0 flex-wrap items-center gap-2 border-b border-[var(--border)] px-4 py-2.5">
          <CountryFlag code={node.country_code} size={16} />
          <h2 className="text-base font-semibold text-[var(--text)]">{node.name}</h2>
          <span className="font-mono text-xs text-[var(--muted)]">{node.host}</span>
          <OnlineBadge status={status} />
          <div className="ml-auto flex flex-wrap items-center gap-1">
            {PERIODS.map((p) => (
              <button
                key={p.id}
                type="button"
                onClick={() => setRange(p.id)}
                className={[
                  "rounded px-2 py-1 text-[11px]",
                  range === p.id
                    ? "bg-[var(--accent-dim)] font-semibold text-[var(--accent)]"
                    : "text-[var(--muted)] hover:text-[var(--text)]",
                ].join(" ")}
              >
                {p.label}
              </button>
            ))}
            <button
              type="button"
              onClick={onClose}
              className="ml-1 rounded border border-[var(--border)] px-2 py-1 text-xs text-[var(--muted)] hover:text-[var(--text)]"
            >
              Закрыть
            </button>
          </div>
        </header>

        <div className="overflow-auto px-4 py-3">
          <dl className="mb-3 grid grid-cols-2 gap-x-3 gap-y-1 text-[11px] sm:grid-cols-4 lg:grid-cols-8">
            <Fact label="пинг" value={fmtMs(status?.online ? status.latency_ms : null)} />
            <Fact label="CPU" value={fmtPct(agent?.present ? agent.cpu_percent : null)} />
            <Fact label="RAM" value={fmtPct(agent?.present ? agent.mem_percent : null)} />
            <Fact label="диск" value={fmtPct(agent?.present ? agent.disk_percent : null)} />
            <Fact
              label="load"
              value={
                load && load.length
                  ? load.map((n) => n.toFixed(2)).join(" · ")
                  : "—"
              }
            />
            <Fact label="агент" value={agent?.version ? `v${agent.version}` : "—"} />
            <Fact
              label="RemnaNode"
              value={agent?.remnanode_version ? `v${agent.remnanode_version}` : "—"}
            />
            <Fact
              label="WARP"
              value={
                agent?.warp_present
                  ? agent.warp_healthy
                    ? "ok"
                    : "нет"
                  : "—"
              }
            />
          </dl>

          {loading && <p className="mb-2 text-xs text-[var(--muted)]">Загрузка периода…</p>}

          <div className="grid grid-cols-1 gap-2.5 sm:grid-cols-2">
            <ChartBlock title="Пинг" unit="мс">
              <NodeMetricLineChart
                {...chart}
                values={points.map((p) => p.ping_ms)}
                color={PING}
                unit="мс"
              />
            </ChartBlock>
            <ChartBlock title="CPU" unit="%">
              <NodeMetricLineChart
                {...chart}
                values={points.map((p) => p.cpu_percent)}
                color={CPU}
                unit="%"
                maxY={100}
              />
            </ChartBlock>
            <ChartBlock title="Диск" unit="%">
              <NodeMetricLineChart
                {...chart}
                values={points.map((p) => p.disk_percent)}
                color={DISK}
                unit="%"
                maxY={100}
              />
            </ChartBlock>
            <ChartBlock title="RAM" unit="%">
              <NodeMetricLineChart
                {...chart}
                values={points.map((p) => p.mem_percent)}
                color={MEM}
                unit="%"
                maxY={100}
              />
            </ChartBlock>
          </div>
        </div>
      </div>
    </div>
  );
}

function ChartBlock({ title, unit, children }: { title: string; unit: string; children: ReactNode }) {
  return (
    <section className="rounded-lg border border-[var(--border)] bg-[var(--bg)] px-2.5 py-2">
      <div className="mb-1 flex items-baseline justify-between">
        <h3 className="text-xs font-semibold text-[var(--text)]">{title}</h3>
        <span className="text-[10px] text-[var(--muted)]">{unit}</span>
      </div>
      {children}
    </section>
  );
}

function Fact({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="text-[var(--muted)]">{label}</dt>
      <dd className="truncate font-medium tabular-nums text-[var(--text)]">{value}</dd>
    </div>
  );
}

function fmtMs(v: number | null | undefined): string {
  if (v == null || Number.isNaN(v)) return "—";
  return `${Math.round(v)} мс`;
}

function fmtPct(v: number | null | undefined): string {
  if (v == null || Number.isNaN(v)) return "—";
  return `${Math.round(v)}%`;
}

function ExpandIcon() {
  return (
    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden>
      <path d="M15 3h6v6M9 21H3v-6M21 3l-7 7M3 21l7-7" />
    </svg>
  );
}
