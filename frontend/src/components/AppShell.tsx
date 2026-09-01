import { useCallback, useEffect, useState, type Dispatch, type SetStateAction } from "react";
import { Outlet } from "react-router-dom";
import { api, type RemnaScriptAction, type RemnaScriptRunBody, type RemnawaveVersions } from "../api/client";
import { useNodesAgents } from "../hooks/useNodesAgents";
import { useNodesOnline } from "../hooks/useNodesOnline";
import { useRemnawaveVersions } from "../hooks/useRemnawaveVersions";
import { useAgentInstallQueue, type InstallJob } from "../hooks/useAgentInstallQueue";
import { useScriptQueue } from "../hooks/useScriptQueue";
import type { AgentMap, HostingItem, NodeItem, OnlineMap } from "../types";
import { AgentInstallLogDialog } from "./AgentInstallLogDialog";
import { AgentInstallTray } from "./AgentInstallTray";
import { ConfirmDialog } from "./ConfirmDialog";
import { RemnaScriptDialog } from "./RemnaScriptDialog";
import { ScriptJobLogDialog } from "./ScriptJobLogDialog";
import { Sidebar } from "./Sidebar";
import { StatusBar } from "./StatusBar";

type Props = {
  username: string;
  onLogout: () => void;
};

export type AppOutletContext = {
  nodes: NodeItem[];
  setNodes: Dispatch<SetStateAction<NodeItem[]>>;
  hostings: HostingItem[];
  reloadHostings: () => Promise<void>;
  reloadNodes: () => Promise<void>;
  statuses: OnlineMap;
  agentStatuses: AgentMap;
  latestAgentVersion: string | null;
  latestWgcfVersion: string | null;
  refreshAgents: () => Promise<void>;
  openScriptRun: (nodes: NodeItem[], action: RemnaScriptAction) => void;
  openWarpInstall: (nodes: NodeItem[], force?: boolean) => void;
  openCf204Install: (nodes: NodeItem[]) => void;
  installJobs: Record<string, InstallJob>;
  enqueueAgentInstall: (nodes: NodeItem[], installDeps: boolean) => void;
  openAgentInstallLog: (nodeId: string) => void;
  /** In-flight RemnaNode/WARP/cf204 jobs (queued or running, including confirm dialog). */
  scriptBusy: Record<string, { warp: boolean; remna: boolean; cf204: boolean }>;
  remnawaveVersions: RemnawaveVersions | null;
  remnawaveLoading: boolean;
  refreshRemnawaveVersions: (force?: boolean) => Promise<RemnawaveVersions | void>;
};

