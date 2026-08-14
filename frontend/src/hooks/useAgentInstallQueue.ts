import { useCallback, useRef, useState } from "react";
import { api, type AgentInstallStreamEvent } from "../api/client";
import { getQueueConcurrency } from "../lib/concurrency";
import type { NodeItem } from "../types";

export type InstallPhase = "queued" | "running" | "done" | "error";

export type InstallJob = {
  nodeId: string;
  nodeName: string;
  host: string;
  sshLabel: string;
  phase: InstallPhase;
  lines: string[];
  statusMessage: string | null;
  reinstall: boolean;
  installDeps: boolean;
};

type Options = {
  onNodeUpdated: (node: NodeItem) => void;
  onIdle?: () => void;
};

export function useAgentInstallQueue({ onNodeUpdated, onIdle }: Options) {
  const [jobs, setJobs] = useState<Record<string, InstallJob>>({});
  const abortsRef = useRef(new Map<string, AbortController>());
  const runningRef = useRef(new Set<string>());
  const queueRef = useRef<string[]>([]);
  const jobsRef = useRef(jobs);
  jobsRef.current = jobs;
  const onNodeUpdatedRef = useRef(onNodeUpdated);
  onNodeUpdatedRef.current = onNodeUpdated;
  const onIdleRef = useRef(onIdle);
  onIdleRef.current = onIdle;

  const patchJob = useCallback((nodeId: string, patch: Partial<InstallJob>) => {
    setJobs((prev) => {
      const cur = prev[nodeId];
      if (!cur) return prev;
      return { ...prev, [nodeId]: { ...cur, ...patch } };
    });
  }, []);

  const appendLines = useCallback((nodeId: string, lines: string[]) => {
    setJobs((prev) => {
      const cur = prev[nodeId];
      if (!cur) return prev;
      return { ...prev, [nodeId]: { ...cur, lines: [...cur.lines, ...lines] } };
    });
  }, []);

  const pumpRef = useRef<() => void>(() => undefined);

  pumpRef.current = () => {
    while (runningRef.current.size < getQueueConcurrency() && queueRef.current.length > 0) {
      const nodeId = queueRef.current.shift()!;
      const job = jobsRef.current[nodeId];
      if (!job || job.phase !== "queued") continue;
      void startJob(nodeId);
    }
    if (runningRef.current.size === 0 && queueRef.current.length === 0) {
      onIdleRef.current?.();
    }
  };

  async function startJob(nodeId: string) {
    const job = jobsRef.current[nodeId];
    if (!job) return;

    const ac = new AbortController();
    abortsRef.current.set(nodeId, ac);
    runningRef.current.add(nodeId);
    patchJob(nodeId, { phase: "running", lines: [], statusMessage: null });

    try {
      await api.installAgentStream(nodeId, {
        signal: ac.signal,
        installDeps: job.installDeps,
        onEvent: (ev: AgentInstallStreamEvent) => {
          if (ac.signal.aborted) return;
          if (ev.type === "log") {
            appendLines(nodeId, [ev.line]);
          } else if (ev.type === "done") {
            appendLines(nodeId, ["", `✓ ${ev.message}`]);
            patchJob(nodeId, { phase: "done", statusMessage: ev.message });
            onNodeUpdatedRef.current(ev.node);
          } else if (ev.type === "error") {
            appendLines(nodeId, ["", `✗ ${ev.message}`]);
            patchJob(nodeId, { phase: "error", statusMessage: ev.message });
          }
        },
      });
    } catch (err) {
      const aborted =
        ac.signal.aborted ||
        (err instanceof DOMException && err.name === "AbortError") ||
        (err instanceof Error && err.name === "AbortError");
      if (aborted) {
        setJobs((prev) => {
          const cur = prev[nodeId];
          if (!cur) return prev;
          const last = cur.lines[cur.lines.length - 1] ?? "";
          const lines = last.includes("отменена")
            ? cur.lines
            : [...cur.lines, "", "✗ Установка отменена"];
          return {
            ...prev,
            [nodeId]: {
              ...cur,
              lines,
              phase: "error",
              statusMessage: "Установка отменена",
            },
          };
        });
      } else {
        const msg = err instanceof Error ? err.message : "Не удалось установить агент";
        appendLines(nodeId, ["", `✗ ${msg}`]);
        patchJob(nodeId, { phase: "error", statusMessage: msg });
      }
    } finally {
      abortsRef.current.delete(nodeId);
      runningRef.current.delete(nodeId);
      pumpRef.current();
    }
  }

  const enqueue = useCallback((nodes: NodeItem[], installDeps: boolean) => {
    if (nodes.length === 0) return;

    setJobs((prev) => {
      const next = { ...prev };
      for (const node of nodes) {
        const existing = next[node.id];
        if (existing && (existing.phase === "queued" || existing.phase === "running")) {
          continue;
        }
        next[node.id] = {
          nodeId: node.id,
          nodeName: node.name,
          host: node.host,
          sshLabel: `${node.ssh_user}@${node.host}:${node.ssh_port}`,
          phase: "queued",
          lines: [],
          statusMessage: null,
          reinstall: node.agent_configured,
          installDeps,
        };
        if (!queueRef.current.includes(node.id)) {
          queueRef.current.push(node.id);
        }
      }
      // Keep ref in sync before pump (microtask may run before re-render).
      jobsRef.current = next;
      return next;
    });

    queueMicrotask(() => pumpRef.current());
  }, []);

  const cancel = useCallback(
    (nodeId: string) => {
      const job = jobsRef.current[nodeId];
      if (!job) return;
      if (job.phase === "queued") {
        queueRef.current = queueRef.current.filter((id) => id !== nodeId);
        patchJob(nodeId, {
          phase: "error",
          statusMessage: "Установка отменена",
          lines: ["✗ Установка отменена"],
        });
        return;
      }
      abortsRef.current.get(nodeId)?.abort();
    },
    [patchJob],
  );

  const retry = useCallback(
    (node: NodeItem) => {
      const prev = jobsRef.current[node.id];
      enqueue([node], prev?.installDeps ?? true);
    },
    [enqueue],
  );

  const dismiss = useCallback((nodeId: string) => {
    const job = jobsRef.current[nodeId];
    if (!job) return;
    if (job.phase === "queued" || job.phase === "running") return;
    setJobs((prev) => {
      const next = { ...prev };
      delete next[nodeId];
      return next;
    });
  }, []);

  const dismissFinished = useCallback(() => {
    setJobs((prev) => {
      const next: Record<string, InstallJob> = {};
      for (const [id, job] of Object.entries(prev)) {
        if (job.phase === "queued" || job.phase === "running") {
          next[id] = job;
        }
      }
      return next;
    });
  }, []);

  const jobList = Object.values(jobs).sort((a, b) => a.nodeName.localeCompare(b.nodeName));
  const activeCount = jobList.filter((j) => j.phase === "queued" || j.phase === "running").length;
  const doneCount = jobList.filter((j) => j.phase === "done").length;
  const errorCount = jobList.filter((j) => j.phase === "error").length;

  return {
    jobs,
    jobList,
    enqueue,
    cancel,
    retry,
    dismiss,
    dismissFinished,
    activeCount,
    doneCount,
    errorCount,
  };
}
