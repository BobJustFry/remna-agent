"""Проба донора ИИ: один лёгкий запрос на сервис через SOCKS-вход донора.

Доноры пускают на порт 10443 только адреса нод из своего списка (allowlist),
адрес рагента туда не входит. Поэтому по умолчанию ходим «через ноду»: SSH на
ноду, которая у донора в списке, и оттуда канал direct-tcpip на донор:10443.
Ноду при этом не трогаем — это обычный проброс порта, без команд на ней.
Режим `direct` — если адрес рагента когда-нибудь пустят на доноры.

Сырые исходы одного запроса (RAW_*), и как они читаются:
  ok, challenge                     → донор жив для сервиса (челлендж Cloudflare —
                                      защита от ботов, а не отказ по стране);
  blocked                           → выход донора у сервиса в отказе;
  timeout                           → тишина: донор или его выход мёртв;
  unreachable                       → до порта донора не достучаться с ноды-прыжка;
  auth                              → донор не принял логин/пароль;
  socks_fail                        → донор не смог соединиться с сервисом;
  error, hop_failed, no_hop         → не знаем (не повод переключать).

Всё здесь блокирующее — звать из asyncio.to_thread. Секреты (логин/пароль
донора, SSH ноды) не попадают ни в журнал, ни в заметки: заметки чистятся scrub().
"""
from __future__ import annotations

import random
import re
import socket
import threading
import time
import urllib.parse
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterator

from app.services import ai_access as aa

RAW_OK = "ok"
RAW_CHALLENGE = "challenge"
RAW_BLOCKED = "blocked"
RAW_TIMEOUT = "timeout"
RAW_UNREACHABLE = "unreachable"
RAW_AUTH = "auth"
RAW_SOCKS_FAIL = "socks_fail"
RAW_ERROR = "error"
RAW_HOP_FAILED = "hop_failed"
RAW_NO_HOP = "no_hop"

ALIVE_RAW = frozenset({RAW_OK, RAW_CHALLENGE})
FAIL_RAW = frozenset({RAW_BLOCKED, RAW_TIMEOUT, RAW_UNREACHABLE, RAW_AUTH, RAW_SOCKS_FAIL})
# остальное (error, hop_failed, no_hop) — «не знаем»

URLS = {
    "claude": "https://api.anthropic.com/v1/models",
    "openai": "https://api.openai.com/v1/models",
    "gemini_nokey": "https://ai.google.dev/",
    "elevenlabs": "https://elevenlabs.io/",
    "tiktok": "https://www.tiktok.com/login",
}


@dataclass
class Hop:
    """Нода-прыжок. Секреты только в памяти, в repr не попадают."""

    name: str
    host: str
    port: int
    user: str
    auth_type: str
    password: str | None = None
    private_key: str | None = None

    def __repr__(self) -> str:  # не светим секреты при логировании объекта
        return f"Hop({self.name!r})"


@dataclass
class Creds:
    user: str
    password: str

    def __repr__(self) -> str:
        return "Creds(***)"


def scrub(text: str, secrets: list[str]) -> str:
    out = str(text or "")
    for s in secrets:
        if s and len(s) >= 3:
            out = out.replace(s, "***")
            out = out.replace(urllib.parse.quote(s, safe=""), "***")
    # на всякий случай: user:pass@ в любых адресах
    out = re.sub(r"(socks5h?|https?)://[^@\s/]+@", r"\1://***@", out)
    # ошибки панели могут эхом вернуть кусок конфига или заголовок
    out = re.sub(r'(\\?"(?:pass|password|privateKey|token|secret)\\?"\s*:\s*\\?")[^"\\]*', r"\1***", out,
                 flags=re.I)
    out = re.sub(r"(Bearer\s+)\S+", r"\1***", out, flags=re.I)
    return out[:160]


# ── мост: локальный порт → (SSH-канал | прямой сокет) → донор ───────────────

