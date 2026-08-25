"""REALITY dest picker: scan from the VPS, then loopback-test winners."""

from __future__ import annotations

import json
import queue
import threading
import uuid
from collections.abc import Iterator
from typing import Any

from app.services.agent_install import _run_priv
from app.services.haproxy_script import HaproxyScriptError, _open_priv
from app.services.remnanode_script import RemnaScriptError, _stream_priv_command

# Список доноров по стране ноды — из protocols/sni-choose.txt (Manual32).
# Страны, которых там нет, получают только «*». Домены не выдумывать.
# Handshake REALITY идёт с ноды к dest (дока Xray: target), поэтому близость
# меряем от ноды, не от клиента.
COUNTRY_SEEDS: dict[str, list[str]] = {
    "DE": ["www.bosch.de", "www.siemens.com", "www.miele.de", "www.zalando.de", "www.dm.de"],
    "NL": ["www.asml.com", "www.bol.com", "www.tomtom.com", "www.klm.com", "www.philips.com"],
    "FI": ["www.nokia.com", "www.kone.com", "www.neste.com", "www.fortum.com", "www.wartsila.com"],
    "FR": ["www.michelin.com", "www.decathlon.fr", "www.leroymerlin.fr", "www.orange.fr",
           "www.dassault-aviation.com"],
    "GB": ["www.dyson.co.uk", "www.arm.com", "www.sage.com", "www.jaguar.com", "www.rolls-royce.com"],
    "SE": ["www.electrolux.com", "www.volvocars.com", "www.scania.com", "www.husqvarna.com",
           "www.ikea.com"],
    "NO": ["www.equinor.com", "www.telenor.com", "www.dnb.no", "www.jotun.com",
           "www.hurtigruten.com"],
    "DK": ["www.lego.com", "www.maersk.com", "www.grundfos.com", "www.bang-olufsen.com",
           "www.novonordisk.com"],
    "PL": ["www.orlen.pl", "www.inpost.pl", "www.play.pl", "www.cdprojekt.com", "www.allegro.pl"],
    "TR": ["www.arcelik.com.tr", "www.turkishairlines.com", "www.vestel.com.tr", "www.beko.com",
           "www.ford.com.tr"],
    "US": ["www.dell.com", "www.logitech.com", "www.redhat.com", "www.nvidia.com", "www.akamai.com"],
    "RU": ["www.citilink.ru", "www.dns-shop.ru", "www.sportmaster.ru", "www.mvideo.ru",
           "www.lamoda.ru"],
    "*": ["www.asus.com", "www.philips.com", "www.ikea.com", "www.logitech.com", "www.bosch.de"],
}
CC_ALIAS = {"UK": "GB"}
NEAR_MS = 40
# Метод: не брать «у всех на слуху» доноров (поисковики, магазины софта).
NOISY_DONORS = (
    "google.com", "youtube.com", "gstatic.com", "microsoft.com", "live.com",
    "office.com", "apple.com", "icloud.com", "cloudflare.com", "github.com",
    "amazon.com", "amazonaws.com", "facebook.com", "instagram.com", "twitter.com",
    "x.com", "telegram.org", "bing.com",
)

# ru_only: 40 гигантов. Прогон geosite с es-1 2026-08-25.
# Не сеем: Yandex/VK/WB (403/418/498 с EU-VPS), банки 000, RT-ферма, rbc (loopback).
# Не-.ru: x5.com, ozon.com, ozon.tm, okko.tv, okko.sport, ren.tv, okolo.app.
# extra не режется is_ru. Сначала X25519, потом MLKEM.
# Копия для CLI: vpn-probe-agent/tools/files/ru-cover-dests.txt
RU_SEED = [
    "www.petrovich.ru",
    "www.kion.ru",
    "www.x5.com",
    "www.kommersant.ru",
    "www.rambler.ru",
    "ok.ru",
    "www.tutu.ru",
    "www.ntv.ru",
    "www.ren.tv",
    "www.grandexpress.ru",
    "www.wink.ru",
    "www.x5club.ru",
    "www.vprok.ru",
    "www.iz.ru",
    "www.mtsdengi.ru",
    "www.kontur.ru",
    "www.mts.ru",
    "www.ctc.ru",
    "www.sbermarket.ru",
    "www.friday.ru",
    "www.fivepost.ru",
    "max.ru",
    "hh.ru",
    "www.dixy.ru",
    "www.aeroflot.ru",
    "www.tass.ru",
    "www.megamarket.ru",
    "www.ozon.ru",
    "www.ozon.com",
    "www.ozon.tm",
    "www.okko.sport",
    "www.okko.tv",
    "www.perekrestok.ru",
    "www.x5.ru",
    "www.kuper.ru",
    "www.okolo.app",
    "www.2gis.ru",
    "www.ertelecom.ru",
    "www.spastv.ru",
    "www.domashniy.ru",
]


