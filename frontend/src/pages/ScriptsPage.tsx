import { useEffect, useState, type ReactNode } from "react";
import { api, type RemnaScriptDefaults } from "../api/client";

const emptyDefaults: RemnaScriptDefaults = {
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
  skip_system_update: true,
  cf_204_stub: false,
};

export function ScriptsPage() {
  const [defaults, setDefaults] = useState<RemnaScriptDefaults>(emptyDefaults);
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [msg, setMsg] = useState<string | null>(null);

  useEffect(() => {
    void api
      .getSettings()
      .then((s) => {
        setDefaults({ ...emptyDefaults, ...s.defaults, origin_domain: s.defaults.origin_domain ?? "" });
      })
      .catch((err) => setMsg(err instanceof Error ? err.message : "Не удалось загрузить настройки"))
      .finally(() => setLoading(false));
  }, []);

  async function save() {
    setBusy(true);
    setMsg(null);
    try {
      const s = await api.updateSettings({
        defaults: {
          ...defaults,
          origin_domain: defaults.use_origin ? defaults.origin_domain || null : null,
        },
      });
      setDefaults({ ...emptyDefaults, ...s.defaults, origin_domain: s.defaults.origin_domain ?? "" });
      setMsg("Параметры по умолчанию сохранены");
    } catch (err) {
      setMsg(err instanceof Error ? err.message : "Ошибка сохранения");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex h-full flex-col">
      <header className="border-b border-[var(--border)] px-4 py-4 sm:px-6">
        <h1 className="text-xl font-semibold tracking-tight">Скрипты</h1>
        <p className="mt-0.5 text-sm text-[var(--muted)]">
          Параметры RemnaNode по умолчанию. SECRET_KEY — в «Настройки». Применение — из меню ноды
          или bulk на странице «Ноды».
        </p>
      </header>

      <div className="flex-1 overflow-auto px-3 py-3 sm:px-6 sm:py-4">
        {loading ? (
          <p className="text-sm text-[var(--muted)]">Загрузка…</p>
        ) : (
          <div className="mx-auto max-w-xl space-y-4">
            <section className="space-y-3 rounded-[var(--radius)] border border-[var(--border)] bg-[var(--bg-elevated)] p-4">
              <h2 className="text-sm font-semibold">Установка по умолчанию</h2>
              <p className="text-xs text-[var(--muted)]">
                Эти значения подставляются в форму при запуске скрипта с ноды.
              </p>

              <Field label="NODE_PORT">
                <input
                  type="number"
                  min={1}
                  max={65535}
                  value={defaults.node_port}
                  onChange={(e) =>
                    setDefaults((d) => ({ ...d, node_port: Number(e.target.value) || 2222 }))
                  }
                  className={inputCls}
                />
              </Field>
              <Field label="Доп. порты (через запятую)">
                <input
                  value={defaults.additional_ports}
                  onChange={(e) => setDefaults((d) => ({ ...d, additional_ports: e.target.value }))}
                  className={inputCls}
                  placeholder="опционально"
                />
              </Field>
              <div className="grid grid-cols-2 gap-2">
                <Field label="Cache (tmpfs)">
                  <input
                    value={defaults.cache_size}
                    onChange={(e) => setDefaults((d) => ({ ...d, cache_size: e.target.value }))}
                    className={inputCls}
                  />
                </Field>
                <Field label="Swap">
                  <input
                    value={defaults.swap_size}
                    onChange={(e) => setDefaults((d) => ({ ...d, swap_size: e.target.value }))}
                    className={inputCls}
                    disabled={!defaults.swap}
                  />
                </Field>
              </div>
              <Toggle
                checked={defaults.mtu_ddos}
                onChange={(v) => setDefaults((d) => ({ ...d, mtu_ddos: v }))}
                label="MTU 1450 (DDoS)"
              />
              <Toggle
                checked={defaults.gaming}
                onChange={(v) => setDefaults((d) => ({ ...d, gaming: v }))}
                label="Gaming tuning"
              />
              <Toggle
                checked={defaults.swap}
                onChange={(v) => setDefaults((d) => ({ ...d, swap: v }))}
                label="Swap"
              />
              <Toggle
                checked={defaults.disable_ipv6}
                onChange={(v) => setDefaults((d) => ({ ...d, disable_ipv6: v }))}
                label="Отключить IPv6"
              />
              <Toggle
                checked={defaults.use_origin}
                onChange={(v) => setDefaults((d) => ({ ...d, use_origin: v }))}
                label="Origin домен"
              />
              {defaults.use_origin && (
                <Field label="ORIGIN_DOMAIN">
                  <input
                    value={defaults.origin_domain ?? ""}
                    onChange={(e) => setDefaults((d) => ({ ...d, origin_domain: e.target.value }))}
                    className={inputCls}
                    placeholder="origin.example.com"
                  />
                </Field>
              )}
              <Toggle
                checked={!defaults.skip_system_update}
                onChange={(v) => setDefaults((d) => ({ ...d, skip_system_update: !v }))}
                label="apt update/upgrade"
              />
              <Toggle
                checked={defaults.cf_204_stub}
                onChange={(v) => setDefaults((d) => ({ ...d, cf_204_stub: v }))}
                label="Заглушка cf_204 (proxy-ping без Cloudflare)"
              />
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
