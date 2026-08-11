import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import type { OnlineMap } from "../types";

const INTERVAL_MS = 5000;

export function useNodesOnline(enabled: boolean) {
  const [statuses, setStatuses] = useState<OnlineMap>({});
  const [loading, setLoading] = useState(false);
  const inFlight = useRef(false);

  const refresh = useCallback(async () => {
    if (!enabled || inFlight.current) return;
    inFlight.current = true;
    setLoading(true);
    try {
      const data = await api.online();
      setStatuses(data.statuses);
    } catch {
      // keep previous statuses on transient errors
    } finally {
      inFlight.current = false;
      setLoading(false);
    }
  }, [enabled]);

  useEffect(() => {
    if (!enabled) return;
    void refresh();
    const id = window.setInterval(() => {
      void refresh();
    }, INTERVAL_MS);
    return () => window.clearInterval(id);
  }, [enabled, refresh]);

  return { statuses, loading, refresh };
}
