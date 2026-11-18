import { useEffect, useMemo, useState } from "react";
import { api, type SubwatchStatus } from "../api/client";
import { useSettingsBlock } from "./settings/SaveContext";
import { Advanced, Card, Grid, NumField, StatusToggle, TextField, Toggle, useDraft } from "./settings/ui";

/** Следилка за подпиской. Бот и чат — на вкладке «Уведомления». */
export function SubwatchSettings() {
  const [status, setStatus] = useState<SubwatchStatus | null>(null);

  useEffect(() => {
    api.subwatchStatus().then(setStatus).catch(() => undefined);
  }, []);

  const saved = useMemo(
    () =>
      status
        ? {
            enabled: status.config.enabled,
            notify: status.config.notify,
            url: status.config.url,
            origin_url: status.config.origin_url,
            interval_min: status.config.interval_min,
            min_hosts: status.config.min_hosts,
          }
        : null,
    [status],
  );
  const { draft, set, changed, dirty, reset } = useDraft(saved);

  useSettingsBlock("subwatch", {
    label: "Подписка",
    tab: "subscription",
    dirty,
    reset,
    save: async () => {
      const c = { ...changed };
      if (typeof c.url === "string") c.url = c.url.trim();
      if (typeof c.origin_url === "string") c.origin_url = c.origin_url.trim();
      setStatus(await api.subwatchUpdate(c));
    },
  });

  if (!draft) return <p className="text-sm text-[var(--muted)]">Загрузка…</p>;

  return (
    <Card
      title="Следилка за подпиской"
      right={
        <StatusToggle
          checked={draft.enabled}
          disabled={!draft.url.trim()}
          onChange={(v) => set("enabled", v)}
          on="включена"
          off="выключена"
        />
      }
    >
      <p className="text-xs text-[var(--muted)]">
        Забирает подписку как клиент и проверяет, есть ли внутри конфиги, а не код ответа.
      </p>
      <TextField
        label="Ссылка на подписку (как у клиента)"
        value={draft.url}
        onChange={(v) => set("url", v)}
        placeholder="https://sub.example.com/sub/xxxxxxxx"
      />
      <Toggle checked={draft.notify} onChange={(v) => set("notify", v)} label="Уведомлять в Telegram" />
      <p className="text-xs text-[var(--muted)]">
        Бот и чат настраиваются на вкладке «Уведомления».
      </p>

      <Advanced title="Дополнительно">
        <TextField
          label="Подписка напрямую с ориджина"
          hint="Та же подписка мимо CDN. Не обязательно, но показывает, чья поломка. Проверяется только когда основная не ответила."
          value={draft.origin_url}
          onChange={(v) => set("origin_url", v)}
          placeholder="https://origin-sub.example.com/sub/xxxxxxxx"
        />
        <Grid>
          <NumField
            label="Проверять раз в, минут"
            value={draft.interval_min}
            min={1}
            max={1440}
            onChange={(v) => set("interval_min", v)}
          />
          <NumField
            label="Конфигов не меньше"
            value={draft.min_hosts}
            min={1}
            max={1000}
            onChange={(v) => set("min_hosts", v)}
          />
        </Grid>
        <p className="text-xs text-[var(--muted)]">
          Пишем только на переломах (упало — поднялось), пока лежит — напоминаем раз в час.
        </p>
      </Advanced>
    </Card>
  );
}
