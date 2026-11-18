"""Отчёт-доказательство о шаринге: готовый документ для клиента.

build_report() — чистая функция над ReportData: никакой сети и базы, поэтому
тестируется на фиксированных данных. collect() собирает ReportData из панели,
снимка последнего скана и кеша геоданных.

Правила содержания:
- только факты; «жёсткое доказательство» — когда за SIMULT_SEC число различных пар
  (провайдер, город) среди проводных адресов строго больше лимита устройств пользователя
  (одно устройство в одно время у одного провайдера); иначе, и при лимите 0/безлимите,
  «косвенные признаки». Города и расстояния — лишь дополнительный факт;
- мобильные пулы как доказательство не учитываются;
- внутренние имена нод и адреса инфраструктуры в текст не попадают: серверы
  называются «сервер N», запросы подписки с облачных адресов не показываются;
- нет данных — «не определено».
"""
from __future__ import annotations

import asyncio
import logging
import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.services import ip_geo, sharing_detect, sharing_score, subwatch

log = logging.getLogger("remna.sharing")

MSK = timezone(timedelta(hours=3))
WINDOW_MIN = 15                 # окно «текущих» адресов, минут
SIMULT_SEC = 120                # «одновременно» — в пределах двух минут
SAME_CITY_KM = 30.0             # ближе — одна агломерация, не «разные города»
MAX_PAIRS = 10
MAX_SUB_IPS = 10
SAVED_PREFIX = "sharing_report_"

LEVEL_HARD = "жёсткое доказательство"
LEVEL_INDIRECT = "косвенные признаки"
UNKNOWN = "не определено"


@dataclass
class ReportData:
    now: datetime
    card: dict
    devices: list[dict] = field(default_factory=list)
    sub_records: list[dict] = field(default_factory=list)
    # {"ip", "seen": datetime|None, "server": "сервер N"}
    sessions: list[dict] = field(default_factory=list)
    geo: dict[str, dict] = field(default_factory=dict)
    zone: str = ""                  # green | amber | red из последнего скана
    score: int = 0
    window_min: int = WINDOW_MIN
    banned_at: datetime | None = None
    # Сколько запросов подписки с облачных адресов не показано.
    sub_hidden: int = 0


# ── форматирование ───────────────────────────────────────────────────────────

def _utc(dt: datetime) -> datetime:
    return dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def fmt_dt(dt: datetime | None) -> str:
    if not dt:
        return UNKNOWN
    u = _utc(dt)
    return f"{u:%d.%m.%Y %H:%M} UTC ({u.astimezone(MSK):%H:%M} МСК)"


def fmt_t(dt: datetime | None) -> str:
    return f"{_utc(dt):%H:%M:%S}" if dt else "—"


def fmt_num(v: float, digits: int = 2) -> str:
    return f"{v:.{digits}f}".replace(".", ",")


def fmt_dur(sec: float) -> str:
    sec = int(round(sec))
    if sec < 60:
        return f"{sec} с"
    return f"{sec // 60} мин {sec % 60:02d} с"


def fmt_km(km: float | None) -> str:
    if km is None:
        return UNKNOWN
    return f"около {int(round(km, -1)) if km >= 100 else int(round(km))} км"


def _gb(n: Any) -> float | None:
    try:
        return float(n) / (1024 ** 3)
    except (TypeError, ValueError):
        return None


def _ts(val: Any) -> datetime | None:
    return sharing_detect._parse_ts(val)


def _esc(s: Any) -> str:
    return str(s).replace("|", "/").replace("\n", " ").strip()


# ── геоданные адреса ─────────────────────────────────────────────────────────

def provider_key(g: dict | None) -> str | None:
    if not g:
        return None
    if g.get("asn"):
        return f"as{g['asn']}"
    if g.get("isp"):
        return str(g["isp"]).lower()
    return None


def provider_name(g: dict | None) -> str:
    if not g:
        return UNKNOWN
    isp, asn = g.get("isp"), g.get("asn")
    if isp and asn:
        return f"{_esc(isp)} (AS{asn})"
    if isp:
        return _esc(isp)
    if asn:
        return f"AS{asn}"
    return UNKNOWN


def kind_of(g: dict | None) -> str:
    """wired | mobile | hosting | unknown. Хостинг и VPN проводным не считаем."""
    if not g:
        return "unknown"
    if g.get("mobile") is True:
        return "mobile"
    if g.get("hosting") is True:
        return "hosting"
    if g.get("mobile") is False:
        return "wired"
    return "unknown"


