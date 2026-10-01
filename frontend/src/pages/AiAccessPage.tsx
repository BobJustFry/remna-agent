import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  api,
  type AiAccessStatus,
  type AiCell,
  type AiChange,
  type AiHostHistory,
  type AiHostRow,
  type AiHostState,
  type AiRun,
  type AiService,
  type AiVerdict,
} from "../api/client";
import { CountryFlag } from "../components/CountryFlag";

const INPUT =
  "w-full rounded-lg border border-[var(--border)] bg-[var(--bg)] px-3 py-2 text-sm text-[var(--text)] outline-none focus:border-[var(--accent)]";
const BTN =
  "rounded-lg border border-[var(--border)] px-3 py-1.5 text-sm text-[var(--text)] hover:bg-[var(--bg-row)] disabled:opacity-50";

// Таблица с раздельными границами: со схлопнутыми липкая шапка теряет линии.
const TH = "sticky top-0 bg-[var(--bg-elevated)] py-2 font-medium shadow-[0_1px_0_var(--border)]";
const ROW_LINE = "border-t border-[var(--border)]";
// Разделитель между группами колонок: ИИ отдельно, приложения отдельно.
const GROUP_LINE = "border-l-2 border-l-[var(--border)]";

// Пока идёт прогон — подтягиваем прогресс часто, в покое хватит раза в минуту.
const POLL_RUNNING_MS = 5_000;
const POLL_IDLE_MS = 60_000;

const VERDICT: Record<AiVerdict, { label: string; cell: string; dot: string }> = {
  ok: {
    label: "ok",
    cell: "bg-[rgba(61,214,140,0.16)] text-[var(--success)] border-[rgba(61,214,140,0.35)]",
    dot: "bg-[var(--success)]",
  },
  blocked: {
    label: "отказ",
    cell: "bg-[rgba(240,113,120,0.16)] text-[var(--danger)] border-[rgba(240,113,120,0.4)]",
    dot: "bg-[var(--danger)]",
  },
  challenge: {
    label: "челлендж",
    cell: "bg-[rgba(230,162,60,0.16)] text-[var(--warning)] border-[rgba(230,162,60,0.4)]",
    dot: "bg-[var(--warning)]",
  },
  timeout: {
    label: "таймаут",
    cell: "bg-[rgba(139,154,165,0.14)] text-[var(--muted)] border-[rgba(139,154,165,0.3)]",
    dot: "bg-[var(--muted)]",
  },
  error: {
    label: "ошибка",
    cell: "bg-transparent text-[var(--muted)] border-dashed border-[rgba(139,154,165,0.45)]",
    dot: "bg-[#4b5a64]",
  },
};

function verdictStyle(v: string) {
  return VERDICT[(v as AiVerdict) in VERDICT ? (v as AiVerdict) : "error"];
}

function fmtTime(iso: string | null | undefined) {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "—" : d.toLocaleString();
}

function ago(iso: string | null | undefined): string {
  if (!iso) return "";
  const ms = Date.now() - new Date(iso).getTime();
  if (Number.isNaN(ms)) return "";
  const min = Math.floor(ms / 60000);
  if (min < 1) return "только что";
  if (min < 60) return `${min} мин назад`;
  const h = Math.floor(min / 60);
  return h < 24 ? `${h} ч назад` : `${Math.floor(h / 24)} сут назад`;
}

function fmtDuration(from: string, to: string | null) {
  if (!to) return "";
  const s = Math.round((new Date(to).getTime() - new Date(from).getTime()) / 1000);
  if (!Number.isFinite(s) || s < 0) return "";
  return s < 90 ? `${s} с` : `${Math.round(s / 60)} мин`;
}

/** Колонки, с которых начинается новая группа сервисов: перед ними разделитель. */
function groupStarts(services: AiService[]): Set<string> {
  const out = new Set<string>();
  services.forEach((s, i) => {
    if (i > 0 && services[i - 1].group !== s.group) out.add(s.key);
  });
  return out;
}

/** Хост ещё в работе: ждёт очереди, проверяется или выходит из проверки. */
function isBusy(state: AiHostState | null): boolean {
  return state === "queued" || state === "running" || state === "cancelling";
}

/**
 * Доступность приложений через каждый хост подписки: нейросети и TikTok.
 *
 * Значки 🧠 и 📱 в имени хоста — обещание клиенту. Про ИИ оно держится на
 * WARP и правилах маршрутизации ноды, про TikTok — на том, что TikTok не
 * пометил адрес выхода. Ломается всё молча: клиент просто уходит с хоста.
 * Здесь видно, что обещание выполняется, и что поменялось с прошлого прогона.
 *
 * URL страницы и API остались прежними (/ai-access): на них завязаны скрипты.
 *
 * Проверить можно весь парк, отмеченные хосты или один. Кто в очереди и кто
 * проверяется, знает сервер: страница только рисует то, что пришло в статусе,
 * поэтому спиннеры и кнопки отмены переживают обновление окна.
 */
