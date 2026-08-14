# Данные PostgreSQL

Каталог `postgres/` монтируется в контейнер `db` и хранит всю базу Remna Agent.

- **Не удаляйте** `data/postgres`, если не хотите потерять ноды/хостинги.
- `docker compose down` и `--build` данные **не** трогают.
- Опасно: `docker compose down -v`, ручное удаление `data/postgres`, смена `CREDENTIALS_ENCRYPTION_KEY` (секреты станут нечитаемыми).

Бэкап:

```bash
# Windows
powershell -File scripts/backup-db.ps1

# Linux/macOS
sh scripts/backup-db.sh
```

Копии появятся в `data/backups/`.
