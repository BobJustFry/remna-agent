# Remna Agent

Веб-панель для управления нодами Remnawave: список серверов, SSH-учётные данные, статус Online по ping.

> v0.1 — список нод (CRUD), копирование IP/паролей/ключей, Online-пинг. Установка/переустановка агента на ноде — в следующих версиях.

## Возможности

- Авторизация админа из `.env` (`ADMIN_USERNAME` / `ADMIN_PASSWORD`)
- PostgreSQL: ноды (имя, host, SSH, пароль или ключ в зашифрованном виде)
- Тёмный UI в духе панели Remnawave
- Копирование host и секретов одной кнопкой
- Колонка **Online**: проверка доступности каждые 5 секунд (ICMP, fallback TCP на SSH-порт), без перерисовки всего списка
- Загрузка приватного ключа из файла, включая PuTTY **`.ppk`** (конвертация в OpenSSH на сервере)
- Кнопка **Проверить SSH** в карточке ноды → статус `SSH-OK` / `fail` (текст ошибки во всплывающей подсказке)
- Справочник **Хостинги** (CRUD), URL сайта, переход из списка; favicon как логотип (иначе буква названия)
- Выбор/добавление хостинга в карточке ноды
- Нижняя статус-полоска: сколько нод Online и сколько не на связи
- Лёгкий **агент на ноде** (CPU/RAM/Disk): кнопка установки по SSH, статус и метрики в списке (порт `7422`)

## Требования

- Docker и Docker Compose
- Свободные порты `8080` (web) и `8000` (api, опционально)

## Быстрый старт

```bash
git clone https://github.com/BobJustFry/remna-agent.git
cd remna-agent
cp .env.example .env
```

Отредактируйте `.env`: задайте свой пароль админа и секреты сессии/шифрования.

```bash
docker compose up -d --build
```

Откройте панель: [http://localhost:8080](http://localhost:8080)

Логин по умолчанию из `.env.example`: `admin` / `change-me`.

Остановка (данные **сохраняются**):

```bash
docker compose down
```

## Сохранность данных БД

PostgreSQL пишет в каталог проекта [`data/postgres`](data/postgres) (bind mount).  
Пересборка (`--build`), `docker compose down`, перезапуск контейнеров — данные **не** удаляют.

Что реально стирает базу:

- удаление папки `data/postgres`
- `docker compose down -v` (если где-то снова появятся named volumes)
- смена `CREDENTIALS_ENCRYPTION_KEY` — строки в БД останутся, но пароли/SSH-ключи нод перестанут расшифровываться

Бэкап:

```bash
# Windows
powershell -File scripts/backup-db.ps1

# Linux/macOS
sh scripts/backup-db.sh
```

Копии: `data/backups/`.

## Переменные окружения

| Переменная | Описание |
|---|---|
| `ADMIN_USERNAME` | Логин в веб-интерфейс |
| `ADMIN_PASSWORD` | Пароль |
| `SESSION_SECRET` | Секрет подписи cookie-сессии |
| `CREDENTIALS_ENCRYPTION_KEY` | Ключ AES для паролей/SSH-ключей (рекомендуется 64 hex-символа = 32 байта) |
| `DATABASE_URL` | Строка подключения PostgreSQL (asyncpg) |
| `WEB_PORT` | Порт UI на хосте (по умолчанию `8080`) |
| `API_PORT` | Порт API на хосте (по умолчанию `8000`) |
| `ENVIRONMENT` | `development` или `production` |
| `COOKIE_SECURE` | `true` на HTTPS (в production включается само) |
| `OPENAPI_URL` | Пустая строка отключает `/docs` ([Conditional OpenAPI](https://fastapi.tiangolo.com/how-to/conditional-openapi/)) |
| `ALLOWED_HOSTS` | Хост для TrustedHost; в production конкретный домен |
| `CORS_ORIGINS` | Список Origin через запятую |
| `TRUST_PROXY` | Доверять `X-Forwarded-*` (Caddy/nginx) |

## Production (HTTPS)

[Caddy Automatic HTTPS](https://caddyserver.com/docs/automatic-https) берёт сертификат Let's Encrypt, если A-запись домена смотрит на VPS и открыты 80/443:

```bash
cp .env.example .env
# сильные ADMIN_PASSWORD, SESSION_SECRET, CREDENTIALS_ENCRYPTION_KEY, POSTGRES_PASSWORD
# CORS_ORIGINS=https://your.domain
# ALLOWED_HOSTS=your.domain
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
```

API (`8000`) и Postgres наружу не публикуются. Пароль админа не должен совпадать с root SSH.

Если на VPS уже есть Caddy на 80/443 (как geodat.bob4.fun), **не** поднимайте второй Caddy: оверлей подключает `web` к сети `bob4fun-geodat-editor_default`, а в существующий Caddyfile добавляется сайт `ragent.bob4.fun` → `remna-agent-web:80`.

## Структура

```
backend/    FastAPI + SQLAlchemy + Alembic
frontend/   React + Vite + Tailwind
```

## Примечания по Online

- Запрос `GET /api/nodes/online` пингует все ноды параллельно
- Предпочтительно ICMP; если ICMP недоступен — TCP connect к `ssh_port`
- Для ICMP контейнеру `api` выданы capabilities `NET_RAW` / `NET_ADMIN`

Docker Desktop на Windows ходит в интернет через таблицу маршрутов хоста.
Если поднят **VupenVPN** (Wintun, `198.18.0.0/15` + широкие префиксы), ICMP
к большинству нод отвечает сам TUN (~0–2 мс, TTL 64) — это не пинг до VPS.
Часть адресов (как `de-1`) уже уходит в Ethernet по /32 — отсюда «то через
туннель, то через VPN».

Обход: host-маршруты всех IP нод в LAN-шлюз (нужен Administrator):

```powershell
powershell -File scripts/windows/sync-direct-routes.ps1
```

Повторить после реконнекта Vupen, если /32 пропали. Снять: `-Remove`.

## Лицензия

MIT
