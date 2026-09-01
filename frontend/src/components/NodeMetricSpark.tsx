type Props = {
  values: Array<number | null>;
  color: string;
  maxY?: number;
};

export function NodeMetricSpark({ values, color, maxY }: Props) {
  const nums = values.filter((v): v is number => v != null && Number.isFinite(v));
  if (nums.length < 1) {
    return (
      <div className="flex h-full items-center text-[9px] text-[var(--muted)]">нет данных</div>
    );
  }
  const w = 200;
  const h = 28;
  const min = 0;
  const dataMax = Math.max(...nums);
  const max = Math.max(maxY ?? dataMax, dataMax, 1);
  const span = max - min || 1;
  const n = values.length;
  const step = n > 1 ? (w - 2) / (n - 1) : w;
  const pts: string[] = [];
  values.forEach((v, i) => {
    if (v == null || !Number.isFinite(v)) return;
    const x = 1 + i * step;
    const y = h - 1.5 - ((v - min) / span) * (h - 3);
    pts.push(`${x.toFixed(1)},${y.toFixed(1)}`);
  });
  const last = nums[nums.length - 1];
  const lastX = 1 + (n - 1) * step;
  const lastY = h - 1.5 - ((last - min) / span) * (h - 3);
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
