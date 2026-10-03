import { useCallback, useEffect, useRef, useState } from "react";
import { api, type YcwatchStatus } from "../api/client";

// Само облако рагент опрашивает раз в час; интерфейс читает только нашу базу,
// поэтому можно чаще — свежий вердикт появится через пару минут, не через час.
const POLL_MS = 120_000;

export function useYcwatch(enabled: boolean) {
  const [status, setStatus] = useState<YcwatchStatus | null>(null);
  const timer = useRef<number | null>(null);

  const refresh = useCallback(async () => {
    try {
      const s = await api.ycwatchStatus();
      setStatus(s);
      return s;
    } catch {
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
