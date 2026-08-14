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
    cpu_percent: float | None = None
    mem_percent: float | None = None
    disk_percent: float | None = None
    loadavg: list[float] | None = None
    error: str | None = None


class NodesAgentResponse(BaseModel):
    statuses: dict[str, AgentNodeStatus]
    latest_agent_version: str = "0.0.0"


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
