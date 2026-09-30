"""Доступ к ИИ-сервисам через каждый хост подписки.

Значок 🧠 в имени хоста — обещание клиенту, что через этот хост открываются
нейросети. Держится оно на цепочке, где рвётся всё что угодно: у ноды пропал
интерфейс WARP (запросы к ИИ виснут), пропало правило маршрутизации (ИИ идёт
с адреса дата-центра и получает 403), Cloudflare сменил точку выхода WARP на
страну из отказа. Снаружи ни одна из этих поломок не видна — клиент просто
уходит с хоста. Поэтому проверяем сами, по расписанию.

Методика та же, что у разового замера 29–30.09:

* для каждого хоста берём его профиль из подписки в формате XRAY_JSON и
  поднимаем **отдельный процесс** xray-клиента с этим профилем и socks-входом
  на свободном локальном порту; после проверки процесс гасим, каталог удаляем;
* запросы идут через `curl_cffi` с отпечатком Chrome — голый TLS-клиент
  Cloudflare режет 403 на любой ноде, даже рабочей;
* вердикт выносим по признакам, а не по коду: финальный URL после редиректов,
  JSON-ошибка API, маркеры отказа на разных языках. Челлендж Cloudflare —
  отдельный статус, это не отказ по стране;
* повторяем до трёх раз, пока не получим определённый ответ (ok/blocked):
  вялые ноды дают то таймаут, то 200.
"""
from __future__ import annotations

import asyncio
import html
import json
import logging
import os
import random
import re
import shutil
import socket
import subprocess
import tempfile
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AiCheck, AiRun, AppSetting
from app.services.crypto import decrypt_secret
from app.services import subwatch
from app.services.telegram import send_message

log = logging.getLogger(__name__)

CONFIG_KEY = "ai_access_config"
GEMINI_KEY_KEY = "ai_access_gemini_key"

DEFAULTS = {
    "enabled": True,         # владелец просил именно автоматическую проверку
    "interval_hours": 12,
    "sub_url": "",           # пусто — адрес из следилки за подпиской
    "concurrency": 2,        # хостов одновременно: ноды не долбим
    "exclude": "",           # подстроки имён хостов через запятую
    "notify": True,
    "chat_id": "",           # пусто — тот же чат, что у следилки
    "keep_days": 45,
}

# Вердикты. Порядок важен только для сортировки в сводке.
OK = "ok"
BLOCKED = "blocked"
CHALLENGE = "challenge"
TIMEOUT = "timeout"
ERROR = "error"
DEFINITE = (OK, BLOCKED)

SERVICES: list[tuple[str, str]] = [
    ("chatgpt_web", "ChatGPT"),
    ("openai_api", "OpenAI API"),
    ("claude_web", "Claude"),
    ("anthropic_api", "Anthropic API"),
    ("gemini_web", "Gemini"),
    ("gemini_api", "Gemini API"),     # только если в настройках есть ключ
]
SERVICE_NAMES = dict(SERVICES)

URLS = {
    "trace_direct": "https://www.cloudflare.com/cdn-cgi/trace",
    # chatgpt.com на нодах уходит в WARP по правилу для доменов ИИ — его trace
    # и есть «выход ИИ». www.cloudflare.com идёт напрямую — это выход ноды.
    "trace_ai": "https://chatgpt.com/cdn-cgi/trace",
    "chatgpt_web": "https://chatgpt.com/",
    "openai_api": "https://api.openai.com/v1/models",
    "claude_web": "https://claude.ai/",
    "anthropic_api": "https://api.anthropic.com/v1/models",
    "gemini_web": "https://gemini.google.com/app",
}
GEMINI_GEN_URL = ("https://gemini.google.com/_/BardChatUi/data/"
                  "assistant.lamda.BardFrontendService/StreamGenerate")
# Алиас на текущую быструю модель: конкретные имена Google меняет раз в полгода.
GEMINI_API_MODEL = "gemini-flash-latest"
GEMINI_API_URL = ("https://generativelanguage.googleapis.com/v1beta/models/"
                  f"{GEMINI_API_MODEL}:generateContent")

HEADERS = {"Accept-Language": "en-US,en;q=0.9"}
# Согласие на cookies: без него Google отдаёт страницу consent вместо Gemini.
GOOGLE_COOKIES = {"SOCS": "CAESEwgDEgk0ODE3Nzk3MjQaAmVuIAEaBgiA_LyaBg", "CONSENT": "YES+"}

XRAY_BIN = os.environ.get("XRAY_BIN", "xray")
REQ_TIMEOUT = 20.0
GEN_TIMEOUT = 45.0
TRIES = 3
RETRY_PAUSE = 3.0
WARMUP_TRIES = 5
# Потолок на хост. Нода, где правило на ИИ есть, а интерфейса WARP нет, вешает
# каждый запрос к ИИ до таймаута — без потолка один такой хост растянул бы
# прогон на десяток минут.
HOST_BUDGET_S = 300.0
START_DELAY_S = 300.0
TICK_S = 600.0
FAILED_RETRY_AFTER = timedelta(hours=1)
MAX_BODY_KEEP = 20000

