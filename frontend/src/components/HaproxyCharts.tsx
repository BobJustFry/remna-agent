import { useEffect, useMemo, useState, type PointerEvent, type ReactNode } from "react";
import type { HaproxyHistoryPoint, HaproxyLiveStats, HaproxyStatRow } from "../api/client";

export function formatBytes(n: number | null | undefined): string {
  if (n == null || Number.isNaN(n)) return "—";
  const units = ["Б", "КБ", "МБ", "ГБ", "ТБ"];
  let v = n;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i += 1;
  }
  const digits = i === 0 ? 0 : v >= 10 ? 1 : 2;
  return `${v.toFixed(digits)} ${units[i]}`;
}

const ACCENT = "#22d3bb";
const IN = "#3dd68c";
const OUT = "#6aa7d8";
const MUTED = "#8b9aa5";
const GRID = "#1e2a32";
const MAX_POINTS = 240;
const STORE_PREFIX = "remna.haproxy.hist.v1.";

type Series = {
  id: string;
  label: string;
  color: string;
  values: Array<number | null>;
};

type Hover = { i: number; x: number; y: number } | null;

export function HaproxyCharts({
  stats,
  history,
}: {
  stats: HaproxyLiveStats;
  history: HaproxyHistoryPoint[];
}) {
  const pts = history;
  const fronts = stats.rows.filter((r) => r.svname === "FRONTEND");
  const rates = useMemo(() => trafficRates(pts), [pts]);
  const ages = useMemo(() => sessionAgeBuckets(stats), [stats]);
  const spanMin = spanMinutes(pts);

  return (
    <div className="space-y-3">
      <div className="grid gap-3 lg:grid-cols-2">
        <ChartCard
          title="Сессии"
          caption={
            pts.length < 2
              ? "нужно ещё пару опросов — линия появится сама"
              : `последние ${spanMin} · CurrConns`
          }
        >
          <LineChart
            times={pts.map((p) => p.ts)}
            series={[
              {
                id: "sess",
                label: "сейчас",
                color: ACCENT,
                values: pts.map((p) => p.curr_conns),
              },
            ]}
            formatY={(n) => String(Math.round(n))}
            unit="сессий"
          />
        </ChartCard>
        <ChartCard
          title="Трафик"
          caption={
            rates.inValues.some((v) => v != null)
              ? `последние ${spanMin} · дельта bin/bout`
              : "скорость посчитается со второго опроса"
          }
        >
          <LineChart
            times={pts.map((p) => p.ts)}
            series={[
              { id: "in", label: "вход", color: IN, values: rates.inValues },
              { id: "out", label: "выход", color: OUT, values: rates.outValues },
            ]}
            formatY={formatByteRate}
            unit="Б/с"
          />
        </ChartCard>
      </div>

      {fronts.length > 0 && (
        <ChartCard title="Сейчас по frontend" caption="scur из show stat">
          <FrontBars rows={fronts} />
        </ChartCard>
      )}

      {ages.total > 0 && (
        <ChartCard title="Возраст живых сессий" caption={`show sess · ${ages.total} шт.`}>
          <AgeBars buckets={ages.buckets} />
        </ChartCard>
      )}
    </div>
  );
}

