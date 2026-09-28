# Remna Node Agent

Лёгкий агент на Python 3 (только stdlib).

- `GET /health` — без авторизации
- `GET /metrics` — `Authorization: Bearer <token>`
  - CPU/RAM/disk, RemnaNode version, **WARP** (`warp_present` / `warp_up` / `warp_healthy` / handshake / iface)
  - **cf204_ok** / **cf204_ms** — HTTP `GET http://cp.cloudflare.com/generate_204` с ноды (фон, ~20 с)
  - **proxy_peers** / **proxy_conns** — unique remote IPs and established TCP on public listen ports of `rw-core` / `xray` / `haproxy` (loopback steal/origin skipped). Two `ss` calls, cached ~8s. Absent on older agents.
  - с 0.1.21: **swap_total_mb** / **swap_used_mb** / **swap_percent** (`/proc/meminfo`; без свопа — `swap_total_mb: 0`, процент `null`)
  - с 0.1.21: **tcp_out_segs_total** / **tcp_retrans_segs_total** (`/proc/net/snmp`), **oom_kill_total** (`/proc/vmstat`), **boot_id** — сырые счётчики с загрузки ядра; дельты за интервал считает панель, сброс ловит по `boot_id` и отрицательной разнице
  - с 0.1.22: **tcp_syn_retrans_total** / **tcp_orig_data_sent_total** (`/proc/net/netstat`, TcpExt `TCPSynRetrans` / `TCPOrigDataSent`) — по ним панель считает долю ретрансмитов без повторов SYN/SYN-ACK и на сегмент с данными, а не на все исходящие вместе с чистыми ACK
  - с 0.1.21: **ru_ping_ms** / **ru_loss_pct** (+ `ru_ping_targets` по целям) — фоновый пинг, по 5 ICMP до каждой цели раз в 30 с; цель, не ответившая ни разу, не считается, пока отвечает другая

Переменные: `REMNA_AGENT_TOKEN`, `REMNA_AGENT_PORT` (по умолчанию `7422`),
`REMNA_AGENT_RU_PING_TARGETS` (по умолчанию `185.32.187.161,81.27.254.54` — якоря RIPE Atlas
в Москве: Yandex и RASCOM; панель пишет значение из своего `AGENT_RU_PING_TARGETS` при установке),
`REMNA_AGENT_RU_PING_INTERVAL` (секунды, по умолчанию `30`).

Установка обычно делается из панели («Установить агент»). Вручную:

```bash
# файлы в /opt/remna-agent и unit systemd remna-agent
systemctl enable --now remna-agent
```

Панель должна иметь сетевой доступ до `host:7422`.