# Маркеры отказа по стране — из разового замера, на языках всех наших выходов.
_KW = [
    r"not (?:currently )?(?:available|supported)[^.]{0,60}(?:country|region|territor|location)",
    r"(?:country|region|territor|location)[^.]{0,40}(?:not (?:currently )?(?:available|supported)|unsupported)",
    r"unsupported_country", r"app-unavailable-in-region", r"unavailable[- ]in[- ](?:your )?region",
    r"user location is not supported", r"request not allowed",
    r"недоступ", r"не поддерживается", r"nicht verfügbar", r"nicht unterstützt", r"pas disponible",
    r"n.est pas (?:encore )?disponible", r"no (?:está )?disponible", r"non (?:è )?disponibile",
    r"niedostępn", r"nie jest dostępn", r"ei ole saatavilla", r"inte tillgänglig", r"niet beschikbaar",
    r"kullanılamıyor", r"desteklenmiyor", r"не е наличн", r"nu este disponibil",
    r"není k dispozici", r"ikke tilgængelig", r"ikke tilgjengelig", r"δεν είναι διαθέσιμ",
    r"ekki í boði", r"nav pieejam", r"pole saadaval", r"nuk është i disponueshëm", r"қолжетімсіз",
    r"isn.t supported in your country", r"isn.t available in your country",
]
KW_RE = re.compile("|".join(_KW), re.I)
CHAL_RE = re.compile(
    r"just a moment|challenge-platform|cf-chl|attention required|verify you are human|/cdn-cgi/challenge",
    re.I,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def now() -> datetime:
    return _now()


# ── разбор ответов ───────────────────────────────────────────────────────────

@dataclass
class Resp:
    """Всё, что нужно для вердикта, без привязки к HTTP-клиенту — ради тестов."""
    code: int
    url: str
    text: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    history: list[str] = field(default_factory=list)   # URL до редиректов

    def header(self, name: str) -> str:
        return self.headers.get(name.lower(), "") or ""


@dataclass
class Verdict:
    verdict: str
    note: str = ""


def visible(body: str) -> str:
    """Текст страницы без скриптов и разметки — маркеры ищем в том, что видит человек."""
    b = re.sub(r"(?is)<(script|style|noscript|svg)[^>]*>.*?</\1>", " ", body)
    b = re.sub(r"(?s)<[^>]+>", " ", b)
    return re.sub(r"\s+", " ", html.unescape(b)).strip()


def _title(body: str) -> str:
    m = re.search(r"(?is)<title[^>]*>(.*?)</title>", body)
    return html.unescape(m.group(1)).strip()[:120] if m else ""


def _is_html(r: Resp) -> bool:
    return "html" in r.header("content-type") or "<html" in r.text[:500].lower()


def is_challenge(r: Resp) -> bool:
    if r.header("cf-mitigated").lower() == "challenge":
        return True
    return r.code in (403, 503) and bool(CHAL_RE.search(r.text[:MAX_BODY_KEEP]))


def _kw(r: Resp) -> str | None:
    """Маркер отказа в видимом тексте или в адресе, куда нас отправили."""
    text = visible(r.text) if _is_html(r) else r.text
    m = KW_RE.search(text) or KW_RE.search(r.url)
    return m.group(0).lower() if m else None


def _soft_error(r: Resp, extra: str = "") -> Verdict:
    note = f"{r.code}" + (f" {extra}" if extra else "")
    return Verdict(ERROR, note.strip())


def judge_chatgpt_web(r: Resp) -> Verdict:
    if is_challenge(r):
        return Verdict(CHALLENGE, f"{r.code} челлендж Cloudflare")
    vis = visible(r.text) if _is_html(r) else r.text
    # Так Cloudflare отвечает с российских адресов и адресов из отказа.
    if r.code == 403 and "unable to load site" in vis.lower():
        return Verdict(BLOCKED, "403 Unable to load site")
    kw = _kw(r)
    if kw:
        return Verdict(BLOCKED, f"{r.code} «{kw}»")
    if r.code == 200 and "chatgpt" in _title(r.text).lower():
        return Verdict(OK, "200")
    return _soft_error(r, _title(r.text)[:40])


def judge_openai_api(r: Resp) -> Verdict:
    # Без ключа рабочий выход получает 401 «нет ключа», отказ по стране — 403.
    if r.code == 401:
        return Verdict(OK, "401 без ключа")
    if r.code == 403 and "unsupported_country" in r.text:
        return Verdict(BLOCKED, "403 unsupported_country")
    if is_challenge(r):
        return Verdict(CHALLENGE, f"{r.code} челлендж Cloudflare")
    kw = _kw(r) if r.code == 403 else None
    if kw:
        return Verdict(BLOCKED, f"403 «{kw}»")
    return _soft_error(r, r.text[:60].replace("\n", " "))


def judge_claude_web(r: Resp) -> Verdict:
    urls = [r.url, *r.history]
    if any("app-unavailable-in-region" in u for u in urls):
        return Verdict(BLOCKED, "→ app-unavailable-in-region")
    if is_challenge(r):
        return Verdict(CHALLENGE, f"{r.code} челлендж Cloudflare")
    host = urllib.parse.urlsplit(r.url).hostname or ""
    if r.code == 200 and (host == "claude.ai" or host.endswith(".claude.ai")):
        path = urllib.parse.urlsplit(r.url).path or "/"
        return Verdict(OK, f"200 {path}")
    kw = _kw(r)
    if kw:
        return Verdict(BLOCKED, f"{r.code} «{kw}»")
    return _soft_error(r, r.url[:60])


def judge_anthropic_api(r: Resp) -> Verdict:
    if r.code == 401 and "authentication_error" in r.text:
        return Verdict(OK, "401 без ключа")
    if r.code == 403 and not is_challenge(r):
        return Verdict(BLOCKED, "403 " + ("Request not allowed" if "not allowed" in r.text.lower()
                                          else r.text[:40].replace("\n", " ")))
    if is_challenge(r):
        return Verdict(CHALLENGE, f"{r.code} челлендж Cloudflare")
    return _soft_error(r, r.text[:60].replace("\n", " "))


def judge_gemini_api(r: Resp) -> Verdict:
    body = r.text
    if r.code == 200:
        return Verdict(OK, "200")
    if "FAILED_PRECONDITION" in body or "location is not supported" in body.lower():
        return Verdict(BLOCKED, f"{r.code} location is not supported")
    if "API_KEY_INVALID" in body:
        return Verdict(ERROR, f"{r.code} ключ не принят")
    if r.code == 429:
        return Verdict(ERROR, "429 квота ключа")
    return _soft_error(r, body[:60].replace("\n", " "))


JUDGES: dict[str, Callable[[Resp], Verdict]] = {
    "chatgpt_web": judge_chatgpt_web,
    "openai_api": judge_openai_api,
    "claude_web": judge_claude_web,
    "anthropic_api": judge_anthropic_api,
    "gemini_api": judge_gemini_api,
}


# ── Gemini: страница открывается всем, решает ответ на запрос ────────────────

@dataclass
class GeminiPage:
    bl: str
    sid: str
    at: str | None
    cc: str | None     # страна, которую Google назначил по адресу (RUS, NLD…)


def gemini_page(body: str) -> GeminiPage | None:
    """Токены страницы, без которых запрос к модели не отправить."""
    bl = re.search(r'"cfb2h":"([^"]+)"', body)
    sid = re.search(r'"FdrFJe":"([^"]+)"', body)
    if not (bl and sid):
        return None
    at = re.search(r'"SNlM0e":"([^"]+)"', body)
    cc = re.search(r',2,1,200,"([A-Z]{3})"', body)
    return GeminiPage(bl=bl.group(1), sid=sid.group(1),
                      at=at.group(1) if at else None, cc=cc.group(1) if cc else None)


def judge_gemini_page(r: Resp) -> Verdict | None:
    """Вердикт по одной странице, если до запроса к модели дело не дойдёт."""
    if r.code == 429 or "/sorry/" in r.url or "unusual traffic" in r.text[:MAX_BODY_KEEP].lower():
        return Verdict(CHALLENGE, f"{r.code} капча Google")
    if gemini_page(r.text) is None:
        return _soft_error(r, "нет токенов страницы")
    return None


def judge_gemini_answer(body: str, page_cc: str | None = None) -> Verdict:
    """Ответ StreamGenerate. Открытая страница ещё ничего не значит: отказ по
    стране приходит только на сам запрос — ошибкой BardErrorInfo [1060]."""
    ans = re.search(r'\[\\"rc_[^\\"]+\\",\[\\"(.*?)\\"\]', body)
    if ans:
        loc = re.search(r'\[\\"([^\\"]+)\\",\\"SWML_DESCRIPTION[^\\"]*\\"', body)
        return Verdict(OK, "ответ получен" + (f", {loc.group(1)}" if loc else ""))
    bard = re.search(r'BardErrorInfo",\[(\d+)', body)
    if bard and bard.group(1) == "1060":
        return Verdict(BLOCKED, "ошибка 1060" + (f", страна {page_cc}" if page_cc else ""))
    err = re.search(r'\["wrb\.fr",null,null,null,null,\[(\d+)', body)
    if err:
        return Verdict(ERROR, f"ошибка {err.group(1)}" + (f"/{bard.group(1)}" if bard else ""))
    return Verdict(ERROR, "непонятный ответ")


def parse_trace(body: str) -> dict[str, str]:
    return dict(ln.split("=", 1) for ln in body.splitlines() if "=" in ln)


def classify_exception(exc: BaseException) -> Verdict:
    msg = str(exc)
    name = type(exc).__name__
    if "Timeout" in name or "curl: (28)" in msg or "timed out" in msg.lower():
        return Verdict(TIMEOUT, "нет ответа")
    short = re.sub(r"\s+", " ", msg)
    m = re.search(r"curl: \((\d+)\)[^.]*", short)
    return Verdict(ERROR, (m.group(0) if m else f"{name}: {short}")[:90])


# ── подписка → цели ─────────────────────────────────────────────────────────

# Служебные значки в именах хостов: флаг, статус, тип, значки 🎮🧠📱📺. Меняются
# при каждом переименовании — ключ истории без них, иначе смена 🧠 рвала бы
# историю ровно тогда, когда она нужнее всего.
_MARKERS = set("🟢🟩🔵🟡🔴🟠🟣⚪⚫🟥🟨🟦🟧🟪🟫⬜⬛📡📰🎮🧠📱📺⚡🔀") | {"\ufe0f", "\u200d"}


def host_key(remark: str) -> str:
    out = []
    for ch in remark:
        if ch in _MARKERS or 0x1F1E6 <= ord(ch) <= 0x1F1FF:   # regional indicators = флаги
            continue
        if unicodedata.category(ch) in ("Cc", "Cf"):
            continue
        out.append(ch)
    return re.sub(r"\s+", " ", "".join(out)).strip()[:200] or remark.strip()[:200]


@dataclass
class Target:
    name: str
    key: str
    outbounds: list[dict] | None = None
    error: str | None = None


def is_balancer(profile: dict) -> bool:
    """«АВТО»-профили собирают остальные хосты под балансировщик — их члены
    проверяются по отдельности, а сам профиль ничего нового не скажет."""
    if (profile.get("routing") or {}).get("balancers"):
        return True
    tags = [o.get("tag", "") for o in profile.get("outbounds") or [] if isinstance(o, dict)]
    return sum(1 for t in tags if str(t).startswith("proxy")) > 1


def target_outbounds(profile: dict) -> list[dict]:
    """Outbound `proxy` хоста, цепочка его dialerProxy и freedom на всякий случай."""
    obs = [o for o in profile.get("outbounds") or [] if isinstance(o, dict)]
    by_tag = {o.get("tag"): o for o in obs}
    proxy = by_tag.get("proxy")
    if proxy is None:
        raise ValueError("в профиле нет outbound proxy")
    proxy = json.loads(json.dumps(proxy))
    ss = proxy.get("streamSettings") or {}
    xs = ss.get("xhttpSettings") or {}
    # extra из ссылки клиенты разворачивают сами; так конфиг понимает и ядро
    # постарше — ровно как в разовом замере.
    if isinstance(xs.get("extra"), dict):
        xs.update(xs.pop("extra"))
        ss["xhttpSettings"] = xs
    chain: list[dict] = []
    seen = {"proxy"}
    cur = proxy
    while True:
        tag = ((cur.get("streamSettings") or {}).get("sockopt") or {}).get("dialerProxy")
        if not tag or tag in seen:
            break
        nxt = by_tag.get(tag)
        if nxt is None:
            raise ValueError(f"dialerProxy {tag} не найден в профиле")
        seen.add(tag)
        chain.append(json.loads(json.dumps(nxt)))
        cur = nxt
    return [proxy, *chain, {"tag": "direct", "protocol": "freedom"}]


def build_targets(profiles: list[dict], exclude: str = "") -> list[Target]:
    skip = [s.strip().lower() for s in (exclude or "").split(",") if s.strip()]
    out: list[Target] = []
    seen: dict[str, int] = {}
    for p in profiles:
        if not isinstance(p, dict) or is_balancer(p):
            continue
        name = (p.get("remarks") or "").strip() or "без имени"
        if any(s in name.lower() for s in skip):
            continue
        key = host_key(name)
        seen[key] = seen.get(key, 0) + 1
        if seen[key] > 1:
            key = f"{key} ({seen[key]})"
        try:
            out.append(Target(name=name, key=key, outbounds=target_outbounds(p)))
        except ValueError as exc:
            out.append(Target(name=name, key=key, error=str(exc)))
    return out


SUB_UA = "v2rayNG/1.8.0"


def _get_json(url: str, ua: str) -> object | None:
    req = urllib.request.Request(url, headers={"User-Agent": ua})
    with urllib.request.urlopen(req, timeout=30) as resp:
        raw = resp.read(8 * 1024 * 1024)
    try:
        return json.loads(raw.decode("utf-8", errors="replace"))
    except ValueError:
        return None


def fetch_profiles(url: str) -> list[dict]:
    """Подписка в формате XRAY_JSON: у каждого хоста готовый клиентский профиль.

    Формат панель выбирает по User-Agent; если правила ответа настроены иначе —
    спрашиваем явный тип клиента `/json`.
    """
    url = (url or "").strip()
    if not url:
        raise RuntimeError("не задан адрес подписки (ни здесь, ни в следилке)")
    candidates = [url]
    if "?" not in url:
        candidates.append(url.rstrip("/") + "/json")
    last = "подписка не в формате XRAY_JSON"
    for u in candidates:
        try:
            data = _get_json(u, SUB_UA)
        except urllib.error.HTTPError as exc:
            last = f"подписка ответила {exc.code}"
            continue
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last = f"подписка недоступна: {getattr(exc, 'reason', exc)}"
            continue
        if isinstance(data, dict):
            data = [data]
        if isinstance(data, list) and any(isinstance(p, dict) and p.get("outbounds") for p in data):
            return [p for p in data if isinstance(p, dict)]
    raise RuntimeError(last)


# ── туннель: отдельный процесс xray на хост ─────────────────────────────────

class TunnelError(Exception):
    pass


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _log_tail(path: str, limit: int = 600) -> str:
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read()[-limit:].strip()
    except OSError:
        return ""


@contextmanager
def xray_tunnel(outbounds: list[dict]) -> Iterator[str]:
    """Поднять xray-клиент с socks на 127.0.0.1 и отдать адрес прокси.

    Каждому хосту — свой процесс, свой порт и свой временный каталог: упавший
    или зависший клиент одного хоста не влияет на соседние, а после выхода из
    блока от него не остаётся ни процесса, ни файлов.
    """
    binary = shutil.which(XRAY_BIN) or (XRAY_BIN if os.path.isfile(XRAY_BIN) else None)
    if not binary:
        raise TunnelError("xray не установлен в контейнере")
    port = _free_port()
    workdir = tempfile.mkdtemp(prefix="aiprobe-")
    cfg_path = os.path.join(workdir, "config.json")
    log_path = os.path.join(workdir, "xray.log")
    cfg = {
        "log": {"loglevel": "warning"},
        "inbounds": [{"tag": "socks", "listen": "127.0.0.1", "port": port,
                      "protocol": "socks", "settings": {"udp": False}}],
        "outbounds": outbounds,
    }
    with open(cfg_path, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh)
    logf = open(log_path, "wb")
    proc = None
    try:
        proc = subprocess.Popen([binary, "run", "-c", cfg_path], stdout=logf,
                                stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                start_new_session=True)
        deadline = time.monotonic() + 8
        while True:
            if proc.poll() is not None:
                raise TunnelError("xray не запустился: " + (_log_tail(log_path) or f"код {proc.returncode}"))
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=1):
                    break
            except OSError:
                if time.monotonic() > deadline:
                    raise TunnelError("xray не открыл порт: " + _log_tail(log_path))
                time.sleep(0.2)
        yield f"socks5h://127.0.0.1:{port}"
    finally:
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
        logf.close()
        shutil.rmtree(workdir, ignore_errors=True)


