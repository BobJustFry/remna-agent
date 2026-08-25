import { useCallback, useRef, useState } from "react";
import { api, type ScriptQueueBody, type ScriptStreamEvent } from "../api/client";
import { getQueueConcurrency } from "../lib/concurrency";
import type { NodeItem } from "../types";

export type ScriptPhase = "queued" | "running" | "done" | "error";

export type ScriptJob = {
  jobKey: string;
  nodeId: string;
  nodeName: string;
  host: string;
  sshLabel: string;
  action: ScriptQueueBody["action"];
  phase: ScriptPhase;
  lines: string[];
  statusMessage: string | null;
  body: ScriptQueueBody;
};

type Options = {
  onIdle?: () => void;
};

export function useScriptQueue(opts?: Options) {
  const [jobs, setJobs] = useState<Record<string, ScriptJob>>({});
  const abortsRef = useRef(new Map<string, AbortController>());
  const runningRef = useRef(new Set<string>());
  const queueRef = useRef<string[]>([]);
  const jobsRef = useRef(jobs);
  jobsRef.current = jobs;
  const onIdleRef = useRef(opts?.onIdle);
  onIdleRef.current = opts?.onIdle;

  const patchJob = useCallback((jobKey: string, patch: Partial<ScriptJob>) => {
    setJobs((prev) => {
      const cur = prev[jobKey];
      if (!cur) return prev;
      return { ...prev, [jobKey]: { ...cur, ...patch } };
    });
  }, []);

  const appendLines = useCallback((jobKey: string, lines: string[]) => {
    setJobs((prev) => {
      const cur = prev[jobKey];
      if (!cur) return prev;
      return { ...prev, [jobKey]: { ...cur, lines: [...cur.lines, ...lines] } };
    });
  }, []);

  const pumpRef = useRef<() => void>(() => undefined);

  pumpRef.current = () => {
    while (runningRef.current.size < getQueueConcurrency() && queueRef.current.length > 0) {
      const jobKey = queueRef.current.shift()!;
      const job = jobsRef.current[jobKey];
      if (!job || job.phase !== "queued") continue;
      void startJob(jobKey);
    }
    if (runningRef.current.size === 0 && queueRef.current.length === 0) {
      onIdleRef.current?.();
    }
  };

  async function startJob(jobKey: string) {
    const job = jobsRef.current[jobKey];
    if (!job) return;
    const ac = new AbortController();
    abortsRef.current.set(jobKey, ac);
    runningRef.current.add(jobKey);
    patchJob(jobKey, { phase: "running", lines: [], statusMessage: null });

    try {
      const onEvent = (ev: ScriptStreamEvent) => {
        if (ac.signal.aborted) return;
        if (ev.type === "log") appendLines(jobKey, [ev.line]);
        else if (ev.type === "done") {
          appendLines(jobKey, ["", `✓ ${ev.message}`]);
          patchJob(jobKey, { phase: "done", statusMessage: ev.message });
        } else if (ev.type === "error") {
          appendLines(jobKey, ["", `✗ ${ev.message}`]);
          patchJob(jobKey, { phase: "error", statusMessage: ev.message });
        }
      };
      if (job.body.action === "warp") {
        await api.installWarpStream(job.nodeId, {
          signal: ac.signal,
          force: job.body.force,
          onEvent,
        });
      } else {
        await api.runScriptStream(job.nodeId, job.body, {
          signal: ac.signal,
          onEvent,
        });
      }
    } catch (err) {
      const aborted =
        ac.signal.aborted ||
        (err instanceof DOMException && err.name === "AbortError") ||
        (err instanceof Error && err.name === "AbortError");
      if (aborted) {
        appendLines(jobKey, ["", "✗ Выполнение отменено"]);
        patchJob(jobKey, { phase: "error", statusMessage: "Выполнение отменено" });
      } else {
        const msg = err instanceof Error ? err.message : "Ошибка скрипта";
        appendLines(jobKey, ["", `✗ ${msg}`]);
        patchJob(jobKey, { phase: "error", statusMessage: msg });
      }
    } finally {
      abortsRef.current.delete(jobKey);
      runningRef.current.delete(jobKey);
      pumpRef.current();
    }
  }

  const enqueue = useCallback((nodes: NodeItem[], body: ScriptQueueBody): string[] => {
    if (nodes.length === 0) return [];
    const keys: string[] = [];
    setJobs((prev) => {
      const next = { ...prev };
      for (const node of nodes) {
        const jobKey = `${node.id}:${body.action}:${Date.now()}:${Math.random().toString(36).slice(2, 7)}`;
        keys.push(jobKey);
        next[jobKey] = {
          jobKey,
          nodeId: node.id,
          nodeName: node.name,
          host: node.host,
          sshLabel: `${node.ssh_user}@${node.host}:${node.ssh_port}`,
          action: body.action,
          phase: "queued",
          lines: [],
          statusMessage: null,
          body,
        };
        queueRef.current.push(jobKey);
      }
      jobsRef.current = next;
      return next;
    });
    queueMicrotask(() => pumpRef.current());
    return keys;
  }, []);

  const cancel = useCallback(
    (jobKey: string) => {
      const job = jobsRef.current[jobKey];
      if (!job) return;
      if (job.phase === "queued") {
        queueRef.current = queueRef.current.filter((k) => k !== jobKey);
        patchJob(jobKey, {
          phase: "error",
          statusMessage: "Выполнение отменено",
          lines: ["✗ Выполнение отменено"],
        });
        return;
      }
      abortsRef.current.get(jobKey)?.abort();
    },
    [patchJob],
  );

  const dismiss = useCallback((jobKey: string) => {
    const job = jobsRef.current[jobKey];
    if (!job || job.phase === "queued" || job.phase === "running") return;
    setJobs((prev) => {
      const next = { ...prev };
      delete next[jobKey];
      return next;
    });
  }, []);

  const dismissFinished = useCallback(() => {
    setJobs((prev) => {
      const next: Record<string, ScriptJob> = {};
      for (const [k, job] of Object.entries(prev)) {
        if (job.phase === "queued" || job.phase === "running") next[k] = job;
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
    dismiss,
    dismissFinished,
    activeCount,
    doneCount,
    errorCount,
  };
}