export function MiniSpark({
  values,
  color = ACCENT,
}: {
  values: Array<number | null>;
  color?: string;
}) {
  const nums = values.filter((v): v is number => v != null);
  if (nums.length < 2) return null;
  const w = 72;
  const h = 22;
  const min = Math.min(...nums);
  const max = Math.max(...nums);
  const span = max - min || 1;
  const step = values.length > 1 ? (w - 2) / (values.length - 1) : w;
  const pts = values
    .map((v, i) => {
      if (v == null) return null;
      const x = 1 + i * step;
      const y = h - 2 - ((v - min) / span) * (h - 4);
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .filter(Boolean)
    .join(" ");
  return (
    <svg width={w} height={h} viewBox={`0 0 ${w} ${h}`} className="block" aria-hidden>
      <polyline
        fill="none"
        stroke={color}
        strokeWidth="1.4"
        strokeLinejoin="round"
        strokeLinecap="round"
        points={pts}
      />
    </svg>
  );
}

function ChartCard({
  title,
  caption,
  children,
}: {
  title: string;
  caption: string;
  children: ReactNode;
}) {
  return (
    <div className="rounded-lg border border-[var(--border)] bg-[var(--bg)] px-3 py-2.5">
      <div className="flex items-baseline justify-between gap-3">
        <h4 className="text-[12px] font-semibold text-[var(--text)]">{title}</h4>
        <p className="truncate text-[10px] text-[var(--muted)]">{caption}</p>
      </div>
      <div className="mt-2">{children}</div>
    </div>
  );
}

function LineChart({
  times,
  series,
  formatY,
  unit,
}: {
  times: number[];
  series: Series[];
  formatY: (n: number) => string;
  unit: string;
}) {
  const [hover, setHover] = useState<Hover>(null);
  const w = 560;
  const h = 168;
  const pad = { l: 44, r: 10, t: 10, b: 24 };
  const innerW = w - pad.l - pad.r;
  const innerH = h - pad.t - pad.b;
  const n = times.length;
  const all = series.flatMap((s) => s.values.filter((v): v is number => v != null && Number.isFinite(v)));
  const yMax = niceMax(all.length ? Math.max(...all) : 1);
  const yTicks = [0, yMax / 2, yMax];
  const xAt = (i: number) => pad.l + (n <= 1 ? innerW / 2 : (i / (n - 1)) * innerW);
  const yAt = (v: number) => pad.t + innerH - (v / yMax) * innerH;

  function onMove(e: PointerEvent<SVGRectElement>) {
    if (n === 0) return;
    const svg = e.currentTarget.ownerSVGElement;
    if (!svg) return;
    const box = svg.getBoundingClientRect();
    const x = ((e.clientX - box.left) / box.width) * w;
    const t = (x - pad.l) / innerW;
    const i = Math.min(n - 1, Math.max(0, Math.round(t * (n - 1))));
    setHover({ i, x: xAt(i), y: pad.t });
  }

  return (
    <div className="relative">
      <svg viewBox={`0 0 ${w} ${h}`} className="block h-[168px] w-full" role="img" aria-label={unit}>
        {yTicks.map((tick) => {
          const y = yAt(tick);
          return (
            <g key={tick}>
              <line x1={pad.l} x2={w - pad.r} y1={y} y2={y} stroke={GRID} strokeWidth="1" />
              <text
                x={pad.l - 6}
                y={y + 3}
                textAnchor="end"
                fill={MUTED}
                fontSize="9"
                fontFamily="ui-monospace, monospace"
              >
                {formatY(tick)}
              </text>
            </g>
          );
        })}
        {n >= 2 &&
          [0, Math.floor((n - 1) / 2), n - 1].map((i) => (
            <text
              key={`x-${i}`}
              x={xAt(i)}
              y={h - 6}
              textAnchor={i === 0 ? "start" : i === n - 1 ? "end" : "middle"}
              fill={MUTED}
              fontSize="9"
              fontFamily="ui-monospace, monospace"
            >
              {formatClock(times[i])}
            </text>
          ))}
        {series.map((s) => {
          const path = linePath(s.values, xAt, yAt);
          const area = areaPath(s.values, xAt, yAt, pad.t + innerH);
          if (!path) return null;
          return (
            <g key={s.id}>
              <path d={area} fill={s.color} opacity="0.12" />
              <path d={path} fill="none" stroke={s.color} strokeWidth="1.8" strokeLinejoin="round" />
            </g>
          );
        })}
        {hover && n > 0 && (
          <line
            x1={hover.x}
            x2={hover.x}
            y1={pad.t}
            y2={pad.t + innerH}
            stroke={MUTED}
            strokeWidth="1"
            strokeDasharray="2 3"
          />
        )}
        <rect
          x={pad.l}
          y={pad.t}
          width={innerW}
          height={innerH}
          fill="transparent"
          onPointerMove={onMove}
          onPointerLeave={() => setHover(null)}
        />
      </svg>
      <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-[10px] text-[var(--muted)]">
        {series.map((s) => (
          <span key={s.id} className="inline-flex items-center gap-1.5">
            <span className="inline-block h-1.5 w-1.5 rounded-full" style={{ background: s.color }} />
            {s.label}
          </span>
        ))}
      </div>
      {hover && times[hover.i] != null && (
        <div
          className="pointer-events-none absolute z-10 rounded-md border border-[var(--border)] bg-[var(--bg-elevated)] px-2 py-1.5 text-[11px] shadow-none"
          style={{
            left: `min(calc(${(hover.x / w) * 100}% + 8px), calc(100% - 160px))`,
            top: 8,
          }}
        >
          <div className="font-mono text-[var(--muted)]">{formatClock(times[hover.i], true)}</div>
          {series.map((s) => {
            const v = s.values[hover.i];
            return (
              <div key={s.id} className="tabular-nums text-[var(--text)]">
                <span style={{ color: s.color }}>{s.label}</span>{" "}
                {v == null ? "—" : formatY(v)}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

function FrontBars({ rows }: { rows: HaproxyStatRow[] }) {
  const max = Math.max(1, ...rows.map((r) => Math.max(r.scur ?? 0, r.smax ?? 0, 1)));
  return (
    <div className="space-y-2">
      {rows.map((r) => {
        const cur = r.scur ?? 0;
        const peak = r.smax ?? 0;
        return (
          <div key={`${r.pxname}-${r.svname}`}>
            <div className="mb-0.5 flex items-baseline justify-between gap-2 text-[11px]">
              <span className="truncate font-mono text-[var(--text)]">{r.pxname}</span>
              <span className="shrink-0 tabular-nums text-[var(--muted)]">
                {cur}
                {peak ? ` / пик ${peak}` : ""}
                {r.rate != null ? ` · ${r.rate}/с` : ""}
              </span>
            </div>
            <div className="relative h-2 overflow-hidden rounded-full bg-[var(--bg-row)]">
              {peak > 0 && (
                <div
                  className="absolute inset-y-0 left-0 bg-[var(--border-soft)]"
                  style={{ width: `${(peak / max) * 100}%` }}
                />
              )}
              <div
                className="absolute inset-y-0 left-0 rounded-full bg-[var(--accent)]"
                style={{ width: `${(cur / max) * 100}%` }}
              />
            </div>
            <div className="mt-0.5 flex justify-between text-[10px] text-[var(--muted)]">
              <span>↓ {formatBytes(r.bin)}</span>
              <span>↑ {formatBytes(r.bout)}</span>
            </div>
          </div>
        );
      })}
    </div>
  );
}

function AgeBars({
  buckets,
}: {
  buckets: { label: string; count: number }[];
}) {
  const max = Math.max(1, ...buckets.map((b) => b.count));
  return (
    <div className="grid grid-cols-5 gap-1 sm:gap-2">
      {buckets.map((b) => (
        <div key={b.label} className="flex flex-col items-center gap-1">
          <div className="flex h-16 w-full items-end rounded bg-[var(--bg-row)] px-1.5 pb-0">
            <div
              className="w-full rounded-t bg-[var(--accent)]"
              style={{ height: `${Math.max(b.count ? 8 : 0, (b.count / max) * 100)}%` }}
            />
          </div>
          <div className="text-[10px] tabular-nums text-[var(--text)]">{b.count}</div>
          <div className="text-[10px] text-[var(--muted)]">{b.label}</div>
        </div>
      ))}
    </div>
  );
}

export function useHaproxyHistory(stats: HaproxyLiveStats | null): HaproxyHistoryPoint[] {
  const [pts, setPts] = useState<HaproxyHistoryPoint[]>([]);

  useEffect(() => {
    if (!stats) {
      setPts([]);
      return;
    }
    const stored = loadHistory(stats.node_id);
    const merged = mergeHistory(stats.history ?? [], stored, snapshot(stats));
    saveHistory(stats.node_id, merged);
    setPts(merged);
  }, [stats]);

  return pts;
}

function snapshot(stats: HaproxyLiveStats): HaproxyHistoryPoint | null {
  if (stats.curr_conns == null && stats.bin == null && stats.conn_rate == null) return null;
  return {
    ts: Date.now() / 1000,
    curr_conns: stats.curr_conns,
    conn_rate: stats.conn_rate,
    bin: stats.bin,
    bout: stats.bout,
  };
}

function mergeHistory(
  server: HaproxyHistoryPoint[],
  local: HaproxyHistoryPoint[],
  live: HaproxyHistoryPoint | null,
): HaproxyHistoryPoint[] {
  const map = new Map<number, HaproxyHistoryPoint>();
  for (const p of [...local, ...server, ...(live ? [live] : [])]) {
    const key = Math.round(p.ts * 2) / 2;
    const prev = map.get(key);
    if (!prev) map.set(key, p);
  }
  return [...map.values()]
    .sort((a, b) => a.ts - b.ts)
    .slice(-MAX_POINTS);
}

function loadHistory(nodeId: string | null): HaproxyHistoryPoint[] {
  if (!nodeId) return [];
  try {
    const raw = localStorage.getItem(STORE_PREFIX + nodeId);
    if (!raw) return [];
    const parsed = JSON.parse(raw) as HaproxyHistoryPoint[];
    return Array.isArray(parsed) ? parsed.slice(-MAX_POINTS) : [];
  } catch {
    return [];
  }
}

function saveHistory(nodeId: string | null, pts: HaproxyHistoryPoint[]) {
  if (!nodeId) return;
  try {
    localStorage.setItem(STORE_PREFIX + nodeId, JSON.stringify(pts.slice(-MAX_POINTS)));
  } catch {
    /* ignore quota */
  }
}

function trafficRates(history: HaproxyHistoryPoint[]): {
  inValues: Array<number | null>;
  outValues: Array<number | null>;
} {
  const inValues: Array<number | null> = [];
  const outValues: Array<number | null> = [];
  for (let i = 0; i < history.length; i += 1) {
    const prev = history[i - 1];
    const cur = history[i];
    const dt = prev ? cur.ts - prev.ts : 0;
    if (!prev || dt < 1) {
      inValues.push(null);
      outValues.push(null);
      continue;
    }
    const din = cur.bin != null && prev.bin != null && cur.bin >= prev.bin ? (cur.bin - prev.bin) / dt : null;
    const dout = cur.bout != null && prev.bout != null && cur.bout >= prev.bout ? (cur.bout - prev.bout) / dt : null;
    inValues.push(din);
    outValues.push(dout);
  }
  return { inValues, outValues };
}

function sessionAgeBuckets(stats: HaproxyLiveStats): {
  total: number;
  buckets: { label: string; count: number }[];
} {
  const buckets = [
    { label: "<10с", max: 10, count: 0 },
    { label: "10–60с", max: 60, count: 0 },
    { label: "1–5м", max: 300, count: 0 },
    { label: "5–15м", max: 900, count: 0 },
    { label: ">15м", max: Infinity, count: 0 },
  ];
  let total = 0;
  for (const s of stats.sessions) {
    const sec = parseAgeSec(s.age);
    if (sec == null) continue;
    total += 1;
    const bucket = buckets.find((b) => sec < b.max) ?? buckets[buckets.length - 1];
    bucket.count += 1;
  }
  return { total, buckets: buckets.map(({ label, count }) => ({ label, count })) };
}

function parseAgeSec(age: string | null): number | null {
  if (!age) return null;
  if (/^\d+$/.test(age)) return Number(age);
  let sec = 0;
  const h = age.match(/(\d+)h/);
  const m = age.match(/(\d+)m/);
  const s = age.match(/(\d+)s/);
  if (h) sec += Number(h[1]) * 3600;
  if (m) sec += Number(m[1]) * 60;
  if (s) sec += Number(s[1]);
  return h || m || s ? sec : null;
}

function linePath(
  values: Array<number | null>,
  xAt: (i: number) => number,
  yAt: (v: number) => number,
): string {
  let d = "";
  let drawing = false;
  values.forEach((v, i) => {
    if (v == null || !Number.isFinite(v)) {
      drawing = false;
      return;
    }
    const cmd = drawing ? "L" : "M";
    d += `${cmd}${xAt(i).toFixed(1)},${yAt(v).toFixed(1)} `;
    drawing = true;
  });
  return d.trim();
}

function areaPath(
  values: Array<number | null>,
  xAt: (i: number) => number,
  yAt: (v: number) => number,
  baseline: number,
): string {
  const line = linePath(values, xAt, yAt);
  if (!line) return "";
  let first = -1;
  let last = -1;
  values.forEach((v, i) => {
    if (v == null) return;
    if (first < 0) first = i;
    last = i;
  });
  if (first < 0) return "";
  return `${line} L${xAt(last).toFixed(1)},${baseline.toFixed(1)} L${xAt(first).toFixed(1)},${baseline.toFixed(1)} Z`;
}

function niceMax(n: number): number {
  if (n <= 0) return 1;
  const pow = 10 ** Math.floor(Math.log10(n));
  const m = n / pow;
  const nice = m <= 1 ? 1 : m <= 2 ? 2 : m <= 5 ? 5 : 10;
  return nice * pow;
}

function formatClock(ts: number, withSec = false): string {
  const d = new Date(ts * 1000);
  const hh = String(d.getHours()).padStart(2, "0");
  const mm = String(d.getMinutes()).padStart(2, "0");
  if (!withSec) return `${hh}:${mm}`;
  const ss = String(d.getSeconds()).padStart(2, "0");
  return `${hh}:${mm}:${ss}`;
}

function spanMinutes(history: HaproxyHistoryPoint[]): string {
  if (history.length < 2) return "ожидание";
  const sec = history[history.length - 1].ts - history[0].ts;
  if (sec < 90) return `${Math.max(1, Math.round(sec))} с`;
  if (sec < 3600) return `${Math.round(sec / 60)} мин`;
  return `${(sec / 3600).toFixed(1)} ч`;
}

function formatByteRate(n: number): string {
  const abs = Math.abs(n);
  if (abs < 1024) return `${Math.round(n)} Б/с`;
  if (abs < 1024 * 1024) return `${(n / 1024).toFixed(abs >= 10 * 1024 ? 0 : 1)} КБ/с`;
  return `${(n / (1024 * 1024)).toFixed(abs >= 10 * 1024 * 1024 ? 1 : 2)} МБ/с`;
}
