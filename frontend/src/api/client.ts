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
  SharingDossier,
  SharingStatus,
  SshCheckResult,
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
  createHosting: (payload: { name: string; website_url?: string | null; notes?: string | null }) =>
    request<HostingItem>("/api/hostings", {
      method: "POST",
      body: JSON.stringify({
        name: payload.name,
        website_url: payload.website_url ?? null,
        notes: payload.notes ?? null,
      }),
    }),
  updateHosting: (
    id: string,
    payload: { name?: string; website_url?: string | null; notes?: string | null },
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

export type RemnaScriptRunBody = {
  action: RemnaScriptAction;
  node_port?: number;
  secret_key?: string | null;
  additional_ports?: string;
  mtu_ddos?: boolean;
  gaming?: boolean;
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
