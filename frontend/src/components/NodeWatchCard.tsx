import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, type NodeOutage, type NodeWatchStatus } from "../api/client";
import { signalsText, spanText } from "../lib/nodeWatch";

const DAYS = 30;
// Следилка считает раз в полминуты — чаще минуты журнал перечитывать незачем.
const POLL_MS = 60_000;

function when(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleString([], {
    day: "2-digit",
    month: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

const CLOSED: Record<string, string> = {
  muted: "тревога снята: ноду исключили или выключили в панели",
  removed: "тревога снята: ноду удалили",
  stopped: "тревога снята: следилку выключили",
};

/**
 * Следилка за нодами: кто лежит сейчас и журнал «упала / поднялась».
 *
 * Настройки — на странице настроек, рядом со следилкой за подпиской; здесь
 * только то, что уже случилось.
 */
export function NodeWatchCard() {
  const [status, setStatus] = useState<NodeWatchStatus | null>(null);
  const [events, setEvents] = useState<NodeOutage[]>([]);

  const load = useCallback(async () => {
    try {
      const [s, e] = await Promise.all([api.nodeWatchStatus(), api.nodeWatchEvents(DAYS)]);
      setStatus(s);
      setEvents(e);
    } catch {
      /* оставляем, что было */
    }
  }, []);

  useEffect(() => {
    void load();
    const id = window.setInterval(() => void load(), POLL_MS);
    return () => window.clearInterval(id);
  }, [load]);

  if (!status) return null;
  const cfg = status.config;
  const open = events.filter((e) => !e.ended_at);
  const waiting = status.down.filter((d) => !d.alerted).length;

  return (
    <section className="rounded-[var(--radius)] border border-[var(--border)] bg-[var(--bg-elevated)] p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h2 className="text-sm font-semibold">Следилка за нодами</h2>
          <p className="mt-1 text-xs leading-relaxed text-[var(--muted)]">
            Нода молчит дольше {cfg.threshold_min} мин — тревога в Telegram, поднялась — ещё одно
            сообщение. Ниже журнал простоев за {DAYS} дней.
          </p>
        </div>
        <Link to="/settings" className="shrink-0 text-xs text-[var(--accent)] hover:underline">
          Настройки
        </Link>
      </div>

      <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs tabular-nums">
        {!cfg.enabled ? (
          <span className="text-[var(--muted)]">Выключена — за нодами никто не следит.</span>
        ) : open.length > 0 ? (
          <span className="text-[var(--danger)]">Сейчас лежит: {open.length}</span>
        ) : (
          <span className="text-[var(--success)]">Сейчас все ноды отвечают</span>
        )}
        {cfg.enabled && waiting > 0 && (
          <span className="text-[var(--warning)]">молчат, порог ещё не вышел: {waiting}</span>
        )}
        {cfg.enabled && !cfg.notify && (
          <span className="text-[var(--muted)]">уведомления выключены</span>
        )}
        {cfg.enabled && cfg.notify && !cfg.bot_ready && (
          <span className="text-[var(--warning)]">бот или чат не настроены — сообщений не будет</span>
        )}
      </div>

      {status.fleet && (
        <p className="mt-3 rounded-lg border border-[rgba(251,191,36,0.45)] bg-[rgba(251,191,36,0.10)] px-3 py-2 text-xs text-[var(--warning)]">
          Рагент не видит {status.fleet.down} из {status.fleet.total} нод с {when(status.fleet.since)}
          . Столько разом не падает — возможно, проблема на стороне рагента или его сети.
        </p>
      )}

      {events.length === 0 ? (
        <p className="mt-3 text-sm text-[var(--muted)]">За {DAYS} дней простоев не было.</p>
      ) : (
        <ul className="mt-3 max-h-96 space-y-1 overflow-auto">
          {events.map((e) => (
            <EventRow key={e.id} event={e} />
          ))}
        </ul>
      )}
    </section>
  );
}

function EventRow({ event: e }: { event: NodeOutage }) {
  const down = !e.ended_at;
  const lasted = spanText(
    (e.ended_at ? new Date(e.ended_at).getTime() : Date.now()) - new Date(e.started_at).getTime(),
  );
  const what =
    e.kind === "fleet"
      ? "рагент не видел парк — скорее всего, проблема была на его стороне"
      : signalsText(e.signals);
  const fleet = e.kind === "fleet";
  const closed = e.resolution ? CLOSED[e.resolution] : undefined;

  return (
    <li
      className={`rounded-lg border px-3 py-2 text-xs ${
        down
          ? "border-[rgba(240,113,120,0.45)] bg-[rgba(240,113,120,0.10)]"
          : "border-transparent bg-[var(--bg-row)]"
      }`}
    >
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-0.5">
        <span className="font-medium text-[var(--text)]">
          {fleet ? `Парк: ${e.name}` : e.name}
        </span>
        {e.host && <span className="font-mono text-[var(--muted)]">{e.host}</span>}
        {e.hosting && <span className="text-[var(--muted)]">{e.hosting}</span>}
        <span
          className={`ml-auto tabular-nums ${down ? "font-semibold text-[var(--danger)]" : "text-[var(--muted)]"}`}
        >
          {down ? `${fleet ? "не видит" : "лежит"} ${lasted}` : lasted}
        </span>
      </div>
      <div className="mt-0.5 flex flex-wrap gap-x-3 gap-y-0.5 tabular-nums text-[var(--muted)]">
        <span>
          {fleet ? "с" : "упала"} {when(e.started_at)}
          {e.ended_at && e.resolution === "recovered"
            ? ` · ${fleet ? "снова видит с" : "поднялась"} ${when(e.ended_at)}`
            : ""}
        </span>
        {what && <span>{what}</span>}
        {closed && e.ended_at && (
          <span>
            {closed}, {when(e.ended_at)}
          </span>
        )}
        {down && !e.notified && <span>сообщение в чат не ушло</span>}
      </div>
    </li>
  );
}
