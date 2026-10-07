import { useEffect, useState } from "react";
import { api, type YcwatchStatus } from "../api/client";

const INPUT =
  "w-full rounded-lg border border-[var(--border)] bg-[var(--bg)] px-3 py-2 text-sm text-[var(--text)] outline-none focus:border-[var(--accent)]";
const BTN =
  "rounded-lg border border-[var(--border)] px-3 py-1.5 text-sm text-[var(--text)] hover:bg-[var(--bg-row)] disabled:opacity-50";

/** Настройка следилки за Yandex Cloud: баланс облака и ВМ страницы подписки. */
export function YcwatchSettings() {
  const [status, setStatus] = useState<YcwatchStatus | null>(null);
  const [billing, setBilling] = useState("");
  const [folder, setFolder] = useState("");
  const [instance, setInstance] = useState("");
  const [warnRub, setWarnRub] = useState(300);
  const [interval, setIntervalMin] = useState(60);
  const [remindMin, setRemindMin] = useState(60);
  const [remindLowHours, setRemindLowHours] = useState(24);
  const [chatId, setChatId] = useState("");
  const [saKey, setSaKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  function apply(s: YcwatchStatus) {
    setStatus(s);
    setBilling(s.config.billing_account_id);
    setFolder(s.config.folder_id);
    setInstance(s.config.instance_id);
    setWarnRub(s.config.warn_rub);
    setIntervalMin(s.config.interval_min);
    setRemindMin(s.config.remind_min);
    setRemindLowHours(s.config.remind_low_hours);
    setChatId(s.config.chat_id);
    setSaKey("");
  }

  useEffect(() => {
    api.ycwatchStatus().then(apply).catch(() => undefined);
  }, []);

  async function patch(payload: Parameters<typeof api.ycwatchUpdate>[0], note?: string) {
    setBusy(true);
    setMsg(null);
    try {
      apply(await api.ycwatchUpdate(payload));
      if (note) setMsg(note);
    } catch (err) {
      setMsg(err instanceof Error ? err.message : "Не удалось сохранить");
    } finally {
      setBusy(false);
    }
  }

  if (!status) return null;
  const cfg = status.config;

  return (
    <section className="rounded-[var(--radius)] border border-[var(--border)] bg-[var(--bg-elevated)] p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h2 className="text-sm font-semibold">Следилка за Yandex Cloud</h2>
          <p className="mt-1 text-xs leading-relaxed text-[var(--muted)]">
            Раз в час смотрит баланс платёжного аккаунта и статус ВМ страницы подписки. Кончатся
            деньги — Яндекс остановит ВМ, и подписка ляжет (так было 01.10). Предупреждает, когда
            баланс ниже порога; тревога — при нуле/минусе или если ВМ не RUNNING; отдельно — если
            облако несколько раз подряд не отвечает (истёк ключ, сняли роль). Только чтение: ВМ не
            запускает и биллинг не трогает.
          </p>
        </div>
        <label className="flex shrink-0 items-center gap-2 text-sm">
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
            {cfg.enabled ? "включена" : "выключена"}
          </span>
        </label>
      </div>

      <div className="mt-4 space-y-3">
        <div className="flex flex-wrap gap-3">
          <label className="min-w-40 flex-1">
            <span className="text-xs text-[var(--muted)]">Платёжный аккаунт (id)</span>
            <input
              value={billing}
              onChange={(e) => setBilling(e.target.value)}
              spellCheck={false}
              className={`mt-1 font-mono ${INPUT}`}
            />
          </label>
          <label className="min-w-40 flex-1">
            <span className="text-xs text-[var(--muted)]">Каталог с ВМ (folderId)</span>
            <input
              value={folder}
              onChange={(e) => setFolder(e.target.value)}
              spellCheck={false}
              className={`mt-1 font-mono ${INPUT}`}
            />
          </label>
          <label className="min-w-40 flex-1">
            <span className="text-xs text-[var(--muted)]">id ВМ — пусто: все ВМ каталога</span>
            <input
              value={instance}
              onChange={(e) => setInstance(e.target.value)}
              spellCheck={false}
              className={`mt-1 font-mono ${INPUT}`}
            />
          </label>
        </div>

        <div className="flex flex-wrap gap-3">
          <label className="min-w-32 flex-1">
            <span className="text-xs text-[var(--muted)]">Предупреждать ниже, ₽</span>
            <input
              type="number"
              min={0}
              value={warnRub}
              onChange={(e) => setWarnRub(Number(e.target.value))}
              className={`mt-1 ${INPUT}`}
            />
          </label>
          <label className="min-w-32 flex-1">
            <span className="text-xs text-[var(--muted)]">Опрашивать раз в, минут</span>
            <input
              type="number"
              min={5}
              max={1440}
              value={interval}
              onChange={(e) => setIntervalMin(Number(e.target.value))}
              className={`mt-1 ${INPUT}`}
            />
          </label>
          <label className="min-w-32 flex-1">
            <span className="text-xs text-[var(--muted)]">Авария: напоминать раз в, мин (0 — нет)</span>
            <input
              type="number"
              min={0}
              max={1440}
              value={remindMin}
              onChange={(e) => setRemindMin(Number(e.target.value))}
              className={`mt-1 ${INPUT}`}
            />
          </label>
          <label className="min-w-32 flex-1">
            <span className="text-xs text-[var(--muted)]">Мало денег / не отвечает: раз в, ч (0 — нет)</span>
            <input
              type="number"
              min={0}
              max={168}
              value={remindLowHours}
              onChange={(e) => setRemindLowHours(Number(e.target.value))}
              className={`mt-1 ${INPUT}`}
            />
          </label>
        </div>

        <label className="block">
          <span className="text-xs text-[var(--muted)]">
            id чата — пусто: тот же, что у следилки за подпиской
            {cfg.bot_ready ? "" : " (бот пока не настроен — сообщения не уходят)"}
          </span>
          <input
            value={chatId}
            onChange={(e) => setChatId(e.target.value)}
            spellCheck={false}
            placeholder="-1001234567890"
            className={`mt-1 font-mono ${INPUT}`}
          />
        </label>

        <label className="flex items-center gap-2 text-xs text-[var(--muted)]">
          <input
            type="checkbox"
            checked={cfg.notify}
            disabled={busy}
            onChange={(e) => void patch({ notify: e.target.checked })}
            className="h-4 w-4 accent-[var(--accent)]"
          />
          слать в Telegram
        </label>

        <button
          type="button"
          disabled={busy}
          className={BTN}
          onClick={() =>
            void patch(
              {
                billing_account_id: billing.trim(),
                folder_id: folder.trim(),
                instance_id: instance.trim(),
                warn_rub: warnRub,
                interval_min: interval,
                remind_min: remindMin,
                remind_low_hours: remindLowHours,
                chat_id: chatId.trim(),
              },
              "Сохранено",
            )
          }
        >
          Сохранить
        </button>
      </div>

      <div className="mt-5 border-t border-[var(--border)] pt-4">
        <h3 className="text-sm font-semibold">Ключ сервисного аккаунта</h3>
        <p className="mt-1 text-xs leading-relaxed text-[var(--muted)]">
          Authorized key (JSON) узкого СА с ролями compute.viewer и billing.accounts.viewer. Хранится
          зашифрованным и обратно не показывается.{" "}
          {cfg.sa_key_set ? (
            <>
              Сейчас стоит ключ <code className="font-mono">{cfg.sa_key_id}</code> СА{" "}
              <code className="font-mono">{cfg.sa_id}</code>.
            </>
          ) : cfg.sa_key_broken ? (
            <span className="text-[var(--danger)]">
              Ключ в базе есть, но не расшифровывается — похоже, сменился ключ шифрования. Облако мы
              не видим; вставьте ключ заново.
            </span>
          ) : (
            "Ключ не задан — следилка ждёт."
          )}
        </p>
        <textarea
          value={saKey}
          onChange={(e) => setSaKey(e.target.value)}
          spellCheck={false}
          autoComplete="off"
          rows={3}
          placeholder={cfg.sa_key_set ? "вставьте новый JSON, чтобы заменить" : "{ \"id\": …, \"service_account_id\": …, \"private_key\": … }"}
          className={`mt-2 font-mono text-xs ${INPUT}`}
        />
        <div className="mt-2 flex flex-wrap gap-2">
          <button
            type="button"
            disabled={busy || !saKey.trim()}
            className={BTN}
            onClick={() => void patch({ sa_key: saKey.trim() }, "Ключ сохранён")}
          >
            Сохранить ключ
          </button>
          {cfg.sa_key_set && (
            <button
              type="button"
              disabled={busy}
              className={BTN}
              onClick={() => {
                if (window.confirm("Стереть ключ? Следилка перестанет опрашивать облако.")) {
                  void patch({ sa_key: "" }, "Ключ стёрт");
                }
              }}
            >
              Стереть
            </button>
          )}
        </div>
      </div>

      {msg && <p className="mt-3 text-xs text-[var(--muted)]">{msg}</p>}
    </section>
  );
}
