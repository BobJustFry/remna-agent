import type { NodeDown, NodeSignals, NodeViaPanel, NodeWatchStatus } from "../api/client";

/** Пометка у ноды: лежит (или молчит) — либо жива, но видна только через панель. */
export type NodeMark =
  | ({ kind: "down" } & NodeDown)
  | ({ kind: "via_panel" } & NodeViaPanel);

export type NodeDownMap = Record<string, NodeMark>;

export function downMap(status: NodeWatchStatus | null | undefined): NodeDownMap {
  const out: NodeDownMap = {};
  for (const v of status?.via_panel ?? []) out[v.node_id] = { kind: "via_panel", ...v };
  // Лежащая важнее: если нода в обоих списках, остаётся красная пометка.
  for (const d of status?.down ?? []) out[d.node_id] = { kind: "down", ...d };
  return out;
}

/** «03:12», а если это было не сегодня — «29.09 03:12». Время — местное, браузера. */
export function clockTime(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  const time = d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  if (d.toDateString() === new Date().toDateString()) return time;
  return `${d.toLocaleDateString([], { day: "2-digit", month: "2-digit" })} ${time}`;
}

export function spanText(ms: number): string {
  if (Number.isNaN(ms) || ms < 0) return "";
  const min = Math.max(1, Math.floor(ms / 60000));
  if (min < 60) return `${min} мин`;
  const h = Math.floor(min / 60);
  if (h < 24) return min % 60 ? `${h} ч ${min % 60} мин` : `${h} ч`;
  return h % 24 ? `${Math.floor(h / 24)} сут ${h % 24} ч` : `${Math.floor(h / 24)} сут`;
}

/** Что именно молчит — теми же словами, что в сообщении в Телеграм. */
export function signalsText(sig: NodeSignals | undefined): string {
  if (!sig) return "";
  const parts: string[] = [];
  if (sig.agent === null) parts.push("агента нет");
  else if (sig.agent_error === "token") parts.push("агент не принимает токен");
  else if (sig.agent === false) parts.push("агент молчит");
  if (sig.ssh === true) parts.push("SSH отвечает");
  else if (sig.ssh === false) parts.push("SSH молчит");
  if (sig.panel === true) parts.push("панель видит");
  else if (sig.panel === false) parts.push("панель не видит");
  return parts.join(" · ");
}