class DestPickError(RemnaScriptError):
    pass


def infer_country(country_code: str | None, node_name: str | None = None) -> str | None:
    cc = (country_code or "").strip().upper()
    if len(cc) >= 2 and cc[:2].isalpha():
        return CC_ALIAS.get(cc[:2], cc[:2])
    tok = (node_name or "").split("-", 1)[0].strip().upper()
    if len(tok) == 2 and tok.isalpha():
        return CC_ALIAS.get(tok, tok)
    return None


def seeds_for_country(cc: str | None) -> list[str]:
    key = infer_country(cc) or ""
    out: list[str] = []
    if key and key in COUNTRY_SEEDS:
        out.extend(COUNTRY_SEEDS[key])
    out.extend(COUNTRY_SEEDS["*"])
    return list(dict.fromkeys(out))


def _host_only(url_or_host: str) -> str:
    s = (url_or_host or "").strip().lower()
    if "://" in s:
        from urllib.parse import urlparse
        s = urlparse(s).hostname or ""
    if s.startswith("www."):
        s = s[4:]
    return s.rstrip(".")


def _redirect_to_self(host: str, redirect: str) -> bool:
    if not (redirect or "").strip():
        return True
    a, b = _host_only(host), _host_only(redirect)
    return bool(a and b and a == b)


def _is_noisy(host: str) -> bool:
    h = _host_only(host)
    return any(h == n or h.endswith("." + n) for n in NOISY_DONORS)


