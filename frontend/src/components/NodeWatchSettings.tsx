import { useEffect, useState } from "react";
import { api, type NodeWatchStatus } from "../api/client";
import type { NodeItem } from "../types";

const INPUT =
  "w-full rounded-lg border border-[var(--border)] bg-[var(--bg)] px-3 py-2 text-sm text-[var(--text)] outline-none focus:border-[var(--accent)]";
const BTN =
  "rounded-lg border border-[var(--border)] px-3 py-1.5 text-sm text-[var(--text)] hover:bg-[var(--bg-row)] disabled:opacity-50";

type Props = {
  nodes: NodeItem[];
  /** Настройки поменялись — пометки «недоступна с …» стоит перечитать. */
  onChanged?: () => void;
};

/** Настройка следилки за нодами: порог, напоминания, чат, исключения. */
export function NodeWatchSettings({ nodes, onChanged }: Props) {
  const [status, setStatus] = useState<NodeWatchStatus | null>(null);
  const [threshold, setThreshold] = useState(10);
  const [remind, setRemind] = useState(6);
  const [offset, setOffset] = useState(5);
  const [chatId, setChatId] = useState("");
  const [exclude, setExclude] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  function apply(s: NodeWatchStatus) {
    setStatus(s);
    setThreshold(s.config.threshold_min);
    setRemind(s.config.remind_hours);
    setOffset(s.config.utc_offset);
    setChatId(s.config.chat_id);
    setExclude(new Set(s.config.exclude));
  }

  useEffect(() => {
    api.nodeWatchStatus().then(apply).catch(() => undefined);
  }, []);

  async function patch(payload: Parameters<typeof api.nodeWatchUpdate>[0], note?: string) {
    setBusy(true);
    setMsg(null);
    try {
      apply(await api.nodeWatchUpdate(payload));
      if (note) setMsg(note);
      onChanged?.();
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
      await api.nodeWatchTelegramTest();
      setMsg("Отправил — проверьте чат");
    } catch (err) {
      setMsg(err instanceof Error ? err.message : "Не отправилось");
    } finally {
      setBusy(false);
    }
  }

  function toggle(id: string) {
    setExclude((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  if (!status) return null;
  const cfg = status.config;
  // Нода удалена, а в исключениях осталась — при сохранении такой хвост уходит.
  const known = new Set(nodes.map((n) => n.id));
  const excluded = [...exclude].filter((id) => known.has(id));
  const sorted = [...nodes].sort((a, b) => a.name.localeCompare(b.name));

  return (
    <section className="rounded-[var(--radius)] border border-[var(--border)] bg-[var(--bg-elevated)] p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h2 className="text-sm font-semibold">Следилка за нодами</h2>
          <p className="mt-1 text-xs leading-relaxed text-[var(--muted)]">
            Хостер может выключить сервер на сутки, и видно это только по нулю в счётчике
            пользователей. Рагент и так опрашивает агент на каждой ноде раз в полминуты: если нода
            молчит дольше порога — пишет в чат, когда поднялась — пишет ещё раз. Пропущенный опрос
            тревогой не считается.
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
          <label className="min-w-32 flex-1">
            <span className="text-xs text-[var(--muted)]">Тревога, если молчит дольше, минут</span>
            <input
              type="number"
              min={1}
              max={1440}
              value={threshold}
              onChange={(e) => setThreshold(Number(e.target.value))}
              className={`mt-1 ${INPUT}`}
            />
          </label>
          <label className="min-w-32 flex-1">
            <span className="text-xs text-[var(--muted)]">
              Напоминать раз в, часов (0 — не напоминать)
            </span>
            <input
              type="number"
              min={0}
              max={168}
              value={remind}
              onChange={(e) => setRemind(Number(e.target.value))}
              className={`mt-1 ${INPUT}`}
            />
          </label>
        </div>

        <label className="flex items-start gap-2 text-sm">
          <input
            type="checkbox"
            checked={cfg.skip_panel_disabled}
            disabled={busy}
            onChange={(e) => void patch({ skip_panel_disabled: e.target.checked })}
            className="mt-0.5 h-4 w-4 accent-[var(--accent)]"
          />
          <span>
            Не тревожить по нодам, выключенным в панели Remnawave
            <span className="block text-xs text-[var(--muted)]">
              Выключили ноду в панели на обслуживание — её молчание не авария.
            </span>
          </span>
        </label>

        <label className="flex items-start gap-2 text-sm">
          <input
            type="checkbox"
            checked={cfg.panel_alive}
            disabled={busy}
            onChange={(e) => void patch({ panel_alive: e.target.checked })}
            className="mt-0.5 h-4 w-4 accent-[var(--accent)]"
          />
          <span>
            Считать ноду живой, если её видит панель
            <span className="block text-xs text-[var(--muted)]">
              Рагент стоит в России, и до части зарубежных нод ему не достать — хотя они работают.
              Если агент и SSH молчат, а панель видит ноду подключённой, тревоги нет: за такой нодой
              следим через панель и тревожим, когда её потеряет и панель.
            </span>
          </span>
        </label>

        <details>
          <summary className="cursor-pointer text-xs text-[var(--muted)] hover:text-[var(--text)]">
            Исключения — ноды, по которым не тревожить
            {excluded.length > 0 ? `: ${excluded.length}` : ""}
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
                      checked={exclude.has(n.id)}
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
          Одно сообщение, когда нода упала, и одно, когда поднялась — с тем, сколько лежала. Ноды,
          упавшие вместе, идут одним списком. Если разом замолчало больше половины парка, это,
          скорее всего, ослеп сам рагент: придёт одно сообщение про парк, а не по каждой ноде. Бот —
          тот же, что у следилки за подпиской.
        </p>
        {!cfg.bot_ready && (
          <p className="mt-2 rounded-lg border border-[rgba(251,191,36,0.45)] bg-[rgba(251,191,36,0.10)] px-3 py-2 text-xs text-[var(--warning)]">
            Бот не настроен — сообщения не отправляются. Задайте токен бота и id чата выше, в
            следилке за подпиской (или id чата здесь). Журнал простоев при этом ведётся; когда бот
            появится, придёт одно сообщение о нодах, которые в тот момент ещё лежат.
          </p>
        )}

        <div className="mt-3 flex flex-wrap items-end gap-3">
          <label className="min-w-40 flex-[2]">
            <span className="text-xs text-[var(--muted)]">
              id чата — пусто, тот же, что у следилки за подпиской
            </span>
            <input
              value={chatId}
              onChange={(e) => setChatId(e.target.value)}
              spellCheck={false}
              placeholder="-1001234567890"
              className={`mt-1 font-mono ${INPUT}`}
            />
          </label>
          <label className="min-w-32 flex-1">
            <span className="text-xs text-[var(--muted)]">Часовой пояс, UTC+ (МСК — 3)</span>
            <input
              type="number"
              min={-12}
              max={14}
              value={offset}
              onChange={(e) => setOffset(Number(e.target.value))}
              className={`mt-1 ${INPUT}`}
            />
          </label>
        </div>
      </div>

      <div className="mt-4 flex flex-wrap items-center gap-2">
        <button
          type="button"
          disabled={busy}
          className={BTN}
          onClick={() =>
            void patch(
              {
                threshold_min: threshold,
                remind_hours: remind,
                utc_offset: offset,
                chat_id: chatId.trim(),
                exclude: excluded,
              },
              "Сохранено",
            )
          }
        >
          Сохранить
        </button>
        <button
          type="button"
          disabled={busy || !cfg.bot_ready}
          className={BTN}
          onClick={() => void testTelegram()}
        >
          Отправить тестовое
        </button>
        {msg && <span className="text-xs text-[var(--muted)]">{msg}</span>}
      </div>
    </section>
  );
}
