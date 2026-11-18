import { useCallback, useEffect, useState } from "react";
import { api, type MemWatchStatus } from "../api/client";

const POLL_MS = 30_000;

/** Состояние памятьсторожа: опрос раз в полминуты, как и сам сэмплер. */
export function useMemWatch(): { status: MemWatchStatus | null; refresh: () => Promise<void>; setStatus: (s: MemWatchStatus) => void } {
  const [status, setStatus] = useState<MemWatchStatus | null>(null);

  const refresh = useCallback(async () => {
    try {
      setStatus(await api.memWatchStatus());
    } catch {
      /* без ответа рагента пометки остаются прежними */
    }
  }, []);

  useEffect(() => {
    void refresh();
    const id = window.setInterval(() => void refresh(), POLL_MS);
    return () => window.clearInterval(id);
  }, [refresh]);

  return { status, refresh, setStatus };
}
