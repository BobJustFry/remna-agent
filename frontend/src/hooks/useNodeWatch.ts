import { useCallback, useEffect, useRef, useState } from "react";
import { api, type NodeWatchStatus } from "../api/client";

// Сама следилка считает раз в полминуты, порог — минуты. Раз в минуту хватает,
// чтобы пометка «недоступна с …» появилась вскоре после тревоги.
const POLL_MS = 60_000;

export function useNodeWatch(enabled: boolean) {
  const [status, setStatus] = useState<NodeWatchStatus | null>(null);
  const timer = useRef<number | null>(null);

  const refresh = useCallback(async () => {
    try {
      const s = await api.nodeWatchStatus();
      setStatus(s);
      return s;
    } catch {
      // Молчим: без ответа рагента пометки просто остаются прежними.
      return null;
    }
  }, []);

  useEffect(() => {
    if (!enabled) return;
    void refresh();
    timer.current = window.setInterval(() => void refresh(), POLL_MS);
    return () => {
      if (timer.current) window.clearInterval(timer.current);
      timer.current = null;
    };
  }, [enabled, refresh]);

  return { status, refresh, setStatus };
}
