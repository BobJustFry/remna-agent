import { useEffect, useState } from "react";
import { api, type PanelBackupStatus, type PanelBackupUpdate } from "../api/client";

const INPUT =
  "w-full rounded-lg border border-[var(--border)] bg-[var(--bg)] px-3 py-2 text-sm text-[var(--text)] outline-none focus:border-[var(--accent)]";
const BTN =
  "rounded-lg border border-[var(--border)] px-3 py-1.5 text-sm text-[var(--text)] hover:bg-[var(--bg-row)] disabled:opacity-50";

function mb(bytes: number | null | undefined): string {
  if (bytes === null || bytes === undefined) return "—";
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} КБ`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / 1048576).toFixed(1)} МБ`;
  return `${(bytes / 1073741824).toFixed(1)} ГБ`;
}

function ago(iso: string | null): string {
  if (!iso) return "никогда";
  const ms = Date.now() - new Date(iso).getTime();
  if (Number.isNaN(ms)) return "—";
  const min = Math.floor(ms / 60000);
  if (min < 1) return "только что";
  if (min < 60) return `${min} мин назад`;
  const h = Math.floor(min / 60);
  return h < 24 ? `${h} ч назад` : `${Math.floor(h / 24)} сут назад`;
}

/**
 * Бэкап панели.
 *
 * Единственное, от чего нельзя оправиться: панель — единственное место, где
 * живут пользователи и подписки. Всё остальное в парке пересоздаётся за час.
 */
