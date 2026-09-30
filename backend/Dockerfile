# Ядро xray для проверки доступа к ИИ: на каждый хост подписки рагент поднимает
# отдельный процесс клиента. Берём готовый бинарник из официального образа —
# статическая сборка Go, зависимостей не тянет. Версия та, на которой прошёл
# разовый замер 29–30.09; поднимать осознанно, проверив прогоном.
ARG XRAY_VERSION=26.3.27
FROM ghcr.io/xtls/xray-core:${XRAY_VERSION} AS xray

FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    iputils-ping \
    putty-tools \
    && rm -rf /var/lib/apt/lists/*

COPY --from=xray /usr/local/bin/xray /usr/local/bin/xray

COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ .
COPY agent /app/agent
COPY scripts /app/scripts

EXPOSE 8000

CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
