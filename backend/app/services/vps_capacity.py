"""Rough VPS capacity estimate for RemnaNode / Xray (concurrent tunnels)."""

from __future__ import annotations

import json
import re
import shlex
import uuid
from dataclasses import dataclass, field

from app.services.agent_install import AgentInstallError, _run, _run_priv
from app.services.haproxy_script import HaproxyScriptError, _open_priv

_COLLECT_PY = r"""
import json, os, re, shutil, subprocess
from pathlib import Path

def sh(cmd):
    try:
        p = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=8)
        return (p.stdout or "").strip()
    except Exception:
        return ""

def n(val, default=0):
    try:
        return int(float(val))
    except (TypeError, ValueError):
        return default

mem = {}
try:
    for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
        k, v = line.split(":", 1)
        mem[k] = int(v.strip().split()[0])
except OSError:
    pass

try:
    st = os.statvfs("/")
    disk_total = st.f_frsize * st.f_blocks
    disk_free = st.f_frsize * st.f_bavail
except OSError:
    disk_total = disk_free = 0

cores = n(sh("nproc")) or os.cpu_count() or 1
model = sh("grep -m1 'model name' /proc/cpuinfo | cut -d: -f2").strip()
if not model:
    model = sh("grep -m1 'Model' /proc/cpuinfo | cut -d: -f2").strip()

load = []
try:
    load = [round(float(x), 2) for x in Path("/proc/loadavg").read_text().split()[:3]]
except (OSError, ValueError):
    load = []

virt = sh("systemd-detect-virt 2>/dev/null") or "unknown"
os_name = sh(". /etc/os-release 2>/dev/null && echo \"$PRETTY_NAME\"")

haproxy = bool(shutil.which("haproxy") or Path("/usr/sbin/haproxy").is_file())
haproxy_up = sh("systemctl is-active haproxy 2>/dev/null") == "active"
docker = Path("/var/run/docker.sock").exists() or bool(shutil.which("docker"))
remnanode = False
if docker:
    ps = sh("docker ps --format '{{.Names}} {{.Image}}' 2>/dev/null")
    remnanode = "remnanode" in ps.lower() or "remnawave/node" in ps.lower()
if Path("/opt/remnanode").is_dir():
    remnanode = True

estab = n(sh("ss -Htan state established 2>/dev/null | wc -l"))
ct_max = n(sh("sysctl -n net.netfilter.nf_conntrack_max 2>/dev/null"))
ct_now = n(sh("sysctl -n net.netfilter.nf_conntrack_count 2>/dev/null"))

print(json.dumps({
    "hostname": sh("hostname"),
    "os": os_name,
    "virt": virt,
    "cpu_cores": cores,
    "cpu_model": model,
    "ram_total_mb": round(mem.get("MemTotal", 0) / 1024),
    "ram_avail_mb": round(mem.get("MemAvailable", mem.get("MemFree", 0)) / 1024),
    "disk_total_gb": round(disk_total / (1024 ** 3), 1),
    "disk_free_gb": round(disk_free / (1024 ** 3), 1),
    "loadavg": load,
    "haproxy": haproxy,
    "haproxy_up": haproxy_up,
    "docker": docker,
    "remnanode": remnanode,
    "tcp_estab": estab,
    "conntrack_max": ct_max or None,
    "conntrack_count": ct_now or None,
}, ensure_ascii=False))
"""


@dataclass
class VpsCapacity:
    hostname: str | None = None
    os: str | None = None
    virt: str | None = None
    cpu_cores: int = 1
    cpu_model: str | None = None
    ram_total_mb: int = 0
    ram_avail_mb: int = 0
    disk_total_gb: float = 0.0
    disk_free_gb: float = 0.0
    loadavg: list[float] = field(default_factory=list)
    haproxy: bool = False
    haproxy_up: bool = False
    docker: bool = False
    remnanode: bool = False
    tcp_estab: int = 0
    conntrack_max: int | None = None
    conntrack_count: int | None = None
    comfort: int = 0
    ceiling: int = 0
    panel_users: int = 0
    limiter: str = "RAM"
    summary: str = ""
    notes: list[str] = field(default_factory=list)
    error: str | None = None


def _as_int(v: object, default: int = 0) -> int:
    if isinstance(v, bool):
        return default
    if isinstance(v, int):
        return v
    if isinstance(v, float):
        return int(v)
    if isinstance(v, str):
        m = re.search(r"-?\d+", v)
        return int(m.group(0)) if m else default
    return default


def _as_float(v: object, default: float = 0.0) -> float:
    if isinstance(v, bool):
        return default
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        try:
            return float(v)
        except ValueError:
            return default
    return default


