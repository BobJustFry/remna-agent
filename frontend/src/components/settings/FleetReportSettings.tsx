import { useEffect, useMemo, useState } from "react";
import { api, type FleetReportStatus } from "../../api/client";
import { useSettingsBlock } from "./SaveContext";
import { Advanced, BTN, Card, Grid, NumField, StatusToggle, TextField, Toggle, useDraft } from "./ui";

/** Отчёт о нагрузке парка в Telegram + команды /status /report /nodes. Выключен, пока владелец не включит. */
export function FleetReportSettings() {
  const [status, setStatus] = useState<FleetReportStatus | null>(null);
  const [preview, setPreview] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    api.fleetReportStatus().then(setStatus).catch(() => undefined);
  }, []);

  const saved = useMemo(() => (status ? { ...status.config } : null), [status]);
  const { draft, set, changed, dirty, reset } = useDraft(saved);

  useSettingsBlock("fleet-report", {
    label: "Отчёт о нагрузке",
    tab: "telegram",
    dirty,
    reset,
    save: async () => {
      const payload = { ...changed } as Parameters<typeof api.fleetReportUpdate>[0];
      if (typeof payload.chat_id === "string") payload.chat_id = payload.chat_id.trim();
      setStatus(await api.fleetReportUpdate(payload));
    },
  });

  async function showPreview() {
    setBusy(true);
    setErr(null);
    try {
      setPreview((await api.fleetReportPreview("full")).text);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "не удалось собрать отчёт");
    } finally {
      setBusy(false);
    }
  }

  async function sendNow() {
    setBusy(true);
    setErr(null);
    try {
      setStatus(await api.fleetReportSend());
    } catch (e) {
      setErr(e instanceof Error ? e.message : "не удалось отправить");
    } finally {
      setBusy(false);
    }
  }

  if (!status || !draft) return <p className="text-sm text-[var(--muted)]">Загрузка…</p>;

  return (
    <Card
      title="Отчёт о нагрузке парка"
      right={<StatusToggle checked={draft.enabled} onChange={(v) => set("enabled", v)} />}
    >
      <p className="text-xs text-[var(--muted)]">
        Раз в несколько часов по МСК: полный отчёт (по умолчанию 12, 21, 00) и короткий (06, 09, 15, 18). Вердикты — по
        пикам за 14 дней, а не по текущей нагрузке. В боте: /status, /report, /nodes.
        {!status.bot_ready && " Бот или чат не настроены — сообщения не уходят."}
      </p>
      <Grid>
        <TextField
          label="Чат отчёта"
          hint="Пусто — чат следилки за подпиской. Команды принимаются только из этого чата."
          value={draft.chat_id}
          onChange={(v) => set("chat_id", v)}
        />
        <div className="self-end pb-2">
          <Toggle checked={draft.commands} onChange={(v) => set("commands", v)} label="Команды бота (/status…)" />
        </div>
        <TextField
          label="Полный отчёт, часы"
          value={draft.full_hours}
          onChange={(v) => set("full_hours", v)}
          placeholder="12,21,0"
        />
        <TextField
          label="Короткий отчёт, часы"
          value={draft.short_hours}
          onChange={(v) => set("short_hours", v)}
          placeholder="6,9,15,18"
        />
      </Grid>
      <p className="text-xs text-[var(--muted)]">
        Последний: {status.last_sent_at ? new Date(status.last_sent_at).toLocaleString("ru-RU") : "—"}
        {status.last_kind ? ` (${status.last_kind})` : ""} · следующий: {status.next_report ?? "—"}
      </p>
      <div className="flex flex-wrap items-center gap-2">
        <button type="button" className={BTN} disabled={busy} onClick={() => void showPreview()}>
          Показать пример
        </button>
        <button
          type="button"
          className={BTN}
          disabled={busy || !status.config.enabled || !status.bot_ready}
          onClick={() => void sendNow()}
        >
          Отправить сейчас
        </button>
        {err && <span className="text-xs text-[var(--danger)]">{err}</span>}
      </div>
      {preview && (
        <pre className="max-h-96 overflow-auto whitespace-pre-wrap rounded-lg border border-[var(--border)] p-3 text-xs">
          {preview.replace(/<[^>]+>/g, "")}
        </pre>
      )}
      <Advanced title="Пороги">
        <Grid>
          <NumField label="Красный: p95 памяти ≥, % ОЗУ" value={draft.mem_red_p95} min={30} max={99} onChange={(v) => set("mem_red_p95", v)} />
          <NumField label="…на скольких днях из окна" value={draft.red_days} min={1} max={30} onChange={(v) => set("red_days", v)} />
          <NumField label="Оранжевый: p95 памяти ≥, % ОЗУ" value={draft.mem_orange_p95} min={20} max={98} onChange={(v) => set("mem_orange_p95", v)} />
          <NumField label="…на скольких днях из окна" value={draft.orange_days} min={1} max={30} onChange={(v) => set("orange_days", v)} />
          <NumField label="Оранжевый: рост пика за неделю, п.п." value={draft.trend_pp} min={1} max={50} onChange={(v) => set("trend_pp", v)} />
          <NumField label="Окно, дней" value={draft.window_days} min={3} max={60} onChange={(v) => set("window_days", v)} />
          <NumField label="Канал «жёлтый», % от тарифа" value={draft.chan_pct} min={50} max={100} onChange={(v) => set("chan_pct", v)} />
          <NumField label="…на скольких днях" value={draft.chan_days} min={1} max={30} onChange={(v) => set("chan_days", v)} />
          <NumField label="Экономия: память p95 <, %" value={draft.save_mem_pct} min={1} max={60} onChange={(v) => set("save_mem_pct", v)} />
          <NumField label="Экономия: CPU p95 <, %" value={draft.save_cpu_pct} min={1} max={60} onChange={(v) => set("save_cpu_pct", v)} />
          <NumField label="Экономия: тариф от, ГБ" value={draft.save_min_gb} min={1} max={64} onChange={(v) => set("save_min_gb", v)} />
          <NumField label="Запас при апгрейде, %" value={draft.headroom_pct} min={0} max={200} onChange={(v) => set("headroom_pct", v)} />
          <NumField label="Часовой пояс, UTC+" value={draft.utc_offset} min={-12} max={14} onChange={(v) => set("utc_offset", v)} />
        </Grid>
      </Advanced>
    </Card>
  );
}
