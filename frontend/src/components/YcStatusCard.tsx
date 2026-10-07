import { useState } from "react";
import { Link } from "react-router-dom";
import { api, type YcwatchStatus } from "../api/client";

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

function money(v: number, currency: string | null | undefined): string {
  const cur = (currency ?? "RUB") === "RUB" ? "₽" : currency;
  return `${v.toLocaleString("ru-RU", { maximumFractionDigits: 0 })} ${cur}`;
}

/**
 * Облако страницы подписки: баланс Yandex Cloud и статус ВМ.
 *
 * Рядом с подпиской, потому что это её причина номер один: 01.10 баланс ушёл
 * в минус, Яндекс остановил ВМ, и подписка легла. Здесь видно заранее.
 */
export function YcStatusCard({
  status,
  onRefreshed,
}: {
  status: YcwatchStatus | null;
  onRefreshed?: (s: YcwatchStatus) => void;
}) {
  const [busy, setBusy] = useState(false);

  if (!status) return null;

  if (!status.config.enabled || (!status.config.sa_key_set && !status.config.sa_key_broken)) {
    return (
      <Link
        to="/settings"
        className="inline-flex items-center gap-2 rounded-[var(--radius)] border border-dashed border-[var(--border)] px-3 py-1.5 text-xs text-[var(--muted)] hover:text-[var(--text)]"
      >
        Следилка за Yandex Cloud {status.config.enabled ? "без ключа" : "выключена"} — настроить
      </Link>
    );
  }

  const level = status.level;
  const last = status.last;
  const ok = status.last_ok;
  const tone =
    level === "critical" ? "danger" : level === "low" || level === "blind" ? "warning" : level === "ok" ? "success" : "muted";
  const title =
    level === "critical"
      ? "Облако подписки: авария"
      : level === "low"
        ? "Облако подписки: мало денег"
        : level === "blind"
          ? "Облако подписки не отвечает"
          : "Облако подписки";

  async function checkNow() {
    setBusy(true);
    try {
      onRefreshed?.(await api.ycwatchCheck());
    } catch {
      /* причину покажет сама карточка при следующем опросе */
    } finally {
      setBusy(false);
    }
  }

  // Последний опрос не удался — всё, что ниже, взято из прошлого удачного и может
  // быть многодневной давности. Показываем это прямо, иначе рядом с красной точкой
  // висит бодрый баланс, которого давно нет.
  const stale = !!last && !last.ok;
  const balance = ok?.balance;
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5 rounded-[var(--radius)] border border-[var(--border)] bg-[var(--bg-elevated)] px-3 py-2">
      <span
        aria-hidden
        className={`h-2.5 w-2.5 shrink-0 rounded-full ${
          tone === "danger"
            ? "bg-[var(--danger)]"
            : tone === "warning"
              ? "bg-[var(--warning)]"
              : tone === "success"
                ? "bg-[var(--success)]"
                : "bg-[var(--muted)]"
        }`}
      />
      <span className="text-sm font-semibold" title="Yandex Cloud: баланс и ВМ страницы подписки">
        {title}
      </span>

      {balance !== null && balance !== undefined && (
        <span
          className={`text-xs tabular-nums ${
            stale
              ? "text-[var(--muted)]"
              : balance <= 0
                ? "text-[var(--danger)]"
                : balance < status.config.warn_rub
                  ? "text-[var(--warning)]"
                  : "text-[var(--muted)]"
          }`}
          title={
            stale
              ? `данные последнего удачного опроса, ${ago(ok?.checked_at)}; сейчас облако не отвечает`
              : `порог предупреждения ${money(status.config.warn_rub, ok?.currency)}`
          }
        >
          {stale ? "было " : ""}баланс {money(balance, ok?.currency)}
          {stale ? ` (${ago(ok?.checked_at)})` : ""}
        </span>
      )}

      {!stale && status.days_left !== null && status.burn_per_day !== null && (
        <span
          className={`text-xs tabular-nums ${status.days_left <= 3 ? "text-[var(--warning)]" : "text-[var(--muted)]"}`}
          title={`расход ~${money(status.burn_per_day, ok?.currency)}/сут по последним суткам`}
        >
          ~{Math.floor(status.days_left)} сут
        </span>
      )}

      {ok?.vms.map((vm) => (
        <span
          key={vm.id ?? vm.name ?? "vm"}
          className={`text-xs tabular-nums ${
            stale || vm.status === "RUNNING" ? "text-[var(--muted)]" : "text-[var(--danger)]"
          }`}
          title={`${vm.name ?? vm.id ?? ""}${vm.ip ? ` · ${vm.ip}` : ""}${
            stale ? ` · состояние на ${ago(ok?.checked_at)}` : ""
          }`}
        >
          ВМ {vm.status}
        </span>
      ))}
      {ok && ok.vms.length === 0 && (
        <span className={`text-xs ${stale ? "text-[var(--muted)]" : "text-[var(--danger)]"}`}>
          ВМ не найдена
        </span>
      )}

      {last && (
        <span className="text-xs text-[var(--muted)] tabular-nums" title={last.error ?? undefined}>
          {last.ok ? "" : `ошибка: ${last.error ?? "?"} · `}
          {ago(last.checked_at)}
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