KIND_RU = {"wired": "проводной", "mobile": "мобильный", "hosting": "хостинг / VPN",
           "unknown": UNKNOWN}


def city_of(g: dict | None) -> str:
    if not g or not g.get("city"):
        return UNKNOWN
    return _esc(g["city"])


def _city_norm(g: dict | None) -> str | None:
    if not g or not g.get("city"):
        return None
    return str(g["city"]).strip().lower().replace("ё", "е")


def distance_km(a: dict | None, b: dict | None) -> float | None:
    try:
        la1, lo1, la2, lo2 = (float(a["lat"]), float(a["lon"]), float(b["lat"]), float(b["lon"]))  # type: ignore[index]
    except (TypeError, ValueError, KeyError):
        return None
    p1, p2 = math.radians(la1), math.radians(la2)
    dphi, dl = p2 - p1, math.radians(lo2 - lo1)
    h = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6371.0 * math.asin(math.sqrt(h))


# ── анализ ───────────────────────────────────────────────────────────────────

@dataclass
class Addr:
    ip: str
    seen: datetime | None
    server: str
    geo: dict
    kind: str


def current_addrs(d: ReportData) -> list[Addr]:
    """Адреса входа в окне, по одному на IP (самый свежий вход), старые — первыми."""
    lo = d.now - timedelta(minutes=d.window_min)
    best: dict[str, Addr] = {}
    for s in d.sessions:
        ts = s.get("seen")
        if ts is None or _utc(ts) < _utc(lo):
            continue
        ip = s["ip"]
        cur = best.get(ip)
        if cur is None or _utc(ts) > _utc(cur.seen):  # type: ignore[arg-type]
            g = d.geo.get(ip) or {}
            best[ip] = Addr(ip, ts, s.get("server") or "", g, kind_of(g))
    return sorted(best.values(), key=lambda a: _utc(a.seen))  # type: ignore[arg-type]


def device_limit(card: dict) -> int | None:
    """Лимит устройств; 0, пусто и не число — «лимита нет» (доказать превышение нечем)."""
    try:
        v = int(card.get("hwidDeviceLimit"))
    except (TypeError, ValueError):
        return None
    return v if v > 0 else None


@dataclass
class CityPair:
    a: Addr
    b: Addr
    dt: float
    km: float | None


def city_pairs(wired: list[Addr]) -> list[CityPair]:
    """Пары проводных адресов в разных городах — дополнительный факт, не доказательство."""
    out: list[CityPair] = []
    for i, a in enumerate(wired):
        for b in wired[i + 1:]:
            ca, cb = _city_norm(a.geo), _city_norm(b.geo)
            if not ca or not cb or ca == cb:
                continue
            km = distance_km(a.geo, b.geo)
            if km is not None and km < SAME_CITY_KM:
                continue
            dt = abs((_utc(b.seen) - _utc(a.seen)).total_seconds())  # type: ignore[arg-type]
            out.append(CityPair(a, b, dt, km))
    out.sort(key=lambda p: (p.dt, p.a.ip, p.b.ip))
    return out


def pair_key(a: Addr) -> tuple[str, str] | None:
    """(провайдер, город). Нет провайдера или города — в счёт не идёт: неизвестное
    не должно сойти за «разное»."""
    p, c = provider_key(a.geo), _city_norm(a.geo)
    return (p, c) if p and c else None


@dataclass
class Burst:
    """Окно SIMULT_SEC с наибольшим числом различных пар (провайдер, город)."""
    combos: int
    start: datetime | None
    end: datetime | None
    members: list[Addr]


def best_burst(wired: list[Addr], width: int = SIMULT_SEC) -> Burst:
    best = Burst(0, None, None, [])
    for anchor in wired:
        t0 = _utc(anchor.seen)  # type: ignore[arg-type]
        grp = [x for x in wired if 0 <= (_utc(x.seen) - t0).total_seconds() <= width]  # type: ignore[arg-type]
        n = len({k for k in map(pair_key, grp) if k})
        if (n, len(grp)) > (best.combos, len(best.members)):
            best = Burst(n, grp[0].seen, grp[-1].seen, grp)
    return best


