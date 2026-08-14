import { useEffect, useState, type ReactNode } from "react";
import { api, type RemnaScriptAction, type RemnaScriptRunBody } from "../api/client";
import type { NodeItem } from "../types";

type Props = {
  nodes: NodeItem[];
  action: RemnaScriptAction;
  onClose: () => void;
  onConfirm: (body: RemnaScriptRunBody) => void;
};

const fallbackDefaults: RemnaScriptRunBody = {
  action: "install",
  node_port: 2222,
  additional_ports: "",
  mtu_ddos: true,
  gaming: true,
  swap: true,
  swap_size: "1G",
  cache_size: "1G",
  disable_ipv6: true,
  use_origin: false,
  origin_domain: "",
  tune_mtu: "skip",
  tune_gaming: "skip",
  tune_swap: "skip",
  tune_ports: false,
  tune_ipv6: "skip",
  skip_system_update: true,
};

export function RemnaScriptDialog({ nodes, action, onClose, onConfirm }: Props) {
  const [form, setForm] = useState<RemnaScriptRunBody>({ ...fallbackDefaults, action });
  const [secret, setSecret] = useState("");
  const [hasStoredSecret, setHasStoredSecret] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setForm({ ...fallbackDefaults, action });
    setSecret("");
    setError(null);
    setHasStoredSecret(false);
    void api
      .getSettings()
      .then((s) => {
        const d = s.defaults;
        setHasStoredSecret(Boolean(s.remna_secret_key?.trim()));
        setForm({
          ...fallbackDefaults,
          action,
          node_port: d.node_port,
          additional_ports: d.additional_ports ?? "",
          mtu_ddos: d.mtu_ddos,
          gaming: d.gaming,
          swap: d.swap,
          swap_size: d.swap_size,
          cache_size: d.cache_size,
          disable_ipv6: d.disable_ipv6,
          use_origin: d.use_origin,
          origin_domain: d.origin_domain ?? "",
          skip_system_update: d.skip_system_update,
        });
      })
      .catch(() => undefined);
  }, [action, nodes]);

  if (nodes.length === 0) return null;
  const bulk = nodes.length > 1;
  const title =
    action === "install"
      ? bulk
        ? `Установить RemnaNode на ${nodes.length} нод?`
        : "Установить RemnaNode?"
      : action === "reinstall"
        ? bulk
          ? `Переустановить RemnaNode на ${nodes.length} нод?`
          : "Переустановить RemnaNode?"
        : bulk
          ? `Настроить параметры на ${nodes.length} нод?`
          : "Настроить параметры ноды?";

  function submit() {
    if (action !== "tune" && !secret.trim() && !hasStoredSecret) {
      setError("Нужен SECRET_KEY — введите здесь или сохраните в «Настройки»");
      return;
    }
    onConfirm({
      ...form,
      action,
      secret_key: secret.trim() || null,
      origin_domain: form.use_origin ? form.origin_domain || null : null,
    });
  }

  return (
    <div className="fixed inset-0 z-[60] flex items-end justify-center bg-black/60 p-0 backdrop-blur-sm sm:items-center sm:p-4">
      <div className="flex max-h-[min(100dvh,900px)] w-full max-w-lg flex-col overflow-hidden rounded-t-[var(--radius)] border border-[var(--border)] bg-[var(--bg-elevated)] shadow-2xl sm:rounded-[var(--radius)]">
        <div className="border-b border-[var(--border)] px-5 py-4">
          <h2 className="text-base font-semibold">{title}</h2>
          {!bulk && (
            <p className="mt-1 font-mono text-xs text-[var(--muted)]">
              {nodes[0].name} · {nodes[0].host}
            </p>
          )}
        </div>

        <div className="space-y-3 overflow-y-auto px-5 py-4 text-sm">
          {bulk && (
            <div className="max-h-28 overflow-auto rounded-lg border border-[var(--border)] bg-[var(--bg)] px-3 py-2 text-xs">
              {nodes.map((n) => (
                <div key={n.id} className="flex justify-between gap-2 py-0.5">
                  <span className="truncate text-[var(--text)]">{n.name}</span>
                  <span className="font-mono text-[var(--muted)]">{n.host}</span>
                </div>
              ))}
            </div>
          )}

          {action !== "tune" && (
            <>
              <Field label="NODE_PORT">
                <input
                  type="number"
                  min={1}
                  max={65535}
                  value={form.node_port}
                  onChange={(e) => setForm((f) => ({ ...f, node_port: Number(e.target.value) || 2222 }))}
                  className={inputCls}
                />
              </Field>
              <Field label={`SECRET_KEY${hasStoredSecret ? " (пусто = из настроек)" : ""}`}>
                <input
                  type="text"
                  value={secret}
                  onChange={(e) => setSecret(e.target.value)}
                  className={inputCls}
                  placeholder={hasStoredSecret ? "Из раздела «Настройки»" : "Обязательно"}
                  autoComplete="off"
                  spellCheck={false}
                />
              </Field>
              <Field label="Доп. порты (через запятую)">
                <input
                  value={form.additional_ports}
                  onChange={(e) => setForm((f) => ({ ...f, additional_ports: e.target.value }))}
                  className={inputCls}
                  placeholder="опционально"
                />
              </Field>
              <div className="grid grid-cols-2 gap-2">
                <Field label="Cache (tmpfs)">
                  <input
                    value={form.cache_size}
                    onChange={(e) => setForm((f) => ({ ...f, cache_size: e.target.value }))}
                    className={inputCls}
                  />
                </Field>
                <Field label="Swap">
                  <input
                    value={form.swap_size}
                    onChange={(e) => setForm((f) => ({ ...f, swap_size: e.target.value }))}
                    className={inputCls}
                    disabled={!form.swap}
                  />
                </Field>
              </div>
              <Toggle checked={!!form.mtu_ddos} onChange={(v) => setForm((f) => ({ ...f, mtu_ddos: v }))} label="MTU 1450 (DDoS)" />
              <Toggle checked={!!form.gaming} onChange={(v) => setForm((f) => ({ ...f, gaming: v }))} label="Gaming tuning" />
              <Toggle checked={!!form.swap} onChange={(v) => setForm((f) => ({ ...f, swap: v }))} label="Swap" />
              <Toggle checked={!!form.disable_ipv6} onChange={(v) => setForm((f) => ({ ...f, disable_ipv6: v }))} label="Отключить IPv6" />
              <Toggle checked={!!form.use_origin} onChange={(v) => setForm((f) => ({ ...f, use_origin: v }))} label="Origin домен" />
              {form.use_origin && (
                <Field label="ORIGIN_DOMAIN">
                  <input
                    value={form.origin_domain ?? ""}
                    onChange={(e) => setForm((f) => ({ ...f, origin_domain: e.target.value }))}
                    className={inputCls}
                    placeholder="origin.example.com"
                  />
                </Field>
              )}
              <Toggle
                checked={!form.skip_system_update}
                onChange={(v) => setForm((f) => ({ ...f, skip_system_update: !v }))}
                label="apt update/upgrade"
              />
            </>
          )}

          {action === "tune" && (
            <>
              <Field label="MTU">
                <select
                  value={form.tune_mtu}
                  onChange={(e) => setForm((f) => ({ ...f, tune_mtu: e.target.value as RemnaScriptRunBody["tune_mtu"] }))}
                  className={inputCls}
                >
                  <option value="skip">Не менять</option>
                  <option value="on">Включить 1450</option>
                  <option value="off">Сбросить 1500</option>
                </select>
              </Field>
              <Field label="Gaming">
                <select
                  value={form.tune_gaming}
                  onChange={(e) =>
                    setForm((f) => ({ ...f, tune_gaming: e.target.value as RemnaScriptRunBody["tune_gaming"] }))
                  }
                  className={inputCls}
                >
                  <option value="skip">Не менять</option>
                  <option value="on">Включить</option>
                  <option value="off">Выключить</option>
                </select>
              </Field>
              <Field label="Swap">
                <select
                  value={form.tune_swap}
                  onChange={(e) => setForm((f) => ({ ...f, tune_swap: e.target.value as RemnaScriptRunBody["tune_swap"] }))}
                  className={inputCls}
                >
                  <option value="skip">Не менять</option>
                  <option value="on">Создать/обновить</option>
                </select>
              </Field>
              {form.tune_swap === "on" && (
                <Field label="Размер swap">
                  <input
                    value={form.swap_size}
                    onChange={(e) => setForm((f) => ({ ...f, swap_size: e.target.value }))}
                    className={inputCls}
                  />
                </Field>
              )}
              <Field label="IPv6">
                <select
                  value={form.tune_ipv6}
                  onChange={(e) => setForm((f) => ({ ...f, tune_ipv6: e.target.value as RemnaScriptRunBody["tune_ipv6"] }))}
                  className={inputCls}
                >
                  <option value="skip">Не менять</option>
                  <option value="disable">Отключить</option>
                  <option value="enable">Включить</option>
                </select>
              </Field>
              <Toggle checked={!!form.tune_ports} onChange={(v) => setForm((f) => ({ ...f, tune_ports: v }))} label="Открыть порты" />
              {form.tune_ports && (
                <>
                  <Field label="NODE_PORT">
                    <input
                      type="number"
                      value={form.node_port}
                      onChange={(e) => setForm((f) => ({ ...f, node_port: Number(e.target.value) || 2222 }))}
                      className={inputCls}
                    />
                  </Field>
                  <Field label="Доп. порты">
                    <input
                      value={form.additional_ports}
                      onChange={(e) => setForm((f) => ({ ...f, additional_ports: e.target.value }))}
                      className={inputCls}
                    />
                  </Field>
                </>
              )}
            </>
          )}

          <p className="text-xs text-[var(--muted)]">
            Выполнение в фоне по SSH, лог как при установке агента. Параллельно до 2 нод.
          </p>
          {error && <div className="text-sm text-[var(--danger)]">{error}</div>}
        </div>

        <div className="flex justify-end gap-2 border-t border-[var(--border)] px-5 py-4">
          <button type="button" onClick={onClose} className={btnGhost}>
            Отмена
          </button>
          <button type="button" onClick={submit} className={btnPrimary}>
            {action === "tune" ? "Применить" : action === "reinstall" ? "Переустановить" : "Установить"}
            {bulk ? ` (${nodes.length})` : ""}
          </button>
        </div>
      </div>
    </div>
  );
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="grid gap-1 text-xs text-[var(--muted)]">
      <span>{label}</span>
      {children}
    </label>
  );
}

function Toggle({
  checked,
  onChange,
  label,
}: {
  checked: boolean;
  onChange: (v: boolean) => void;
  label: string;
}) {
  return (
    <label className="flex cursor-pointer items-center gap-2 text-sm text-[var(--text)]">
      <input
        type="checkbox"
        checked={checked}
        onChange={(e) => onChange(e.target.checked)}
        className="h-4 w-4 accent-[var(--accent)]"
      />
      {label}
    </label>
  );
}

const inputCls =
  "w-full rounded-lg border border-[var(--border)] bg-[var(--bg)] px-3 py-2 text-sm text-[var(--text)] outline-none focus:border-[var(--accent)]";
const btnPrimary =
  "rounded-lg bg-[var(--accent)] px-4 py-2 text-sm font-semibold text-[#06221e] transition hover:brightness-110";
const btnGhost =
  "rounded-lg border border-[var(--border)] px-4 py-2 text-sm text-[var(--muted)] transition hover:text-[var(--text)]";
