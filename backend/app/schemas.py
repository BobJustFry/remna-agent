import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


AuthTypeLiteral = Literal["password", "private_key"]


class LoginRequest(BaseModel):
    # OWASP: cap password length to avoid hashing/compare DoS on huge bodies
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=256)


class UserOut(BaseModel):
    username: str


class HostingCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    website_url: str | None = Field(default=None, max_length=512)
    notes: str | None = None
    # Channel the hosting sells, Mbit/s. Null — unknown, no gauge is drawn.
    bandwidth_mbps: int | None = Field(default=None, ge=1, le=1_000_000)


class HostingUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    website_url: str | None = Field(default=None, max_length=512)
    notes: str | None = None
    # Channel the hosting sells, Mbit/s. Null — unknown, no gauge is drawn.
    bandwidth_mbps: int | None = Field(default=None, ge=1, le=1_000_000)


class HostingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    website_url: str | None
    favicon_data: str | None
    notes: str | None
    bandwidth_mbps: int | None
    created_at: datetime
    updated_at: datetime


class NodeCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    host: str = Field(min_length=1, max_length=255)
    ssh_port: int = Field(default=22, ge=1, le=65535)
    ssh_user: str = Field(default="root", min_length=1, max_length=128)
    auth_type: AuthTypeLiteral
    password: str | None = None
    private_key: str | None = None
    private_key_passphrase: str | None = None
    hosting_id: uuid.UUID | None = None
    country_code: str | None = Field(default=None, max_length=8)
    notes: str | None = None


class NodeUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    host: str | None = Field(default=None, min_length=1, max_length=255)
    ssh_port: int | None = Field(default=None, ge=1, le=65535)
    ssh_user: str | None = Field(default=None, min_length=1, max_length=128)
    auth_type: AuthTypeLiteral | None = None
    password: str | None = None
    private_key: str | None = None
    private_key_passphrase: str | None = None
    clear_password: bool = False
    clear_private_key: bool = False
    hosting_id: uuid.UUID | None = None
    clear_hosting: bool = False
    country_code: str | None = Field(default=None, max_length=8)
    notes: str | None = None
    monitoring_enabled: bool | None = None


class NodeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    host: str
    ssh_port: int
    ssh_user: str
    auth_type: AuthTypeLiteral
    has_password: bool
    has_private_key: bool
    hosting_id: uuid.UUID | None
    hosting_name: str | None
    hosting_website_url: str | None
    hosting_favicon_data: str | None
    country_code: str | None
    notes: str | None
    agent_configured: bool
    agent_port: int
    monitoring_enabled: bool = True
    created_at: datetime
    updated_at: datetime


class AgentNodeStatus(BaseModel):
    present: bool
    configured: bool
    version: str | None = None
    remnanode_version: str | None = None
    remnanode_running: bool | None = None
    warp_present: bool | None = None
    warp_up: bool | None = None
    warp_healthy: bool | None = None
    warp_handshake_sec: int | None = None
    warp_egress_ok: bool | None = None
    warp_interface: str | None = None
    warp_method: str | None = None
    warp_version: str | None = None
    warp_ipv4: str | None = None
    haproxy_present: bool | None = None
    haproxy_up: bool | None = None
    haproxy_version: str | None = None
    haproxy_listen: str | None = None
    proxy_peers: int | None = None
    proxy_top: list[dict] = Field(default_factory=list)
    proxy_conns: int | None = None
    cpu_percent: float | None = None
    mem_percent: float | None = None
    disk_percent: float | None = None
    loadavg: list[float] | None = None
    cf204_ok: bool | None = None
    cf204_ms: float | None = None
    capacity_comfort: int | None = None
    capacity_ceiling: int | None = None
    capacity_limiter: str | None = None
    net_rx_bps: int | None = None
    net_tx_bps: int | None = None
    net_iface: str | None = None
    net_link_mbps: int | None = None
    # Своп и пинг/потери до РФ прямо сейчас. Null на агентах старше 0.1.21.
    swap_total_mb: float | None = None
    swap_percent: float | None = None
    ru_ping_ms: float | None = None
    ru_loss_pct: float | None = None
    # Память для памятьсторожа (агент 0.1.23).
    mem_total_mb: float | None = None
    mem_available_mb: float | None = None
    xray_rss_mb: float | None = None
    xray_age_sec: int | None = None
    # Channel limit of the node's hosting, Mbit/s — null when the hosting has none set.
    hosting_bandwidth_mbps: int | None = None
    # Users the Xray core reports online. Rides with the node event so it does not
    # depend on the stream's final message arriving.
    xray_online: int | None = None
    # "xray" — RemnaNode on the box; "proxy" — HAProxy only; "unknown" — no agent yet.
    kind: str = "unknown"
    error: str | None = None


