import { memo } from "react";
import { clockTime, signalsText, spanText, type NodeMark } from "../lib/nodeWatch";

type Props = {
  down?: NodeMark;
  compact?: boolean;
};

/**
 * Пометка у ноды, от которой рагент не получает данных: «недоступна с 03:12».
 *
 * Красная — тревога уже поднята. Жёлтая — молчит, но порог ещё не вышел: так
 * видно, что следилка ноду уже заметила, хотя в чат пока не писала. Серая —
 * нода жива, просто рагент до неё не дотягивается и следит за ней через панель.
 */
export const DownSince = memo(function DownSince({ down, compact }: Props) {
  if (!down) return null;
  const since = clockTime(down.since);
  if (down.kind === "via_panel") {
    const title = down.panel_seen
      ? `Рагент не дотягивается до ноды с ${since}, но панель видит её подключённой — нода жива. Тревога будет, если её потеряет и панель.`
      : `Рагент не дотягивается до ноды с ${since}, а панель сейчас не отвечает — жива ли нода, неизвестно. Тревоги не будет, пока панель не вернётся.`;
    return (
      <span
        title={title}
        className={`inline-flex shrink-0 items-center whitespace-nowrap rounded border border-[var(--border)] bg-[var(--bg-row)] font-medium text-[var(--muted)] ${
          compact ? "px-1 text-[9px] leading-[14px]" : "px-1.5 py-0.5 text-[11px]"
        }`}
      >
        {compact
          ? down.panel_seen
            ? "через панель"
            : "панель молчит"
          : down.panel_seen
            ? "рагент не достаёт, панель видит"
            : "рагент не достаёт, панель молчит"}
      </span>
    );
  }
  const lasted = spanText(Date.now() - new Date(down.since).getTime());
  const what = signalsText(down.signals);
  const title = [
    down.alerted ? `Недоступна с ${since}` : `Не отвечает с ${since} — порог ещё не вышел`,
    lasted ? `уже ${lasted}` : "",
    what,
  ]
    .filter(Boolean)
    .join(" · ");
  const tone = down.alerted
    ? "border-[rgba(240,113,120,0.45)] bg-[rgba(240,113,120,0.12)] text-[var(--danger)]"
    : "border-[rgba(251,191,36,0.45)] bg-[rgba(251,191,36,0.10)] text-[var(--warning)]";

  return (
    <span
      title={title}
      className={`inline-flex shrink-0 items-center whitespace-nowrap rounded border font-medium tabular-nums ${tone} ${
        compact ? "px-1 text-[9px] leading-[14px]" : "px-1.5 py-0.5 text-[11px]"
      }`}
    >
      {compact ? `с ${since}` : `${down.alerted ? "недоступна" : "молчит"} с ${since}`}
    </span>
  );
});
