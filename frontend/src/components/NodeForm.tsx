import { useEffect, useMemo, useRef, useState, type ChangeEvent, type ReactNode } from "react";
import { api } from "../api/client";
import { COUNTRIES } from "../lib/countries";
import type { HostingItem, NodeFormValues, NodeItem } from "../types";
import { ConfirmDialog } from "./ConfirmDialog";
import { CountryFlag } from "./CountryFlag";
import { HostingLogo } from "./HostingLogo";
import { ResizableDialog } from "./ResizableDialog";

type Props = {
  open: boolean;
  initial?: NodeItem | null;
  hostings: HostingItem[];
  busy?: boolean;
  error?: string | null;
  onClose: () => void;
  onSubmit: (values: NodeFormValues) => Promise<void>;
  onHostingsChange: () => Promise<void>;
};

const empty: NodeFormValues = {
  name: "",
  host: "",
  ssh_port: 22,
  ssh_user: "root",
  auth_type: "password",
  password: "",
  private_key: "",
  private_key_passphrase: "",
  hosting_id: "",
  country_code: "",
  notes: "",
};

const NEW_HOSTING = "__new__";

export function NodeForm({
  open,
  initial,
  hostings,
  busy,
  error,
  onClose,
  onSubmit,
  onHostingsChange,
}: Props) {
  const [values, setValues] = useState<NodeFormValues>(empty);
  const [keyFileName, setKeyFileName] = useState<string | null>(null);
  const [hostingMode, setHostingMode] = useState<"pick" | "new">("pick");
  const [newHostingName, setNewHostingName] = useState("");
  const [newHostingUrl, setNewHostingUrl] = useState("");
  const [hostingBusy, setHostingBusy] = useState(false);
  const [localError, setLocalError] = useState<string | null>(null);
  const [resetOpen, setResetOpen] = useState(false);
  const [resetBusy, setResetBusy] = useState(false);
  const [resetMsg, setResetMsg] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const editing = Boolean(initial);
  const editId = initial?.id ?? null;
  const hasStoredPassword = Boolean(initial?.has_password);
  const hasStoredKey = Boolean(initial?.has_private_key);

  // Reset only when dialog opens / switches node — NOT on every nodes poll refresh
  // (otherwise password/key fields get wiped while the user is editing).
  useEffect(() => {
    if (!open) return;
    setKeyFileName(null);
    setHostingMode("pick");
    setNewHostingName("");
    setNewHostingUrl("");
    setLocalError(null);
    setResetOpen(false);
    setResetMsg(null);
    if (initial) {
      setValues({
        name: initial.name,
        host: initial.host,
        ssh_port: initial.ssh_port,
        ssh_user: initial.ssh_user,
        auth_type: initial.auth_type,
        password: "",
        private_key: "",
        private_key_passphrase: "",
        hosting_id: initial.hosting_id ?? "",
        country_code: initial.country_code ?? "",
        notes: initial.notes ?? "",
      });
    } else {
      setValues(empty);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- intentionally only open/editId
  }, [open, editId]);

  const selectedHosting = useMemo(
    () => hostings.find((h) => h.id === values.hosting_id) ?? null,
    [hostings, values.hosting_id],
  );

  if (!open) return null;

  function set<K extends keyof NodeFormValues>(key: K, value: NodeFormValues[K]) {
    setValues((prev) => ({ ...prev, [key]: value }));
  }

  async function onKeyFile(e: ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    const text = await file.text();
    set("private_key", text);
    set("auth_type", "private_key");
    setKeyFileName(file.name);
    e.target.value = "";
  }

  async function ensureHostingId(): Promise<string> {
    if (hostingMode !== "new") {
      return values.hosting_id;
    }
    const name = newHostingName.trim();
    if (!name) {
      throw new Error("Укажите название хостинга");
    }
    setHostingBusy(true);
    try {
      const created = await api.createHosting({
        name,
        website_url: newHostingUrl.trim() || null,
      });
      await onHostingsChange();
      setHostingMode("pick");
      set("hosting_id", created.id);
      setNewHostingName("");
      setNewHostingUrl("");
      return created.id;
    } finally {
      setHostingBusy(false);
    }
  }

  const isPpk =
    values.private_key.trimStart().startsWith("PuTTY-User-Key-File-") ||
    (keyFileName?.toLowerCase().endsWith(".ppk") ?? false);

  return (
    <ResizableDialog
      storageKey="node-form"
      defaultWidth={720}
      defaultHeight={800}
      minWidth={360}
      minHeight={440}
      zClass="z-50"
    >
        <div className="flex shrink-0 items-center justify-between border-b border-[var(--border)] px-4 py-4 sm:px-5">
          <h2 className="text-base font-semibold">{editing ? "Редактировать ноду" : "Добавить ноду"}</h2>
          <button type="button" onClick={onClose} className="text-[var(--muted)] hover:text-[var(--text)]">
            ✕
          </button>
        </div>

        <form
          className="grid min-h-0 flex-1 gap-3 overflow-y-auto px-4 py-4 sm:px-5"
          onSubmit={(e) => {
            e.preventDefault();
            setLocalError(null);
            void (async () => {
              try {
                const hostingId = await ensureHostingId();
                await onSubmit({ ...values, hosting_id: hostingId });
              } catch (err) {
                setLocalError(err instanceof Error ? err.message : "Ошибка");
              }
            })();
          }}
        >
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <Field label="Название">
              <input required value={values.name} onChange={(e) => set("name", e.target.value)} className={inputCls} />
            </Field>
            <div className="grid gap-1.5 text-xs text-[var(--muted)]">
              <span>Хостинг</span>
              {hostingMode === "pick" ? (
                <div className="flex items-center gap-2">
                  {selectedHosting && (
                    <HostingLogo
                      name={selectedHosting.name}
                      faviconData={selectedHosting.favicon_data}
                      size={22}
                    />
                  )}
                  <select
                    value={values.hosting_id}
                    onChange={(e) => {
                      if (e.target.value === NEW_HOSTING) {
                        setHostingMode("new");
                        return;
                      }
                      set("hosting_id", e.target.value);
                    }}
                    className={inputCls}
                  >
                    <option value="">— не выбран —</option>
                    {hostings.map((h) => (
                      <option key={h.id} value={h.id}>
                        {h.name}
                      </option>
                    ))}
                    <option value={NEW_HOSTING}>＋ Добавить хостинг…</option>
                  </select>
                </div>
              ) : (
                <div className="grid gap-2">
                  <input
                    autoFocus
                    placeholder="Название хостинга"
                    value={newHostingName}
                    onChange={(e) => setNewHostingName(e.target.value)}
                    className={inputCls}
                  />
                  <input
                    placeholder="https://сайт-хостинга (опционально)"
                    value={newHostingUrl}
                    onChange={(e) => setNewHostingUrl(e.target.value)}
                    className={inputCls}
                  />
                  <button
                    type="button"
                    className={`${btnGhost} justify-self-start`}
                    onClick={() => {
                      setHostingMode("pick");
                      setNewHostingName("");
                      setNewHostingUrl("");
                    }}
                  >
                    Отмена
                  </button>
                </div>
              )}
            </div>
          </div>

          <div className="grid grid-cols-1 gap-3 sm:grid-cols-[1fr_100px_minmax(140px,1fr)]">
            <Field label="Host / IP">
              <input required value={values.host} onChange={(e) => set("host", e.target.value)} className={inputCls} />
            </Field>
            <Field label="SSH port">
              <input
                type="number"
                min={1}
                max={65535}
                value={values.ssh_port}
                onChange={(e) => set("ssh_port", Number(e.target.value))}
                className={inputCls}
              />
            </Field>
            <Field label="Страна">
              <div className="flex items-center gap-2">
                {values.country_code && <CountryFlag code={values.country_code} size={16} />}
                <select
                  value={values.country_code}
                  onChange={(e) => set("country_code", e.target.value)}
                  className={`${inputCls} min-w-0 flex-1`}
                >
                  <option value="">— не указана —</option>
                  {COUNTRIES.map((c) => (
                    <option key={c.code} value={c.code}>
                      {c.name}
                    </option>
                  ))}
                  {/* keep unknown legacy code selectable until changed */}
                  {values.country_code &&
                    !COUNTRIES.some((c) => c.code === values.country_code) && (
                      <option value={values.country_code}>{values.country_code}</option>
                    )}
                </select>
              </div>
            </Field>
          </div>

          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <Field label="SSH user">
              <input value={values.ssh_user} onChange={(e) => set("ssh_user", e.target.value)} className={inputCls} />
            </Field>
            <Field label="Тип доступа">
              <select
                value={values.auth_type}
                onChange={(e) => set("auth_type", e.target.value as NodeFormValues["auth_type"])}
                className={inputCls}
              >
                <option value="password">Пароль</option>
                <option value="private_key">SSH ключ</option>
              </select>
            </Field>
          </div>

          {values.auth_type === "password" ? (
            <Field label={editing ? "Пароль (пусто = не менять)" : "Пароль"}>
              {editing && hasStoredPassword && (
                <p className="mb-1 text-[11px] text-[var(--success)]">Пароль сохранён в панели — пустое поле его не удалит.</p>
              )}
              <input
                type="password"
                name="node-ssh-password"
                required={!editing}
                value={values.password}
                onChange={(e) => set("password", e.target.value)}
                className={inputCls}
                autoComplete="off"
                placeholder={editing && hasStoredPassword ? "••••••••" : undefined}
              />
            </Field>
          ) : (
            <div className="grid gap-3">
              <div className="flex flex-wrap items-center gap-2">
                <input
                  ref={fileRef}
                  type="file"
                  accept=".ppk,.pem,.key,.txt,application/x-pem-file,text/plain"
                  className="hidden"
                  onChange={(e) => void onKeyFile(e)}
                />
                <button type="button" className={btnGhost} onClick={() => fileRef.current?.click()}>
                  Загрузить ключ (.ppk / OpenSSH)
                </button>
                {keyFileName && <span className="text-xs text-[var(--accent)]">{keyFileName}</span>}
                {editing && hasStoredKey && !values.private_key && !keyFileName && (
                  <span className="text-xs text-[var(--success)]">Ключ сохранён в панели</span>
                )}
              </div>
              <p className="text-[11px] text-[var(--muted)]">
                PuTTY `.ppk` конвертируется на сервере в OpenSSH. При редактировании пустое поле ключ не удаляет.
              </p>
              <Field label={editing ? "Приватный ключ (пусто = не менять)" : "Приватный ключ"}>
                <textarea
                  required={!editing}
                  name="node-ssh-private-key"
                  rows={5}
                  value={values.private_key}
                  onChange={(e) => {
                    set("private_key", e.target.value);
                    setKeyFileName(null);
                  }}
                  className={`${inputCls} font-mono text-xs`}
                  placeholder={
                    editing && hasStoredKey
                      ? "Ключ уже сохранён — вставьте новый, только если хотите заменить"
                      : "-----BEGIN ... PRIVATE KEY----- или содержимое .ppk"
                  }
                  autoComplete="off"
                />
              </Field>
              {editing && hasStoredPassword && (
                <Field label="Пароль для sudo (пусто = не менять)">
                  <p className="mb-1 text-[11px] text-[var(--success)]">Пароль сохранён — используется для sudo при установке агента.</p>
                  <input
                    type="password"
                    name="node-ssh-sudo-password"
                    value={values.password}
                    onChange={(e) => set("password", e.target.value)}
                    className={inputCls}
                    autoComplete="off"
                    placeholder="••••••••"
                  />
                </Field>
              )}
              {isPpk && (
                <Field label="Passphrase PPK (если ключ с паролем)">
                  <input
                    type="password"
                    name="node-ssh-ppk-passphrase"
                    value={values.private_key_passphrase}
                    onChange={(e) => set("private_key_passphrase", e.target.value)}
                    className={inputCls}
                    autoComplete="off"
                  />
                </Field>
              )}
            </div>
          )}

          <Field label="Заметки">
            <textarea rows={2} value={values.notes} onChange={(e) => set("notes", e.target.value)} className={inputCls} />
          </Field>

          {editing && (
            <div className="mt-1 rounded-lg border border-[rgba(240,113,120,0.3)] bg-[rgba(240,113,120,0.05)] px-3 py-3">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div className="min-w-0">
                  <p className="text-sm font-medium text-[var(--text)]">Статистика ноды</p>
                  <p className="mt-0.5 text-[11px] leading-relaxed text-[var(--muted)]">
                    Сотрёт всю накопленную историю: пинг, CPU, RAM, диск, канал, онлайн и доступность.
                    Ноду, доступы и заметки не трогает. Отменить нельзя, сбор начнётся заново.
                  </p>
                </div>
                <button
                  type="button"
                  disabled={resetBusy}
                  onClick={() => {
                    setResetMsg(null);
                    setResetOpen(true);
                  }}
                  className="shrink-0 rounded-lg border border-[rgba(240,113,120,0.45)] px-3 py-2 text-sm text-[var(--danger)] transition hover:bg-[rgba(240,113,120,0.12)] disabled:opacity-60"
                >
                  Сбросить статистику
                </button>
              </div>
              {resetMsg && <p className="mt-2 text-[11px] text-[var(--success)]">{resetMsg}</p>}
            </div>
          )}

          {(error || localError) && (
            <div className="rounded-md border border-[rgba(240,113,120,0.35)] bg-[rgba(240,113,120,0.08)] px-3 py-2 text-sm text-[var(--danger)]">
              {localError || error}
            </div>
          )}

          <div className="flex flex-col-reverse gap-2 pt-2 sm:flex-row sm:justify-end sm:pr-2">
            <button type="button" onClick={onClose} className={btnGhost}>
              Отмена
            </button>
            <button type="submit" disabled={busy || hostingBusy} className={btnPrimary}>
              {busy || hostingBusy ? "Сохранение…" : "Сохранить"}
            </button>
          </div>
        </form>
        <ConfirmDialog
          open={resetOpen}
          danger
          title="Сбросить статистику ноды?"
          message={`Будет удалена вся история метрик ноды «${initial?.name ?? ""}». Ноду, доступы и заметки это не затронет. Отменить нельзя.`}
          confirmLabel="Сбросить"
          busy={resetBusy}
          busyLabel="Сброс…"
          zClass="z-[80]"
          onCancel={() => {
            if (!resetBusy) setResetOpen(false);
          }}
          onConfirm={() => {
            if (!editId) return;
            void (async () => {
              setResetBusy(true);
              setLocalError(null);
              try {
                const r = await api.resetNodeMetrics(editId);
                setResetMsg(`Удалено точек: ${r.deleted}`);
                setResetOpen(false);
              } catch (err) {
                setLocalError(err instanceof Error ? err.message : "Не удалось сбросить статистику");
                setResetOpen(false);
              } finally {
                setResetBusy(false);
              }
            })();
          }}
        />
    </ResizableDialog>
  );
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="grid gap-1.5 text-xs text-[var(--muted)]">
      <span>{label}</span>
      {children}
    </label>
  );
}

const inputCls =
  "w-full rounded-lg border border-[var(--border)] bg-[var(--bg)] px-3 py-2 text-sm text-[var(--text)] outline-none transition focus:border-[var(--accent)]";

const btnPrimary =
  "rounded-lg bg-[var(--accent)] px-4 py-2.5 text-sm font-semibold text-[#06221e] transition hover:brightness-110 disabled:opacity-60 sm:py-2";

const btnGhost =
  "rounded-lg border border-[var(--border)] px-4 py-2.5 text-sm text-[var(--muted)] transition hover:text-[var(--text)] sm:py-2";