class NodesAgentResponse(BaseModel):
    statuses: dict[str, AgentNodeStatus]
    latest_agent_version: str = "0.0.0"
    latest_wgcf_version: str | None = None
    # Users the Xray core reports online, per node id. Sourced from the Remnawave
    # panel, which reads it from the core via RemnaNode — the nodes expose no
    # gRPC stats API of their own.
    xray_online: dict[str, int] = Field(default_factory=dict)


class RemnawaveVersionsOut(BaseModel):
    panel_version: str | None = None
    node_version: str | None = None
    panel_url: str | None = None
    node_url: str | None = None
    checked_at: float | None = None
    error: str | None = None


class AgentInstallOut(BaseModel):
    ok: bool
    message: str
    agent_port: int
    node: NodeOut


class NodeSecretOut(BaseModel):
    auth_type: AuthTypeLiteral
    secret: str


class NodeOnlineStatus(BaseModel):
    online: bool
    latency_ms: float | None = None
    method: str | None = None


class NodesOnlineResponse(BaseModel):
    statuses: dict[str, NodeOnlineStatus]


class MetricPointOut(BaseModel):
    t: float
    ping_ms: float | None = None
    cpu_percent: float | None = None
    mem_percent: float | None = None
    disk_percent: float | None = None
    net_rx_bps: int | None = None
    net_tx_bps: int | None = None
    users_online: float | None = None
    capacity: int | None = None
    # Сегменты и ретрансмиты — суммы дельт за шаг. С агента 0.1.22
    # retr_pct = (retrans − syn_retrans) / orig_data_sent * 100, на старых —
    # retrans / out * 100.
    tcp_out_segs: int | None = None
    tcp_retrans_segs: int | None = None
    tcp_syn_retrans: int | None = None
    tcp_orig_data_sent: int | None = None
    # Числитель новой формулы за шаг (ретрансмиты без SYN, только по отсчётам с
    # новыми полями) — чтобы фронт честно сложил долю за весь период.
    tcp_retrans_no_syn: int | None = None
    retr_pct: float | None = None
    # Сегментов с данными за шаг меньше порога — долю показываем, но не тревожим.
    # Null — старая формула, объём по ней не оценивается.
    retr_low_volume: bool | None = None
    # Сколько процессов убил OOM-killer за шаг.
    oom_kills: int | None = None
    swap_percent: float | None = None
    ru_ping_ms: float | None = None
    ru_loss_pct: float | None = None
    # RSS процесса ядра, МБ (макс. за шаг). Агент 0.1.23+.
    xray_rss_mb: float | None = None


class NodesMetricsResponse(BaseModel):
    range: str
    step_sec: int
    from_ts: float
    to_ts: float
    series: dict[str, list[MetricPointOut]]


class SshCheckOut(BaseModel):
    ok: bool
    message: str


class NodeRebootOut(BaseModel):
    ok: bool
    message: str


class RemnaScriptDefaults(BaseModel):
    node_port: int = Field(default=2222, ge=1, le=65535)
    additional_ports: str = ""
    mtu_ddos: bool = True
    gaming: bool = True
    swap: bool = True
    swap_size: str = Field(default="1G", max_length=16)
    cache_size: str = Field(default="1G", max_length=16)
    disable_ipv6: bool = True
    use_origin: bool = False
    origin_domain: str | None = Field(default=None, max_length=255)
    skip_system_update: bool = True
    cf_204_stub: bool = False
    shaper: bool = True


