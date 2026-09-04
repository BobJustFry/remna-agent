import { useCallback, useEffect, useState, type ReactNode } from "react";
import { api } from "../api/client";
import type {
  AgentMap,
  MetricPoint,
  MetricsRange,
  NodeItem,
  NodeKind,
  OnlineMap,
  SharingUserHit,
  XrayOnlineMap,
} from "../types";
import { CountryFlag } from "./CountryFlag";
import { NodeMetricLineChart } from "./NodeMetricCharts";
import { NodeMetricSpark } from "./NodeMetricSpark";
import { inboundPeerCount, inboundPeerFromXray } from "../lib/inboundPeerCount";
import { pingToneClass } from "../lib/pingTone";
import { capacityLabel, capacityTitle, capacityToneClass } from "../lib/capacityTone";
import { bandwidthTitle, bandwidthToneClass, formatUpDown } from "../lib/bandwidth";
import { OnlineBadge } from "./OnlineBadge";
import { SharedBadge } from "./SharedBadge";
import { useSharingStatus } from "../hooks/useSharingStatus";

const PING = "#22d3bb";
const CPU = "#e6a23c";
const DISK = "#6aa7d8";
const MEM = "#3dd68c";
const NET = "#b98cd6";
const ONLINE_OK = "#3dd68c";
const ONLINE_WARN = "#e6a23c";
const ONLINE_DANGER = "#f07178";

const PERIODS: { id: MetricsRange; label: string }[] = [
  { id: "day", label: "сутки" },
  { id: "week", label: "неделя" },
  { id: "month", label: "месяц" },
  { id: "all", label: "всё время" },
];

const SECTIONS: { kind: NodeKind; title: string }[] = [
  { kind: "xray", title: "XRAY-ноды" },
  { kind: "proxy", title: "Прокси HAProxy" },
  { kind: "unknown", title: "Тип не определён" },
];

/** Kind comes from the agent. Without one we cannot tell, so the node is grouped apart. */
function groupByKind(nodes: NodeItem[], agents: AgentMap): Record<NodeKind, NodeItem[]> {
  const out: Record<NodeKind, NodeItem[]> = { xray: [], proxy: [], unknown: [] };
  for (const n of nodes) {
    const kind = agents[n.id]?.kind ?? "unknown";
    out[kind === "xray" || kind === "proxy" ? kind : "unknown"].push(n);
  }
  return out;
}

function RangeDayIcon() {
  return (
    <svg viewBox="0 0 16 16" width="13" height="13" fill="none" stroke="currentColor" strokeWidth="1.4" aria-hidden="true">
      <circle cx="8" cy="8" r="5.2" />
      <path d="M8 5.2V8l1.9 1.2" strokeLinecap="round" />
    </svg>
  );
}

function RangeWeekIcon() {
  return (
    <svg viewBox="0 0 16 16" width="13" height="13" fill="none" stroke="currentColor" strokeWidth="1.4" aria-hidden="true">
      <rect x="2.2" y="3.4" width="11.6" height="10" rx="1.4" />
      <path d="M2.2 6.4h11.6M5.4 2.2v2.2M10.6 2.2v2.2" strokeLinecap="round" />
    </svg>
  );
}

function RangeMonthIcon() {
  return (
    <svg viewBox="0 0 16 16" width="13" height="13" fill="none" stroke="currentColor" strokeWidth="1.4" aria-hidden="true">
      <rect x="2.2" y="3.4" width="11.6" height="10" rx="1.4" />
      <path d="M2.2 6.4h11.6" strokeLinecap="round" />
      <path d="M5 8.8h1.4M9.6 8.8H11M5 11h1.4M9.6 11H11" strokeLinecap="round" />
    </svg>
  );
}

