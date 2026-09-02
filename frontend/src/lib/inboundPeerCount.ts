import type { AgentStatus, SharingStatus } from "../types";

/** ss ESTAB peers, or Xray client IPs when inbound is localhost/CDN (ss = 0). */
export function inboundPeerCount(
  agent: AgentStatus | undefined,
  sharing: SharingStatus | null | undefined,
  nodeId: string,
): number | null {
  const ss = agent?.proxy_peers;
  const xray = sharing?.peers_by_agent_id?.[nodeId]?.ips;
  if (typeof ss === "number" && ss > 0) return ss;
  if (typeof xray === "number") return xray;
  return ss ?? null;
}

export function inboundPeerFromXray(
  agent: AgentStatus | undefined,
  sharing: SharingStatus | null | undefined,
  nodeId: string,
): boolean {
  const ss = agent?.proxy_peers;
  if (typeof ss === "number" && ss > 0) return false;
  return typeof sharing?.peers_by_agent_id?.[nodeId]?.ips === "number";
}
