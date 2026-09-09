import { useCallback, useEffect, useRef, useState } from "react";
import { api, type SubwatchStatus } from "../api/client";

// Опрашиваем чаще, чем ходит сама проверка: интерфейс должен показать
// свежий вердикт вскоре после того, как он появился, а не через четверть часа.
const POLL_MS = 60_000;

export function useSubwatch(enabled: boolean) {
  const [status, setStatus] = useState<SubwatchStatus | null>(null);
  const timer = useRef<number | null>(null);

  const refresh = useCallback(async () => {
    try {
      const s = await api.subwatchStatus();
      setStatus(s);
      return s;
    } catch {
      // Молчим: недоступный рагент виден и без этого, а красная полоса
      // про подписку в такой момент только собьёт с толку.
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
