import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { useOutletContext } from "react-router-dom";
import { api, type RemnaScriptAction } from "../api/client";
import {
  AgentInstallConfirmDialog,
  type InstallIntent,
} from "../components/AgentInstallConfirmDialog";
import { AgentInstallLogDialog } from "../components/AgentInstallLogDialog";
import { AgentInstallTray } from "../components/AgentInstallTray";
import type { AppOutletContext } from "../components/AppShell";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { CapacityDialog } from "../components/CapacityDialog";
import { DestPickDialog } from "../components/DestPickDialog";
import { HaproxyDialog } from "../components/HaproxyDialog";
import { NodeForm } from "../components/NodeForm";
import { NodeRow, type NodeListDensity } from "../components/NodeRow";
import { useAgentInstallQueue } from "../hooks/useAgentInstallQueue";
import { remnanodeNeedsUpdate, warpNeedsInstall } from "../hooks/useRemnawaveVersions";
import type { NodeFormValues, NodeItem } from "../types";

type ConfirmInstallState = { nodes: NodeItem[]; intent: InstallIntent };
type SortKey = "online" | "name" | "host";
type SortDir = "asc" | "desc";
type OnlineFilter = "all" | "online" | "offline" | "pending";
type FilterOpenKey = "online" | "name" | "host";

const DENSITY_KEY = "remna.nodes.density";

function readDensity(): NodeListDensity {
  try {
    const v = localStorage.getItem(DENSITY_KEY);
    if (v === "compact" || v === "comfortable") return v;
  } catch {
    // ignore
  }
  return "comfortable";
}

