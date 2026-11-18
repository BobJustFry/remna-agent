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
  monitoring_enabled: boolean;
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
  proxy_top?: { ip: string; conns: number }[];
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
  /** Своп и пинг/потери до РФ прямо сейчас. Нет на агентах < 0.1.21. */
  swap_total_mb?: number | null;
  swap_percent?: number | null;
  ru_ping_ms?: number | null;
  ru_loss_pct?: number | null;
  /** Память для памятьсторожа. Нет на агентах < 0.1.23. */
  mem_total_mb?: number | null;
  mem_available_mb?: number | null;
  xray_rss_mb?: number | null;
  xray_age_sec?: number | null;
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
  /**
   * Суммы дельт TCP за шаг. С агента 0.1.22 retr_pct = (retrans − syn) / orig_data · 100,
   * на старых — retrans / out · 100. Null на агентах < 0.1.21.
   */
  tcp_out_segs?: number | null;
  tcp_retrans_segs?: number | null;
  /** TcpExt TCPSynRetrans / TCPOrigDataSent за шаг. Null на агентах < 0.1.22. */
  tcp_syn_retrans?: number | null;
  tcp_orig_data_sent?: number | null;
  /** Числитель новой формулы за шаг: ретрансмиты без SYN, только по отсчётам с новыми полями. */
  tcp_retrans_no_syn?: number | null;
  retr_pct?: number | null;
  /** Сегментов с данными за шаг меньше порога: цифру показываем серой, «мало трафика». */
  retr_low_volume?: boolean | null;
  /** Сколько процессов убил OOM-killer за шаг. */
  oom_kills?: number | null;
  swap_percent?: number | null;
  /** Пинг и потери до якорей RIPE Atlas в Москве, с самой ноды. */
  ru_ping_ms?: number | null;
  ru_loss_pct?: number | null;
  /** RSS процесса ядра (rw-core), МБ. Агент 0.1.23+. */
  xray_rss_mb?: number | null;
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

export type SharingPart = {
  key: string;
  points: number;
  why: string;
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
  score: number;
  level: "green" | "amber" | "red";
  device_limit: number;
  unlimited: boolean;
  parts: SharingPart[];
  countries: string[];
  platforms: string[];
  hwid_active: number | null;
  hwid_total: number | null;
  night_busy_pct: number | null;
  traffic_ratio: number | null;
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
  suspects: SharingUserHit[];
  by_agent_id: Record<string, SharingUserHit[]>;
  peers_by_agent_id?: Record<string, SharingNodePeers>;
  thresholds: Record<string, number>;
};

export type SharingDossier = {
  filename: string;
  text: string;
};

export type SharingConfig = {
  enabled: boolean;
  red: number;
  amber: number;
  red_streak: number;
  green_streak: number;
  fallback_device_limit: number;
  unlimited_as: number;
  ip_per_device: number;
  ip_slack_5m: number;
  ip_slack_15m: number;
  conc_ip_per_device: number;
  conc_ip_slack: number;
  net_per_device: number;
  net_slack: number;
  w_conc_ips: number;
  w_conc_nets: number;
  w_ips_5m: number;
  w_ips_15m: number;
  w_platforms: number;
  w_travel: number;
  w_hwid: number;
  w_night: number;
  w_traffic: number;
  traffic_median_mult: number;
  night_window_h: number;
  night_min_days: number;
  night_quiet_pct: number;
  night_full_pct: number;
  night_keep_days: number;
  hwid_active_min: number;
  users_refresh_min: number;
  max_candidates: number;
  notify: boolean;
  chat_id: string;
  remind_hours: number;
  auto_ban: boolean;
  auto_ban_score: number;
  auto_ban_scans: number;
  ban_exclude_ids: number[];
  bot_ready: boolean;
};

export type SharingBanRow = {
  ts: string;
  action: string;
  source: string;
  user_id: number;
  username: string;
  score: number;
  ok: boolean;
  skipped?: string | null;
  error?: string | null;
  warn?: string | null;
};

export type SharingBans = { banned: SharingBanRow[]; log: SharingBanRow[] };

export type SharingBanResult = {
  ok: boolean;
  skipped?: string | null;
  error?: string | null;
  warn?: string | null;
  steps: string[];
};

export type SharingConfigUpdate = Partial<Omit<SharingConfig, "bot_ready" | "fallback_device_limit">>;