export function AppShell({ username, onLogout }: Props) {
  const [nodes, setNodes] = useState<NodeItem[]>([]);
  const [hostings, setHostings] = useState<HostingItem[]>([]);
  const [navOpen, setNavOpen] = useState(false);
  const [scriptTargets, setScriptTargets] = useState<{
    nodes: NodeItem[];
    action: RemnaScriptAction;
  } | null>(null);
  const [updateTargets, setUpdateTargets] = useState<NodeItem[] | null>(null);
  const [warpTargets, setWarpTargets] = useState<{ nodes: NodeItem[]; force: boolean } | null>(
    null,
  );
  const [viewScriptJobKey, setViewScriptJobKey] = useState<string | null>(null);
  const [viewAgentJobId, setViewAgentJobId] = useState<string | null>(null);
  const { statuses } = useNodesOnline(true);
  const {
    statuses: agentStatuses,
    latestAgentVersion,
    latestWgcfVersion,
    refresh: refreshAgents,
  } = useNodesAgents(true);
  const {
    versions: remnawaveVersions,
    loading: remnawaveLoading,
    refresh: refreshRemnawaveVersions,
  } = useRemnawaveVersions(true);
  const scriptQueue = useScriptQueue({
    onIdle: () => {
      void refreshAgents();
    },
  });

  const onNodeUpdated = useCallback((updated: NodeItem) => {
    setNodes((prev) => prev.map((n) => (n.id === updated.id ? updated : n)));
  }, []);

  const agentQueue = useAgentInstallQueue({
    onNodeUpdated,
    onIdle: () => {
      void refreshAgents();
    },
  });

  const reloadNodes = useCallback(async () => {
    const data = await api.listNodes();
    setNodes(data);
  }, []);

  const reloadHostings = useCallback(async () => {
    const data = await api.listHostings();
    setHostings(data);
  }, []);

  useEffect(() => {
    void reloadNodes().catch(() => undefined);
    void reloadHostings().catch(() => undefined);
  }, [reloadNodes, reloadHostings]);

  const openScriptRun = useCallback((targetNodes: NodeItem[], action: RemnaScriptAction) => {
    if (action === "update") {
      setUpdateTargets(targetNodes);
      return;
    }
    setScriptTargets({ nodes: targetNodes, action });
  }, []);

  const openWarpInstall = useCallback((targetNodes: NodeItem[], force = false) => {
    if (targetNodes.length === 0) return;
    setWarpTargets({ nodes: targetNodes, force });
  }, []);

  const openCf204Install = useCallback((targetNodes: NodeItem[]) => {
    if (targetNodes.length === 0) return;
    scriptQueue.enqueue(targetNodes, { action: "cf204", patch_profile: true });
  }, [scriptQueue.enqueue]);

  const openAgentInstallLog = useCallback((nodeId: string) => {
    setViewAgentJobId(nodeId);
  }, []);

  const scriptBusy: Record<string, { warp: boolean; remna: boolean; cf204: boolean }> = {};
  const markBusy = (nodeId: string) => {
    if (!scriptBusy[nodeId]) scriptBusy[nodeId] = { warp: false, remna: false, cf204: false };
    return scriptBusy[nodeId];
  };
  for (const j of scriptQueue.jobList) {
    if (j.phase !== "queued" && j.phase !== "running") continue;
    if (j.action === "warp") markBusy(j.nodeId).warp = true;
    else if (j.action === "cf204") markBusy(j.nodeId).cf204 = true;
    else markBusy(j.nodeId).remna = true;
  }
  for (const n of warpTargets?.nodes ?? []) markBusy(n.id).warp = true;
  for (const n of scriptTargets?.nodes ?? []) markBusy(n.id).remna = true;
  for (const n of updateTargets ?? []) markBusy(n.id).remna = true;

  const ctx: AppOutletContext = {
    nodes,
    setNodes,
    hostings,
    reloadHostings,
    reloadNodes,
    statuses,
    agentStatuses,
    latestAgentVersion,
    latestWgcfVersion,
    refreshAgents,
    openScriptRun,
    openWarpInstall,
    openCf204Install,
    installJobs: agentQueue.jobs,
    enqueueAgentInstall: agentQueue.enqueue,
    openAgentInstallLog,
    scriptBusy,
    remnawaveVersions,
    remnawaveLoading,
    refreshRemnawaveVersions,
  };

  const viewJob = viewScriptJobKey ? scriptQueue.jobs[viewScriptJobKey] : undefined;
  const viewAgentJob = viewAgentJobId ? agentQueue.jobs[viewAgentJobId] : undefined;
  const viewAgentNode = viewAgentJob
    ? nodes.find((n) => n.id === viewAgentJob.nodeId)
    : undefined;

  const trayJobs = [
    ...agentQueue.jobList.map((j) => ({
      nodeId: `agent:${j.nodeId}`,
      nodeName: `${j.nodeName} · агент`,
      host: j.host,
      sshLabel: j.sshLabel,
      phase: j.phase,
      lines: j.lines,
      statusMessage: j.statusMessage,
      reinstall: j.reinstall,
      installDeps: j.installDeps,
    })),
    ...scriptQueue.jobList.map((j) => ({
      nodeId: `script:${j.jobKey}`,
      nodeName: `${j.nodeName} · ${j.action === "warp" ? "WARP" : j.action === "cf204" ? "cf_204" : j.action}`,
      host: j.host,
      sshLabel: j.sshLabel,
      phase: j.phase,
      lines: j.lines,
      statusMessage: j.statusMessage,
      reinstall: j.action === "reinstall" || (j.action === "warp" && "force" in j.body && !!j.body.force),
      installDeps: false,
    })),
  ];
  const trayActive = agentQueue.activeCount + scriptQueue.activeCount;
  const trayDone = agentQueue.doneCount + scriptQueue.doneCount;
  const trayError = agentQueue.errorCount + scriptQueue.errorCount;
  const trayHasAgent = agentQueue.jobList.length > 0;
  const trayHasWarp = scriptQueue.jobList.some((j) => j.action === "warp");
  const trayHasCf204 = scriptQueue.jobList.some((j) => j.action === "cf204");
  const trayHasRemna = scriptQueue.jobList.some((j) => j.action !== "warp" && j.action !== "cf204");
  const trayTitle =
    trayHasAgent && !trayHasWarp && !trayHasCf204 && !trayHasRemna
      ? "Установка агентов"
      : trayHasWarp && !trayHasRemna && !trayHasCf204 && !trayHasAgent
        ? "WARP"
        : trayHasCf204 && !trayHasRemna && !trayHasWarp && !trayHasAgent
          ? "Заглушка cf_204"
          : "Задачи на нодах";

  const latestNode = remnawaveVersions?.node_version;

  return (
    <div className="flex h-dvh overflow-hidden">
      <Sidebar
        username={username}
        onLogout={onLogout}
        open={navOpen}
        onClose={() => setNavOpen(false)}
      />
      <div className="flex min-w-0 flex-1 flex-col">
        <div className="flex h-12 shrink-0 items-center gap-3 border-b border-[var(--border)] bg-[var(--sidebar)] px-3 lg:hidden">
          <button
            type="button"
            onClick={() => setNavOpen(true)}
            className="inline-flex h-9 w-9 items-center justify-center rounded-lg border border-[var(--border)] text-[var(--text)]"
            aria-label="Открыть меню"
          >
            <MenuIcon />
          </button>
          <div className="min-w-0">
            <div className="truncate text-sm font-semibold">Remna Agent</div>
          </div>
        </div>
        <div className="min-h-0 flex-1 overflow-auto">
          <Outlet context={ctx} />
        </div>
        {trayJobs.length > 0 && (
          <AgentInstallTray
            jobs={trayJobs}
            title={trayTitle}
            activeCount={trayActive}
            doneCount={trayDone}
            errorCount={trayError}
            onOpen={(id) => {
              if (id.startsWith("agent:")) setViewAgentJobId(id.slice("agent:".length));
              else if (id.startsWith("script:")) setViewScriptJobKey(id.slice("script:".length));
            }}
            onCancel={(id) => {
              if (id.startsWith("agent:")) agentQueue.cancel(id.slice("agent:".length));
              else if (id.startsWith("script:")) scriptQueue.cancel(id.slice("script:".length));
            }}
            onDismissFinished={() => {
              agentQueue.dismissFinished();
            }}
          />
        )}
        <StatusBar
          nodes={nodes}
          statuses={statuses}
          remnawaveVersions={remnawaveVersions}
          remnawaveLoading={remnawaveLoading}
          onRefreshVersions={() => void refreshRemnawaveVersions(true)}
        />
      </div>

      {scriptTargets && scriptTargets.action !== "update" && (
        <RemnaScriptDialog
          nodes={scriptTargets.nodes}
          action={scriptTargets.action}
          onClose={() => setScriptTargets(null)}
          onConfirm={(body: RemnaScriptRunBody) => {
            const targets = scriptTargets.nodes;
            const keys = scriptQueue.enqueue(targets, body);
            setScriptTargets(null);
            if (keys.length === 1) setViewScriptJobKey(keys[0]);
          }}
        />
      )}

      <ConfirmDialog
        open={!!updateTargets && updateTargets.length > 0}
        title={
          updateTargets && updateTargets.length > 1
            ? `Обновить RemnaNode на ${updateTargets.length} нодах?`
            : "Обновить RemnaNode?"
        }
        message={
          updateTargets && updateTargets.length === 1
            ? `${updateTargets[0].name} · ${updateTargets[0].host}${
                latestNode ? ` → v${latestNode}` : ""
              }. Команда: docker compose pull && down && up -d в /opt/remnanode.`
            : `Официальный апдейт RemnaNode${
                latestNode ? ` до v${latestNode}` : ""
              }: pull → down → up -d. Лог — как при установке.`
        }
        confirmLabel={
          updateTargets && updateTargets.length > 1
            ? `Обновить (${updateTargets.length})`
            : "Обновить"
        }
        danger={false}
        onCancel={() => setUpdateTargets(null)}
        onConfirm={() => {
          if (!updateTargets?.length) return;
          const targets = updateTargets;
          const keys = scriptQueue.enqueue(targets, { action: "update" });
          setUpdateTargets(null);
          if (keys.length === 1) setViewScriptJobKey(keys[0]);
        }}
      />

      <ConfirmDialog
        open={!!warpTargets && warpTargets.nodes.length > 0}
        title={
          warpTargets && warpTargets.force
            ? warpTargets.nodes.length > 1
              ? `Переустановить WARP на ${warpTargets.nodes.length} нодах?`
              : "Переустановить WARP?"
            : warpTargets && warpTargets.nodes.length > 1
              ? `Установить WARP на ${warpTargets.nodes.length} нодах?`
              : "Установить WARP?"
        }
        message={
          warpTargets && warpTargets.nodes.length === 1
            ? `${warpTargets.nodes[0].name} · ${warpTargets.nodes[0].host}. Интерфейс warp (wgcf), без default route — для Xray sockopt.interface.`
            : "На каждой ноде: wgcf register + wg-quick@warp. Маршрут по умолчанию не меняется."
        }
        confirmLabel={
          warpTargets && warpTargets.nodes.length > 1
            ? `${warpTargets.force ? "Переустановить" : "Установить"} (${warpTargets.nodes.length})`
            : warpTargets?.force
              ? "Переустановить"
              : "Установить"
        }
        danger={false}
        onCancel={() => setWarpTargets(null)}
        onConfirm={() => {
          if (!warpTargets?.nodes.length) return;
          const targets = warpTargets.nodes;
          const force = warpTargets.force;
          const keys = scriptQueue.enqueue(targets, { action: "warp", force });
          setWarpTargets(null);
          if (keys.length === 1) setViewScriptJobKey(keys[0]);
        }}
      />

      {viewJob && (
        <ScriptJobLogDialog
          job={viewJob}
          onClose={() => setViewScriptJobKey(null)}
          onCancel={() => scriptQueue.cancel(viewJob.jobKey)}
          onDismiss={() => scriptQueue.dismiss(viewJob.jobKey)}
        />
      )}
      {viewAgentJob && (
        <AgentInstallLogDialog
          job={viewAgentJob}
          onClose={() => setViewAgentJobId(null)}
          onCancel={() => agentQueue.cancel(viewAgentJob.nodeId)}
          onRetry={() => {
            if (viewAgentNode) agentQueue.retry(viewAgentNode);
          }}
          onDismiss={() => agentQueue.dismiss(viewAgentJob.nodeId)}
        />
      )}
    </div>
  );
}

function MenuIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
      <path d="M4 6h16M4 12h16M4 18h16" />
    </svg>
  );
}