export function NodesPage() {
  const {
    nodes,
    setNodes,
    hostings,
    reloadHostings,
    reloadNodes,
    statuses,
    agentStatuses,
    refreshAgents,
    openScriptRun,
    openWarpInstall,
    scriptBusy,
    remnawaveVersions,
    latestAgentVersion,
    latestWgcfVersion,
  } = useOutletContext<AppOutletContext>();
  const [error, setError] = useState<string | null>(null);
  const [formOpen, setFormOpen] = useState(false);
  const [editing, setEditing] = useState<NodeItem | null>(null);
  const [formBusy, setFormBusy] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [onlineFilter, setOnlineFilter] = useState<OnlineFilter>("all");
  const [nameFilter, setNameFilter] = useState("");
  const [hostFilter, setHostFilter] = useState("");
  const [filterOpen, setFilterOpen] = useState<Record<FilterOpenKey, boolean>>({
    online: false,
    name: false,
    host: false,
  });
  const [sortKey, setSortKey] = useState<SortKey>("name");
  const [sortDir, setSortDir] = useState<SortDir>("asc");
  const [density, setDensity] = useState<NodeListDensity>(() => readDensity());
  const [pendingDelete, setPendingDelete] = useState<NodeItem | null>(null);
  const [deleteBusy, setDeleteBusy] = useState(false);
  const [selectedIds, setSelectedIds] = useState<Set<string>>(() => new Set());
  const [confirmInstall, setConfirmInstall] = useState<ConfirmInstallState | null>(null);
  const [pendingReboot, setPendingReboot] = useState<NodeItem[] | null>(null);
  const [rebootBusy, setRebootBusy] = useState(false);
  const [rebootingIds, setRebootingIds] = useState<Set<string>>(() => new Set());
  const [viewJobId, setViewJobId] = useState<string | null>(null);
  const [haproxyNode, setHaproxyNode] = useState<NodeItem | null>(null);
  const [haproxyBusyId, setHaproxyBusyId] = useState<string | null>(null);
  const [destNode, setDestNode] = useState<NodeItem | null>(null);
  const [destBusyId, setDestBusyId] = useState<string | null>(null);
  const [capacityNode, setCapacityNode] = useState<NodeItem | null>(null);

  useEffect(() => {
    try {
      localStorage.setItem(DENSITY_KEY, density);
    } catch {
      // ignore
    }
  }, [density]);

  const onNodeUpdated = useCallback(
    (updated: NodeItem) => {
      setNodes((prev) => prev.map((n) => (n.id === updated.id ? updated : n)));
    },
    [setNodes],
  );

  const compact = density === "compact";
  const headerGrid = compact
    ? "lg:grid-cols-[28px_56px_minmax(100px,1fr)_minmax(100px,1fr)_48px_minmax(92px,auto)_minmax(48px,auto)_minmax(48px,auto)_minmax(110px,1fr)_minmax(64px,auto)_36px] lg:gap-2"
    : "lg:grid-cols-[32px_120px_minmax(140px,1fr)_minmax(130px,1fr)_90px_minmax(110px,auto)_minmax(56px,auto)_minmax(56px,auto)_minmax(140px,1.1fr)_minmax(100px,auto)_44px] lg:gap-3";

  const {
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
  } = useAgentInstallQueue({
    onNodeUpdated,
    onIdle: () => {
      void refreshAgents();
    },
  });

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    const nameQ = nameFilter.trim().toLowerCase();
    const hostQ = hostFilter.trim().toLowerCase();

    const list = nodes.filter((n) => {
      if (q) {
        const hit =
          n.name.toLowerCase().includes(q) ||
          n.host.toLowerCase().includes(q) ||
          (n.hosting_name ?? "").toLowerCase().includes(q);
        if (!hit) return false;
      }
      if (nameQ && !n.name.toLowerCase().includes(nameQ)) return false;
      if (hostQ && !n.host.toLowerCase().includes(hostQ)) return false;

      if (onlineFilter !== "all") {
        const st = statuses[n.id];
        if (onlineFilter === "pending" && st) return false;
        if (onlineFilter === "online" && !st?.online) return false;
        if (onlineFilter === "offline" && !(st && !st.online)) return false;
      }
      return true;
    });

    const rank = (id: string) => {
      const st = statuses[id];
      if (!st) return 1; // pending in the middle when sorting online asc = online, pending, offline
      return st.online ? 0 : 2;
    };

    const dir = sortDir === "asc" ? 1 : -1;
    return [...list].sort((a, b) => {
      let cmp = 0;
      if (sortKey === "online") {
        cmp = rank(a.id) - rank(b.id);
        if (cmp === 0) {
          const la = statuses[a.id]?.latency_ms;
          const lb = statuses[b.id]?.latency_ms;
          if (la != null && lb != null) cmp = la - lb;
          else if (la != null) cmp = -1;
          else if (lb != null) cmp = 1;
        }
      } else if (sortKey === "name") {
        cmp = a.name.localeCompare(b.name, "ru", { sensitivity: "base" });
      } else {
        cmp = a.host.localeCompare(b.host, "en", { numeric: true, sensitivity: "base" });
      }
      if (cmp === 0) {
        cmp = a.name.localeCompare(b.name, "ru", { sensitivity: "base" });
      }
      return cmp * dir;
    });
  }, [nodes, query, nameFilter, hostFilter, onlineFilter, statuses, sortKey, sortDir]);

  const filtersActive =
    onlineFilter !== "all" || nameFilter.trim() !== "" || hostFilter.trim() !== "" || query.trim() !== "";

  const selectedNodes = useMemo(
    () => filtered.filter((n) => selectedIds.has(n.id)),
    [filtered, selectedIds],
  );

  const allFilteredSelected =
    filtered.length > 0 && filtered.every((n) => selectedIds.has(n.id));

  const latestNodeVersion = remnawaveVersions?.node_version ?? null;
  const outdatedNodes = useMemo(
    () =>
      filtered.filter((n) =>
        remnanodeNeedsUpdate(agentStatuses[n.id]?.remnanode_version, latestNodeVersion),
      ),
    [filtered, agentStatuses, latestNodeVersion],
  );

  function toggleSort(key: SortKey) {
    if (sortKey === key) {
      setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    } else {
      setSortKey(key);
      setSortDir("asc");
    }
  }

  function clearColumnFilters() {
    setOnlineFilter("all");
    setNameFilter("");
    setHostFilter("");
    setFilterOpen({ online: false, name: false, host: false });
  }

  function toggleColumnFilter(key: FilterOpenKey) {
    setFilterOpen((prev) => {
      const nextOpen = !prev[key];
      if (!nextOpen) {
        if (key === "online") setOnlineFilter("all");
        if (key === "name") setNameFilter("");
        if (key === "host") setHostFilter("");
      }
      return { ...prev, [key]: nextOpen };
    });
  }

  const anyHeaderFilterOpen = filterOpen.online || filterOpen.name || filterOpen.host;
  const columnFiltersActive =
    onlineFilter !== "all" || nameFilter.trim() !== "" || hostFilter.trim() !== "";

  function toggleSelect(nodeId: string, selected: boolean) {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (selected) next.add(nodeId);
      else next.delete(nodeId);
      return next;
    });
  }

  function toggleSelectAll() {
    if (allFilteredSelected) {
      setSelectedIds((prev) => {
        const next = new Set(prev);
        for (const n of filtered) next.delete(n.id);
        return next;
      });
    } else {
      setSelectedIds((prev) => {
        const next = new Set(prev);
        for (const n of filtered) next.add(n.id);
        return next;
      });
    }
  }

  async function handleSubmit(values: NodeFormValues) {
    setFormBusy(true);
    setFormError(null);
    try {
      if (editing) {
        const updated = await api.updateNode(editing.id, values);
        setNodes((prev) => prev.map((n) => (n.id === updated.id ? updated : n)));
      } else {
        const created = await api.createNode(values);
        setNodes((prev) => [...prev, created].sort((a, b) => a.name.localeCompare(b.name)));
      }
      setFormOpen(false);
      setEditing(null);
    } catch (err) {
      setFormError(err instanceof Error ? err.message : "Ошибка сохранения");
    } finally {
      setFormBusy(false);
    }
  }

  async function confirmDelete() {
    if (!pendingDelete) return;
    setDeleteBusy(true);
    setError(null);
    try {
      await api.deleteNode(pendingDelete.id);
      setNodes((prev) => prev.filter((n) => n.id !== pendingDelete.id));
      setSelectedIds((prev) => {
        const next = new Set(prev);
        next.delete(pendingDelete.id);
        return next;
      });
      setPendingDelete(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось удалить");
    } finally {
      setDeleteBusy(false);
    }
  }

  function startConfirmedInstall(installDeps: boolean) {
    if (!confirmInstall || confirmInstall.nodes.length === 0) return;
    const targets = confirmInstall.nodes;
    enqueue(targets, installDeps);
    setConfirmInstall(null);
    setSelectedIds((prev) => {
      const next = new Set(prev);
      for (const n of targets) next.delete(n.id);
      return next;
    });
    if (targets.length === 1) {
      setViewJobId(targets[0].id);
    }
  }

  async function confirmReboot() {
    if (!pendingReboot || pendingReboot.length === 0) return;
    const targets = pendingReboot;
    setRebootBusy(true);
    setError(null);
    setRebootingIds(new Set(targets.map((n) => n.id)));

    const ok: string[] = [];
    const fail: string[] = [];
    const concurrency = 2;
    let idx = 0;

    async function worker() {
      while (idx < targets.length) {
        const current = targets[idx];
        idx += 1;
        try {
          const res = await api.rebootNode(current.id);
          if (res.ok) ok.push(current.name);
          else fail.push(`${current.name}: ${res.message}`);
        } catch (err) {
          fail.push(`${current.name}: ${err instanceof Error ? err.message : "ошибка"}`);
        } finally {
          setRebootingIds((prev) => {
            const next = new Set(prev);
            next.delete(current.id);
            return next;
          });
        }
      }
    }

    try {
      await Promise.all(Array.from({ length: Math.min(concurrency, targets.length) }, () => worker()));
      setPendingReboot(null);
      setSelectedIds((prev) => {
        const next = new Set(prev);
        for (const n of targets) next.delete(n.id);
        return next;
      });
      if (fail.length > 0) {
        setError(
          `Перезагрузка: OK ${ok.length}, ошибок ${fail.length}. ${fail.slice(0, 5).join("; ")}`,
        );
      }
    } finally {
      setRebootBusy(false);
      setRebootingIds(new Set());
    }
  }

  const viewJob = viewJobId ? jobs[viewJobId] : undefined;
  const viewNode = viewJob ? nodes.find((n) => n.id === viewJob.nodeId) : undefined;

  return (
    <div className="flex h-full flex-col">
      <header className="flex flex-col gap-3 border-b border-[var(--border)] px-4 py-4 sm:flex-row sm:items-center sm:justify-between sm:px-6">
        <div className="min-w-0">
          <h1 className="text-xl font-semibold tracking-tight">Ноды</h1>
          <p className="mt-0.5 text-sm text-[var(--muted)]">
            Online — ping; ресурсы — агент на ноде (порт 7422).
          </p>
        </div>
        <button
          type="button"
          onClick={() => {
            setEditing(null);
            setFormError(null);
            setFormOpen(true);
          }}
          className="shrink-0 rounded-lg bg-[var(--accent)] px-4 py-2 text-sm font-semibold text-[#06221e] transition hover:brightness-110"
        >
          + Добавить
        </button>
      </header>

      <div className="flex flex-col gap-2 border-b border-[var(--border)] px-4 py-3 sm:flex-row sm:items-center sm:gap-3 sm:px-6">
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Поиск по имени, IP, хостингу…"
          className="w-full max-w-md rounded-lg border border-[var(--border)] bg-[var(--bg-elevated)] px-3 py-2 text-sm outline-none focus:border-[var(--accent)]"
        />
        <button
          type="button"
          onClick={() => {
            void reloadNodes().catch((err: Error) => setError(err.message));
            void refreshAgents();
          }}
          className="shrink-0 rounded-lg border border-[var(--border)] px-3 py-2 text-sm text-[var(--muted)] hover:text-[var(--text)]"
        >
          Обновить
        </button>
        <div className="flex min-w-0 flex-wrap items-center gap-2 sm:ml-auto">
          <span className="text-xs text-[var(--muted)]">
            Показано {filtered.length} из {nodes.length}
            {columnFiltersActive && (
              <>
                {" · "}
                <button type="button" onClick={clearColumnFilters} className="text-[var(--accent)] hover:underline">
                  сбросить фильтры
                </button>
              </>
            )}
          </span>
          <button
            type="button"
            onClick={() => setDensity((d) => (d === "compact" ? "comfortable" : "compact"))}
            className="inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border border-[var(--border)] bg-[var(--bg-elevated)] text-[var(--muted)] transition hover:border-[var(--accent)] hover:text-[var(--accent)]"
            title={density === "compact" ? "Компактный вид — нажмите для обычного" : "Обычный вид — нажмите для компактного"}
            aria-label={density === "compact" ? "Компактный вид" : "Обычный вид"}
          >
            <DensityIcon mode={density} />
          </button>
        </div>
      </div>

      {selectedNodes.length > 0 && (
        <div className="flex flex-wrap items-center gap-2 border-b border-[var(--border)] bg-[var(--accent-dim)] px-4 py-2.5 sm:px-6">
          <span className="text-sm text-[var(--text)]">Выбрано: {selectedNodes.length}</span>
          <button
            type="button"
            onClick={() => setConfirmInstall({ nodes: selectedNodes, intent: "install" })}
            className="rounded-lg bg-[var(--accent)] px-3 py-1.5 text-sm font-semibold text-[#06221e] transition hover:brightness-110"
          >
            Установить агент
          </button>
          <button
            type="button"
            onClick={() => setConfirmInstall({ nodes: selectedNodes, intent: "reinstall" })}
            className="rounded-lg border border-[var(--accent)] bg-[var(--bg)] px-3 py-1.5 text-sm font-semibold text-[var(--accent)] transition hover:bg-[var(--accent-dim)]"
          >
            Переустановить агент
          </button>
          <button
            type="button"
            onClick={() => setPendingReboot(selectedNodes)}
            disabled={rebootBusy}
            className="rounded-lg border border-[rgba(230,162,60,0.45)] bg-[rgba(230,162,60,0.1)] px-3 py-1.5 text-sm font-semibold text-[var(--warning)] transition hover:bg-[rgba(230,162,60,0.18)] disabled:opacity-50"
          >
            Перезапустить ноду
          </button>
          <BulkScriptsMenu
            onAction={(action) => {
              openScriptRun(selectedNodes, action);
            }}
          />
          {selectedNodes.some(
            (n) =>
              !scriptBusy[n.id]?.warp &&
              warpNeedsInstall(agentStatuses[n.id], latestWgcfVersion),
          ) && (
            <button
              type="button"
              onClick={() => {
                const targets = selectedNodes.filter(
                  (n) =>
                    !scriptBusy[n.id]?.warp &&
                    warpNeedsInstall(agentStatuses[n.id], latestWgcfVersion),
                );
                const force = targets.some((n) => agentStatuses[n.id]?.warp_present === true);
                openWarpInstall(targets, force);
              }}
              className="rounded-lg border border-[var(--accent)] bg-[var(--bg)] px-3 py-1.5 text-sm font-semibold text-[var(--accent)] transition hover:bg-[var(--accent-dim)]"
            >
              Установить WARP
            </button>
          )}
          <button
            type="button"
            onClick={() => setSelectedIds(new Set())}
            className="rounded-lg border border-[var(--border)] px-3 py-1.5 text-sm text-[var(--muted)] hover:text-[var(--text)]"
          >
            Снять выбор
          </button>
        </div>
      )}

      {outdatedNodes.length > 0 && selectedNodes.length === 0 && (
        <div className="flex flex-wrap items-center gap-2 border-b border-[rgba(230,162,60,0.35)] bg-[rgba(230,162,60,0.08)] px-4 py-2.5 sm:px-6">
          <span className="text-sm text-[var(--warning)]">
            RemnaNode устарел на {outdatedNodes.length}{" "}
            {outdatedNodes.length === 1 ? "ноде" : "нодах"}
            {latestNodeVersion ? ` (актуально v${latestNodeVersion})` : ""}
          </span>
          <button
            type="button"
            onClick={() => openScriptRun(outdatedNodes, "update")}
            className="rounded-lg bg-[var(--accent)] px-3 py-1.5 text-sm font-semibold text-[#06221e] transition hover:brightness-110"
          >
            Обновить все
          </button>
        </div>
      )}

      <div className="flex min-h-0 flex-1 flex-col">
        <div
          className={`flex-1 overflow-auto px-3 py-3 sm:px-6 sm:py-4 ${compact ? "space-y-1" : "space-y-2"}`}
        >
          {filtered.length > 0 && (
            <label className="flex items-center gap-2 px-1 text-xs text-[var(--muted)] lg:hidden">
              <input
                type="checkbox"
                checked={allFilteredSelected}
                onChange={toggleSelectAll}
                className="h-4 w-4 accent-[var(--accent)]"
              />
              Выбрать все на экране
            </label>
          )}

          <div className="mb-2 space-y-2 lg:hidden">
            <div className="flex flex-wrap gap-2">
              <SortFilterChip
                label="Online"
                sortActive={sortKey === "online"}
                dir={sortDir}
                filterOpen={filterOpen.online}
                filterActive={onlineFilter !== "all"}
                onSort={() => toggleSort("online")}
                onToggleFilter={() => toggleColumnFilter("online")}
              />
              <SortFilterChip
                label="Нода"
                sortActive={sortKey === "name"}
                dir={sortDir}
                filterOpen={filterOpen.name}
                filterActive={nameFilter.trim() !== ""}
                onSort={() => toggleSort("name")}
                onToggleFilter={() => toggleColumnFilter("name")}
              />
              <SortFilterChip
                label="Host"
                sortActive={sortKey === "host"}
                dir={sortDir}
                filterOpen={filterOpen.host}
                filterActive={hostFilter.trim() !== ""}
                onSort={() => toggleSort("host")}
                onToggleFilter={() => toggleColumnFilter("host")}
              />
            </div>
            {(filterOpen.online || filterOpen.name || filterOpen.host) && (
              <div className="grid grid-cols-1 gap-2 sm:grid-cols-3">
                {filterOpen.online && (
                  <select
                    value={onlineFilter}
                    onChange={(e) => setOnlineFilter(e.target.value as OnlineFilter)}
                    className={headerFilterCls}
                    aria-label="Фильтр Online"
                    autoFocus
                  >
                    <option value="all">Online: все</option>
                    <option value="online">Online</option>
                    <option value="offline">Offline</option>
                    <option value="pending">Проверка…</option>
                  </select>
                )}
                {filterOpen.name && (
                  <input
                    value={nameFilter}
                    onChange={(e) => setNameFilter(e.target.value)}
                    placeholder="Нода…"
                    className={headerFilterCls}
                    aria-label="Фильтр ноды"
                    autoFocus={!filterOpen.online}
                  />
                )}
                {filterOpen.host && (
                  <input
                    value={hostFilter}
                    onChange={(e) => setHostFilter(e.target.value)}
                    placeholder="Host…"
                    className={headerFilterCls}
                    aria-label="Фильтр host"
                    autoFocus={!filterOpen.online && !filterOpen.name}
                  />
                )}
              </div>
            )}
          </div>

          <div
            className={`sticky top-0 z-[1] mb-1 hidden border-b border-[var(--border)] bg-[var(--bg)] pb-2 pt-1 lg:grid ${
              anyHeaderFilterOpen ? "items-end" : "items-center"
            } ${headerGrid} ${compact ? "px-2.5" : "px-4"}`}
          >
            <div className={`flex justify-center ${anyHeaderFilterOpen ? "pb-1.5" : ""}`}>
              <input
                type="checkbox"
                checked={allFilteredSelected}
                onChange={toggleSelectAll}
                className={`accent-[var(--accent)] ${compact ? "h-3.5 w-3.5" : "h-4 w-4"}`}
                aria-label="Выбрать все"
                disabled={filtered.length === 0}
              />
            </div>
            <FilterSortHeader
              label="Online"
              sortActive={sortKey === "online"}
              dir={sortDir}
              filterOpen={filterOpen.online}
              filterActive={onlineFilter !== "all"}
              onSort={() => toggleSort("online")}
              onToggleFilter={() => toggleColumnFilter("online")}
            >
              <select
                value={onlineFilter}
                onChange={(e) => setOnlineFilter(e.target.value as OnlineFilter)}
                className={headerFilterCls}
                aria-label="Фильтр Online"
              >
                <option value="all">Все</option>
                <option value="online">Online</option>
                <option value="offline">Offline</option>
                <option value="pending">…</option>
              </select>
            </FilterSortHeader>
            <FilterSortHeader
              label="Нода"
              sortActive={sortKey === "name"}
              dir={sortDir}
              filterOpen={filterOpen.name}
              filterActive={nameFilter.trim() !== ""}
              onSort={() => toggleSort("name")}
              onToggleFilter={() => toggleColumnFilter("name")}
            >
              <input
                value={nameFilter}
                onChange={(e) => setNameFilter(e.target.value)}
                placeholder="Фильтр…"
                className={headerFilterCls}
                aria-label="Фильтр ноды"
              />
            </FilterSortHeader>
            <FilterSortHeader
              label="Host"
              sortActive={sortKey === "host"}
              dir={sortDir}
              filterOpen={filterOpen.host}
              filterActive={hostFilter.trim() !== ""}
              onSort={() => toggleSort("host")}
              onToggleFilter={() => toggleColumnFilter("host")}
            >
              <input
                value={hostFilter}
                onChange={(e) => setHostFilter(e.target.value)}
                placeholder="Фильтр…"
                className={headerFilterCls}
                aria-label="Фильтр host"
              />
            </FilterSortHeader>
            <div
              className={`text-center text-[10px] font-semibold uppercase tracking-[0.12em] text-[var(--muted)] ${
                anyHeaderFilterOpen ? "pb-1.5" : ""
              }`}
            >
              {compact ? "Auth" : "Доступ"}
            </div>
            <div
              className={`text-center text-[10px] font-semibold uppercase tracking-[0.12em] text-[var(--muted)] ${
                anyHeaderFilterOpen ? "pb-1.5" : ""
              }`}
            >
              {compact ? "Ver" : "Версии"}
            </div>
            <div
              className={`text-center text-[10px] font-semibold uppercase tracking-[0.12em] text-[var(--muted)] ${
                anyHeaderFilterOpen ? "pb-1.5" : ""
              }`}
            >
              WARP
            </div>
            <div
              className={`text-center text-[10px] font-semibold uppercase tracking-[0.12em] text-[var(--muted)] ${
                anyHeaderFilterOpen ? "pb-1.5" : ""
              }`}
            >
              HAP
            </div>
            <div
              className={`text-center text-[10px] font-semibold uppercase tracking-[0.12em] text-[var(--muted)] ${
                anyHeaderFilterOpen ? "pb-1.5" : ""
              }`}
            >
              Агент
            </div>
            <div
              className={`text-center text-[10px] font-semibold uppercase tracking-[0.12em] text-[var(--muted)] ${
                anyHeaderFilterOpen ? "pb-1.5" : ""
              }`}
            >
              SSH
            </div>
            <div />
          </div>

          {error && (
            <div className="rounded-lg border border-[rgba(240,113,120,0.35)] bg-[rgba(240,113,120,0.08)] px-4 py-3 text-sm text-[var(--danger)]">
              {error}
            </div>
          )}
          {filtered.length === 0 && (
            <div className="rounded-[var(--radius)] border border-dashed border-[var(--border)] px-4 py-10 text-center text-sm text-[var(--muted)]">
              {nodes.length === 0
                ? "Нод пока нет. Добавьте первую."
                : filtersActive
                  ? "Ничего не найдено по фильтрам."
                  : "Нод пока нет. Добавьте первую."}
            </div>
          )}

          {filtered.map((node) => (
            <NodeRow
              key={node.id}
              node={node}
              status={statuses[node.id]}
              agent={agentStatuses[node.id]}
              selected={selectedIds.has(node.id)}
              density={density}
              onSelectChange={toggleSelect}
              installJob={jobs[node.id]}
              onEdit={(n) => {
                setEditing(n);
                setFormError(null);
                setFormOpen(true);
              }}
              onDelete={(n) => setPendingDelete(n)}
              onInstallAgent={(n) =>
                setConfirmInstall({
                  nodes: [n],
                  intent: n.agent_configured ? "reinstall" : "install",
                })
              }
              onReboot={(n) => setPendingReboot([n])}
              onRemnaScript={(n, action) => openScriptRun([n], action)}
              onInstallWarp={(n, force) => openWarpInstall([n], force)}
              onManageHaproxy={(n) => setHaproxyNode(n)}
              onPickDest={(n) => setDestNode(n)}
              onCapacityCheck={(n) => setCapacityNode(n)}
              warpBusy={!!scriptBusy[node.id]?.warp}
              haproxyBusy={haproxyBusyId === node.id}
              destBusy={destBusyId === node.id}
              remnaBusy={!!scriptBusy[node.id]?.remna}
              latestRemnanodeVersion={latestNodeVersion}
              latestAgentVersion={latestAgentVersion}
              latestWgcfVersion={latestWgcfVersion}
              onOpenInstallLog={(id) => setViewJobId(id)}
              rebooting={rebootingIds.has(node.id)}
            />
          ))}
        </div>

        <AgentInstallTray
          jobs={jobList}
          activeCount={activeCount}
          doneCount={doneCount}
          errorCount={errorCount}
          onOpen={(id) => setViewJobId(id)}
          onCancel={cancel}
          onDismissFinished={dismissFinished}
        />
      </div>

      {capacityNode && (
        <CapacityDialog node={capacityNode} onClose={() => setCapacityNode(null)} />
      )}

      {haproxyNode && (
        <HaproxyDialog
          node={haproxyNode}
          onClose={() => {
            setHaproxyNode(null);
            setHaproxyBusyId(null);
            void refreshAgents();
          }}
          onBusyChange={(busy) => setHaproxyBusyId(busy ? haproxyNode.id : null)}
        />
      )}

      {destNode && (
        <DestPickDialog
          node={destNode}
          onClose={() => {
            setDestNode(null);
            setDestBusyId(null);
          }}
          onBusyChange={(busy) => setDestBusyId(busy ? destNode.id : null)}
        />
      )}

      <NodeForm
        open={formOpen}
        initial={editing}
        hostings={hostings}
        busy={formBusy}
        error={formError}
        onHostingsChange={reloadHostings}
        onClose={() => {
          setFormOpen(false);
          setEditing(null);
        }}
        onSubmit={handleSubmit}
      />

      <ConfirmDialog
        open={Boolean(pendingDelete)}
        title="Удалить ноду?"
        message={
          pendingDelete
            ? `Нода «${pendingDelete.name}» (${pendingDelete.host}) будет удалена безвозвратно.`
            : ""
        }
        busy={deleteBusy}
        onCancel={() => {
          if (!deleteBusy) setPendingDelete(null);
        }}
        onConfirm={() => void confirmDelete()}
      />

      {confirmInstall && (
        <AgentInstallConfirmDialog
          nodes={confirmInstall.nodes}
          intent={confirmInstall.intent}
          onClose={() => setConfirmInstall(null)}
          onConfirm={startConfirmedInstall}
        />
      )}

      <ConfirmDialog
        open={Boolean(pendingReboot?.length)}
        title={
          pendingReboot && pendingReboot.length > 1
            ? `Перезагрузить ${pendingReboot.length} нод?`
            : "Перезагрузить ноду?"
        }
        message={
          pendingReboot && pendingReboot.length === 1
            ? `Нода «${pendingReboot[0].name}» (${pendingReboot[0].host}) будет перезагружена по SSH (reboot). Связь пропадёт на время рестарта.`
            : pendingReboot
              ? `Будет отправлена команда reboot на ${pendingReboot.length} нод по SSH. Связь пропадёт на время рестарта.`
              : ""
        }
        confirmLabel={
          pendingReboot && pendingReboot.length > 1
            ? `Перезагрузить (${pendingReboot.length})`
            : "Перезагрузить"
        }
        busy={rebootBusy}
        busyLabel="Перезагрузка…"
        danger
        onCancel={() => {
          if (!rebootBusy) setPendingReboot(null);
        }}
        onConfirm={() => void confirmReboot()}
      />

      {viewJob && (
        <AgentInstallLogDialog
          job={viewJob}
          onClose={() => setViewJobId(null)}
          onCancel={() => cancel(viewJob.nodeId)}
          onRetry={() => {
            if (viewNode) retry(viewNode);
          }}
          onDismiss={() => dismiss(viewJob.nodeId)}
        />
      )}
    </div>
  );
}

