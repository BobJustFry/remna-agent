import { useEffect, useMemo, useState } from "react";
import { api, type MemWatchConfig, type MemWatchUpdate } from "../../api/client";
import { useSettingsBlock } from "./SaveContext";
import { Card, NumField, TextField, useDraft } from "./ui";

const TAB = "memory";

// utc_offset сюда не входит: часовой пояс общий и пишется блоком «Уведомления».
const NUM_FIELDS: { key: Exclude<keyof MemWatchConfig, "utc_offset">; label: string; hint?: string }[] = [
  { key: "rss_min_mb", label: "RSS ядра, пол, МБ", hint: "порог = max(пол, % ОЗУ)" },
  { key: "rss_pct", label: "RSS ядра, % ОЗУ" },
  { key: "avail_pct", label: "MemAvailable ниже, %" },
  { key: "swap_pct", label: "Своп занят выше, %" },
  { key: "critical_avail_pct", label: "Критично: MemAvailable ниже, %" },
  { key: "oom_window_min", label: "После OOM критично, мин" },
  { key: "confirm_ticks", label: "Подтверждение, опросов по 30 с" },
  { key: "clear_ticks", label: "Снятие, опросов в норме" },
  { key: "remind_hours", label: "Напоминать каждые, ч (0 — нет)" },
  { key: "cooldown_h", label: "Пауза после перезапуска ноды, ч" },
  { key: "min_core_uptime_h", label: "Мин. аптайм ядра, ч" },
  { key: "max_per_node_24h", label: "На ноду за 24 ч, не более" },
  { key: "max_per_node_7d", label: "На ноду за 7 дней, не более" },
  { key: "fleet_gap_min", label: "По парку: пауза между, мин" },
  { key: "fleet_per_hour", label: "По парку: в час, не более" },
  { key: "window_start", label: "Тихое окно с, час" },
  { key: "window_end", label: "Тихое окно до, час" },
  { key: "post_check_after_s", label: "Проверка после перезапуска, с" },
  { key: "post_check_deadline_s", label: "Срок проверки, с" },
  { key: "relapse_h", label: "Повторный разгон в течение, ч" },
];

const split = (s: string) => s.split(",").map((x) => x.trim()).filter(Boolean);

/** Пороги и защиты памятьсторожа; сохраняются общей кнопкой настроек, уходят только изменённые поля. */
export function MemWatchSettings() {
  const [cfg, setCfg] = useState<MemWatchConfig | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .memWatchStatus()
      .then((s) => setCfg(s.config))
      .catch((e) => setError(e instanceof Error ? e.message : "не удалось загрузить"));
  }, []);

  const saved = useMemo(() => {
    if (!cfg) return null;
    const o: Record<string, string | number> = {};
    for (const f of NUM_FIELDS) o[f.key] = cfg[f.key] as number;
    o.exclude = cfg.exclude.join(", ");
    o.exclude_contains = cfg.exclude_contains.join(", ");
    return o;
  }, [cfg]);
  const { draft, set, changed, dirty, reset } = useDraft(saved);

  async function save() {
    const payload: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(changed)) {
      if (k === "exclude" || k === "exclude_contains") payload[k] = split(v as string);
      else if (Number.isFinite(v)) payload[k] = Math.round(v as number);
    }
    if (Object.keys(payload).length === 0) return;
    const s = await api.memWatchUpdate(payload as MemWatchUpdate);
    setCfg(s.config);
  }

  useSettingsBlock("memwatch", { label: "Память ядер", tab: TAB, dirty, save, reset });

  if (error) return <p className="text-sm text-[var(--danger)]">{error}</p>;
  if (!draft) return <p className="text-sm text-[var(--muted)]">Загрузка…</p>;

  return (
    <Card title="Память ядер: пороги и защиты">
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {NUM_FIELDS.map((f) => (
          <NumField
            key={f.key}
            label={f.label}
            hint={f.hint}
            value={draft[f.key] as number}
            onChange={(v) => set(f.key, v)}
          />
        ))}
      </div>
      <p className="text-xs text-[var(--muted)]">Часовой пояс общий — задаётся на вкладке «Уведомления».</p>
      <TextField
        label="Не перезапускать (имена нод целиком, через запятую)"
        value={draft.exclude as string}
        onChange={(v) => set("exclude", v)}
      />
      <TextField
        label="Не перезапускать, если в имени есть (через запятую)"
        value={draft.exclude_contains as string}
        onChange={(v) => set("exclude_contains", v)}
      />
    </Card>
  );
}