# ── запросы ─────────────────────────────────────────────────────────────────

def _to_resp(r) -> Resp:
    hist: list[str] = []
    try:
        hist = [str(h.url) for h in (r.history or [])][:10]
    except Exception:  # noqa: BLE001 — у разных версий curl_cffi история разная
        pass
    return Resp(code=int(r.status_code), url=str(r.url), text=r.text or "",
                headers={k.lower(): v for k, v in r.headers.items()}, history=hist)


def _get(url: str, proxy: str, timeout: float = REQ_TIMEOUT, **kw) -> Resp:
    from curl_cffi import requests as creq   # тяжёлый импорт — только когда проверяем

    r = creq.get(url, impersonate="chrome", timeout=timeout, allow_redirects=True,
                 max_redirects=10, proxies={"http": proxy, "https": proxy},
                 headers={**HEADERS, **kw.pop("headers", {})}, **kw)
    return _to_resp(r)


def _gemini_web(proxy: str) -> Verdict:
    from curl_cffi import requests as creq

    s = creq.Session(impersonate="chrome")
    s.proxies = {"http": proxy, "https": proxy}
    try:
        page = _to_resp(s.get(URLS["gemini_web"], headers=HEADERS, cookies=GOOGLE_COOKIES,
                              timeout=REQ_TIMEOUT))
        early = judge_gemini_page(page)
        if early:
            return early
        tok = gemini_page(page.text)
        assert tok is not None
        inner = json.dumps([["Reply with the single word: pong"], None, ["", "", ""]])
        data = {"f.req": json.dumps([None, inner])}
        if tok.at:
            data["at"] = tok.at
        url = (f"{GEMINI_GEN_URL}?bl={urllib.parse.quote(tok.bl)}&f.sid={urllib.parse.quote(tok.sid)}"
               f"&hl=en&_reqid={random.randint(10000, 99999)}&rt=c")
        r2 = s.post(url, data=data, timeout=GEN_TIMEOUT, headers={
            **HEADERS, "Origin": "https://gemini.google.com", "Referer": "https://gemini.google.com/",
            "X-Same-Domain": "1", "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"})
        if r2.status_code == 429:
            return Verdict(CHALLENGE, "429 капча Google")
        return judge_gemini_answer(r2.text or "", tok.cc)
    finally:
        s.close()