SCAN_PY = r'''
import json, re, subprocess, threading
from concurrent.futures import ThreadPoolExecutor, as_completed

def sh(cmd, timeout=30):
    try:
        return subprocess.run(cmd, shell=True, capture_output=True, text=True,
                              timeout=timeout).stdout
    except Exception:
        return ""

_log_lock = threading.Lock()

def log(msg):
    with _log_lock:
        print("@@LOG@@" + msg, flush=True)

RU_ONLY = __RU_ONLY__
SCAN = __SCAN__
EXTRA = __CANDS__
LIMIT = __LIMIT__

def is_ru(h):
    h = (h or "").strip().lower().rstrip(".")
    return (
        h.endswith(".ru") or h.endswith(".su") or h.endswith(".xn--p1ai")
        or h.endswith(".рф") or h == "vk.com" or h.endswith(".vk.com")
    )

log("нода онлайн, смотрю свой IPv4…")
own = sh("ip -4 route get 1.1.1.1 | grep -oP 'src \\K\\S+'").strip()
base = ".".join(own.split(".")[:3]) if own else ""
res = {"own": own, "subnet": (base + ".0/24") if base else "", "found": [], "checked": []}
log("IP ноды %s%s" % (own or "—", (" · " + res["subnet"]) if base else ""))
log("режим: " + ("только РФ, без /24" if RU_ONLY else ("скан /24 + свои домены" if SCAN else "только список, без /24")))

def port_open(ip):
    import socket
    s = socket.socket(); s.settimeout(1.5)
    try:
        s.connect((ip, 443)); return ip
    except Exception:
        return None
    finally:
        s.close()

domains = []
if SCAN and base:
    log("скан %s:443 …" % (base + ".0/24"))
    ips = [f"{base}.{i}" for i in range(1, 255) if f"{base}.{i}" != own]
    with ThreadPoolExecutor(max_workers=80) as ex:
        alive = [ip for ip in ex.map(port_open, ips) if ip]
    res["alive443"] = len(alive)
    log("живых на :443 — %s" % len(alive))

    def cert_names(ip):
        out = sh("timeout 6 openssl s_client -connect %s:443 </dev/null 2>/dev/null "
                 "| openssl x509 -noout -subject -ext subjectAltName 2>/dev/null" % ip, 20)
        return ip, re.findall(r"DNS:([^,\s]+)", out) + re.findall(r"CN\s*=\s*([^,\s/]+)", out)

    log("читаю сертификаты с %s адресов…" % len(alive))
    cert_done = 0
    with ThreadPoolExecutor(max_workers=30) as ex:
        for ip, names in ex.map(cert_names, alive):
            cert_done += 1
            if cert_done == 1 or cert_done % 20 == 0 or cert_done == len(alive):
                log("сертификаты [%s/%s]" % (cert_done, len(alive)))
            for n in names:
                n = n.strip().lower()
                if n.startswith("*."):
                    n = "www." + n[2:]
                if "." not in n or n.endswith(".local"):
                    continue
                if RU_ONLY and not is_ru(n):
                    continue
                domains.append(n)
                res["found"].append({"ip": ip, "name": n})
    log("имён в сертификатах — %s" % len(res["found"]))

extra = [s.strip().lower() for s in EXTRA if s.strip()]
cands = list(dict.fromkeys(extra + domains))[:LIMIT]
log("кандидатов к проверке — %s (по 5 сразу: TLS, HTTP/2, 8 коннектов, tls ping)" % len(cands))
if cands:
    log("очередь: " + ", ".join(cands[:8]) + ("…" if len(cands) > 8 else ""))

def screen(h):
    with inflight_lock:
        inflight.add(h)
    try:
        return _screen_body(h)
    finally:
        with inflight_lock:
            inflight.discard(h)

def _screen_body(h):
    log("→ " + h + "  (заголовок TLS)")
    d = {"host": h}
    out = sh("curl -sI --http2 --tlsv1.3 --tls-max 1.3 --max-time 8 "
             "-w 'M|%{http_code}|%{http_version}|%{time_connect}|%{time_appconnect}|%{remote_ip}|%{redirect_url}\\n' "
             "https://" + h + "/ 2>/dev/null", 25)
    for ln in out.splitlines():
        if ln.startswith("M|"):
            p = ln.split("|")
            d.update({"code": p[1], "http_version": p[2],
                      "connect_ms": float(p[3] or 0) * 1000,
                      "tls_ms": (float(p[4] or 0) - float(p[3] or 0)) * 1000,
                      "ip": p[5], "redirect": p[6].strip()})
    # curl --tlsv1.3 --tls-max 1.3: ответ = TLS 1.3 (метод sni-choose: TLS13 обязателен)
    d["tls13"] = bool(d.get("http_version"))
    log("  %s  HTTP %s  h%s  %s" % (
        h, d.get("code") or "—", d.get("http_version") or "—", d.get("ip") or "нет IP"))
    d["cdn"] = sh("curl -sI --max-time 8 https://" + h +
                  "/ 2>/dev/null | grep -icE 'cf-ray|cloudflare|x-amz-cf|x-akamai|fastly'", 20).strip()
    log("  %s  8 коннектов…" % h)
    vals, fails = [], 0
    for _ in range(8):
        o = sh("curl -s -o /dev/null --max-time 8 -w '%{time_connect}' https://" + h + "/ 2>/dev/null", 15)
        try:
            vals.append(float(o) * 1000)
        except ValueError:
            fails += 1
    d["stalls"] = len([v for v in vals if v > 800])
    d["fails"] = fails
    d["connect_med"] = sorted(vals)[len(vals) // 2] if vals else None
    log("  tls ping " + h + " …")
    ping = sh("docker exec remnanode /usr/local/bin/rw-core tls ping " + h + " 2>&1", 40)
    kex = re.findall(r"key exchange:\s+\S+\s+\(([^)]+)\)", ping)
    d["kex"] = ", ".join(dict.fromkeys(kex))
    return d

done = 0
lock = threading.Lock()
inflight = set()
inflight_lock = threading.Lock()
checked = []
total = len(cands)
stop_beat = threading.Event()

def beat():
    waited = 0
    while not stop_beat.wait(8):
        waited += 8
        with inflight_lock:
            now = ", ".join(sorted(inflight)) or "жду слот"
        log("ещё работаю %sс, сейчас в работе: %s" % (waited, now))

threading.Thread(target=beat, daemon=True).start()
try:
    with ThreadPoolExecutor(max_workers=5) as ex:
        futs = {ex.submit(screen, h): h for h in cands}
        for fut in as_completed(futs):
            h = futs[fut]
            try:
                d = fut.result()
            except Exception as exc:
                d = {"host": h, "code": "", "kex": "", "connect_med": None, "redirect": "", "cdn": "0"}
                log("  ошибка %s: %s" % (h, exc))
            with lock:
                done += 1
                n = done
            checked.append(d)
            ms = d.get("connect_med")
            bits = [
                "[%s/%s]" % (n, total),
                h,
                "HTTP " + (d.get("code") or "—"),
                ("%dмс" % int(ms)) if isinstance(ms, (int, float)) else "—мс",
                d.get("kex") or "нет kex",
            ]
            if d.get("redirect"):
                bits.append("редирект")
            if (d.get("cdn") or "0") not in ("0", ""):
                bits.append("CDN")
            log(" ".join(bits))
finally:
    stop_beat.set()
res["checked"] = checked
log("проверка кандидатов закончена: %s шт." % len(checked))

print("@@JSON@@" + json.dumps(res, ensure_ascii=False), flush=True)
'''

