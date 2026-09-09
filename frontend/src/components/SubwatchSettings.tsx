import { useEffect, useState } from "react";
import { api, type SubwatchStatus } from "../api/client";

const INPUT =
  "w-full rounded-lg border border-[var(--border)] bg-[var(--bg)] px-3 py-2 text-sm text-[var(--text)] outline-none focus:border-[var(--accent)]";
const BTN =
  "rounded-lg border border-[var(--border)] px-3 py-1.5 text-sm text-[var(--text)] hover:bg-[var(--bg-row)] disabled:opacity-50";

/** Настройка следилки за подпиской и телеграм-уведомлений. */
export function SubwatchSettings() {
  const [status, setStatus] = useState<SubwatchStatus | null>(null);
  const [url, setUrl] = useState("");
  const [originUrl, setOriginUrl] = useState("");
  const [chatId, setChatId] = useState("");
  const [botToken, setBotToken] = useState("");
  const [interval, setIntervalMin] = useState(15);
  const [minHosts, setMinHosts] = useState(3);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  function apply(s: SubwatchStatus) {
    setStatus(s);
    setUrl(s.config.url);
    setOriginUrl(s.config.origin_url);
    setChatId(s.config.chat_id);
    setIntervalMin(s.config.interval_min);
    setMinHosts(s.config.min_hosts);
    setBotToken("");
  }

  useEffect(() => {
    api.subwatchStatus().then(apply).catch(() => undefined);
  }, []);

  async function patch(payload: Parameters<typeof api.subwatchUpdate>[0], note?: string) {
    setBusy(true);
    setMsg(null);
    try {
      apply(await api.subwatchUpdate(payload));
      if (note) setMsg(note);
    } catch (err) {
      setMsg(err instanceof Error ? err.message : "Не удалось сохранить");
    } finally {
      setBusy(false);
    }
  }

  async function testTelegram() {
    setBusy(true);
    setMsg(null);
    try {
      await api.subwatchTelegramTest();
      setMsg("Отправил — проверьте группу");
    } catch (err) {
      setMsg(err instanceof Error ? err.message : "Не отправилось");
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
          <h2 className="text-sm font-semibold">Следилка за подпиской</h2>
          <p className="mt-1 text-xs leading-relaxed text-[var(--muted)]">
            Забирает подписку так же, как её забирает клиент, и смотрит не на код ответа, а на
            то, есть ли внутри конфиги. Единственная поломка, о которой никто не сообщит: уже
            подключённые продолжают работать на скачанном конфиге, а новые не подключаются вовсе.
          </p>
        </div>
        <label className="flex shrink-0 items-center gap-2 text-sm">
          <input
            type="checkbox"
            checked={cfg.enabled}
            disabled={busy || !cfg.url}
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
        <label className="block">
          <span className="text-xs text-[var(--muted)]">Ссылка на подписку — та, что у клиента</span>
          <input
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            spellCheck={false}
            placeholder="https://sub.example.com/sub/xxxxxxxx"
            className={`mt-1 font-mono ${INPUT}`}
          />
        </label>

        <label className="block">
          <span className="text-xs text-[var(--muted)]">
            Та же подписка мимо CDN, напрямую с ориджина — не обязательно, но по ней видно, чья
            поломка. Щупается только когда основная не ответила.
          </span>
          <input
            value={originUrl}
            onChange={(e) => setOriginUrl(e.target.value)}
            spellCheck={false}
            placeholder="https://origin-sub.example.com/sub/xxxxxxxx"
            className={`mt-1 font-mono ${INPUT}`}
          />
        </label>

        <div className="flex flex-wrap gap-3">
          <label className="min-w-32 flex-1">
            <span className="text-xs text-[var(--muted)]">Проверять раз в, минут</span>
            <input
              type="number"
              min={1}
              max={1440}
              value={interval}
              onChange={(e) => setIntervalMin(Number(e.target.value))}
              className={`mt-1 ${INPUT}`}
            />
          </label>
          <label className="min-w-32 flex-1">
            <span className="text-xs text-[var(--muted)]">Конфигов должно быть не меньше</span>
            <input
              type="number"
              min={1}
              max={1000}
              value={minHosts}
              onChange={(e) => setMinHosts(Number(e.target.value))}
              className={`mt-1 ${INPUT}`}
            />
          </label>
        </div>

        <button
          type="button"
          disabled={busy}
          className={BTN}
          onClick={() =>
            void patch(
              {
                url: url.trim(),
                origin_url: originUrl.trim(),
                interval_min: interval,
                min_hosts: minHosts,
              },
              "Сохранено",
            )
          }
        >
          Сохранить
        </button>
      </div>

      <div className="mt-5 border-t border-[var(--border)] pt-4">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h3 className="text-sm font-semibold">Уведомления в Telegram</h3>
          <label className="flex items-center gap-2 text-xs text-[var(--muted)]">
            <input
              type="checkbox"
              checked={cfg.notify}
              disabled={busy}
              onChange={(e) => void patch({ notify: e.target.checked })}
              className="h-4 w-4 accent-[var(--accent)]"
            />
            слать в чат
          </label>
        </div>
        <p className="mt-1 text-xs leading-relaxed text-[var(--muted)]">
          Пишем только на переломах: упало — сообщение, поднялось — сообщение. Пока лежит,
          напоминаем раз в час, чтобы группа не превратилась в поток. Токен бота хранится
          зашифрованным и обратно не показывается.
        </p>

        <div className="mt-3 space-y-3">
          <label className="block">
            <span className="text-xs text-[var(--muted)]">
              Токен бота{cfg.bot_token_set ? " — задан, введите новый чтобы заменить" : ""}
            </span>
            <input
              value={botToken}
              onChange={(e) => setBotToken(e.target.value)}
              type="password"
              spellCheck={false}
              autoComplete="off"
              placeholder={cfg.bot_token_set ? "••••••••" : "1234567890:AA..."}
              className={`mt-1 font-mono ${INPUT}`}
            />
          </label>
          <label className="block">
            <span className="text-xs text-[var(--muted)]">
              id чата или группы — у группы он отрицательный
            </span>
            <input
              value={chatId}
              onChange={(e) => setChatId(e.target.value)}
              spellCheck={false}
              placeholder="-1001234567890"
              className={`mt-1 font-mono ${INPUT}`}
            />
          </label>
          <div className="flex flex-wrap gap-2">
            <button
              type="button"
              disabled={busy}
              className={BTN}
              onClick={() =>
                void patch(
                  {
                    chat_id: chatId.trim(),
                    ...(botToken.trim() ? { bot_token: botToken.trim() } : {}),
                  },
                  "Сохранено",
                )
              }
            >
              Сохранить бота
            </button>
            <button
              type="button"
              disabled={busy || !cfg.bot_token_set || !cfg.chat_id}
              className={BTN}
              onClick={() => void testTelegram()}
            >
              Отправить тестовое
            </button>
          </div>
        </div>
      </div>

      {msg && <p className="mt-3 text-xs text-[var(--muted)]">{msg}</p>}
    </section>
  );
}
