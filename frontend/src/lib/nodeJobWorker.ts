/**
 * Process-wide SSH job runner. Lives outside React so leaving a page does not
 * abort installs / RemnaNode scripts / WARP / cf_204.
 */
import { api, type ScriptQueueBody, type ScriptStreamEvent } from "../api/client";
import type { NodeItem } from "../types";
import { getQueueConcurrency } from "./concurrency";

export type JobPhase = "queued" | "running" | "done" | "error";

export type InstallJob = {
  nodeId: string;
  nodeName: string;
  host: string;
  sshLabel: string;
  phase: JobPhase;
  lines: string[];
  statusMessage: string | null;
  reinstall: boolean;
  installDeps: boolean;
};

export type ScriptJob = {
  jobKey: string;
  nodeId: string;
  nodeName: string;
  host: string;
  sshLabel: string;
  action: ScriptQueueBody["action"];
  phase: JobPhase;
  lines: string[];
  statusMessage: string | null;
  body: ScriptQueueBody;
};

export type InstallPhase = JobPhase;
export type ScriptPhase = JobPhase;

type QueueItem =
  | { kind: "agent"; id: string }
  | { kind: "script"; id: string };

type Listener = () => void;

type AgentHandlers = {
  onNodeUpdated?: (node: NodeItem) => void;
  onIdle?: () => void;
};

class NodeJobWorker {
  private listeners = new Set<Listener>();
  private emitScheduled = false;
  private agentJobs: Record<string, InstallJob> = {};
  private scriptJobs: Record<string, ScriptJob> = {};
  private queue: QueueItem[] = [];
  private running = new Set<string>();
  private aborts = new Map<string, AbortController>();
  private agentHandlers: AgentHandlers = {};
  private scriptOnIdle: (() => void) | undefined;

  subscribe(fn: Listener): () => void {
    this.listeners.add(fn);
    return () => this.listeners.delete(fn);
  }

  setAgentHandlers(h: AgentHandlers) {
    this.agentHandlers = h;
  }

  setScriptOnIdle(fn: (() => void) | undefined) {
    this.scriptOnIdle = fn;
  }

  getAgentJobs(): Record<string, InstallJob> {
    return this.agentJobs;
  }

  getScriptJobs(): Record<string, ScriptJob> {
    return this.scriptJobs;
  }

  enqueueAgent(nodes: NodeItem[], installDeps: boolean) {
    if (nodes.length === 0) return;
    const next = { ...this.agentJobs };
    for (const node of nodes) {
      const existing = next[node.id];
      if (existing && (existing.phase === "queued" || existing.phase === "running")) continue;
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
      if (!this.queue.some((q) => q.kind === "agent" && q.id === node.id)) {
        this.queue.push({ kind: "agent", id: node.id });
      }
    }
    this.agentJobs = next;
    this.emit();
    this.pump();
  }