const headerFilterCls =
  "w-full min-w-0 rounded-md border border-[var(--border)] bg-[var(--bg-elevated)] px-2 py-1 text-xs font-normal normal-case tracking-normal text-[var(--text)] outline-none focus:border-[var(--accent)]";

function FilterSortHeader({
  label,
  sortActive,
  dir,
  filterOpen,
  filterActive,
  onSort,
  onToggleFilter,
  children,
}: {
  label: string;
  sortActive: boolean;
  dir: SortDir;
  filterOpen: boolean;
  filterActive: boolean;
  onSort: () => void;
  onToggleFilter: () => void;
  children: ReactNode;
}) {
  const fieldRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!filterOpen || !fieldRef.current) return;
    const el = fieldRef.current.querySelector<HTMLElement>("input, select");
    el?.focus();
  }, [filterOpen]);

  return (
    <div className="min-w-0">
      <div className={`flex items-center justify-center gap-0.5 ${filterOpen ? "mb-1" : ""}`}>
        <button
          type="button"
          onClick={onSort}
          className={[
            "inline-flex min-w-0 items-center justify-center gap-1 text-center text-[10px] font-semibold uppercase tracking-[0.12em] transition hover:text-[var(--text)]",
            sortActive ? "text-[var(--accent)]" : "text-[var(--muted)]",
          ].join(" ")}
        >
          <span className="truncate">{label}</span>
          <span className="shrink-0 font-mono text-[9px] opacity-80" aria-hidden>
            {sortActive ? (dir === "asc" ? "↑" : "↓") : "↕"}
          </span>
        </button>
        <button
          type="button"
          onClick={onToggleFilter}
          className={[
            "inline-flex h-5 w-5 shrink-0 items-center justify-center rounded transition",
            filterOpen || filterActive
              ? "text-[var(--accent)] hover:bg-[var(--accent-dim)]"
              : "text-[var(--muted)] hover:bg-[var(--bg-row)] hover:text-[var(--text)]",
          ].join(" ")}
          title={filterOpen ? "Скрыть и сбросить фильтр" : "Показать фильтр"}
          aria-label={filterOpen ? `Скрыть фильтр ${label}` : `Показать фильтр ${label}`}
          aria-pressed={filterOpen}
        >
          <FunnelIcon active={filterOpen || filterActive} />
        </button>
      </div>
      {filterOpen && <div ref={fieldRef}>{children}</div>}
    </div>
  );
}

