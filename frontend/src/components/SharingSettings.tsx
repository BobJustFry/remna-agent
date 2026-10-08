import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { SharingConfig, SharingConfigUpdate } from "../types";

const INPUT =
  "w-full rounded-lg border border-[var(--border)] bg-[var(--bg)] px-3 py-2 text-sm text-[var(--text)] outline-none focus:border-[var(--accent)]";
const BTN =
  "rounded-lg border border-[var(--border)] px-3 py-1.5 text-sm text-[var(--text)] hover:bg-[var(--bg-row)] disabled:opacity-50";

/** Числовые поля настроек: ключ в конфиге, подпись и пояснение. */
const ZONES: [keyof SharingConfig, string][] = [
  ["red", "Красная зона от"],
  ["amber", "Жёлтая зона от"],
  ["red_streak", "Красных сканов подряд до тревоги"],
  ["green_streak", "Зелёных сканов подряд до снятия"],
  ["remind_hours", "Напоминать не чаще, ч"],
];

const NORMS: [keyof SharingConfig, string][] = [
  ["ip_per_device", "Адресов на устройство (5 и 15 мин)"],
  ["ip_slack_5m", "Запас адресов, 5 мин"],
  ["ip_slack_15m", "Запас адресов, 15 мин"],
  ["conc_ip_per_device", "Одновременных адресов на устройство"],
  ["conc_ip_slack", "Запас одновременных адресов"],
  ["net_per_device", "Операторов на устройство"],
  ["net_slack", "Запас операторов"],
  ["unlimited_as", "Безлимит считать как лимит"],
];

const WEIGHTS: [keyof SharingConfig, string][] = [
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

const MISC: [keyof SharingConfig, string][] = [
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

/** Пороги и веса оценки шаринга. Лежат в app_settings, меняются только отсюда. */
export function SharingSettings() {
  const [cfg, setCfg] = useState<SharingConfig | null>(null);
  const [draft, setDraft] = useState<Record<string, number | string>>({});
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [open, setOpen] = useState(false);

  function apply(next: SharingConfig) {
    setCfg(next);
    const d: Record<string, number | string> = { chat_id: next.chat_id };
    for (const [key] of [...ZONES, ...NORMS, ...WEIGHTS, ...MISC]) {
      d[key] = next[key] as number;
    }
    setDraft(d);
  }

  useEffect(() => {
    api.sharingSettings().then(apply).catch(() => undefined);
  }, []);

  async function patch(payload: SharingConfigUpdate, note?: string) {
    setBusy(true);
    setMsg(null);
    try {
      apply(await api.sharingUpdate(payload));
      if (note) setMsg(note);
    } catch (err) {
      setMsg(err instanceof Error ? err.message : "Не удалось сохранить");
    } finally {
      setBusy(false);
    }
  }

  if (!cfg) return null;

  const weightSum = WEIGHTS.reduce((acc, [key]) => acc + Number(draft[key] ?? 0), 0);

  return (
    <section className="rounded-[var(--radius)] border border-[var(--border)] bg-[var(--bg-elevated)] p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h2 className="text-sm font-semibold">Настройки оценки</h2>
          <p className="mt-1 max-w-3xl text-xs leading-relaxed text-[var(--muted)]">
            Норма считается от лимита устройств: норма = лимит × «на устройство» + запас.
            Признак даёт свой вес целиком, когда значение вдвое больше нормы, и ноль, пока
            оно в норме. Общий лимит ({cfg.fallback_device_limit}) берётся из панели и здесь
            не меняется. Уведомления уходят тем же ботом, что у следилки за подпиской
            {cfg.bot_ready ? "" : " — сейчас бот не настроен, сообщения не отправляются"}.
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-3">
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={cfg.notify}
              disabled={busy}
              onChange={(e) => void patch({ notify: e.target.checked })}
              className="h-4 w-4 accent-[var(--accent)]"
            />
            <span className="text-[var(--muted)]">уведомлять</span>
          </label>
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={cfg.enabled}
              disabled={busy}
              onChange={(e) =>
                void patch({ enabled: e.target.checked }, e.target.checked ? "Включено" : "Выключено")
              }
              className="h-4 w-4 accent-[var(--accent)]"
            />
            <span className={cfg.enabled ? "text-[var(--success)]" : "text-[var(--muted)]"}>
              {cfg.enabled ? "включено" : "выключено"}
            </span>
          </label>
        </div>
      </div>

      <Group title="Зоны и выдержка" fields={ZONES} draft={draft} setDraft={setDraft} />

      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="mt-3 text-xs text-[var(--accent)] hover:underline"
      >
        {open ? "Свернуть нормы и веса" : "Показать нормы и веса"}
      </button>

      {open && (
        <>
          <Group title="Нормы от лимита устройств" fields={NORMS} draft={draft} setDraft={setDraft} />
          <Group
            title={`Веса признаков (сумма ${Math.round(weightSum)})`}
            fields={WEIGHTS}
            draft={draft}
            setDraft={setDraft}
          />
          <Group title="Ночной профиль, HWID и трафик" fields={MISC} draft={draft} setDraft={setDraft} />
        </>
      )}

      <div className="mt-3 flex flex-wrap items-end gap-3">
        <label className="min-w-48 flex-1">
          <span className="text-xs text-[var(--muted)]">
            Чат для тревог — пусто: тот же, что у следилки за подпиской
          </span>
          <input
            value={String(draft.chat_id ?? "")}
            onChange={(e) => setDraft({ ...draft, chat_id: e.target.value })}
            spellCheck={false}
            className={`mt-1 font-mono ${INPUT}`}
          />
        </label>
        <button
          type="button"
          disabled={busy}
          onClick={() => void patch(draft as SharingConfigUpdate, "Сохранено")}
          className={BTN}
        >
          Сохранить
        </button>
        {msg && <span className="text-xs text-[var(--muted)]">{msg}</span>}
      </div>
    </section>
  );
}

function Group({
  title,
  fields,
  draft,
  setDraft,
}: {
  title: string;
  fields: [keyof SharingConfig, string][];
  draft: Record<string, number | string>;
  setDraft: (next: Record<string, number | string>) => void;
}) {
  return (
    <div className="mt-4">
      <div className="text-[11px] font-bold uppercase tracking-[0.12em] text-[var(--text)]/70">
        {title}
      </div>
      <div className="mt-2 flex flex-wrap gap-3">
        {fields.map(([key, label]) => (
          <label key={String(key)} className="min-w-44 flex-1">
            <span className="text-xs text-[var(--muted)]">{label}</span>
            <input
              type="number"
              step="any"
              value={String(draft[key] ?? "")}
              onChange={(e) => setDraft({ ...draft, [key]: Number(e.target.value) })}
              className={`mt-1 ${INPUT}`}
            />
          </label>
        ))}
      </div>
    </div>
  );
}
