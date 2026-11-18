import { useEffect, useMemo, useState } from "react";
import { api } from "../../api/client";
import type { SharingConfig, SharingConfigUpdate } from "../../types";
import { useSettingsBlock } from "./SaveContext";
import { Advanced, Card, NumField, StatusToggle, TextField, Toggle, useDraft } from "./ui";

type NumKey = {
  [K in keyof SharingConfig]: SharingConfig[K] extends number ? K : never;
}[keyof SharingConfig];
type BoolKey = "enabled" | "notify" | "auto_ban";
type Fields = [NumKey, string][];

const ZONES: Fields = [
  ["red", "Красная зона от"],
  ["amber", "Жёлтая зона от"],
];

const ZONES_MORE: Fields = [
  ["red_streak", "Красных сканов подряд до тревоги"],
  ["green_streak", "Зелёных сканов подряд до снятия"],
  ["remind_hours", "Напоминать не чаще, ч"],
];

const NORMS: Fields = [
  ["ip_per_device", "Адресов на устройство (5 и 15 мин)"],
  ["ip_slack_5m", "Запас адресов, 5 мин"],
  ["ip_slack_15m", "Запас адресов, 15 мин"],
  ["conc_ip_per_device", "Одновременных адресов на устройство"],
  ["conc_ip_slack", "Запас одновременных адресов"],
  ["net_per_device", "Операторов на устройство"],
  ["net_slack", "Запас операторов"],
  ["unlimited_as", "Безлимит считать как лимит"],
];

const WEIGHTS: Fields = [
  ["w_conc_ips", "Одновременные адреса"],
  ["w_conc_nets", "Одновременные операторы"],
  ["w_ips_5m", "Адреса за 5 мин"],
  ["w_ips_15m", "Адреса за 15 мин"],
  ["w_platforms", "Платформы сверх лимита"],
  ["w_travel", "Разные страны сетей"],
  ["w_hwid", "HWID сверх лимита"],
  ["w_night", "Нет ночной паузы"],
  ["w_traffic", "Трафик против медианы"],
];

const AUTOBAN: Fields = [
  ["auto_ban_score", "Автобан: оценка от"],
  ["auto_ban_scans", "Автобан: красных сканов подряд"],
];

const MISC: Fields = [
  ["night_window_h", "Ширина ночного окна, ч"],
  ["night_min_days", "Суток наблюдений до признака"],
  ["night_quiet_pct", "Пауза, если занятость ниже, %"],
  ["night_full_pct", "Полный вес, если занятость выше, %"],
  ["night_keep_days", "Хранить профиль, суток"],
  ["hwid_active_min", "HWID считать живым, мин"],
  ["users_refresh_min", "Перечитывать карточки, мин"],
  ["max_candidates", "Кандидатов на HWID за скан"],
  ["traffic_median_mult", "Трафик: во сколько раз медиана — норма"],
];

const ALL_NUM = [...ZONES, ...ZONES_MORE, ...NORMS, ...WEIGHTS, ...AUTOBAN, ...MISC];
const BOOLS: BoolKey[] = ["enabled", "notify", "auto_ban"];