@dataclass
class Analysis:
    addrs: list[Addr]
    wired: list[Addr]
    mobile: list[Addr]
    other: list[Addr]
    pairs: list[CityPair]
    burst: Burst
    limit: int | None
    level: str
    unknown_geo: int


def analyse(d: ReportData) -> Analysis:
    addrs = current_addrs(d)
    wired = [a for a in addrs if a.kind == "wired"]
    mobile = [a for a in addrs if a.kind == "mobile"]
    other = [a for a in addrs if a.kind in ("hosting", "unknown")]
    burst = best_burst(wired)
    limit = device_limit(d.card)
    hard = limit is not None and burst.combos > limit
    return Analysis(
        addrs=addrs, wired=wired, mobile=mobile, other=other, pairs=city_pairs(wired),
        burst=burst, limit=limit, level=LEVEL_HARD if hard else LEVEL_INDIRECT,
        unknown_geo=sum(1 for a in addrs if a.kind == "unknown"),
    )


# ── сборка технического текста ───────────────────────────────────────────────

def gb_per_hour(d: ReportData) -> tuple[float, float] | None:
    """(ГБ/час, часов) с первого подключения; меньше часа — не определяем."""
    ut = d.card.get("userTraffic") or {}
    gb = _gb(ut.get("lifetimeUsedTrafficBytes") or ut.get("usedTrafficBytes"))
    start = _ts(ut.get("firstConnectedAt")) or _ts(d.card.get("createdAt"))
    if gb is None or not start:
        return None
    hours = (_utc(d.now) - _utc(start)).total_seconds() / 3600
    return (gb / hours, hours) if hours >= 1 else None


def _account_section(d: ReportData) -> list[str]:
    c = d.card
    ut = c.get("userTraffic") or {}
    created = _ts(c.get("createdAt"))
    first = _ts(ut.get("firstConnectedAt"))
    used = ut.get("lifetimeUsedTrafficBytes") or ut.get("usedTrafficBytes")
    gb = _gb(used)
    lines = [
        "## 1. Аккаунт", "",
        "| Параметр | Значение |", "|---|---|",
        f"| Имя / ID | {_esc(c.get('username') or UNKNOWN)} / {c.get('id')} |",
        f"| Telegram ID | {_esc(c.get('telegramId') or 'не указан')} |",
        f"| Создан | {fmt_dt(created)} |",
        f"| Первое подключение | {fmt_dt(first)} |",
        f"| Лимит устройств | {c.get('hwidDeviceLimit') if c.get('hwidDeviceLimit') is not None else UNKNOWN} |",
    ]
    if gb is not None:
        lines.append(f"| Трафик | {fmt_num(gb)} ГБ к {fmt_dt(d.now)} |")
        rate = gb_per_hour(d)
        if rate is not None:
            lines.append(f"| Средний расход | около {fmt_num(rate[0], 1)} ГБ/час "
                         f"за {fmt_num(rate[1], 1)} ч с первого подключения |")
        else:
            lines.append("| Средний расход | не определён (аккаунт подключён меньше часа) |")
    else:
        lines.append(f"| Трафик | {UNKNOWN} |")
    return lines


def _devices_section(d: ReportData) -> list[str]:
    n = len(d.devices)
    limit = d.card.get("hwidDeviceLimit")
    lines = ["", f"## 2. Устройства, зарегистрированные на аккаунте ({n}"
                  + (f" из {limit}" if limit is not None else "") + ")", ""]
    if not d.devices:
        return lines + ["Зарегистрированных устройств нет."]
    lines += ["| Устройство | Клиент | Адрес при регистрации | Обновлено (UTC) |", "|---|---|---|---|"]
    for dev in d.devices:
        ip = dev.get("requestIp")
        g = d.geo.get(str(ip)) if ip else None
        where = f"{ip} ({city_of(g)}, {provider_name(g)})" if ip else UNKNOWN
        name = ", ".join(x for x in (_esc(dev.get("platform") or ""), _esc(dev.get("deviceModel") or "")) if x)
        upd = _ts(dev.get("updatedAt"))
        upd_s = f"{_utc(upd):%d.%m.%Y %H:%M}" if upd else UNKNOWN
        lines.append(f"| {name or UNKNOWN} | {_esc(dev.get('userAgent') or UNKNOWN)} | {where} | {upd_s} |")
    return lines


