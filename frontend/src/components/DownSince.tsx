import { memo } from "react";
import type { NodeDown } from "../api/client";
import { clockTime, signalsText, spanText } from "../lib/nodeWatch";

type Props = {
  down?: NodeDown;
  compact?: boolean;
};

/**
 * Пометка у ноды, от которой рагент не получает данных: «недоступна с 03:12».
 *
 * Красная — тревога уже поднята. Жёлтая — молчит, но порог ещё не вышел: так
 * видно, что следилка ноду уже заметила, хотя в чат пока не писала.
 */
export const DownSince = memo(function DownSince({ down, compact }: Props) {
  if (!down) return null;
  const since = clockTime(down.since);
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
