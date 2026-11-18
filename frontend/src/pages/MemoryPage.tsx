import { useState } from "react";
import { Link } from "react-router-dom";
import { api, type MemWatchNode, type MemWatchUpdate } from "../api/client";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { MemRestartButton } from "../components/MemBadge";
import { useMemWatch } from "../hooks/useMemWatch";

const KIND_LABEL: Record<string, string> = {
  alarm: "тревога",
  alarm_critical: "критично",
  alarm_remind: "напоминание",
  alarm_clear: "снята",
  relapse: "повторный разгон",
  blocked: "отказ защиты",
  restart_plan: "план (сухой прогон)",
  restart_start: "перезапуск ДО",
  restart_ok: "перезапуск ПОСЛЕ",
  restart_failed: "перезапуск не подтвердился",
  restart_error: "ошибка панели",
  manual_restart: "ручной перезапуск",
  reset_block: "сброс блокировки",
  mode: "режим",
};

const ACTION_LABEL: Record<string, string> = {
  none: "—",
  alarm_only: "только тревога",
  blocked: "заблокировано",
  wait: "ждёт",
  wait_window: "ждёт тихое окно",
  restart: "перезапустит",
  restarting: "перезапускается",
};

function fmtUptime(s: number | null): string {
  if (s == null) return "—";
  const h = Math.floor(s / 3600);
  return h >= 48 ? `${Math.floor(h / 24)} д` : `${h} ч ${Math.floor((s % 3600) / 60)} м`;
}

function fmtTs(ts: string, offset: number): string {
  const d = new Date(new Date(ts).getTime() + offset * 3600_000);
  const p = (n: number) => String(n).padStart(2, "0");
  return `${p(d.getUTCDate())}.${p(d.getUTCMonth() + 1)} ${p(d.getUTCHours())}:${p(d.getUTCMinutes())}`;
}

function state(n: MemWatchNode): { label: string; cls: string } {
  if (n.alarm && n.critical) return { label: "КРИТИЧНО", cls: "bg-[var(--danger)] text-white" };
  if (n.alarm) return { label: "тревога", cls: "bg-[var(--warning)] text-black" };
  return { label: "норма", cls: "bg-[var(--success)] text-black" };
}