def _subs_section(d: ReportData) -> list[str]:
    ips = [str(r.get("requestIp")) for r in d.sub_records if isinstance(r, dict) and r.get("requestIp")]
    uniq = list(dict.fromkeys(ips))
    lines = ["", "### Запросы подписки (список серверов)", "",
             f"Запросов в истории: {len(ips)}, уникальных адресов: {len(uniq)}."]
    if d.sub_hidden:
        lines.append(f"Ещё {d.sub_hidden} запросов с облачных адресов в расчёт не включены.")
    if 0 < len(uniq) <= MAX_SUB_IPS:
        lines += ["", "| Адрес | Оператор | Город | Запросов |", "|---|---|---|---|"]
        for ip in uniq:
            g = d.geo.get(ip)
            lines.append(f"| {ip} | {provider_name(g)} | {city_of(g)} | {ips.count(ip)} |")
    return lines


def _tunnel_tables(d: ReportData, an: Analysis) -> list[str]:
    lines = ["", f"## 3. Адреса входа на наши серверы за последние {d.window_min} минут", ""]
    if not an.addrs:
        return lines + ["В этом окне входов с аккаунта не зафиксировано."]
    span = (_utc(an.addrs[-1].seen) - _utc(an.addrs[0].seen)).total_seconds()  # type: ignore[arg-type]
    lines += [
        f"Замер на {fmt_dt(d.now)}. Первый вход в окне {fmt_t(an.addrs[0].seen)} UTC, "
        f"последний {fmt_t(an.addrs[-1].seen)} UTC (разброс {fmt_dur(span)}).",
        f"Всего разных адресов: **{len(an.addrs)}** "
        f"(проводных {len(an.wired)}, мобильных {len(an.mobile)}, "
        f"хостинг/не определён тип: {len(an.other)}).", "",
    ]
    lines += ["### Проводные адреса", ""]
    if an.wired:
        lines += ["| Адрес | Оператор | Город | Сервер | Последний вход (UTC) |", "|---|---|---|---|---|"]
        for a in an.wired:
            lines.append(f"| {a.ip} | {provider_name(a.geo)} | {city_of(a.geo)} | {a.server or '—'} | {fmt_t(a.seen)} |")
    else:
        lines.append("Проводных адресов в окне нет.")
    lines += ["", "### Мобильные адреса", ""]
    if an.mobile:
        lines += ["| Адрес | Оператор | Последний вход (UTC) |", "|---|---|---|"]
        for a in an.mobile:
            lines.append(f"| {a.ip} | {provider_name(a.geo)} | {fmt_t(a.seen)} |")
    else:
        lines.append("Мобильных адресов в окне нет.")
    if an.other:
        lines += ["", "### Тип не определён или хостинг", "",
                  "| Адрес | Оператор | Тип | Последний вход (UTC) |", "|---|---|---|---|"]
        for a in an.other:
            lines.append(f"| {a.ip} | {provider_name(a.geo)} | {KIND_RU[a.kind]} | {fmt_t(a.seen)} |")
    return lines


