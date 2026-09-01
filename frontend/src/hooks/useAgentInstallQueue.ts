import { useCallback, useEffect, useState } from "react";
import { nodeJobWorker, type InstallJob, type InstallPhase } from "../lib/nodeJobWorker";
import type { NodeItem } from "../types";

export type { InstallJob, InstallPhase };

type Options = {
  onNodeUpdated: (node: NodeItem) => void;
  onIdle?: () => void;
};

export function useAgentInstallQueue({ onNodeUpdated, onIdle }: Options) {
  const [, bump] = useState(0);

  useEffect(() => {
    return nodeJobWorker.subscribe(() => bump((n) => n + 1));
  }, []);

  useEffect(() => {
    nodeJobWorker.setAgentHandlers({ onNodeUpdated, onIdle });
  }, [onNodeUpdated, onIdle]);

  const enqueue = useCallback((nodes: NodeItem[], installDeps: boolean) => {
    nodeJobWorker.enqueueAgent(nodes, installDeps);
  }, []);
  const cancel = useCallback((nodeId: string) => nodeJobWorker.cancel("agent", nodeId), []);
  const retry = useCallback((node: NodeItem) => nodeJobWorker.retryAgent(node), []);
  const dismiss = useCallback((nodeId: string) => nodeJobWorker.dismissAgent(nodeId), []);
  const dismissFinished = useCallback(() => nodeJobWorker.dismissFinished(), []);

  const jobs = nodeJobWorker.getAgentJobs();
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
