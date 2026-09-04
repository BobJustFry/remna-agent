/** Live online/capacity number: green under 70% used, yellow 70–90%, red from 90%. */
export function capacityToneClass(
  online: number | null | undefined,
  capacity: number | null | undefined,
): string {
  const base = "font-bold tabular-nums";
  if (online == null || !Number.isFinite(online)) return `${base} text-[var(--muted)]`;
  if (capacity == null || !Number.isFinite(capacity) || capacity <= 0) {
    // No budget to compare against — show the count, but do not imply it is safe.
    return `${base} text-[var(--text)]`;
  }
  const used = online / capacity;
  if (used < 0.7) return `${base} text-[var(--success)]`;
  if (used < 0.9) return `${base} text-[var(--warning)]`;
  return `${base} text-[var(--danger)]`;
}

/** "12/180", or "12/—" while the node runs an agent older than 0.1.16. */
export function capacityLabel(
  online: number | null | undefined,
  capacity: number | null | undefined,
): string {
  const x = online == null || !Number.isFinite(online) ? "—" : String(online);
  const y = capacity == null || !Number.isFinite(capacity) || capacity <= 0 ? "—" : String(capacity);
  return `${x}/${y}`;
}

export function capacityTitle(
  online: number | null | undefined,
  capacity: number | null | undefined,
  limiter: string | null | undefined,
): string {
  if (capacity == null || capacity <= 0) {
    return "Ёмкость не известна — агент ноды старее 0.1.16 или не отвечает";
  }
  const pct = online != null && Number.isFinite(online)
    ? ` · занято ${Math.round((online / capacity) * 100)}%`
    : "";
  const by = limiter ? ` · упирается в ${limiter}` : "";
  return `Онлайн в ядре Xray из ёмкости ноды на текущем конфиге${pct}${by}`;
}