def _evidence_section(d: ReportData, an: Analysis) -> list[str]:
    lines = ["", "## 4. Главный довод: подключений больше, чем устройств", ""]
    b = an.burst
    lines.append(f"Правило: в окне не более {SIMULT_SEC // 60} минут считаем различные пары "
                 "«провайдер + город» среди проводных адресов. Одно устройство в одно время "
                 "подключено через одного провайдера, поэтому каждая пара означает хотя бы одно "
                 "устройство. Два адреса одного провайдера в одном городе считаем одной парой "
                 "(адрес мог смениться). Мобильные адреса не считаем.")
    lines.append("")
    if b.combos == 0:
        lines.append("Проводных адресов с определёнными провайдером и городом в окне нет: "
                     "по этому признаку недостаточно для вывода.")
    else:
        lines.append(f"Максимум в одном {SIMULT_SEC // 60}-минутном окне "
                     f"({fmt_t(b.start)}–{fmt_t(b.end)} UTC): **{b.combos}** различных пар "
                     f"«провайдер + город», адресов {len(b.members)}.")
        lines += ["", "| Адрес | Провайдер | Город | Вход (UTC) |", "|---|---|---|---|"]
        for a in b.members:
            lines.append(f"| {a.ip} | {provider_name(a.geo)} | {city_of(a.geo)} | {fmt_t(a.seen)} |")
    lines.append("")
    if an.limit is None:
        lines.append("Лимит устройств у аккаунта не задан или не ограничен: превышение лимита "
                     "посчитать нельзя, это косвенные признаки.")
    elif an.level == LEVEL_HARD:
        lines.append(f"Различных пар {b.combos}, лимит устройств {an.limit}: подключений больше, "
                     "чем разрешено устройств. Критерий выполнен.")
    else:
        lines.append(f"Различных пар {b.combos}, лимит устройств {an.limit}: лимит не превышен "
                     "(нужно строго больше). Критерий не выполнен.")
    lines += ["", "## 5. Дополнительно: проводные адреса в разных городах", ""]
    if not an.pairs:
        lines.append("Пар проводных адресов в разных городах в окне не выявлено "
                     "(или город по адресам не определён).")
    else:
        lines += [f"Пары проводных адресов в разных городах (расстояние между городами меньше "
                  f"{int(SAME_CITY_KM)} км за разные не считаем). Показано до {MAX_PAIRS} пар с "
                  "наименьшей разницей по времени. Это дополнительный факт, не доказательство.", "",
                  "| Адрес 1 (город) | Адрес 2 (город) | Вход 1 / вход 2 (UTC) | Разница | Расстояние |",
                  "|---|---|---|---|---|"]
        for p in an.pairs[:MAX_PAIRS]:
            lines.append(f"| {p.a.ip} ({city_of(p.a.geo)}) | {p.b.ip} ({city_of(p.b.geo)}) | "
                         f"{fmt_t(p.a.seen)} / {fmt_t(p.b.seen)} | {fmt_dur(p.dt)} | {fmt_km(p.km)} |")
    return lines


def sub_fact(d: ReportData, an: Analysis) -> str | None:
    """Подписку обновляли с одного-двух адресов, а входов — с многих."""
    ips = [str(r.get("requestIp")) for r in d.sub_records if isinstance(r, dict) and r.get("requestIp")]
    if ips and len(an.addrs) >= 4 and len(set(ips)) <= 2:
        return (f"Подписку (список серверов) запрашивали {len(ips)} раз(а) с {len(set(ips))} "
                f"адрес(а/ов), тогда как входов на серверы с {len(an.addrs)} адресов.")
    return None


def _conclusion_section(d: ReportData, an: Analysis) -> list[str]:
    n_dev = len(d.devices)
    lines = ["", "## 6. Итог", "", f"Уровень: **{an.level}**.", ""]
    facts: list[str] = []
    if an.level == LEVEL_HARD:
        facts.append(f"В {SIMULT_SEC // 60}-минутном окне {an.burst.combos} различных пар "
                     f"«провайдер + город» среди проводных адресов при лимите устройств {an.limit}.")
    elif an.burst.combos >= 2:
        facts.append(f"В {SIMULT_SEC // 60}-минутном окне {an.burst.combos} различных пар "
                     "«провайдер + город» среди проводных адресов"
                     + (f" при лимите устройств {an.limit}." if an.limit else "; лимит устройств не задан."))
    if an.addrs and an.limit and len(an.addrs) > an.limit:
        facts.append(f"Лимит устройств {an.limit}, зарегистрировано {n_dev}, "
                     f"а разных адресов входа в окне {len(an.addrs)}.")
    sub = sub_fact(d, an)
    if sub:
        facts.append(sub)
    c_ut = d.card.get("userTraffic") or {}
    gb = _gb(c_ut.get("lifetimeUsedTrafficBytes") or c_ut.get("usedTrafficBytes"))
    if gb is not None and gb > 0 and not facts:
        facts.append("Нагрузка по трафику приведена в разделе 1; сама по себе она шарингом не доказывается.")
    lines += [f"{i}. {t}" for i, t in enumerate(facts, 1)] or ["Значимых фактов в окне не выявлено."]
    if d.banned_at:
        lines += ["", f"Решение: аккаунт отключён {fmt_dt(d.banned_at)} за использование одной "
                      "учётной записью несколькими людьми."]
    else:
        lines += ["", f"Статус аккаунта в панели: {_esc(d.card.get('status') or UNKNOWN)}."]
    return lines


