type Props = {
  times: number[];
  values: Array<number | null>;
  color: string;
  maxY?: number;
  fromTs: number;
  toTs: number;
};

export function NodeMetricSpark({ times, values, color, maxY, fromTs, toTs }: Props) {
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
  const max = Math.max(maxY ?? dataMax, dataMax, 1);
  const pts: string[] = [];
  values.forEach((v, i) => {
    const t = times[i];
    if (v == null || !Number.isFinite(v) || t == null) return;
    const x = 1 + ((t - fromTs) / span) * (w - 2);
    const y = h - 1.5 - (v / max) * (h - 3);
    pts.push(`${x.toFixed(1)},${y.toFixed(1)}`);
  });
  const lastI = values.reduce((acc, v, i) => (v != null ? i : acc), -1);
  const last = lastI >= 0 ? (values[lastI] as number) : nums[nums.length - 1];
  const lastT = lastI >= 0 ? times[lastI] : toTs;
  const lastX = 1 + ((lastT - fromTs) / span) * (w - 2);
  const lastY = h - 1.5 - (last / max) * (h - 3);
  return (
    <svg viewBox={`0 0 ${w} ${h}`} className="block h-full w-full" preserveAspectRatio="none" aria-hidden>
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
