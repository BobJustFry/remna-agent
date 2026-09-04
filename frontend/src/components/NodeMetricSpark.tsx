type Props = {
  times: number[];
  values: Array<number | null>;
  color: string;
  maxY?: number;
  fromTs: number;
  toTs: number;
  /**
   * Draw a dashed reference line at this value — a ceiling the series should stay
   * under. The scale still follows the data, so a small series against a huge
   * ceiling stays readable; the line moves instead of flattening the curve.
   */
  refY?: number;
  refColor?: string;
};

export function NodeMetricSpark({
  times,
  values,
  color,
  maxY,
  fromTs,
  toTs,
  refY,
  refColor,
}: Props) {
  const nums = values.filter((v): v is number => v != null && Number.isFinite(v));
  if (nums.length < 1) {
    return (
      <div className="flex h-full items-center text-[9px] text-[var(--muted)]">нет данных</div>
    );
  }
  const w = 200;
  const h = 28;
  const span = Math.max(1, toTs - fromTs);
  const dataMax = Math.max(...nums);
  // Headroom above the peak so the line does not touch the top edge. A reference
  // line is only allowed to raise the scale while it stays near the data — a
  // 1 Gbit/s ceiling must not flatten a 6 Mbit/s series into the baseline.
  const scaleTarget = Math.max(maxY ?? dataMax, dataMax, 1) * 1.15;
  const max =
    refY != null && refY > 0 && refY <= dataMax * 2 ? Math.max(scaleTarget, refY * 1.05) : scaleTarget;
  const refYPos = refY != null && refY > 0 ? h - 1.5 - (Math.min(refY, max) / max) * (h - 3) : null;
  const refClamped = refY != null && refY > max;
  const pts: string[] = [];
  let last = nums[nums.length - 1];
  let lastT = toTs;
  values.forEach((v, i) => {
    const t = times[i];
    if (v == null || !Number.isFinite(v) || t == null) return;
    const x = 1 + ((t - fromTs) / span) * (w - 2);
    const y = h - 1.5 - (v / max) * (h - 3);
    pts.push(`${x.toFixed(1)},${y.toFixed(1)}`);
    last = v;
    lastT = t;
  });
  const lastX = 1 + ((lastT - fromTs) / span) * (w - 2);
  const lastY = h - 1.5 - (last / max) * (h - 3);
  return (
    <svg viewBox={`0 0 ${w} ${h}`} className="block h-full w-full" preserveAspectRatio="none" aria-hidden>
      {refYPos != null && (
        <line
          x1="0"
          x2={w}
          y1={refClamped ? 1 : refYPos}
          y2={refClamped ? 1 : refYPos}
          stroke={refColor ?? "var(--danger)"}
          strokeWidth="1"
          strokeDasharray="3 3"
          opacity={refClamped ? 0.28 : 0.5}
          vectorEffect="non-scaling-stroke"
        />
      )}
      <polyline
        fill="none"
        stroke={color}
        strokeWidth="1.6"
        strokeLinejoin="round"
        strokeLinecap="round"
        vectorEffect="non-scaling-stroke"
        points={pts.join(" ")}
      />
      <circle cx={lastX} cy={lastY} r="1.8" fill={color} vectorEffect="non-scaling-stroke" />
    </svg>
  );
}
