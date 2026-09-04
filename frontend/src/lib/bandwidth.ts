/** Traffic rate against the hosting's channel: green under 70% used, yellow 70–90%, red from 90%. */
export function bandwidthToneClass(
  bps: number | null | undefined,
  limitMbps: number | null | undefined,
): string {
  const base = "font-bold tabular-nums";
  if (bps == null || !Number.isFinite(bps)) return `${base} text-[var(--muted)]`;
  if (limitMbps == null || !Number.isFinite(limitMbps) || limitMbps <= 0) {
    // No channel limit on the hosting — show the rate, but claim nothing about headroom.
    return `${base} text-[var(--text)]`;
  }
  const used = bps / (limitMbps * 1_000_000);
  if (used < 0.7) return `${base} text-[var(--success)]`;
  if (used < 0.9) return `${base} text-[var(--warning)]`;
  return `${base} text-[var(--danger)]`;
}

/** Up and down on one line under a shared unit: "↑6.2 ↓1.1 M".
 *
 * Two numbers each carrying their own unit did not fit the tile column and got
 * truncated to "6.2 M/...". The larger value picks the unit for both.
 */
export function formatUpDown(
  tx: number | null | undefined,
  rx: number | null | undefined,
): string {
  const up = tx != null && Number.isFinite(tx) ? tx : null;
  const down = rx != null && Number.isFinite(rx) ? rx : null;
  if (up == null && down == null) return "—";
  const peak = Math.max(up ?? 0, down ?? 0);
  const [div, unit] =
    peak >= 1_000_000_000
      ? [1_000_000_000, "G"]
      : peak >= 1_000_000
        ? [1_000_000, "M"]
        : peak >= 1_000
          ? [1_000, "K"]
          : [1, "b"];
  const fmt = (v: number | null) => {
    if (v == null) return "—";
    const n = v / div;
    return n < 10 && div > 1 ? n.toFixed(1) : String(Math.round(n));
  };
  return `↑${fmt(up)} ↓${fmt(down)} ${unit}`;
}

/** Compact bit-rate: 940 Kb, 12.4 Mb, 1.2 Gb. Decimal units — that is how links are sold. */
export function formatBps(bps: number | null | undefined): string {
  if (bps == null || !Number.isFinite(bps) || bps < 0) return "—";
  if (bps < 1_000) return `${Math.round(bps)} b`;
  if (bps < 1_000_000) return `${Math.round(bps / 1_000)} K`;
  if (bps < 1_000_000_000) {
    const m = bps / 1_000_000;
    return `${m < 10 ? m.toFixed(1) : Math.round(m)} M`;
  }
  return `${(bps / 1_000_000_000).toFixed(1)} G`;
}

export function bandwidthTitle(
  rx: number | null | undefined,
  tx: number | null | undefined,
  limitMbps: number | null | undefined,
  iface: string | null | undefined,
): string {
  if (rx == null && tx == null) {
    return "Скорость канала не известна — агент ноды старее 0.1.17 или ещё не сделал вторую выборку";
  }
  const peak = Math.max(rx ?? 0, tx ?? 0);
  const on = iface ? ` · ${iface}` : "";
  if (limitMbps == null || limitMbps <= 0) {
    return `Приём / отдача${on} · лимит канала у хостинга не задан`;
  }
  const pct = Math.round((peak / (limitMbps * 1_000_000)) * 100);
  return `Приём / отдача${on} · канал хостинга ${limitMbps} Мбит/с · занято ${pct}%`;
}
