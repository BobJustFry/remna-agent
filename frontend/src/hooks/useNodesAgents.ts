import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import type { AgentMap, AgentStatus, XrayOnlineMap } from "../types";

// The backend sampler already fetches /metrics from every node every 30s, so a
// 10s poll here mostly duplicated it. Matching that cadence halves the traffic
// without the dashboard feeling any staler.
const INTERVAL_MS = 30000;
/** Extra UI debounce on top of backend hysteresis. */
const UI_OFFLINE_STREAK = 2;

function isAbortError(err: unknown): boolean {
  return (
    (err instanceof DOMException && err.name === "AbortError") ||
    (err instanceof Error && err.name === "AbortError")
  );
}

function mergeAgentStatuses(
  prev: AgentMap,
  patch: AgentMap,
  failStreaks: Map<string, number>,
): AgentMap {
  const out: AgentMap = { ...prev };
  for (const [id, incoming] of Object.entries(patch)) {
    if (!incoming.configured) {
      failStreaks.delete(id);
      out[id] = incoming;
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

function applyBatch(
  prev: AgentMap,
  next: AgentMap,
  failStreaks: Map<string, number>,
): AgentMap {
  const merged = mergeAgentStatuses(prev, next, failStreaks);
  const keep = new Set(Object.keys(next));
  for (const id of Object.keys(merged)) {
    if (!keep.has(id)) delete merged[id];
  }
  return merged;
}

export function useNodesAgents(enabled: boolean) {
  const [statuses, setStatuses] = useState<AgentMap>({});
  const [latestAgentVersion, setLatestAgentVersion] = useState<string | null>(null);
  const [latestWgcfVersion, setLatestWgcfVersion] = useState<string | null>(null);
  const [xrayOnline, setXrayOnline] = useState<XrayOnlineMap>({});
  const inFlight = useRef(false);
  const failStreaks = useRef(new Map<string, number>());
  const abortRef = useRef<AbortController | null>(null);

  const refresh = useCallback(async () => {
    if (!enabled || inFlight.current) return;
    inFlight.current = true;
    const ac = new AbortController();
    abortRef.current = ac;
    const seen = new Set<string>();
    try {
      await api.agentsStream({
        signal: ac.signal,
        onEvent: (ev) => {
          if (ev.type === "node") {
            seen.add(ev.id);
            setStatuses((prev) =>
              mergeAgentStatuses(prev, { [ev.id]: ev.status }, failStreaks.current),
            );
          } else if (ev.type === "done") {
            if (ev.latest_agent_version) setLatestAgentVersion(ev.latest_agent_version);
            if (ev.latest_wgcf_version) setLatestWgcfVersion(ev.latest_wgcf_version);
            if (ev.xray_online) setXrayOnline(ev.xray_online);
            setStatuses((prev) => {
              const out: AgentMap = {};
              for (const id of seen) {
                if (prev[id]) out[id] = prev[id];
              }
              return out;
            });
          }
        },
      });
    } catch (err) {
      if (isAbortError(err)) return;
      try {
        const data = await api.agents();
        setStatuses((prev) => applyBatch(prev, data.statuses, failStreaks.current));
        if (data.latest_agent_version) setLatestAgentVersion(data.latest_agent_version);
        if (data.latest_wgcf_version) setLatestWgcfVersion(data.latest_wgcf_version);
        if (data.xray_online) setXrayOnline(data.xray_online);
      } catch {
        // keep previous
      }
    } finally {
      if (abortRef.current === ac) abortRef.current = null;
      inFlight.current = false;
    }
  }, [enabled]);

  useEffect(() => {
    if (!enabled) return;
    void refresh();
    const id = window.setInterval(() => {
      void refresh();
    }, INTERVAL_MS);
    return () => {
      window.clearInterval(id);
      abortRef.current?.abort();
    };
  }, [enabled, refresh]);

  return { statuses, latestAgentVersion, latestWgcfVersion, xrayOnline, refresh };
}
