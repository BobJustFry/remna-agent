import { useState, type PointerEvent } from "react";

const MUTED = "#8b9aa5";
const GRID = "#1e2a32";

type Props = {
  times: number[];
  values: Array<number | null>;
  color: string;
  unit: string;
  formatY?: (n: number) => string;
  maxY?: number;
  height?: number;
  /** Unix seconds. Chart X always spans this window (e.g. last 24h). */
  xMin: number;
  xMax: number;
};

export function NodeMetricLineChart({
  times,
  values,
  color,
  unit,
  formatY = (n) => String(Math.round(n)),
  maxY,
  height = 132,
  xMin,
  xMax,
}: Props) {
  const [hover, setHover] = useState<{ i: number; x: number } | null>(null);
  const w = 640;
  const h = height;
  const pad = { l: 36, r: 8, t: 8, b: 20 };
  const innerW = w - pad.l - pad.r;
  const innerH = h - pad.t - pad.b;
  const span = Math.max(1, xMax - xMin);
  const nums = values.filter((v): v is number => v != null && Number.isFinite(v));
  const yMax = niceMax(maxY ?? (nums.length ? Math.max(...nums) : 1));
  const yTicks = [0, yMax / 2, yMax];
  const xAtT = (t: number) => pad.l + ((t - xMin) / span) * innerW;
  const yAt = (v: number) => pad.t + innerH - (v / yMax) * innerH;
  const xLabels = [0, 0.25, 0.5, 0.75, 1].map((p) => xMin + p * span);

  function onMove(e: PointerEvent<SVGRectElement>) {
    if (times.length === 0) return;
    const svg = e.currentTarget.ownerSVGElement;
    if (!svg) return;
    const box = svg.getBoundingClientRect();
    const x = ((e.clientX - box.left) / box.width) * w;
    const t = xMin + ((x - pad.l) / innerW) * span;
    let best = 0;
    let bestD = Infinity;
    times.forEach((ts, i) => {
      const d = Math.abs(ts - t);
      if (d < bestD) {
        bestD = d;
        best = i;
      }
    });
    setHover({ i: best, x: xAtT(times[best]) });
  }

  const path = linePath(times, values, xAtT, yAt);
  const area = areaPath(times, values, xAtT, yAt, pad.t + innerH);

  return (
    <div className="relative">
      <svg viewBox={`0 0 ${w} ${h}`} className="block w-full" style={{ height }} role="img" aria-label={unit}>
        {yTicks.map((tick) => {
          const y = yAt(tick);
          return (
            <g key={tick}>
              <line x1={pad.l} x2={w - pad.r} y1={y} y2={y} stroke={GRID} strokeWidth="1" />
              <text
                x={pad.l - 5}
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
        {xLabels.map((t, i) => (
          <text
            key={`x-${i}`}
            x={xAtT(t)}
            y={h - 4}
            textAnchor={i === 0 ? "start" : i === xLabels.length - 1 ? "end" : "middle"}
            fill={MUTED}
            fontSize="9"
            fontFamily="ui-monospace, monospace"
          >
            {formatTick(t, span)}
          </text>
        ))}
        {area && <path d={area} fill={color} opacity="0.12" />}
        {path && (
          <path d={path} fill="none" stroke={color} strokeWidth="1.8" strokeLinejoin="round" />
        )}
        {hover && times[hover.i] != null && (
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
      {hover && times[hover.i] != null && (
        <div
          className="pointer-events-none absolute z-10 rounded-md border border-[var(--border)] bg-[var(--bg-elevated)] px-2 py-1.5 text-[11px]"
          style={{
            left: `min(calc(${(hover.x / w) * 100}% + 8px), calc(100% - 140px))`,
            top: 8,
          }}
        >
          <div className="font-mono text-[var(--muted)]">{formatTick(times[hover.i], span, true)}</div>
          <div className="tabular-nums text-[var(--text)]">
            {values[hover.i] == null ? "—" : `${formatY(values[hover.i] as number)} ${unit}`}
          </div>
        </div>
      )}
      {nums.length < 2 && (
        <div className="absolute inset-0 flex items-center justify-center text-xs text-[var(--muted)]">
          Пока нет истории — первый замер через ~30 с
        </div>
      )}
    </div>
  );
}

function linePath(
  times: number[],
  values: Array<number | null>,
  xAtT: (t: number) => number,
  yAt: (v: number) => number,
): string {
  let d = "";
  let drawing = false;
  values.forEach((v, i) => {
    const t = times[i];
    if (v == null || !Number.isFinite(v) || t == null) {
      drawing = false;
      return;
    }
    const cmd = drawing ? "L" : "M";
    d += `${cmd}${xAtT(t).toFixed(1)},${yAt(v).toFixed(1)} `;
    drawing = true;
  });
  return d.trim();
}

function areaPath(
  times: number[],
  values: Array<number | null>,
  xAtT: (t: number) => number,
  yAt: (v: number) => number,
  baseline: number,
): string {
  const line = linePath(times, values, xAtT, yAt);
  if (!line) return "";
  let first = -1;
  let last = -1;
  values.forEach((v, i) => {
    if (v == null || times[i] == null) return;
    if (first < 0) first = i;
    last = i;
  });
  if (first < 0) return "";
  return `${line} L${xAtT(times[last]).toFixed(1)},${baseline.toFixed(1)} L${xAtT(times[first]).toFixed(1)},${baseline.toFixed(1)} Z`;
}

function niceMax(n: number): number {
  if (n <= 0) return 1;
  const pow = 10 ** Math.floor(Math.log10(n));
  const m = n / pow;
  const nice = m <= 1 ? 1 : m <= 2 ? 2 : m <= 5 ? 5 : 10;
  return nice * pow;
}

function formatTick(ts: number, spanSec: number, withTime = false): string {
  const d = new Date(ts * 1000);
  const dd = String(d.getDate()).padStart(2, "0");
  const mo = String(d.getMonth() + 1).padStart(2, "0");
  const hh = String(d.getHours()).padStart(2, "0");
  const mm = String(d.getMinutes()).padStart(2, "0");
  if (withTime) return `${dd}.${mo} ${hh}:${mm}`;
  if (spanSec > 36 * 3600) return `${dd}.${mo}`;
  return `${hh}:${mm}`;
}