export function AiAccessPage() {
  const [status, setStatus] = useState<AiAccessStatus | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [filter, setFilter] = useState<"all" | "problems" | "changed">("all");
  const [open, setOpen] = useState<string | null>(null);
  // Отметки — дело этой вкладки; сам прогон и его очередь живут на сервере.
  const [selected, setSelected] = useState<Set<string>>(new Set());
  // Хосты, по которым запрос ещё в пути: чтобы кнопку не нажали дважды.
  const [sending, setSending] = useState<Set<string>>(new Set());
  const timer = useRef<number | null>(null);

  const load = useCallback(async () => {
    try {
      const s = await api.aiAccessStatus();
      setStatus(s);
      return s;
    } catch (err) {
      setMsg(err instanceof Error ? err.message : "Не удалось получить состояние");
      return null;
    }
  }, []);

  const running = Boolean(status?.running);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    if (timer.current) window.clearInterval(timer.current);
    timer.current = window.setInterval(() => void load(), running ? POLL_RUNNING_MS : POLL_IDLE_MS);
    return () => {
      if (timer.current) window.clearInterval(timer.current);
      timer.current = null;
    };
  }, [running, load]);

  /** Запрос, который возвращает свежий статус: запуск или отмена. */
  async function send(call: () => Promise<AiAccessStatus>, keys: string[], fail: string) {
    setMsg(null);
    if (keys.length) setSending((prev) => new Set([...prev, ...keys]));
    try {
      setStatus(await call());
    } catch (err) {
      setMsg(err instanceof Error ? err.message : fail);
    } finally {
      if (keys.length)
        setSending((prev) => {
          const next = new Set(prev);
          for (const k of keys) next.delete(k);
          return next;
        });
    }
  }

  async function runAll() {
    setBusy(true);
    await send(() => api.aiAccessRun(), [], "Не запустилось");
    setBusy(false);
  }

  async function cancelAll() {
    setBusy(true);
    await send(() => api.aiAccessCancel(), [], "Не отменилось");
    setBusy(false);
  }

  const runHosts = (keys: string[]) => send(() => api.aiAccessRun(keys), keys, "Не запустилось");
  const cancelHosts = (keys: string[]) => send(() => api.aiAccessCancel(keys), keys, "Не отменилось");

  function toggle(key: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (!next.delete(key)) next.add(key);
      return next;
    });
  }

  const rows = useMemo(() => {
    const all = status?.rows ?? [];
    if (filter === "changed") return all.filter((r) => r.changed || r.is_new);
    if (filter === "problems")
      return all.filter((r) => Object.values(r.results).some((c) => c.verdict !== "ok"));
    return all;
  }, [status, filter]);

  const counts = useMemo(() => {
    const out: Record<string, Record<string, number>> = {};
    for (const r of status?.rows ?? []) {
      for (const [k, c] of Object.entries(r.results)) {
        out[k] ??= {};
        out[k][c.verdict] = (out[k][c.verdict] ?? 0) + 1;
      }
    }
    return out;
  }, [status]);

  if (!status) {
    return (
      <div className="p-4 text-sm text-[var(--muted)] sm:p-6">{msg ?? "Загрузка…"}</div>
    );
  }

  const { shown, last, services, last_partial: lastPartial } = status;
  const starts = groupStarts(services);
  const grouped = new Set(services.map((s) => s.group)).size > 1;
  const run = status.running;
  const failed = last && (last.status === "failed" || last.status === "aborted") ? last : null;
  const changedCount = status.rows.filter((r) => r.changed).length;
  const canRun = status.config.sub_configured;

  // Отмеченные, которые ещё есть в матрице и не заняты прогоном.
  const picked = status.rows.filter((r) => selected.has(r.host_key) && !isBusy(r.state));
  const pickable = rows.filter((r) => !isBusy(r.state));
  const allPicked = pickable.length > 0 && pickable.every((r) => selected.has(r.host_key));

  // Последняя выборочная проверка что-то не смогла — говорим, пока её не
  // перекрыл следующий прогон.
  const partialNote =
    lastPartial && !run && (!last || new Date(lastPartial.started_at) > new Date(last.started_at))
      ? lastPartial
      : null;
  const partialMissing = partialNote ? partialNote.hosts.filter((h) => h.state === "missing") : [];
  const partialFailed =
    partialNote && partialMissing.length === 0 &&
    (partialNote.status === "failed" || partialNote.status === "aborted")
      ? partialNote
      : null;

  const fullActive = run?.scope === "full";
  const fullLabel = fullActive
    ? "Идёт прогон…"
    : status.pending_full
      ? "Полный — в очереди"
      : busy
        ? "Запускаю…"
        : "Проверить всё";
  const fullTitle = fullActive
    ? "Полный прогон уже идёт"
    : status.pending_full
      ? "Полный прогон начнётся, как только закончится выборочная проверка"
      : run
        ? "Полный прогон встанет следом за выборочной проверкой"
        : "Проверить все хосты подписки";

  return (
    <div className="flex h-full min-h-0 flex-col overflow-hidden">
      <header className="border-b border-[var(--border)] px-4 py-3 sm:px-6 sm:py-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="min-w-0">
            <h1 className="hidden text-xl font-semibold tracking-tight lg:block">Доступность приложений</h1>
            <p className="text-sm text-[var(--muted)] lg:mt-0.5">
              Открываются ли ChatGPT, Claude, Gemini и TikTok через каждый хост подписки.
            </p>
          </div>
          <div className="flex shrink-0 flex-wrap items-center gap-2">
            {run && (
              <button
                type="button"
                disabled={busy || run.stopping}
                onClick={() => void cancelAll()}
                title="Остановить прогон: проверенные хосты сохранятся, остальные останутся с прежним результатом"
                className="rounded-lg border border-[rgba(240,113,120,0.45)] px-3 py-1.5 text-sm text-[var(--danger)] hover:bg-[rgba(240,113,120,0.10)] disabled:opacity-50"
              >
                {run.stopping ? "Отменяется…" : "Отменить прогон"}
              </button>
            )}
            <button
              type="button"
              disabled={busy || fullActive || Boolean(status.pending_full) || !canRun}
              onClick={() => void runAll()}
              title={fullTitle}
              className="rounded-lg bg-[var(--accent)] px-3 py-1.5 text-sm font-medium text-[#062a25] hover:opacity-90 disabled:opacity-50"
            >
              {fullLabel}
            </button>
          </div>
        </div>
      </header>

      <div className="flex-1 overflow-auto px-3 py-3 sm:px-6 sm:py-4">
        <div className="mx-auto max-w-[1600px] space-y-4">
          <p className="max-w-3xl text-xs leading-relaxed text-[var(--muted)]">
            Через каждый хост подписки поднимается отдельный клиент xray, и через него рагент
            открывает ChatGPT, Claude, Gemini, их API и TikTok с отпечатком браузера. Вердикт — по
            признакам (редирект, JSON-ошибка, маркеры отказа), а не по коду ответа. Челлендж
            Cloudflare — не отказ по стране, а проверка на бота. TikTok работает, если отдаёт
            страницу входа; заслонка WAF вместо неё («Please wait…», полтора килобайта вместо
            сотен) — отказ: TikTok пометил адрес выхода.
          </p>

          {msg && (
            <div className="rounded-lg border border-[var(--border)] bg-[var(--bg-row)] px-3 py-2 text-sm">
              {msg}
            </div>
          )}

          <section className="flex flex-wrap items-center gap-x-5 gap-y-2 rounded-[var(--radius)] border border-[var(--border)] bg-[var(--bg-elevated)] px-4 py-3 text-sm">
            {run ? <RunProgress run={run} pendingFull={status.pending_full} /> : null}
            <span>
              <span className="text-[var(--muted)]">Полный прогон: </span>
              {shown ? (
                <>
                  <span className="tabular-nums">{fmtTime(shown.started_at)}</span>
                  <span className="text-[var(--muted)]">
                    {" "}
                    · {ago(shown.started_at)}
                    {shown.finished_at ? ` · ${fmtDuration(shown.started_at, shown.finished_at)}` : ""}
                    {` · ${shown.trigger === "manual" ? "вручную" : "по расписанию"}`}
                    {` · хостов ${status.rows.length}`}
                  </span>
                </>
              ) : (
                <span className="text-[var(--muted)]">ещё не было</span>
              )}
            </span>
            {status.prev && (
              <span className="text-[var(--muted)]">
                сравнение с {fmtTime(status.prev.started_at)} ·{" "}
                <span className={changedCount ? "text-[var(--accent)]" : ""}>
                  изменилось хостов: {changedCount}
                </span>
              </span>
            )}
            {last?.status === "cancelled" && !fullActive && (
              <span className="text-[var(--muted)]">
                прогон {fmtTime(last.started_at)} отменён · успел проверить {last.hosts_done}
              </span>
            )}
            <span className="text-[var(--muted)]">
              {status.config.enabled
                ? `Следующий:${status.next_run_at ? fmtTime(status.next_run_at) : "после текущего"} · раз в ${status.config.interval_hours} ч`
                : "Расписание выключено"}
            </span>
          </section>

          {failed && (
            <div className="rounded-lg border border-[rgba(240,113,120,0.45)] bg-[rgba(240,113,120,0.10)] px-3 py-2 text-sm">
              Прогон {fmtTime(failed.started_at)} {failed.status === "aborted" ? "прерван" : "упал"}:{" "}
              {failed.error ?? "без подробностей"}
            </div>
          )}

          {partialMissing.length > 0 && partialNote && (
            <div className="rounded-lg border border-[rgba(230,162,60,0.45)] bg-[rgba(230,162,60,0.10)] px-3 py-2 text-sm">
              Выборочная проверка {fmtTime(partialNote.started_at)}: не проверен{" "}
              {partialMissing.map((h) => h.host).join(", ")} —{" "}
              {partialMissing[0].error ?? "хоста нет в подписке"}.
            </div>
          )}
          {partialFailed && (
            <div className="rounded-lg border border-[rgba(240,113,120,0.45)] bg-[rgba(240,113,120,0.10)] px-3 py-2 text-sm">
              Выборочная проверка {fmtTime(partialFailed.started_at)}{" "}
              {partialFailed.status === "aborted" ? "прервана" : "упала"}:{" "}
              {partialFailed.error ?? "без подробностей"}
            </div>
          )}

          {!canRun && (
            <div className="rounded-lg border border-[rgba(230,162,60,0.45)] bg-[rgba(230,162,60,0.10)] px-3 py-2 text-sm">
              Не задан адрес подписки — ни здесь в настройках, ни в следилке за подпиской.
            </div>
          )}

          {shown && shown.changes.length > 0 && <ChangesList changes={shown.changes} services={services} />}

          {status.rows.length > 0 && (
            <section className="rounded-[var(--radius)] border border-[var(--border)] bg-[var(--bg-elevated)]">
              <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-2 border-b border-[var(--border)] px-4 py-2.5">
                <Legend />
                <div className="flex flex-wrap items-center gap-1 text-xs">
                  {picked.length > 0 && (
                    <>
                      <button
                        type="button"
                        disabled={!canRun}
                        onClick={() => {
                          void runHosts(picked.map((r) => r.host_key));
                          setSelected(new Set());
                        }}
                        title="Проверить только отмеченные хосты. Если прогон уже идёт, они встанут в его очередь"
                        className="rounded-md border border-[var(--accent)] bg-[var(--accent-dim)] px-2 py-1 font-medium text-[var(--accent)] hover:brightness-110 disabled:opacity-50"
                      >
                        Проверить выбранные ({picked.length})
                      </button>
                      <button
                        type="button"
                        onClick={() => setSelected(new Set())}
                        title="Снять все отметки"
                        className="mr-2 rounded-md border border-[var(--border)] px-2 py-1 text-[var(--muted)] hover:text-[var(--text)]"
                      >
                        снять
                      </button>
                    </>
                  )}
                  {(
                    [
                      ["all", "все"],
                      ["problems", "с проблемами"],
                      ["changed", "изменились"],
                    ] as const
                  ).map(([k, label]) => (
                    <button
                      key={k}
                      type="button"
                      onClick={() => setFilter(k)}
                      className={`rounded-md border px-2 py-1 ${
                        filter === k
                          ? "border-[var(--accent)] bg-[var(--accent-dim)] text-[var(--accent)]"
                          : "border-[var(--border)] text-[var(--muted)] hover:text-[var(--text)]"
                      }`}
                    >
                      {label}
                    </button>
                  ))}
                </div>
              </div>
              {/* Свой скролл в обе стороны: иначе шапка таблицы не прилипает —
                  горизонтальная прокрутка матрицы отрезает её от прокрутки страницы. */}
              <div className="max-h-[calc(100dvh-11rem)] overflow-auto overscroll-x-contain rounded-b-[var(--radius)]">
                <table className="w-full border-separate border-spacing-0 text-sm">
                  <thead>
                    <tr className="text-left text-xs text-[var(--muted)]">
                      <th className={`${TH} left-0 z-30 px-3`}>
                        <span className="flex items-center gap-1.5">
                          <input
                            type="checkbox"
                            checked={allPicked}
                            disabled={pickable.length === 0}
                            onChange={() =>
                              setSelected((prev) => {
                                const next = new Set(prev);
                                for (const r of pickable)
                                  if (allPicked) next.delete(r.host_key);
                                  else next.add(r.host_key);
                                return next;
                              })
                            }
                            title={allPicked ? "Снять отметки" : "Отметить все хосты в списке"}
                            aria-label="Отметить все хосты в списке"
                            className="h-3.5 w-3.5 shrink-0 accent-[var(--accent)]"
                          />
                          Хост
                        </span>
                      </th>
                      <th className={`${TH} z-20 px-3`}>Выход ноды</th>
                      <th className={`${TH} z-20 px-3`}>Выход ИИ</th>
                      {services.map((s, i) => (
                        <th
                          key={s.key}
                          className={`${TH} z-20 px-2 text-center ${starts.has(s.key) ? GROUP_LINE : ""}`}
                        >
                          {grouped && (
                            <div className="text-left text-[10px] font-normal uppercase tracking-wide opacity-70">
                              {i === 0 || starts.has(s.key) ? s.group_name : "\u00a0"}
                            </div>
                          )}
                          <div>{s.name}</div>
                          <div className="mt-0.5 font-normal tabular-nums">
                            <span className="text-[var(--success)]">{counts[s.key]?.ok ?? 0}</span>
                            <span className="opacity-60"> / {status.rows.length}</span>
                          </div>
                        </th>
                      ))}
                      <th className={`${TH} z-20 px-3`}>Проверен</th>
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((r) => (
                      <Fragment key={r.host_key}>
                        <HostRow
                          row={r}
                          services={services}
                          starts={starts}
                          expanded={open === r.host_key}
                          onToggle={() => setOpen(open === r.host_key ? null : r.host_key)}
                          picked={selected.has(r.host_key)}
                          onPick={() => toggle(r.host_key)}
                          sending={sending.has(r.host_key)}
                          canRun={canRun}
                          onRun={() => void runHosts([r.host_key])}
                          onCancel={() => void cancelHosts([r.host_key])}
                        />
                        {open === r.host_key && (
                          <tr>
                            <td colSpan={4 + services.length} className={`${ROW_LINE} bg-[var(--bg)] px-4 py-3`}>
                              <HostHistory hostKey={r.host_key} services={services} />
                            </td>
                          </tr>
                        )}
                      </Fragment>
                    ))}
                    {rows.length === 0 && (
                      <tr>
                        <td colSpan={4 + services.length} className="px-4 py-6 text-center text-sm text-[var(--muted)]">
                          Под фильтр ничего не попало
                        </td>
                      </tr>
                    )}
                  </tbody>
                </table>
              </div>
            </section>
          )}

          <AiAccessSettings status={status} onSaved={setStatus} />
        </div>
      </div>
    </div>
  );
}