def _gemini_api(proxy: str, key: str) -> Verdict:
    body = {"contents": [{"parts": [{"text": "Reply with the single word: pong"}]}],
            "generationConfig": {"maxOutputTokens": 5}}
    from curl_cffi import requests as creq

    # Ключ — в заголовке, а не в адресе: адреса попадают в логи прокси и ошибок.
    r = creq.post(GEMINI_API_URL, impersonate="chrome", timeout=REQ_TIMEOUT,
                  proxies={"http": proxy, "https": proxy}, json=body,
                  headers={**HEADERS, "x-goog-api-key": key})
    return judge_gemini_api(_to_resp(r))


def _attempts(fn: Callable[[], Verdict], deadline: float) -> tuple[Verdict, int]:
    """До трёх попыток, пока не получим ok или blocked. Остальное — последний ответ."""
    v = Verdict(TIMEOUT, "не хватило времени на хост")
    n = 0
    for i in range(TRIES):
        if time.monotonic() > deadline:
            break
        n += 1
        try:
            v = fn()
        except Exception as exc:  # noqa: BLE001 — сеть ломается как угодно
            v = classify_exception(exc)
        if v.verdict in DEFINITE:
            break
        if i + 1 < TRIES:
            time.sleep(RETRY_PAUSE)
    return v, n