/** Раздел «Память»: ядра xray, раздувшиеся по памяти, и осторожный автоперезапуск. */
export function MemoryPage() {
  const { status, refresh, setStatus } = useMemWatch();
  const [confirmLive, setConfirmLive] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const cfg = status?.config;

  async function save(payload: MemWatchUpdate) {
    setBusy(true);
    setErr(null);
    try {
      setStatus(await api.memWatchUpdate(payload));
    } catch (e) {
      setErr(e instanceof Error ? e.message : "не удалось сохранить");
    } finally {
      setBusy(false);
    }
  }

  if (!status || !cfg) {
    return <div className="text-sm text-[var(--muted)]">Загрузка…</div>;
  }

  const nodes = [...status.nodes].sort(
    (a, b) =>
      Number(b.critical) - Number(a.critical) ||
      Number(b.alarm) - Number(a.alarm) ||
      (b.rss_mb ?? 0) - (a.rss_mb ?? 0),
  );
  const noRss = status.nodes.filter((n) => n.rss_mb == null && !n.excluded).length;

  return (
    <div className="h-full space-y-4 overflow-auto px-3 py-3 sm:px-6 sm:py-4">
      <header>
        <h1 className="text-lg font-semibold">Память ядер</h1>
        <p className="mt-1 max-w-3xl text-xs leading-relaxed text-[var(--muted)]">
          Следит за RSS процесса rw-core, свободной памятью и свопом. Тревога поднимается сразу и
          всегда: пометка у ноды, запись в журнал, Телеграм (когда бот настроен). Автоперезапуск ядра
          идёт только под защитами: пауза на ноду, лимиты на ноду и на парк, тихое окно, строго по
          одному, контроль после перезапуска. Не помогло — тревога и блокировка, а не повторы.
        </p>
      </header>

      <section
        className={`rounded-lg border p-3 ${
          cfg.dry_run
            ? "border-[var(--warning)] bg-[rgba(255,196,60,0.08)]"
            : "border-[rgba(240,113,120,0.45)] bg-[rgba(240,113,120,0.06)]"
        }`}
      >
        <div className="flex flex-wrap items-center gap-3">
          <div className="min-w-0 flex-1">
            <div className="text-sm font-semibold">
              {!cfg.enabled
                ? "Памятьсторож выключен"
                : cfg.dry_run
                  ? "СУХОЙ ПРОГОН: считаю и показываю, ничего не перезапускаю"
                  : cfg.auto_restart
                    ? "БОЕВОЙ РЕЖИМ: автоперезапуск включён"
                    : "Только тревоги: автоперезапуск выключен"}
            </div>
            <div className="mt-0.5 text-xs text-[var(--muted)]">
              Телеграм: {cfg.bot_ready ? "бот настроен" : "бот не настроен — тревоги видны здесь и в журнале"}
            </div>
          </div>
          {cfg.dry_run ? (
            <button
              type="button"
              disabled={busy}
              onClick={() => setConfirmLive(true)}
              className="rounded-lg border border-[rgba(240,113,120,0.45)] bg-[rgba(240,113,120,0.12)] px-3 py-1.5 text-sm font-semibold text-[var(--danger)]"
            >
              Включить боевой режим
            </button>
          ) : (
            <button
              type="button"
              disabled={busy}
              onClick={() => void save({ dry_run: true })}
              className="rounded-lg border border-[var(--border)] px-3 py-1.5 text-sm"
            >
              Вернуть сухой прогон
            </button>
          )}
        </div>
        <div className="mt-2 flex flex-wrap gap-4 text-xs">
          {(
            [
              ["enabled", "Следить"],
              ["auto_restart", "Автоперезапуск (иначе только тревоги)"],
              ["notify", "Телеграм"],
            ] as const
          ).map(([k, label]) => (
            <label key={k} className="flex items-center gap-1.5">
              <input
                type="checkbox"
                checked={cfg[k]}
                disabled={busy}
                onChange={(e) => void save({ [k]: e.target.checked })}
              />
              {label}
            </label>
          ))}
        </div>
        {err && <div className="mt-2 text-xs text-[var(--danger)]">{err}</div>}
      </section>

      {noRss > 0 && (
        <p className="text-xs text-[var(--muted)]">
          У {noRss} нод RSS ядра пока не приходит (агент старше 0.1.23 или ядра нет): по ним работает тревога по
          свободной памяти и свопу, а RSS рагент меряет по SSH только когда нода уже в тревоге. Автоперезапуск без
          известного RSS не выполняется.
        </p>
      )}

      <section className="overflow-x-auto rounded-lg border border-[var(--border)]">
        <table className="w-full min-w-[820px] text-xs">
          <thead className="text-left text-[var(--muted)]">
            <tr>
              {["Нода", "Состояние", "rw-core / порог", "Свободно", "Своп", "Аптайм ядра", "Решение", ""].map((h) => (
                <th key={h} className="px-2 py-1.5 font-medium">
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {nodes.map((n) => {
              const st = state(n);
              return (
                <tr key={n.node_id} className="border-t border-[var(--border)] align-top">
                  <td className="px-2 py-1.5 font-medium">
                    {n.name}
                    {n.excluded && <span className="ml-1 text-[10px] text-[var(--muted)]">(исключена)</span>}
                  </td>
                  <td className="px-2 py-1.5">
                    <span className={`rounded px-1.5 py-0.5 text-[10px] font-bold ${st.cls}`}>{st.label}</span>
                    {n.reasons.length > 0 && (
                      <div className="mt-0.5 max-w-[240px] text-[10px] text-[var(--muted)]">{n.reasons.join("; ")}</div>
                    )}
                  </td>
                  <td className="px-2 py-1.5 tabular-nums">
                    {n.rss_mb != null ? `${Math.round(n.rss_mb)}` : "—"}
                    {n.rss_limit_mb != null && ` / ${Math.round(n.rss_limit_mb)}`} МБ
                    {n.rss_source === "ssh" && <span className="ml-1 text-[10px] text-[var(--muted)]">ssh</span>}
                  </td>
                  <td className="px-2 py-1.5 tabular-nums">{n.avail_pct != null ? `${Math.round(n.avail_pct)}%` : "—"}</td>
                  <td className="px-2 py-1.5 tabular-nums">{n.swap_percent != null ? `${Math.round(n.swap_percent)}%` : "—"}</td>
                  <td className="px-2 py-1.5 tabular-nums">{fmtUptime(n.core_uptime_s)}</td>
                  <td className="px-2 py-1.5">
                    <div className="font-medium">{n.pending ? "проверка после перезапуска" : ACTION_LABEL[n.action] ?? n.action}</div>
                    {n.action_reason && <div className="max-w-[260px] text-[10px] text-[var(--muted)]">{n.action_reason}</div>}
                  </td>
                  <td className="px-2 py-1.5">
                    <div className="flex flex-col items-start gap-1">
                      <MemRestartButton nodeId={n.node_id} nodeName={n.name} rssMb={n.rss_mb} onDone={() => void refresh()} />
                      {n.blocked && (
                        <button
                          type="button"
                          className="rounded border border-[var(--border)] px-2 py-1 text-[11px]"
                          onClick={() => void api.memWatchResetBlock(n.node_id).then(() => refresh())}
                        >
                          Снять блокировку
                        </button>
                      )}
                    </div>
                  </td>
                </tr>
              );
            })}
            {nodes.length === 0 && (
              <tr>
                <td colSpan={8} className="px-2 py-6 text-center text-[var(--muted)]">
                  Данных пока нет — первый опрос ещё не прошёл.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </section>

      <p className="text-xs text-[var(--muted)]">
        <Link
          to="/settings"
          className="text-[var(--accent)] hover:underline"
          onClick={() => {
            try {
              localStorage.setItem("remna-agent.settings.tab", "memory");
            } catch {
              /* не страшно */
            }
          }}
        >
          Пороги и защиты — в Настройках
        </Link>
      </p>

      <section className="rounded-lg border border-[var(--border)] p-3">
        <h2 className="text-sm font-semibold">Журнал</h2>
        <ul className="mt-2 space-y-1 text-xs">
          {status.log.map((r, i) => (
            <li key={`${r.ts}-${i}`} className="flex gap-2">
              <span className="shrink-0 tabular-nums text-[var(--muted)]">{fmtTs(r.ts, cfg.utc_offset)}</span>
              <span className="shrink-0 font-semibold">{r.name ?? "—"}</span>
              <span className="shrink-0 text-[var(--muted)]">[{KIND_LABEL[r.kind] ?? r.kind}]</span>
              <span className="min-w-0">
                {r.dry_run && "🧪 "}
                {r.text}
                {r.notified && " ✉"}
              </span>
            </li>
          ))}
          {status.log.length === 0 && <li className="text-[var(--muted)]">Пока тихо.</li>}
        </ul>
      </section>

      <ConfirmDialog
        open={confirmLive}
        title="Включить боевой режим?"
        message="Рагент начнёт сам перезапускать ядро xray на нодах в тревоге — только под защитами (пауза, лимиты, тихое окно, по одному). Каждое действие будет в журнале. Отключается тут же кнопкой «Вернуть сухой прогон»."
        confirmLabel="Включить"
        danger
        onConfirm={() => {
          setConfirmLive(false);
          void save({ dry_run: false });
        }}
        onCancel={() => setConfirmLive(false)}
      />
    </div>
  );
}
