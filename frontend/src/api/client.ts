import type { AuthType, NodeFormValues, NodeItem, OnlineMap } from "../types";

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
};

function toPayload(values: NodeFormValues, creating: boolean) {
  const payload: Record<string, unknown> = {
    name: values.name.trim(),
    host: values.host.trim(),
    ssh_port: Number(values.ssh_port) || 22,
    ssh_user: values.ssh_user.trim() || "root",
    auth_type: values.auth_type,
    provider: values.provider.trim() || null,
    country_code: values.country_code.trim() || null,
    notes: values.notes.trim() || null,
  };

  if (values.auth_type === "password") {
    if (values.password) {
      payload.password = values.password;
    } else if (creating) {
      payload.password = "";
    }
  } else if (values.private_key) {
    payload.private_key = values.private_key;
  } else if (creating) {
    payload.private_key = "";
  }

  return payload;
}