def _limits_section(d: ReportData, an: Analysis) -> list[str]:
    lines = ["", "## 7. Ограничения", ""]
    pts = [
        "Мобильные адреса как доказательство не учитываются: один телефон может выходить "
        "в сеть через несколько адресов пула оператора. Выводы строятся только по проводным адресам.",
        "Оператор, город и тип подключения определены по открытым базам адресов. Город по адресу "
        "приблизителен (часто это город узла оператора, а не квартира абонента); где данных нет, "
        f"указано «{UNKNOWN}». Адрес без определённого провайдера или города в счёт пар не идёт.",
        "Данные о входах — состояние на серверах на момент замера "
        f"({fmt_dt(d.now)}): история адресов за прошлые дни не хранится, поэтому отчёт отражает окно "
        f"последних {d.window_min} минут.",
        "Два адреса одного провайдера в одном городе считаются одной парой: возможна смена адреса "
        "у одного абонента. Это занижает число устройств, а не завышает.",
    ]
    if an.unknown_geo:
        pts.append(f"Для {an.unknown_geo} адресов тип подключения не определён: они не включены "
                   "ни в проводные, ни в мобильные.")
    if an.level == LEVEL_INDIRECT:
        pts.append(f"Критерий «жёсткого доказательства» (за {SIMULT_SEC // 60} минуты различных пар "
                   "«провайдер + город» среди проводных адресов строго больше лимита устройств; "
                   "при неограниченном лимите не применяется) не выполнен: это косвенные признаки.")
    return lines + [f"- {p}" for p in pts]


def build_report(d: ReportData) -> str:
    an = analyse(d)
    c = d.card
    name = _esc(c.get("username") or UNKNOWN)
    head = [
        f"# Отчёт по аккаунту {name} (ID {c.get('id')}): использование аккаунта несколькими пользователями",
        "",
        f"Дата отчёта: {d.now.astimezone(timezone.utc):%d.%m.%Y}. Время везде указано в UTC и в скобках "
        "по Москве (МСК = UTC+3).",
        "Источник: данные входа на наши серверы (адреса, с которых подключаются к серверам), список "
        "устройств аккаунта и история запросов подписки. Оператор, город и тип подключения определены "
        "по открытым базам адресов.",
        "",
        f"Уровень уверенности: **{an.level}**.",
        "",
    ]
    body = (_account_section(d) + _devices_section(d) + _subs_section(d) + _tunnel_tables(d, an)
            + _evidence_section(d, an) + _conclusion_section(d, an) + _limits_section(d, an))
    return "\n".join(head + body) + "\n"


# ── клиентский текст (простой язык, для пересылки) ───────────────────────────

def fmt_msk(dt: datetime | None) -> str:
    return f"{_utc(dt).astimezone(MSK):%d.%m.%Y %H:%M} МСК" if dt else UNKNOWN


def _msk_t(dt: datetime | None) -> str:
    return f"{_utc(dt).astimezone(MSK):%H:%M:%S}" if dt else "—"


def _line(a: Addr) -> str:
    city = city_of(a.geo) if _city_norm(a.geo) else "город не определён"
    isp = _esc(a.geo["isp"]) if a.geo.get("isp") else "провайдер не определён"
    return f"• {_msk_t(a.seen)} — {city} — {isp} — {a.ip}"