function RunProgress({ run, pendingFull }: { run: AiRun; pendingFull: AiAccessStatus["pending_full"] }) {
  const { hosts_done: done, hosts_total: total } = run;
  const pct = total ? Math.round((100 * done) / total) : 0;
  const label = run.stopping ? "Отменяется" : run.scope === "partial" ? "Проверка выбранных" : "Идёт прогон";
  return (
    <span className="flex flex-wrap items-center gap-2">
      <span className="h-2 w-2 animate-pulse rounded-full bg-[var(--accent)]" aria-hidden />
      <span>{label}</span>
      <span className="tabular-nums text-[var(--muted)]">
        {total ? `${done} / ${total}` : "забираю подписку…"}
      </span>
      {total ? (
        <span className="h-1.5 w-28 overflow-hidden rounded-full bg-[var(--bg-row)]">
          <span className="block h-full bg-[var(--accent)]" style={{ width: `${pct}%` }} />
        </span>
      ) : null}
      {pendingFull && (
        <span className="text-[var(--muted)]">
          · следом полный прогон{pendingFull === "schedule" ? " по расписанию" : ""}
        </span>
      )}
    </span>
  );
}

function Legend() {
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-[var(--muted)]">
      {(Object.keys(VERDICT) as AiVerdict[]).map((v) => (
        <span key={v} className="flex items-center gap-1.5">
          <span className={`h-2.5 w-2.5 rounded-sm ${VERDICT[v].dot}`} />
          {VERDICT[v].label}
        </span>
      ))}
      <span className="flex items-center gap-1.5">
        <span className="h-2.5 w-2.5 rounded-sm ring-2 ring-[var(--accent)]" />
        изменилось с прошлой проверки
      </span>
    </div>
  );
}