function SortFilterChip({
  label,
  sortActive,
  dir,
  filterOpen,
  filterActive,
  onSort,
  onToggleFilter,
}: {
  label: string;
  sortActive: boolean;
  dir: SortDir;
  filterOpen: boolean;
  filterActive: boolean;
  onSort: () => void;
  onToggleFilter: () => void;
}) {
  return (
    <div
      className={[
        "inline-flex items-center overflow-hidden rounded-md border",
        sortActive || filterOpen || filterActive
          ? "border-[var(--accent)] bg-[var(--accent-dim)]"
          : "border-[var(--border)]",
      ].join(" ")}
    >
      <button
        type="button"
        onClick={onSort}
        className={[
          "px-2 py-1 text-[11px] font-semibold uppercase tracking-wide transition",
          sortActive ? "text-[var(--accent)]" : "text-[var(--muted)] hover:text-[var(--text)]",
        ].join(" ")}
      >
        {label}
        {sortActive ? (dir === "asc" ? " ↑" : " ↓") : ""}
      </button>
      <button
        type="button"
        onClick={onToggleFilter}
        className={[
          "border-l border-[var(--border)] px-1.5 py-1 transition",
          filterOpen || filterActive
            ? "text-[var(--accent)]"
            : "text-[var(--muted)] hover:text-[var(--text)]",
        ].join(" ")}
        title={filterOpen ? "Скрыть и сбросить фильтр" : "Показать фильтр"}
        aria-label={filterOpen ? `Скрыть фильтр ${label}` : `Показать фильтр ${label}`}
        aria-pressed={filterOpen}
      >
        <FunnelIcon active={filterOpen || filterActive} />
      </button>
    </div>
  );
}