function RangeAllIcon() {
  return (
    <svg viewBox="0 0 16 16" width="13" height="13" fill="none" stroke="currentColor" strokeWidth="1.4" aria-hidden="true">
      <path d="M1.8 11.4 5.4 6.6l2.9 2.6 4-6" strokeLinecap="round" strokeLinejoin="round" />
      <path d="M10.6 3.2h3.6v3.4" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

type Props = {
  nodes: NodeItem[];
  statuses: OnlineMap;
  agentStatuses: AgentMap;
  xrayOnline: XrayOnlineMap;
};

/** Icon-only period switcher: the tiles are dense, labels would crowd them out. */
const RANGE_BUTTONS: { id: MetricsRange; title: string; icon: ReactNode }[] = [
  { id: "day", title: "Сутки", icon: <RangeDayIcon /> },
  { id: "week", title: "Неделя", icon: <RangeWeekIcon /> },
  { id: "month", title: "Месяц", icon: <RangeMonthIcon /> },
  { id: "all", title: "Всё время", icon: <RangeAllIcon /> },
];

export function NodeMetricsGrid({ nodes, statuses, agentStatuses, xrayOnline }: Props) {
  const [series, setSeries] = useState<Record<string, MetricPoint[]>>({});
  const [fromTs, setFromTs] = useState(() => Date.now() / 1000 - 86400);
  const [toTs, setToTs] = useState(() => Date.now() / 1000);
  const [openId, setOpenId] = useState<string | null>(null);
  const [range, setRange] = useState<MetricsRange>("day");
  const sharing = useSharingStatus();

  const load = useCallback(async () => {
    try {
      const data = await api.nodeMetrics(range);
      setSeries(data.series ?? {});
      setFromTs(data.from_ts);
      setToTs(data.to_ts);
    } catch {
      /* keep last */
    }
  }, [range]);

  useEffect(() => {
    void load();
    const id = window.setInterval(() => void load(), 30000);
    return () => window.clearInterval(id);
  }, [load]);

  const openNode = nodes.find((n) => n.id === openId) ?? null;

  const groups = groupByKind(nodes, agentStatuses);

  const renderTiles = (list: NodeItem[]) => (
    <div className="grid auto-rows-min grid-cols-1 gap-1.5 min-[420px]:grid-cols-2 sm:grid-cols-3 md:grid-cols-4 xl:grid-cols-6 2xl:grid-cols-7">
      {list.map((node) => (
        <NodeTile
          key={node.id}
          node={node}
          points={series[node.id] ?? []}
          fromTs={fromTs}
          toTs={toTs}
          status={statuses[node.id]}
          agent={agentStatuses[node.id]}
          xrayOnline={agentStatuses[node.id]?.xray_online ?? xrayOnline[node.id] ?? null}
          inboundIps={inboundPeerCount(agentStatuses[node.id], sharing, node.id)}
          inboundFromXray={inboundPeerFromXray(agentStatuses[node.id], sharing, node.id)}
          sharingHits={sharing?.by_agent_id[node.id] ?? []}
          onOpen={() => setOpenId(node.id)}
        />
      ))}
    </div>
  );

  return (
    <>
      <div className="flex h-full min-h-0 flex-col gap-2 overflow-auto">
        <div className="flex items-center justify-end gap-1">
          <div className="inline-flex rounded-md border border-[var(--border)] p-0.5" role="group" aria-label="Период графиков">
            {RANGE_BUTTONS.map((b) => (
              <button
                key={b.id}
                type="button"
                onClick={() => setRange(b.id)}
                title={b.title}
                aria-label={b.title}
                aria-pressed={range === b.id}
                className={`inline-flex h-6 w-7 items-center justify-center rounded transition ${
                  range === b.id
                    ? "bg-[var(--accent-dim)] text-[var(--accent)]"
                    : "text-[var(--muted)] hover:bg-[var(--bg-row)] hover:text-[var(--text)]"
                }`}
              >
                {b.icon}
              </button>
            ))}
          </div>
        </div>

        {SECTIONS.map(({ kind, title }) => {
          const list = groups[kind];
          if (!list.length) return null;
          return (
            <section key={kind} className="min-w-0">
              <h2 className="mb-1 flex items-center gap-1.5 text-[10px] font-semibold uppercase tracking-wider text-[var(--muted)]">
                {title}
                <span className="rounded bg-[var(--bg-row)] px-1 tabular-nums">{list.length}</span>
              </h2>
              {renderTiles(list)}
            </section>
          );
        })}
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
  xrayOnline,
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
  xrayOnline: number | null;
  inboundIps: number | null;
  inboundFromXray: boolean;
  sharingHits: SharingUserHit[];
  onOpen: () => void;
}) {
  const cpuLive = agent?.present ? agent.cpu_percent : null;
  const diskLive = agent?.present ? agent.disk_percent : null;
  const capacity = agent?.present ? agent.capacity_comfort : null;
  // Линия онлайна красится по той же шкале, что и само число, иначе плитка
  // говорила бы две разные вещи об одном показателе.
  const onlineTone =
    capacity != null && capacity > 0 && xrayOnline != null
      ? xrayOnline / capacity < 0.7
        ? ONLINE_OK
        : xrayOnline / capacity < 0.9
          ? ONLINE_WARN
          : ONLINE_DANGER
      : ONLINE_OK;
  const netRx = agent?.present ? agent.net_rx_bps : null;
  const netTx = agent?.present ? agent.net_tx_bps : null;
  const bandwidthLimit = agent?.hosting_bandwidth_mbps ?? null;
  // One channel carries both directions, so the busier one decides the colour.
  const netPeak =
    netRx == null && netTx == null ? null : Math.max(netRx ?? 0, netTx ?? 0);
  const times = points.map((p) => p.t);
  return (
    <article className="relative flex min-h-[162px] flex-col rounded-md border border-[var(--border)] bg-[var(--bg-elevated)] px-1.5 py-1">
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
      <div className="mt-0.5 grid min-h-0 flex-1 grid-rows-[1fr_1fr_1fr_auto_1fr] gap-0.5">
        <SparkRow
          label="онлайн"
          value={capacityLabel(xrayOnline, capacity)}
          valueClass={`${capacityToneClass(xrayOnline, capacity)} text-[11px]`}
          color={onlineTone}
          times={times}
          values={points.map((p) => p.users_online)}
          fromTs={fromTs}
          toTs={toTs}
          refY={capacity ?? undefined}
          title={capacityTitle(xrayOnline, capacity, agent?.capacity_limiter)}
        />
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
        <BarRow label="диск" percent={diskLive} color={DISK} />
        <SparkRow
          label="канал"
          value={formatUpDown(netTx, netRx)}
          valueClass={`${bandwidthToneClass(netPeak, bandwidthLimit)} text-[10px]`}
          color={NET}
          times={times}
          values={points.map((p) =>
            p.net_rx_bps == null && p.net_tx_bps == null
              ? null
              : Math.max(p.net_rx_bps ?? 0, p.net_tx_bps ?? 0),
          )}
          fromTs={fromTs}
          toTs={toTs}
          refY={bandwidthLimit ? bandwidthLimit * 1_000_000 : undefined}
          title={bandwidthTitle(netRx, netTx, bandwidthLimit, agent?.net_iface)}
        />
      </div>
    </article>
  );
}

function BarRow({
  label,
  percent,
  color,
}: {
  label: string;
  percent: number | null | undefined;
  color: string;
}) {
  const pct = percent != null && Number.isFinite(percent) ? Math.max(0, Math.min(100, percent)) : null;
  // Same 70/90 bands as the online and channel figures, so one glance reads the
  // same way everywhere on the tile. Below 70 the bar keeps its neutral colour.
  const tone = pct == null || pct < 70 ? color : pct < 90 ? "var(--warning)" : "var(--danger)";
  return (
    <div className="flex items-center gap-1" title="Занято на диске">
      <div className="w-[68px] shrink-0 leading-tight">
        <div className="text-[9px] uppercase tracking-wide text-[var(--muted)]">{label}</div>
      </div>
      <div className="flex min-w-0 flex-1 items-center gap-1.5">
        <div className="h-1 flex-1 overflow-hidden rounded-full bg-[var(--border)]">
          {pct != null && (
            <div className="h-full rounded-full" style={{ width: `${pct}%`, background: tone }} />
          )}
        </div>
        <span className="shrink-0 text-[10px] font-semibold tabular-nums" style={{ color: tone }}>
          {pct != null ? `${Math.round(pct)}%` : "—"}
        </span>
      </div>
    </div>
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
  refY,
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
  refY?: number;
  title?: string;
}) {
  return (
    <div className="flex min-h-0 items-stretch gap-1" title={title}>
      <div className="w-[68px] shrink-0 leading-tight">
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
          refY={refY}
          fromTs={fromTs}
          toTs={toTs}
        />
      </div>
    </div>
  );
}

const toMbit = (bps: number | null | undefined) =>
  bps == null || !Number.isFinite(bps) ? null : bps / 1_000_000;

const fmtMbit = (n: number) => (n >= 10 ? String(Math.round(n)) : n.toFixed(1));

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
  // Ёмкость для пунктира и подписи. Берём ту же величину, что показывает плитка.
  const modalCapacity = agent?.present ? agent.capacity_comfort : null;
  const onlineNums = points
    .map((p) => p.users_online)
    .filter((v): v is number => v != null && Number.isFinite(v));
  const onlinePeak = onlineNums.length ? Math.round(Math.max(...onlineNums)) : null;
  const onlineAvg = onlineNums.length
    ? Math.round(onlineNums.reduce((a, b) => a + b, 0) / onlineNums.length)
    : 0;

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
  // Charts autoscale to the data: pinning them to a 1 Gbit/s ceiling would flatten
  // a few-Mbit series into the baseline. The ceiling goes in the title instead.
  const limitLabel = agent?.hosting_bandwidth_mbps
    ? ` · лимит ${agent.hosting_bandwidth_mbps}`
    : "";
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
            <Fact
              label="онлайн"
              value={capacityLabel(
                agent?.xray_online ?? null,
                agent?.present ? agent.capacity_comfort : null,
              )}
              valueClass={capacityToneClass(
                agent?.xray_online ?? null,
                agent?.present ? agent.capacity_comfort : null,
              )}
              title={capacityTitle(
                agent?.xray_online ?? null,
                agent?.present ? agent.capacity_comfort : null,
                agent?.capacity_limiter,
              )}
            />
            <Fact
              label="канал"
              value={formatUpDown(
                agent?.present ? agent.net_tx_bps : null,
                agent?.present ? agent.net_rx_bps : null,
              )}
              valueClass={bandwidthToneClass(
                agent?.present
                  ? Math.max(agent.net_rx_bps ?? 0, agent.net_tx_bps ?? 0)
                  : null,
                agent?.hosting_bandwidth_mbps ?? null,
              )}
              title={bandwidthTitle(
                agent?.net_rx_bps ?? null,
                agent?.net_tx_bps ?? null,
                agent?.hosting_bandwidth_mbps ?? null,
                agent?.net_iface,
              )}
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
            <div className="sm:col-span-2">
              <ChartBlock
                title={`Онлайн и ёмкость${
                  onlinePeak != null ? ` · пик ${onlinePeak}, среднее ${onlineAvg}` : ""
                }`}
                unit="чел."
              >
                <NodeMetricLineChart
                  {...chart}
                  values={points.map((p) => p.users_online)}
                  color={MEM}
                  unit="чел."
                  refY={modalCapacity ?? undefined}
                  refLabel={modalCapacity ? `ёмкость ${modalCapacity}` : undefined}
                />
              </ChartBlock>
            </div>
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
            <ChartBlock title={`Канал ↑ отдача${limitLabel}`} unit="Мбит/с">
              <NodeMetricLineChart
                {...chart}
                values={points.map((p) => toMbit(p.net_tx_bps))}
                color={NET}
                unit="Мбит/с"
                formatY={fmtMbit}
              />
            </ChartBlock>
            <ChartBlock title={`Канал ↓ приём${limitLabel}`} unit="Мбит/с">
              <NodeMetricLineChart
                {...chart}
                values={points.map((p) => toMbit(p.net_rx_bps))}
                color={NET}
                unit="Мбит/с"
                formatY={fmtMbit}
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