def probe_target(t: Target, gemini_key: str = "") -> dict:
    """Проверить один хост. Синхронно: зовётся из потока."""
    started = time.monotonic()
    deadline = started + HOST_BUDGET_S
    services = [k for k, _ in SERVICES if k != "gemini_api" or gemini_key]
    out: dict = {"host": t.name, "host_key": t.key, "tunnel": "error", "exit_ip": None,
                 "exit_country": None, "ai_ip": None, "ai_country": None, "ai_warp": None,
                 "results": {}, "error": None}

    def fill(verdict: str, note: str) -> None:
        for k in services:
            out["results"][k] = {"v": verdict, "note": note, "tries": 0}

    if t.error or not t.outbounds:
        out["error"] = t.error or "нет конфига"
        fill(ERROR, out["error"])
        out["duration_ms"] = int((time.monotonic() - started) * 1000)
        return out

    try:
        with xray_tunnel(t.outbounds) as proxy:
            trace: dict[str, str] | None = None
            last = Verdict(TIMEOUT, "нет ответа")
            for i in range(WARMUP_TRIES):
                try:
                    r = _get(URLS["trace_direct"], proxy, timeout=12)
                    if r.code == 200:
                        trace = parse_trace(r.text)
                        break
                    last = Verdict(ERROR, f"trace {r.code}")
                except Exception as exc:  # noqa: BLE001
                    last = classify_exception(exc)
                time.sleep(RETRY_PAUSE)
            if trace is None:
                out["tunnel"] = "down"
                out["error"] = f"туннель не поднялся: {last.note}"
                fill(last.verdict if last.verdict == TIMEOUT else ERROR, "туннель не поднялся")
                return out
            out["tunnel"] = "up"
            out["exit_ip"] = trace.get("ip")
            out["exit_country"] = trace.get("loc")

            for _ in range(2):
                try:
                    r = _get(URLS["trace_ai"], proxy)
                    if r.code == 200:
                        tr = parse_trace(r.text)
                        out["ai_ip"], out["ai_country"], out["ai_warp"] = (
                            tr.get("ip"), tr.get("loc"), tr.get("warp"))
                        break
                except Exception:  # noqa: BLE001 — выход ИИ покажем пустым
                    pass

            for key in services:
                if key == "gemini_web":
                    fn = lambda: _gemini_web(proxy)  # noqa: E731
                elif key == "gemini_api":
                    fn = lambda: _gemini_api(proxy, gemini_key)  # noqa: E731
                else:
                    judge = JUDGES[key]
                    url = URLS[key]
                    fn = lambda judge=judge, url=url: judge(_get(url, proxy))  # noqa: E731
                v, n = _attempts(fn, deadline)
                out["results"][key] = {"v": v.verdict, "note": v.note, "tries": n}
    except TunnelError as exc:
        out["tunnel"] = "error"
        out["error"] = str(exc)[:600]
        fill(ERROR, "xray не запустился")
    finally:
        out["duration_ms"] = int((time.monotonic() - started) * 1000)
    return out


