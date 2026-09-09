import { useState } from "react";
import { Link } from "react-router-dom";
import { api, type SubwatchStatus } from "../api/client";

function ago(iso: string | null | undefined): string {
  if (!iso) return "—";
  const ms = Date.now() - new Date(iso).getTime();
  if (Number.isNaN(ms)) return "—";
  const min = Math.floor(ms / 60000);
  if (min < 1) return "только что";
  if (min < 60) return `${min} мин назад`;
  const h = Math.floor(min / 60);
  return h < 24 ? `${h} ч назад` : `${Math.floor(h / 24)} сут назад`;
}

/**
 * Состояние подписки на дашборде.
 *
 * Отдельно от прочей сводки: остальные плитки про наш парк, а эта — про то,
 * может ли новый человек вообще получить конфиг. Это единственное, что ломается
 * без единого признака: работающие клиенты продолжают работать.
 */
export function SubStatusCard({
  status,
  onRefreshed,
}: {
  status: SubwatchStatus | null;
  onRefreshed?: (s: SubwatchStatus) => void;
}) {
  const [busy, setBusy] = useState(false);

  if (!status) return null;

  if (!status.config.enabled || !status.config.url) {
    return (
      <Link
        to="/settings"
        className="inline-flex items-center gap-2 rounded-[var(--radius)] border border-dashed border-[var(--border)] px-3 py-1.5 text-xs text-[var(--muted)] hover:text-[var(--text)]"
      >
        Следилка за подпиской выключена — настроить
      </Link>
    );
  }

  const ok = status.ok;
  const last = status.last;
  const tone = ok === false ? "danger" : ok === true ? "success" : "muted";

  async function checkNow() {
    setBusy(true);
    try {
      const s = await api.subwatchCheck();
      onRefreshed?.(s);
    } catch {
      /* причину покажет сама карточка при следующем опросе */
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5 rounded-[var(--radius)] border border-[var(--border)] bg-[var(--bg-elevated)] px-3 py-2">
      <span
        aria-hidden
        className={`h-2.5 w-2.5 shrink-0 rounded-full ${
          tone === "danger"
            ? "bg-[var(--danger)]"
            : tone === "success"
              ? "bg-[var(--success)]"
              : "bg-[var(--muted)]"
        }`}
      />
      <span className="text-sm font-semibold">
        {ok === false ? "Подписка не отдаётся" : ok === true ? "Подписка отдаётся" : "Подписка"}
      </span>

      {last && (
        <span className="text-xs text-[var(--muted)] tabular-nums">
          {last.ok ? `${last.hosts} конфигов · ${last.duration_ms} мс` : last.error}
          {" · "}
          {ago(last.checked_at)}
        </span>
      )}

      {status.history.length > 1 && (
        <span className="flex items-end gap-[2px]" title="последние проверки, свежие справа">
          {[...status.history].reverse().slice(-24).map((h, i) => (
            <span
              key={`${h.checked_at}-${i}`}
              className={`inline-block h-3 w-[3px] rounded-sm ${
                h.ok ? "bg-[var(--success)]" : "bg-[var(--danger)]"
              }`}
            />
          ))}
        </span>
      )}

      {status.uptime_24h !== null && (
        <span className="text-xs text-[var(--muted)] tabular-nums">
          за сутки {status.uptime_24h}%
        </span>
      )}

      {last?.cert_days !== null && last?.cert_days !== undefined && (
        <span
          className={`text-xs tabular-nums ${
            last.cert_days <= 7
              ? "text-[var(--danger)]"
              : last.cert_days <= 21
                ? "text-[var(--warning)]"
                : "text-[var(--muted)]"
          }`}
        >
          серт: {last.cert_days} дн
        </span>
      )}

      <button
        type="button"
        disabled={busy}
        onClick={() => void checkNow()}
        className="ml-auto shrink-0 rounded border border-[var(--border)] px-2 py-0.5 text-xs text-[var(--muted)] hover:text-[var(--accent)] disabled:opacity-50"
      >
        {busy ? "Проверяю…" : "Проверить"}
      </button>
    </div>
  );
}
