import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import { Link, useOutletContext } from "react-router-dom";
import { api, type HaproxyLiveStats } from "../api/client";
import type { AppOutletContext } from "../components/AppShell";
import { CountryFlag } from "../components/CountryFlag";
import { HostingLogo } from "../components/HostingLogo";
import { formatBytes, HaproxyStatsView } from "../components/HaproxyStatsView";
import { NodeMetricsGrid } from "../components/NodeMetricsGrid";
import { OnlineBadge } from "../components/OnlineBadge";
import { agentNeedsUpdate, remnanodeNeedsUpdate } from "../hooks/useRemnawaveVersions";
import { countryName } from "../lib/countries";
import type { AgentStatus, NodeItem, OnlineStatus } from "../types";

type DashTab = "tiles" | "overview" | "issues" | "load" | "geo" | "versions" | "haproxy";

type IssueKind = "offline" | "no_agent" | "agent_down" | "token" | "port";

type Issue = {
  node: NodeItem;
  kind: IssueKind;
  label: string;
  detail: string;
};

type ResourceRow = {
  node: NodeItem;
  agent: AgentStatus;
  score: number;
};

type VersionRow = {
  node: NodeItem;
  agentVersion: string | null;
  remnaVersion: string | null;
  remnaRunning: boolean | null;
  agentOutdated: boolean;
  remnaOutdated: boolean;
};

const TABS: { id: DashTab; label: string }[] = [
  { id: "tiles", label: "Ноды" },
  { id: "overview", label: "Обзор" },
  { id: "issues", label: "Проблемы" },
  { id: "load", label: "Нагрузка" },
  { id: "geo", label: "Распределение" },
  { id: "versions", label: "Версии" },
  { id: "haproxy", label: "HAProxy" },
];

const TAB_KEY = "remna.dashboard.tab.v2";

function readTab(): DashTab {
  try {
    const v = localStorage.getItem(TAB_KEY);
    if (TABS.some((t) => t.id === v)) return v as DashTab;
  } catch {
    /* ignore */
  }
  return "tiles";
}

