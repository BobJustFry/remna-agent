import { memo } from "react";
import { pingToneClass } from "../lib/pingTone";
import type { OnlineStatus } from "../types";

type Props = {
  status?: OnlineStatus;
  compact?: boolean;
};

export const OnlineBadge = memo(function OnlineBadge({ status, compact }: Props) {
  if (!status) {
    return (
      <span
        className={`inline-flex items-center text-[var(--muted)] ${compact ? "gap-1 text-[10px]" : "gap-2 text-xs"}`}
        title="Проверка…"
      >
        <span className={`rounded-full bg-[#3a4a54] ${compact ? "h-1.5 w-1.5" : "h-2 w-2"}`} />
        {compact ? "…" : "…"}
      </span>
    );
  }

  if (status.online) {
    const via = status.method ? ` · ${status.method}` : "";
    const title =
      status.latency_ms != null
        ? `Online · cf_204 ${status.latency_ms} ms${via}`
        : `Online${via}`;
    return (
      <span
        className={`inline-flex items-center font-medium text-[var(--success)] ${compact ? "gap-1 text-[10px]" : "gap-2 text-xs"}`}
        title={title}
      >
        <span
          className={`rounded-full bg-[var(--success)] shadow-[0_0_8px_rgba(61,214,140,0.7)] ${compact ? "h-1.5 w-1.5" : "h-2 w-2"}`}
        />
        {compact ? (
          status.latency_ms != null ? (
            <span className={pingToneClass(status.latency_ms)}>{Math.round(status.latency_ms)}</span>
          ) : (
            <span className={pingToneClass(null)}>—</span>
          )
        ) : (
          <>
            Online
            <span className={pingToneClass(status.latency_ms)}>
              {status.latency_ms != null ? `${Math.round(status.latency_ms)} ms` : "—"}
            </span>
          </>
        )}
      </span>
    );
  }

  return (
    <span
      className={`inline-flex items-center font-medium text-[var(--danger)] ${compact ? "gap-1 text-[10px]" : "gap-2 text-xs"}`}
      title="Offline"
    >
      <span className={`rounded-full bg-[var(--danger)] ${compact ? "h-1.5 w-1.5" : "h-2 w-2"}`} />
      {compact ? "off" : "Offline"}
    </span>
  );
});
