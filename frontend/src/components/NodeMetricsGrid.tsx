import { useCallback, useEffect, useState, type ReactNode } from "react";
import { api } from "../api/client";
import type { AgentMap, MetricPoint, MetricsRange, NodeItem, OnlineMap, SharingUserHit } from "../types";
import { CountryFlag } from "./CountryFlag";
import { NodeMetricLineChart } from "./NodeMetricCharts";
import { NodeMetricSpark } from "./NodeMetricSpark";
import { inboundPeerCount, inboundPeerFromXray } from "../lib/inboundPeerCount";
import { pingToneClass } from "../lib/pingTone";
import { OnlineBadge } from "./OnlineBadge";
import { SharedBadge } from "./SharedBadge";
import { useSharingStatus } from "../hooks/useSharingStatus";

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
  const sharing = useSharingStatus();

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
      <div className="grid h-full min-h-0 auto-rows-min content-start grid-cols-1 gap-1.5 overflow-auto min-[420px]:grid-cols-2 sm:grid-cols-3 md:grid-cols-4 xl:grid-cols-6 2xl:grid-cols-7">
        {nodes.map((node) => (
          <NodeTile
            key={node.id}
            node={node}
            points={series[node.id] ?? []}
            fromTs={fromTs}
            toTs={toTs}
            status={statuses[node.id]}
            agent={agentStatuses[node.id]}
            inboundIps={inboundPeerCount(agentStatuses[node.id], sharing, node.id)}
            inboundFromXray={inboundPeerFromXray(agentStatuses[node.id], sharing, node.id)}
            sharingHits={sharing?.by_agent_id[node.id] ?? []}
            onOpen={() => setOpenId(node.id)}
          />
        ))}
      </div>
      {openNode && (
        <NodeMetricModal
          node={openNode}
          status={statuses[openNode.id]}
          agent={agentStatuses[openNode.id]}
          inboundIps={inboundPeerCount(agentStatuses[openNode.id], sharing, openNode.id)}
          inboundFromXray={inboundPeerFromXray(agentStatuses[openNode.id], sharing, openNode.id)}
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
  inboundIps,
  inboundFromXray,
  sharingHits,
  onOpen,
}: {
  node: NodeItem;
  points: MetricPoint[];
  fromTs: number;
  toTs: number;
  status: OnlineMap[string] | undefined;
  agent: AgentMap[string] | undefined;
  inboundIps: number | null;
  inboundFromXray: boolean;
  sharingHits: SharingUserHit[];
  onOpen: () => void;
}) {
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
        <SharedBadge nodeId={node.id} hits={sharingHits} compact />
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
          label="вх. IP"
          value={fmtCount(inboundIps)}
          valueClass={peersToneClass(inboundIps)}
          color={PING}
          times={times}
          values={points.map((p) => p.ping_ms)}
          fromTs={fromTs}
          toTs={toTs}
          title={
            inboundFromXray
              ? "Цифра — уникальные клиентские IP из Xray за 15 мин (ss не видит localhost/CDN). График — cf_204 за сутки."
              : "Цифра — уникальные входящие IP (ss). График — cf_204 за сутки."
          }
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
  valueClass,
  times,
  values,
  fromTs,
  toTs,
  maxY,
  title,
}: {
  label: string;
  value: string;
  color: string;
  valueClass?: string;
  times: number[];
  values: Array<number | null>;
  fromTs: number;
  toTs: number;
  maxY?: number;
  title?: string;
}) {
  return (
    <div className="flex min-h-0 items-stretch gap-1" title={title}>
      <div className="w-[52px] shrink-0 leading-tight">
        <div className="text-[9px] uppercase tracking-wide text-[var(--muted)]">{label}</div>
        <div
          className={`truncate text-[11px] ${valueClass ?? "font-semibold tabular-nums"}`}
          style={valueClass ? undefined : { color }}
        >
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
  inboundIps,
  inboundFromXray,
  dayPoints,
  dayFromTs,
  dayToTs,
  onClose,
}: {
  node: NodeItem;
  status: OnlineMap[string] | undefined;
  agent: AgentMap[string] | undefined;
  inboundIps: number | null;
  inboundFromXray: boolean;
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
      className="fixed inset-0 z-[70] flex items-end justify-center bg-black/65 p-0 backdrop-blur-[2px] sm:items-center sm:p-4"
      onClick={onClose}
    >
      <div
        role="dialog"
        aria-modal="true"
        className="flex h-[min(96dvh,100%)] w-full max-w-[880px] flex-col overflow-hidden rounded-t-xl border border-[var(--border)] bg-[var(--bg-elevated)] shadow-2xl sm:h-auto sm:max-h-[min(90dvh,760px)] sm:rounded-xl"
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
            <Fact
              label="вх. IP"
              value={fmtCount(inboundIps)}
              valueClass={peersToneClass(inboundIps)}
              title={
                inboundFromXray
                  ? "уникальные клиентские IP Xray за 15 мин"
                  : "уникальные входящие TCP (ss)"
              }
            />
            <Fact
              label="cf_204"
              value={fmtMs(agent?.cf204_ok ? agent.cf204_ms : null)}
              valueClass={pingToneClass(agent?.cf204_ok ? agent.cf204_ms : null)}
            />
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
            <ChartBlock title="cf_204" unit="мс">
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

function Fact({
  label,
  value,
  valueClass,
  title,
}: {
  label: string;
  value: string;
  valueClass?: string;
  title?: string;
}) {
  return (
    <div title={title}>
      <dt className="text-[var(--muted)]">{label}</dt>
      <dd className={`truncate tabular-nums ${valueClass ?? "font-medium text-[var(--text)]"}`}>{value}</dd>
    </div>
  );
}

function fmtCount(v: number | null | undefined): string {
  if (v == null || Number.isNaN(v)) return "—";
  return String(Math.round(v));
}

function peersToneClass(n: number | null | undefined): string {
  const base = "font-bold tabular-nums";
  if (n == null || !Number.isFinite(n)) return `${base} text-[var(--muted)]`;
  if (n === 0) return `${base} text-[var(--muted)]`;
  return `${base} text-[var(--accent)]`;
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