# ── сравнение прогонов ──────────────────────────────────────────────────────

def diff_runs(prev: dict[str, dict], cur: dict[str, dict]) -> list[dict]:
    """Что поменялось между двумя прогонами: по сервисам и по стране выхода ИИ.

    На входе — {host_key: {"host", "results", "ai_country"}}. Хосты, которых
    не было в одном из прогонов, не сравниваем: это переименование или новый
    хост, а не поломка.
    """
    changes: list[dict] = []
    for key, row in cur.items():
        old = prev.get(key)
        if old is None:
            continue
        for svc, _ in SERVICES:
            a = ((old.get("results") or {}).get(svc) or {}).get("v")
            b = ((row.get("results") or {}).get(svc) or {}).get("v")
            if a and b and a != b:
                changes.append({"host": row.get("host") or key, "host_key": key,
                                "service": svc, "before": a, "after": b})
        a, b = old.get("ai_country"), row.get("ai_country")
        if a and b and a != b:
            changes.append({"host": row.get("host") or key, "host_key": key,
                            "service": "ai_exit", "before": a, "after": b})
    return changes


_BAD = (BLOCKED, CHALLENGE)


def notable(changes: list[dict]) -> tuple[list[dict], list[dict], list[dict]]:
    """Что стоит сообщения: ok ↔ отказ/челлендж и смена страны выхода.

    Переходы в таймаут и ошибку в сообщение не идут: это лежащая нода или
    моргнувшая сеть — про них тревожат другие проверки, а тут они только шум.
    """
    broke = [c for c in changes if c["service"] != "ai_exit" and c["before"] == OK and c["after"] in _BAD]
    fixed = [c for c in changes if c["service"] != "ai_exit" and c["before"] in _BAD and c["after"] == OK]
    moved = [c for c in changes if c["service"] == "ai_exit"]
    return broke, fixed, moved