class AppSettingsOut(BaseModel):
    remna_secret_key: str = ""
    defaults: RemnaScriptDefaults = Field(default_factory=RemnaScriptDefaults)


class AppSettingsUpdate(BaseModel):
    remna_secret_key: str | None = None
    clear_remna_secret_key: bool = False
    defaults: RemnaScriptDefaults | None = None


class AuditFindingOut(BaseModel):
    severity: str
    target: str
    title: str
    detail: str = ""


class AuditResultOut(BaseModel):
    ran_at: str | None = None
    duration_ms: int = 0
    error: str | None = None
    checked: int = 0
    crit: int = 0
    warn: int = 0
    findings: list[AuditFindingOut] = Field(default_factory=list)


class AuditCheckOut(BaseModel):
    id: str
    title: str
    what: str
    why: str
    running: bool = False
    result: AuditResultOut | None = None


class AuditStatusOut(BaseModel):
    checks: list[AuditCheckOut] = Field(default_factory=list)
    any_running: bool = False
    # Фоновое расписание пока не заведено — раздел работает только по кнопке.
    scheduler_enabled: bool = False
    cert_domains: list[str] = Field(default_factory=list)


class AuditSettingsOut(BaseModel):
    cert_domains: list[str] = Field(default_factory=list)


class AuditSettingsUpdate(BaseModel):
    cert_domains: list[str] | None = Field(default=None, max_length=64)


class ApiTokenCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64)


class ApiTokenOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    prefix: str
    created_at: datetime
    last_used_at: datetime | None = None


class ApiTokenCreated(ApiTokenOut):
    # Полный токен — только в ответе на создание, больше нигде.
    token: str


class RemnaScriptRunRequest(BaseModel):
    action: Literal["install", "reinstall", "tune", "update"]
    node_port: int = Field(default=2222, ge=1, le=65535)
    secret_key: str | None = None
    additional_ports: str = ""
    mtu_ddos: bool = True
    gaming: bool = True
    swap: bool = True
    swap_size: str = Field(default="1G", max_length=16)
    cache_size: str = Field(default="1G", max_length=16)
    disable_ipv6: bool = True
    use_origin: bool = False
    origin_domain: str | None = Field(default=None, max_length=255)
    tune_mtu: Literal["on", "off", "skip"] = "skip"
    tune_gaming: Literal["on", "off", "skip"] = "skip"
    tune_swap: Literal["on", "off", "skip"] = "skip"
    tune_ports: bool = False
    tune_ipv6: Literal["disable", "enable", "skip"] = "skip"
    tune_shaper: Literal["on", "off", "skip"] = "skip"
    skip_system_update: bool = True
    cf_204_stub: bool = False
    shaper: bool = True


class ProbeStubInstallRequest(BaseModel):
    patch_profile: bool = True


class WarpInstallRequest(BaseModel):
    force: bool = False


class DestScanRequest(BaseModel):
    ru_only: bool = True
    scan_subnet: bool | None = None
    extra: list[str] = Field(default_factory=list, max_length=80)
    limit: int = Field(default=45, ge=5, le=80)


class DestLoopbackRequest(BaseModel):
    dests: list[str] = Field(min_length=1, max_length=12)
    port: int = Field(default=18443, ge=1024, le=65535)


HaproxyActionLiteral = Literal["install", "apply", "reload", "start", "stop", "uninstall"]
HaproxyTemplateLiteral = Literal["minimal", "front-xhttp", "tcp"]


class HaproxyRouteIn(BaseModel):
    listen: int = Field(ge=1, le=65535)
    backend: str = Field(max_length=255)


class HaproxyRunRequest(BaseModel):
    action: HaproxyActionLiteral = "install"
    force: bool = False
    template: HaproxyTemplateLiteral = "minimal"
    bind_port: int = Field(default=80, ge=1, le=65535)
    backend: str = Field(default="127.0.0.1:10087", max_length=255)
    path_prefix: str = Field(default="/api/generate/", max_length=255)
    proxy_protocol: bool = True
    routes: list[HaproxyRouteIn] = Field(default_factory=list, max_length=32)
    config: str | None = Field(default=None, max_length=256 * 1024)