function ChangesList({
  changes,
  services,
}: {
  changes: AiChange[];
  services: AiService[];
}) {
  const names = Object.fromEntries(services.map((s) => [s.key, s.name]));
  return (
    <section className="rounded-[var(--radius)] border border-[var(--border)] bg-[var(--bg-elevated)] px-4 py-3">
      <h2 className="text-sm font-semibold">Изменения с прошлого прогона</h2>
      <div className="mt-2 flex flex-wrap gap-1.5">
        {changes.map((c, i) => {
          const exit = c.service === "ai_exit";
          const after = exit ? null : verdictStyle(c.after);
          return (
            <span
              key={`${c.host_key}-${c.service}-${i}`}
              className={`rounded-md border px-2 py-0.5 text-xs ${
                after ? after.cell : "border-[var(--border)] text-[var(--text)]"
              }`}
            >
              {c.host} · {exit ? "страна выхода ИИ" : names[c.service] ?? c.service}: {c.before} → {c.after}
            </span>
          );
        })}
      </div>
    </section>
  );
}

function Exit({ ip, cc, warp, prevCc }: { ip: string | null; cc: string | null; warp?: string | null; prevCc?: string | null }) {
  if (!ip && !cc) return <span className="text-[var(--muted)]">—</span>;
  return (
    <span className="flex items-center gap-1.5 whitespace-nowrap">
      <CountryFlag code={cc} size={12} />
      <span className="tabular-nums">{cc ?? "?"}</span>
      <span className="font-mono text-xs text-[var(--muted)]">{ip}</span>
      {warp && warp !== "off" && (
        <span className="rounded bg-[rgba(246,130,31,0.15)] px-1 text-[10px] font-semibold text-[#f6821f]">WARP</span>
      )}
      {prevCc && (
        <span className="rounded px-1 text-[10px] text-[var(--accent)] ring-1 ring-[var(--accent)]" title="страна выхода ИИ сменилась">
          было {prevCc}
        </span>
      )}
    </span>
  );
}