class _Bridge:
    """Слушает 127.0.0.1:<порт>; каждое соединение curl уходит новым каналом к донору."""

    def __init__(self, opener: Callable[[], object]) -> None:
        self._open = opener
        self.errors: list[BaseException] = []
        self._srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._srv.bind(("127.0.0.1", 0))
        self._srv.listen(8)
        self._srv.settimeout(0.5)
        self.port = self._srv.getsockname()[1]
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        t = threading.Thread(target=self._accept, daemon=True, name="donor-bridge")
        t.start()
        self._threads.append(t)

    def _accept(self) -> None:
        while not self._stop.is_set():
            try:
                c, _ = self._srv.accept()
            except (socket.timeout, OSError):
                continue
            t = threading.Thread(target=self._handle, args=(c,), daemon=True)
            t.start()
            self._threads.append(t)

    def _handle(self, c: socket.socket) -> None:
        try:
            up = self._open()
        except BaseException as exc:  # noqa: BLE001
            self.errors.append(exc)
            c.close()
            return

        def pump(src, dst) -> None:
            try:
                while not self._stop.is_set():
                    data = src.recv(65536)
                    if not data:
                        break
                    dst.sendall(data)
            except Exception:  # noqa: BLE001
                pass
            finally:
                for x in (src, dst):
                    try:
                        x.close()
                    except Exception:  # noqa: BLE001
                        pass

        t = threading.Thread(target=pump, args=(up, c), daemon=True)
        t.start()
        pump(c, up)

    def close(self) -> None:
        self._stop.set()
        try:
            self._srv.close()
        except OSError:
            pass


def classify_open_error(exc: BaseException) -> str:
    """Почему не открылся канал к донору: прыжок не пускает или донор не отвечает."""
    try:
        import paramiko

        if isinstance(exc, paramiko.ChannelException):
            # 1 = ADMINISTRATIVELY_PROHIBITED (на ноде выключен проброс) — дело в прыжке
            return RAW_HOP_FAILED if getattr(exc, "code", None) == 1 else RAW_UNREACHABLE
        if isinstance(exc, paramiko.SSHException):
            return RAW_UNREACHABLE if "timeout" in str(exc).lower() else RAW_HOP_FAILED
    except ImportError:  # pragma: no cover
        pass
    if isinstance(exc, (ConnectionRefusedError, socket.timeout, TimeoutError, ConnectionResetError)):
        return RAW_UNREACHABLE
    if isinstance(exc, OSError):
        return RAW_UNREACHABLE
    return RAW_ERROR


def _recv_exact(ch, n: int) -> bytes:
    buf = b""
    while len(buf) < n:
        part = ch.recv(n - len(buf))
        if not part:
            raise ConnectionResetError("closed")
        buf += part
    return buf


def socks_auth(ch, creds: Creds, timeout: float) -> str:
    """Приветствие SOCKS5 + логин. Без CONNECT — к сервисам не ходим."""
    ch.settimeout(timeout)
    try:
        ch.sendall(b"\x05\x01\x02")
        r = _recv_exact(ch, 2)
        if r[0] != 5:
            return RAW_SOCKS_FAIL
        if r[1] == 0x00:
            return RAW_OK              # пускает без логина — тоже рабочий вход
        if r[1] != 0x02:
            return RAW_AUTH
        u, p = creds.user.encode(), creds.password.encode()
        ch.sendall(bytes([1, len(u)]) + u + bytes([len(p)]) + p)
        r = _recv_exact(ch, 2)
        return RAW_OK if r[1] == 0 else RAW_AUTH
    except (socket.timeout, TimeoutError):
        return RAW_TIMEOUT
    except Exception:  # noqa: BLE001 — оборвал соединение посреди рукопожатия
        return RAW_SOCKS_FAIL


@contextmanager
def _opener(hop: Hop | None, address: str, port: int, connect_timeout: float) -> Iterator[Callable[[], object]]:
    if hop is None:
        yield lambda: socket.create_connection((address, port), timeout=connect_timeout)
        return
    from app.services.ssh_client import ssh_connect

    with ssh_connect(host=hop.host, port=hop.port, username=hop.user, auth_type=hop.auth_type,
                     password=hop.password, private_key=hop.private_key, timeout=15.0) as client:
        tr = client.get_transport()
        if tr is None:
            raise OSError("ssh transport closed")
        yield lambda: tr.open_channel("direct-tcpip", (address, port), ("127.0.0.1", 0),
                                      timeout=connect_timeout)


