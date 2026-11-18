import { useEffect, useMemo, useState } from "react";
import { api, type YcwatchStatus } from "../api/client";
import { useSettingsBlock } from "./settings/SaveContext";
import { Advanced, BTN, Card, Grid, NumField, StatusToggle, TextField, Toggle, useDraft } from "./settings/ui";

/** Следилка за Yandex Cloud: баланс облака и ВМ страницы подписки. Чат — на вкладке «Уведомления». */
export function YcwatchSettings() {
  const [status, setStatus] = useState<YcwatchStatus | null>(null);

  useEffect(() => {
    api.ycwatchStatus().then(setStatus).catch(() => undefined);
  }, []);

  const saved = useMemo(
    () =>
      status
        ? {
            enabled: status.config.enabled,
            notify: status.config.notify,
            billing_account_id: status.config.billing_account_id,
            folder_id: status.config.folder_id,
            instance_id: status.config.instance_id,
            warn_rub: status.config.warn_rub,
            interval_min: status.config.interval_min,
            remind_min: status.config.remind_min,
            remind_low_hours: status.config.remind_low_hours,
            sa_key: "",
            sa_erase: false,
          }
        : null,
    [status],
  );
  const { draft, set, changed, dirty, reset } = useDraft(saved);

  useSettingsBlock("ycwatch", {
    label: "Облако",
    tab: "cloud",
    dirty,
    reset,
    save: async () => {
      const { sa_key, sa_erase, ...rest } = changed;
      const payload: Parameters<typeof api.ycwatchUpdate>[0] = { ...rest };
      for (const k of ["billing_account_id", "folder_id", "instance_id"] as const) {
        const v = payload[k];
        if (typeof v === "string") payload[k] = v.trim();
      }
      if (sa_erase) payload.sa_key = "";
      else if (typeof sa_key === "string" && sa_key.trim()) payload.sa_key = sa_key.trim();
      setStatus(await api.ycwatchUpdate(payload));
      set("sa_key", "");
      set("sa_erase", false);
    },
  });

  if (!status || !draft) return <p className="text-sm text-[var(--muted)]">Загрузка…</p>;
  const cfg = status.config;

  return (
    <Card
      title="Следилка за Yandex Cloud"
      right={<StatusToggle checked={draft.enabled} onChange={(v) => set("enabled", v)} />}
    >
      <p className="text-xs text-[var(--muted)]">
        Смотрит баланс облака и статус ВМ страницы подписки.
        <span className="ml-1 text-[var(--text)]">
          Тревога при нуле/минусе баланса или если ВМ не RUNNING.
        </span>
      </p>
      <Grid>
        <NumField
          label="Предупреждать ниже, ₽"
          value={draft.warn_rub}
          min={0}
          onChange={(v) => set("warn_rub", v)}
        />
        <div className="self-end pb-2">
          <Toggle checked={draft.notify} onChange={(v) => set("notify", v)} label="Уведомлять в Telegram" />
        </div>
      </Grid>
      <p className="text-xs text-[var(--muted)]">
        {cfg.bot_ready ? "" : "Бот пока не настроен — сообщения не уходят. "}
        Чат и бот — на вкладке «Уведомления».
      </p>

      <div className="rounded-lg border border-[var(--border)] p-3">
        <div className="text-xs font-semibold">Ключ сервисного аккаунта</div>
        <p className="mt-1 text-xs text-[var(--muted)]">
          {cfg.sa_key_set ? (
            <>
              Стоит ключ <code className="font-mono">{cfg.sa_key_id}</code> СА{" "}
              <code className="font-mono">{cfg.sa_id}</code>.
            </>
          ) : cfg.sa_key_broken ? (
            <span className="text-[var(--danger)]">
              Ключ в базе не расшифровывается (сменился ключ шифрования) — вставьте заново.
            </span>
          ) : (
            "Ключ не задан — следилка ждёт."
          )}
        </p>
        <textarea
          value={draft.sa_key}
          onChange={(e) => {
            set("sa_key", e.target.value);
            if (e.target.value) set("sa_erase", false);
          }}
          spellCheck={false}
          autoComplete="off"
          rows={3}
          disabled={draft.sa_erase}
          placeholder={
            cfg.sa_key_set
              ? "вставьте новый JSON, чтобы заменить"
              : '{ "id": …, "service_account_id": …, "private_key": … }'
          }
          className="mt-2 w-full rounded-lg border border-[var(--border)] bg-[var(--bg)] px-3 py-2 font-mono text-xs text-[var(--text)] outline-none focus:border-[var(--accent)] disabled:opacity-50"
        />
        {cfg.sa_key_set && (
          <div className="mt-2 flex flex-wrap items-center gap-2">
            <button
              type="button"
              className={BTN}
              onClick={() => {
                set("sa_erase", !draft.sa_erase);
                set("sa_key", "");
              }}
            >
              {draft.sa_erase ? "Не стирать ключ" : "Стереть ключ"}
            </button>
            {draft.sa_erase && (
              <span className="text-xs text-[var(--warning)]">
                Ключ будет стёрт при сохранении — следилка перестанет опрашивать облако.
              </span>
            )}
          </div>
        )}
      </div>

      <Advanced title="Дополнительно">
        <Grid>
          <TextField
            label="Платёжный аккаунт (id)"
            value={draft.billing_account_id}
            onChange={(v) => set("billing_account_id", v)}
          />
          <TextField
            label="Каталог с ВМ (folderId)"
            value={draft.folder_id}
            onChange={(v) => set("folder_id", v)}
          />
          <TextField
            label="id ВМ"
            hint="Пусто — следим за всеми ВМ каталога."
            value={draft.instance_id}
            onChange={(v) => set("instance_id", v)}
          />
          <NumField
            label="Опрашивать раз в, минут"
            value={draft.interval_min}
            min={5}
            max={1440}
            onChange={(v) => set("interval_min", v)}
          />
          <NumField
            label="Авария: напоминать раз в, мин"
            hint="0 — не напоминать."
            value={draft.remind_min}
            min={0}
            max={1440}
            onChange={(v) => set("remind_min", v)}
          />
          <NumField
            label="Мало денег / не отвечает: раз в, ч"
            hint="0 — не напоминать."
            value={draft.remind_low_hours}
            min={0}
            max={168}
            onChange={(v) => set("remind_low_hours", v)}
          />
        </Grid>
        <p className="text-xs text-[var(--muted)]">
          Только чтение: ВМ не запускает, биллинг не трогает. Ключ — authorized key узкого СА с ролями
          compute.viewer и billing.accounts.viewer, хранится зашифрованным.
        </p>
      </Advanced>
    </Card>
  );
}