function Cell({ cell }: { cell: AiCell | undefined }) {
  if (!cell) return <span className="text-[var(--muted)]">—</span>;
  const st = verdictStyle(cell.verdict);
  const tip = [cell.note, cell.tries > 1 ? `попыток: ${cell.tries}` : "", cell.prev ? `было: ${cell.prev}` : ""]
    .filter(Boolean)
    .join(" · ");
  return (
    <span
      title={tip}
      className={`inline-flex min-w-[76px] flex-col items-center rounded-md border px-1.5 py-0.5 text-xs leading-tight ${st.cell} ${
        cell.prev ? "ring-2 ring-[var(--accent)] ring-offset-1 ring-offset-[var(--bg-elevated)]" : ""
      }`}
    >
      <span className="font-medium">{st.label}</span>
      {cell.prev && <span className="text-[10px] opacity-80">было {verdictStyle(cell.prev).label}</span>}
    </span>
  );
}

/** Кольцо-спиннер во всю кнопку: яркое — хост проверяется, тусклое и медленное — ждёт. */
function Ring({ muted }: { muted?: boolean }) {
  return (
    <svg
      className={`absolute inset-0 h-full w-full animate-spin ${
        muted ? "text-[var(--muted)] [animation-duration:2.4s]" : "text-[var(--accent)]"
      }`}
      viewBox="0 0 24 24"
      fill="none"
      aria-hidden
    >
      <circle cx="12" cy="12" r="10.5" stroke="currentColor" strokeOpacity="0.25" strokeWidth="2" />
      <path d="M12 1.5A10.5 10.5 0 0 1 22.5 12" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
    </svg>
  );
}