# ── запросы к сервисам: ровно один на сервис ────────────────────────────────

def _creq(method: str, url: str, proxy: str, timeout: float, **kw) -> aa.Resp:
    from curl_cffi import requests as creq

    r = creq.request(method, url, impersonate="chrome", timeout=timeout,
                     proxies={"http": proxy, "https": proxy},
                     headers={**aa.HEADERS, **kw.pop("headers", {})}, **kw)
    return aa._to_resp(r)


def judge_elevenlabs(r: aa.Resp) -> aa.Verdict:
    loc = r.header("location").lower()
    if 300 <= r.code < 400 and ("help.elevenlabs.io" in loc or "restricted" in loc):
        return aa.Verdict(aa.BLOCKED, f"{r.code} → restricted countries")
    if aa.is_challenge(r):
        return aa.Verdict(aa.CHALLENGE, f"{r.code} челлендж Cloudflare")
    if r.code in (200, 204, 404) or 300 <= r.code < 400:
        return aa.Verdict(aa.OK, str(r.code))
    if r.code in (403, 451):
        return aa.Verdict(aa.BLOCKED, str(r.code))
    return aa.Verdict(aa.ERROR, str(r.code))


def judge_gemini_nokey(r: aa.Resp) -> aa.Verdict:
    """Без ключа отказ по стране не виден — только «выход живой и Google отвечает»."""
    if r.code == 429 or "/sorry/" in r.url:
        return aa.Verdict(aa.CHALLENGE, f"{r.code} капча Google")
    kw = aa._kw(r)
    if r.code in (403, 451) or (kw and "available-regions" not in r.url):
        return aa.Verdict(aa.BLOCKED, f"{r.code}" + (f" «{kw}»" if kw else ""))
    if r.code == 200:
        return aa.Verdict(aa.OK, "200 (без ключа: только доступность)")
    return aa.Verdict(aa.ERROR, str(r.code))


def request_service(service: str, proxy: str, timeout: float, gemini_key: str = "") -> aa.Verdict:
    if service == "claude":
        return aa.judge_anthropic_api(_creq("GET", URLS["claude"], proxy, timeout))
    if service == "openai":
        return aa.judge_openai_api(_creq("GET", URLS["openai"], proxy, timeout))
    if service == "gemini":
        if gemini_key:
            body = {"contents": [{"parts": [{"text": "Reply with the single word: pong"}]}],
                    "generationConfig": {"maxOutputTokens": 5}}
            # ключ — в заголовке, не в адресе
            return aa.judge_gemini_api(_creq("POST", aa.GEMINI_API_URL, proxy, timeout, json=body,
                                             headers={"x-goog-api-key": gemini_key}))
        return judge_gemini_nokey(_creq("GET", URLS["gemini_nokey"], proxy, timeout,
                                        cookies=aa.GOOGLE_COOKIES))
    if service == "elevenlabs":
        return judge_elevenlabs(_creq("GET", URLS["elevenlabs"], proxy, timeout, allow_redirects=False))
    if service == "tiktok":
        return aa.judge_tiktok_web(_creq("GET", URLS["tiktok"], proxy, max(timeout, aa.TT_TIMEOUT),
                                         allow_redirects=True, max_redirects=10))
    return aa.Verdict(aa.ERROR, f"неизвестный сервис {service}")


_VERDICT_RAW = {aa.OK: RAW_OK, aa.CHALLENGE: RAW_CHALLENGE, aa.BLOCKED: RAW_BLOCKED,
                aa.TIMEOUT: RAW_TIMEOUT, aa.ERROR: RAW_ERROR}