def estimate(facts: dict) -> VpsCapacity:
    cores = max(1, _as_int(facts.get("cpu_cores"), 1))
    ram = max(0, _as_int(facts.get("ram_total_mb")))
    avail = max(0, _as_int(facts.get("ram_avail_mb")))
    disk_free = _as_float(facts.get("disk_free_gb"))
    disk_total = _as_float(facts.get("disk_total_gb"))
    load = facts.get("loadavg") if isinstance(facts.get("loadavg"), list) else []
    load1 = _as_float(load[0]) if load else 0.0
    virt = facts.get("virt") if isinstance(facts.get("virt"), str) else None
    docker = bool(facts.get("docker"))
    remnanode = bool(facts.get("remnanode"))
    haproxy = bool(facts.get("haproxy"))

    # Baseline: OS + typical RemnaNode stack. Not a lab benchmark — a conservative budget.
    reserve = 350
    if docker:
        reserve += 180
    if remnanode:
        reserve += 120
    if haproxy:
        reserve += 40
    usable = max(0, ram - reserve)

    # Concurrent Xray Reality / xHTTP tunnels: ~2.5 MB comfort, ~1.5 MB packed.
    ram_comfort = usable * 10 // 25
    ram_max = usable * 10 // 15

    cpu_comfort = cores * 90
    cpu_max = cores * 160
    virt_l = (virt or "").lower()
    if virt_l in ("openvz", "lxc", "lxc-libvirt"):
        cpu_comfort = int(cpu_comfort * 0.7)
        cpu_max = int(cpu_max * 0.75)

    comfort = min(ram_comfort, cpu_comfort)
    ceiling = min(ram_max, cpu_max)
    if load1 and load1 > cores * 0.85:
        comfort = int(comfort * 0.7)
    comfort = max(0, comfort)
    ceiling = max(comfort, ceiling)

    limiter = "RAM" if ram_comfort <= cpu_comfort else "CPU"
    # Typical VPN: not everyone online at once.
    panel_users = comfort * 4

    notes: list[str] = []
    if not remnanode:
        notes.append("RemnaNode сейчас нет — оценка, если поставить Xray на эту машину.")
    if haproxy:
        notes.append("HAProxy сам по себе лёгкий: десятки тысяч TCP-сессий ему не проблема.")
    if ram < 900:
        notes.append("Мало RAM: упираться будет память, не процессор.")
    if cores == 1:
        notes.append("Одно ядро: при всплеске рукопожатий (много людей онлайн сразу) будет очередь.")
    if disk_free and disk_free < 2:
        notes.append(f"Свободно {disk_free:.1f} ГБ диска — для логов и docker мало.")
    if virt_l in ("openvz", "lxc"):
        notes.append(f"Виртуализация {virt}: CPU часто общий, потолок ниже, чем у KVM.")
    if facts.get("conntrack_max") and _as_int(facts.get("conntrack_max")) < 32768:
        notes.append("conntrack низкий — при живой ноде таблица закончится раньше RAM.")
    notes.append("Канал не мерили: 50 человек в 1080p уже просят сотни мегабит.")
    notes.append("Это одновременные туннели, не «записи в панели». Торренты режут цифру в разы.")

    bits = [
        f"{cores} CPU",
        f"{ram} МБ RAM" if ram else "RAM ?",
        f"{disk_total:.0f} ГБ диск" if disk_total else None,
        virt if virt and virt != "unknown" else None,
    ]
    iron = ", ".join(x for x in bits if x)
    if comfort <= 0:
        summary = (
            f"{iron}. Под RemnaNode почти нет запаса — нужен тариф больше по RAM."
        )
    else:
        summary = (
            f"{iron}. Комфортно ~{comfort} одновременных туннелей "
            f"(Reality / xHTTP, обычный сёрфинг). Потолок ~{ceiling}, дальше свопы и лаги. "
            f"В панели при живых {comfort} онлайнах можно держать порядка {panel_users} учёток. "
            f"Упирается в {limiter}."
        )

    return VpsCapacity(
        hostname=facts.get("hostname") if isinstance(facts.get("hostname"), str) else None,
        os=facts.get("os") if isinstance(facts.get("os"), str) else None,
        virt=virt,
        cpu_cores=cores,
        cpu_model=facts.get("cpu_model") if isinstance(facts.get("cpu_model"), str) else None,
        ram_total_mb=ram,
        ram_avail_mb=avail,
        disk_total_gb=disk_total,
        disk_free_gb=disk_free,
        loadavg=[_as_float(x) for x in load][:3],
        haproxy=haproxy,
        haproxy_up=bool(facts.get("haproxy_up")),
        docker=docker,
        remnanode=remnanode,
        tcp_estab=_as_int(facts.get("tcp_estab")),
        conntrack_max=_as_int(facts.get("conntrack_max")) or None,
        conntrack_count=_as_int(facts.get("conntrack_count")) or None,
        comfort=comfort,
        ceiling=ceiling,
        panel_users=panel_users,
        limiter=limiter,
        summary=summary,
        notes=notes,
    )


def fetch_vps_capacity(
    *,
    host: str,
    ssh_port: int,
    username: str,
    auth_type: str,
    password: str | None,
    private_key: str | None,
) -> VpsCapacity:
    try:
        for client, priv in _open_priv(
            host=host,
            ssh_port=ssh_port,
            username=username,
            auth_type=auth_type,
            password=password,
            private_key=private_key,
            cancel=None,
        ):
            remote = f"/tmp/vps-capacity-{uuid.uuid4().hex[:10]}.py"
            sftp = client.open_sftp()
            try:
                with sftp.file(remote, "w") as f:
                    f.write(_COLLECT_PY)
            finally:
                sftp.close()
            try:
                code, out, err = _run_priv(
                    client,
                    priv,
                    f"python3 {shlex.quote(remote)}",
                    timeout=25.0,
                )
            finally:
                _run(client, f"rm -f {shlex.quote(remote)}")
            raw = ((out or "") + ("\n" + err if err else "")).strip()
            last = None
            for line in raw.splitlines():
                line = line.strip()
                if line.startswith("{") and line.endswith("}"):
                    last = line
            if last is None:
                start, end = raw.find("{"), raw.rfind("}")
                if start >= 0 and end > start:
                    last = raw[start : end + 1]
            if not last:
                return VpsCapacity(error=f"не удалось снять параметры (exit {code})")
            try:
                facts = json.loads(last)
            except json.JSONDecodeError as exc:
                return VpsCapacity(error=f"ответ ноды не JSON: {exc}")
            if not isinstance(facts, dict):
                return VpsCapacity(error="некорректный ответ ноды")
            return estimate(facts)
    except HaproxyScriptError as exc:
        return VpsCapacity(error=exc.message)
    except AgentInstallError as exc:
        return VpsCapacity(error=exc.message)
    return VpsCapacity(error="не удалось проверить VPS")
