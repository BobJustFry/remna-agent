import { useEffect, useState } from "react";
import { api } from "../api/client";
import { Link } from "react-router-dom";
import { useSharingStatus } from "../hooks/useSharingStatus";
import type { SharingBanRow, SharingDossier, SharingUserHit } from "../types";

const ZONE: Record<string, { label: string; cls: string }> = {
  red: { label: "красная", cls: "bg-[var(--danger)] text-white" },
  amber: { label: "жёлтая", cls: "bg-[var(--warning)] text-black" },
  green: { label: "зелёная", cls: "bg-[var(--success)] text-black" },
};

/** Раздел «Шаринг»: кто набрал баллы, за что именно и досье по человеку. */
export function SharingPage() {
  const status = useSharingStatus();
  const [open, setOpen] = useState<SharingUserHit | null>(null);
  const [scanning, setScanning] = useState(false);
  const [banned, setBanned] = useState<SharingBanRow[]>([]);
  const [note, setNote] = useState<string | null>(null);

  function loadBans() {
    api.sharingBans().then((b) => setBanned(b.banned)).catch(() => undefined);
  }
  useEffect(loadBans, []);

  async function report(userId: number, saved: boolean, format: "md" | "txt" = "md") {
    setNote("Собираю отчёт…");
    try {
      const r = await api.sharingUserReport(userId, saved, format);
      const blob = new Blob([r.text], { type: format === "txt" ? "text/plain;charset=utf-8" : "text/markdown;charset=utf-8" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = r.filename;
      a.click();
      URL.revokeObjectURL(url);
      setNote(`Отчёт скачан: ${r.filename}`);
    } catch (err: unknown) {
      setNote(err instanceof Error ? err.message : "Не удалось собрать отчёт");
    }
  }

  async function ban(s: SharingUserHit) {
    const name = s.username || `id ${s.user_id}`;
    if (
      !window.confirm(
        `Отключить ${name} (оценка ${s.score}) в панели и разорвать его соединения на всех нодах?\n\nВернуть можно кнопкой «Вернуть» ниже.`,
      )
    )
      return;
    try {
      const r = await api.sharingBan(s.user_id);
      setNote(
        r.skipped
          ? `${name}: ${r.skipped}`
          : `${name}: отключён${r.steps.includes("drop") ? ", соединения сброшены" : ""}${r.warn ? " (" + r.warn + ")" : ""}`,
      );
    } catch (err) {
      setNote(err instanceof Error ? err.message : "не удалось отключить");
    }
    loadBans();
  }

  async function unban(row: SharingBanRow) {
    const name = row.username || `id ${row.user_id}`;
    if (!window.confirm(`Вернуть ${name}: включить пользователя в панели?`)) return;
    try {
      const r = await api.sharingUnban(row.user_id);
      setNote(r.skipped ? `${name}: ${r.skipped}` : `${name}: возвращён`);
    } catch (err) {
      setNote(err instanceof Error ? err.message : "не удалось вернуть");
    }
    loadBans();
  }

  const suspects = status?.suspects ?? [];
  const red = suspects.filter((s) => s.level === "red");

  return (
    <div className="space-y-4">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h1 className="text-lg font-semibold">Шаринг</h1>
          <p className="mt-1 max-w-3xl text-xs leading-relaxed text-[var(--muted)]">
            Вердикт — не один порог на всех, а оценка от 0 до 100. Нормы по адресам,
            операторам и устройствам считаются от лимита устройств конкретного человека:
            восемь адресов у подписки на одно устройство и у семейной на пять — разные
            вещи. Тревога уходит в Телеграм, только когда красная зона держится несколько
            проверок подряд: переезд с Wi-Fi на LTE её не поднимает. Лимиты устройств
            рагент не меняет никогда — это платная услуга.
          </p>
        </div>
        <button
          type="button"
          disabled={scanning}
          onClick={() => {
            setScanning(true);
            void api.sharingScan().finally(() => setScanning(false));
          }}
          className="shrink-0 rounded-lg border border-[var(--border)] px-3 py-1.5 text-sm text-[var(--text)] hover:bg-[var(--bg-row)] disabled:opacity-50"
        >
          {scanning ? "Сканирую…" : "Сканировать сейчас"}
        </button>
      </header>

      <section className="grid gap-3 sm:grid-cols-4">
        <Tile title="Онлайн" value={status ? String(status.online_users) : "—"} />
        <Tile title="Красная зона" value={String(red.length)} danger={red.length > 0} />
        <Tile title="Жёлтая зона" value={String(suspects.length - red.length)} />
        <Tile
          title="Последний скан"
          value={status?.scanned_at ? new Date(status.scanned_at).toLocaleTimeString() : "—"}
        />
      </section>

      {status?.error && (
        <div className="rounded-[var(--radius)] border border-[var(--danger)] bg-[var(--danger)]/10 px-4 py-2 text-sm text-[var(--danger)]">
          {status.error}
        </div>
      )}

      <section className="overflow-hidden rounded-[var(--radius)] border border-[var(--border)] bg-[var(--bg-elevated)]">
        <h2 className="border-b border-[var(--border)] px-4 py-2.5 text-sm font-semibold">
          Подозрительные
        </h2>
        {!suspects.length ? (
          <p className="px-4 py-6 text-sm text-[var(--muted)]">
            {status
              ? "Никто не набрал даже жёлтой зоны — в последнем скане парк чистый."
              : "Загрузка…"}
          </p>
        ) : (
          <ul className="divide-y divide-[var(--border)]">
            {suspects.map((s) => (
              <li key={s.user_id} className="px-4 py-3">
                <div className="flex flex-wrap items-center gap-2">
                  <span
                    className={`rounded px-1.5 py-0.5 text-[11px] font-bold ${
                      ZONE[s.level]?.cls ?? ""
                    }`}
                  >
                    {s.score}
                  </span>
                  <span className="font-medium">{s.username || `id ${s.user_id}`}</span>
                  <span className="text-xs text-[var(--muted)]">
                    id {s.user_id} · лимит устройств {s.device_limit}
                    {s.unlimited ? " (безлимит)" : ""} · зона {ZONE[s.level]?.label}
                  </span>
                  <button
                    type="button"
                    onClick={() => setOpen(s)}
                    className="ml-auto rounded border border-[var(--border)] px-2 py-1 text-xs text-[var(--accent)] hover:bg-[var(--accent-dim)]"
                  >
                    Досье
                  </button>
                  <button
                    type="button"
                    onClick={() => void report(s.user_id, false)}
                    className="rounded border border-[var(--border)] px-2 py-1 text-xs text-[var(--accent)] hover:bg-[var(--accent-dim)]"
                  >
                    Отчёт
                  </button>
                  <button
                    type="button"
                    onClick={() => void report(s.user_id, false, "txt")}
                    className="rounded border border-[var(--border)] px-2 py-1 text-xs text-[var(--accent)] hover:bg-[var(--accent-dim)]"
                  >
                    Текст клиенту
                  </button>
                  <button
                    type="button"
                    onClick={() => void ban(s)}
                    className="rounded border border-[var(--danger)] px-2 py-1 text-xs text-[var(--danger)] hover:bg-[var(--danger)]/10"
                  >
                    Отключить и разорвать
                  </button>
                </div>
                <ul className="mt-1.5 space-y-0.5">
                  {s.parts.map((p) => (
                    <li key={p.key} className="text-xs leading-relaxed text-[var(--muted)]">
                      <span className="mr-1.5 font-mono text-[var(--text)]">+{p.points}</span>
                      {p.why}
                    </li>
                  ))}
                </ul>
                <div className="mt-1 text-[11px] text-[var(--muted)]">
                  ноды: {s.rw_nodes.join(", ") || "—"}
                  {s.platforms.length ? ` · платформы: ${s.platforms.join(", ")}` : ""}
                  {s.countries.length ? ` · страны сетей: ${s.countries.join(", ")}` : ""}
                </div>
              </li>
            ))}
          </ul>
        )}
      </section>

      {note && (
        <div className="rounded-[var(--radius)] border border-[var(--border)] bg-[var(--bg-elevated)] px-4 py-2 text-sm">
          {note}
        </div>
      )}

      <section className="overflow-hidden rounded-[var(--radius)] border border-[var(--border)] bg-[var(--bg-elevated)]">
        <h2 className="border-b border-[var(--border)] px-4 py-2.5 text-sm font-semibold">
          Отключённые за шаринг
        </h2>
        {!banned.length ? (
          <p className="px-4 py-4 text-sm text-[var(--muted)]">Пока никого.</p>
        ) : (
          <ul className="divide-y divide-[var(--border)]">
            {banned.map((b) => (
              <li key={b.user_id} className="flex flex-wrap items-center gap-2 px-4 py-2.5">
                <span className="font-medium">{b.username || `id ${b.user_id}`}</span>
                <span className="text-xs text-[var(--muted)]">
                  id {b.user_id} · оценка {b.score} · {new Date(b.ts).toLocaleString()} ·{" "}
                  {b.source === "auto" ? "автоматически" : "вручную"}
                </span>
                <button
                  type="button"
                  onClick={() => void report(b.user_id, true)}
                  className="ml-auto rounded border border-[var(--border)] px-2 py-1 text-xs text-[var(--accent)] hover:bg-[var(--accent-dim)]"
                >
                  Отчёт
                </button>
                <button
                  type="button"
                  onClick={() => void report(b.user_id, true, "txt")}
                  className="rounded border border-[var(--border)] px-2 py-1 text-xs text-[var(--accent)] hover:bg-[var(--accent-dim)]"
                >
                  Текст клиенту
                </button>
                <button
                  type="button"
                  onClick={() => void unban(b)}
                  className="rounded border border-[var(--border)] px-2 py-1 text-xs text-[var(--accent)] hover:bg-[var(--accent-dim)]"
                >
                  Вернуть
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>

      <p className="text-xs text-[var(--muted)]">
        <Link
          to="/settings"
          className="text-[var(--accent)] hover:underline"
          onClick={() => {
            try {
              localStorage.setItem("remna-agent.settings.tab", "sharing");
            } catch {
              /* не страшно */
            }
          }}
        >
          Настройки шаринга — в Настройках
        </Link>
      </p>

      {open && <UserDossierModal hit={open} onClose={() => setOpen(null)} />}
    </div>
  );
}

function Tile({ title, value, danger }: { title: string; value: string; danger?: boolean }) {
  return (
    <div className="rounded-[var(--radius)] border border-[var(--border)] bg-[var(--bg-elevated)] px-4 py-3">
      <div className="text-xs text-[var(--muted)]">{title}</div>
      <div
        className={`mt-1 text-xl font-semibold ${danger ? "text-[var(--danger)]" : "text-[var(--text)]"}`}
      >
        {value}
      </div>
    </div>
  );
}

function UserDossierModal({ hit, onClose }: { hit: SharingUserHit; onClose: () => void }) {
  const [data, setData] = useState<SharingDossier | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    api
      .sharingUserDossier(hit.user_id)
      .then((d) => {
        if (!cancelled) setData(d);
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(err instanceof Error ? err.message : "не удалось собрать досье");
      });
    return () => {
      cancelled = true;
    };
  }, [hit.user_id]);

  function download() {
    if (!data) return;
    const blob = new Blob([data.text], { type: "text/plain;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = data.filename;
    a.click();
    URL.revokeObjectURL(url);
  }

  return (
    <div
      className="fixed inset-0 z-[80] flex items-end justify-center bg-black/65 p-0 backdrop-blur-[2px] sm:items-center sm:p-4"
      onClick={onClose}
    >
      <div
        role="dialog"
        aria-modal="true"
        className="flex max-h-[min(96dvh,100%)] w-full max-w-[720px] flex-col overflow-hidden rounded-t-xl border border-[var(--border)] bg-[var(--bg-elevated)] shadow-2xl sm:rounded-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <header className="flex shrink-0 items-center gap-2 border-b border-[var(--border)] px-4 py-2.5">
          <span className={`rounded px-1.5 py-0.5 text-[10px] font-bold ${ZONE[hit.level]?.cls ?? ""}`}>
            {hit.score}
          </span>
          <h2 className="truncate text-sm font-semibold">
            {hit.username || `id ${hit.user_id}`}
          </h2>
          <div className="ml-auto flex gap-1">
            <button
              type="button"
              onClick={download}
              disabled={!data}
              className="rounded border border-[var(--border)] px-2 py-1 text-xs text-[var(--accent)] hover:bg-[var(--accent-dim)] disabled:opacity-40"
            >
              Скачать
            </button>
            <button
              type="button"
              onClick={onClose}
              className="rounded border border-[var(--border)] px-2 py-1 text-xs text-[var(--muted)] hover:text-[var(--text)]"
            >
              Закрыть
            </button>
          </div>
        </header>
        <pre className="min-h-0 flex-1 overflow-auto px-4 py-3 font-mono text-[11px] leading-snug text-[var(--text)]">
          {error ?? data?.text ?? "загрузка…"}
        </pre>
      </div>
    </div>
  );
}