const ICON_BTN =
  "relative inline-flex h-7 w-7 shrink-0 items-center justify-center text-[var(--muted)] disabled:opacity-40";

/**
 * Кнопка хоста: запустить проверку, а пока он в прогоне — спиннер с крестиком
 * отмены внутри. Один значок вместо двух: в узкой колонке место нужно имени.
 */
function RowAction({
  state,
  sending,
  canRun,
  onRun,
  onCancel,
}: {
  state: AiHostState | null;
  sending: boolean;
  canRun: boolean;
  onRun: () => void;
  onCancel: () => void;
}) {
  if (state === "cancelling")
    return (
      <span className={ICON_BTN} title="Отменяется…" aria-label="Отменяется…">
        <Ring muted />
      </span>
    );
  if (state === "queued" || state === "running") {
    const queued = state === "queued";
    const title = queued
      ? "В очереди. Нажмите, чтобы убрать из очереди"
      : "Проверяется. Нажмите, чтобы прервать — останется прежний результат";
    return (
      <button
        type="button"
        disabled={sending}
        onClick={(e) => {
          e.stopPropagation();
          onCancel();
        }}
        title={title}
        aria-label={title}
        className={`${ICON_BTN} rounded-full hover:text-[var(--danger)]`}
      >
        <Ring muted={queued} />
        <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" aria-hidden>
          <path d="M6 6l12 12M18 6L6 18" />
        </svg>
      </button>
    );
  }
  return (
    <button
      type="button"
      disabled={sending || !canRun}
      onClick={(e) => {
        e.stopPropagation();
        onRun();
      }}
      title="Проверить этот хост"
      aria-label="Проверить этот хост"
      className={`${ICON_BTN} rounded-md border border-[var(--border)] hover:border-[var(--accent)] hover:text-[var(--accent)]`}
    >
      <svg width="11" height="11" viewBox="0 0 24 24" fill="currentColor" aria-hidden>
        <path d="M7 4.5v15a1 1 0 0 0 1.5.86l13-7.5a1 1 0 0 0 0-1.72l-13-7.5A1 1 0 0 0 7 4.5z" />
      </svg>
    </button>
  );
}

function HostRow({
  row,
  services,
  starts,
  expanded,
  onToggle,
  picked,
  onPick,
  sending,
  canRun,
  onRun,
  onCancel,
}: {
  row: AiHostRow;
  services: AiService[];
  starts: Set<string>;
  expanded: boolean;
  onToggle: () => void;
  picked: boolean;
  onPick: () => void;
  sending: boolean;
  canRun: boolean;
  onRun: () => void;
  onCancel: () => void;
}) {
  const busy = isBusy(row.state);
  const checked = Boolean(row.checked_at);   // ни разу не проверенному показывать нечего
  return (
    <tr
      onClick={checked ? onToggle : undefined}
      className={`hover:bg-[var(--bg-row-hover)] ${checked ? "cursor-pointer" : ""} ${
        row.changed ? "bg-[rgba(34,211,187,0.04)]" : ""
      }`}
    >
      <td className={`${ROW_LINE} sticky left-0 z-10 max-w-[210px] bg-[var(--bg-elevated)] px-3 py-1.5 sm:max-w-[260px]`}>
        <div className="flex items-center gap-1.5">
          <input
            type="checkbox"
            checked={picked && !busy}
            disabled={busy}
            onChange={onPick}
            onClick={(e) => e.stopPropagation()}
            title={busy ? "Хост уже в прогоне" : "Отметить для выборочной проверки"}
            aria-label={`Отметить ${row.host}`}
            className="h-3.5 w-3.5 shrink-0 accent-[var(--accent)]"
          />
          <span className="w-2 shrink-0 text-[10px] text-[var(--muted)]">
            {checked ? (expanded ? "▾" : "▸") : ""}
          </span>
          <span className="min-w-0 flex-1 truncate" title={row.host}>
            {row.host}
          </span>
          {row.is_new && (
            <span className="shrink-0 rounded bg-[var(--accent-dim)] px-1 text-[10px] text-[var(--accent)]">новый</span>
          )}
          <RowAction state={row.state} sending={sending} canRun={canRun} onRun={onRun} onCancel={onCancel} />
        </div>
        {busy ? (
          <div className={`mt-0.5 pl-[30px] text-[11px] ${row.state === "running" ? "text-[var(--accent)]" : "text-[var(--muted)]"}`}>
            {row.state === "running" ? "проверяется…" : row.state === "queued" ? "в очереди" : "отменяется…"}
          </div>
        ) : row.state === "cancelled" ? (
          <div className="mt-0.5 pl-[30px] text-[11px] text-[var(--muted)]" title="Проверку отменили — в строке прежний результат">
            отменён, результат прежний
          </div>
        ) : row.run_error ? (
          <div className="mt-0.5 truncate pl-[30px] text-[11px] text-[var(--warning)]" title={row.run_error}>
            не проверен: {row.run_error}
          </div>
        ) : null}
        {checked && row.tunnel !== "up" && (
          <div className="mt-0.5 truncate pl-[30px] text-[11px] text-[var(--danger)]" title={row.error ?? ""}>
            {row.tunnel === "down" ? "туннель не поднялся" : row.error ?? "ошибка"}
          </div>
        )}
      </td>
      <td className={`${ROW_LINE} px-3 py-1.5`}>
        <Exit ip={row.exit_ip} cc={row.exit_country} />
      </td>
      <td className={`${ROW_LINE} px-3 py-1.5`}>
        <Exit ip={row.ai_ip} cc={row.ai_country} warp={row.ai_warp} prevCc={row.prev_ai_country} />
      </td>
      {services.map((s) => (
        <td key={s.key} className={`${ROW_LINE} px-2 py-1.5 text-center ${starts.has(s.key) ? GROUP_LINE : ""}`}>
          <Cell cell={row.results[s.key]} />
        </td>
      ))}
      <td className={`${ROW_LINE} whitespace-nowrap px-3 py-1.5 text-xs leading-tight text-[var(--muted)]`}>
        <div title={row.checked_at ? fmtTime(row.checked_at) : "ещё не проверялся"}>
          {row.checked_at ? ago(row.checked_at) : "—"}
        </div>
        {row.partial && (
          <div
            className="text-[10px] text-[var(--accent)]"
            title="Проверен отдельно, после полного прогона; сравнение — с его же прошлым результатом"
          >
            отдельно
          </div>
        )}
      </td>
    </tr>
  );
}