/** Пороги и веса оценки шаринга; сохраняются общей кнопкой настроек, уходят только изменённые поля. */
export function SharingSettings() {
  const [cfg, setCfg] = useState<SharingConfig | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .sharingSettings()
      .then(setCfg)
      .catch((e) => setError(e instanceof Error ? e.message : "не удалось загрузить"));
  }, []);

  const saved = useMemo(() => {
    if (!cfg) return null;
    const o: Record<string, string | number | boolean> = {};
    for (const [key] of ALL_NUM) o[key] = cfg[key];
    for (const key of BOOLS) o[key] = cfg[key];
    o.exclude_text = (cfg.ban_exclude_ids ?? []).join(", ");
    return o;
  }, [cfg]);
  const { draft, set, changed, dirty, reset } = useDraft(saved);

  async function save() {
    const payload: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(changed)) {
      if (k === "exclude_text") {
        payload.ban_exclude_ids = String(v)
          .split(/[\s,;]+/)
          .map((x) => Number(x))
          .filter((n) => Number.isInteger(n) && n > 0);
      } else if (typeof v === "boolean" || Number.isFinite(v)) {
        payload[k] = v;
      }
    }
    if (Object.keys(payload).length === 0) return;
    setCfg(await api.sharingUpdate(payload as SharingConfigUpdate));
  }

  useSettingsBlock("sharing", { label: "Шаринг", tab: "sharing", dirty, save, reset });

  if (error) return <p className="text-sm text-[var(--danger)]">{error}</p>;
  if (!draft || !cfg) return <p className="text-sm text-[var(--muted)]">Загрузка…</p>;

  const weightSum = WEIGHTS.reduce((acc, [key]) => acc + (Number(draft[key]) || 0), 0);
  const autoBan = draft.auto_ban as boolean;

  function group(title: string, fields: Fields) {
    return (
      <div className="space-y-2">
        <div className="text-[11px] font-bold uppercase tracking-[0.12em] text-[var(--text)]/70">{title}</div>
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {fields.map(([key, label]) => (
            <NumField key={key} label={label} step="any" value={draft![key] as number} onChange={(v) => set(key, v)} />
          ))}
        </div>
      </div>
    );
  }

  return (
    <Card
      title="Настройки оценки шаринга"
      right={
        <div className="flex items-center gap-3">
          <Toggle label="уведомлять" checked={draft.notify as boolean} onChange={(v) => set("notify", v)} />
          <StatusToggle
            checked={draft.enabled as boolean}
            onChange={(v) => set("enabled", v)}
            on="включено"
            off="выключено"
          />
        </div>
      }
    >
      <p className="text-xs leading-relaxed text-[var(--muted)]">
        Норма считается от лимита устройств: норма = лимит × «на устройство» + запас. Признак даёт свой вес целиком,
        когда значение вдвое больше нормы, и ноль, пока оно в норме. Общий лимит ({cfg.fallback_device_limit})
        берётся из панели и здесь не меняется. Чат для тревог и часовой пояс — на вкладке «Уведомления»
        {cfg.bot_ready ? "" : "; сейчас бот не настроен, сообщения не отправляются"}.
      </p>

      {group("Зоны", ZONES)}

      <div className="space-y-3 rounded-lg border border-[var(--border)] p-3">
        <div className="flex flex-wrap items-center gap-3">
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={autoBan}
              onChange={(e) => {
                const on = e.target.checked;
                if (
                  on &&
                  !window.confirm(
                    "Включить автобан? Рагент будет САМ отключать пользователей в панели и рвать их соединения, когда тревога поднята, оценка и выдержка выше порогов. Не больше 3 в час. Вступит в силу после «Сохранить».",
                  )
                )
                  return;
                set("auto_ban", on);
              }}
              className="h-4 w-4 accent-[var(--accent)]"
            />
            <span className={autoBan ? "text-[var(--danger)]" : "text-[var(--muted)]"}>
              автобан {autoBan ? "ВКЛЮЧЁН" : "выключен"}
            </span>
          </label>
          <span className="text-xs text-[var(--muted)]">
            не более 3 в час, уже отключённых и исключённых не трогает; лимит устройств не меняет
          </span>
        </div>
        <TextField
          label="Не банить (белый список): id пользователей через запятую"
          value={draft.exclude_text as string}
          onChange={(v) => set("exclude_text", v)}
        />
      </div>

      <Advanced title="Дополнительно: пороги, нормы, веса">
        {group("Выдержка и напоминания", ZONES_MORE)}
        {group("Пороги автобана", AUTOBAN)}
        {group("Нормы от лимита устройств", NORMS)}
        {group(`Веса признаков (сумма ${Math.round(weightSum)})`, WEIGHTS)}
        {group("Ночной профиль, HWID и трафик", MISC)}
      </Advanced>
    </Card>
  );
}
