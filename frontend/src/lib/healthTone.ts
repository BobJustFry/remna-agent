/**
 * Цвета для здоровья ноды: ретрансмиты TCP, потери и пинг до России, своп.
 * Пороги те же по духу, что у онлайна и канала: зелёный — норма, жёлтый —
 * присмотреться, красный — клиенты уже чувствуют.
 */

import type { MetricPoint } from "../types";

const BASE = "font-bold tabular-nums";

/** Подсказка к доле ретрансмитов, посчитанной по слишком малому объёму данных. */
export const RETR_LOW_VOLUME_HINT = "мало трафика";

/**
 * Доля ретрансмитов TCP, %: до 2 — норма для прокси, 2–5 — сеть сыплет, от 5 — плохо.
 * При малом объёме данных цифра не про сеть, а про пару случайных повторов, —
 * всегда серая, чтобы тихая нода не выглядела аварийной.
 */
export function retrToneClass(pct: number | null | undefined, lowVolume?: boolean | null): string {
  if (lowVolume) return `${BASE} text-[var(--muted)]`;
  if (pct == null || !Number.isFinite(pct)) return `${BASE} text-[var(--muted)]`;
  if (pct < 2) return `${BASE} text-[var(--success)]`;
  if (pct < 5) return `${BASE} text-[var(--warning)]`;
  return `${BASE} text-[var(--danger)]`;
}

export type RetrValue = { pct: number | null; low: boolean };

/**
 * Последний шаг истории с долей ретрансмитов — «сейчас» для плитки. Шаг без
 * доли, но с пометкой «мало данных» (сегментов с данными ноль) тоже считается:
 * иначе плитка показала бы старую красную цифру вместо честного «мало трафика».
 */
export function lastRetr(points: MetricPoint[]): RetrValue {
  for (let i = points.length - 1; i >= 0; i--) {
    const p = points[i];
    const pct = p.retr_pct;
    const hasPct = pct != null && Number.isFinite(pct);
    if (hasPct || p.retr_low_volume === true) {
      return { pct: hasPct ? (pct as number) : null, low: p.retr_low_volume === true };
    }
  }
  return { pct: null, low: false };
}

/**
 * Доля ретрансмитов за весь период — от сумм, а не среднее процентов. Есть
 * точки с новыми счётчиками (агент ≥ 0.1.22) — считаем по ним:
 * (retrans − syn) / сегменты с данными; иначе старая retrans / out. «Мало
 * данных» — когда ни одна точка периода не набрала порог объёма.
 */
export function periodRetr(points: MetricPoint[]): RetrValue {
  const withData = points.filter((p) => p.tcp_orig_data_sent != null);
  if (withData.length > 0) {
    const num = sumValues(withData.map((p) => p.tcp_retrans_no_syn));
    const den = sumValues(withData.map((p) => p.tcp_orig_data_sent));
    const pct = den && num != null ? Math.min(100, (Math.max(0, num) / den) * 100) : null;
    return { pct, low: withData.every((p) => p.retr_low_volume === true) };
  }
  const out = sumValues(points.map((p) => p.tcp_out_segs));
  const retr = sumValues(points.map((p) => p.tcp_retrans_segs));
  return { pct: out && retr != null ? (retr / out) * 100 : null, low: false };
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
