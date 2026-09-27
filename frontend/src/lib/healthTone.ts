/**
 * Цвета для здоровья ноды: ретрансмиты TCP, потери и пинг до России, своп.
 * Пороги те же по духу, что у онлайна и канала: зелёный — норма, жёлтый —
 * присмотреться, красный — клиенты уже чувствуют.
 */

const BASE = "font-bold tabular-nums";

/** Доля ретрансмитов TCP, %: до 2 — норма для прокси, 2–5 — сеть сыплет, от 5 — плохо. */
export function retrToneClass(pct: number | null | undefined): string {
  if (pct == null || !Number.isFinite(pct)) return `${BASE} text-[var(--muted)]`;
  if (pct < 2) return `${BASE} text-[var(--success)]`;
  if (pct < 5) return `${BASE} text-[var(--warning)]`;
  return `${BASE} text-[var(--danger)]`;
}

/** Потери до РФ, %: 0 — чисто, до 5 — единичные, дальше — заметно клиентам. */
export function lossToneClass(pct: number | null | undefined): string {
  if (pct == null || !Number.isFinite(pct)) return `${BASE} text-[var(--muted)]`;
  if (pct < 1) return `${BASE} text-[var(--success)]`;
  if (pct < 5) return `${BASE} text-[var(--warning)]`;
  return `${BASE} text-[var(--danger)]`;
}

/** «42 мс · 0%»; «— · 100%», когда не ответил ни один якорь. */
export function ruPingLabel(ms: number | null | undefined, loss: number | null | undefined): string {
  if ((ms == null || !Number.isFinite(ms)) && (loss == null || !Number.isFinite(loss))) return "—";
  const a = ms == null || !Number.isFinite(ms) ? "—" : `${Math.round(ms)} мс`;
  const b = loss == null || !Number.isFinite(loss) ? "" : ` · ${Math.round(loss)}%`;
  return `${a}${b}`;
}

export function fmtRetr(pct: number | null | undefined): string {
  if (pct == null || !Number.isFinite(pct)) return "—";
  return pct >= 10 ? `${Math.round(pct)}%` : `${pct.toFixed(1)}%`;
}

/** Последнее непустое значение ряда — «сейчас» для метрик, которых нет в живом статусе. */
export function lastValue(values: Array<number | null | undefined>): number | null {
  for (let i = values.length - 1; i >= 0; i--) {
    const v = values[i];
    if (v != null && Number.isFinite(v)) return v;
  }
  return null;
}

/** Сумма непустых значений или null, если их нет вовсе (старый агент). */
export function sumValues(values: Array<number | null | undefined>): number | null {
  let seen = false;
  let total = 0;
  for (const v of values) {
    if (v == null || !Number.isFinite(v)) continue;
    seen = true;
    total += v;
  }
  return seen ? total : null;
}
