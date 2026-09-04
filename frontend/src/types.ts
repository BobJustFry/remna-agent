export type AuthType = "password" | "private_key";

export type HostingItem = {
  id: string;
  name: string;
  website_url: string | null;
  favicon_data: string | null;
  notes: string | null;
  /** Channel the hosting sells, Mbit/s. Null — unknown, no gauge is drawn. */
  bandwidth_mbps: number | null;
  created_at: string;
  updated_at: string;
};

export type NodeItem = {
  id: string;
  name: string;
  host: string;
  ssh_port: number;
  ssh_user: string;
  auth_type: AuthType;
  has_password: boolean;
  has_private_key: boolean;
  hosting_id: string | null;
  hosting_name: string | null;
  hosting_website_url: string | null;
  hosting_favicon_data: string | null;
  country_code: string | null;
  notes: string | null;
  agent_configured: boolean;
  agent_port: number;
  created_at: string;
  updated_at: string;
};

export type AgentStatus = {
  present: boolean;
  configured: boolean;
  version: string | null;
  remnanode_version: string | null;
  remnanode_running: boolean | null;
  warp_present: boolean | null;
  warp_up: boolean | null;
  warp_healthy: boolean | null;
  warp_handshake_sec: number | null;
  warp_egress_ok: boolean | null;
  warp_interface: string | null;
  warp_method: string | null;
  warp_version: string | null;
  warp_ipv4: string | null;
  haproxy_present: boolean | null;
  haproxy_up: boolean | null;
  haproxy_version: string | null;
  haproxy_listen: string | null;
  proxy_peers: number | null;
  proxy_conns: number | null;
  cpu_percent: number | null;
  mem_percent: number | null;
  disk_percent: number | null;
  loadavg: number[] | null;
  cf204_ok: boolean | null;
  cf204_ms: number | null;
  /** Concurrent tunnels the box carries on its current config. Null on agents < 0.1.16. */
  capacity_comfort: number | null;
  capacity_ceiling: number | null;
  capacity_limiter: string | null;
  /** Throughput on the default-route interface, bits/s. Null on agents < 0.1.17. */
  net_rx_bps: number | null;
  net_tx_bps: number | null;
  net_iface: string | null;
  net_link_mbps: number | null;
  /** Channel limit of this node's hosting, Mbit/s. */
  hosting_bandwidth_mbps: number | null;
  /** Users the Xray core reports online, delivered with this node's own event. */
  xray_online: number | null;
  /** "xray" — RemnaNode on the box, "proxy" — HAProxy only, "unknown" — no agent. */
  kind: NodeKind;
  error: string | null;
};

export type NodeKind = "xray" | "proxy" | "unknown";

export type AgentMap = Record<string, AgentStatus>;

/** Users the Xray core reports online, per node id. */
export type XrayOnlineMap = Record<string, number>;

export type NodeFormValues = {
  name: string;
  host: string;
  ssh_port: number;
  ssh_user: string;
  auth_type: AuthType;
  password: string;
  private_key: string;
  private_key_passphrase: string;
  hosting_id: string;
  country_code: string;
  notes: string;
};

export type OnlineStatus = {
  online: boolean;
  latency_ms: number | null;
  method: string | null;
};

export type OnlineMap = Record<string, OnlineStatus>;

export type MetricsRange = "day" | "week" | "month" | "all";

export type MetricPoint = {
  t: number;
  ping_ms: number | null;
  cpu_percent: number | null;
  mem_percent: number | null;
  disk_percent: number | null;
  net_rx_bps: number | null;
  net_tx_bps: number | null;
  users_online: number | null;
  capacity: number | null;
};

export type NodesMetricsResponse = {
  range: MetricsRange;
  step_sec: number;
  from_ts: number;
  to_ts: number;
  series: Record<string, MetricPoint[]>;
};

export type SshCheckResult = {
  ok: boolean;
  message: string;
};

export type SharingUserHit = {
  user_id: number;
  username: string;
  ips_5m: number;
  ips_15m: number;
  s16_5m: number;
  conc_ips: number;
  conc_nets: number;
  own_ips: number;
  ips_on_node: number;
  reasons: string[];
  rw_nodes: string[];
};

export type SharingNodePeers = {
  ips: number;
  users: number;
};

export type SharingStatus = {
  scanned_at: string | null;
  error: string | null;
  scanning: boolean;
  online_users: number;
  flagged: number;
  by_agent_id: Record<string, SharingUserHit[]>;
  peers_by_agent_id?: Record<string, SharingNodePeers>;
  thresholds: Record<string, number>;
};

export type SharingDossier = {
  filename: string;
  text: string;
};
