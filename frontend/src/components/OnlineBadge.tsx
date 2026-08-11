import { memo } from "react";
import type { OnlineStatus } from "../types";

type Props = {
  status?: OnlineStatus;
};

export const OnlineBadge = memo(function OnlineBadge({ status }: Props) {
  if (!status) {
    return (
      <span className="inline-flex items-center gap-2 text-xs text-[var(--muted)]">
        <span className="h-2 w-2 rounded-full bg-[#3a4a54]" />
        …
      </span>
    );
  }

  if (status.online) {
    return (
      <span className="inline-flex items-center gap-2 text-xs font-medium text-[var(--success)]">
        <span className="h-2 w-2 rounded-full bg-[var(--success)] shadow-[0_0_8px_rgba(61,214,140,0.7)]" />
        Online
        {status.latency_ms != null && (
          <span className="font-normal text-[var(--muted)]">{status.latency_ms} ms</span>
        )}
      </span>
    );
  }

  return (
    <span className="inline-flex items-center gap-2 text-xs font-medium text-[var(--danger)]">
      <span className="h-2 w-2 rounded-full bg-[var(--danger)]" />
      Offline
    </span>
  );
});
