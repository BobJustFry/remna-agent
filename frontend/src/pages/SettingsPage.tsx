import { useEffect, useMemo, useState, type ReactNode } from "react";
import { Link, useOutletContext } from "react-router-dom";
import { api, type ApiToken } from "../api/client";
import { NodeWatchSettings } from "../components/NodeWatchSettings";
import { SubwatchSettings } from "../components/SubwatchSettings";
import { FleetReportSettings } from "../components/settings/FleetReportSettings";
import { YcwatchSettings } from "../components/YcwatchSettings";
import type { AppOutletContext } from "../components/AppShell";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { CopyButton } from "../components/CopyButton";
import { SaveBar } from "../components/settings/SaveBar";
import { SettingsSaveProvider, useSettingsBlock, useSettingsSave } from "../components/settings/SaveContext";
import { MemWatchSettings } from "../components/settings/MemWatchSettings";
import { SharingSettings } from "../components/settings/SharingSettings";
import { TelegramSettings } from "../components/settings/TelegramSettings";
import { BTN, Card, Hint, INPUT, NumField, TextField, useDraft } from "../components/settings/ui";
import { getQueueConcurrency, QUEUE_CONCURRENCY_BOUNDS, setQueueConcurrency } from "../lib/concurrency";

const TABS = [
  { id: "telegram", label: "Уведомления" },
  { id: "sharing", label: "Шаринг" },
  { id: "memory", label: "Память" },
  { id: "nodes", label: "Ноды" },
  { id: "cloud", label: "Облако" },
  { id: "subscription", label: "Подписка" },
  { id: "access", label: "Доступы" },
] as const;
type TabId = (typeof TABS)[number]["id"];

const TAB_KEY = "remna-agent.settings.tab";

function loadTab(): TabId {
  try {
    const v = localStorage.getItem(TAB_KEY);
    if (TABS.some((t) => t.id === v)) return v as TabId;
  } catch {
    /* localStorage недоступен — берём первую вкладку */
  }
  return "telegram";
}

export function SettingsPage() {
  return (
    <SettingsSaveProvider>
      <SettingsInner />
    </SettingsSaveProvider>
  );
}

function SettingsInner() {
  const { nodes, refreshNodeWatch } = useOutletContext<AppOutletContext>();
  const { dirtyByTab } = useSettingsSave();
  const [tab, setTab] = useState<TabId>(loadTab);

  function pick(id: TabId) {
    setTab(id);
    try {
      localStorage.setItem(TAB_KEY, id);
    } catch {
      /* не страшно */
    }
  }

  // Все вкладки смонтированы (неактивные скрыты), чтобы несохранённые правки не терялись при переключении.
  const panels: Record<TabId, ReactNode> = {
    telegram: (
      <div className="space-y-4">
        <TelegramSettings />
        <FleetReportSettings />
      </div>
    ),
    sharing: <SharingSettings />,
    memory: (
      <div className="space-y-4">
        <MemWatchSettings />
        <LinkCard
          title="Память ядер: режим и журнал"
          to="/memory"
          text="Режим (сухой прогон / боевой), таблица нод и журнал — на странице «Память». Чат и часовой пояс — на вкладке «Уведомления»."
        />
      </div>
    ),
    nodes: <NodeWatchSettings nodes={nodes} onChanged={() => void refreshNodeWatch()} />,
    cloud: <YcwatchSettings />,
    subscription: <SubwatchSettings />,
    access: <AccessTab />,
  };

  return (
    <div className="flex h-full min-h-0 flex-col overflow-hidden">
      <header className="border-b border-[var(--border)] px-4 pt-3 sm:px-6 sm:pt-4">
        <h1 className="hidden text-xl font-semibold tracking-tight lg:block">Настройки</h1>
        <div role="tablist" className="-mx-1 mt-2 flex gap-1 overflow-x-auto pb-2 lg:mt-3">
          {TABS.map((t) => {
            const n = dirtyByTab[t.id] ?? 0;
            const active = t.id === tab;
            return (
              <button
                key={t.id}
                type="button"
                role="tab"
                aria-selected={active}
                onClick={() => pick(t.id)}
                className={`shrink-0 whitespace-nowrap rounded-lg px-3 py-1.5 text-sm ${
                  active
                    ? "bg-[var(--accent)] font-semibold text-[#06221e]"
                    : "text-[var(--muted)] hover:bg-[var(--bg-row)] hover:text-[var(--text)]"
                }`}
              >
                {t.label}
                {n > 0 && <span className={active ? "ml-1" : "ml-1 text-[var(--warning)]"}>●</span>}
              </button>
            );
          })}
        </div>
      </header>

      <div className="flex-1 overflow-auto px-3 py-3 sm:px-6 sm:py-4">
        <div className="mx-auto max-w-3xl">
          {TABS.map((t) => (
            <div key={t.id} role="tabpanel" hidden={t.id !== tab}>
              {panels[t.id]}
            </div>
          ))}
        </div>
      </div>

      <SaveBar />
    </div>
  );
}

