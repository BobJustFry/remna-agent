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


class NodeCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    host: str = Field(min_length=1, max_length=255)
    ssh_port: int = Field(default=22, ge=1, le=65535)
    ssh_user: str = Field(default="root", min_length=1, max_length=128)
    auth_type: AuthTypeLiteral
    password: str | None = None
    private_key: str | None = None
    provider: str | None = Field(default=None, max_length=128)
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
    clear_password: bool = False
    clear_private_key: bool = False
    provider: str | None = Field(default=None, max_length=128)
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
    provider: str | None
    country_code: str | None
    notes: str | None
    created_at: datetime
    updated_at: datetime


class NodeSecretOut(BaseModel):
    auth_type: AuthTypeLiteral
    secret: str


class NodeOnlineStatus(BaseModel):
    online: bool
    latency_ms: float | None = None
    method: str | None = None


class NodesOnlineResponse(BaseModel):
    statuses: dict[str, NodeOnlineStatus]
