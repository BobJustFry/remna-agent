import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  api,
  type AiAccessStatus,
  type AiCell,
  type AiChange,
  type AiHostHistory,
  type AiHostRow,
  type AiVerdict,
} from "../api/client";
import { CountryFlag } from "../components/CountryFlag";

const INPUT =
  "w-full rounded-lg border border-[var(--border)] bg-[var(--bg)] px-3 py-2 text-sm text-[var(--text)] outline-none focus:border-[var(--accent)]";
const BTN =
  "rounded-lg border border-[var(--border)] px-3 py-1.5 text-sm text-[var(--text)] hover:bg-[var(--bg-row)] disabled:opacity-50";

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

/**
 * Доступ к ИИ через каждый хост подписки.
 *
 * Значок 🧠 в имени хоста — обещание клиенту. Держится оно на WARP и правилах
 * маршрутизации ноды, и ломается молча: клиент просто уходит с хоста. Здесь
 * видно, что обещание выполняется, и что поменялось с прошлого прогона.
 */
export function AiAccessPage() {
  const [status, setStatus] = useState<AiAccessStatus | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [filter, setFilter] = useState<"all" | "problems" | "changed">("all");
  const [open, setOpen] = useState<string | null>(null);
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

  async function runNow() {
    setBusy(true);
    setMsg(null);
    try {
      setStatus(await api.aiAccessRun());
    } catch (err) {
      setMsg(err instanceof Error ? err.message : "Не запустилось");
    } finally {
      setBusy(false);
    }
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

  const { shown, last, services } = status;
  const failed = last && (last.status === "failed" || last.status === "aborted") ? last : null;
  const changedCount = status.rows.filter((r) => r.changed).length;

  return (
    <div className="mx-auto max-w-[1600px] space-y-4 p-4 sm:p-6">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h1 className="text-xl font-semibold tracking-tight">ИИ-доступ</h1>
          <p className="mt-1 max-w-3xl text-xs leading-relaxed text-[var(--muted)]">
            Через каждый хост подписки поднимается отдельный клиент xray, и через него рагент
            открывает ChatGPT, Claude, Gemini и их API с отпечатком браузера. Вердикт — по
            признакам (редирект, JSON-ошибка, маркеры отказа), а не по коду ответа. Челлендж
            Cloudflare — не отказ по стране, а проверка на бота.
          </p>
        </div>
        <button
          type="button"
          disabled={busy || running || !status.config.sub_configured}
          onClick={() => void runNow()}
          className="shrink-0 rounded-lg bg-[var(--accent)] px-3 py-1.5 text-sm font-medium text-[#062a25] hover:opacity-90 disabled:opacity-50"
        >
          {running ? "Идёт прогон…" : busy ? "Запускаю…" : "Проверить сейчас"}
        </button>
      </header>

      {msg && (
        <div className="rounded-lg border border-[var(--border)] bg-[var(--bg-row)] px-3 py-2 text-sm">
          {msg}
        </div>
      )}

      <section className="flex flex-wrap items-center gap-x-5 gap-y-2 rounded-[var(--radius)] border border-[var(--border)] bg-[var(--bg-elevated)] px-4 py-3 text-sm">
        {status.running ? (
          <RunProgress done={status.running.hosts_done} total={status.running.hosts_total} />
        ) : null}
        <span>
          <span className="text-[var(--muted)]">Прогон: </span>
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
        <span className="text-[var(--muted)]">
          {status.config.enabled
            ? `Следующий: ${status.next_run_at ? fmtTime(status.next_run_at) : "после текущего"} · раз в ${status.config.interval_hours} ч`
            : "Расписание выключено"}
        </span>
      </section>

      {failed && (
        <div className="rounded-lg border border-[rgba(240,113,120,0.45)] bg-[rgba(240,113,120,0.10)] px-3 py-2 text-sm">
          Прогон {fmtTime(failed.started_at)} {failed.status === "aborted" ? "прерван" : "упал"}:{" "}
          {failed.error ?? "без подробностей"}
        </div>
      )}

      {!status.config.sub_configured && (
        <div className="rounded-lg border border-[rgba(230,162,60,0.45)] bg-[rgba(230,162,60,0.10)] px-3 py-2 text-sm">
          Не задан адрес подписки — ни здесь в настройках, ни в следилке за подпиской.
        </div>
      )}

      {shown && shown.changes.length > 0 && <ChangesList changes={shown.changes} services={services} />}

      {status.rows.length > 0 && (
        <section className="rounded-[var(--radius)] border border-[var(--border)] bg-[var(--bg-elevated)]">
          <div className="flex flex-wrap items-center justify-between gap-3 border-b border-[var(--border)] px-4 py-2.5">
            <Legend />
            <div className="flex gap-1 text-xs">
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
          <div className="overflow-x-auto">
            <table className="w-full border-collapse text-sm">
              <thead>
                <tr className="text-left text-xs text-[var(--muted)]">
                  <th className="sticky left-0 z-10 bg-[var(--bg-elevated)] px-3 py-2 font-medium">Хост</th>
                  <th className="px-3 py-2 font-medium">Выход ноды</th>
                  <th className="px-3 py-2 font-medium">Выход ИИ</th>
                  {services.map((s) => (
                    <th key={s.key} className="px-2 py-2 text-center font-medium">
                      <div>{s.name}</div>
                      <div className="mt-0.5 font-normal tabular-nums">
                        <span className="text-[var(--success)]">{counts[s.key]?.ok ?? 0}</span>
                        <span className="opacity-60"> / {status.rows.length}</span>
                      </div>
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <Fragment key={r.host_key}>
                    <HostRow
                      row={r}
                      services={services}
                      expanded={open === r.host_key}
                      onToggle={() => setOpen(open === r.host_key ? null : r.host_key)}
                    />
                    {open === r.host_key && (
                      <tr>
                        <td colSpan={3 + services.length} className="border-t border-[var(--border)] bg-[var(--bg)] px-4 py-3">
                          <HostHistory hostKey={r.host_key} services={services} />
                        </td>
                      </tr>
                    )}
                  </Fragment>
                ))}
                {rows.length === 0 && (
                  <tr>
                    <td colSpan={3 + services.length} className="px-4 py-6 text-center text-sm text-[var(--muted)]">
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
  );
}

function RunProgress({ done, total }: { done: number; total: number | null }) {
  const pct = total ? Math.round((100 * done) / total) : 0;
  return (
    <span className="flex items-center gap-2">
      <span className="h-2 w-2 animate-pulse rounded-full bg-[var(--accent)]" aria-hidden />
      <span>Идёт прогон</span>
      <span className="tabular-nums text-[var(--muted)]">
        {total ? `${done} / ${total}` : "забираю подписку…"}
      </span>
      {total ? (
        <span className="h-1.5 w-28 overflow-hidden rounded-full bg-[var(--bg-row)]">
          <span className="block h-full bg-[var(--accent)]" style={{ width: `${pct}%` }} />
        </span>
      ) : null}
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
        изменилось с прошлого прогона
      </span>
    </div>
  );
}

function ChangesList({
  changes,
  services,
}: {
  changes: AiChange[];
  services: { key: string; name: string }[];
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

function HostRow({
  row,
  services,
  expanded,
  onToggle,
}: {
  row: AiHostRow;
  services: { key: string; name: string }[];
  expanded: boolean;
  onToggle: () => void;
}) {
  return (
    <tr
      onClick={onToggle}
      className={`cursor-pointer border-t border-[var(--border)] hover:bg-[var(--bg-row-hover)] ${
        row.changed ? "bg-[rgba(34,211,187,0.04)]" : ""
      }`}
    >
      <td className="sticky left-0 z-10 max-w-[260px] bg-[var(--bg-elevated)] px-3 py-1.5">
        <div className="flex items-center gap-1.5">
          <span className="text-[10px] text-[var(--muted)]">{expanded ? "▾" : "▸"}</span>
          <span className="truncate" title={row.host}>
            {row.host}
          </span>
          {row.is_new && (
            <span className="shrink-0 rounded bg-[var(--accent-dim)] px-1 text-[10px] text-[var(--accent)]">новый</span>
          )}
        </div>
        {row.tunnel !== "up" && (
          <div className="mt-0.5 truncate text-[11px] text-[var(--danger)]" title={row.error ?? ""}>
            {row.tunnel === "down" ? "туннель не поднялся" : row.error ?? "ошибка"}
          </div>
        )}
      </td>
      <td className="px-3 py-1.5">
        <Exit ip={row.exit_ip} cc={row.exit_country} />
      </td>
      <td className="px-3 py-1.5">
        <Exit ip={row.ai_ip} cc={row.ai_country} warp={row.ai_warp} prevCc={row.prev_ai_country} />
      </td>
      {services.map((s) => (
        <td key={s.key} className="px-2 py-1.5 text-center">
          <Cell cell={row.results[s.key]} />
        </td>
      ))}
    </tr>
  );
}

function HostHistory({ hostKey, services }: { hostKey: string; services: { key: string; name: string }[] }) {
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

  const exits = hist.points.reduce<{ at: string; cc: string | null; ip: string | null }[]>((acc, p) => {
    const lastExit = acc[acc.length - 1];
    if (!lastExit || lastExit.cc !== p.ai_country) acc.push({ at: p.checked_at, cc: p.ai_country, ip: p.ai_ip });
    return acc;
  }, []);

  return (
    <div className="space-y-2">
      <div className="text-xs text-[var(--muted)]">
        История за {hist.days} дней · прогонов {hist.points.length} · старые слева, свежие справа
      </div>
      <div className="grid grid-cols-[max-content_1fr] items-center gap-x-3 gap-y-1.5">
        {services.map((s) => (
          <Fragment key={s.key}>
            <span className="text-xs text-[var(--muted)]">{s.name}</span>
            <span className="flex flex-wrap gap-[3px]">
              {hist.points.map((p) => {
                const c = p.results[s.key];
                const st = c ? verdictStyle(c.verdict) : null;
                return (
                  <span
                    key={p.run_id}
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
            Пишем, только когда что-то сломалось (ok → отказ или челлендж), починилось или сменилась
            страна выхода ИИ. Таймауты не шлём — это лежащая нода, про неё скажут другие проверки.
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