  enqueueScript(nodes: NodeItem[], body: ScriptQueueBody): string[] {
    if (nodes.length === 0) return [];
    const keys: string[] = [];
    const next = { ...this.scriptJobs };
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
      this.queue.push({ kind: "script", id: jobKey });
    }
    this.scriptJobs = next;
    this.emit();
    this.pump();
    return keys;
  }

  retryAgent(node: NodeItem) {
    const prev = this.agentJobs[node.id];
    this.enqueueAgent([node], prev?.installDeps ?? true);
  }

  cancel(kind: "agent" | "script", id: string) {
    if (kind === "agent") {
      const job = this.agentJobs[id];
      if (!job) return;
      if (job.phase === "queued") {
        this.queue = this.queue.filter((q) => !(q.kind === "agent" && q.id === id));
        this.patchAgent(id, {
          phase: "error",
          statusMessage: "Установка отменена",
          lines: ["✗ Установка отменена"],
        });
        return;
      }
      this.aborts.get(`agent:${id}`)?.abort();
      return;
    }
    const job = this.scriptJobs[id];
    if (!job) return;
    if (job.phase === "queued") {
      this.queue = this.queue.filter((q) => !(q.kind === "script" && q.id === id));
      this.patchScript(id, {
        phase: "error",
        statusMessage: "Выполнение отменено",
        lines: ["✗ Выполнение отменено"],
      });
      return;
    }
    this.aborts.get(`script:${id}`)?.abort();
  }

  dismissAgent(nodeId: string) {
    const job = this.agentJobs[nodeId];
    if (!job || job.phase === "queued" || job.phase === "running") return;
    const next = { ...this.agentJobs };
    delete next[nodeId];
    this.agentJobs = next;
    this.emit();
  }

  dismissScript(jobKey: string) {
    const job = this.scriptJobs[jobKey];
    if (!job || job.phase === "queued" || job.phase === "running") return;
    const next = { ...this.scriptJobs };
    delete next[jobKey];
    this.scriptJobs = next;
    this.emit();
  }

  dismissFinished() {
    const agents: Record<string, InstallJob> = {};
    for (const [id, job] of Object.entries(this.agentJobs)) {
      if (job.phase === "queued" || job.phase === "running") agents[id] = job;
    }
    const scripts: Record<string, ScriptJob> = {};
    for (const [id, job] of Object.entries(this.scriptJobs)) {
      if (job.phase === "queued" || job.phase === "running") scripts[id] = job;
    }
    this.agentJobs = agents;
    this.scriptJobs = scripts;
    this.emit();
  }

  private emit() {
    if (this.emitScheduled) return;
    this.emitScheduled = true;
    queueMicrotask(() => {
      this.emitScheduled = false;
      for (const fn of this.listeners) fn();
    });
  }

  private patchAgent(nodeId: string, patch: Partial<InstallJob>) {
    const cur = this.agentJobs[nodeId];
    if (!cur) return;
    this.agentJobs = { ...this.agentJobs, [nodeId]: { ...cur, ...patch } };
    this.emit();
  }

  private patchScript(jobKey: string, patch: Partial<ScriptJob>) {
    const cur = this.scriptJobs[jobKey];
    if (!cur) return;
    this.scriptJobs = { ...this.scriptJobs, [jobKey]: { ...cur, ...patch } };
    this.emit();
  }

  private appendAgent(nodeId: string, lines: string[]) {
    const cur = this.agentJobs[nodeId];
    if (!cur) return;
    this.agentJobs = { ...this.agentJobs, [nodeId]: { ...cur, lines: [...cur.lines, ...lines] } };
    this.emit();
  }

  private appendScript(jobKey: string, lines: string[]) {
    const cur = this.scriptJobs[jobKey];
    if (!cur) return;
    this.scriptJobs = { ...this.scriptJobs, [jobKey]: { ...cur, lines: [...cur.lines, ...lines] } };
    this.emit();
  }

  private runKey(item: QueueItem) {
    return `${item.kind}:${item.id}`;
  }

  private pump() {
    while (this.running.size < getQueueConcurrency() && this.queue.length > 0) {
      const item = this.queue.shift()!;
      if (item.kind === "agent") {
        const job = this.agentJobs[item.id];
        if (!job || job.phase !== "queued") continue;
        void this.startAgent(item.id);
      } else {
        const job = this.scriptJobs[item.id];
        if (!job || job.phase !== "queued") continue;
        void this.startScript(item.id);
      }
    }
    if (this.running.size === 0 && this.queue.length === 0) {
      (this.agentHandlers.onIdle ?? this.scriptOnIdle)?.();
    }
  }

  private finishRun(item: QueueItem) {
    this.aborts.delete(this.runKey(item));
    this.running.delete(this.runKey(item));
    this.pump();
  }

  private isAbort(err: unknown, ac: AbortController) {
    return (
      ac.signal.aborted ||
      (err instanceof DOMException && err.name === "AbortError") ||
      (err instanceof Error && err.name === "AbortError")
    );
  }

  private async startAgent(nodeId: string) {
    const job = this.agentJobs[nodeId];
    if (!job) return;
    const item: QueueItem = { kind: "agent", id: nodeId };
    const ac = new AbortController();
    this.aborts.set(this.runKey(item), ac);
    this.running.add(this.runKey(item));
    this.patchAgent(nodeId, { phase: "running", lines: [], statusMessage: null });

    try {
      await api.installAgentStream(nodeId, {
        signal: ac.signal,
        installDeps: job.installDeps,
        onEvent: (ev) => {
          if (ac.signal.aborted) return;
          if (ev.type === "log") this.appendAgent(nodeId, [ev.line]);
          else if (ev.type === "done") {
            this.appendAgent(nodeId, ["", `✓ ${ev.message}`]);
            this.patchAgent(nodeId, { phase: "done", statusMessage: ev.message });
            this.agentHandlers.onNodeUpdated?.(ev.node);
          } else if (ev.type === "error") {
            this.appendAgent(nodeId, ["", `✗ ${ev.message}`]);
            this.patchAgent(nodeId, { phase: "error", statusMessage: ev.message });
          }
        },
      });
    } catch (err) {
      if (this.isAbort(err, ac)) {
        const cur = this.agentJobs[nodeId];
        if (cur) {
          const last = cur.lines[cur.lines.length - 1] ?? "";
          const lines = last.includes("отменена") ? cur.lines : [...cur.lines, "", "✗ Установка отменена"];
          this.patchAgent(nodeId, { lines, phase: "error", statusMessage: "Установка отменена" });
        }
      } else {
        const msg = err instanceof Error ? err.message : "Не удалось установить агент";
        this.appendAgent(nodeId, ["", `✗ ${msg}`]);
        this.patchAgent(nodeId, { phase: "error", statusMessage: msg });
      }
    } finally {
      this.finishRun(item);
    }
  }

  private async startScript(jobKey: string) {
    const job = this.scriptJobs[jobKey];
    if (!job) return;
    const item: QueueItem = { kind: "script", id: jobKey };
    const ac = new AbortController();
    this.aborts.set(this.runKey(item), ac);
    this.running.add(this.runKey(item));
    this.patchScript(jobKey, { phase: "running", lines: [], statusMessage: null });

    const onEvent = (ev: ScriptStreamEvent) => {
      if (ac.signal.aborted) return;
      if (ev.type === "log") this.appendScript(jobKey, [ev.line]);
      else if (ev.type === "done") {
        this.appendScript(jobKey, ["", `✓ ${ev.message}`]);
        this.patchScript(jobKey, { phase: "done", statusMessage: ev.message });
      } else if (ev.type === "error") {
        this.appendScript(jobKey, ["", `✗ ${ev.message}`]);
        this.patchScript(jobKey, { phase: "error", statusMessage: ev.message });
      }
    };

    try {
      if (job.body.action === "warp") {
        await api.installWarpStream(job.nodeId, {
          signal: ac.signal,
          force: job.body.force,
          onEvent,
        });
      } else if (job.body.action === "cf204") {
        await api.installCf204Stream(job.nodeId, {
          signal: ac.signal,
          patch_profile: job.body.patch_profile,
          onEvent,
        });
      } else {
        await api.runScriptStream(job.nodeId, job.body, { signal: ac.signal, onEvent });
      }
    } catch (err) {
      if (this.isAbort(err, ac)) {
        this.appendScript(jobKey, ["", "✗ Выполнение отменено"]);
        this.patchScript(jobKey, { phase: "error", statusMessage: "Выполнение отменено" });
      } else {
        const msg = err instanceof Error ? err.message : "Ошибка скрипта";
        this.appendScript(jobKey, ["", `✗ ${msg}`]);
        this.patchScript(jobKey, { phase: "error", statusMessage: msg });
      }
    } finally {
      this.finishRun(item);
    }
  }
}

export const nodeJobWorker = new NodeJobWorker();
