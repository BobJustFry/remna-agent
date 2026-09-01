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
};

export function NodeMetricLineChart({
  times,
  values,
  color,
  unit,
  formatY = (n) => String(Math.round(n)),
  maxY,
  height = 180,
}: Props) {
  const [hover, setHover] = useState<{ i: number; x: number } | null>(null);
  const w = 640;
  const h = height;
  const pad = { l: 40, r: 10, t: 10, b: 22 };
  const innerW = w - pad.l - pad.r;
  const innerH = h - pad.t - pad.b;
  const n = times.length;
  const nums = values.filter((v): v is number => v != null && Number.isFinite(v));
  const yMax = niceMax(maxY ?? (nums.length ? Math.max(...nums) : 1));
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
    setHover({ i, x: xAt(i) });
  }

  const path = linePath(values, xAt, yAt);
  const area = areaPath(values, xAt, yAt, pad.t + innerH);

  return (
    <div className="relative">
      <svg viewBox={`0 0 ${w} ${h}`} className="block w-full" style={{ height }} role="img" aria-label={unit}>
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
              y={h - 5}
              textAnchor={i === 0 ? "start" : i === n - 1 ? "end" : "middle"}
              fill={MUTED}
              fontSize="9"
              fontFamily="ui-monospace, monospace"
            >
              {formatTick(times[i])}
            </text>
          ))}
        {area && <path d={area} fill={color} opacity="0.12" />}
        {path && (
          <path d={path} fill="none" stroke={color} strokeWidth="1.8" strokeLinejoin="round" />
        )}
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
      {hover && times[hover.i] != null && (
        <div
          className="pointer-events-none absolute z-10 rounded-md border border-[var(--border)] bg-[var(--bg-elevated)] px-2 py-1.5 text-[11px]"
          style={{
            left: `min(calc(${(hover.x / w) * 100}% + 8px), calc(100% - 140px))`,
            top: 8,
          }}
        >
          <div className="font-mono text-[var(--muted)]">{formatTick(times[hover.i], true)}</div>
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

function formatTick(ts: number, withTime = false): string {
  const d = new Date(ts * 1000);
  const dd = String(d.getDate()).padStart(2, "0");
  const mo = String(d.getMonth() + 1).padStart(2, "0");
  const hh = String(d.getHours()).padStart(2, "0");
  const mm = String(d.getMinutes()).padStart(2, "0");
  if (withTime) return `${dd}.${mo} ${hh}:${mm}`;
  const spanHint = Date.now() / 1000 - ts;
  if (spanHint > 36 * 3600) return `${dd}.${mo}`;
  return `${hh}:${mm}`;
}