def summary_text(changes: list[dict]) -> str | None:
    broke, fixed, moved = notable(changes)
    if not (broke or fixed or moved):
        return None
    esc = html.escape

    def lines(items: list[dict]) -> list[str]:
        by_host: dict[str, list[str]] = {}
        for c in items:
            by_host.setdefault(c["host"], []).append(
                f"{SERVICE_NAMES.get(c['service'], c['service'])} ({c['before']} → {c['after']})")
        return [f"• {esc(h)}: {esc(', '.join(v))}" for h, v in by_host.items()]

    out = ["🧠 <b>Доступ к ИИ: изменения</b>"]
    if broke:
        out += ["", "🔴 <b>Сломалось</b>", *lines(broke)]
    if fixed:
        out += ["", "🟢 <b>Починилось</b>", *lines(fixed)]
    if moved:
        out += ["", "🌍 <b>Сменилась страна выхода ИИ</b>",
                *[f"• {esc(c['host'])}: {esc(c['before'])} → {esc(c['after'])}" for c in moved]]
    return "\n".join(out)


# ── настройки ───────────────────────────────────────────────────────────────

async def _json_setting(db: AsyncSession, key: str, fallback: dict) -> dict:
    row = await db.get(AppSetting, key)
    if not row or not row.value_enc:
        return dict(fallback)
    try:
        data = json.loads(row.value_enc)
    except ValueError:
        return dict(fallback)
    return {**fallback, **data} if isinstance(data, dict) else dict(fallback)


async def get_config(db: AsyncSession) -> dict:
    return await _json_setting(db, CONFIG_KEY, DEFAULTS)


async def save_config(db: AsyncSession, cfg: dict) -> None:
    row = await db.get(AppSetting, CONFIG_KEY)
    if row is None:
        row = AppSetting(key=CONFIG_KEY, value_enc=None)
        db.add(row)
        await db.flush()
    row.value_enc = json.dumps(cfg, ensure_ascii=False)


async def gemini_key(db: AsyncSession) -> str:
    row = await db.get(AppSetting, GEMINI_KEY_KEY)
    if not row or not row.value_enc:
        return ""
    try:
        return decrypt_secret(row.value_enc)
    except Exception:  # noqa: BLE001 — сменился ключ шифрования
        return ""


async def effective_sub_url(db: AsyncSession, cfg: dict) -> str:
    own = (cfg.get("sub_url") or "").strip()
    if own:
        return own
    return ((await subwatch.get_config(db)).get("url") or "").strip()


# ── прогон ──────────────────────────────────────────────────────────────────

_active: asyncio.Task | None = None


class AlreadyRunning(Exception):
    pass


def is_running() -> bool:
    return _active is not None and not _active.done()


async def start_run(trigger: str) -> uuid.UUID:
    """Завести прогон и запустить его в фоне. Один прогон за раз."""
    global _active
    from app.db import SessionLocal

    if is_running():
        raise AlreadyRunning()
    async with SessionLocal() as db:
        run = AiRun(status="running", trigger=trigger, hosts_done=0)
        db.add(run)
        await db.commit()
        run_id = run.id
    _active = asyncio.create_task(_execute(run_id), name=f"ai-access-{trigger}")
    return run_id


async def _finish(run_id: uuid.UUID, **values) -> None:
    from app.db import SessionLocal

    async with SessionLocal() as db:
        await db.execute(update(AiRun).where(AiRun.id == run_id)
                         .values(finished_at=_now(), **values))
        await db.commit()


async def _rows_of(db: AsyncSession, run_id: uuid.UUID) -> dict[str, dict]:
    rows = await db.execute(select(AiCheck).where(AiCheck.run_id == run_id))
    return {c.host_key: {"host": c.host, "results": c.results or {}, "ai_country": c.ai_country}
            for c in rows.scalars()}


async def previous_done(db: AsyncSession, before: datetime | None = None) -> AiRun | None:
    q = select(AiRun).where(AiRun.status == "done")
    if before is not None:
        q = q.where(AiRun.started_at < before)
    row = await db.execute(q.order_by(AiRun.started_at.desc()).limit(1))
    return row.scalars().first()


