#!/usr/bin/env python3
"""Upload the current git HEAD to a VPS and start docker compose prod.

Credentials come from the environment, never from the repo:

  DEPLOY_HOST  DEPLOY_USER  DEPLOY_PASSWORD  DEPLOY_DOMAIN
"""

from __future__ import annotations

import os
import secrets
import subprocess
import sys
import time
from pathlib import Path

import paramiko

ROOT = Path(__file__).resolve().parents[1]
SECRETS = ROOT / "deploy" / "secrets"


def run(cmd: list[str]) -> None:
    subprocess.check_call(cmd, cwd=ROOT)


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


def sh(client: paramiko.SSHClient, command: str, timeout: int = 120) -> str:
    stdin, stdout, stderr = client.exec_command(command, timeout=timeout)
    out = stdout.read().decode("utf-8", "replace")
    err = stderr.read().decode("utf-8", "replace")
    code = stdout.channel.recv_exit_status()
    text = (out + err).replace("\u2192", "->")
    if code != 0:
        raise RuntimeError(f"exit {code}\n$ {command}\n{text}")
    return text


def main() -> int:
    domain = os.environ.get("DEPLOY_DOMAIN", "ragent.bob4.fun")
    SECRETS.mkdir(parents=True, exist_ok=True)
    archive = SECRETS / "remna-agent.tar.gz"
    run(["git", "archive", "--format=tar.gz", "-o", str(archive), "HEAD"])

    admin_password = secrets.token_urlsafe(18)
    session_secret = secrets.token_hex(32)
    enc_key = secrets.token_hex(32)
    pg_password = secrets.token_hex(24)
    env_text = "\n".join(
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
    env_path = SECRETS / "ragent.env"
    env_path.write_text(env_text, encoding="utf-8")
    try:
        os.chmod(env_path, 0o600)
    except OSError:
        pass

    client = connect()
    try:
        print(sh(client, "uname -a; command -v docker; ss -lnt | sed -n '1,20p'"))
        print(sh(
            client,
            "mkdir -p /opt/remna-agent",
            timeout=30,
        ))

        sftp = client.open_sftp()
        sftp.put(str(archive), "/tmp/remna-agent.tar.gz")
        sftp.close()

        print(sh(
            client,
            "rm -rf /opt/remna-agent/* /opt/remna-agent/.[!.]* 2>/dev/null || true; "
            "tar -xzf /tmp/remna-agent.tar.gz -C /opt/remna-agent; "
            "rm -f /tmp/remna-agent.tar.gz; "
            "ls -la /opt/remna-agent | head",
            timeout=60,
        ))

        sftp = client.open_sftp()
        with sftp.file("/opt/remna-agent/.env", "w") as fh:
            fh.write(env_text)
        sftp.chmod("/opt/remna-agent/.env", 0o600)
        sftp.close()

        caddy_snippet = (ROOT / "deploy" / "Caddyfile").read_text(encoding="utf-8")
        print(sh(
            client,
            "python3 - <<'PY'\n"
            "from pathlib import Path\n"
            "p = Path('/opt/bob4fun-geodat-editor/Caddyfile')\n"
            "text = p.read_text()\n"
            "if 'ragent.bob4.fun' not in text:\n"
            "    backup = p.with_name('Caddyfile.bak-remna')\n"
            "    backup.write_text(text)\n"
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
        print(sh(client, compose, timeout=900))
        print(sh(
            client,
            "docker exec bob4fun-geodat-editor-caddy-1 caddy reload --config /etc/caddy/Caddyfile",
            timeout=60,
        ))
        time.sleep(8)
        print(sh(client, "cd /opt/remna-agent && docker compose -f docker-compose.yml -f docker-compose.prod.yml ps"))
        print(sh(client, "cd /opt/remna-agent && docker compose -f docker-compose.yml -f docker-compose.prod.yml logs --tail=50 api web"))
    finally:
        client.close()

    print(f"URL=https://{domain}")
    print("ADMIN_USERNAME=admin")
    print(f"ADMIN_PASSWORD={admin_password}")
    print(f"ENV_COPY={env_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