function LinkCard({ title, text, to }: { title: string; text: string; to: string }) {
  return (
    <Card title={title}>
      <p className="text-sm text-[var(--muted)]">{text}</p>
      <Link to={to} className={`${BTN} inline-block`}>
        Открыть страницу →
      </Link>
    </Card>
  );
}

function AccessTab() {
  const { remnawaveVersions, remnawaveLoading, refreshRemnawaveVersions } =
    useOutletContext<AppOutletContext>();
  const [saved, setSaved] = useState<{ secret_key: string; concurrency: number } | null>(null);
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
    const conc = getQueueConcurrency();
    void api
      .getSettings()
      .then((s) => setSaved({ secret_key: s.remna_secret_key ?? "", concurrency: conc }))
      .catch((err) => {
        setMsg(err instanceof Error ? err.message : "Не удалось загрузить настройки");
      });
    void api
      .listApiTokens()
      .then(setTokens)
      .catch((err) => setTokenMsg(err instanceof Error ? err.message : "Не удалось загрузить токены"));
  }, []);

  const savedMemo = useMemo(() => saved, [saved]);
  const { draft, set, changed, dirty, reset } = useDraft(savedMemo);

  useSettingsBlock("access", {
    label: "Доступы",
    tab: "access",
    dirty,
    reset,
    save: async () => {
      if (!draft || !saved) return;
      let secret = saved.secret_key;
      if ("secret_key" in changed) {
        const s = await api.updateSettings({ remna_secret_key: draft.secret_key });
        secret = s.remna_secret_key ?? "";
      }
      const conc = "concurrency" in changed ? setQueueConcurrency(draft.concurrency) : saved.concurrency;
      setSaved({ secret_key: secret, concurrency: conc });
    },
  });

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

  return (
    <div className="space-y-4">
      <Card title="Ключ Remna и очереди">
        {draft ? (
          <TextField
            label="SECRET_KEY"
            hint="Ключ панели Remnawave. Подставляется при установке RemnaNode, если в диалоге поле пустое."
            value={draft.secret_key}
            onChange={(v) => set("secret_key", v)}
            placeholder="Значение из панели Remnawave"
            mono={false}
          />
        ) : (
          <p className="text-sm text-[var(--muted)]">{msg ?? "Загрузка…"}</p>
        )}
        {draft && (
          <NumField
            label={`Параллельных установок (${QUEUE_CONCURRENCY_BOUNDS.min}–${QUEUE_CONCURRENCY_BOUNDS.max})`}
            hint="Сколько агент-установок и RemnaNode-скриптов запускать одновременно. Хранится локально в браузере."
            value={draft.concurrency}
            min={QUEUE_CONCURRENCY_BOUNDS.min}
            max={QUEUE_CONCURRENCY_BOUNDS.max}
            onChange={(v) => set("concurrency", v || QUEUE_CONCURRENCY_BOUNDS.default)}
          />
        )}
      </Card>

      <Card title="API-токены">
        <p className="text-xs text-[var(--muted)]">
          Для скриптов: <code className="font-mono">Authorization: Bearer &lt;токен&gt;</code>
          <Hint>
            Токен показывается один раз при выпуске; в базе хранится только его хеш. Управлять токенами
            можно только из браузера — сам токен выпускать другие токены не может.
          </Hint>
        </p>
        <div className="flex flex-wrap items-end gap-2">
          <label className="grid min-w-48 flex-1 gap-1 text-xs text-[var(--muted)]">
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
              className={INPUT}
              placeholder="probe-tools на домашнем ПК"
              maxLength={64}
              autoComplete="off"
              spellCheck={false}
            />
          </label>
          <button type="button" disabled={tokenBusy || !tokenName.trim()} onClick={() => void createToken()} className={BTN}>
            {tokenBusy ? "Выпуск…" : "Выпустить"}
          </button>
        </div>
        {freshToken && (
          <div className="rounded-lg border border-[rgba(66,184,131,0.45)] bg-[rgba(66,184,131,0.08)] p-3">
            <p className="text-xs text-[var(--muted)]">
              Токен «{freshToken.name}». Скопируй сейчас — после ухода со страницы он больше не покажется.
            </p>
            <div className="mt-2 flex items-center gap-2">
              <code className="min-w-0 flex-1 break-all font-mono text-xs text-[var(--text)]">{freshToken.token}</code>
              <CopyButton value={freshToken.token} title="Скопировать токен" />
            </div>
          </div>
        )}
        {tokens.length > 0 ? (
          <ul className="divide-y divide-[var(--border)] text-sm">
            {tokens.map((t) => (
              <li key={t.id} className="flex flex-wrap items-center gap-x-3 gap-y-1 py-2">
                <span className="font-medium">{t.name}</span>
                <span className="font-mono text-xs text-[var(--muted)]">{t.prefix}…</span>
                <span className="text-xs text-[var(--muted)]">
                  выпущен {new Date(t.created_at).toLocaleDateString()}
                  {t.last_used_at ? ` · использован ${new Date(t.last_used_at).toLocaleString()}` : " · не использовался"}
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
          <p className="text-xs text-[var(--muted)]">Токенов пока нет.</p>
        )}
        {tokenMsg && <p className="text-xs text-[var(--danger)]">{tokenMsg}</p>}
      </Card>

      <Card title="Версии Remnawave">
        <div className="grid gap-1 text-sm">
          <div className="flex justify-between gap-3">
            <span className="text-[var(--muted)]">Panel</span>
            <span className="font-mono">{remnawaveVersions?.panel_version ?? "—"}</span>
          </div>
          <div className="flex justify-between gap-3">
            <span className="text-[var(--muted)]">Node</span>
            <span className="font-mono">{remnawaveVersions?.node_version ?? "—"}</span>
          </div>
          {remnawaveVersions?.checked_at && (
            <div className="flex justify-between gap-3 text-xs text-[var(--muted)]">
              <span>Проверено</span>
              <span>{new Date(remnawaveVersions.checked_at * 1000).toLocaleString()}</span>
            </div>
          )}
        </div>
        <button type="button" disabled={remnawaveLoading} onClick={() => void refreshRemnawaveVersions(true)} className={BTN}>
          {remnawaveLoading ? "Обновление…" : "Обновить с GitHub"}
        </button>
      </Card>

      <Card title="Данные графиков">
        <p className="text-xs text-[var(--muted)]">
          Стирает историю cf_204 / CPU / RAM / диска по всем нодам.
          <Hint>
            Ноды, SSH и настройки не трогает. После сброса графики начнут писаться заново (cf_204 — с агента
            0.1.14+).
          </Hint>
        </p>
        <button
          type="button"
          disabled={resetBusy}
          onClick={() => setResetOpen(true)}
          className="rounded-lg border border-[rgba(240,113,120,0.45)] px-3 py-1.5 text-sm font-semibold text-[var(--danger)] hover:bg-[rgba(240,113,120,0.12)] disabled:opacity-50"
        >
          Сбросить накопленные данные
        </button>
        {msg && <p className="text-xs text-[var(--muted)]">{msg}</p>}
      </Card>

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
