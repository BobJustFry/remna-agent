import { useEffect, useState, type ReactNode } from "react";
import { useOutletContext } from "react-router-dom";
import { api } from "../api/client";
import type { AppOutletContext } from "../components/AppShell";
import { getQueueConcurrency, QUEUE_CONCURRENCY_BOUNDS, setQueueConcurrency } from "../lib/concurrency";

export function SettingsPage() {
  const { remnawaveVersions, remnawaveLoading, refreshRemnawaveVersions } =
    useOutletContext<AppOutletContext>();
  const [secretKey, setSecretKey] = useState("");
  const [concurrency, setConcurrency] = useState(getQueueConcurrency);
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [msg, setMsg] = useState<string | null>(null);

  useEffect(() => {
    void api
      .getSettings()
      .then((s) => setSecretKey(s.remna_secret_key ?? ""))
      .catch((err) => setMsg(err instanceof Error ? err.message : "Не удалось загрузить настройки"))
      .finally(() => setLoading(false));
  }, []);

  async function save() {
    setBusy(true);
    setMsg(null);
    try {
      const s = await api.updateSettings({ remna_secret_key: secretKey });
      setSecretKey(s.remna_secret_key ?? "");
      setConcurrency(setQueueConcurrency(concurrency));
      setMsg("Настройки сохранены");
    } catch (err) {
      setMsg(err instanceof Error ? err.message : "Ошибка сохранения");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex h-full flex-col">
      <header className="border-b border-[var(--border)] px-4 py-4 sm:px-6">
        <h1 className="text-xl font-semibold tracking-tight">Настройки</h1>
        <p className="mt-0.5 text-sm text-[var(--muted)]">
          Общие параметры панели: SECRET_KEY Remnawave и параллелизм задач.
        </p>
      </header>

      <div className="flex-1 overflow-auto px-3 py-3 sm:px-6 sm:py-4">
        {loading ? (
          <p className="text-sm text-[var(--muted)]">Загрузка…</p>
        ) : (
          <div className="mx-auto max-w-xl space-y-4">
            <section className="rounded-[var(--radius)] border border-[var(--border)] bg-[var(--bg-elevated)] p-4">
              <h2 className="text-sm font-semibold">SECRET_KEY Remna</h2>
              <p className="mt-1 text-xs text-[var(--muted)]">
                Ключ панели Remnawave. Подставляется при установке RemnaNode, если в диалоге поле пустое.
              </p>
              <Field label="SECRET_KEY">
                <input
                  type="text"
                  value={secretKey}
                  onChange={(e) => setSecretKey(e.target.value)}
                  className={inputCls}
                  placeholder="Значение из панели Remnawave"
                  autoComplete="off"
                  spellCheck={false}
                />
              </Field>
            </section>

            <section className="rounded-[var(--radius)] border border-[var(--border)] bg-[var(--bg-elevated)] p-4">
              <h2 className="text-sm font-semibold">Параллелизм очередей</h2>
              <p className="mt-1 text-xs text-[var(--muted)]">
                Сколько агент-установок и RemnaNode-скриптов запускать одновременно (локально в
                браузере).
              </p>
              <Field label={`Одновременно (${QUEUE_CONCURRENCY_BOUNDS.min}–${QUEUE_CONCURRENCY_BOUNDS.max})`}>
                <input
                  type="number"
                  min={QUEUE_CONCURRENCY_BOUNDS.min}
                  max={QUEUE_CONCURRENCY_BOUNDS.max}
                  value={concurrency}
                  onChange={(e) => setConcurrency(Number(e.target.value) || QUEUE_CONCURRENCY_BOUNDS.default)}
                  className={inputCls}
                />
              </Field>
            </section>

            <section className="rounded-[var(--radius)] border border-[var(--border)] bg-[var(--bg-elevated)] p-4">
              <h2 className="text-sm font-semibold">Версии Remnawave</h2>
              <p className="mt-1 text-xs text-[var(--muted)]">
                Кэш GitHub releases (panel / node). Обновление принудительно опрашивает API.
              </p>
              <div className="mt-3 grid gap-1 text-sm">
                <div className="flex justify-between gap-3">
                  <span className="text-[var(--muted)]">Panel</span>
                  <span className="font-mono text-[var(--text)]">
                    {remnawaveVersions?.panel_version ?? "—"}
                  </span>
                </div>
                <div className="flex justify-between gap-3">
                  <span className="text-[var(--muted)]">Node</span>
                  <span className="font-mono text-[var(--text)]">
                    {remnawaveVersions?.node_version ?? "—"}
                  </span>
                </div>
                {remnawaveVersions?.checked_at && (
                  <div className="flex justify-between gap-3 text-xs text-[var(--muted)]">
                    <span>Проверено</span>
                    <span>{new Date(remnawaveVersions.checked_at * 1000).toLocaleString()}</span>
                  </div>
                )}
              </div>
              <button
                type="button"
                disabled={remnawaveLoading}
                onClick={() => void refreshRemnawaveVersions(true)}
                className="mt-3 rounded-lg border border-[var(--border)] px-3 py-1.5 text-sm text-[var(--text)] hover:bg-[var(--bg-row)] disabled:opacity-50"
              >
                {remnawaveLoading ? "Обновление…" : "Обновить с GitHub"}
              </button>
            </section>

            <div className="flex flex-wrap items-center gap-3">
              <button
                type="button"
                disabled={busy}
                onClick={() => void save()}
                className="rounded-lg bg-[var(--accent)] px-4 py-2 text-sm font-semibold text-[#06221e] disabled:opacity-50"
              >
                {busy ? "Сохранение…" : "Сохранить"}
              </button>
              {msg && <p className="text-xs text-[var(--muted)]">{msg}</p>}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="mt-3 grid gap-1 text-xs text-[var(--muted)] first:mt-0">
      <span>{label}</span>
      {children}
    </label>
  );
}

const inputCls =
  "w-full rounded-lg border border-[var(--border)] bg-[var(--bg)] px-3 py-2 text-sm text-[var(--text)] outline-none focus:border-[var(--accent)]";