LOOPBACK_PY = r'''
import json, re, subprocess, time, uuid

def sh(cmd, timeout=40):
    try:
        p = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        return (p.stdout or "") + (("\n" + p.stderr) if p.stderr else "")
    except Exception as e:
        return str(e)

def log(msg):
    print("@@LOG@@" + msg, flush=True)

DESTS = __DESTS__
PORT = __PORT__
LOCAL = 10891

def kill_loop():
    # PID из docker top — хостовые. В контейнере нет pkill.
    top = sh("docker top remnanode", 15)
    for ln in top.splitlines()[1:]:
        if "/tmp/porttest.json" not in ln and "/tmp/portcli.json" not in ln:
            continue
        parts = ln.split()
        if len(parts) >= 2 and parts[1].isdigit():
            sh("kill -9 %s || true" % parts[1], 8)
    sh("pkill -9 -f /tmp/porttest.json || true; pkill -9 -f /tmp/portcli.json || true", 10)
    for _ in range(8):
        left = sh("ss -ltn | grep -E ':%s|:%s' || true" % (PORT, LOCAL), 8).strip()
        if not left:
            return
        time.sleep(0.4)

def x25519():
    out = sh("docker exec remnanode /usr/local/bin/rw-core x25519 2>&1", 20)
    priv = pub = ""
    for ln in out.splitlines():
        low = ln.lower()
        if "private" in low:
            priv = ln.split(":")[-1].strip()
        elif "password" in low or "public" in low:
            pub = ln.split(":")[-1].strip()
    return priv, pub

ps = sh("docker ps --format '{{.Names}} {{.Image}}'", 15)
if "remnanode" not in ps.lower() and "remnawave/node" not in ps.lower():
    print("@@JSON@@" + json.dumps({"error": "на ноде нет контейнера remnanode — петлю проверить нельзя"}))
    raise SystemExit(0)

results = []
for i, dest in enumerate(DESTS, 1):
    log("[%s/%s] петля: %s" % (i, len(DESTS), dest))
    priv, pub = x25519()
    if not priv or not pub:
        results.append({"host": dest, "ok": False, "note": "не смог сгенерировать ключи REALITY"})
        continue
    sid = uuid.uuid4().hex[:16]
    uid = str(uuid.uuid4())
    server = {
        "log": {"loglevel": "info"},
        "inbounds": [{
            "tag": "porttest", "port": PORT, "protocol": "vless",
            "settings": {"clients": [{"id": uid, "flow": "xtls-rprx-vision"}], "decryption": "none"},
            "streamSettings": {
                "network": "raw", "security": "reality",
                "realitySettings": {
                    "target": dest + ":443", "xver": 0,
                    "serverNames": [dest], "privateKey": priv, "shortIds": [sid],
                },
            },
        }],
        "outbounds": [{"protocol": "freedom"}],
    }
    client = {
        "log": {"loglevel": "warning"},
        "inbounds": [{"port": LOCAL, "listen": "127.0.0.1", "protocol": "socks",
                      "settings": {"auth": "noauth", "udp": True}}],
        "outbounds": [{
            "protocol": "vless",
            "settings": {"vnext": [{"address": "127.0.0.1", "port": PORT,
                                    "users": [{"id": uid, "encryption": "none",
                                               "flow": "xtls-rprx-vision"}]}]},
            "streamSettings": {
                "network": "raw", "security": "reality",
                "realitySettings": {"serverName": dest, "fingerprint": "chrome",
                                    "publicKey": pub, "shortId": sid},
            },
        }],
    }
    kill_loop()
    time.sleep(1)
    open("/tmp/porttest.json", "w").write(json.dumps(server))
    open("/tmp/portcli.json", "w").write(json.dumps(client))
    sh("docker cp /tmp/porttest.json remnanode:/tmp/porttest.json", 20)
    sh("docker cp /tmp/portcli.json remnanode:/tmp/portcli.json", 20)
    sh("docker exec -d remnanode sh -c '/usr/local/bin/rw-core run -c /tmp/porttest.json > /tmp/porttest.log 2>&1'", 15)
    listening = False
    for _ in range(12):
        if sh("ss -ltn | grep ':%s' || true" % PORT, 10).strip():
            listening = True
            break
        time.sleep(1)
    if not listening:
        results.append({"host": dest, "ok": False, "note": "инбаунд не поднялся на :%s" % PORT})
        log("❌ %s — инбаунд не поднялся" % dest)
        kill_loop()
        continue
    sh("docker exec -d remnanode sh -c '/usr/local/bin/rw-core run -c /tmp/portcli.json > /tmp/portcli.log 2>&1'", 15)
    socks = False
    for _ in range(8):
        if sh("ss -ltn | grep ':%s' || true" % LOCAL, 10).strip():
            socks = True
            break
        time.sleep(0.5)
    if not socks:
        time.sleep(2)
    # свежий vision+REALITY на :18443 рвёт первый хендшейк RST (es-1, проверено).
    # вторая попытка на той же петле проходит. ipify ещё и сам нестабилен.
    ip = ""
    body = ""
    for attempt in range(1, 4):
        body = sh(
            "curl -sS --max-time 12 -x socks5h://127.0.0.1:%s "
            "https://cp.cloudflare.com/cdn-cgi/trace" % LOCAL,
            20,
        ).strip()
        log("curl trace [%s]: %s" % (attempt, (body.replace("\n", " | ")[:140]) if body else "пусто"))
        for ln in body.splitlines():
            ln = ln.strip().strip("\r")
            if ln.startswith("ip="):
                cand = ln.split("=", 1)[1].strip()
                if re.fullmatch(r"\d{1,3}(?:\.\d{1,3}){3}", cand):
                    ip = cand
                    break
            elif re.fullmatch(r"\d{1,3}(?:\.\d{1,3}){3}", ln):
                ip = ln
                break
        if ip:
            break
        time.sleep(1)
    ok = bool(ip)
    if ip:
        note = "выход " + ip
    else:
        err = sh(
            "docker exec remnanode sh -c "
            "'grep -iE \"failed to|invalid|key share|rejected|reset by peer\" "
            "/tmp/portcli.log /tmp/porttest.log | tail -3'",
            20,
        )
        note = (err.strip().splitlines()[-1] if err.strip() else (body or "нет ответа"))[:180]
    log(("✅ " if ok else "❌ ") + dest + " — " + note)
    results.append({"host": dest, "ok": ok, "note": note})
    kill_loop()
    time.sleep(1)

kill_loop()
sh("docker exec remnanode sh -c 'rm -f /tmp/porttest.json /tmp/portcli.json /tmp/porttest.log /tmp/portcli.log'", 20)
print("@@JSON@@" + json.dumps({"results": results}, ensure_ascii=False), flush=True)
'''


