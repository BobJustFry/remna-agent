"""Доступность приложений через каждый хост подписки: ИИ-сервисы и TikTok.

Значки 🧠 и 📱 в имени хоста — обещание клиенту, что через этот хост
открываются нейросети и TikTok. Держится обещание про ИИ на цепочке, где
рвётся всё что угодно: у ноды пропал интерфейс WARP (запросы к ИИ виснут),
пропало правило маршрутизации (ИИ идёт с адреса дата-центра и получает 403),
Cloudflare сменил точку выхода WARP на страну из отказа. TikTok же метит
адреса выходов сам: помеченный адрес вместо сайта получает заслонку WAF.
Снаружи ни одна из этих поломок не видна — клиент просто уходит с хоста.
Поэтому проверяем сами, по расписанию.

Внутренние имена (модуль, таблицы ai_runs/ai_checks, API /api/ai-access)
остались от времён, когда проверялся только ИИ: их не переименовываем, чтобы
не ломать скрипты и историю.

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
import threading
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections.abc import Callable, Iterable, Iterator
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
    ("tiktok_web", "TikTok"),
]
SERVICE_NAMES = dict(SERVICES)

# Группы в матрице: нейросети отдельно от прочих приложений. Новое приложение —
# строка в SERVICES и его ключ сюда; всё, чего здесь нет, считается ИИ.
GROUP_AI = "ai"
GROUP_APPS = "apps"
GROUP_NAMES = {GROUP_AI: "ИИ", GROUP_APPS: "Приложения"}
SERVICE_GROUPS = {"tiktok_web": GROUP_APPS}


def service_group(key: str) -> str:
    return SERVICE_GROUPS.get(key, GROUP_AI)


def service_list(with_gemini_api: bool) -> list[dict]:
    """Колонки матрицы по порядку: сначала ИИ, потом приложения."""
    out = [{"key": k, "name": n, "group": service_group(k), "group_name": GROUP_NAMES[service_group(k)]}
           for k, n in SERVICES if k != "gemini_api" or with_gemini_api]
    order = list(GROUP_NAMES)
    return sorted(out, key=lambda s: order.index(s["group"]))

URLS = {
    "trace_direct": "https://www.cloudflare.com/cdn-cgi/trace",
    # chatgpt.com на нодах уходит в WARP по правилу для доменов ИИ — его trace
    # и есть «выход ИИ». www.cloudflare.com идёт напрямую — это выход ноды.
    "trace_ai": "https://chatgpt.com/cdn-cgi/trace",
    "chatgpt_web": "https://chatgpt.com/",
    "openai_api": "https://api.openai.com/v1/models",
    "claude_web": "https://claude.ai/",
    # Страницу claude.ai Cloudflare отдаёт curl-клиентам за управляемым
    # челленджем (с 23.10.2026 — с большинства адресов дата-центров и WARP),
    # хотя настоящий браузер проходит. Этот адрес не за челленджем.
    "claude_api_boot": "https://claude.ai/api/bootstrap",
    "anthropic_api": "https://api.anthropic.com/v1/models",
    "gemini_web": "https://gemini.google.com/app",
    # Страница входа, а не главная: владелец судит о доступе по ней — при блоке
    # TikTok страницу входа не отдаёт. Только GET, ничего не заполняем.
    "tiktok_web": "https://www.tiktok.com/login",
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
# Страница TikTok весит сотни КБ — вялой ноде нужно больше времени, как в замере 10.09.
TT_TIMEOUT = 30.0
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


# Страны, где Claude недоступен: там запасной признак «API отвечает» ничего не доказывает.
CLAUDE_UNSUPPORTED = frozenset({"RU", "BY", "CN", "IR", "KP", "CU", "SY"})


def judge_claude_fallback(page: Verdict, boot: Resp, country: str | None) -> Verdict:
    """Страница за челленджем Cloudflare — смотрим на /api/bootstrap.

    Челлендж — это защита от ботов, а не отказ по стране: настоящий браузер на
    том же выходе получает страницу входа (проверено 23.10.2026). Поэтому если
    JSON отдан, а страна выхода не из списка недоступных, засчитываем доступ.
    """
    if page.verdict != CHALLENGE:
        return page
    if (country or "").upper() in CLAUDE_UNSUPPORTED:
        return page
    ctype = boot.header("content-type").lower()
    if boot.code == 200 and "json" in ctype and '"statsig"' in boot.text[:4000]:
        return Verdict(OK, "200 /api/bootstrap (страница за капчей CF, ботам не отдаётся)")
    return page


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


# ── TikTok: страница входа ───────────────────────────────────────────────────
#
# Два признака из одного ответа на GET /login:
#
# * страница входа — по ней вердикт (так видит владелец в приложении и в
#   браузере): при блоке TikTok страницу входа не отдаёт;
# * размер — из замера 10.09: рабочий выход получает настоящую страницу в
#   сотни КБ, помеченный адрес — заслонку WAF ~1,5 КБ («Please wait…»,
#   slardar_us_waf). Заголовок `Server: TLB` ничего не значит: с ним приходит
#   и рабочая страница.
#
# Разошлись признаки — вердикт по странице входа, расхождение пишем в note.

TT_PAGE_MIN = 50_000     # настоящая страница — сотни КБ, заслонка — полторы
TT_WAF_RE = re.compile(r"slardar_us_waf|_wafchallengeid", re.I)
TT_WAIT_RE = re.compile(r"please wait\s*(?:\.\.\.|…)", re.I)
# Данные, которыми TikTok оживляет страницу: есть только у настоящей страницы.
TT_APP_RE = re.compile(r"__UNIVERSAL_DATA_FOR_REHYDRATION__|SIGI_STATE|webapp\.app-context")
TT_LOGIN_TITLE_RE = re.compile(r"log\s?in|sign\s?in|войти|вход", re.I)
TT_REGION_RE = re.compile(r'"region"\s*:\s*"([A-Z]{2})"')
# Регионы, где TikTok работает в урезанном виде: для России с 2022 года лента
# из старых и местных роликов, новое не грузится. Это не рабочий доступ.
TT_REGION_LIMITED = {"RU": "TikTok для России урезан"}


def _kb(size: int) -> str:
    return f"{size / 1024:.1f} КБ".replace(".", ",") if size < 10240 else f"{round(size / 1024)} КБ"


def tiktok_region(body: str) -> str | None:
    """Страна, которую TikTok назначил адресу, — из данных страницы."""
    at = body.find("app-context")
    m = TT_REGION_RE.search(body, at) if at >= 0 else TT_REGION_RE.search(body)
    return m.group(1) if m else None


def judge_tiktok_web(r: Resp) -> Verdict:
    size = len(r.text.encode("utf-8", errors="replace"))
    split = urllib.parse.urlsplit(r.url)
    host, path = split.hostname or "", split.path or "/"
    on_tiktok = host == "tiktok.com" or host.endswith(".tiktok.com")
    big = size >= TT_PAGE_MIN
    # Заслонка — маленькая страница с меткой WAF или с одним «Please wait…»
    # без данных приложения. В настоящей странице эти строки тоже встречаются.
    app = bool(TT_APP_RE.search(r.text))
    waf = not big and (bool(TT_WAF_RE.search(r.text)) or (not app and bool(TT_WAIT_RE.search(r.text))))
    server = r.header("server")
    tail = f", Server: {server}" if server else ""

    # Признак 1 — страница входа: настоящая страница TikTok по адресу /login.
    login = (r.code == 200 and on_tiktok and path.startswith("/login") and not waf
             and (bool(TT_LOGIN_TITLE_RE.search(_title(r.text))) or app))
    # Признак 2 — размер (big/waf выше): настоящая страница или заслонка.
    if login:
        region = tiktok_region(r.text)
        note = f"200 {path}, {_kb(size)}" + (f", регион {region}" if region else "")
        if region in TT_REGION_LIMITED:
            return Verdict(BLOCKED, f"{note} — {TT_REGION_LIMITED[region]}")
        if not big:
            note += " · расхождение: страница входа есть, а размер мал"
        return Verdict(OK, note)

    if waf:
        return Verdict(BLOCKED, f"{r.code} заслонка WAF, {_kb(size)}{tail}")
    kw = _kw(r) if not big or r.code != 200 else None
    if kw:
        return Verdict(BLOCKED, f"{r.code} «{kw}»")
    if r.code == 200 and on_tiktok and not path.startswith("/login"):
        # Увёл со страницы входа — её не отдают.
        note = f"200 → {path}, нет страницы входа, {_kb(size)}"
        if big:
            note += " · расхождение: по размеру страница настоящая"
        return Verdict(BLOCKED, note)
    if r.code in (403, 451):
        return Verdict(BLOCKED, f"{r.code} {_kb(size)}{tail}")
    return _soft_error(r, f"{_kb(size)} {_title(r.text)[:40]}".strip())


JUDGES: dict[str, Callable[[Resp], Verdict]] = {
    "chatgpt_web": judge_chatgpt_web,
    "openai_api": judge_openai_api,
    "claude_web": judge_claude_web,
    "anthropic_api": judge_anthropic_api,
    "gemini_api": judge_gemini_api,
    "tiktok_web": judge_tiktok_web,
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


class ProbeCancelled(Exception):
    """Проверку хоста отменили: результата нет, и записывать нечего."""


class CancelToken:
    """Флаг отмены для потока проверки и рубильник его процесса xray.

    Проверка идёт в потоке и висит в блокирующих запросах до таймаута. Чтобы
    отмена не ждала минуту, гасим xray: socks-порт закрывается, запрос падает
    сразу, поток видит флаг и выходит через обычную уборку туннеля — процесс
    добит, порт свободен, временный каталог удалён.
    """

    def __init__(self) -> None:
        self._event = threading.Event()
        self._lock = threading.Lock()
        self._proc: subprocess.Popen | None = None

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def cancel(self) -> None:
        self._event.set()
        with self._lock:
            proc = self._proc
        if proc is not None:
            _terminate(proc)

    def attach(self, proc: subprocess.Popen) -> None:
        with self._lock:
            self._proc = proc
        if self.cancelled:          # отменили, пока процесс запускался
            _terminate(proc)

    def detach(self) -> None:
        with self._lock:
            self._proc = None

    def check(self) -> None:
        if self.cancelled:
            raise ProbeCancelled()

    def wait(self, seconds: float) -> None:
        self._event.wait(seconds)


def _terminate(proc: subprocess.Popen) -> None:
    try:
        if proc.poll() is None:
            proc.terminate()
    except OSError:     # процесс успел выйти сам
        pass


def _pause(seconds: float, cancel: CancelToken | None = None) -> None:
    """Пауза между попытками. Отмена обрывает её сразу."""
    if cancel is None:
        time.sleep(seconds)
        return
    cancel.wait(seconds)
    cancel.check()


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
def xray_tunnel(outbounds: list[dict], cancel: CancelToken | None = None) -> Iterator[str]:
    """Поднять xray-клиент с socks на 127.0.0.1 и отдать адрес прокси.

    Каждому хосту — свой процесс, свой порт и свой временный каталог: упавший
    или зависший клиент одного хоста не влияет на соседние, а после выхода из
    блока от него не остаётся ни процесса, ни файлов. Так же уходит и отменённая
    проверка: `cancel` гасит процесс, остальное делает уборка ниже.
    """
    binary = shutil.which(XRAY_BIN) or (XRAY_BIN if os.path.isfile(XRAY_BIN) else None)
    if not binary:
        raise TunnelError("xray не установлен в контейнере")
    if cancel is not None:
        cancel.check()
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
        if cancel is not None:
            cancel.attach(proc)
        deadline = time.monotonic() + 8
        while True:
            # Раньше проверки процесса: погашенный отменой xray — не «не запустился».
            if cancel is not None:
                cancel.check()
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
        if cancel is not None:
            cancel.detach()
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


def _claude_web(proxy: str, country: str | None) -> Verdict:
    page = JUDGES["claude_web"](_get(URLS["claude_web"], proxy))
    if page.verdict != CHALLENGE:
        return page
    return judge_claude_fallback(page, _get(URLS["claude_api_boot"], proxy), country)


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


def _attempts(fn: Callable[[], Verdict], deadline: float,
              cancel: CancelToken | None = None) -> tuple[Verdict, int]:
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
        # Ответ, полученный под отмену, — это наш же погашенный xray, а не нода.
        if cancel is not None:
            cancel.check()
        if v.verdict in DEFINITE:
            break
        if i + 1 < TRIES:
            _pause(RETRY_PAUSE, cancel)
    return v, n


def probe_target(t: Target, gemini_key: str = "", cancel: CancelToken | None = None) -> dict:
    """Проверить один хост. Синхронно: зовётся из потока.

    Отмена через `cancel` обрывает проверку исключением ProbeCancelled:
    недомеренный хост не должен выглядеть сломанным.
    """
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

    def gate() -> None:
        if cancel is not None:
            cancel.check()

    try:
        with xray_tunnel(t.outbounds, cancel) as proxy:
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
                _pause(RETRY_PAUSE, cancel)
            gate()
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
                gate()
            gate()

            # Приложения — первыми: они идут мимо WARP, и нода, где запросы к ИИ
            # виснут до таймаута, не должна съесть их долю бюджета хоста.
            for key in sorted(services, key=lambda k: service_group(k) != GROUP_APPS):
                if key == "gemini_web":
                    fn = lambda: _gemini_web(proxy)  # noqa: E731
                elif key == "gemini_api":
                    fn = lambda: _gemini_api(proxy, gemini_key)  # noqa: E731
                elif key == "claude_web":
                    fn = lambda: _claude_web(proxy, out.get("ai_country"))  # noqa: E731
                else:
                    judge = JUDGES[key]
                    url = URLS[key]
                    tmo = TT_TIMEOUT if key == "tiktok_web" else REQ_TIMEOUT
                    fn = lambda judge=judge, url=url, tmo=tmo: judge(_get(url, proxy, timeout=tmo))  # noqa: E731
                v, n = _attempts(fn, deadline, cancel)
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


def followup_lines(prev_changes: list[dict] | None, rows: dict[str, dict]) -> list[str]:
    """Что стало с тем, что сломалось в прошлый раз: без этого тишина читается как «всё ещё сломано».

    rows — проверки текущего прогона. Таймаут и ошибка — «не удалось проверить»,
    а не «починилось» и не «осталось».
    """
    broke, _, _ = notable(prev_changes or [])
    if not broke:
        return []
    fixed = 0
    left: list[str] = []
    for c in broke:
        row = rows.get(c["host_key"])
        v = ((row or {}).get("results") or {}).get(c["service"], {}).get("v")
        if v == OK:
            fixed += 1
        elif v in _BAD:
            left.append(f"{c['host']} ({SERVICE_NAMES.get(c['service'], c['service'])} {v})")
    unknown = len(broke) - fixed - len(left)
    esc = html.escape
    line = f"починилось {fixed} из {len(broke)}"
    if unknown:
        line += f", не удалось проверить: {unknown}"
    if left:
        line += "; остались: " + ", ".join(left)
    return ["ℹ️ <b>Что было сломано в прошлый раз</b>", esc(line)]


def summary_text(changes: list[dict], followup: list[str] | None = None) -> str | None:
    broke, fixed, moved = notable(changes)
    if not (broke or fixed or moved or followup):
        return None
    esc = html.escape

    def lines(items: list[dict]) -> list[str]:
        by_host: dict[str, list[str]] = {}
        for c in items:
            by_host.setdefault(c["host"], []).append(
                f"{SERVICE_NAMES.get(c['service'], c['service'])} ({c['before']} → {c['after']})")
        return [f"• {esc(h)}: {esc(', '.join(v))}" for h, v in by_host.items()]

    out = ["🧠📱 <b>Доступность приложений: изменения</b>"]
    if broke:
        out += ["", "🔴 <b>Сломалось</b>", *lines(broke)]
    if fixed:
        out += ["", "🟢 <b>Починилось</b>", *lines(fixed)]
    if moved:
        out += ["", "🌍 <b>Сменилась страна выхода ИИ</b>",
                *[f"• {esc(c['host'])}: {esc(c['before'])} → {esc(c['after'])}" for c in moved]]
    if followup:
        out += ["", *followup]
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
#
# Прогон бывает полный (весь парк: по расписанию или кнопкой) и выборочный
# (хосты, которые выбрал человек). Исполнитель один: пока он занят, новые хосты
# встают в очередь идущего прогона, а полный прогон ждёт конца выборочного.
# Очередь живёт в памяти процесса — её и отдаём интерфейсу, поэтому спиннеры
# переживают обновление страницы и видны с любого устройства; копия состояния
# пишется в ai_runs.targets. Рестарт рагента очередь теряет: abort_stale
# закрывает прогон, зависших спиннеров не остаётся.

FULL = "full"
PARTIAL = "partial"

# Что с хостом внутри прогона.
QUEUED = "queued"
RUNNING = "running"
CANCELLING = "cancelling"    # отмену попросили, поток ещё выходит
DONE = "done"
CANCELLED = "cancelled"
MISSING = "missing"          # в свежей подписке такого хоста нет
ABORTED = "aborted"          # прогон оборвался раньше, чем дошёл до хоста
_WAITING = (QUEUED, RUNNING)
MISSING_NOTE = "хоста нет в подписке или он исключён в настройках"


class AlreadyRunning(Exception):
    pass


@dataclass
class _Item:
    key: str
    name: str
    position: int = 0
    state: str = QUEUED
    error: str | None = None
    target: Target | None = None
    token: CancelToken = field(default_factory=CancelToken)

    @property
    def shown_state(self) -> str:
        return CANCELLING if self.state == RUNNING and self.token.cancelled else self.state


class _Run:
    """Прогон в памяти: очередь хостов и состояние каждого."""

    def __init__(self, scope: str, trigger: str, keys: Iterable[str] = ()) -> None:
        self.id = uuid.uuid4()
        self.scope = scope
        self.trigger = trigger
        self.items: dict[str, _Item] = {}
        # Хосты свежей подписки; None — подписку ещё не забрали.
        self.targets: dict[str, tuple[int, Target]] | None = None
        # Полный прогон до подписки своих хостов не знает — отмену запоминаем.
        self.skipped: set[str] = set()
        self.stopped = False     # отменён целиком
        self.closed = False      # очередь закрыта: прогон дописывает итог
        self.created = False     # строка в ai_runs заведена
        self.wake = asyncio.Event()   # в очередь встал новый хост
        for key in keys:
            self.add(key)

    def add(self, key: str) -> bool:
        """Поставить хост в очередь. False — он и так ждёт или проверяется."""
        self.skipped.discard(key)
        old = self.items.get(key)
        if old is not None and old.state in _WAITING:
            return False
        item = _Item(key=key, name=old.name if old else key, position=old.position if old else 0)
        self.items[key] = item
        if self.targets is not None:
            self._bind(item)
        self.wake.set()
        return True

    @property
    def accepting(self) -> bool:
        """Берёт ли прогон новые хосты: остановленный и дописывающий итог — нет."""
        return not (self.closed or self.stopped)

    def _bind(self, item: _Item) -> None:
        found = (self.targets or {}).get(item.key)
        if found is not None:
            item.position, item.target = found
            item.name = item.target.name
        elif item.state == QUEUED:
            item.state, item.error = MISSING, MISSING_NOTE

    def resolve(self, targets: list[Target]) -> None:
        """Привязать очередь к хостам свежей подписки."""
        self.targets = {t.key: (i, t) for i, t in enumerate(targets)}
        if self.scope == FULL:
            # Порядок — как в подписке. Хосты, которые успели попросить отдельно,
            # уже в очереди; лишние (которых в подписке нет) уходят в хвост.
            asked, self.items = self.items, {}
            for t in targets:
                self.items[t.key] = asked.pop(t.key, None) or _Item(key=t.key, name=t.name)
            self.items.update(asked)
        for item in self.items.values():
            if item.target is None:
                self._bind(item)
            if item.state == QUEUED and (self.stopped or item.key in self.skipped):
                item.state = CANCELLED
        self.skipped.clear()

    def next_queued(self) -> _Item | None:
        if self.closed:
            return None
        for item in self.items.values():
            if item.state == QUEUED and item.target is not None:
                return item
        return None

    def cancel(self, keys: Iterable[str]) -> None:
        """Снять хосты из очереди, идущие проверки — прервать."""
        for key in keys:
            item = self.items.get(key)
            if item is None:
                if self.scope == FULL and self.targets is None:
                    self.skipped.add(key)
            elif item.state == QUEUED:
                item.state = CANCELLED
            elif item.state == RUNNING:
                item.token.cancel()      # состояние сменит исполнитель, когда поток выйдет

    def stop(self) -> None:
        """Отмена человеком: очередь снята, идущие проверки прерваны."""
        self.stopped = True
        self.cancel(list(self.items))

    def halt(self) -> None:
        """Прогон оборвался (рагент выключается или упало): гасим идущие
        проверки, очередь дальше не идёт. Ждавшие хосты остаются «в очереди» —
        это не отмена, в записи прогона они будут оборванными."""
        self.closed = True
        for item in self.items.values():
            if item.state == RUNNING:
                item.token.cancel()

    def count(self, state: str) -> int:
        return sum(1 for i in self.items.values() if i.state == state)

    def snapshot(self) -> list[dict]:
        return [{"key": i.key, "host": i.name, "pos": i.position, "state": i.shown_state,
                 "error": i.error} for i in self.items.values()]


_current: _Run | None = None        # идущий прогон
_next: _Run | None = None           # ждёт, пока идущий закончится
_driver: asyncio.Task | None = None


def is_running() -> bool:
    return _current is not None


def live() -> dict | None:
    """Идущий прогон глазами интерфейса: кто в очереди, кто проверяется."""
    cur, nxt = _current, _next
    if cur is None:
        return None
    hosts = {h["key"]: h for h in cur.snapshot()}
    if nxt is not None and nxt.scope == PARTIAL:
        hosts.update({h["key"]: h for h in nxt.snapshot()})
    return {
        "id": cur.id, "scope": cur.scope, "trigger": cur.trigger,
        "resolved": cur.targets is not None, "stopping": cur.stopped,
        "skipped": set(cur.skipped), "hosts": list(hosts.values()),
        "pending_full": nxt.trigger if nxt is not None and nxt.scope == FULL else None,
    }


async def start_run(trigger: str, keys: list[str] | None = None) -> None:
    """Запустить прогон: полный (keys пуст) или по выбранным хостам.

    Исполнитель один. Хосты, попрошенные во время идущего прогона, встают в его
    очередь — без дублей. Полный прогон поверх полного — AlreadyRunning; поверх
    выборочного он встаёт следом и стартует, как только тот закончится.
    """
    global _current, _next, _driver
    scope = PARTIAL if keys else FULL
    cur = _current
    if cur is None:
        run = _Run(scope, trigger, keys or ())
        # До первого await: следующий запрос уже увидит прогон и встанет в него.
        _current = run
        try:
            await _db_create(run)
        except BaseException:
            _current = None
            raise
        _driver = asyncio.create_task(_drive(), name=f"ai-access-{trigger}")
        return

    if scope == PARTIAL:
        if cur.accepting:
            if [k for k in keys or () if cur.add(k)]:
                await _db_sync(cur)
        elif _next is None:
            # Идущий прогон остановлен или пишет итог — хосты пойдут сразу за ним.
            _next = _Run(PARTIAL, trigger, keys or ())
        elif _next.scope == PARTIAL:
            for k in keys or ():
                _next.add(k)
        # Полный прогон в очереди проверит их и так.
        return

    if (cur.scope == FULL and cur.accepting) or (_next is not None and _next.scope == FULL):
        raise AlreadyRunning()
    _next = _Run(FULL, trigger)


async def cancel_run(keys: list[str] | None = None) -> None:
    """Отменить проверку хостов или весь прогон (keys пуст), включая ждущий.

    Уже проверенные хосты остаются с результатом; отменённые — с прежним:
    строки в ai_checks для них просто нет.
    """
    global _next
    cur = _current
    if keys is None:
        _next = None
        if cur is not None:
            cur.stop()
    else:
        if _next is not None and _next.scope == PARTIAL:
            for k in keys:
                _next.items.pop(k, None)
            if not _next.items:
                _next = None
        if cur is not None:
            cur.cancel(keys)
    if cur is not None and cur.created and not cur.closed:
        await _db_sync(cur)


async def _drive() -> None:
    """Исполнитель: гонит прогоны по одному, пока есть что гнать."""
    global _current, _next
    try:
        while _current is not None:
            await _execute(_current)
            # Без await между строками: запрос не попадёт в зазор между прогонами.
            _current, _next = _next, None
    except BaseException:
        _current = _next = None
        raise


async def _check(run: _Run, item: _Item, gemini: str) -> None:
    await _db_sync(run)
    try:
        res = await asyncio.to_thread(probe_target, item.target, gemini, item.token)
    except ProbeCancelled:
        item.state = CANCELLED
    else:
        await _db_save_check(run, item, res)
        item.state = DONE
    await _db_sync(run)


async def _pump(run: _Run, gemini: str, limit: int) -> None:
    """Держать до `limit` проверок разом, пока очередь не опустеет.

    Хосты доставляют и во время прогона: новый сразу занимает свободное место,
    не дожидаясь, пока закончится соседняя проверка.
    """
    busy: set[asyncio.Task] = set()
    try:
        while True:
            run.wake.clear()
            while len(busy) < limit and (item := run.next_queued()) is not None:
                item.state = RUNNING
                busy.add(asyncio.create_task(_check(run, item, gemini)))
            if not busy:
                # Без await до выхода: хост, попрошенный позже, уйдёт в следующий прогон.
                run.closed = True
                return
            waker = asyncio.create_task(run.wake.wait())
            try:
                done, _ = await asyncio.wait({*busy, waker}, return_when=asyncio.FIRST_COMPLETED)
            finally:
                waker.cancel()
            for task in done - {waker}:
                busy.discard(task)
                task.result()       # ошибка записи в базу роняет весь прогон
    finally:
        for task in busy:
            task.cancel()


def _outcome(run: _Run) -> tuple[str, str | None]:
    done = run.count(DONE)
    if run.count(CANCELLED) and (run.stopped or not done):
        return "cancelled", None
    if not done:
        names = ", ".join(i.name for i in run.items.values() if i.state == MISSING)
        return "failed", f"{MISSING_NOTE}: {names}"[:500]
    return "done", None


async def _execute(run: _Run) -> None:
    try:
        if not run.created:
            await _db_create(run)
        cfg, key, url = await _db_context()
        profiles = await asyncio.to_thread(fetch_profiles, url)
        targets = build_targets(profiles, cfg.get("exclude") or "")
        if run.scope == FULL and not targets:
            raise RuntimeError("в подписке не нашлось ни одного хоста")
        run.resolve(targets)
        await _db_sync(run)

        await _pump(run, key, max(1, min(4, int(cfg.get("concurrency") or 2))))

        status, error = _outcome(run)
        if status != "done" or run.scope != FULL:
            await _db_finish(run, status, error=error)
            log.info("ai-access: %s прогон %s, проверено хостов %s",
                     "выборочный" if run.scope == PARTIAL else "полный", status, run.count(DONE))
            return

        # Сравнение, сводка и расписание — только у полного прогона.
        changes = await _db_changes(run)
        await _db_finish(run, "done", changes=changes)
        log.info("ai-access: прогон готов, хостов %s, изменений %s", run.count(DONE), len(changes))

        text = summary_text(changes, await _db_followup(run))
        if text and cfg.get("notify"):
            err = await _send_summary(cfg, text)
            if err:
                log.warning("ai-access: уведомление не ушло: %s", err)
    except asyncio.CancelledError:
        run.halt()
        await asyncio.shield(_db_finish(run, "aborted", error="прогон прерван"))
        raise
    except Exception as exc:  # noqa: BLE001
        log.exception("ai-access: прогон упал")
        run.halt()      # соседние проверки не должны работать в пустоту
        try:
            await _db_finish(run, "failed", error=str(exc)[:500])
        except Exception:  # noqa: BLE001 — база легла вместе с прогоном
            log.exception("ai-access: не смог записать итог прогона")


# ── прогон: записи в базу ───────────────────────────────────────────────────

async def _db_create(run: _Run) -> None:
    from app.db import SessionLocal

    async with SessionLocal() as db:
        db.add(AiRun(id=run.id, status="running", trigger=run.trigger, scope=run.scope,
                     hosts_total=len(run.items) or None, hosts_done=0, targets=run.snapshot()))
        await db.commit()
    run.created = True


async def _db_context() -> tuple[dict, str, str]:
    from app.db import SessionLocal

    async with SessionLocal() as db:
        cfg = await get_config(db)
        return cfg, await gemini_key(db), await effective_sub_url(db, cfg)


async def _db_sync(run: _Run) -> None:
    """Состояние очереди — в базу: его видно в списке прогонов и после конца."""
    from app.db import SessionLocal

    async with SessionLocal() as db:
        await db.execute(update(AiRun).where(AiRun.id == run.id).values(
            hosts_total=len(run.items) or None, hosts_done=run.count(DONE), targets=run.snapshot()))
        await db.commit()


async def _db_save_check(run: _Run, item: _Item, res: dict) -> None:
    from app.db import SessionLocal

    async with SessionLocal() as db:
        db.add(AiCheck(
            run_id=run.id, host=res["host"][:255], host_key=res["host_key"][:255],
            position=item.position,
            tunnel=res["tunnel"], exit_ip=res["exit_ip"], exit_country=res["exit_country"],
            ai_ip=res["ai_ip"], ai_country=res["ai_country"], ai_warp=res["ai_warp"],
            duration_ms=res.get("duration_ms") or 0, results=res["results"],
            error=res["error"]))
        await db.commit()


async def _db_finish(run: _Run, status: str, error: str | None = None,
                     changes: list[dict] | None = None) -> None:
    from app.db import SessionLocal

    async with SessionLocal() as db:
        await db.execute(update(AiRun).where(AiRun.id == run.id).values(
            finished_at=_now(), status=status, error=error, changes=changes,
            hosts_total=len(run.items) or None, hosts_done=run.count(DONE),
            targets=run.snapshot()))
        await db.commit()


async def _db_changes(run: _Run) -> list[dict]:
    """Отличия полного прогона от предыдущего удачного полного."""
    from app.db import SessionLocal

    async with SessionLocal() as db:
        row = await db.get(AiRun, run.id)
        prev = await previous_done(db, before=row.started_at if row else None)
        if prev is None:
            return []
        return diff_runs(await _rows_of(db, prev.id), await _rows_of(db, run.id))


async def _db_followup(run: _Run) -> list[str]:
    """Итог по тому, что сломалось в предыдущем полном прогоне."""
    from app.db import SessionLocal

    try:
        async with SessionLocal() as db:
            row = await db.get(AiRun, run.id)
            prev = await previous_done(db, before=row.started_at if row else None)
            if prev is None or not prev.changes:
                return []
            return followup_lines(prev.changes, await _rows_of(db, run.id))
    except Exception:  # noqa: BLE001 — прогон уже записан, сводка не должна его ронять
        log.exception("ai-access: не смог собрать итог по прошлым поломкам")
        return []


async def _send_summary(cfg: dict, text: str) -> str | None:
    from app.db import SessionLocal

    async with SessionLocal() as db:
        return await _notify(db, cfg, text)


async def _rows_of(db: AsyncSession, run_id: uuid.UUID) -> dict[str, dict]:
    # По времени: хост, перепроверенный внутри прогона, берём по последней проверке.
    rows = await db.execute(select(AiCheck).where(AiCheck.run_id == run_id)
                            .order_by(AiCheck.checked_at.asc()))
    return {c.host_key: {"host": c.host, "results": c.results or {}, "ai_country": c.ai_country}
            for c in rows.scalars()}


async def previous_done(db: AsyncSession, before: datetime | None = None) -> AiRun | None:
    """Последний удачный полный прогон — опора матрицы и сравнения."""
    q = select(AiRun).where(AiRun.status == "done", AiRun.scope == FULL)
    if before is not None:
        q = q.where(AiRun.started_at < before)
    row = await db.execute(q.order_by(AiRun.started_at.desc()).limit(1))
    return row.scalars().first()


async def last_full(db: AsyncSession) -> AiRun | None:
    """Последний полный прогон любого исхода: по нему считается расписание."""
    row = await db.execute(select(AiRun).where(AiRun.scope == FULL)
                           .order_by(AiRun.started_at.desc()).limit(1))
    return row.scalars().first()


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
    global _next
    _next = None
    task = _driver
    if task is not None and not task.done():
        task.cancel()
        try:
            await task
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


# ── матрица: последний результат каждого хоста ──────────────────────────────

@dataclass
class MatrixRow:
    key: str
    check: AiCheck            # свежий результат хоста
    before: AiCheck | None    # с чем его сравниваем
    is_new: bool
    partial: bool             # результат из выборочного прогона


def build_matrix(pairs: list[tuple[AiCheck, AiRun]], shown: AiRun | None, prev: AiRun | None,
                 base_keys: list[str]) -> list[MatrixRow]:
    """Матрица из последних результатов каждого хоста, а не из одного прогона.

    pairs — проверки вместе со своими прогонами; shown — последний удачный
    полный прогон, prev — удачный полный перед ним; base_keys — хосты shown в
    порядке подписки. Состав матрицы задаёт shown, а результат у каждого хоста
    свой — самый свежий, из какого бы прогона он ни был. Хост, отменённый в
    shown, остаётся с прежним результатом; хост, которого в shown нет, попадает
    в матрицу, только если проверен позже.

    С чем сравниваем: результат полного прогона — с предыдущим удачным полным
    (как в сводке изменений), результат выборочного — с предыдущим результатом
    этого же хоста.
    """
    by_key: dict[str, list[tuple[AiCheck, AiRun]]] = {}
    for check, run in sorted(pairs, key=lambda p: p[0].checked_at):
        by_key.setdefault(check.host_key, []).append((check, run))

    keys = [k for k in dict.fromkeys(base_keys) if k in by_key]
    base = set(keys)
    floor = shown.started_at if shown is not None else None
    extra = [k for k, items in by_key.items()
             if k not in base and (floor is None or items[-1][0].checked_at >= floor)]
    extra.sort(key=lambda k: (by_key[k][-1][0].position, k))

    def reference(run: AiRun) -> AiRun | None:
        for cand in (shown, prev):
            if cand is not None and cand.id != run.id and cand.started_at < run.started_at:
                return cand
        return None

    rows: list[MatrixRow] = []
    for key in [*keys, *extra]:
        items = by_key[key]
        check, run = items[-1]
        if run.scope == PARTIAL:
            before = items[-2][0] if len(items) > 1 else None
            rows.append(MatrixRow(key, check, before, is_new=False, partial=True))
            continue
        ref = reference(run)
        before = next((c for c, r in reversed(items) if ref is not None and r.id == ref.id), None)
        rows.append(MatrixRow(key, check, before, is_new=ref is not None and before is None,
                              partial=False))
    return rows


async def load_matrix(db: AsyncSession) -> tuple[AiRun | None, AiRun | None, list[MatrixRow]]:
    """(последний удачный полный прогон, удачный полный перед ним, строки матрицы)."""
    shown = await previous_done(db)
    prev = await previous_done(db, before=shown.started_at) if shown else None

    q = select(AiCheck, AiRun).join(AiRun, AiRun.id == AiCheck.run_id)
    if shown is not None:
        # Всё новее опорного прогона и сам он с предыдущим — для сравнения.
        q = q.where(AiCheck.checked_at >= (prev or shown).started_at)
    pairs = [(c, r) for c, r in (await db.execute(q)).all()]

    if shown is None:
        return None, None, build_matrix(pairs, None, None, [])
    if shown.targets:
        own = [t for t in shown.targets
               if isinstance(t, dict) and t.get("key") and t.get("state") != MISSING]
        base_keys = [t["key"] for t in sorted(own, key=lambda t: t.get("pos") or 0)]
    else:   # прогон до миграции 014: состав — его же строки
        rows = sorted((c for c, r in pairs if r.id == shown.id),
                      key=lambda c: (c.position, c.host_key))
        base_keys = [c.host_key for c in rows]
    # Хост, отменённый и в shown, и в prev: его последний результат старше окна.
    seen = {c.host_key for c, _ in pairs}
    lost = [k for k in base_keys if k not in seen]
    if lost:
        older = await db.execute(select(AiCheck, AiRun).join(AiRun, AiRun.id == AiCheck.run_id)
                                 .where(AiCheck.host_key.in_(lost)))
        pairs += [(c, r) for c, r in older.all()]
    return shown, prev, build_matrix(pairs, shown, prev, base_keys)


# ── расписание ──────────────────────────────────────────────────────────────

def is_due(last: AiRun | None, interval_hours: float, now: datetime) -> bool:
    """Пора ли запускать полный прогон. last — последний ПОЛНЫЙ прогон:
    выборочные расписание не сдвигают."""
    if last is None:
        return True
    age = now - last.started_at
    if last.status in ("failed", "aborted"):
        return age >= min(FAILED_RETRY_AFTER, timedelta(hours=interval_hours))
    # Отменённый человеком (cancelled) через час не повторяем: отменил — значит
    # не хотел. Ждём обычный интервал.
    return age >= timedelta(hours=interval_hours)


async def schedule_tick(db: AsyncSession, cfg: dict) -> None:
    """Запустить плановый полный прогон, если пора.

    Идёт выборочный прогон — плановый встаёт следом за ним и стартует, как
    только тот закончится, а не ждёт следующего тика. Идёт полный или плановый
    уже стоит в очереди — AlreadyRunning.
    """
    if not cfg.get("enabled"):
        return
    hours = max(1.0, float(cfg.get("interval_hours") or 12))
    if is_due(await last_full(db), hours, _now()):
        await start_run("schedule")


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
                try:
                    await schedule_tick(db, cfg)
                except AlreadyRunning:
                    pass
                if time.monotonic() - last_prune > 86400:
                    await prune(db, int(cfg.get("keep_days") or 45))
                    last_prune = time.monotonic()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("ai-access: цикл упал")
        await asyncio.sleep(TICK_S)