class HaproxyPreviewRequest(BaseModel):
    template: HaproxyTemplateLiteral = "minimal"
    bind_port: int = Field(default=80, ge=1, le=65535)
    backend: str = Field(default="127.0.0.1:10087", max_length=255)
    path_prefix: str = Field(default="/api/generate/", max_length=255)
    proxy_protocol: bool = True
    routes: list[HaproxyRouteIn] = Field(default_factory=list, max_length=32)


class HaproxyPreviewOut(BaseModel):
    config: str
    template: HaproxyTemplateLiteral
    bind_port: int
    backend: str
    path_prefix: str
    proxy_protocol: bool
    routes: list[HaproxyRouteIn] = Field(default_factory=list)


class HaproxyParsedOut(BaseModel):
    template: HaproxyTemplateLiteral | None = None
    bind_port: int | None = None
    backend: str | None = None
    path_prefix: str | None = None
    proxy_protocol: bool = False
    routes: list[HaproxyRouteIn] = Field(default_factory=list)


class HaproxyStatusOut(BaseModel):
    installed: bool
    running: bool
    enabled: bool
    version: str | None = None
    config: str | None = None
    listen: list[str] = Field(default_factory=list)
    valid: bool | None = None
    error: str | None = None
    parsed: HaproxyParsedOut | None = None


class HaproxyDiagOut(BaseModel):
    lines: list[str] = Field(default_factory=list)
    error: str | None = None


class HaproxyStatRowOut(BaseModel):
    pxname: str
    svname: str
    scur: int | None = None
    smax: int | None = None
    stot: int | None = None
    bin: int | None = None
    bout: int | None = None
    rate: int | None = None
    rate_max: int | None = None
    status: str = ""
    ereq: int | None = None
    econ: int | None = None
    eresp: int | None = None
    wretr: int | None = None
    wredis: int | None = None
    lastsess: int | None = None


class HaproxySessionOut(BaseModel):
    raw: str
    src: str | None = None
    frontend: str | None = None
    backend: str | None = None
    age: str | None = None


class HaproxyHistoryPointOut(BaseModel):
    ts: float
    curr_conns: int | None = None
    conn_rate: int | None = None
    bin: int | None = None
    bout: int | None = None


class HaproxyLiveStatsOut(BaseModel):
    node_id: str | None = None
    node_name: str | None = None
    host: str | None = None
    uptime: str | None = None
    curr_conns: int | None = None
    cum_conns: int | None = None
    conn_rate: int | None = None
    bin: int | None = None
    bout: int | None = None
    rows: list[HaproxyStatRowOut] = Field(default_factory=list)
    sessions: list[HaproxySessionOut] = Field(default_factory=list)
    history: list[HaproxyHistoryPointOut] = Field(default_factory=list)
    errors: str = ""
    error: str | None = None


class VpsCapacityOut(BaseModel):
    hostname: str | None = None
    os: str | None = None
    virt: str | None = None
    cpu_cores: int = 1
    cpu_model: str | None = None
    ram_total_mb: int = 0
    ram_avail_mb: int = 0
    disk_total_gb: float = 0.0
    disk_free_gb: float = 0.0
    loadavg: list[float] = Field(default_factory=list)
    haproxy: bool = False
    haproxy_up: bool = False
    docker: bool = False
    remnanode: bool = False
    tcp_estab: int = 0
    conntrack_max: int | None = None
    conntrack_count: int | None = None
    comfort: int = 0
    ceiling: int = 0
    panel_users: int = 0
    limiter: str = "RAM"
    summary: str = ""
    notes: list[str] = Field(default_factory=list)
    error: str | None = None


class SubProbeOut(BaseModel):
    ok: bool
    via: str
    http_status: int | None = None
    duration_ms: int = 0
    hosts: int | None = None
    cert_days: int | None = None
    error: str | None = None


class SubCheckOut(BaseModel):
    checked_at: datetime
    via: str
    ok: bool
    http_status: int | None = None
    duration_ms: int = 0
    hosts: int | None = None
    cert_days: int | None = None
    error: str | None = None


