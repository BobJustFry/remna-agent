# Remna Node Agent

Лёгкий агент на Python 3 (только stdlib).

- `GET /health` — без авторизации
- `GET /metrics` — `Authorization: Bearer <token>`
  - CPU/RAM/disk, RemnaNode version, **WARP** (`warp_present` / `warp_up` / `warp_healthy` / handshake / iface)
  - **proxy_peers** / **proxy_conns** — unique remote IPs and established TCP on public listen ports of `rw-core` / `xray` / `haproxy` (loopback steal/origin skipped). Two `ss` calls, cached ~8s. Absent on older agents.

Переменные: `REMNA_AGENT_TOKEN`, `REMNA_AGENT_PORT` (по умолчанию `7422`).

Установка обычно делается из панели («Установить агент»). Вручную:

```bash
# файлы в /opt/remna-agent и unit systemd remna-agent
systemctl enable --now remna-agent
```

Панель должна иметь сетевой доступ до `host:7422`.