def build_client_text(d: ReportData) -> str:
    """Текст для клиента: без терминов и внутренних имён; абзацы разделены пустой строкой
    (по ним текст режется на сообщения Telegram)."""
    an = analyse(d)
    b = an.burst
    name = _esc(d.card.get("username") or UNKNOWN)
    hard = an.level == LEVEL_HARD
    paras = [f"Отключён аккаунт {name}"]
    if hard:
        paras.append("Причина: одним аккаунтом одновременно пользуются с большего числа устройств, "
                     "чем разрешено.")
    else:
        paras.append("Причина: по совокупности признаков аккаунтом пользуются не только вы одни или "
                     "с большего числа устройств, чем разрешено.")
    minutes = SIMULT_SEC // 60
    if an.limit:
        paras.append(f"Разрешено устройств: {an.limit}. "
                     f"Одновременных подключений из разных мест за {minutes} минуты: {b.combos}.")
    else:
        paras.append("Лимит устройств на аккаунте не ограничен, поэтому точное превышение "
                     f"посчитать нельзя. Подключений из разных мест за {minutes} минуты: {b.combos}.")
    if b.members:
        paras.append(f"Подключения из домашних сетей (время МСК — город — провайдер — адрес), "
                     f"{fmt_msk(b.start)} – {_msk_t(b.end)}:\n" + "\n".join(_line(a) for a in b.members))
        if hard:
            paras.append(
                f"Главный довод: подключений ({b.combos}) больше, чем разрешённых устройств "
                f"({an.limit}). Одно устройство в одно время подключено через одного провайдера, "
                f"поэтому подключения через {b.combos} разных сочетаний «провайдер + город» означают "
                f"не менее {b.combos} устройств. Два адреса одного провайдера в одном городе мы "
                "считали за одно подключение.")
    else:
        paras.append("Домашних подключений с определённым провайдером и городом за это время нет.")
    if an.mobile:
        paras.append(f"Ещё зафиксировано подключений из мобильных сетей: {len(an.mobile)}. "
                     "В расчёт мы их не берём: один телефон может менять адрес.")
    extra: list[str] = []
    if sub_fact(d, an):
        ips = [r.get("requestIp") for r in d.sub_records if isinstance(r, dict) and r.get("requestIp")]
        extra.append(f"Список серверов обновляли {len(ips)} раз с {len(set(ips))} адреса(ов), "
                     f"а подключались с {len(an.addrs)} разных адресов.")
    ut = d.card.get("userTraffic") or {}
    gb = _gb(ut.get("lifetimeUsedTrafficBytes") or ut.get("usedTrafficBytes"))
    if gb is not None and gb > 0:
        rate = gb_per_hour(d)
        extra.append(f"Израсходовано трафика: {fmt_num(gb)} ГБ"
                     + (f", в среднем около {fmt_num(rate[0], 1)} ГБ в час." if rate else "."))
    if extra:
        paras.append("Дополнительно:\n" + "\n".join(f"• {t}" for t in extra))
    if d.banned_at:
        if hard:
            paras.append(f"Решение: аккаунт отключён {fmt_msk(d.banned_at)}. Число разрешённых "
                         "устройств мы не меняли.")
        else:
            paras.append(f"Решение: аккаунт отключён {fmt_msk(d.banned_at)} по совокупности признаков "
                         f"(оценка {d.score} из 100). Однозначно подтвердить превышение числа "
                         "устройств по этому замеру нельзя.")
    else:
        paras.append("Аккаунт пока не отключён: это предварительный отчёт.")
    return "\n\n".join(paras) + "\n"


TG_LIMIT_HARD = 4096    # предел Telegram на одно сообщение
TG_SAFE = 4000          # лимит Telegram 4096; запас на счётчик и разницу подсчёта символов
TG_PREFIX = 12          # место под «99/99\n\n»


def split_parts(text: str, limit: int = TG_SAFE) -> list[str]:
    """Режет текст на части не длиннее limit по границам абзацев (длинный абзац — по строкам,
    слишком длинную строку — жёстко). При нескольких частях ставит «1/3» в начало."""
    room = limit - TG_PREFIX
    units: list[str] = []
    for para in [p.strip("\n") for p in text.split("\n\n") if p.strip()]:
        if len(para) <= room:
            units.append(para)
            continue
        cur = ""
        for ln in para.split("\n"):
            while len(ln) > room:
                if cur:
                    units.append(cur)
                    cur = ""
                units.append(ln[:room])
                ln = ln[room:]
            if cur and len(cur) + 1 + len(ln) > room:
                units.append(cur)
                cur = ln
            else:
                cur = f"{cur}\n{ln}" if cur else ln
        if cur:
            units.append(cur)
    parts: list[str] = []
    cur = ""
    for u in units:
        if cur and len(cur) + 2 + len(u) > room:
            parts.append(cur)
            cur = u
        else:
            cur = f"{cur}\n\n{u}" if cur else u
    if cur:
        parts.append(cur)
    if len(parts) > 1:
        parts = [f"{i}/{len(parts)}\n\n{p}" for i, p in enumerate(parts, 1)]
    return parts


def owner_header(username: str, user_id: int, score: int, auto: bool, parts: int) -> str:
    how = "автоматически" if auto else "вручную"
    return (f"Отчёт по бану за шаринг\nКто: {username or user_id} (ID {user_id})\n"
            f"Балл: {score} из 100\nКак забанен: {how}\n"
            f"Ниже текст для клиента, сообщений: {parts}. Можно пересылать.")



