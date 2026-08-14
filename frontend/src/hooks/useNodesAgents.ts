import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import type { AgentMap, AgentStatus } from "../types";

const INTERVAL_MS = 10000;
/** Extra UI debounce on top of backend hysteresis. */
const UI_OFFLINE_STREAK = 2;

function mergeAgentStatuses(
  prev: AgentMap,
  next: AgentMap,
  failStreaks: Map<string, number>,
): AgentMap {
  const out: AgentMap = { ...next };
  for (const [id, incoming] of Object.entries(next)) {
    if (!incoming.configured) {
      failStreaks.delete(id);
      continue;
    }
    if (incoming.present) {
      failStreaks.set(id, 0);
      out[id] = incoming;
      continue;
    }
    const hardAuth =
      incoming.error?.toLowerCase().includes("токен") ||
      incoming.error?.toLowerCase().includes("авторизац");
    if (hardAuth) {
      failStreaks.set(id, UI_OFFLINE_STREAK);
      out[id] = incoming;
      continue;
    }

    const old: AgentStatus | undefined = prev[id];
    const streak = (failStreaks.get(id) ?? 0) + 1;
    failStreaks.set(id, streak);
    if (streak < UI_OFFLINE_STREAK && old?.present) {
      out[id] = old;
    } else {
      out[id] = incoming;
    }
  }
  return out;
}

export function useNodesAgents(enabled: boolean) {
  const [statuses, setStatuses] = useState<AgentMap>({});
  const [latestAgentVersion, setLatestAgentVersion] = useState<string | null>(null);
  const inFlight = useRef(false);
  const failStreaks = useRef(new Map<string, number>());

  const refresh = useCallback(async () => {
    if (!enabled || inFlight.current) return;
    inFlight.current = true;
    try {
      const data = await api.agents();
      setStatuses((prev) => mergeAgentStatuses(prev, data.statuses, failStreaks.current));
      if (data.latest_agent_version) setLatestAgentVersion(data.latest_agent_version);
    } catch {
      // keep previous
    } finally {
      inFlight.current = false;
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

  return { statuses, latestAgentVersion, refresh };
}
