import { useEffect, useState, type ReactNode } from "react";
import { useOutletContext } from "react-router-dom";
import { api, type ApiToken } from "../api/client";
import { NodeWatchSettings } from "../components/NodeWatchSettings";
import { SubwatchSettings } from "../components/SubwatchSettings";
import type { AppOutletContext } from "../components/AppShell";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { CopyButton } from "../components/CopyButton";
import { getQueueConcurrency, QUEUE_CONCURRENCY_BOUNDS, setQueueConcurrency } from "../lib/concurrency";

export function SettingsPage() {
  const { nodes, remnawaveVersions, remnawaveLoading, refreshRemnawaveVersions, refreshNodeWatch } =
    useOutletContext<AppOutletContext>();
  const [secretKey, setSecretKey] = useState("");
  const [concurrency, setConcurrency] = useState(getQueueConcurrency);
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [msg, setMsg] = useState<string | null>(null);
  const [resetOpen, setResetOpen] = useState(false);
  const [resetBusy, setResetBusy] = useState(false);
  const [tokens, setTokens] = useState<ApiToken[]>([]);
  const [tokenName, setTokenName] = useState("");
  const [tokenBusy, setTokenBusy] = useState(false);
  const [tokenMsg, setTokenMsg] = useState<string | null>(null);
  // Свежевыпущенный токен: показывается один раз, до ухода со страницы.
  const [freshToken, setFreshToken] = useState<{ name: string; token: string } | null>(null);
  const [revokeTarget, setRevokeTarget] = useState<ApiToken | null>(null);
  const [revokeBusy, setRevokeBusy] = useState(false);

  useEffect(() => {
    void api
      .getSettings()
      .then((s) => setSecretKey(s.remna_secret_key ?? ""))
      .catch((err) => setMsg(err instanceof Error ? err.message : "Не удалось загрузить настройки"))
      .finally(() => setLoading(false));
    void api
      .listApiTokens()
      .then(setTokens)
      .catch((err) => setTokenMsg(err instanceof Error ? err.message : "Не удалось загрузить токены"));
  }, []);

  async function createToken() {
    const name = tokenName.trim();
    if (!name) return;
    setTokenBusy(true);
    setTokenMsg(null);
    try {
      const created = await api.createApiToken(name);
      setFreshToken({ name: created.name, token: created.token });
      setTokens((prev) => [created, ...prev]);
      setTokenName("");
    } catch (err) {
      setTokenMsg(err instanceof Error ? err.message : "Не удалось выпустить токен");
    } finally {
      setTokenBusy(false);
    }
  }

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
    <div className="flex h-full min-h-0 flex-col overflow-hidden">
      <header className="border-b border-[var(--border)] px-4 py-3 sm:px-6 sm:py-4">
        <h1 className="hidden text-xl font-semibold tracking-tight lg:block">Настройки</h1>
        <p className="text-sm text-[var(--muted)] lg:mt-0.5">
          Общие параметры панели: SECRET_KEY Remnawave, API-токены и параллелизм задач.
        </p>
      </header>

      <div className="flex-1 overflow-auto px-3 py-3 sm:px-6 sm:py-4">
        {loading ? (
          <p className="text-sm text-[var(--muted)]">Загрузка…</p>
        ) : (
          <div className="mx-auto max-w-xl space-y-4">
            <section className="rounded-[var(--radius)] border border-[var(--border)] bg-[var(--bg-elevated)] p-4">
              <h2 className="text-sm font-semibold">Данные графиков</h2>
              <p className="mt-1 text-xs text-[var(--muted)]">
                Стирает накопленную историю cf_204 / CPU / RAM / диск по всем нодам. Ноды, SSH и
                настройки не трогает. После сброса графики начнут писаться заново (cf_204 — с агента
                0.1.14+).
              </p>
              <button
                type="button"
                disabled={busy || resetBusy}
                onClick={() => setResetOpen(true)}
                className="mt-3 rounded-lg border border-[rgba(240,113,120,0.45)] px-3 py-1.5 text-sm font-semibold text-[var(--danger)] hover:bg-[rgba(240,113,120,0.12)] disabled:opacity-50"
              >
                Сбросить накопленные данные
              </button>
            </section>

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

            <SubwatchSettings />

            <NodeWatchSettings nodes={nodes} onChanged={() => void refreshNodeWatch()} />

            <section className="rounded-[var(--radius)] border border-[var(--border)] bg-[var(--bg-elevated)] p-4">
              <h2 className="text-sm font-semibold">API-токены</h2>
              <p className="mt-1 text-xs text-[var(--muted)]">
                Доступ к API панели для скриптов: заголовок{" "}
                <code className="font-mono">Authorization: Bearer &lt;токен&gt;</code>. Токен показывается
                один раз при выпуске; в базе хранится только его хеш. Управлять токенами можно только из
                браузера — сам токен выпускать другие токены не может.
              </p>
              <div className="mt-3 flex flex-wrap items-end gap-2">
                <label className="grid flex-1 gap-1 text-xs text-[var(--muted)]">
                  <span>Название (для чего токен)</span>
                  <input
                    type="text"
                    value={tokenName}
                    onChange={(e) => setTokenName(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter") {
                        e.preventDefault();
                        void createToken();
                      }
                    }}
                    className={inputCls}
                    placeholder="probe-tools на домашнем ПК"
                    maxLength={64}
                    autoComplete="off"
                    spellCheck={false}
                  />
                </label>
                <button
                  type="button"
                  disabled={tokenBusy || !tokenName.trim()}
                  onClick={() => void createToken()}
                  className="rounded-lg border border-[var(--border)] px-3 py-2 text-sm text-[var(--text)] hover:bg-[var(--bg-row)] disabled:opacity-50"
                >
                  {tokenBusy ? "Выпуск…" : "Выпустить"}
                </button>
              </div>
              {freshToken && (
                <div className="mt-3 rounded-lg border border-[rgba(66,184,131,0.45)] bg-[rgba(66,184,131,0.08)] p-3">
                  <p className="text-xs text-[var(--muted)]">
                    Токен «{freshToken.name}». Скопируй сейчас — после ухода со страницы он больше не
                    покажется.
                  </p>
                  <div className="mt-2 flex items-center gap-2">
                    <code className="min-w-0 flex-1 break-all font-mono text-xs text-[var(--text)]">
                      {freshToken.token}
                    </code>
                    <CopyButton value={freshToken.token} title="Скопировать токен" />
                  </div>
                </div>
              )}
              {tokens.length > 0 ? (
                <ul className="mt-3 divide-y divide-[var(--border)] text-sm">
                  {tokens.map((t) => (
                    <li key={t.id} className="flex flex-wrap items-center gap-x-3 gap-y-1 py-2">
                      <span className="font-medium">{t.name}</span>
                      <span className="font-mono text-xs text-[var(--muted)]">{t.prefix}…</span>
                      <span className="text-xs text-[var(--muted)]">
                        выпущен {new Date(t.created_at).toLocaleDateString()}
                        {t.last_used_at
                          ? ` · использован ${new Date(t.last_used_at).toLocaleString()}`
                          : " · не использовался"}
                      </span>
                      <button
                        type="button"
                        onClick={() => setRevokeTarget(t)}
                        className="ml-auto rounded-md px-2 py-1 text-xs text-[var(--danger)] hover:bg-[rgba(240,113,120,0.12)]"
                      >
                        Отозвать
                      </button>
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="mt-3 text-xs text-[var(--muted)]">Токенов пока нет.</p>
              )}
              {tokenMsg && <p className="mt-2 text-xs text-[var(--danger)]">{tokenMsg}</p>}
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
      <ConfirmDialog
        open={revokeTarget !== null}
        title="Отозвать токен?"
        message={`Токен «${revokeTarget?.name ?? ""}» перестанет работать сразу. Скрипты, которые им пользуются, получат 401.`}
        confirmLabel="Отозвать"
        busy={revokeBusy}
        busyLabel="Отзыв…"
        danger
        zClass="z-[80]"
        onCancel={() => {
          if (!revokeBusy) setRevokeTarget(null);
        }}
        onConfirm={() => {
          const target = revokeTarget;
          if (!target) return;
          void (async () => {
            setRevokeBusy(true);
            setTokenMsg(null);
            try {
              await api.revokeApiToken(target.id);
              setTokens((prev) => prev.filter((t) => t.id !== target.id));
              if (freshToken?.name === target.name) setFreshToken(null);
              setRevokeTarget(null);
            } catch (err) {
              setTokenMsg(err instanceof Error ? err.message : "Не удалось отозвать токен");
            } finally {
              setRevokeBusy(false);
            }
          })();
        }}
      />
      <ConfirmDialog
        open={resetOpen}
        title="Сбросить графики?"
        message="Будут удалены все накопленные точки cf_204, CPU, RAM и диска. Это нельзя отменить. Ноды и учётные данные останутся."
        confirmLabel="Сбросить"
        busy={resetBusy}
        busyLabel="Сброс…"
        zClass="z-[80]"
        onCancel={() => {
          if (!resetBusy) setResetOpen(false);
        }}
        onConfirm={() => {
          void (async () => {
            setResetBusy(true);
            setMsg(null);
            try {
              const r = await api.resetMetrics();
              setMsg(`Удалено точек: ${r.deleted}`);
              setResetOpen(false);
            } catch (err) {
              setMsg(err instanceof Error ? err.message : "Не удалось сбросить данные");
            } finally {
              setResetBusy(false);
            }
          })();
        }}
      />
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
