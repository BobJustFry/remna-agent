import { useEffect, useMemo, useState } from "react";
import { api, type NodeWatchStatus } from "../api/client";
import type { NodeItem } from "../types";
import { useSettingsBlock } from "./settings/SaveContext";
import { Advanced, Card, Grid, NumField, StatusToggle, Toggle, useDraft } from "./settings/ui";

type Props = {
  nodes: NodeItem[];
  /** Настройки поменялись — пометки «недоступна с …» стоит перечитать. */
  onChanged?: () => void;
};

const splitIds = (s: string) => (s ? s.split(",") : []);

/** Следилка за нодами. Чат и часовой пояс — на вкладке «Уведомления». */
export function NodeWatchSettings({ nodes, onChanged }: Props) {
  const [status, setStatus] = useState<NodeWatchStatus | null>(null);

  useEffect(() => {
    api.nodeWatchStatus().then(setStatus).catch(() => undefined);
  }, []);

  const saved = useMemo(
    () =>
      status
        ? {
            enabled: status.config.enabled,
            notify: status.config.notify,
            threshold_min: status.config.threshold_min,
            remind_hours: status.config.remind_hours,
            skip_panel_disabled: status.config.skip_panel_disabled,
            panel_alive: status.config.panel_alive,
            exclude: [...status.config.exclude].sort().join(","),
          }
        : null,
    [status],
  );
  const { draft, set, changed, dirty, reset } = useDraft(saved);

  // Нода удалена, а в исключениях осталась — при сохранении такой хвост уходит.
  const known = useMemo(() => new Set(nodes.map((n) => n.id)), [nodes]);

  useSettingsBlock("nodewatch", {
    label: "Ноды",
    tab: "nodes",
    dirty,
    reset,
    save: async () => {
      const { exclude, ...rest } = changed;
      const payload: Parameters<typeof api.nodeWatchUpdate>[0] = { ...rest };
      if (typeof exclude === "string") payload.exclude = splitIds(exclude).filter((id) => known.has(id));
      setStatus(await api.nodeWatchUpdate(payload));
      onChanged?.();
    },
  });

  if (!status || !draft) return <p className="text-sm text-[var(--muted)]">Загрузка…</p>;
  const cfg = status.config;
  const excludeSet = new Set(splitIds(draft.exclude));
  const excluded = [...excludeSet].filter((id) => known.has(id));
  const sorted = [...nodes].sort((a, b) => a.name.localeCompare(b.name));

  function toggle(id: string) {
    const next = new Set(excludeSet);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    set("exclude", [...next].sort().join(","));
  }

  return (
    <Card
      title="Следилка за нодами"
      right={<StatusToggle checked={draft.enabled} onChange={(v) => set("enabled", v)} />}
    >
      <p className="text-xs text-[var(--muted)]">
        Если нода молчит дольше порога — сообщение в чат, когда поднялась — ещё одно.
      </p>
      <Grid>
        <NumField
          label="Тревога, если молчит дольше, минут"
          value={draft.threshold_min}
          min={1}
          max={1440}
          onChange={(v) => set("threshold_min", v)}
        />
        <div className="self-end pb-2">
          <Toggle checked={draft.notify} onChange={(v) => set("notify", v)} label="Уведомлять в Telegram" />
        </div>
      </Grid>
      {!cfg.bot_ready && (
        <p className="rounded-lg border border-[rgba(251,191,36,0.45)] bg-[rgba(251,191,36,0.10)] px-3 py-2 text-xs text-[var(--warning)]">
          Бот не настроен — сообщения не отправляются. Задайте токен и чат на вкладке «Уведомления».
          Журнал простоев при этом ведётся.
        </p>
      )}

      <Advanced title="Дополнительно" badge={excluded.length > 0 ? `исключений: ${excluded.length}` : undefined}>
        <NumField
          label="Напоминать раз в, часов"
          hint="0 — не напоминать."
          value={draft.remind_hours}
          min={0}
          max={168}
          onChange={(v) => set("remind_hours", v)}
        />
        <Toggle
          checked={draft.skip_panel_disabled}
          onChange={(v) => set("skip_panel_disabled", v)}
          label="Не тревожить по нодам, выключенным в панели"
          hint="Выключили ноду в панели на обслуживание — её молчание не авария."
        />
        <Toggle
          checked={draft.panel_alive}
          onChange={(v) => set("panel_alive", v)}
          label="Считать ноду живой, если её видит панель"
          hint="Рагент стоит в России, и до части зарубежных нод ему не достать, хотя они работают. Если агент и SSH молчат, а панель видит ноду подключённой — тревоги нет."
        />
        <details>
          <summary className="cursor-pointer text-xs text-[var(--muted)] hover:text-[var(--text)]">
            Исключения — ноды, по которым не тревожить{excluded.length > 0 ? `: ${excluded.length}` : ""}
          </summary>
          {sorted.length === 0 ? (
            <p className="mt-2 text-xs text-[var(--muted)]">Нод пока нет.</p>
          ) : (
            <ul className="mt-2 max-h-56 space-y-1 overflow-auto">
              {sorted.map((n) => (
                <li key={n.id}>
                  <label className="flex items-center gap-2 rounded-lg bg-[var(--bg-row)] px-3 py-1.5 text-xs">
                    <input
                      type="checkbox"
                      checked={excludeSet.has(n.id)}
                      onChange={() => toggle(n.id)}
                      className="h-3.5 w-3.5 accent-[var(--accent)]"
                    />
                    <span className="min-w-0 truncate text-[var(--text)]">{n.name}</span>
                    <span className="ml-auto shrink-0 font-mono text-[var(--muted)]">{n.host}</span>
                  </label>
                </li>
              ))}
            </ul>
          )}
        </details>
      </Advanced>
    </Card>
  );
}
