import { useCallback, useEffect, useState } from "react";
import type { ScriptQueueBody } from "../api/client";
import { nodeJobWorker, type ScriptJob, type ScriptPhase } from "../lib/nodeJobWorker";
import type { NodeItem } from "../types";

export type { ScriptJob, ScriptPhase };

type Options = {
  onIdle?: () => void;
};

export function useScriptQueue(opts?: Options) {
  const [, bump] = useState(0);

  useEffect(() => {
    return nodeJobWorker.subscribe(() => bump((n) => n + 1));
  }, []);

  useEffect(() => {
    nodeJobWorker.setScriptOnIdle(opts?.onIdle);
  }, [opts?.onIdle]);

  const enqueue = useCallback(
    (nodes: NodeItem[], body: ScriptQueueBody) => nodeJobWorker.enqueueScript(nodes, body),
    [],
  );
  const cancel = useCallback((jobKey: string) => nodeJobWorker.cancel("script", jobKey), []);
  const dismiss = useCallback((jobKey: string) => nodeJobWorker.dismissScript(jobKey), []);
  const dismissFinished = useCallback(() => nodeJobWorker.dismissFinished(), []);

  const jobs = nodeJobWorker.getScriptJobs();
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