function FunnelIcon({ active }: { active?: boolean }) {
  return (
    <svg
      width="12"
      height="12"
      viewBox="0 0 24 24"
      fill={active ? "currentColor" : "none"}
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
    >
      <path d="M4 4h16l-6 8v6l-4 2v-8L4 4z" />
    </svg>
  );
}

function DensityIcon({ mode }: { mode: NodeListDensity }) {
  if (mode === "compact") {
    return (
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden>
        <path d="M4 6h16M4 10h16M4 14h16M4 18h16" strokeLinecap="round" />
      </svg>
    );
  }
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden>
      <path d="M4 7h16M4 12h16M4 17h16" strokeLinecap="round" />
      <path d="M4 5.5v3M4 10.5v3M4 15.5v3" strokeLinecap="round" opacity="0.45" />
    </svg>
  );
}

const BULK_SCRIPT_ITEMS: { action: RemnaScriptAction; label: string }[] = [
  { action: "install", label: "Установить" },
  { action: "reinstall", label: "Переустановить" },
  { action: "update", label: "Обновить" },
  { action: "tune", label: "Параметры" },
];

function BulkScriptsMenu({ onAction }: { onAction: (action: RemnaScriptAction) => void }) {
  const [open, setOpen] = useState(false);

  return (
    <div className="relative">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="inline-flex items-center gap-1.5 rounded-lg border border-[var(--border)] px-3 py-1.5 text-sm text-[var(--text)] hover:border-[var(--accent)]"
        aria-expanded={open}
        aria-haspopup="menu"
      >
        RemnaNode
        <span className="text-[10px] text-[var(--muted)]" aria-hidden>
          ▾
        </span>
      </button>
      {open && (
        <>
          <div className="fixed inset-0 z-10" onClick={() => setOpen(false)} />
          <div
            role="menu"
            className="absolute left-0 z-20 mt-1 min-w-[200px] overflow-hidden rounded-lg border border-[var(--border)] bg-[var(--bg-elevated)] py-1 shadow-xl"
          >
            {BULK_SCRIPT_ITEMS.map((item) => (
              <button
                key={item.action}
                type="button"
                role="menuitem"
                className={[
                  "block w-full px-3 py-2 text-left text-sm hover:bg-[var(--accent-dim)]",
                  item.action === "update" ? "font-semibold text-[var(--accent)]" : "text-[var(--text)]",
                ].join(" ")}
                onClick={() => {
                  setOpen(false);
                  onAction(item.action);
                }}
              >
                {item.label}
              </button>
            ))}
          </div>
        </>
      )}
    </div>
  );
}