def _exc_raw(exc: BaseException, bridge: _Bridge) -> tuple[str, str]:
    if bridge.errors:
        kind = classify_open_error(bridge.errors[-1])
        return kind, f"канал к донору: {type(bridge.errors[-1]).__name__}"
    v = aa.classify_exception(exc)
    if v.verdict == aa.TIMEOUT:
        return RAW_TIMEOUT, v.note
    msg = str(exc)
    if "curl: (97)" in msg or "SOCKS" in msg.upper():
        return RAW_SOCKS_FAIL, v.note
    return RAW_ERROR, v.note


def probe(hop: Hop | None, address: str, port: int, creds: Creds, services: list[str], *,
          timeout: float = 20.0, gemini_key: str = "", gap: tuple[float, float] = (3.0, 10.0),
          sleep: Callable[[float], None] = time.sleep) -> dict[str, tuple[str, str]]:
    """{сервис: (сырой исход, заметка)}. Один запрос на сервис, паузы со случайным разбросом."""
    secrets = [creds.user, creds.password, gemini_key]
    if hop is not None:
        secrets += [hop.password or ""]
    out: dict[str, tuple[str, str]] = {}

    def all_(raw: str, note: str) -> dict[str, tuple[str, str]]:
        return {s: (raw, scrub(note, secrets)) for s in services}

    try:
        with _opener(hop, address, port, connect_timeout=min(timeout, 15.0)) as open_ch:
            # 1) порт донора и логин — без запросов к сервисам
            try:
                ch = open_ch()
            except BaseException as exc:  # noqa: BLE001
                return all_(classify_open_error(exc), f"нет соединения с донором: {type(exc).__name__}")
            try:
                pre = socks_auth(ch, creds, timeout=min(timeout, 15.0))
            finally:
                try:
                    ch.close()
                except Exception:  # noqa: BLE001
                    pass
            if pre != RAW_OK:
                note = {RAW_AUTH: "донор не принял логин", RAW_TIMEOUT: "порт открыт, SOCKS молчит",
                        RAW_SOCKS_FAIL: "SOCKS оборвал рукопожатие"}.get(pre, pre)
                return all_(pre, note)
            # 2) по одному запросу на сервис
            bridge = _Bridge(open_ch)
            try:
                proxy = (f"socks5h://{urllib.parse.quote(creds.user, safe='')}:"
                         f"{urllib.parse.quote(creds.password, safe='')}@127.0.0.1:{bridge.port}")
                for i, svc in enumerate(services):
                    if i:
                        sleep(random.uniform(*gap))
                    bridge.errors.clear()
                    try:
                        v = request_service(svc, proxy, timeout, gemini_key)
                        out[svc] = (_VERDICT_RAW.get(v.verdict, RAW_ERROR), scrub(v.note, secrets))
                    except Exception as exc:  # noqa: BLE001
                        raw, note = _exc_raw(exc, bridge)
                        out[svc] = (raw, scrub(note, secrets))
            finally:
                bridge.close()
    except BaseException as exc:  # noqa: BLE001 — SSH до прыжка не поднялся
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        if out:
            for s in services:
                out.setdefault(s, (RAW_ERROR, "прервано"))
            return out
        return all_(RAW_HOP_FAILED if hop is not None else RAW_ERROR,
                    f"прыжок {hop.name if hop else '—'}: {type(exc).__name__}")
    return out


# ── фаервол донора (только чтение) ──────────────────────────────────────────

FW_CMD = ("iptables-save 2>/dev/null; echo '@@IPSET'; ipset list 2>/dev/null; "
          "echo '@@NFT'; nft list ruleset 2>/dev/null; true")


def read_firewall(hop: Hop, timeout: float = 30.0) -> str:
    """Снять правила фаервола донора по SSH. Ничего не меняет."""
    from app.services.ssh_client import run_command, ssh_connect

    with ssh_connect(host=hop.host, port=hop.port, username=hop.user, auth_type=hop.auth_type,
                     password=hop.password, private_key=hop.private_key, timeout=15.0) as client:
        _code, out, _err = run_command(client, FW_CMD, timeout=timeout)
    return out