class SubwatchConfigOut(BaseModel):
    enabled: bool = False
    interval_min: int = 15
    url: str = ""
    origin_url: str = ""
    min_hosts: int = 3
    notify: bool = True
    chat_id: str = ""
    bot_token_set: bool = False
    proxy_set: bool = False
    proxy_masked: str = ""            # схема://host:port, без логина и пароля


class SubwatchConfigUpdate(BaseModel):
    enabled: bool | None = None
    interval_min: int | None = Field(default=None, ge=1, le=1440)
    url: str | None = None
    origin_url: str | None = None
    min_hosts: int | None = Field(default=None, ge=1, le=1000)
    notify: bool | None = None
    chat_id: str | None = None
    bot_token: str | None = None      # пустая строка стирает токен
    telegram_proxy: str | None = None  # пустая строка стирает прокси


class TelegramProbeIn(BaseModel):
    """Необязательное тело проверок Telegram: прокси из поля формы, без сохранения.

    Нет поля — берётся сохранённый прокси; пустая строка — проверка напрямую.
    """
    proxy: str | None = None


class SubwatchStatusOut(BaseModel):
    config: SubwatchConfigOut
    ok: bool | None = None
    since: datetime | None = None
    fails: int = 0
    last: SubCheckOut | None = None
    history: list[SubCheckOut] = Field(default_factory=list)
    uptime_24h: float | None = None


class YcVmOut(BaseModel):
    id: str | None = None
    name: str | None = None
    status: str = "UNKNOWN"
    ip: str | None = None


class YcCheckOut(BaseModel):
    checked_at: datetime
    ok: bool
    balance: float | None = None
    currency: str | None = None
    account_active: bool | None = None
    vms: list[YcVmOut] = Field(default_factory=list)
    duration_ms: int = 0
    error: str | None = None


class YcwatchConfigOut(BaseModel):
    enabled: bool = True
    interval_min: int = 60
    billing_account_id: str = ""
    folder_id: str = ""
    instance_id: str = ""
    warn_rub: float = 300
    remind_min: int = 60
    remind_low_hours: int = 24
    notify: bool = True
    chat_id: str = ""
    sa_key_set: bool = False
    sa_key_broken: bool = False   # ключ в базе есть, но не расшифровывается
    # Не секрет: id ключа и СА видны в консоли — по ним понятно, какой ключ стоит.
    sa_key_id: str | None = None
    sa_id: str | None = None
    bot_ready: bool = False


class YcwatchConfigUpdate(BaseModel):
    enabled: bool | None = None
    interval_min: int | None = Field(default=None, ge=5, le=1440)
    billing_account_id: str | None = Field(default=None, max_length=64)
    folder_id: str | None = Field(default=None, max_length=64)
    instance_id: str | None = Field(default=None, max_length=64)
    warn_rub: float | None = Field(default=None, ge=0, le=1_000_000)
    remind_min: int | None = Field(default=None, ge=0, le=1440)          # 0 — не напоминать
    remind_low_hours: int | None = Field(default=None, ge=0, le=168)     # 0 — не напоминать
    notify: bool | None = None
    chat_id: str | None = None
    sa_key: str | None = Field(default=None, max_length=20_000)  # authorized key JSON; "" стирает


class YcwatchStatusOut(BaseModel):
    config: YcwatchConfigOut
    level: str | None = None          # ok | low | critical | blind
    since: datetime | None = None
    fails: int = 0
    reasons: list[str] = Field(default_factory=list)
    last: YcCheckOut | None = None    # последний опрос, удачный или нет
    last_ok: YcCheckOut | None = None  # последний, где облако ответило
    burn_per_day: float | None = None
    days_left: float | None = None
    history: list[YcCheckOut] = Field(default_factory=list)


class BackupFileOut(BaseModel):
    name: str
    size: int
    created: datetime


class PanelBackupConfigOut(BaseModel):
    enabled: bool = False
    interval_hours: int = 6
    host: str = ""
    port: int = 22
    user: str = "root"
    keep_recent: int = 28
    keep_daily: int = 30
    notify: bool = True
    ssh_key_set: bool = False


