# Remna Node Agent

Лёгкий агент на Python 3 (только stdlib).

- `GET /health` — без авторизации
- `GET /metrics` — `Authorization: Bearer <token>`

Переменные: `REMNA_AGENT_TOKEN`, `REMNA_AGENT_PORT` (по умолчанию `7422`).

Установка обычно делается из панели («Установить агент»). Вручную:

```bash
# файлы в /opt/remna-agent и unit systemd remna-agent
systemctl enable --now remna-agent
```

Панель должна иметь сетевой доступ до `host:7422`.
