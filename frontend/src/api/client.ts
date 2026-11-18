import type {
  AgentMap,
  AgentStatus,
  AuthType,
  HostingItem,
  MetricsRange,
  NodeFormValues,
  NodeItem,
  NodesMetricsResponse,
  OnlineMap,
  SharingBanResult,
  SharingBans,
  SharingConfig,
  SharingConfigUpdate,
  SharingDossier,
  SharingStatus,
  SshCheckResult,
  XrayOnlineMap,
} from "../types";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers ?? {}),
    },
    ...init,
  });

  if (res.status === 204) {
    return undefined as T;
  }

  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const detail = typeof data.detail === "string" ? data.detail : "Ошибка запроса";
    throw new Error(detail);
  }
  return data as T;
}

export const api = {
  me: () => request<{ username: string }>("/api/auth/me"),
  login: (username: string, password: string) =>
    request<{ username: string }>("/api/auth/login", {
      method: "POST",
      body: JSON.stringify({ username, password }),
    }),
  logout: () => request<void>("/api/auth/logout", { method: "POST" }),

  listHostings: () => request<HostingItem[]>("/api/hostings"),
  createHosting: (payload: {
    name: string;
    website_url?: string | null;
    notes?: string | null;
    bandwidth_mbps?: number | null;
  }) =>
    request<HostingItem>("/api/hostings", {
      method: "POST",
      body: JSON.stringify({
        name: payload.name,
        website_url: payload.website_url ?? null,
        notes: payload.notes ?? null,
        bandwidth_mbps: payload.bandwidth_mbps ?? null,
      }),
    }),
  updateHosting: (
    id: string,
    payload: {
      name?: string;
      website_url?: string | null;
      notes?: string | null;
      bandwidth_mbps?: number | null;
    },
  ) =>
    request<HostingItem>(`/api/hostings/${id}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    }),
  refreshHostingFavicon: (id: string) =>
    request<HostingItem>(`/api/hostings/${id}/refresh-favicon`, { method: "POST" }),
  deleteHosting: (id: string) => request<void>(`/api/hostings/${id}`, { method: "DELETE" }),

  listNodes: () => request<NodeItem[]>("/api/nodes"),
  createNode: (values: NodeFormValues) =>
    request<NodeItem>("/api/nodes", {
      method: "POST",
      body: JSON.stringify(toPayload(values, true)),
    }),
  updateNode: (id: string, values: NodeFormValues) =>
    request<NodeItem>(`/api/nodes/${id}`, {
      method: "PATCH",
      body: JSON.stringify(toPayload(values, false)),
    }),
  setNodeMonitoring: (id: string, enabled: boolean) =>
    request<NodeItem>(`/api/nodes/${id}`, {
      method: "PATCH",
      body: JSON.stringify({ monitoring_enabled: enabled }),
    }),
  deleteNode: (id: string) => request<void>(`/api/nodes/${id}`, { method: "DELETE" }),
  getSecret: (id: string) =>
    request<{ auth_type: AuthType; secret: string }>(`/api/nodes/${id}/secret`),
  online: () => request<{ statuses: OnlineMap }>("/api/nodes/online"),
  nodeMetrics: (range: MetricsRange = "day") =>
    request<NodesMetricsResponse>(`/api/nodes/metrics?range=${range}`),
  nodeMetricsOne: (id: string, range: MetricsRange = "day") =>
    request<NodesMetricsResponse>(`/api/nodes/${id}/metrics?range=${range}`),
  agents: () =>
    request<{
      statuses: AgentMap;
      latest_agent_version: string;
      latest_wgcf_version: string | null;
      xray_online: XrayOnlineMap;
    }>("/api/nodes/agents"),
  agentsStream: (opts: {
    onEvent: (event: AgentsStreamEvent) => void;
    signal?: AbortSignal;
  }) =>
    streamNdjson<AgentsStreamEvent>("/api/nodes/agents/stream", {
      method: "GET",
      signal: opts.signal,
      onEvent: opts.onEvent,
    }),
  sharingStatus: () => request<SharingStatus>("/api/sharing/status"),
  sharingScan: () =>
    request<SharingStatus>("/api/sharing/scan", { method: "POST" }),
  sharingDossier: (nodeId: string) =>
    request<SharingDossier>(`/api/sharing/nodes/${nodeId}/dossier`),
  sharingUserDossier: (userId: number) =>
    request<SharingDossier>(`/api/sharing/users/${userId}/dossier`),
  /** Отчёт-доказательство (md — технический, txt — клиентский текст): свежий для жёлтой/красной зоны, иначе копия на момент бана. */
  sharingUserReport: async (userId: number, saved = false, format: "md" | "txt" = "md") => {
    const q = new URLSearchParams({ fmt: format });
    if (saved) q.set("saved", "true");
    const res = await fetch(`/api/sharing/users/${userId}/report?${q.toString()}`, {
      credentials: "include",
    });
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      throw new Error(typeof data.detail === "string" ? data.detail : "Не удалось собрать отчёт");
    }
    const cd = res.headers.get("Content-Disposition") ?? "";
    const m = /filename="([^"]+)"/.exec(cd);
    return { filename: m ? m[1] : `sharing-${userId}.${format}`, text: await res.text() };
  },
  sharingBans: () => request<SharingBans>("/api/sharing/bans"),
  sharingBan: (userId: number) =>
    request<SharingBanResult>(`/api/sharing/users/${userId}/ban`, { method: "POST" }),
  sharingUnban: (userId: number) =>
    request<SharingBanResult>(`/api/sharing/users/${userId}/unban`, { method: "POST" }),
  sharingSettings: () => request<SharingConfig>("/api/sharing/settings"),
  sharingUpdate: (body: SharingConfigUpdate) =>
    request<SharingConfig>("/api/sharing/settings", {
      method: "PATCH",
      body: JSON.stringify(body),
    }),
  sshCheck: (id: string) =>
    request<SshCheckResult>(`/api/nodes/${id}/ssh-check`, { method: "POST" }),
  rebootNode: (id: string) =>
    request<{ ok: boolean; message: string }>(`/api/nodes/${id}/reboot`, { method: "POST" }),
  installAgentStream: (
    id: string,
    opts: {
      onEvent: (event: AgentInstallStreamEvent) => void;
      signal?: AbortSignal;
      installDeps?: boolean;
    },
  ) =>
    streamNdjson<AgentInstallStreamEvent>(
      `/api/nodes/${id}/agent/install${opts.installDeps ? "?install_deps=true" : ""}`,
      {
        method: "POST",
        signal: opts.signal,
        onEvent: opts.onEvent,
      },
    ),
  getRemnawaveVersions: (force = false) =>
    request<RemnawaveVersions>(`/api/remnawave/versions${force ? "?force=true" : ""}`),
  getSettings: () => request<AppSettings>("/api/settings"),
  updateSettings: (payload: {
    remna_secret_key?: string;
    clear_remna_secret_key?: boolean;
    defaults?: RemnaScriptDefaults;
  }) =>
    request<AppSettings>("/api/settings", {
      method: "PATCH",
      body: JSON.stringify(payload),
    }),
  resetMetrics: () =>
    request<{ deleted: number }>("/api/settings/reset-metrics", { method: "POST" }),
  auditChecks: () => request<AuditStatus>("/api/audit/checks"),
  auditRun: (id: string) =>
    request<AuditStatus>(`/api/audit/checks/${id}/run`, { method: "POST" }),
  auditUpdateSettings: (payload: { cert_domains: string[] }) =>
    request<{ cert_domains: string[] }>("/api/audit/settings", {
      method: "PATCH",
      body: JSON.stringify(payload),
    }),

  subwatchStatus: () => request<SubwatchStatus>("/api/subwatch/status"),
  subwatchCheck: () => request<SubwatchStatus>("/api/subwatch/check", { method: "POST" }),
  subwatchUpdate: (payload: SubwatchUpdate) =>
    request<SubwatchStatus>("/api/subwatch/settings", {
      method: "PATCH",
      body: JSON.stringify(payload),
    }),
  subwatchTelegramTest: () =>
    request<{ sent: boolean }>("/api/subwatch/telegram/test", { method: "POST" }),
  // proxy: адрес из поля формы для разовой проверки (не сохраняется); без него — сохранённый
  subwatchCheckProxy: (proxy?: string) =>
    request<{ ok: boolean; username: string; message: string }>(
      "/api/subwatch/telegram/check-proxy",
      {
        method: "POST",
        ...(proxy !== undefined ? { body: JSON.stringify({ proxy }) } : {}),
      },
    ),
  subwatchDetectChat: (proxy?: string) =>
    request<{ chats: DetectedChat[]; hint: string | null }>("/api/subwatch/detect-chat", {
      method: "POST",
      ...(proxy !== undefined ? { body: JSON.stringify({ proxy }) } : {}),
    }),

  ycwatchStatus: () => request<YcwatchStatus>("/api/ycwatch/status"),
  ycwatchCheck: () => request<YcwatchStatus>("/api/ycwatch/check", { method: "POST" }),
  ycwatchUpdate: (payload: YcwatchUpdate) =>
    request<YcwatchStatus>("/api/ycwatch/settings", {
      method: "PATCH",
      body: JSON.stringify(payload),
    }),

  fleetReportStatus: () => request<FleetReportStatus>("/api/fleet-report/status"),
  fleetReportUpdate: (payload: FleetReportUpdate) =>
    request<FleetReportStatus>("/api/fleet-report/settings", {
      method: "PATCH",
      body: JSON.stringify(payload),
    }),
  fleetReportPreview: (kind: "full" | "short") =>
    request<{ text: string }>(`/api/fleet-report/preview?kind=${kind}`, { method: "POST" }),
  fleetReportSend: () => request<FleetReportStatus>("/api/fleet-report/send", { method: "POST" }),

  aiAccessStatus: () => request<AiAccessStatus>("/api/ai-access/status"),
  /** Без ключей — весь парк; с ключами — только эти хосты (встают в очередь идущего прогона). */
  aiAccessRun: (hosts?: string[]) =>
    request<AiAccessStatus>("/api/ai-access/run", {
      method: "POST",
      ...(hosts ? { body: JSON.stringify({ hosts }) } : {}),
    }),
  /** Без ключей — отменить весь прогон; с ключами — снять эти хосты. */
  aiAccessCancel: (hosts?: string[]) =>
    request<AiAccessStatus>("/api/ai-access/cancel", {
      method: "POST",
      ...(hosts ? { body: JSON.stringify({ hosts }) } : {}),
    }),
  aiAccessUpdate: (payload: AiAccessUpdate) =>
    request<AiAccessStatus>("/api/ai-access/settings", {
      method: "PATCH",
      body: JSON.stringify(payload),
    }),
  aiAccessHistory: (key: string, days = 30) =>
    request<AiHostHistory>(
      `/api/ai-access/history?key=${encodeURIComponent(key)}&days=${days}`,
    ),

  nodeWatchStatus: () => request<NodeWatchStatus>("/api/node-watch/status"),
  nodeWatchEvents: (days = 30) =>
    request<NodeOutage[]>(`/api/node-watch/events?days=${days}`),
  nodeWatchUpdate: (payload: NodeWatchUpdate) =>
    request<NodeWatchStatus>("/api/node-watch/settings", {
      method: "PATCH",
      body: JSON.stringify(payload),
    }),
  nodeWatchTelegramTest: () =>
    request<{ sent: boolean }>("/api/node-watch/telegram/test", { method: "POST" }),

  memWatchStatus: () => request<MemWatchStatus>("/api/mem-watch/status"),
  memWatchUpdate: (payload: MemWatchUpdate) =>
    request<MemWatchStatus>("/api/mem-watch/settings", {
      method: "PATCH",
      body: JSON.stringify(payload),
    }),
  memWatchRestart: (nodeId: string) =>
    request<{ ok: boolean; message?: string }>(`/api/mem-watch/nodes/${nodeId}/restart`, {
      method: "POST",
      body: JSON.stringify({ confirm: true }),
    }),
  memWatchResetBlock: (nodeId: string) =>
    request<{ reset: boolean }>(`/api/mem-watch/nodes/${nodeId}/reset-block`, { method: "POST" }),

  panelBackupStatus: () => request<PanelBackupStatus>("/api/panel-backup/status"),
  panelBackupRun: () => request<PanelBackupStatus>("/api/panel-backup/run", { method: "POST" }),
  panelBackupUpdate: (payload: PanelBackupUpdate) =>
    request<PanelBackupStatus>("/api/panel-backup/settings", {
      method: "PATCH",
      body: JSON.stringify(payload),
    }),
  panelBackupFileUrl: (name: string) => `/api/panel-backup/files/${encodeURIComponent(name)}`,

  listApiTokens: () => request<ApiToken[]>("/api/settings/api-tokens"),
  createApiToken: (name: string) =>
    request<ApiTokenCreated>("/api/settings/api-tokens", {
      method: "POST",
      body: JSON.stringify({ name }),
    }),
  revokeApiToken: (id: string) =>
    request<void>(`/api/settings/api-tokens/${id}`, { method: "DELETE" }),
  resetNodeMetrics: (id: string) =>
    request<{ deleted: number }>(`/api/nodes/${id}/reset-metrics`, { method: "POST" }),
  runScriptStream: (
    id: string,
    body: RemnaScriptRunBody,
    opts: {
      onEvent: (event: ScriptStreamEvent) => void;
      signal?: AbortSignal;
    },
  ) =>
    streamNdjson(`/api/nodes/${id}/scripts/run`, {
      method: "POST",
      body: JSON.stringify(body),
      signal: opts.signal,
      onEvent: opts.onEvent,
    }),
    installWarpStream: (
    id: string,
    opts: {
      onEvent: (event: ScriptStreamEvent) => void;
      signal?: AbortSignal;
      force?: boolean;
    },
  ) =>
    streamNdjson(`/api/nodes/${id}/warp/install`, {
      method: "POST",
      body: JSON.stringify({ force: !!opts.force }),
      signal: opts.signal,
      onEvent: opts.onEvent,
    }),
  installCf204Stream: (
    id: string,
    opts: {
      onEvent: (event: ScriptStreamEvent) => void;
      signal?: AbortSignal;
      patch_profile?: boolean;
    },
  ) =>
    streamNdjson(`/api/nodes/${id}/probe-stub/install`, {
      method: "POST",
      body: JSON.stringify({ patch_profile: opts.patch_profile !== false }),
      signal: opts.signal,
      onEvent: opts.onEvent,
    }),
  getHaproxy: (id: string) => request<HaproxyStatus>(`/api/nodes/${id}/haproxy`),
  getHaproxyDiag: (id: string) => request<HaproxyDiag>(`/api/nodes/${id}/haproxy-diag`),
  getHaproxyStats: (id: string) => request<HaproxyLiveStats>(`/api/nodes/${id}/haproxy-stats`),
  getCapacity: (id: string) => request<VpsCapacity>(`/api/nodes/${id}/capacity`),
  haproxyPreview: (body: HaproxyPreviewBody) =>
    request<{ config: string } & HaproxyPreviewBody>("/api/nodes/haproxy-preview", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  runHaproxyStream: (
    id: string,
    opts: {
      action: HaproxyAction;
      force?: boolean;
      template?: HaproxyTemplate;
      bind_port?: number;
      backend?: string;
      path_prefix?: string;
      proxy_protocol?: boolean;
      routes?: HaproxyRoute[];
      config?: string;
      onEvent: (event: ScriptStreamEvent) => void;
      signal?: AbortSignal;
    },
  ) =>
    streamNdjson(`/api/nodes/${id}/haproxy`, {
      method: "POST",
      body: JSON.stringify({
        action: opts.action,
        force: !!opts.force,
        template: opts.template ?? "minimal",
        bind_port: opts.bind_port ?? 80,
        backend: opts.backend ?? "127.0.0.1:10087",
        path_prefix: opts.path_prefix ?? "/api/generate/",
        proxy_protocol: opts.proxy_protocol ?? true,
        routes: opts.routes ?? [],
        config: opts.config || null,
      }),
      signal: opts.signal,
      onEvent: opts.onEvent,
    }),
  destScanStream: (
    id: string,
    opts: {
      ru_only?: boolean;
      scan_subnet?: boolean | null;
      extra?: string[];
      limit?: number;
      onEvent: (event: DestScanEvent) => void;
      signal?: AbortSignal;
    },
  ) =>
    streamNdjson<DestScanEvent>(`/api/nodes/${id}/dest-scan`, {
      method: "POST",
      body: JSON.stringify({
        ru_only: opts.ru_only ?? true,
        scan_subnet: opts.scan_subnet ?? null,
        extra: opts.extra ?? [],
        limit: opts.limit ?? 45,
      }),
      signal: opts.signal,
      onEvent: opts.onEvent,
    }),
  destLoopbackStream: (
    id: string,
    opts: {
      dests: string[];
      port?: number;
      onEvent: (event: DestLoopbackEvent) => void;
      signal?: AbortSignal;
    },
  ) =>
    streamNdjson<DestLoopbackEvent>(`/api/nodes/${id}/dest-loopback`, {
      method: "POST",
      body: JSON.stringify({
        dests: opts.dests,
        port: opts.port ?? 18443,
      }),
      signal: opts.signal,
      onEvent: opts.onEvent,
    }),
};

export type RemnawaveVersions = {
  panel_version: string | null;
  node_version: string | null;
  panel_url: string | null;
  node_url: string | null;
  checked_at: number | null;
  error: string | null;
};

export type RemnaScriptAction = "install" | "reinstall" | "tune" | "update";
export type ScriptQueueAction = RemnaScriptAction | "warp" | "cf204";
export type HaproxyAction = "install" | "apply" | "reload" | "start" | "stop" | "uninstall";
export type HaproxyTemplate = "minimal" | "front-xhttp" | "tcp";

export type HaproxyRoute = {
  listen: number;
  backend: string;
};

export type HaproxyPreviewBody = {
  template: HaproxyTemplate;
  bind_port: number;
  backend: string;
  path_prefix: string;
  proxy_protocol: boolean;
  routes?: HaproxyRoute[];
};

export type VpsCapacity = {
  hostname: string | null;
  os: string | null;
  virt: string | null;
  cpu_cores: number;
  cpu_model: string | null;
  ram_total_mb: number;
  ram_avail_mb: number;
  disk_total_gb: number;
  disk_free_gb: number;
  loadavg: number[];
  haproxy: boolean;
  haproxy_up: boolean;
  docker: boolean;
  remnanode: boolean;
  tcp_estab: number;
  conntrack_max: number | null;
  conntrack_count: number | null;
  comfort: number;
  ceiling: number;
  panel_users: number;
  limiter: string;
  summary: string;
  notes: string[];
  error: string | null;
};

export type HaproxyParsed = {
  template: HaproxyTemplate | null;
  bind_port: number | null;
  backend: string | null;
  path_prefix: string | null;
  proxy_protocol: boolean;
  routes?: HaproxyRoute[];
};

export type HaproxyStatus = {
  installed: boolean;
  running: boolean;
  enabled: boolean;
  version: string | null;
  config: string | null;
  listen: string[];
  valid: boolean | null;
  error: string | null;
  parsed?: HaproxyParsed | null;
};

export type HaproxyDiag = {
  lines: string[];
  error: string | null;
};

export type HaproxyStatRow = {
  pxname: string;
  svname: string;
  scur: number | null;
  smax: number | null;
  stot: number | null;
  bin: number | null;
  bout: number | null;
  rate: number | null;
  rate_max: number | null;
  status: string;
  ereq: number | null;
  econ: number | null;
  eresp: number | null;
  wretr: number | null;
  wredis: number | null;
  lastsess: number | null;
};

export type HaproxySession = {
  raw: string;
  src: string | null;
  frontend: string | null;
  backend: string | null;
  age: string | null;
};

export type HaproxyHistoryPoint = {
  ts: number;
  curr_conns: number | null;
  conn_rate: number | null;
  bin: number | null;
  bout: number | null;
};

export type HaproxyLiveStats = {
  node_id: string | null;
  node_name: string | null;
  host: string | null;
  uptime: string | null;
  curr_conns: number | null;
  cum_conns: number | null;
  conn_rate: number | null;
  bin: number | null;
  bout: number | null;
  rows: HaproxyStatRow[];
  sessions: HaproxySession[];
  history: HaproxyHistoryPoint[];
  errors: string;
  error: string | null;
};

export type RemnaScriptDefaults = {
  node_port: number;
  additional_ports: string;
  mtu_ddos: boolean;
  gaming: boolean;
  shaper: boolean;
  swap: boolean;
  swap_size: string;
  cache_size: string;
  disable_ipv6: boolean;
  use_origin: boolean;
  origin_domain: string | null;
  skip_system_update: boolean;
  cf_204_stub: boolean;
};

export type AppSettings = {
  remna_secret_key: string;
  defaults: RemnaScriptDefaults;
};

export type AuditFinding = {
  severity: "crit" | "warn" | "info";
  target: string;
  title: string;
  detail: string;
};

export type AuditResult = {
  ran_at: string | null;
  duration_ms: number;
  error: string | null;
  checked: number;
  crit: number;
  warn: number;
  findings: AuditFinding[];
};

export type AuditCheck = {
  id: string;
  title: string;
  what: string;
  why: string;
  running: boolean;
  result: AuditResult | null;
};

export type AuditStatus = {
  checks: AuditCheck[];
  any_running: boolean;
  scheduler_enabled: boolean;
  cert_domains: string[];
};

export type ApiToken = {
  id: string;
  name: string;
  prefix: string;
  created_at: string;
  last_used_at: string | null;
};

// Полный токен приходит только в ответе на создание.
export type ApiTokenCreated = ApiToken & { token: string };

export type RemnaScriptRunBody = {
  action: RemnaScriptAction;
  node_port?: number;
  secret_key?: string | null;
  additional_ports?: string;
  mtu_ddos?: boolean;
  gaming?: boolean;
  shaper?: boolean;
  swap?: boolean;
  swap_size?: string;
  cache_size?: string;
  disable_ipv6?: boolean;
  use_origin?: boolean;
  origin_domain?: string | null;
  tune_mtu?: "on" | "off" | "skip";
  tune_gaming?: "on" | "off" | "skip";
  tune_swap?: "on" | "off" | "skip";
  tune_ports?: boolean;
  tune_ipv6?: "disable" | "enable" | "skip";
  tune_shaper?: "on" | "off" | "skip";
  skip_system_update?: boolean;
  cf_204_stub?: boolean;
};

export type ScriptQueueBody = RemnaScriptRunBody | { action: "warp"; force?: boolean } | { action: "cf204"; patch_profile?: boolean };

export type ScriptStreamEvent =
  | { type: "log"; line: string }
  | { type: "done"; ok: true; message: string }
  | { type: "error"; message: string };

export type DestCandidate = {
  host: string;
  ok: boolean;
  why: string;
  verdict?: string;
  near?: boolean;
  noisy?: boolean;
  tls13?: boolean;
  code?: string;
  http_version?: string;
  connect_ms?: number;
  tls_ms?: number;
  connect_med?: number | null;
  ip?: string;
  redirect?: string;
  cdn?: string;
  kex?: string;
  stalls?: number;
  fails?: number;
};

export type DestLoopResult = {
  host: string;
  ok: boolean;
  note: string;
};

export type DestScanEvent =
  | { type: "log"; line: string }
  | {
      type: "done";
      ok: true;
      message: string;
      own?: string;
      subnet?: string;
      alive443?: number;
      found?: number;
      checked: DestCandidate[];
      good: DestCandidate[];
      best: string | null;
      ru_only: boolean;
      country?: string | null;
    }
  | { type: "error"; message: string };

export type DestLoopbackEvent =
  | { type: "log"; line: string }
  | {
      type: "done";
      ok: true;
      message: string;
      results: DestLoopResult[];
      winners: DestLoopResult[];
      best: string | null;
    }
  | { type: "error"; message: string };

export type AgentInstallStreamEvent =
  | { type: "log"; line: string }
  | {
      type: "done";
      ok: true;
      message: string;
      agent_port: number;
      node: NodeItem;
    }
  | { type: "error"; message: string };

export type AgentsStreamEvent =
  | { type: "node"; id: string; status: AgentStatus }
  | {
      type: "done";
      latest_agent_version: string;
      latest_wgcf_version: string | null;
      xray_online: XrayOnlineMap;
    }
  | { type: "error"; message: string };

async function streamNdjson<T extends { type: string }>(
  path: string,
  opts: {
    method?: string;
    body?: string;
    signal?: AbortSignal;
    onEvent: (event: T) => void;
  },
): Promise<void> {
  const res = await fetch(path, {
    method: opts.method ?? "GET",
    credentials: "include",
    headers: {
      Accept: "application/x-ndjson",
      ...(opts.body ? { "Content-Type": "application/json" } : {}),
    },
    body: opts.body,
    signal: opts.signal,
  });

  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    const detail = typeof data.detail === "string" ? data.detail : "Ошибка запроса";
    throw new Error(detail);
  }
  if (!res.body) {
    throw new Error("Пустой ответ сервера");
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let sawTerminal = false;

  try {
    while (true) {
      if (opts.signal?.aborted) {
        await reader.cancel().catch(() => undefined);
        throw new DOMException("Aborted", "AbortError");
      }
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const parts = buffer.split("\n");
      buffer = parts.pop() ?? "";
      for (const part of parts) {
        const line = part.trim();
        if (!line) continue;
        let event: T;
        try {
          event = JSON.parse(line) as T;
        } catch {
          throw new Error("Некорректный поток ответа");
        }
        if (event.type === "done" || event.type === "error") {
          sawTerminal = true;
        }
        opts.onEvent(event);
      }
    }

    const tail = buffer.trim();
    if (tail) {
      const event = JSON.parse(tail) as T;
      if (event.type === "done" || event.type === "error") {
        sawTerminal = true;
      }
      opts.onEvent(event);
    }

    if (!sawTerminal) {
      if (opts.signal?.aborted) {
        throw new DOMException("Aborted", "AbortError");
      }
      throw new Error("Поток прервался без результата");
    }
  } catch (err) {
    if (opts.signal?.aborted) {
      await reader.cancel().catch(() => undefined);
      throw new DOMException("Aborted", "AbortError");
    }
    throw err;
  }
}

function toPayload(values: NodeFormValues, creating: boolean) {
  const payload: Record<string, unknown> = {
    name: values.name.trim(),
    host: values.host.trim(),
    ssh_port: Number(values.ssh_port) || 22,
    ssh_user: values.ssh_user.trim() || "root",
    auth_type: values.auth_type,
    hosting_id: values.hosting_id || null,
    country_code: values.country_code.trim() || null,
    notes: values.notes.trim() || null,
  };
  if (!creating && !values.hosting_id) {
    payload.clear_hosting = true;
  }

  // On update: omit empty secrets so stored password/key are never wiped.
  const password = values.password.trim();
  const privateKey = values.private_key.trim();

  if (values.auth_type === "password") {
    if (password) {
      payload.password = password;
    } else if (creating) {
      payload.password = "";
    }
  } else {
    if (privateKey) {
      payload.private_key = values.private_key;
      if (values.private_key_passphrase) {
        payload.private_key_passphrase = values.private_key_passphrase;
      }
    } else if (creating) {
      payload.private_key = "";
    }
    // Key auth may also keep a sudo password — update only if non-empty.
    if (password) {
      payload.password = password;
    }
  }

  return payload;
}

export type SubCheck = {
  checked_at: string;
  via: string;
  ok: boolean;
  http_status: number | null;
  duration_ms: number;
  hosts: number | null;
  cert_days: number | null;
  error: string | null;
};

export type SubwatchConfig = {
  enabled: boolean;
  interval_min: number;
  url: string;
  origin_url: string;
  min_hosts: number;
  notify: boolean;
  chat_id: string;
  bot_token_set: boolean;
  proxy_set: boolean;
  proxy_masked: string;
};

export type DetectedChat = {
  id: number;
  type: string;
  title: string | null;
  username: string | null;
};

export type SubwatchStatus = {
  config: SubwatchConfig;
  ok: boolean | null;
  since: string | null;
  fails: number;
  last: SubCheck | null;
  history: SubCheck[];
  uptime_24h: number | null;
};

export type SubwatchUpdate = Partial<
  Omit<SubwatchConfig, "bot_token_set" | "proxy_set" | "proxy_masked"> & {
    bot_token: string;
    telegram_proxy: string;
  }
>;

export type YcVm = {
  id: string | null;
  name: string | null;
  status: string;
  ip: string | null;
};

export type YcCheck = {
  checked_at: string;
  ok: boolean;
  balance: number | null;
  currency: string | null;
  account_active: boolean | null;
  vms: YcVm[];
  duration_ms: number;
  error: string | null;
};

export type YcwatchConfig = {
  enabled: boolean;
  interval_min: number;
  billing_account_id: string;
  folder_id: string;
  instance_id: string;
  warn_rub: number;
  remind_min: number;
  remind_low_hours: number;
  notify: boolean;
  chat_id: string;
  sa_key_set: boolean;
  /** Ключ в базе есть, но не расшифровывается — следилка ослепла. */
  sa_key_broken: boolean;
  sa_key_id: string | null;
  sa_id: string | null;
  bot_ready: boolean;
};

/** ok — всё хорошо, low — денег меньше порога, critical — ноль/минус или ВМ не RUNNING, blind — облако не отвечает. */
export type YcLevel = "ok" | "low" | "critical" | "blind";

export type YcwatchStatus = {
  config: YcwatchConfig;
  level: YcLevel | null;
  since: string | null;
  fails: number;
  reasons: string[];
  last: YcCheck | null;
  last_ok: YcCheck | null;
  burn_per_day: number | null;
  days_left: number | null;
  history: YcCheck[];
};

export type YcwatchUpdate = Partial<
  Omit<YcwatchConfig, "sa_key_set" | "sa_key_broken" | "sa_key_id" | "sa_id" | "bot_ready"> & {
    sa_key: string;
  }
>;

export type BackupFile = {
  name: string;
  size: number;
  created: string;
};

export type PanelBackupConfig = {
  enabled: boolean;
  interval_hours: number;
  host: string;
  port: number;
  user: string;
  keep_recent: number;
  keep_daily: number;
  notify: boolean;
  ssh_key_set: boolean;
};

export type PanelBackupStatus = {
  config: PanelBackupConfig;
  last_run_at: string | null;
  last_ok_at: string | null;
  last_error: string | null;
  last_size: number | null;
  last_duration_ms: number | null;
  files: BackupFile[];
  total_bytes: number;
  disk_free_bytes: number | null;
};

export type PanelBackupUpdate = Partial<
  Omit<PanelBackupConfig, "ssh_key_set"> & { ssh_key: string }
>;

export type NodeWatchConfig = {
  enabled: boolean;
  /** Сколько минут нода должна молчать подряд, чтобы это стало тревогой. */
  threshold_min: number;
  /** Напоминать о лежащей раз в столько часов; 0 — не напоминать. */
  remind_hours: number;
  notify: boolean;
  /** Пусто — тот же чат, что у следилки за подпиской. */
  chat_id: string;
  /** id нод, по которым не тревожим. */
  exclude: string[];
  skip_panel_disabled: boolean;
  /** Рагент до ноды не достаёт, но панель видит её подключённой — нода жива. */
  panel_alive: boolean;
  /** Часовой пояс времени в сообщениях, часы от UTC. */
  utc_offset: number;
  bot_ready: boolean;
};

/** Что именно молчит: null — такого сигнала нет (агент не стоит, панель не ответила). */
export type NodeSignals = {
  agent?: boolean | null;
  agent_error?: string;
  ssh?: boolean | null;
  panel?: boolean | null;
};

export type NodeDown = {
  node_id: string;
  name: string;
  host: string | null;
  hosting: string | null;
  since: string;
  /** false — молчит, но порог ещё не вышел: тревоги пока нет. */
  alerted: boolean;
  signals: NodeSignals;
};

/** Нода, до которой рагент не дотягивается: наблюдается через панель. */
export type NodeViaPanel = {
  node_id: string;
  name: string;
  host: string | null;
  hosting: string | null;
  since: string;
  /** false — панель сейчас про ноду молчит: жива ли она, неизвестно. */
  panel_seen: boolean;
};

export type NodeWatchStatus = {
  config: NodeWatchConfig;
  down: NodeDown[];
  via_panel: NodeViaPanel[];
  /** Рагент разом перестал видеть большинство нод. */
  fleet: { since: string; alerted: boolean; down: number; total: number } | null;
  /** Панель не отвечает рагенту. */
  panel_lost: { since: string; alerted: boolean } | null;
};

export type NodeWatchUpdate = Partial<Omit<NodeWatchConfig, "bot_ready">>;

export type NodeOutage = {
  id: string;
  kind: "node" | "fleet" | "panel";
  node_id: string | null;
  name: string;
  host: string | null;
  hosting: string | null;
  started_at: string;
  alerted_at: string;
  ended_at: string | null;
  resolution: "recovered" | "muted" | "removed" | "stopped" | null;
  notified: boolean;
  signals: NodeSignals & { down?: number; total?: number };
};

export type AiVerdict = "ok" | "blocked" | "challenge" | "timeout" | "error";

export type AiAccessConfig = {
  enabled: boolean;
  interval_hours: number;
  sub_url: string;
  concurrency: number;
  exclude: string;
  notify: boolean;
  chat_id: string;
  keep_days: number;
  gemini_key_set: boolean;
  sub_from_subwatch: boolean;
  sub_configured: boolean;
  bot_ready: boolean;
};

export type AiCell = {
  verdict: AiVerdict;
  note: string;
  tries: number;
  prev: AiVerdict | null;
};

/** Что с хостом в идущем прогоне. */
export type AiHostState =
  | "queued"
  | "running"
  | "cancelling"
  | "done"
  | "cancelled"
  | "missing"
  | "aborted";

export type AiHostRow = {
  host: string;
  host_key: string;
  /** null — хост стоит в очереди и ещё ни разу не проверялся. */
  checked_at: string | null;
  tunnel: "up" | "down" | "error" | null;
  exit_ip: string | null;
  exit_country: string | null;
  ai_ip: string | null;
  ai_country: string | null;
  ai_warp: string | null;
  prev_ai_country: string | null;
  duration_ms: number;
  error: string | null;
  results: Record<string, AiCell>;
  changed: boolean;
  is_new: boolean;
  /** Результат из выборочного прогона, а не из полного. */
  partial: boolean;
  /** Состояние в идущем прогоне; null — хост в нём не участвует. */
  state: AiHostState | null;
  /** Почему последний выборочный прогон не смог его проверить. */
  run_error: string | null;
};

export type AiRunHost = {
  key: string;
  host: string;
  state: AiHostState;
  error: string | null;
};

export type AiChange = {
  host: string;
  host_key: string;
  service: string;
  before: string;
  after: string;
};

export type AiRun = {
  id: string;
  started_at: string;
  finished_at: string | null;
  status: "running" | "done" | "failed" | "aborted" | "cancelled";
  trigger: "schedule" | "manual";
  scope: "full" | "partial";
  hosts_total: number | null;
  hosts_done: number;
  error: string | null;
  changes: AiChange[];
  hosts: AiRunHost[];
  /** Прогон отменён и сворачивается. */
  stopping: boolean;
};

/** Колонка матрицы. group — ai | apps: ИИ-сервисы отдельно от прочих приложений. */
export type AiService = {
  key: string;
  name: string;
  group: string;
  group_name: string;
};

export type AiAccessStatus = {
  config: AiAccessConfig;
  services: AiService[];
  /** Идущий прогон, полный или выборочный. Состояние живёт на сервере. */
  running: AiRun | null;
  /** Последний полный прогон любого исхода. */
  last: AiRun | null;
  /** Опора матрицы: последний удачный полный прогон. */
  shown: AiRun | null;
  prev: AiRun | null;
  /** Последний законченный выборочный прогон. */
  last_partial: AiRun | null;
  /** Полный прогон ждёт конца выборочного. */
  pending_full: "schedule" | "manual" | null;
  rows: AiHostRow[];
  next_run_at: string | null;
};

export type AiAccessUpdate = Partial<
  Omit<AiAccessConfig, "gemini_key_set" | "sub_from_subwatch" | "sub_configured" | "bot_ready"> & {
    gemini_api_key: string;
  }
>;

export type AiHistoryPoint = {
  run_id: string;
  checked_at: string;
  host: string;
  tunnel: string;
  exit_ip: string | null;
  exit_country: string | null;
  ai_ip: string | null;
  ai_country: string | null;
  ai_warp: string | null;
  error: string | null;
  results: Record<string, AiCell>;
};

export type AiHostHistory = {
  host_key: string;
  days: number;
  points: AiHistoryPoint[];
};


export type MemWatchConfig = {
  enabled: boolean;
  dry_run: boolean;
  auto_restart: boolean;
  notify: boolean;
  chat_id: string;
  rss_min_mb: number;
  rss_pct: number;
  avail_pct: number;
  swap_pct: number;
  critical_avail_pct: number;
  oom_window_min: number;
  confirm_ticks: number;
  clear_ticks: number;
  remind_hours: number;
  cooldown_h: number;
  min_core_uptime_h: number;
  max_per_node_24h: number;
  max_per_node_7d: number;
  fleet_gap_min: number;
  fleet_per_hour: number;
  window_start: number;
  window_end: number;
  utc_offset: number;
  post_check_after_s: number;
  post_check_deadline_s: number;
  relapse_h: number;
  exclude: string[];
  exclude_contains: string[];
  bot_ready: boolean;
};

export type MemWatchNode = {
  node_id: string;
  name: string;
  host: string;
  checked_at: string | null;
  rss_mb: number | null;
  rss_source: string | null;
  rss_limit_mb: number | null;
  mem_total_mb: number | null;
  avail_pct: number | null;
  swap_percent: number | null;
  core_uptime_s: number | null;
  kinds: string[];
  reasons: string[];
  alarm: boolean;
  critical: boolean;
  alarm_since: string | null;
  action: string;
  action_reason: string;
  blocked: string | null;
  pending: boolean;
  last_restart_at: string | null;
  excluded: boolean;
};

export type MemWatchLogRow = {
  ts: string;
  kind: string;
  node_id?: string | null;
  name?: string | null;
  text: string;
  dry_run: boolean;
  notified: boolean;
};

export type MemWatchStatus = {
  config: MemWatchConfig;
  nodes: MemWatchNode[];
  log: MemWatchLogRow[];
  alarms: number;
};

export type MemWatchUpdate = Partial<Omit<MemWatchConfig, "bot_ready">>;

export type FleetReportConfig = {
  enabled: boolean;
  commands: boolean;
  chat_id: string;
  full_hours: string;
  short_hours: string;
  utc_offset: number;
  window_days: number;
  mem_red_p95: number;
  red_days: number;
  mem_orange_p95: number;
  orange_days: number;
  trend_pp: number;
  chan_pct: number;
  chan_days: number;
  save_mem_pct: number;
  save_cpu_pct: number;
  save_min_gb: number;
  headroom_pct: number;
};

export type FleetReportStatus = {
  config: FleetReportConfig;
  bot_ready: boolean;
  last_sent_at: string | null;
  last_kind: string | null;
  next_report: string | null;
};

export type FleetReportUpdate = Partial<FleetReportConfig>;
