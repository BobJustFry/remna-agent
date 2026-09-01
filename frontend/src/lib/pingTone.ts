/** Live cf_204 / ping number: green ≤50, yellow <100, red blink ≥100 or missing. */
export function pingToneClass(ms: number | null | undefined): string {
  const base = "font-bold tabular-nums";
  if (ms == null || !Number.isFinite(ms)) {
    return `${base} text-[var(--danger)] ping-blink`;
  }
  if (ms <= 50) return `${base} text-[var(--success)]`;
  if (ms < 100) return `${base} text-[var(--warning)]`;
  return `${base} text-[var(--danger)] ping-blink`;
}
