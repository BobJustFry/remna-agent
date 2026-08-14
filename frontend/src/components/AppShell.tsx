import { useCallback, useEffect, useState, type Dispatch, type SetStateAction } from "react";
import { Outlet } from "react-router-dom";
import { api, type RemnaScriptAction, type RemnaScriptRunBody, type RemnawaveVersions } from "../api/client";
import { useNodesAgents } from "../hooks/useNodesAgents";
import { useNodesOnline } from "../hooks/useNodesOnline";
import { useRemnawaveVersions } from "../hooks/useRemnawaveVersions";
import { useScriptQueue } from "../hooks/useScriptQueue";
import type { AgentMap, HostingItem, NodeItem, OnlineMap } from "../types";
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
  refreshAgents: () => Promise<void>;
  openScriptRun: (nodes: NodeItem[], action: RemnaScriptAction) => void;
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
  const [viewScriptJobKey, setViewScriptJobKey] = useState<string | null>(null);
  const { statuses } = useNodesOnline(true);
  const {
    statuses: agentStatuses,
    latestAgentVersion,
    refresh: refreshAgents,
  } = useNodesAgents(true);
  const {
    versions: remnawaveVersions,
    loading: remnawaveLoading,
    refresh: refreshRemnawaveVersions,
  } = useRemnawaveVersions(true);
  const scriptQueue = useScriptQueue();

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

  const ctx: AppOutletContext = {
    nodes,
    setNodes,
    hostings,
    reloadHostings,
    reloadNodes,
    statuses,
    agentStatuses,
    latestAgentVersion,
    refreshAgents,
    openScriptRun,
    remnawaveVersions,
    remnawaveLoading,
    refreshRemnawaveVersions,
  };

  const viewJob = viewScriptJobKey ? scriptQueue.jobs[viewScriptJobKey] : undefined;

  const trayJobs = scriptQueue.jobList.map((j) => ({
    nodeId: j.jobKey,
    nodeName: `${j.nodeName} · ${j.action}`,
    host: j.host,
    sshLabel: j.sshLabel,
    phase: j.phase,
    lines: j.lines,
    statusMessage: j.statusMessage,
    reinstall: j.action === "reinstall",
    installDeps: false,
  }));

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
            title="Скрипты RemnaNode"
            activeCount={scriptQueue.activeCount}
            doneCount={scriptQueue.doneCount}
            errorCount={scriptQueue.errorCount}
            onOpen={(jobKey) => setViewScriptJobKey(jobKey)}
            onCancel={scriptQueue.cancel}
            onDismissFinished={scriptQueue.dismissFinished}
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

      {viewJob && (
        <ScriptJobLogDialog
          job={viewJob}
          onClose={() => setViewScriptJobKey(null)}
          onCancel={() => scriptQueue.cancel(viewJob.jobKey)}
          onDismiss={() => scriptQueue.dismiss(viewJob.jobKey)}
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
