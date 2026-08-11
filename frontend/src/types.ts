export type AuthType = "password" | "private_key";

export type NodeItem = {
  id: string;
  name: string;
  host: string;
  ssh_port: number;
  ssh_user: string;
  auth_type: AuthType;
  has_password: boolean;
  has_private_key: boolean;
  provider: string | null;
  country_code: string | null;
  notes: string | null;
  created_at: string;
  updated_at: string;
};

export type NodeFormValues = {
  name: string;
  host: string;
  ssh_port: number;
  ssh_user: string;
  auth_type: AuthType;
  password: string;
  private_key: string;
  provider: string;
  country_code: string;
  notes: string;
};

export type OnlineStatus = {
  online: boolean;
  latency_ms: number | null;
  method: string | null;
};

export type OnlineMap = Record<string, OnlineStatus>;
