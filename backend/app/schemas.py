import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


AuthTypeLiteral = Literal["password", "private_key"]


class LoginRequest(BaseModel):
    username: str
    password: str


class UserOut(BaseModel):
    username: str


class HostingCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    website_url: str | None = Field(default=None, max_length=512)
    notes: str | None = None


class HostingUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    website_url: str | None = Field(default=None, max_length=512)
    notes: str | None = None


class HostingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    website_url: str | None
    favicon_data: str | None
    notes: str | None
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
    cpu_percent: float | None = None
    mem_percent: float | None = None
    disk_percent: float | None = None
    loadavg: list[float] | None = None
    error: str | None = None


class NodesAgentResponse(BaseModel):
    statuses: dict[str, AgentNodeStatus]
    latest_agent_version: str = "0.0.0"
    latest_wgcf_version: str | None = None


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


class AppSettingsOut(BaseModel):
    remna_secret_key: str = ""
    defaults: RemnaScriptDefaults = Field(default_factory=RemnaScriptDefaults)


class AppSettingsUpdate(BaseModel):
    remna_secret_key: str | None = None
    clear_remna_secret_key: bool = False
    defaults: RemnaScriptDefaults | None = None


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
    skip_system_update: bool = True


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


HaproxyActionLiteral = Literal["install", "apply", "reload", "start", "stop"]
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