async def _execute(run_id: uuid.UUID) -> None:
    from app.db import SessionLocal

    try:
        async with SessionLocal() as db:
            cfg = await get_config(db)
            key = await gemini_key(db)
            url = await effective_sub_url(db, cfg)
        profiles = await asyncio.to_thread(fetch_profiles, url)
        targets = build_targets(profiles, cfg.get("exclude") or "")
        if not targets:
            raise RuntimeError("в подписке не нашлось ни одного хоста")
        async with SessionLocal() as db:
            await db.execute(update(AiRun).where(AiRun.id == run_id).values(hosts_total=len(targets)))
            await db.commit()

        sem = asyncio.Semaphore(max(1, min(4, int(cfg.get("concurrency") or 2))))

        async def one(pos: int, t: Target) -> None:
            async with sem:
                res = await asyncio.to_thread(probe_target, t, key)
            async with SessionLocal() as db:
                db.add(AiCheck(
                    run_id=run_id, host=res["host"][:255], host_key=res["host_key"][:255],
                    position=pos,
                    tunnel=res["tunnel"], exit_ip=res["exit_ip"], exit_country=res["exit_country"],
                    ai_ip=res["ai_ip"], ai_country=res["ai_country"], ai_warp=res["ai_warp"],
                    duration_ms=res.get("duration_ms") or 0, results=res["results"],
                    error=res["error"]))
                await db.execute(update(AiRun).where(AiRun.id == run_id)
                                 .values(hosts_done=AiRun.hosts_done + 1))
                await db.commit()

        await asyncio.gather(*(one(i, t) for i, t in enumerate(targets)))

        async with SessionLocal() as db:
            run = await db.get(AiRun, run_id)
            prev = await previous_done(db, before=run.started_at if run else None)
            changes = diff_runs(await _rows_of(db, prev.id), await _rows_of(db, run_id)) if prev else []
        await _finish(run_id, status="done", changes=changes)
        log.info("ai-access: прогон готов, хостов %s, изменений %s", len(targets), len(changes))

        text = summary_text(changes)
        if text and cfg.get("notify"):
            async with SessionLocal() as db:
                err = await _notify(db, cfg, text)
            if err:
                log.warning("ai-access: уведомление не ушло: %s", err)
    except asyncio.CancelledError:
        await asyncio.shield(_finish(run_id, status="aborted", error="прогон прерван"))
        raise
    except Exception as exc:  # noqa: BLE001
        log.exception("ai-access: прогон упал")
        await _finish(run_id, status="failed", error=str(exc)[:500])


async def _notify(db: AsyncSession, cfg: dict, text: str) -> str | None:
    """Бот у нас один — тот, что настроен в следилке за подпиской."""
    token = await subwatch.bot_token(db)
    chat = (cfg.get("chat_id") or "").strip() or \
        ((await subwatch.get_config(db)).get("chat_id") or "").strip()
    if not token or not chat:
        return "бот или чат не настроены"
    return await asyncio.to_thread(send_message, token, chat, text)


async def shutdown() -> None:
    """Остановить идущий прогон при выключении рагента."""
    if is_running() and _active is not None:
        _active.cancel()
        try:
            await _active
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass


async def abort_stale(db: AsyncSession) -> None:
    """Прогон, начатый прошлым процессом, уже никто не допишет."""
    await db.execute(update(AiRun).where(AiRun.status == "running")
                     .values(status="aborted", finished_at=_now(),
                             error="рагент перезапустился во время прогона"))
    await db.commit()


async def prune(db: AsyncSession, keep_days: int) -> int:
    cutoff = _now() - timedelta(days=max(30, keep_days))
    res = await db.execute(delete(AiRun).where(AiRun.started_at < cutoff))
    await db.commit()
    return res.rowcount or 0


def is_due(last: AiRun | None, interval_hours: float, now: datetime) -> bool:
    if last is None:
        return True
    age = now - last.started_at
    if last.status in ("failed", "aborted"):
        return age >= min(FAILED_RETRY_AFTER, timedelta(hours=interval_hours))
    return age >= timedelta(hours=interval_hours)


async def run_ai_access() -> None:
    """Спит и раз в N часов проверяет. Выключенный — только спит."""
    from app.db import SessionLocal

    # Старые прогоны закрываем сразу, до паузы: иначе ручной запуск в первые
    # минуты после старта попал бы под эту зачистку.
    try:
        async with SessionLocal() as db:
            await abort_stale(db)
    except Exception:  # noqa: BLE001
        log.exception("ai-access: не смог закрыть старые прогоны")
    # Пауза после старта: выкладка не должна сразу же гнать прогон по всем нодам.
    await asyncio.sleep(START_DELAY_S)
    last_prune = 0.0
    while True:
        try:
            async with SessionLocal() as db:
                cfg = await get_config(db)
                if cfg.get("enabled") and not is_running():
                    row = await db.execute(select(AiRun).order_by(AiRun.started_at.desc()).limit(1))
                    last = row.scalars().first()
                    hours = max(1.0, float(cfg.get("interval_hours") or 12))
                    if is_due(last, hours, _now()):
                        await start_run("schedule")
                if time.monotonic() - last_prune > 86400:
                    await prune(db, int(cfg.get("keep_days") or 45))
                    last_prune = time.monotonic()
        except asyncio.CancelledError:
            raise
        except AlreadyRunning:
            pass
        except Exception:  # noqa: BLE001
            log.exception("ai-access: цикл упал")
        await asyncio.sleep(TICK_S)