function HostHistory({ hostKey, services }: { hostKey: string; services: AiService[] }) {
  const [hist, setHist] = useState<AiHostHistory | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    api
      .aiAccessHistory(hostKey, 30)
      .then((h) => alive && setHist(h))
      .catch((e) => alive && setErr(e instanceof Error ? e.message : "Не загрузилось"));
    return () => {
      alive = false;
    };
  }, [hostKey]);

  if (err) return <div className="text-xs text-[var(--danger)]">{err}</div>;
  if (!hist) return <div className="text-xs text-[var(--muted)]">Загрузка истории…</div>;
  if (!hist.points.length) return <div className="text-xs text-[var(--muted)]">За 30 дней записей нет</div>;

  const grouped = new Set(services.map((s) => s.group)).size > 1;
  const exits = hist.points.reduce<{ at: string; cc: string | null; ip: string | null }[]>((acc, p) => {
    const lastExit = acc[acc.length - 1];
    if (!lastExit || lastExit.cc !== p.ai_country) acc.push({ at: p.checked_at, cc: p.ai_country, ip: p.ai_ip });
    return acc;
  }, []);

  return (
    <div className="space-y-2">
      <div className="text-xs text-[var(--muted)]">
        История за {hist.days} дней · проверок {hist.points.length} · старые слева, свежие справа
      </div>
      <div className="grid grid-cols-[max-content_1fr] items-center gap-x-3 gap-y-1.5">
        {services.map((s, i) => (
          <Fragment key={s.key}>
            {grouped && (i === 0 || services[i - 1].group !== s.group) && (
              <span className={`col-span-2 text-[10px] uppercase tracking-wide text-[var(--muted)] opacity-70 ${i ? "mt-1" : ""}`}>
                {s.group_name}
              </span>
            )}
            <span className="text-xs text-[var(--muted)]">{s.name}</span>
            <span className="flex flex-wrap gap-[3px]">
              {hist.points.map((p) => {
                const c = p.results[s.key];
                const st = c ? verdictStyle(c.verdict) : null;
                return (
                  <span
                    key={`${p.run_id}-${p.checked_at}`}
                    title={`${fmtTime(p.checked_at)} — ${c ? `${st!.label}${c.note ? `: ${c.note}` : ""}` : "не проверялся"}`}
                    className={`inline-block h-3.5 w-2.5 rounded-sm ${st ? st.dot : "bg-[var(--bg-row)]"}`}
                  />
                );
              })}
            </span>
          </Fragment>
        ))}
      </div>
      <div className="flex flex-wrap items-center gap-2 text-xs text-[var(--muted)]">
        <span>Выход ИИ:</span>
        {exits.map((e, i) => (
          <span key={`${e.at}-${i}`} className="flex items-center gap-1" title={fmtTime(e.at)}>
            {i > 0 && <span>→</span>}
            <CountryFlag code={e.cc} size={11} />
            <span>{e.cc ?? "?"}</span>
            <span className="font-mono">{e.ip}</span>
            <span className="opacity-70">с {new Date(e.at).toLocaleDateString()}</span>
          </span>
        ))}
      </div>
    </div>
  );
}

