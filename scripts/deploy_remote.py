#!/usr/bin/env python3
"""Upload the working tree to the production VPS and rebuild compose.

Credentials come from the environment, never from the repo:

  DEPLOY_HOST  DEPLOY_USER  DEPLOY_PASSWORD  DEPLOY_DOMAIN

If /opt/remna-agent/.env already exists, it is kept (update deploy).
A new .env is generated only on first install.
Never ships local .env, postgres data, or docker-compose.override.yml.
"""

from __future__ import annotations

import os
import secrets
import sys
import tarfile
import time
from pathlib import Path

import paramiko

ROOT = Path(__file__).resolve().parents[1]
SECRETS = ROOT / "deploy" / "secrets"

_SKIP_DIRS = {
    ".git",
    ".cursor",
    "node_modules",
    "__pycache__",
    ".venv",
    "venv",
    "dist",
    "build",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".idea",
    ".vscode",
    "coverage",
    ".docker-data",
}
_SKIP_REL = {
    "docker-compose.override.yml",
    "deploy/secrets",
    "data/postgres",
    "data/backups",
    "data/wgcf",
}


def connect() -> paramiko.SSHClient:
    host = os.environ["DEPLOY_HOST"]
    user = os.environ.get("DEPLOY_USER", "root")
    password = os.environ["DEPLOY_PASSWORD"]
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(
        hostname=host,
        username=user,
        password=password,
        timeout=30,
        allow_agent=False,
        look_for_keys=False,
    )
    return client


def _skip(path: Path) -> bool:
    rel = path.relative_to(ROOT).as_posix()
    if any(part in _SKIP_DIRS for part in path.relative_to(ROOT).parts):
        return True
    if rel in _SKIP_REL or any(rel.startswith(p + "/") for p in _SKIP_REL):
        return True
    name = path.name
    if name == ".env" or (name.startswith(".env.") and name != ".env.example"):
        return True
    if name.endswith((".pyc", ".log", ".tar.gz")):
        return True
    return False


def pack_tree(archive: Path) -> None:
    archive.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive, "w:gz") as tar:
        for path in ROOT.rglob("*"):
            if not path.is_file() or _skip(path):
                continue
            tar.add(path, arcname=path.relative_to(ROOT).as_posix())


def sh(client: paramiko.SSHClient, command: str, timeout: int = 120) -> str:
    stdin, stdout, stderr = client.exec_command(command, timeout=timeout)
    out = stdout.read().decode("utf-8", "replace")
    err = stderr.read().decode("utf-8", "replace")
    code = stdout.channel.recv_exit_status()
    text = (out + err).replace("\u2192", "->")
    if code != 0:
        raise RuntimeError(f"exit {code}\n$ {command}\n{text}")
    return text


def build_env(domain: str) -> str:
    admin_password = secrets.token_urlsafe(18)
    session_secret = secrets.token_hex(32)
    enc_key = secrets.token_hex(32)
    pg_password = secrets.token_hex(24)
    return "\n".join(
        [
            "ADMIN_USERNAME=admin",
            f"ADMIN_PASSWORD={admin_password}",
            f"SESSION_SECRET={session_secret}",
            f"CREDENTIALS_ENCRYPTION_KEY={enc_key}",
            "POSTGRES_USER=remna",
            f"POSTGRES_PASSWORD={pg_password}",
            "POSTGRES_DB=remna_agent",
            f"DATABASE_URL=postgresql+asyncpg://remna:{pg_password}@db:5432/remna_agent",
            "ENVIRONMENT=production",
            "COOKIE_SECURE=true",
            "OPENAPI_URL=",
            f"ALLOWED_HOSTS={domain}",
            "TRUST_PROXY=true",
            f"CORS_ORIGINS=https://{domain}",
            "WEB_PORT=8080",
            "API_PORT=8000",
            "REMNAWAVE_PANEL_URL=",
            "REMNAWAVE_API_TOKEN=",
            "",
        ]
    )


def main() -> int:
    domain = os.environ.get("DEPLOY_DOMAIN", "ragent.bob4.fun")
    SECRETS.mkdir(parents=True, exist_ok=True)
    archive = SECRETS / "remna-agent.tar.gz"
    pack_tree(archive)
    print(f"packed {archive.stat().st_size} bytes")

    client = connect()
    try:
        print(sh(client, "mkdir -p /opt/remna-agent; test -f /opt/remna-agent/.env && echo HAS_ENV || echo NEW_ENV"))
        sftp = client.open_sftp()
        sftp.put(str(archive), "/tmp/remna-agent.tar.gz")
        sftp.close()

        extract = r"""
set -euo pipefail
cd /opt
if [ -f remna-agent/.env ]; then
  cp -a remna-agent/.env /tmp/remna-agent.env.bak
fi
# overlay code; never delete data/postgres
tar -xzf /tmp/remna-agent.tar.gz -C remna-agent
rm -f /tmp/remna-agent.tar.gz
if [ -f /tmp/remna-agent.env.bak ]; then
  cp -a /tmp/remna-agent.env.bak remna-agent/.env
  rm -f /tmp/remna-agent.env.bak
  echo KEPT_ENV
else
  echo NEED_ENV
fi
"""
        extract_out = sh(client, extract, timeout=60)
        print(extract_out)

        if "NEED_ENV" in extract_out:
            env_text = build_env(domain)
            env_path = SECRETS / "ragent.env"
            env_path.write_text(env_text, encoding="utf-8")
            sftp = client.open_sftp()
            with sftp.file("/opt/remna-agent/.env", "w") as fh:
                fh.write(env_text)
            sftp.chmod("/opt/remna-agent/.env", 0o600)
            sftp.close()
            print(f"wrote first-install .env, copy at {env_path}")

        print(sh(
            client,
            "python3 - <<'PY'\n"
            "from pathlib import Path\n"
            "p = Path('/opt/bob4fun-geodat-editor/Caddyfile')\n"
            "text = p.read_text()\n"
            "if 'ragent.bob4.fun' not in text:\n"
            "    extra = Path('/opt/remna-agent/deploy/Caddyfile').read_text()\n"
            "    p.write_text(text.rstrip() + '\\n\\n' + extra.strip() + '\\n')\n"
            "    print('caddyfile: appended ragent.bob4.fun')\n"
            "else:\n"
            "    print('caddyfile: ragent.bob4.fun already present')\n"
            "PY",
            timeout=30,
        ))

        compose = (
            "cd /opt/remna-agent && docker compose "
            "-f docker-compose.yml -f docker-compose.prod.yml up -d --build"
        )
        compose_out = sh(client, compose, timeout=900)
    sys.stdout.buffer.write((compose_out + "\n").encode("utf-8", "replace"))
        print(sh(
            client,
            "docker exec bob4fun-geodat-editor-caddy-1 caddy reload --config /etc/caddy/Caddyfile",
            timeout=60,
        ))
        time.sleep(8)
        print(sh(client, "cd /opt/remna-agent && docker compose -f docker-compose.yml -f docker-compose.prod.yml ps"))
        print(sh(client, "cd /opt/remna-agent && docker compose -f docker-compose.yml -f docker-compose.prod.yml logs --tail=30 api web"))
    finally:
        client.close()

    print(f"URL=https://{domain}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
