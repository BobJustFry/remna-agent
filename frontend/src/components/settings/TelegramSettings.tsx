import { useEffect, useMemo, useState } from "react";
import {
  api,
  type DetectedChat,
  type MemWatchStatus,
  type NodeWatchStatus,
  type SubwatchStatus,
  type YcwatchStatus,
} from "../../api/client";
import type { SharingConfig } from "../../types";
import { useSettingsBlock } from "./SaveContext";
import { Advanced, BTN, Card, Grid, NumField, TextField, useDraft } from "./ui";

const TAB = "telegram";

/**
 * Всё про Telegram-бота в одном месте. Токен, прокси, общий чат и часовой пояс живут в
 * конфиге следилки за подпиской (общий чат), часовой пояс пишется в следилки нод и памяти;
 * отдельные чаты — в конфигах нод, облака, шаринга и памяти. Пустой чат = общий.
 */
export function TelegramSettings() {
  const [sub, setSub] = useState<SubwatchStatus | null>(null);
  const [nodes, setNodes] = useState<NodeWatchStatus | null>(null);
  const [mem, setMem] = useState<MemWatchStatus | null>(null);
  const [yc, setYc] = useState<YcwatchStatus | null>(null);
  const [sharing, setSharing] = useState<SharingConfig | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [chats, setChats] = useState<DetectedChat[] | null>(null);
  const [chatHint, setChatHint] = useState<string | null>(null);

  useEffect(() => {
    void Promise.allSettled([
      api.subwatchStatus().then(setSub),
      api.nodeWatchStatus().then(setNodes),
      api.memWatchStatus().then(setMem),
      api.ycwatchStatus().then(setYc),
      api.sharingSettings().then(setSharing),
    ]).finally(() => setLoaded(true));
  }, []);

  const savedProxy = sub?.config.proxy_set ? sub.config.proxy_masked : "";

  const saved = useMemo(
    () =>
      loaded
        ? {
            bot_token: "",
            proxy: savedProxy,
            chat_id: sub?.config.chat_id ?? "",
            utc_offset: nodes?.config.utc_offset ?? mem?.config.utc_offset ?? 3,
            chat_nodes: nodes?.config.chat_id ?? "",
            chat_yc: yc?.config.chat_id ?? "",
            chat_sharing: sharing?.chat_id ?? "",
            chat_mem: mem?.config.chat_id ?? "",
          }
        : null,
    [loaded, savedProxy, sub, nodes, mem, yc, sharing],
  );
  const { draft, set, changed, dirty, reset } = useDraft(saved);

  async function save() {
    const c = changed;
    const jobs: Promise<unknown>[] = [];
    const names: string[] = [];
    const run = (name: string, p: Promise<unknown>) => {
      names.push(name);
      jobs.push(p);
    };

    if ("bot_token" in c || "proxy" in c || "chat_id" in c) {
      run(
        "бот и общий чат",
        api
          .subwatchUpdate({
            ...("chat_id" in c ? { chat_id: (c.chat_id as string).trim() } : {}),
            ...("bot_token" in c && (c.bot_token as string).trim()
              ? { bot_token: (c.bot_token as string).trim() }
              : {}),
            ...("proxy" in c ? { telegram_proxy: (c.proxy as string).trim() } : {}),
          })
          .then((s) => {
            setSub(s);
            if ("bot_token" in c) set("bot_token", "");
          }),
      );
    }
    if ("utc_offset" in c || "chat_nodes" in c) {
      run(
        "ноды",
        api
          .nodeWatchUpdate({
            ...("utc_offset" in c ? { utc_offset: c.utc_offset as number } : {}),
            ...("chat_nodes" in c ? { chat_id: (c.chat_nodes as string).trim() } : {}),
          })
          .then(setNodes),
      );
    }
    if ("utc_offset" in c || "chat_mem" in c) {
      run(
        "память",
        api
          .memWatchUpdate({
            ...("utc_offset" in c ? { utc_offset: c.utc_offset as number } : {}),
            ...("chat_mem" in c ? { chat_id: (c.chat_mem as string).trim() } : {}),
          })
          .then(setMem),
      );
    }
    if ("chat_yc" in c) {
      run("облако", api.ycwatchUpdate({ chat_id: (c.chat_yc as string).trim() }).then(setYc));
    }
    if ("chat_sharing" in c) {
      run(
        "шаринг",
        api.sharingUpdate({ chat_id: (c.chat_sharing as string).trim() }).then(setSharing),
      );
    }

    const res = await Promise.allSettled(jobs);
    const errors = res.flatMap((r, i) =>
      r.status === "rejected"
        ? [`${names[i]}: ${r.reason instanceof Error ? r.reason.message : "ошибка"}`]
        : [],
    );
    if (errors.length) throw new Error(errors.join("; "));
  }

  useSettingsBlock("telegram", {
    label: "Telegram",
    tab: TAB,
    dirty,
    save,
    reset: () => {
      reset();
      setMsg(null);
    },
  });

  // Для проверок: изменённое поле уходит в теле (временно), нетронутое — не уходит.
  function probeProxy(): string | undefined {
    if (!draft) return undefined;
    return draft.proxy.trim() !== savedProxy ? draft.proxy.trim() : undefined;
  }

  async function action(fn: () => Promise<void>, fail: string) {
    setBusy(true);
    setMsg(null);
    try {
      await fn();
    } catch (err) {
      setMsg(err instanceof Error ? err.message : fail);
    } finally {
      setBusy(false);
    }
  }

  if (!loaded || !draft) return <p className="text-sm text-[var(--muted)]">Загрузка…</p>;

  const cfg = sub?.config;
  const botSet = !!cfg?.bot_token_set;
  // Тестовое уходит по сохранённым значениям, поэтому при правках бота сначала сохранить.
  const botDirty = "bot_token" in changed || "proxy" in changed || "chat_id" in changed;

  return (
    <div className="space-y-4">
      <Card title="Telegram-бот">
        <TextField
          label={`Токен бота${botSet ? " (задан — введите новый, чтобы заменить)" : ""}`}
          hint="Хранится зашифрованным и обратно не показывается. Пустое поле — токен не меняется."
          type="password"
          value={draft.bot_token}
          onChange={(v) => set("bot_token", v)}
          placeholder={botSet ? "••••••••" : "1234567890:AA..."}
          disabled={!sub}
        />
        <TextField
          label="Прокси для Telegram (необязательно)"
          hint="Показан сохранённый адрес без логина и пароля. Не меняете поле — логин и пароль остаются. Очистите поле и сохраните — прокси сотрётся."
          value={draft.proxy}
          onChange={(v) => set("proxy", v)}
          placeholder="socks5://user:pass@host:1080 или http://host:3128"
          disabled={!sub}
        />
        <Grid>
          <TextField
            label="Общий id чата"
            hint="У группы id отрицательный. Сюда идут все уведомления, если у сторожа не задан свой чат."
            value={draft.chat_id}
            onChange={(v) => set("chat_id", v)}
            placeholder="-1001234567890"
            disabled={!sub}
          />
          <NumField
            label="Часовой пояс, UTC+ (МСК — 3)"
            hint="Время в сообщениях и журналах. Записывается в сторожей нод и памяти."
            value={draft.utc_offset}
            min={-12}
            max={14}
            onChange={(v) => set("utc_offset", v)}
          />
        </Grid>
        {nodes && mem && nodes.config.utc_offset !== mem.config.utc_offset && (
          <p className="text-xs text-[var(--warning)]">
            Сейчас пояса разные: у нод UTC{nodes.config.utc_offset >= 0 ? "+" : ""}
            {nodes.config.utc_offset}, у памяти UTC{mem.config.utc_offset >= 0 ? "+" : ""}
            {mem.config.utc_offset}. После сохранения изменённого значения станут одинаковыми.
          </p>
        )}

        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            disabled={busy || !botSet}
            className={BTN}
            title="Работает с тем, что сейчас в поле прокси"
            onClick={() =>
              void action(async () => {
                setMsg((await api.subwatchCheckProxy(probeProxy())).message);
              }, "Telegram недоступен")
            }
          >
            Проверить прокси
          </button>
          <button
            type="button"
            disabled={busy || !botSet}
            className={BTN}
            title="Читает последние сообщения боту; прокси берёт из поля"
            onClick={() =>
              void action(async () => {
                try {
                  const r = await api.subwatchDetectChat(probeProxy());
                  setChats(r.chats);
                  setChatHint(r.hint);
                } catch (err) {
                  setChats(null);
                  throw err;
                }
              }, "Telegram недоступен")
            }
          >
            Определить мой id
          </button>
          <button
            type="button"
            disabled={busy || !botSet || !cfg?.chat_id || botDirty}
            className={BTN}
            title={botDirty ? "Сначала сохраните изменения бота" : "Уходит в общий чат"}
            onClick={() =>
              void action(async () => {
                await api.subwatchTelegramTest();
                setMsg("Отправил — проверьте чат");
              }, "Не отправилось")
            }
          >
            Отправить тестовое
          </button>
        </div>
        {msg && <p className="text-xs text-[var(--muted)]">{msg}</p>}

        {chats && (
          <div className="rounded-lg border border-[var(--border)] p-2 text-xs text-[var(--text)]">
            {chats.length === 0 ? (
              <p className="text-[var(--muted)]">{chatHint}</p>
            ) : (
              <ul className="space-y-1">
                {chats.map((c) => (
                  <li key={c.id} className="flex flex-wrap items-center gap-2">
                    <span className="font-mono">{c.id}</span>
                    <span className="text-[var(--muted)]">
                      {c.type}
                      {c.title ? ` · ${c.title}` : ""}
                      {c.username ? ` · @${c.username}` : ""}
                    </span>
                    <button type="button" className={BTN} onClick={() => set("chat_id", String(c.id))}>
                      Подставить в общий чат
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}
      </Card>

      <Advanced title="Дополнительно: отдельные чаты для сторожей">
        <p className="text-xs text-[var(--muted)]">
          Пусто — уведомления идут в общий чат. Следилка за подпиской всегда пишет в общий.
        </p>
        <Grid>
          <TextField
            label="Ноды"
            value={draft.chat_nodes}
            onChange={(v) => set("chat_nodes", v)}
            placeholder="как общий"
            disabled={!nodes}
          />
          <TextField
            label="Облако (Yandex)"
            value={draft.chat_yc}
            onChange={(v) => set("chat_yc", v)}
            placeholder="как общий"
            disabled={!yc}
          />
          <TextField
            label="Шаринг"
            value={draft.chat_sharing}
            onChange={(v) => set("chat_sharing", v)}
            placeholder="как общий"
            disabled={!sharing}
          />
          <TextField
            label="Память"
            value={draft.chat_mem}
            onChange={(v) => set("chat_mem", v)}
            placeholder="как общий"
            disabled={!mem}
          />
        </Grid>
      </Advanced>
    </div>
  );
}