def _verdict(d: dict) -> tuple[bool, str]:
    # Таблица из sni-choose: TLS13, HTTP2, код 200 или редирект на себя, 403 — мимо.
    # CDN режем отдельно: дока REALITY — dest за CF превращает ноду в дыру.
    why = []
    code = str(d.get("code") or "")
    if code == "403":
        why.append("код 403")
    elif code not in ("200", "301", "302", "303", "307", "308"):
        why.append(f"код {code or '—'}")
    if d.get("http_version") != "2":
        why.append("нет HTTP/2")
    redir = (d.get("redirect") or "").strip()
    if redir and not _redirect_to_self(str(d.get("host") or ""), redir):
        why.append("редирект чужой")
    if not d.get("tls13") and not (d.get("kex") or "").strip():
        why.append("нет TLS1.3")
    if (d.get("cdn") or "0") not in ("0", ""):
        why.append("CDN")
    kex = (d.get("kex") or "").strip()
    if not kex:
        why.append("нет обмена ключами")
    elif "X25519" not in kex:
        why.append(f"обмен {kex}")
    if d.get("fails"):
        why.append(f"{d['fails']} отказов")
    if d.get("stalls"):
        why.append(f"{d['stalls']} залипаний")
    return (not why), ", ".join(why)


def _annotate(rows: list[dict]) -> list[dict]:
    out = []
    for d in rows:
        ok, why = _verdict(d)
        ms = d.get("connect_med")
        near = isinstance(ms, (int, float)) and ms <= NEAR_MS
        noisy = _is_noisy(str(d.get("host") or ""))
        item = dict(d)
        item["ok"] = ok
        item["why"] = why
        item["near"] = near
        item["noisy"] = noisy
        if ok and near:
            item["verdict"] = "годится · близко"
        elif ok:
            item["verdict"] = "годится"
        else:
            item["verdict"] = why or "пропустить"
        out.append(item)
    out.sort(key=lambda x: (
        not x["ok"],
        x.get("noisy", False),
        not x.get("near", False),
        x.get("connect_med") if x.get("connect_med") is not None else 9999,
    ))
    return out