class PanelBackupConfigUpdate(BaseModel):
    enabled: bool | None = None
    interval_hours: int | None = Field(default=None, ge=1, le=168)
    host: str | None = None
    port: int | None = Field(default=None, ge=1, le=65535)
    user: str | None = None
    keep_recent: int | None = Field(default=None, ge=1, le=500)
    keep_daily: int | None = Field(default=None, ge=0, le=365)
    notify: bool | None = None
    ssh_key: str | None = None       # пустая строка стирает ключ


class PanelBackupStatusOut(BaseModel):
    config: PanelBackupConfigOut
    last_run_at: datetime | None = None
    last_ok_at: datetime | None = None
    last_error: str | None = None
    last_size: int | None = None
    last_duration_ms: int | None = None
    files: list[BackupFileOut] = Field(default_factory=list)
    total_bytes: int = 0
    disk_free_bytes: int | None = None


# ── доступность приложений (ИИ, TikTok) через хосты ─────────────────────────

class AiAccessConfigOut(BaseModel):
    enabled: bool = True
    interval_hours: int = 12
    sub_url: str = ""
    concurrency: int = 2
    exclude: str = ""
    notify: bool = True
    chat_id: str = ""
    keep_days: int = 45
    gemini_key_set: bool = False
    # Свой адрес не задан — подписку берём у следилки. Сам адрес не отдаём
    # второй раз: в нём короткий uuid пользователя.
    sub_from_subwatch: bool = False
    sub_configured: bool = False
    bot_ready: bool = False


class AiAccessConfigUpdate(BaseModel):
    enabled: bool | None = None
    interval_hours: int | None = Field(default=None, ge=1, le=168)
    sub_url: str | None = Field(default=None, max_length=512)
    concurrency: int | None = Field(default=None, ge=1, le=4)
    exclude: str | None = Field(default=None, max_length=1000)
    notify: bool | None = None
    chat_id: str | None = Field(default=None, max_length=64)
    keep_days: int | None = Field(default=None, ge=30, le=365)
    gemini_api_key: str | None = Field(default=None, max_length=256)   # пустая строка стирает


class AiServiceOut(BaseModel):
    key: str
    name: str
    group: str = "ai"             # ai | apps — колонки матрицы идут группами
    group_name: str = "ИИ"


class AiCellOut(BaseModel):
    verdict: str
    note: str = ""
    tries: int = 0
    prev: str | None = None       # вердикт прошлого прогона, если отличался


class AiHostRowOut(BaseModel):
    host: str
    host_key: str
    # Пусто у хоста, который стоит в очереди и ещё ни разу не проверялся.
    checked_at: datetime | None = None
    tunnel: str | None = None
    exit_ip: str | None = None
    exit_country: str | None = None
    ai_ip: str | None = None
    ai_country: str | None = None
    ai_warp: str | None = None
    prev_ai_country: str | None = None
    duration_ms: int = 0
    error: str | None = None
    results: dict[str, AiCellOut] = Field(default_factory=dict)
    changed: bool = False
    is_new: bool = False
    partial: bool = False         # результат из выборочного прогона, а не из полного
    # Что с хостом в идущем прогоне: queued | running | cancelling | done |
    # cancelled | missing. Пусто — хост в нём не участвует.
    state: str | None = None
    run_error: str | None = None  # почему последний выборочный прогон его не проверил


class AiRunHostOut(BaseModel):
    key: str
    host: str
    state: str                    # как AiHostRowOut.state, плюс aborted у оборванного прогона
    error: str | None = None


class AiRunOut(BaseModel):
    id: uuid.UUID
    started_at: datetime
    finished_at: datetime | None = None
    status: str                   # running | done | failed | aborted | cancelled
    trigger: str
    scope: str = "full"           # full | partial
    hosts_total: int | None = None
    hosts_done: int = 0
    error: str | None = None
    changes: list[dict] = Field(default_factory=list)
    hosts: list[AiRunHostOut] = Field(default_factory=list)
    stopping: bool = False        # прогон отменён и сворачивается