# ── сбор данных ──────────────────────────────────────────────────────────────

def _server_labels(rows: list[dict]) -> dict[str, str]:
    names = sorted({str(r.get("node") or "") for r in rows})
    return {n: f"сервер {i}" for i, n in enumerate(names, 1)}


def _panel_fetch(user_id: int) -> tuple[dict, list[dict], list[dict]]:
    card = sharing_detect._unwrap_user(sharing_detect._try_api(f"/api/users/{user_id}"))
    hwid = sharing_detect._try_api(f"/api/hwid/devices/{user_id}")
    hist = sharing_detect._try_api(
        f"/api/users/{user_id}/subscription-request-history?size=1000&start=0")
    devices = (hwid.get("devices") or []) if isinstance(hwid, dict) else []
    recs = (hist.get("records") or []) if isinstance(hist, dict) else []
    return card, [x for x in devices if isinstance(x, dict)], [x for x in recs if isinstance(x, dict)]


def hit_for(user_id: int) -> dict:
    for h in sharing_detect.snapshot().get("suspects") or []:
        if int(h.get("user_id")) == user_id:
            return dict(h)
    return {}


def in_zone(user_id: int) -> bool:
    return hit_for(user_id).get("level") in (sharing_score.AMBER, sharing_score.RED)


async def collect(db: AsyncSession, user_id: int, banned_at: datetime | None = None,
                  fetch_geo: bool = True) -> ReportData | None:
    card, devices, recs = await asyncio.to_thread(_panel_fetch, user_id)
    if not card:
        return None
    hit = hit_for(user_id)
    st = sharing_detect.verdicts().get(user_id)
    rows = list(st.rows) if st else []
    labels = _server_labels(rows)
    sessions = [{"ip": r["ip"], "seen": _ts(r.get("lastSeen")),
                 "server": labels.get(str(r.get("node") or ""), "")} for r in rows]
    # Запросы подписки с облачных адресов — возможная наша инфраструктура: не показываем.
    shown = [r for r in recs if not sharing_detect.in_yandex_cloud(str(r.get("requestIp") or ""))]
    hidden = len(recs) - len(shown)
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(minutes=WINDOW_MIN)
    session_ips = [s["ip"] for s in sessions if s["seen"] and _utc(s["seen"]) >= cutoff]
    extra = [str(x.get("requestIp")) for x in devices if x.get("requestIp")]
    extra += [str(x.get("requestIp")) for x in shown if x.get("requestIp")]
    want = list(dict.fromkeys(session_ips + extra))[:ip_geo.BATCH]
    geo = await ip_geo.lookup(db, want, fetch=fetch_geo)
    return ReportData(
        now=now, card=card, devices=devices, sub_records=shown, sessions=sessions, geo=geo,
        zone=str(hit.get("level") or ""), score=int(hit.get("score") or 0),
        banned_at=banned_at, sub_hidden=hidden,
    )


def report_filename(user_id: int, username: str, now: datetime | None = None,
                    ext: str = "md") -> str:
    safe = re.sub(r"[^A-Za-z0-9_-]+", "", username or "") or "user"
    return f"sharing-{user_id}-{safe}-{(now or datetime.now(timezone.utc)):%Y%m%d}.{ext}"


# ── сохранённая копия ────────────────────────────────────────────────────────

async def get_saved(db: AsyncSession, user_id: int) -> dict | None:
    data = await subwatch._json_setting(db, f"{SAVED_PREFIX}{user_id}", {})
    return data if data.get("text") else None


async def store(db: AsyncSession, user_id: int, text: str, username: str, now: datetime,
                client: str = "") -> None:
    await subwatch._save_json(db, f"{SAVED_PREFIX}{user_id}", {
        "ts": now.isoformat(), "username": username, "text": text, "client": client,
    })


@dataclass
class BanReport:
    text: str           # технический отчёт (markdown)
    client: str         # клиентский текст
    username: str


async def prepare_for_ban(db: AsyncSession, user_id: int, now: datetime) -> BanReport | None:
    """Отчёты для бана; строятся ДО отключения, пока адреса ещё на серверах.
    Любая ошибка — не повод срывать бан, вызывающий ловит исключения."""
    data = await collect(db, user_id, banned_at=now)
    if data is None:
        return None
    return BanReport(build_report(data), build_client_text(data), str(data.card.get("username") or ""))