def _best_host(good: list[dict]) -> str | None:
    quiet = [x for x in good if not x.get("noisy")]
    pick = quiet or good
    return pick[0]["host"] if pick else None


def _upload_and_stream(
    *,
    host: str,
    ssh_port: int,
    username: str,
    auth_type: str,
    password: str | None,
    private_key: str | None,
    script: str,
    remote_name: str,
    timeout: float,
    cancel: threading.Event | None,
) -> Iterator[str | dict[str, Any]]:
    q: queue.Queue[str | dict[str, Any] | BaseException | None] = queue.Queue()

    class Tap(list):
        def append(self, line: object) -> None:
            super().append(line)
            if isinstance(line, str):
                q.put(line)

    def worker() -> None:
        payload: dict[str, Any] | None = None
        try:
            tap: list[str] = Tap()
            for client, priv in _open_priv(
                host=host,
                ssh_port=ssh_port,
                username=username,
                auth_type=auth_type,
                password=password,
                private_key=private_key,
                cancel=cancel,
                log=tap,
            ):
                q.put("✓ SSH готов, заливаю скрипт…")
                remote = f"/tmp/{remote_name}-{uuid.uuid4().hex[:8]}.py"
                sftp = client.open_sftp()
                try:
                    with sftp.file(remote, "w") as fh:
                        fh.write(script)
                finally:
                    sftp.close()
                q.put(f"→ на ноде python3 -u {remote_name}…")
                buf: list[str] = []
                try:
                    for line in _stream_priv_command(
                        client,
                        priv,
                        f"PYTHONUNBUFFERED=1 python3 -u {remote}",
                        timeout=timeout,
                        cancel=cancel,
                    ):
                        text = line.rstrip()
                        if text.startswith("@@LOG@@"):
                            q.put(text[7:])
                        elif text.startswith("@@JSON@@"):
                            payload = json.loads(text[8:])
                        elif text.strip():
                            buf.append(text)
                finally:
                    try:
                        _run_priv(client, priv, f"rm -f {remote}", timeout=15)
                    except Exception:
                        pass
                if payload is None and buf:
                    blob = "\n".join(buf)
                    if "@@JSON@@" in blob:
                        payload = json.loads(blob.split("@@JSON@@", 1)[1].strip())
                if payload is None:
                    raise DestPickError("нода не вернула результат скана")
                q.put(payload)
                return
        except BaseException as exc:
            q.put(exc)
        finally:
            q.put(None)

    threading.Thread(target=worker, daemon=True).start()
    quiet = 0
    ssh_up = False
    while True:
        if cancel is not None and cancel.is_set():
            raise DestPickError("отменено")
        try:
            item = q.get(timeout=1.0)
        except queue.Empty:
            quiet += 1
            if quiet in (3, 8) or quiet % 15 == 0:
                if ssh_up:
                    yield f"… скрипт на ноде молчит {quiet}с (curl / tls ping / петля)"
                else:
                    yield f"… жду ноду {host}:{ssh_port} уже {quiet}с (TCP/SSH ещё не ответил)"
            continue
        quiet = 0
        if isinstance(item, str) and ("SSH готов" in item or "на ноде python" in item):
            ssh_up = True
        if item is None:
            return
        if isinstance(item, BaseException):
            if isinstance(item, (HaproxyScriptError, DestPickError, RemnaScriptError)):
                raise item
            raise DestPickError(str(item) or type(item).__name__) from item
        yield item


