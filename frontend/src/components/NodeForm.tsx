import { useEffect, useState, type ReactNode } from "react";
import type { NodeFormValues, NodeItem } from "../types";

type Props = {
  open: boolean;
  initial?: NodeItem | null;
  busy?: boolean;
  error?: string | null;
  onClose: () => void;
  onSubmit: (values: NodeFormValues) => Promise<void>;
};

const empty: NodeFormValues = {
  name: "",
  host: "",
  ssh_port: 22,
  ssh_user: "root",
  auth_type: "password",
  password: "",
  private_key: "",
  provider: "",
  country_code: "",
  notes: "",
};

export function NodeForm({ open, initial, busy, error, onClose, onSubmit }: Props) {
  const [values, setValues] = useState<NodeFormValues>(empty);
  const editing = Boolean(initial);

  useEffect(() => {
    if (!open) return;
    if (initial) {
      setValues({
        name: initial.name,
        host: initial.host,
        ssh_port: initial.ssh_port,
        ssh_user: initial.ssh_user,
        auth_type: initial.auth_type,
        password: "",
        private_key: "",
        provider: initial.provider ?? "",
        country_code: initial.country_code ?? "",
        notes: initial.notes ?? "",
      });
    } else {
      setValues(empty);
    }
  }, [open, initial]);

  if (!open) return null;

  function set<K extends keyof NodeFormValues>(key: K, value: NodeFormValues[K]) {
    setValues((prev) => ({ ...prev, [key]: value }));
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/55 p-4 backdrop-blur-sm">
      <div className="w-full max-w-xl rounded-[var(--radius)] border border-[var(--border)] bg-[var(--bg-elevated)] shadow-2xl">
        <div className="flex items-center justify-between border-b border-[var(--border)] px-5 py-4">
          <h2 className="text-base font-semibold">{editing ? "Редактировать ноду" : "Добавить ноду"}</h2>
          <button type="button" onClick={onClose} className="text-[var(--muted)] hover:text-[var(--text)]">
            ✕
          </button>
        </div>

        <form
          className="grid gap-3 px-5 py-4"
          onSubmit={(e) => {
            e.preventDefault();
            void onSubmit(values);
          }}
        >
          <div className="grid grid-cols-2 gap-3">
            <Field label="Название">
              <input required value={values.name} onChange={(e) => set("name", e.target.value)} className={inputCls} />
            </Field>
            <Field label="Провайдер">
              <input value={values.provider} onChange={(e) => set("provider", e.target.value)} className={inputCls} />
            </Field>
          </div>

          <div className="grid grid-cols-[1fr_100px_90px] gap-3">
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
              <input
                maxLength={2}
                placeholder="DE"
                value={values.country_code}
                onChange={(e) => set("country_code", e.target.value.toUpperCase())}
                className={inputCls}
              />
            </Field>
          </div>

          <div className="grid grid-cols-2 gap-3">
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
            <Field label={editing ? "Пароль (оставьте пустым, чтобы не менять)" : "Пароль"}>
              <input
                type="password"
                required={!editing}
                value={values.password}
                onChange={(e) => set("password", e.target.value)}
                className={inputCls}
                autoComplete="new-password"
              />
            </Field>
          ) : (
            <Field label={editing ? "Приватный ключ (оставьте пустым, чтобы не менять)" : "Приватный ключ"}>
              <textarea
                required={!editing}
                rows={5}
                value={values.private_key}
                onChange={(e) => set("private_key", e.target.value)}
                className={`${inputCls} font-mono text-xs`}
              />
            </Field>
          )}

          <Field label="Заметки">
            <textarea rows={2} value={values.notes} onChange={(e) => set("notes", e.target.value)} className={inputCls} />
          </Field>

          {error && <div className="rounded-md border border-[rgba(240,113,120,0.35)] bg-[rgba(240,113,120,0.08)] px-3 py-2 text-sm text-[var(--danger)]">{error}</div>}

          <div className="flex justify-end gap-2 pt-2">
            <button type="button" onClick={onClose} className={btnGhost}>
              Отмена
            </button>
            <button type="submit" disabled={busy} className={btnPrimary}>
              {busy ? "Сохранение…" : "Сохранить"}
            </button>
          </div>
        </form>
      </div>
    </div>
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
  "rounded-lg bg-[var(--accent)] px-4 py-2 text-sm font-semibold text-[#06221e] transition hover:brightness-110 disabled:opacity-60";

const btnGhost =
  "rounded-lg border border-[var(--border)] px-4 py-2 text-sm text-[var(--muted)] transition hover:text-[var(--text)]";