function AiAccessSettings({
  status,
  onSaved,
}: {
  status: AiAccessStatus;
  onSaved: (s: AiAccessStatus) => void;
}) {
  const cfg = status.config;
  const [interval, setIntervalH] = useState(cfg.interval_hours);
  const [concurrency, setConcurrency] = useState(cfg.concurrency);
  const [subUrl, setSubUrl] = useState(cfg.sub_url);
  const [exclude, setExclude] = useState(cfg.exclude);
  const [chatId, setChatId] = useState(cfg.chat_id);
  const [keepDays, setKeepDays] = useState(cfg.keep_days);
  const [geminiKey, setGeminiKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  async function patch(payload: Parameters<typeof api.aiAccessUpdate>[0], note?: string) {
    setBusy(true);
    setMsg(null);
    try {
      const s = await api.aiAccessUpdate(payload);
      onSaved(s);
      setGeminiKey("");
      if (note) setMsg(note);
    } catch (err) {
      setMsg(err instanceof Error ? err.message : "Не удалось сохранить");
    } finally {
      setBusy(false);
    }
  }

  return (
    <details className="rounded-[var(--radius)] border border-[var(--border)] bg-[var(--bg-elevated)] p-4">
      <summary className="cursor-pointer text-sm font-semibold">Настройки проверки</summary>

      <div className="mt-4 space-y-3">
        <label className="flex items-center gap-2 text-sm">
          <input
            type="checkbox"
            checked={cfg.enabled}
            disabled={busy}
            onChange={(e) => void patch({ enabled: e.target.checked }, e.target.checked ? "Включено" : "Выключено")}
            className="h-4 w-4 accent-[var(--accent)]"
          />
          <span className={cfg.enabled ? "text-[var(--success)]" : "text-[var(--muted)]"}>
            {cfg.enabled ? "проверять по расписанию" : "по расписанию не проверяется"}
          </span>
        </label>

        <div className="flex flex-wrap gap-3">
          <label className="min-w-32 flex-1">
            <span className="text-xs text-[var(--muted)]">Раз в, часов</span>
            <input type="number" min={1} max={168} value={interval}
              onChange={(e) => setIntervalH(Number(e.target.value))} className={`mt-1 ${INPUT}`} />
          </label>
          <label className="min-w-32 flex-1">
            <span className="text-xs text-[var(--muted)]">Хостов одновременно (1–4)</span>
            <input type="number" min={1} max={4} value={concurrency}
              onChange={(e) => setConcurrency(Number(e.target.value))} className={`mt-1 ${INPUT}`} />
          </label>
          <label className="min-w-32 flex-1">
            <span className="text-xs text-[var(--muted)]">Хранить историю, дней (от 30)</span>
            <input type="number" min={30} max={365} value={keepDays}
              onChange={(e) => setKeepDays(Number(e.target.value))} className={`mt-1 ${INPUT}`} />
          </label>
        </div>

        <label className="block">
          <span className="text-xs text-[var(--muted)]">
            Подписка, по хостам которой идём (XRAY_JSON). Пусто — та же, что у следилки
            {cfg.sub_from_subwatch ? " (сейчас берётся оттуда)" : ""}. Нужен пользователь, которому
            видны все хосты.
          </span>
          <input value={subUrl} onChange={(e) => setSubUrl(e.target.value)} spellCheck={false}
            placeholder="https://sub.example.com/sub/xxxxxxxx" className={`mt-1 font-mono ${INPUT}`} />
        </label>

        <label className="block">
          <span className="text-xs text-[var(--muted)]">
            Не проверять хосты, в имени которых есть (через запятую)
          </span>
          <input value={exclude} onChange={(e) => setExclude(e.target.value)} spellCheck={false}
            placeholder="LTE • AT, тест" className={`mt-1 ${INPUT}`} />
        </label>

        <label className="block">
          <span className="text-xs text-[var(--muted)]">
            Ключ Gemini API{cfg.gemini_key_set ? " — задан, введите новый чтобы заменить" : ""}. Без
            ключа API Gemini не проверяется: ответ без ключа ничего не говорит о стране.
          </span>
          <input value={geminiKey} onChange={(e) => setGeminiKey(e.target.value)} type="password"
            autoComplete="off" spellCheck={false} placeholder={cfg.gemini_key_set ? "••••••••" : "AIza..."}
            className={`mt-1 font-mono ${INPUT}`} />
        </label>

        <div className="border-t border-[var(--border)] pt-3">
          <label className="flex items-center gap-2 text-sm">
            <input type="checkbox" checked={cfg.notify} disabled={busy}
              onChange={(e) => void patch({ notify: e.target.checked })}
              className="h-4 w-4 accent-[var(--accent)]" />
            Сводка изменений в Telegram
          </label>
          <p className="mt-1 text-xs leading-relaxed text-[var(--muted)]">
            Пишем, только когда у ИИ или TikTok что-то сломалось (ok → отказ или челлендж),
            починилось или сменилась страна выхода ИИ. Таймауты не шлём — это лежащая нода, про неё скажут другие проверки.
            Бот — тот же, что у следилки{cfg.bot_ready ? "" : " (сейчас не настроен)"}.
          </p>
          <label className="mt-2 block">
            <span className="text-xs text-[var(--muted)]">id чата — пусто, тот же, что у следилки</span>
            <input value={chatId} onChange={(e) => setChatId(e.target.value)} spellCheck={false}
              className={`mt-1 font-mono ${INPUT}`} />
          </label>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <button
            type="button"
            disabled={busy}
            className={BTN}
            onClick={() =>
              void patch(
                {
                  interval_hours: interval,
                  concurrency,
                  keep_days: keepDays,
                  sub_url: subUrl.trim(),
                  exclude: exclude.trim(),
                  chat_id: chatId.trim(),
                  ...(geminiKey.trim() ? { gemini_api_key: geminiKey.trim() } : {}),
                },
                "Сохранено",
              )
            }
          >
            Сохранить
          </button>
          {cfg.gemini_key_set && (
            <button type="button" disabled={busy} className={BTN}
              onClick={() => void patch({ gemini_api_key: "" }, "Ключ Gemini стёрт")}>
              Стереть ключ Gemini
            </button>
          )}
          {msg && <span className="text-xs text-[var(--muted)]">{msg}</span>}
        </div>
      </div>
    </details>
  );
}
