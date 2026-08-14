import { useCallback, useEffect, useRef, useState } from "react";
import { api, type RemnawaveVersions } from "../api/client";

const POLL_MS = 30 * 60 * 1000;

export function useRemnawaveVersions(enabled = true) {
  const [versions, setVersions] = useState<RemnawaveVersions | null>(null);
  const [loading, setLoading] = useState(false);
  const mounted = useRef(true);

  const refresh = useCallback(async (force = false) => {
    setLoading(true);
    try {
      const data = await api.getRemnawaveVersions(force);
      if (mounted.current) setVersions(data);
      return data;
    } finally {
      if (mounted.current) setLoading(false);
    }
  }, []);

  useEffect(() => {
    mounted.current = true;
    if (!enabled) return;
    void refresh(false).catch(() => undefined);
    const id = window.setInterval(() => {
      void refresh(false).catch(() => undefined);
    }, POLL_MS);
    return () => {
      mounted.current = false;
      window.clearInterval(id);
    };
  }, [enabled, refresh]);

  return { versions, loading, refresh };
}

export function normalizeVersion(raw: string | null | undefined): string | null {
  if (!raw) return null;
  let v = raw.trim();
  if (v.toLowerCase().startsWith("v") && v.length > 1 && /\d/.test(v[1])) {
    v = v.slice(1);
  }
  if (!v) return null;
  const low = v.toLowerCase();
  // Unknown / placeholder — do not compare for update prompts
  if (low === "latest" || low === "dev" || low === "0.0.0" || low === "0.0.1") {
    return null;
  }
  return v;
}

/** True when installed version is known and differs from latest. */
export function versionNeedsUpdate(
  installed: string | null | undefined,
  latest: string | null | undefined,
): boolean {
  const a = normalizeVersion(installed);
  const b = normalizeVersion(latest);
  if (!a || !b) return false;
  return a !== b;
}

/** RemnaNode outdated vs latest GitHub node release. */
export function remnanodeNeedsUpdate(
  installed: string | null | undefined,
  latestNode: string | null | undefined,
): boolean {
  return versionNeedsUpdate(installed, latestNode);
}

export function agentNeedsUpdate(
  installed: string | null | undefined,
  latestAgent: string | null | undefined,
): boolean {
  return versionNeedsUpdate(installed, latestAgent);
}