def run_dest_scan(
    *,
    host: str,
    ssh_port: int,
    username: str,
    auth_type: str,
    password: str | None,
    private_key: str | None,
    ru_only: bool = True,
    scan_subnet: bool | None = None,
    extra: list[str] | None = None,
    limit: int = 45,
    country_code: str | None = None,
    node_name: str | None = None,
    cancel: threading.Event | None = None,
) -> Iterator[str | dict[str, Any]]:
    extra = [s.strip() for s in (extra or []) if s.strip()]
    cc = infer_country(country_code, node_name)
    if ru_only:
        seed_note = "режим: только РФ, короткий список гигантов, без /24"
    else:
        seed_note = (
            f"кандидаты под {cc} (+ общие *) — метод sni-choose"
            if cc else
            "страна ноды не задана, только общие доноры (*) и /24"
        )
    yield seed_note
    if ru_only:
        extra = list(dict.fromkeys(RU_SEED + extra))
        do_scan = False if scan_subnet is None else scan_subnet
    else:
        extra = list(dict.fromkeys(seeds_for_country(cc) + extra))
        do_scan = True if scan_subnet is None else scan_subnet
    script = (
        SCAN_PY.replace("__RU_ONLY__", "1" if ru_only else "0")
        .replace("__SCAN__", "1" if do_scan else "0")
        .replace("__CANDS__", json.dumps(extra))
        .replace("__LIMIT__", str(max(5, min(limit, 80))))
    )
    yield f"→ открываю SSH {username}@{host}:{ssh_port}…"
    for item in _upload_and_stream(
        host=host, ssh_port=ssh_port, username=username, auth_type=auth_type,
        password=password, private_key=private_key, script=script,
        remote_name="dest-scan", timeout=2400, cancel=cancel,
    ):
        if isinstance(item, dict):
            checked = _annotate(item.get("checked") or [])
            good = [x for x in checked if x["ok"]]
            yield {
                "own": item.get("own"),
                "subnet": item.get("subnet"),
                "alive443": item.get("alive443"),
                "found": len(item.get("found") or []),
                "checked": checked,
                "good": good,
                "best": _best_host(good),
                "ru_only": ru_only,
                "country": cc,
            }
        else:
            yield item


def run_dest_loopback(
    *,
    host: str,
    ssh_port: int,
    username: str,
    auth_type: str,
    password: str | None,
    private_key: str | None,
    dests: list[str],
    port: int = 18443,
    cancel: threading.Event | None = None,
) -> Iterator[str | dict[str, Any]]:
    dests = [s.strip() for s in dests if s.strip()][:12]
    if not dests:
        raise DestPickError("нет доменов для петли")
    script = (
        LOOPBACK_PY.replace("__DESTS__", json.dumps(dests))
        .replace("__PORT__", str(int(port)))
    )
    yield f"→ открываю SSH {username}@{host}:{ssh_port} для петли…"
    for item in _upload_and_stream(
        host=host, ssh_port=ssh_port, username=username, auth_type=auth_type,
        password=password, private_key=private_key, script=script,
        remote_name="dest-loop", timeout=900, cancel=cancel,
    ):
        if isinstance(item, dict):
            if item.get("error"):
                raise DestPickError(str(item["error"]))
            rows = item.get("results") or []
            winners = [r for r in rows if r.get("ok")]
            yield {"results": rows, "winners": winners, "best": winners[0]["host"] if winners else None}
        else:
            yield item
