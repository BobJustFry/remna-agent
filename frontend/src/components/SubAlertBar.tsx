import { Link } from "react-router-dom";
import type { SubwatchStatus } from "../api/client";

function downFor(since: string | null): string {
  if (!since) return "";
  const ms = Date.now() - new Date(since).getTime();
  if (Number.isNaN(ms) || ms < 0) return "";
  const min = Math.floor(ms / 60000);
  if (min < 60) return `${min} мин`;
  const h = Math.floor(min / 60);
  return h < 24 ? `${h} ч ${min % 60} мин` : `${Math.floor(h / 24)} сут ${h % 24} ч`;
}

/**
 * Полоса поверх всего приложения: подписка не отдаётся.
 *
 * Закрыть её нельзя намеренно. Пока подписка лежит, новые клиенты не могут
 * подключиться — а старые работают, и без этой полосы поломку легко не
 * заметить сутками.
 */
export function SubAlertBar({ status }: { status: SubwatchStatus | null }) {
  if (!status || status.ok !== false) return null;

  const reason = status.last?.error ?? "подписка не отвечает";
  const elapsed = downFor(status.since);

  return (
    <div
      role="alert"
      className="flex shrink-0 flex-wrap items-center gap-x-3 gap-y-1 border-b border-[rgba(240,113,120,0.5)] bg-[rgba(240,113,120,0.16)] px-3 py-2 text-sm sm:px-4"
    >
      <span className="font-semibold text-[var(--danger)]">Подписка не отдаётся</span>
      <span className="min-w-0 text-[var(--text)]">{reason}</span>
      {elapsed && <span className="text-[var(--muted)]">уже {elapsed}</span>}
      <Link
        to="/settings"
        className="ml-auto shrink-0 rounded border border-[rgba(240,113,120,0.5)] px-2 py-0.5 text-xs text-[var(--danger)] hover:bg-[rgba(240,113,120,0.12)]"
      >
        Разобраться
      </Link>
    </div>
  );
}
