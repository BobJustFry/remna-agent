"""Оценка шаринга: вместо одного порога на всех — баллы с поправкой на лимит устройств.

Жёсткий порог «8 адресов за 5 минут» одинаково судил человека с лимитом 1 и
семью с лимитом 5. У первого восемь адресов — ферма, у второй — пятеро дома и в
дороге. Поэтому норма считается от лимита устройств пользователя, а вердикт
складывается из нескольких признаков: ни один из них сам по себе не приговор.

Признаки и веса (сумма 100). Вес тем больше, чем труднее признак подделать
обычной жизнью абонента:

  24  одновременно живых адресов     — пачка адресов в одну минуту; роуминг по
                                       вышкам даёт их последовательно
  24  одновременно живых операторов  — разные сети в одну минуту; это уже не
                                       «переехал с Wi-Fi на LTE»
   8  уникальных адресов за 5 мин    — слабее: мобильный абонент меняет адрес
   4  уникальных адресов за 15 мин   — ещё слабее, окно шире
  14  одновременно активных платформ — iOS+Android+Windows при лимите 1 честно
                                       означает трёх разных людей
   9  невозможное перемещение        — ПРИЗНАК СЛАБЫЙ: страна берётся из
                                       регистрации сети (Team Cymru), а не из
                                       геобазы. Хостинг в Нидерландах с
                                       российскими абонентами даст «переезд» на
                                       ровном месте, потому вес невелик
   9  одновременных HWID сверх лимита — переустановки идут друг за другом,
                                       одновременные устройства живут вместе
   5  нет ночной паузы               — у человека есть провал на сон, у фермы нет
   3  трафик против медианы парка    — сам по себе ничего не доказывает

Каждый признак даёт `вес × насколько превышена норма`, с насыщением на двойной
норме: вдвое больше нормы — весь вес, ровно норма — ноль. Так один выброс не
красит, а совпадение трёх признаков красит.

Тревога — только когда красная зона держится несколько проверок подряд:
переезд Wi-Fi↔LTE даёт один красный скан и проходит.

Здесь только чистые функции: ни сети, ни базы. Всё, что приходит снаружи —
в Features, всё, что настраивается — в cfg (лежит в app_settings).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

# Ключи настроек в app_settings.
CONFIG_KEY = "sharing_config"
STATE_KEY = "sharing_state"

GREEN = "green"
AMBER = "amber"
RED = "red"

DEFAULTS: dict = {
    "enabled": True,
    # Зоны. 60 — совпали два сильных признака или один сильный и два слабых.
    "red": 60,
    "amber": 35,
    # Сколько проверок подряд держаться в зоне, чтобы это считалось событием.
    "red_streak": 3,
    "green_streak": 3,

    # Лимит устройств. Пусто у пользователя — берём общий из панели
    # (/api/subscription-settings → hwidSettings.fallbackDeviceLimit), а если и
    # её не спросить — вот это число.
    "fallback_device_limit": 3,
    # 0 у пользователя значит «без лимита» — это платная услуга, такие люди
    # законно сидят с десятка устройств. Нормы считаем от этого числа.
    "unlimited_as": 10,

    # Нормы от лимита L: норма = L × на_устройство + запас.
    # На устройство два адреса: телефон живёт между Wi-Fi и LTE, плюс CGNAT.
    "ip_per_device": 2.0,
    "ip_slack_5m": 2.0,
    "ip_slack_15m": 6.0,      # L=3 → 12, ровно старый порог ips_15m
    # Одновременно (в окне 60 с) одно устройство держит один адрес, изредка два.
    # Числа выбраны по замеру живого парка 08.10.2026: на 328 онлайна максимум
    # три одновременных адреса и три оператора, у людей с лимитом 3, 5 и 6.
    # Норма при лимите 3 — 4.6 адреса и 3.6 оператора: выше всего, что
    # встречается у честных, и заметно ниже того, что даёт раздача на компанию.
    "conc_ip_per_device": 1.2,
    "conc_ip_slack": 1.0,
    "net_per_device": 0.8,
    "net_slack": 1.2,

    "w_conc_ips": 24,
    "w_conc_nets": 24,
    "w_ips_5m": 8,
    "w_ips_15m": 4,
    "w_platforms": 14,
    "w_travel": 9,
    "w_hwid": 9,
    "w_night": 5,
    "w_traffic": 3,

    # Трафик: во сколько раз больше медианы парка считается нормой.
    "traffic_median_mult": 5.0,
    # Ночная пауза: самое тихое окно такой ширины; занятость ниже quiet_pct —
    # пауза есть. Нужно хотя бы min_days суток наблюдений, иначе признак молчит.
    "night_window_h": 4,
    "night_min_days": 3,
    "night_quiet_pct": 15,
    "night_full_pct": 50,
    "night_keep_days": 14,
    # HWID считаем одновременными, если их updatedAt попал в это окно.
    "hwid_active_min": 60,

    # Карточки пользователей (лимит, трафик) перечитываем не чаще.
    "users_refresh_min": 30,
    # Сколько кандидатов за скан спрашивать о HWID: панель не долбим.
    "max_candidates": 60,

    "notify": True,
    "chat_id": "",            # пусто — общий чат следилки за подпиской
    "remind_hours": 24,
}


@dataclass
class Features:
    """Всё, что собрано о пользователе за один скан. Ничего не считает сам."""

    user_id: int
    username: str = ""
    # Лимит как его видит панель: None — не задан, 0 — без лимита.
    raw_limit: int | None = None
    ips_5m: int = 0
    ips_15m: int = 0
    conc_ips: int = 0
    conc_nets: int = 0
    # Страны регистрации сетей, живших одновременно. Слабый признак, см. шапку.
    conc_countries: list[str] = field(default_factory=list)
    # Платформы и HWID, активные в окне hwid_active_min. None — не спрашивали.
    platforms: list[str] | None = None
    hwid_active: int | None = None
    hwid_total: int | None = None
    # Занятость самого тихого окна суток, % сканов, и на скольких сутках считали.
    night_busy_pct: float | None = None
    night_days: int = 0
    # Трафик пользователя к медиане парка.
    traffic_ratio: float | None = None


@dataclass
class Part:
    key: str
    points: float
    why: str


@dataclass
class Verdict:
    user_id: int
    score: int
    level: str
    device_limit: int
    unlimited: bool
    parts: list[Part] = field(default_factory=list)

    @property
    def reasons(self) -> list[str]:
        return [p.why for p in self.parts]


def _num(cfg: dict, key: str) -> float:
    try:
        return float(cfg.get(key, DEFAULTS[key]))
    except (TypeError, ValueError):
        return float(DEFAULTS[key])


def device_limit(cfg: dict, raw: int | None) -> tuple[int, bool]:
    """(лимит, безлимит ли). Пусто — общий лимит панели, 0 — безлимит."""
    if raw is None:
        return max(1, int(_num(cfg, "fallback_device_limit"))), False
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return max(1, int(_num(cfg, "fallback_device_limit"))), False
    if value <= 0:
        return max(1, int(_num(cfg, "unlimited_as"))), True
    return value, False


def norms(cfg: dict, limit: int) -> dict[str, float]:
    """Сколько адресов, сетей и устройств для этого лимита ещё норма."""
    return {
        "ips_5m": limit * _num(cfg, "ip_per_device") + _num(cfg, "ip_slack_5m"),
        "ips_15m": limit * _num(cfg, "ip_per_device") + _num(cfg, "ip_slack_15m"),
        "conc_ips": limit * _num(cfg, "conc_ip_per_device") + _num(cfg, "conc_ip_slack"),
        "conc_nets": limit * _num(cfg, "net_per_device") + _num(cfg, "net_slack"),
        "platforms": float(limit),
        "hwid": float(limit),
    }


def _over(value: float, norm: float) -> float:
    """Насколько превышена норма: 0 — в норме, 1 — вдвое и больше."""
    if norm <= 0:
        return 0.0
    return max(0.0, min(1.0, value / norm - 1.0))


def _plural_ip(n: int) -> str:
    return f"{n} адрес" + ("" if n % 10 == 1 and n % 100 != 11 else
                           "а" if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14 else "ов")


def score(cfg: dict, f: Features) -> Verdict:
    """Оценка 0..100 и расшифровка «за что». Чистая функция."""
    limit, unlimited = device_limit(cfg, f.raw_limit)
    n = norms(cfg, limit)
    parts: list[Part] = []
    tail = f"лимит устройств {limit}" + (" (безлимит)" if unlimited else "")

    def add(key: str, weight_key: str, over: float, why: str) -> None:
        pts = _num(cfg, weight_key) * over
        if pts > 0.05:
            parts.append(Part(key=key, points=round(pts, 1), why=why))

    add("conc_ips", "w_conc_ips", _over(f.conc_ips, n["conc_ips"]),
        f"одновременно {_plural_ip(f.conc_ips)} в одном окне "
        f"(норма {n['conc_ips']:.1f}, {tail})")
    add("conc_nets", "w_conc_nets", _over(f.conc_nets, n["conc_nets"]),
        f"одновременно операторов: {f.conc_nets} (норма {n['conc_nets']:.1f}, {tail}) — "
        "разные сети в одну минуту, а не смена адреса")
    add("ips_5m", "w_ips_5m", _over(f.ips_5m, n["ips_5m"]),
        f"уникальных адресов за 5 мин: {f.ips_5m} (норма {n['ips_5m']:.1f})")
    add("ips_15m", "w_ips_15m", _over(f.ips_15m, n["ips_15m"]),
        f"уникальных адресов за 15 мин: {f.ips_15m} (норма {n['ips_15m']:.1f})")

    if f.platforms:
        extra = len(f.platforms) - limit
        add("platforms", "w_platforms", max(0.0, min(1.0, extra / max(1, limit))),
            f"одновременно платформ: {len(f.platforms)} "
            f"({', '.join(f.platforms)}) при лимите {limit}")

    countries = [c for c in (f.conc_countries or []) if c]
    if len(countries) > 1:
        add("travel", "w_travel", min(1.0, (len(countries) - 1) / 2.0),
            f"в одном окне сети разных стран: {', '.join(sorted(set(countries)))} "
            "— признак слабый, страна берётся из регистрации сети, не из геобазы")

    if f.hwid_active is not None and f.hwid_active > 0:
        extra = f.hwid_active - limit
        add("hwid", "w_hwid", max(0.0, min(1.0, extra / max(1, limit))),
            f"одновременно активных HWID: {f.hwid_active} из {f.hwid_total or f.hwid_active} "
            f"при лимите {limit} (не переустановки — устройства живут вместе)")

    if f.night_busy_pct is not None and f.night_days >= int(_num(cfg, "night_min_days")):
        quiet = _num(cfg, "night_quiet_pct")
        full = max(quiet + 1, _num(cfg, "night_full_pct"))
        over = max(0.0, min(1.0, (f.night_busy_pct - quiet) / (full - quiet)))
        add("night", "w_night", over,
            f"нет ночной паузы: в самом тихом окне суток онлайн "
            f"{f.night_busy_pct:.0f}% сканов (наблюдений {f.night_days} сут.)")

    if f.traffic_ratio is not None:
        mult = max(1.0, _num(cfg, "traffic_median_mult"))
        add("traffic", "w_traffic", max(0.0, min(1.0, (f.traffic_ratio - mult) / mult)),
            f"трафик ×{f.traffic_ratio:.1f} от медианы парка")

    total = round(sum(p.points for p in parts))
    parts.sort(key=lambda p: -p.points)
    return Verdict(
        user_id=f.user_id,
        score=int(min(100, total)),
        level=level_of(cfg, total),
        device_limit=limit,
        unlimited=unlimited,
        parts=parts,
    )


def level_of(cfg: dict, value: float) -> str:
    if value >= _num(cfg, "red"):
        return RED
    if value >= _num(cfg, "amber"):
        return AMBER
    return GREEN


# ── выдержка в зоне ──────────────────────────────────────────────────────────

def empty_user_state() -> dict:
    return {"red": 0, "green": 0, "alerted": False, "since": None,
            "last_notified": None, "score": 0, "level": GREEN}


def step_user(state: dict | None, cfg: dict, level: str, score_value: int,
              now: datetime) -> tuple[dict, str | None]:
    """Новое состояние пользователя и событие: "alert" | "remind" | "clear" | None.

    Красная зона сама по себе ничего не значит: человек переехал с Wi-Fi на LTE,
    дом и дача разом, гость с телефоном. Значит выдержка — красным нужно быть
    несколько проверок подряд. Так же и обратно: один зелёный скан не снимает
    тревогу, иначе ферма «успокаивалась» бы между пачками.
    """
    st = dict(state or empty_user_state())
    st["score"] = int(score_value)
    st["level"] = level

    if level == RED:
        st["red"] = int(st.get("red") or 0) + 1
        st["green"] = 0
    else:
        st["green"] = int(st.get("green") or 0) + 1
        if level != AMBER:
            st["red"] = 0

    need_red = max(1, int(_num(cfg, "red_streak")))
    need_green = max(1, int(_num(cfg, "green_streak")))

    if not st.get("alerted"):
        if level == RED and st["red"] >= need_red:
            st.update(alerted=True, since=now.isoformat(), last_notified=now.isoformat())
            return st, "alert"
        return st, None

    if st["green"] >= need_green:
        st.update(alerted=False, since=None, last_notified=None, red=0)
        return st, "clear"

    hours = _num(cfg, "remind_hours")
    if hours > 0 and level == RED:
        last = st.get("last_notified")
        try:
            waited = now - datetime.fromisoformat(last) if last else None
        except (TypeError, ValueError):
            waited = None
        if waited is None or waited >= timedelta(hours=hours):
            st["last_notified"] = now.isoformat()
            return st, "remind"
    return st, None


# ── ночной профиль ───────────────────────────────────────────────────────────

def quiet_window(hours: dict[int, tuple[int, int]], width: int = 4) -> float | None:
    """Занятость самого тихого окна суток, % сканов.

    hours: {час UTC: (сканов всего, сканов где человек был онлайн)}. Окно
    катается по кругу, потому что часовой пояс абонента нам неизвестен: нас
    интересует не «спит ли он ночью по Москве», а есть ли у него вообще провал.
    Возвращает None, если суток наблюдений не хватает на полный круг.
    """
    if len(hours) < 24:
        return None
    width = max(1, min(12, int(width)))
    best: float | None = None
    for start in range(24):
        scans = seen = 0
        for k in range(width):
            total, hit = hours.get((start + k) % 24, (0, 0))
            scans += total
            seen += hit
        if scans <= 0:
            return 0.0      # целое окно без единого скана — считать нечего, пауза
        pct = seen * 100.0 / scans
        if best is None or pct < best:
            best = pct
    return best