class AiRunRequest(BaseModel):
    # Ключи хостов (host_key из матрицы). Поля нет — речь про весь прогон.
    hosts: list[str] | None = Field(default=None, max_length=500)


class AiAccessStatusOut(BaseModel):
    config: AiAccessConfigOut
    services: list[AiServiceOut]
    running: AiRunOut | None = None     # идущий прогон, полный или выборочный
    last: AiRunOut | None = None        # последний полный прогон любого исхода
    shown: AiRunOut | None = None       # опора матрицы: последний удачный полный прогон
    prev: AiRunOut | None = None        # с чем сравниваем
    last_partial: AiRunOut | None = None   # последний законченный выборочный прогон
    pending_full: str | None = None     # полный прогон ждёт конца выборочного: schedule | manual
    rows: list[AiHostRowOut] = Field(default_factory=list)
    next_run_at: datetime | None = None


class AiHistoryPointOut(BaseModel):
    run_id: uuid.UUID
    checked_at: datetime
    host: str
    tunnel: str
    exit_ip: str | None = None
    exit_country: str | None = None
    ai_ip: str | None = None
    ai_country: str | None = None
    ai_warp: str | None = None
    error: str | None = None
    results: dict[str, AiCellOut] = Field(default_factory=dict)


class AiHostHistoryOut(BaseModel):
    host_key: str
    days: int
    points: list[AiHistoryPointOut] = Field(default_factory=list)


# ── следилка за нодами ───────────────────────────────────────────────────────

class NodeWatchConfigOut(BaseModel):
    enabled: bool = True
    threshold_min: int = 10
    remind_hours: int = 6
    notify: bool = True
    chat_id: str = ""
    exclude: list[str] = Field(default_factory=list)
    skip_panel_disabled: bool = True
    panel_alive: bool = True
    utc_offset: int = 5
    bot_ready: bool = False


class NodeWatchConfigUpdate(BaseModel):
    enabled: bool | None = None
    threshold_min: int | None = Field(default=None, ge=1, le=1440)
    remind_hours: int | None = Field(default=None, ge=0, le=168)   # 0 — не напоминать
    notify: bool | None = None
    chat_id: str | None = Field(default=None, max_length=64)
    exclude: list[uuid.UUID] | None = Field(default=None, max_length=1000)
    skip_panel_disabled: bool | None = None
    panel_alive: bool | None = None
    utc_offset: int | None = Field(default=None, ge=-12, le=14)


class NodeDownOut(BaseModel):
    node_id: str
    name: str
    host: str | None = None
    hosting: str | None = None
    since: datetime
    # false — молчит, но порог ещё не вышел: тревоги пока нет.
    alerted: bool = True
    signals: dict = Field(default_factory=dict)


class FleetBlindOut(BaseModel):
    since: datetime
    alerted: bool = False
    down: int = 0
    total: int = 0


class NodeViaPanelOut(BaseModel):
    """Рагент до ноды не дотягивается — наблюдается через панель."""

    node_id: str
    name: str
    host: str | None = None
    hosting: str | None = None
    since: datetime
    # false — панель сейчас про ноду молчит: жива ли она, неизвестно.
    panel_seen: bool = True


class PanelLostOut(BaseModel):
    since: datetime
    alerted: bool = False


class NodeWatchStatusOut(BaseModel):
    config: NodeWatchConfigOut
    down: list[NodeDownOut] = Field(default_factory=list)
    via_panel: list[NodeViaPanelOut] = Field(default_factory=list)
    fleet: FleetBlindOut | None = None
    panel_lost: PanelLostOut | None = None


class NodeOutageOut(BaseModel):
    id: uuid.UUID
    kind: str                       # node | fleet | panel
    node_id: uuid.UUID | None = None
    name: str
    host: str | None = None
    hosting: str | None = None
    started_at: datetime
    alerted_at: datetime
    ended_at: datetime | None = None
    resolution: str | None = None   # recovered | muted | removed | stopped
    notified: bool = False
    signals: dict = Field(default_factory=dict)