export function DashboardPage() {
  const {
    nodes,
    hostings,
    statuses,
    agentStatuses,
    latestAgentVersion,
    remnawaveVersions,
    reloadNodes,
    refreshAgents,
    refreshRemnawaveVersions,
  } = useOutletContext<AppOutletContext>();

  const [tab, setTab] = useState<DashTab>(() => readTab());
  const [hpStats, setHpStats] = useState<Record<string, HaproxyLiveStats>>({});
  const [hpLoading, setHpLoading] = useState(false);

  const haproxyNodes = useMemo(
    () =>
      nodes.filter((n) => {
        const agent = agentStatuses[n.id];
        return agent?.haproxy_present === true || /haproxy/i.test(n.name);
      }),
    [nodes, agentStatuses],
  );

  const haproxyKey = haproxyNodes.map((n) => n.id).join(",");

  const loadHaproxyFleet = useCallback(async () => {
    const targets = nodes.filter((n) => haproxyKey.split(",").includes(n.id));
    if (targets.length === 0) {
      setHpStats({});
      return;
    }
    setHpLoading(true);
    const entries = await Promise.all(
      targets.map(async (n) => {
        try {
          return [n.id, await api.getHaproxyStats(n.id)] as const;
        } catch (err) {
          const empty: HaproxyLiveStats = {
            node_id: n.id,
            node_name: n.name,
            host: n.host,
            uptime: null,
            curr_conns: null,
            cum_conns: null,
            conn_rate: null,
            bin: null,
            bout: null,
            rows: [],
            sessions: [],
            history: [],
            errors: "",
            error: err instanceof Error ? err.message : "ошибка",
          };
          return [n.id, empty] as const;
        }
      }),
    );
    setHpStats(Object.fromEntries(entries));
    setHpLoading(false);
  }, [haproxyKey, nodes]);

  useEffect(() => {
    if (tab !== "haproxy") return;
    void loadHaproxyFleet();
    const timer = window.setInterval(() => {
      void loadHaproxyFleet();
    }, 20000);
    return () => window.clearInterval(timer);
  }, [tab, haproxyKey, loadHaproxyFleet]);

  function selectTab(next: DashTab) {
    setTab(next);
    try {
      localStorage.setItem(TAB_KEY, next);
    } catch {
      /* ignore */
    }
  }

  const latestNodeVersion = remnawaveVersions?.node_version ?? null;

  const stats = useMemo(
    () =>
      computeStats(
        nodes,
        statuses,
        agentStatuses,
        hostings.length,
        latestAgentVersion,
        latestNodeVersion,
      ),
    [nodes, statuses, agentStatuses, hostings.length, latestAgentVersion, latestNodeVersion],
  );

  return (
    <div className="flex h-full flex-col">
      <header
        className={`flex flex-col gap-3 border-b border-[var(--border)] sm:flex-row sm:items-center sm:justify-between sm:px-6 ${
          tab === "tiles" ? "px-3 py-2" : "px-4 py-4 sm:px-6"
        }`}
      >
        <div className="min-w-0">
          <h1 className={`font-semibold tracking-tight ${tab === "tiles" ? "text-base" : "text-xl"}`}>
            Дашборд
          </h1>
          {tab !== "tiles" && (
            <p className="mt-0.5 text-sm text-[var(--muted)]">
              Сводка по нодам, агентам и RemnaNode.
            </p>
          )}
        </div>
        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            onClick={() => {
              void reloadNodes();
              void refreshAgents();
              void refreshRemnawaveVersions(true);
              if (tab === "haproxy") void loadHaproxyFleet();
            }}
            className="rounded-lg border border-[var(--border)] px-3 py-2 text-sm text-[var(--muted)] hover:text-[var(--text)]"
          >
            Обновить
          </button>
          <Link
            to="/nodes"
            className="rounded-lg bg-[var(--accent)] px-4 py-2 text-sm font-semibold text-[#06221e] transition hover:brightness-110"
          >
            К нодам
          </Link>
        </div>
      </header>

      <div className="border-b border-[var(--border)] px-3 sm:px-6">
        <nav className="-mb-px flex gap-1 overflow-x-auto" aria-label="Разделы дашборда">
          {TABS.map((t) => {
            const active = tab === t.id;
            const badge =
              t.id === "issues"
                ? stats.issues.length
                : t.id === "versions"
                  ? stats.versionOutdated
                  : t.id === "haproxy"
                    ? haproxyNodes.length
                    : 0;
            return (
              <button
                key={t.id}
                type="button"
                onClick={() => selectTab(t.id)}
                className={[
                  "relative shrink-0 px-3 py-2.5 text-sm transition",
                  active
                    ? "font-semibold text-[var(--accent)]"
                    : "text-[var(--muted)] hover:text-[var(--text)]",
                ].join(" ")}
              >
                <span className="inline-flex items-center gap-1.5">
                  {t.label}
                  {badge > 0 && (
                    <span
                      className={[
                        "rounded-full px-1.5 py-0.5 text-[10px] font-semibold tabular-nums",
                        active
                          ? "bg-[var(--accent-dim)] text-[var(--accent)]"
                          : "bg-[var(--bg-row)] text-[var(--muted)]",
                      ].join(" ")}
                    >
                      {badge}
                    </span>
                  )}
                </span>
                {active && (
                  <span className="absolute inset-x-2 bottom-0 h-0.5 rounded-full bg-[var(--accent)]" />
                )}
              </button>
            );
          })}
        </nav>
      </div>

      <div
        className={
          tab === "tiles"
            ? "min-h-0 flex-1 px-1.5 py-1.5"
            : "flex-1 space-y-4 overflow-auto px-3 py-3 sm:px-6 sm:py-4"
        }
      >
        {tab === "tiles" && (
          <NodeMetricsGrid nodes={nodes} statuses={statuses} agentStatuses={agentStatuses} />
        )}

        {tab === "overview" && (
          <>
            <section className="grid grid-cols-2 gap-2 sm:grid-cols-3 xl:grid-cols-6">
              <Stat label="Ноды" value={stats.total} />
              <Stat label="Online" value={stats.online} tone="success" />
              <Stat label="Offline" value={stats.offline} tone={stats.offline ? "danger" : "muted"} />
              <Stat label="Агент OK" value={stats.agentOk} tone="success" />
              <Stat
                label="Проблемы"
                value={stats.issues.length}
                tone={stats.issues.length ? "warning" : "muted"}
              />
              <Stat
                label="Устарели"
                value={stats.versionOutdated}
                tone={stats.versionOutdated ? "warning" : "muted"}
              />
            </section>

            <section className="grid gap-4 xl:grid-cols-2">
              <Panel
                title="Требуют внимания"
                subtitle={
                  stats.issues.length
                    ? `${stats.issues.length} нод с проблемами`
                    : "Критических проблем нет"
                }
                action={
                  <button
                    type="button"
                    onClick={() => selectTab("issues")}
                    className="text-xs text-[var(--accent)] hover:underline"
                  >
                    Все проблемы
                  </button>
                }
              >
                <IssuesList issues={stats.issues.slice(0, 8)} statuses={statuses} empty="Все ноды online, агенты отвечают." />
              </Panel>

              <Panel
                title="Нагрузка"
                subtitle="Топ по CPU / RAM / Disk"
                action={
                  <button
                    type="button"
                    onClick={() => selectTab("load")}
                    className="text-xs text-[var(--accent)] hover:underline"
                  >
                    Подробнее
                  </button>
                }
              >
                <LoadList rows={stats.hot.slice(0, 5)} empty="Нет данных от агентов." />
              </Panel>
            </section>

            <section className="grid gap-2 sm:grid-cols-3">
              <QuickLink to="/nodes" title="Ноды" desc="Список, SSH, агент, RemnaNode" />
              <QuickLink to="/hostings" title="Хостинги" desc="Справочник и favicon" />
              <QuickLink to="/scripts" title="Скрипты" desc="Дефолты RemnaNode" />
              <QuickLink to="/settings" title="Настройки" desc="SECRET_KEY и параллелизм" />
            </section>
          </>
        )}

        {tab === "issues" && (
          <Panel
            title="Проблемы"
            subtitle={
              stats.issues.length
                ? `${stats.issues.length} нод · offline / агент / токен / порт`
                : "Проблем не найдено"
            }
            action={
              <Link to="/nodes" className="text-xs text-[var(--accent)] hover:underline">
                Открыть ноды
              </Link>
            }
          >
            <IssuesList issues={stats.issues} statuses={statuses} empty="Критических проблем нет." />
          </Panel>
        )}

        {tab === "load" && (
          <Panel
            title="Нагрузка агентов"
            subtitle={`${stats.hot.length} online-агентов · сортировка по max(CPU, RAM, Disk)`}
          >
            <LoadList rows={stats.hot} empty="Нет данных от агентов — установите агент на ноды." />
          </Panel>
        )}

        {tab === "geo" && (
          <section className="grid gap-4 lg:grid-cols-2">
            <Panel title="По хостингам" subtitle={`${hostings.length} в справочнике`}>
              {stats.byHosting.length === 0 ? (
                <Empty text="Хостинги ещё не привязаны к нодам." />
              ) : (
                <ul className="space-y-2">
                  {stats.byHosting.map((row) => (
                    <li key={row.key} className="flex items-center gap-3">
                      {row.favicon != null || row.name !== "Без хостинга" ? (
                        <HostingLogo name={row.name} faviconData={row.favicon} size={20} />
                      ) : (
                        <span className="inline-flex h-5 w-5 items-center justify-center rounded-md bg-[var(--border)] text-[10px] text-[var(--muted)]">
                          —
                        </span>
                      )}
                      <div className="min-w-0 flex-1">
                        <div className="truncate text-sm text-[var(--text)]">{row.name}</div>
                        <div className="h-1.5 overflow-hidden rounded-full bg-[var(--border)]">
                          <div
                            className="h-full rounded-full bg-[var(--accent)]"
                            style={{ width: `${row.pct}%` }}
                          />
                        </div>
                      </div>
                      <div className="shrink-0 text-right text-xs text-[var(--muted)]">
                        <div className="font-semibold text-[var(--text)]">{row.count}</div>
                        <div>
                          <span className="text-[var(--success)]">{row.online}</span>
                          {" / "}
                          <span className={row.offline ? "text-[var(--danger)]" : undefined}>
                            {row.offline}
                          </span>
                        </div>
                      </div>
                    </li>
                  ))}
                </ul>
              )}
            </Panel>

            <Panel title="По странам" subtitle="По полю country у ноды">
              {stats.byCountry.length === 0 ? (
                <Empty text="Страны у нод не указаны." />
              ) : (
                <ul className="space-y-2">
                  {stats.byCountry.map((row) => (
                    <li key={row.code} className="flex items-center gap-3">
                      {row.code !== "?" ? (
                        <CountryFlag code={row.code} size={16} />
                      ) : (
                        <span className="inline-flex h-4 w-5 items-center justify-center text-[10px] text-[var(--muted)]">
                          ?
                        </span>
                      )}
                      <div className="min-w-0 flex-1 truncate text-sm text-[var(--text)]">{row.name}</div>
                      <div className="shrink-0 text-xs text-[var(--muted)]">
                        <span className="font-semibold text-[var(--text)]">{row.count}</span>
                        <span className="mx-1">·</span>
                        <span className="text-[var(--success)]">{row.online}</span>
                      </div>
                    </li>
                  ))}
                </ul>
              )}
            </Panel>
          </section>
        )}

        {tab === "versions" && (
          <Panel
            title="Версии"
            subtitle={
              [
                remnawaveVersions?.panel_version
                  ? `Panel GitHub v${remnawaveVersions.panel_version}`
                  : null,
                latestNodeVersion ? `Node GitHub v${latestNodeVersion}` : null,
                latestAgentVersion ? `Агент панели v${latestAgentVersion}` : null,
              ]
                .filter(Boolean)
                .join(" · ") || "Актуальные версии с панели / GitHub"
            }
            action={
              <Link to="/nodes" className="text-xs text-[var(--accent)] hover:underline">
                Обновить на нодах
              </Link>
            }
          >
            {stats.versions.length === 0 ? (
              <Empty text="Нет нод с данными агента." />
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full min-w-[520px] text-left text-sm">
                  <thead>
                    <tr className="border-b border-[var(--border)] text-[10px] font-semibold uppercase tracking-[0.12em] text-[var(--muted)]">
                      <th className="pb-2 pr-3 font-semibold">Нода</th>
                      <th className="pb-2 pr-3 text-center font-semibold">Агент</th>
                      <th className="pb-2 pr-3 text-center font-semibold">RemnaNode</th>
                      <th className="pb-2 text-center font-semibold">Статус</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-[var(--border)]">
                    {stats.versions.map((row) => (
                      <tr key={row.node.id}>
                        <td className="py-2.5 pr-3">
                          <div className="font-medium text-[var(--text)]">{row.node.name}</div>
                          <div className="font-mono text-[11px] text-[var(--muted)]">{row.node.host}</div>
                        </td>
                        <td
                          className={`py-2.5 pr-3 text-center tabular-nums ${
                            row.agentOutdated ? "text-[var(--warning)]" : "text-[var(--text)]"
                          }`}
                        >
                          {row.agentVersion ? `v${row.agentVersion}` : "—"}
                        </td>
                        <td
                          className={`py-2.5 pr-3 text-center tabular-nums ${
                            row.remnaOutdated ? "text-[var(--warning)]" : "text-[var(--text)]"
                          }`}
                        >
                          {row.remnaVersion
                            ? `v${row.remnaVersion}`
                            : row.remnaRunning
                              ? "?"
                              : "—"}
                        </td>
                        <td className="py-2.5 text-center">
                          {row.agentOutdated || row.remnaOutdated ? (
                            <span className="rounded-full border border-[rgba(230,162,60,0.35)] bg-[rgba(230,162,60,0.1)] px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-[var(--warning)]">
                              Устарело
                            </span>
                          ) : (
                            <span className="text-[10px] uppercase tracking-wide text-[var(--muted)]">
                              OK
                            </span>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Panel>
        )}

        {tab === "haproxy" && (
          <HaproxyDash
            nodes={haproxyNodes}
            stats={hpStats}
            loading={hpLoading}
            onRefresh={() => void loadHaproxyFleet()}
          />
        )}
      </div>
    </div>
  );
}

function HaproxyDash({
  nodes,
  stats,
  loading,
  onRefresh,
}: {
  nodes: NodeItem[];
  stats: Record<string, HaproxyLiveStats>;
  loading: boolean;
  onRefresh: () => void;
}) {
  const list = nodes.map((n) => stats[n.id]).filter(Boolean);
  const curr = list.reduce((s, x) => s + (x.curr_conns ?? 0), 0);
  const bin = list.reduce((s, x) => s + (x.bin ?? 0), 0);
  const bout = list.reduce((s, x) => s + (x.bout ?? 0), 0);
  const bad = list.filter((x) => x.error || x.rows.some((r) => /down|nolb/i.test(r.status))).length;

  return (
    <div className="space-y-4">
      <section className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        <Stat label="Коробки" value={nodes.length} />
        <Stat label="Сейчас сессий" value={curr} />
        <Stat label="Проблемы" value={bad} tone={bad ? "danger" : "muted"} />
        <div className="rounded-[var(--radius)] border border-[var(--border)] bg-[var(--bg-row)] px-3 py-3">
          <div className="text-[10px] font-semibold uppercase tracking-[0.12em] text-[var(--muted)]">
            Трафик
          </div>
          <div className="mt-1 text-sm font-semibold tabular-nums text-[var(--text)]">
            ↓ {formatBytes(bin)} · ↑ {formatBytes(bout)}
          </div>
        </div>
      </section>

      {nodes.length === 0 ? (
        <Empty text="HAProxy ни на одной ноде не найден. Поставьте его из списка нод." />
      ) : (
        nodes.map((n) => (
          <Panel
            key={n.id}
            title={n.name}
            subtitle={n.host}
            action={
              <button
                type="button"
                onClick={onRefresh}
                className="text-xs text-[var(--accent)] hover:underline"
              >
                {loading ? "Снимаю…" : "Обновить"}
              </button>
            }
          >
            <HaproxyStatsView stats={stats[n.id] ?? null} loading={loading && !stats[n.id]} />
          </Panel>
        ))
      )}
    </div>
  );
}

function IssuesList({
  issues,
  statuses,
  empty,
}: {
  issues: Issue[];
  statuses: Record<string, OnlineStatus>;
  empty: string;
}) {
  if (issues.length === 0) return <Empty text={empty} />;
  return (
    <ul className="divide-y divide-[var(--border)]">
      {issues.map((issue) => (
        <li key={`${issue.node.id}-${issue.kind}`} className="flex items-start gap-3 py-2.5 first:pt-0 last:pb-0">
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-2">
              <Link
                to="/nodes"
                className="truncate font-medium text-[var(--text)] hover:text-[var(--accent)]"
              >
                {issue.node.name}
              </Link>
              <IssueBadge kind={issue.kind} label={issue.label} />
            </div>
            <div className="mt-0.5 truncate font-mono text-[11px] text-[var(--muted)]">
              {issue.node.host}
              {issue.detail ? ` · ${issue.detail}` : ""}
            </div>
          </div>
          <OnlineBadge status={statuses[issue.node.id]} />
        </li>
      ))}
    </ul>
  );
}

function LoadList({ rows, empty }: { rows: ResourceRow[]; empty: string }) {
  if (rows.length === 0) return <Empty text={empty} />;
  return (
    <ul className="space-y-2">
      {rows.map(({ node, agent }) => (
        <li key={node.id} className="rounded-lg border border-[var(--border)] bg-[var(--bg)] px-3 py-2">
          <div className="flex items-center justify-between gap-2">
            <div className="min-w-0">
              <div className="truncate text-sm font-medium text-[var(--text)]">{node.name}</div>
              <div className="truncate font-mono text-[11px] text-[var(--muted)]">{node.host}</div>
            </div>
            {agent.version && (
              <span className="shrink-0 text-[10px] text-[var(--muted)]">v{agent.version}</span>
            )}
          </div>
          <div className="mt-2 grid grid-cols-3 gap-2">
            <Meter label="CPU" value={agent.cpu_percent} />
            <Meter label="RAM" value={agent.mem_percent} />
            <Meter label="Disk" value={agent.disk_percent} />
          </div>
        </li>
      ))}
    </ul>
  );
}

function computeStats(
  nodes: NodeItem[],
  statuses: Record<string, OnlineStatus>,
  agents: Record<string, AgentStatus>,
  hostingsCount: number,
  latestAgentVersion: string | null,
  latestNodeVersion: string | null,
) {
  let online = 0;
  let offline = 0;
  let agentOk = 0;
  const issues: Issue[] = [];
  const hot: ResourceRow[] = [];
  const versions: VersionRow[] = [];

  const hostingMap = new Map<
    string,
    { key: string; name: string; favicon: string | null; count: number; online: number; offline: number }
  >();
  const countryMap = new Map<string, { code: string; name: string; count: number; online: number }>();

  for (const node of nodes) {
    const st = statuses[node.id];
    const isOnline = Boolean(st?.online);
    if (st) {
      if (isOnline) online += 1;
      else offline += 1;
    }

    const ag = agents[node.id];
    if (ag?.present) {
      agentOk += 1;
      const score = Math.max(ag.cpu_percent ?? 0, ag.mem_percent ?? 0, ag.disk_percent ?? 0);
      hot.push({ node, agent: ag, score });
      const agentOutdated = agentNeedsUpdate(ag.version, latestAgentVersion);
      const remnaOutdated = remnanodeNeedsUpdate(ag.remnanode_version, latestNodeVersion);
      versions.push({
        node,
        agentVersion: ag.version,
        remnaVersion: ag.remnanode_version,
        remnaRunning: ag.remnanode_running,
        agentOutdated,
        remnaOutdated,
      });
    } else if (!node.agent_configured) {
      issues.push({
        node,
        kind: "no_agent",
        label: "Нет агента",
        detail: "не установлен",
      });
    } else if (ag && !ag.present) {
      const err = ag.error ?? "Нет ответа";
      const low = err.toLowerCase();
      if (low.includes("токен") || low.includes("авторизац")) {
        issues.push({ node, kind: "token", label: "Токен", detail: err });
      } else if (low.includes("порт") || low.includes("security") || low.includes("firewall")) {
        issues.push({ node, kind: "port", label: "Порт", detail: err });
      } else {
        issues.push({ node, kind: "agent_down", label: "Агент", detail: err });
      }
    }

    if (st && !st.online) {
      issues.push({
        node,
        kind: "offline",
        label: "Offline",
        detail: st.method ? `ping/${st.method}` : "не отвечает",
      });
    }

    const hKey = node.hosting_id ?? "__none__";
    const hName = node.hosting_name ?? "Без хостинга";
    const h = hostingMap.get(hKey) ?? {
      key: hKey,
      name: hName,
      favicon: node.hosting_favicon_data,
      count: 0,
      online: 0,
      offline: 0,
    };
    h.count += 1;
    if (isOnline) h.online += 1;
    else if (st) h.offline += 1;
    hostingMap.set(hKey, h);

    const code = (node.country_code || "?").toUpperCase();
    const c = countryMap.get(code) ?? {
      code,
      name: code === "?" ? "Не указана" : countryName(code) || code,
      count: 0,
      online: 0,
    };
    c.count += 1;
    if (isOnline) c.online += 1;
    countryMap.set(code, c);
  }

  const priority: Record<IssueKind, number> = {
    offline: 0,
    token: 1,
    port: 2,
    agent_down: 3,
    no_agent: 4,
  };
  const best = new Map<string, Issue>();
  for (const issue of issues) {
    const prev = best.get(issue.node.id);
    if (!prev || priority[issue.kind] < priority[prev.kind]) {
      best.set(issue.node.id, issue);
    }
  }
  const uniqueIssues = [...best.values()].sort((a, b) => {
    const p = priority[a.kind] - priority[b.kind];
    if (p !== 0) return p;
    return a.node.name.localeCompare(b.node.name);
  });

  hot.sort((a, b) => b.score - a.score);
  versions.sort((a, b) => {
    const ao = Number(a.agentOutdated || a.remnaOutdated);
    const bo = Number(b.agentOutdated || b.remnaOutdated);
    if (ao !== bo) return bo - ao;
    return a.node.name.localeCompare(b.node.name);
  });

  const total = nodes.length || 1;
  const byHosting = [...hostingMap.values()]
    .sort((a, b) => b.count - a.count)
    .map((row) => ({ ...row, pct: Math.round((row.count / total) * 100) }));

  const byCountry = [...countryMap.values()].sort((a, b) => b.count - a.count);
  const versionOutdated = versions.filter((v) => v.agentOutdated || v.remnaOutdated).length;

  return {
    total: nodes.length,
    online,
    offline,
    agentOk,
    hostings: hostingsCount,
    issues: uniqueIssues,
    hot,
    byHosting,
    byCountry,
    versions,
    versionOutdated,
  };
}

function Stat({
  label,
  value,
  tone = "default",
}: {
  label: string;
  value: number;
  tone?: "default" | "success" | "danger" | "warning" | "muted";
}) {
  const valueCls =
    tone === "success"
      ? "text-[var(--success)]"
      : tone === "danger"
        ? "text-[var(--danger)]"
        : tone === "warning"
          ? "text-[var(--warning)]"
          : tone === "muted"
            ? "text-[var(--muted)]"
            : "text-[var(--text)]";

  return (
    <div className="rounded-[var(--radius)] border border-[var(--border)] bg-[var(--bg-row)] px-3 py-3">
      <div className="text-[10px] font-semibold uppercase tracking-[0.12em] text-[var(--muted)]">{label}</div>
      <div className={`mt-1 text-2xl font-semibold tabular-nums ${valueCls}`}>{value}</div>
    </div>
  );
}

function Panel({
  title,
  subtitle,
  action,
  children,
}: {
  title: string;
  subtitle?: string;
  action?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className="rounded-[var(--radius)] border border-[var(--border)] bg-[var(--bg-elevated)] p-4">
      <div className="mb-3 flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h2 className="text-sm font-semibold text-[var(--text)]">{title}</h2>
          {subtitle && <p className="mt-0.5 text-xs text-[var(--muted)]">{subtitle}</p>}
        </div>
        {action}
      </div>
      {children}
    </section>
  );
}

function Empty({ text }: { text: string }) {
  return (
    <div className="rounded-lg border border-dashed border-[var(--border)] px-3 py-6 text-center text-sm text-[var(--muted)]">
      {text}
    </div>
  );
}

function IssueBadge({ kind, label }: { kind: IssueKind; label: string }) {
  const cls =
    kind === "offline" || kind === "agent_down"
      ? "border-[rgba(240,113,120,0.35)] bg-[rgba(240,113,120,0.1)] text-[var(--danger)]"
      : kind === "token" || kind === "port"
        ? "border-[rgba(230,162,60,0.35)] bg-[rgba(230,162,60,0.1)] text-[var(--warning)]"
        : "border-[rgba(34,211,187,0.35)] bg-[var(--accent-dim)] text-[var(--accent)]";
  return (
    <span className={`rounded-full border px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide ${cls}`}>
      {label}
    </span>
  );
}

function Meter({ label, value }: { label: string; value: number | null | undefined }) {
  const v = value == null || Number.isNaN(value) ? null : Math.max(0, Math.min(100, value));
  const tone =
    v == null
      ? "bg-[var(--border)]"
      : v >= 90
        ? "bg-[var(--danger)]"
        : v >= 75
          ? "bg-[var(--warning)]"
          : "bg-[var(--accent)]";
  return (
    <div>
      <div className="mb-1 flex justify-between text-[10px] text-[var(--muted)]">
        <span>{label}</span>
        <span className="tabular-nums text-[var(--text)]">{v == null ? "—" : `${Math.round(v)}%`}</span>
      </div>
      <div className="h-1.5 overflow-hidden rounded-full bg-[var(--border)]">
        <div className={`h-full rounded-full ${tone}`} style={{ width: `${v ?? 0}%` }} />
      </div>
    </div>
  );
}

function QuickLink({ to, title, desc }: { to: string; title: string; desc: string }) {
  return (
    <Link
      to={to}
      className="rounded-[var(--radius)] border border-[var(--border)] bg-[var(--bg-row)] px-4 py-3 transition hover:border-[var(--accent)] hover:bg-[var(--bg-row-hover)]"
    >
      <div className="text-sm font-medium text-[var(--text)]">{title}</div>
      <div className="mt-0.5 text-xs text-[var(--muted)]">{desc}</div>
    </Link>
  );
}