export function PanelBackupCard() {
  const [status, setStatus] = useState<PanelBackupStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [open, setOpen] = useState(false);
  const [host, setHost] = useState("");
  const [user, setUser] = useState("root");
  const [port, setPort] = useState(22);
  const [hours, setHours] = useState(6);
  const [key, setKey] = useState("");

  function apply(s: PanelBackupStatus) {
    setStatus(s);
    setHost(s.config.host);
    setUser(s.config.user);
    setPort(s.config.port);
    setHours(s.config.interval_hours);
    setKey("");
  }

  useEffect(() => {
    api.panelBackupStatus().then(apply).catch(() => undefined);
  }, []);

  async function patch(payload: PanelBackupUpdate, note?: string) {
    setBusy(true);
    setMsg(null);
    try {
      apply(await api.panelBackupUpdate(payload));
      if (note) setMsg(note);
    } catch (err) {
      setMsg(err instanceof Error ? err.message : "Не удалось сохранить");
    } finally {
      setBusy(false);
    }
  }

  async function runNow() {
    setBusy(true);
    setMsg("Снимаю…");
    try {
      const s = await api.panelBackupRun();
      apply(s);
      setMsg(s.last_error ? `Не снялся: ${s.last_error}` : "Снят");
    } catch (err) {
      setMsg(err instanceof Error ? err.message : "Не удалось снять");
    } finally {
      setBusy(false);
    }
  }

  if (!status) return null;
  const cfg = status.config;
  const stale = status.last_ok_at
    ? Date.now() - new Date(status.last_ok_at).getTime() > cfg.interval_hours * 3600_000 * 2
    : true;

  return (
    <section className="rounded-[var(--radius)] border border-[var(--border)] bg-[var(--bg-elevated)] p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h2 className="text-sm font-semibold">Бэкап панели</h2>
          <p className="mt-1 text-xs leading-relaxed text-[var(--muted)]">
            Дамп базы вместе с <code className="font-mono">.env</code> панели. Шифруется{" "}
            <b>на самой панели</b>, до отправки — сюда приезжает закрытый файл, и расшифровать
            его можно только вашим ключом, которого на серверах нет. Проверяется через{" "}
            <code className="font-mono">pg_restore</code> ещё до шифрования.
          </p>
        </div>
        <label className="flex shrink-0 items-center gap-2 text-sm">
          <input
            type="checkbox"
            checked={cfg.enabled}
            disabled={busy || !cfg.host || !cfg.ssh_key_set}
            onChange={(e) =>
              void patch({ enabled: e.target.checked },
                e.target.checked ? "Расписание включено" : "Расписание выключено")
            }
            className="h-4 w-4 accent-[var(--accent)]"
          />
          <span className={cfg.enabled ? "text-[var(--success)]" : "text-[var(--muted)]"}>
            {cfg.enabled ? `раз в ${cfg.interval_hours} ч` : "по кнопке"}
          </span>
        </label>
      </div>

      <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-[var(--muted)] tabular-nums">
        <span className={stale ? "text-[var(--danger)]" : "text-[var(--success)]"}>
          Последний удачный: {ago(status.last_ok_at)}
        </span>
        {status.last_size ? <span>{mb(status.last_size)}</span> : null}
        {status.last_duration_ms ? <span>за {(status.last_duration_ms / 1000).toFixed(1)} с</span> : null}
        <span>копий: {status.files.length}</span>
        <span>занято {mb(status.total_bytes)}</span>
        {status.disk_free_bytes !== null && <span>свободно {mb(status.disk_free_bytes)}</span>}
        <button type="button" disabled={busy} onClick={() => void runNow()} className={`ml-auto ${BTN}`}>
          {busy ? "…" : "Снять сейчас"}
        </button>
      </div>

      {status.last_error && (
        <p className="mt-3 rounded-lg border border-[rgba(240,113,120,0.45)] bg-[rgba(240,113,120,0.10)] px-3 py-2 text-xs text-[var(--danger)]">
          {status.last_error}
        </p>
      )}

      {status.files.length > 0 && (
        <ul className="mt-3 max-h-52 space-y-1 overflow-auto">
          {status.files.map((f) => (
            <li key={f.name} className="flex items-center gap-3 rounded-lg bg-[var(--bg-row)] px-3 py-1.5 text-xs">
              <span className="font-mono">{f.name}</span>
              <span className="text-[var(--muted)] tabular-nums">{mb(f.size)}</span>
              <a
                href={api.panelBackupFileUrl(f.name)}
                className="ml-auto text-[var(--accent)] hover:underline"
              >
                скачать
              </a>
            </li>
          ))}
        </ul>
      )}

      <details className="mt-3" open={open} onToggle={(e) => setOpen(e.currentTarget.open)}>
        <summary className="cursor-pointer text-xs text-[var(--muted)] hover:text-[var(--text)]">
          Доступ к панели
        </summary>
        <div className="mt-3 space-y-3">
          <div className="flex flex-wrap gap-3">
            <label className="min-w-40 flex-[2]">
              <span className="text-xs text-[var(--muted)]">Адрес панели</span>
              <input value={host} onChange={(e) => setHost(e.target.value)} spellCheck={false}
                     placeholder="194.0.0.1" className={`mt-1 font-mono ${INPUT}`} />
            </label>
            <label className="min-w-24 flex-1">
              <span className="text-xs text-[var(--muted)]">Пользователь</span>
              <input value={user} onChange={(e) => setUser(e.target.value)} spellCheck={false}
                     className={`mt-1 font-mono ${INPUT}`} />
            </label>
            <label className="min-w-20 flex-1">
              <span className="text-xs text-[var(--muted)]">Порт</span>
              <input type="number" value={port} onChange={(e) => setPort(Number(e.target.value))}
                     className={`mt-1 ${INPUT}`} />
            </label>
            <label className="min-w-20 flex-1">
              <span className="text-xs text-[var(--muted)]">Раз в, часов</span>
              <input type="number" min={1} max={168} value={hours}
                     onChange={(e) => setHours(Number(e.target.value))} className={`mt-1 ${INPUT}`} />
            </label>
          </div>
          <label className="block">
            <span className="text-xs text-[var(--muted)]">
              SSH-ключ{cfg.ssh_key_set ? " — задан, вставьте новый чтобы заменить" : ""}. На панели
              он ограничен одной командой и оболочки не даёт.
            </span>
            <textarea value={key} onChange={(e) => setKey(e.target.value)} rows={3} spellCheck={false}
                      placeholder={cfg.ssh_key_set ? "••••••••" : "-----BEGIN OPENSSH PRIVATE KEY-----"}
                      className={`mt-1 font-mono ${INPUT}`} />
          </label>
          <button
            type="button"
            disabled={busy}
            className={BTN}
            onClick={() =>
              void patch(
                {
                  host: host.trim(), user: user.trim(), port, interval_hours: hours,
                  ...(key.trim() ? { ssh_key: key } : {}),
                },
                "Сохранено",
              )
            }
          >
            Сохранить
          </button>
        </div>
      </details>

      {msg && <p className="mt-3 text-xs text-[var(--muted)]">{msg}</p>}
    </section>
  );
}
