"""Памятьсторож: раздувшееся ядро xray на ноде — тревога сразу, перезапуск только под защитами.

После шарера процесс rw-core на ряде нод раздулся до 800–1100 МБ, своп забит,
на 1-гигабайтных нодах пошли OOM. Лечится перезапуском ядра (панель:
POST /api/nodes/{uuid}/actions/restart {"forceRestart": true}), но «лечить»
утечку бесконечными перезапусками нельзя: ядро не должно улететь в частые
перезапуски, а повторная проблема после перезапуска — повод для тревоги, а не
для очередного перезапуска.

Правила, коротко:
  • Тревога — всегда и сразу (после короткого подтверждения, чтобы не ловить
    всплески): отметка в интерфейсе, запись в журнал, Телеграм, если бот есть.
    Без бота работает всё остальное.
  • Автоперезапуск — только если ВСЕ защиты пройдены (см. decide()). Любой отказ
    защиты — это тревога, а не повод повторять.
  • Пока `dry_run` — считаем и показываем, что бы сделали, но ничего не
    перезапускаем. Боевой режим включает владелец одним переключателем.
  • Каждое действие озвучено: сообщение/запись ДО и ПОСЛЕ.

Чистая логика (verdict / advance / decide) не ходит ни в базу, ни в сеть —
на ней тесты. База, панель и Телеграм живут вокруг.
"""
from __future__ import annotations

import asyncio
import html
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.services import subwatch
from app.services.telegram import send_message

log = logging.getLogger("remna.memwatch")

CONFIG_KEY = "mem_watch_config"
STATE_KEY = "mem_watch_state"
LOG_KEY = "mem_watch_log"
LOG_KEEP = 300
RESTARTS_KEEP = timedelta(days=8)

DEFAULTS: dict = {
    "enabled": True,
    # Первые сутки — сухой прогон: считаем и показываем, не перезапускаем.
    "dry_run": True,
    "auto_restart": True,
    "notify": True,
    "chat_id": "",                  # пусто — чат следилки за подпиской
    # Пороги тревоги (любой из трёх):
    "rss_min_mb": 300,              # rw-core RSS > max(rss_min_mb, rss_pct % ОЗУ)
    "rss_pct": 35,
    # Большой RSS ядра сам по себе — не тревога: он считается, только если ноде
    # уже тесно (свободной памяти меньше rss_pressure_avail_pct % или своп занят
    # больше rss_pressure_swap_pct %). Если эти метрики неизвестны — судим по RSS.
    "rss_pressure_avail_pct": 20,
    "rss_pressure_swap_pct": 30,
    "avail_pct": 12,               # MemAvailable < 12 % ОЗУ
    "swap_pct": 60,                 # своп занят > 60 %
    "critical_avail_pct": 5,        # MemAvailable < 5 % или OOM — критично
    "oom_window_min": 10,           # сколько после OOM-килла нода считается критичной
    "confirm_ticks": 6,             # сколько опросов (по 30 с) подряд, чтобы поднять тревогу
    "clear_ticks": 20,              # сколько опросов подряд в норме, чтобы снять
    "remind_hours": 6,
    # Защиты автоперезапуска:
    "cooldown_h": 12,               # с прошлого перезапуска этой ноды
    "min_core_uptime_h": 12,        # аптайм ядра перед перезапуском
    "max_per_node_24h": 1,
    "max_per_node_7d": 2,
    "fleet_gap_min": 10,            # между перезапусками по всему парку
    "fleet_per_hour": 4,
    "window_start": 3,              # тихое окно, местное время (utc_offset)
    "window_end": 6,
    "utc_offset": 3,                # МСК
    "post_check_after_s": 150,      # когда проверять, что ядро вернулось
    "post_check_deadline_s": 420,   # не вернулось к этому сроку — тревога
    "relapse_h": 24,                # повторный разгон после перезапуска
    # Исключения: только тревожим, не перезапускаем.
    "exclude": ["HAProxy-1", "HAProxy-2", "cn-1", "ua-1"],   # имена нод целиком
    "exclude_contains": ["bot"],                             # подстрока в имени
}

# Целочисленные/числовые границы настроек — тот же словарь использует API.
LIMITS: dict[str, tuple[float, float]] = {
    "rss_min_mb": (50, 4096), "rss_pct": (5, 90), "avail_pct": (1, 50),
    "rss_pressure_avail_pct": (1, 90), "rss_pressure_swap_pct": (1, 100),
    "swap_pct": (10, 100), "critical_avail_pct": (1, 30), "oom_window_min": (1, 120),
    "confirm_ticks": (1, 120), "clear_ticks": (1, 720), "remind_hours": (0, 168),
    "cooldown_h": (1, 168), "min_core_uptime_h": (0, 168),
    "max_per_node_24h": (0, 5), "max_per_node_7d": (0, 20),
    "fleet_gap_min": (1, 240), "fleet_per_hour": (1, 12),
    "window_start": (0, 23), "window_end": (0, 24), "utc_offset": (-12, 14),
    "post_check_after_s": (30, 1800), "post_check_deadline_s": (60, 3600),
    "relapse_h": (1, 168),
}

# Ядру одного перезапуска хватает на всё: сколько ждать, пока панель отдаст
# новый xrayUptime после рестарта, мы смотрим в post-check.
SSH_PROBE_EVERY = timedelta(minutes=5)

# Записи состояния и журнала лежат в app_settings. Блокировка — чтобы ручной
# перезапуск из API и тик сэмплера не писали состояние одновременно.
_lock = asyncio.Lock()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def _parse(value) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def clock(dt: datetime, cfg: dict) -> str:
    """HH:MM в часовом поясе владельца (по умолчанию МСК)."""
    local = dt + timedelta(hours=int(cfg.get("utc_offset", 3)))
    return local.strftime("%H:%M")


def clean_config(cfg: dict) -> dict:
    """Привести значения к границам: из браузера может прийти что угодно."""
    out = dict(cfg)
    for key, (lo, hi) in LIMITS.items():
        try:
            out[key] = int(max(lo, min(hi, float(out.get(key, DEFAULTS[key])))))
        except (TypeError, ValueError):
            out[key] = DEFAULTS[key]
    for key in ("enabled", "dry_run", "auto_restart", "notify"):
        out[key] = bool(out.get(key, DEFAULTS[key]))
    for key in ("exclude", "exclude_contains"):
        raw = out.get(key)
        out[key] = sorted({str(x).strip() for x in (raw if isinstance(raw, list) else []) if str(x).strip()})
    out["chat_id"] = str(out.get("chat_id") or "").strip()
    return out


# ── что видим ────────────────────────────────────────────────────────────────

@dataclass
class Obs:
    """Что рагент знает о ноде за один опрос."""

    node_id: str
    name: str
    host: str
    answered: bool = False                 # агент ответил именно сейчас
    mem_total_mb: float | None = None
    mem_available_mb: float | None = None
    mem_percent: float | None = None       # занято, % (total - available)
    swap_percent: float | None = None
    rss_mb: float | None = None            # RSS rw-core; None — неизвестно
    rss_source: str | None = None          # "agent" | "ssh"
    core_age_s: int | None = None          # возраст процесса ядра по данным ноды
    oom_delta: int | None = None           # OOM-киллов с прошлого опроса
    has_core: bool | None = None           # remnanode_running
    panel_uuid: str | None = None
    panel_connected: bool | None = None
    panel_uptime_s: int | None = None      # xrayUptime из панели
    panel_dup: bool = False                # адрес занят несколькими узлами панели
    panel_disabled: bool = False

    @property
    def avail_pct(self) -> float | None:
        total = self.mem_total_mb
        if not total:
            return None
        if self.mem_available_mb is not None:
            return round(self.mem_available_mb / total * 100.0, 1)
        if self.mem_percent is not None:
            return round(100.0 - self.mem_percent, 1)
        return None

    @property
    def core_uptime_s(self) -> int | None:
        """Аптайм ядра: панель знает его лучше, возраст процесса — запасной."""
        return self.panel_uptime_s if self.panel_uptime_s is not None else self.core_age_s


@dataclass
class Verdict:
    kinds: list[str] = field(default_factory=list)    # rss | avail | swap | oom
    reasons: list[str] = field(default_factory=list)
    critical: bool = False
    rss_limit_mb: float | None = None
    rss_known: bool = False


def rss_limit(total_mb: float | None, cfg: dict) -> float | None:
    if not total_mb:
        return None
    return round(max(float(cfg["rss_min_mb"]), total_mb * float(cfg["rss_pct"]) / 100.0), 1)


def verdict(obs: Obs, cfg: dict, oom_active: bool = False) -> Verdict:
    v = Verdict(rss_known=obs.rss_mb is not None)
    total = obs.mem_total_mb
    v.rss_limit_mb = rss_limit(total, cfg)
    ap = obs.avail_pct
    if v.rss_known and v.rss_limit_mb is not None and obs.rss_mb > v.rss_limit_mb:
        tight_avail = ap is not None and ap < float(cfg.get("rss_pressure_avail_pct", 20))
        tight_swap = (obs.swap_percent is not None
                      and obs.swap_percent > float(cfg.get("rss_pressure_swap_pct", 30)))
        unknown = ap is None and obs.swap_percent is None
        if tight_avail or tight_swap or unknown:
            v.kinds.append("rss")
            v.reasons.append(f"rw-core {obs.rss_mb:.0f} МБ при пороге {v.rss_limit_mb:.0f} МБ")
    if ap is not None and ap < float(cfg["avail_pct"]):
        v.kinds.append("avail")
        v.reasons.append(f"свободной памяти {ap:.0f}% при пороге {cfg['avail_pct']}%")
    if ap is not None and ap < float(cfg["critical_avail_pct"]):
        v.critical = True
    if obs.swap_percent is not None and obs.swap_percent > float(cfg["swap_pct"]):
        v.kinds.append("swap")
        v.reasons.append(f"своп занят на {obs.swap_percent:.0f}% при пороге {cfg['swap_pct']}%")
    if oom_active:
        v.kinds.append("oom")
        v.reasons.append("идут OOM-киллы")
        v.critical = True
    return v


def is_excluded(name: str, cfg: dict) -> bool:
    low = name.strip().lower()
    if low in {str(x).strip().lower() for x in cfg.get("exclude") or []}:
        return True
    return any(str(s).strip() and str(s).strip().lower() in low for s in cfg.get("exclude_contains") or [])


# ── состояние ноды ───────────────────────────────────────────────────────────

def new_node_state() -> dict:
    return {"bad": 0, "ok": 0, "alarm": None, "blocked": None, "pending": None,
            "last_restart_at": None, "oom_until": None, "last": {}, "decision": None,
            "dry_key": None, "probe_at": None}


def _event(kind: str, text: str, **extra) -> dict:
    return {"kind": kind, "text": text, **extra}


def _in_window(now: datetime, cfg: dict) -> bool:
    hour = (now + timedelta(hours=int(cfg["utc_offset"]))).hour
    start, end = int(cfg["window_start"]), int(cfg["window_end"])
    if start == end:
        return True
    if start < end:
        return start <= hour < end
    return hour >= start or hour < end


def advance(st: dict, obs: Obs, cfg: dict, now: datetime) -> tuple[Verdict | None, list[dict]]:
    """Один опрос одной ноды: счётчики, тревога, post-check. Возвращает события."""
    events: list[dict] = []
    if not obs.answered:
        # Агент молчит — это дело следилки за нодами; наши счётчики не трогаем.
        return None, events

    if obs.oom_delta and obs.oom_delta > 0:
        st["oom_until"] = _iso(now + timedelta(minutes=int(cfg["oom_window_min"])))
    until = _parse(st.get("oom_until"))
    oom_active = bool(until and now < until)

    v = verdict(obs, cfg, oom_active)
    st["last"] = {
        "at": _iso(now), "rss_mb": obs.rss_mb, "rss_source": obs.rss_source,
        "rss_limit_mb": v.rss_limit_mb, "mem_total_mb": obs.mem_total_mb,
        "avail_pct": obs.avail_pct, "swap_percent": obs.swap_percent,
        "core_uptime_s": obs.core_uptime_s, "kinds": v.kinds, "reasons": v.reasons,
        "critical": v.critical, "oom": oom_active,
    }
    if v.kinds:
        st["bad"] = int(st.get("bad") or 0) + 1
        st["ok"] = 0
    else:
        st["ok"] = int(st.get("ok") or 0) + 1
        st["bad"] = 0

    alarm = st.get("alarm")
    if v.kinds and alarm is None and (st["bad"] >= int(cfg["confirm_ticks"]) or v.critical):
        alarm = {"since": _iso(now), "kinds": v.kinds, "critical": v.critical,
                 "reminded_at": _iso(now), "max_rss_mb": obs.rss_mb}
        st["alarm"] = alarm
        why = "; ".join(v.reasons)
        events.append(_event("alarm", f"{'КРИТИЧНО: ' if v.critical else ''}память: {why}"))
        lr = _parse(st.get("last_restart_at"))
        if lr and now - lr < timedelta(hours=int(cfg["relapse_h"])) and not st.get("blocked"):
            st["blocked"] = {"reason": "relapse", "at": _iso(now)}
            events.append(_event(
                "relapse",
                f"повторный разгон: ядро перезапускали в {clock(lr, cfg)} МСК, а память снова "
                f"выше порога. Автоперезапуск этой ноды заблокирован до ручного сброса — "
                f"нужен человек (утечка или системная проблема)."))
    elif alarm is not None:
        alarm["kinds"] = v.kinds or alarm.get("kinds") or []
        if obs.rss_mb is not None:
            alarm["max_rss_mb"] = max(float(alarm.get("max_rss_mb") or 0), obs.rss_mb)
        if v.critical and not alarm.get("critical"):
            alarm["critical"] = True
            events.append(_event("alarm_critical", f"стало критично: {'; '.join(v.reasons)}"))
        if not v.kinds and st["ok"] >= int(cfg["clear_ticks"]):
            st["alarm"] = None
            st["dry_key"] = None
            events.append(_event("alarm_clear", "память в норме, тревога снята"))
        else:
            hrs = int(cfg["remind_hours"])
            rem = _parse(alarm.get("reminded_at"))
            if hrs and v.kinds and rem and now - rem >= timedelta(hours=hrs):
                alarm["reminded_at"] = _iso(now)
                events.append(_event("alarm_remind", f"проблема с памятью держится: {'; '.join(v.reasons)}"))

    # Контроль после перезапуска.
    pend = st.get("pending")
    if pend:
        at = _parse(pend.get("at"))
        elapsed = (now - at).total_seconds() if at else 1e9
        if elapsed >= int(cfg["post_check_after_s"]):
            up = obs.panel_uptime_s
            reset = up is not None and up < elapsed + 30
            if obs.panel_connected is True and reset:
                st["pending"] = None
                events.append(_event(
                    "restart_ok",
                    f"ядро вернулось: нода connected, аптайм ядра {up} с, "
                    f"rw-core сейчас {obs.rss_mb:.0f} МБ" if obs.rss_mb is not None
                    else f"ядро вернулось: нода connected, аптайм ядра {up} с",
                    rss_after=obs.rss_mb))
            elif elapsed >= int(cfg["post_check_deadline_s"]):
                st["pending"] = None
                st["blocked"] = {"reason": "post_check", "at": _iso(now)}
                events.append(_event(
                    "restart_failed",
                    f"после перезапуска ядро не подтвердилось за {int(elapsed)} с "
                    f"(connected={obs.panel_connected}, аптайм ядра={up}). "
                    f"Повторных попыток не будет, автоперезапуск ноды заблокирован — нужен человек."))
    return v, events


# ── решение о перезапуске ────────────────────────────────────────────────────

@dataclass
class Decision:
    action: str            # none | alarm_only | blocked | wait | wait_window | restart
    reason: str = ""


@dataclass
class FleetCtx:
    restarts: list[dict] = field(default_factory=list)   # {"ts", "node_id", "source"}
    pending_nodes: list[str] = field(default_factory=list)


def _count(restarts: list[dict], node_id: str | None, since: datetime, auto_only: bool = True) -> int:
    n = 0
    for r in restarts:
        if node_id is not None and r.get("node_id") != node_id:
            continue
        if auto_only and r.get("source") != "auto":
            continue
        ts = _parse(r.get("ts"))
        if ts and ts >= since:
            n += 1
    return n


def decide(obs: Obs, st: dict, v: Verdict | None, fleet: FleetCtx, cfg: dict, now: datetime) -> Decision:
    """Можно ли перезапускать ядро прямо сейчас. Любое «нет» — это тревога, не повод повторять."""
    alarm = st.get("alarm")
    if not alarm or v is None:
        return Decision("none")
    if is_excluded(obs.name, cfg):
        return Decision("alarm_only", "нода в списке исключений")
    if obs.has_core is False:
        return Decision("alarm_only", "на ноде нет rw-core")
    if not cfg.get("auto_restart"):
        return Decision("alarm_only", "автоперезапуск выключен — только тревоги")
    if st.get("pending"):
        return Decision("wait", "идёт проверка прошлого перезапуска")
    blocked = st.get("blocked")
    if blocked:
        names = {"relapse": "повторный разгон", "post_check": "ядро не подтвердилось после перезапуска",
                 "api_error": "панель не приняла перезапуск"}
        return Decision("blocked", f"автоперезапуск заблокирован ({names.get(blocked.get('reason'), blocked.get('reason'))}), "
                                   f"нужен человек")
    # (а) нода жива для панели и для нас
    if not obs.panel_uuid:
        return Decision("alarm_only", "узел в панели не найден по адресу")
    if obs.panel_dup:
        return Decision("alarm_only", "адрес ноды занят несколькими узлами панели — цель неоднозначна")
    if obs.panel_disabled:
        return Decision("alarm_only", "нода выключена в панели")
    if obs.panel_connected is not True:
        return Decision("wait", "нода не connected в панели")
    if not obs.answered:
        return Decision("wait", "агент не отвечает")
    # Ядро — виновник? Без RSS или при малом RSS перезапуск не поможет.
    if obs.rss_mb is None:
        return Decision("alarm_only", "RSS ядра неизвестен (агент старше 0.1.23, SSH-замер не удался)")
    if obs.rss_mb < float(cfg["rss_min_mb"]):
        return Decision("alarm_only", f"rw-core занимает всего {obs.rss_mb:.0f} МБ — память съедена не ядром, перезапуск не поможет")
    # (б) cooldown и аптайм ядра
    up = obs.core_uptime_s
    if up is None:
        return Decision("alarm_only", "аптайм ядра неизвестен")
    if up < int(cfg["min_core_uptime_h"]) * 3600:
        return Decision("blocked", f"ядро живёт всего {up // 3600} ч {up % 3600 // 60} мин (< {cfg['min_core_uptime_h']} ч): "
                                   f"так быстро раздуваться — не то, что лечит перезапуск; только тревога, нужен человек")
    lr = _parse(st.get("last_restart_at"))
    if lr and now - lr < timedelta(hours=int(cfg["cooldown_h"])):
        return Decision("blocked", f"прошлый перезапуск в {clock(lr, cfg)} МСК, пауза {cfg['cooldown_h']} ч не вышла")
    # (в) лимиты на ноду
    n24 = _count(fleet.restarts, obs.node_id, now - timedelta(hours=24))
    n7 = _count(fleet.restarts, obs.node_id, now - timedelta(days=7))
    if n24 >= int(cfg["max_per_node_24h"]) or n7 >= int(cfg["max_per_node_7d"]):
        return Decision("blocked", f"лимит автоперезапусков исчерпан ({n24} за сутки, {n7} за 7 дней): "
                                   f"утечка или системная проблема, автоперезапуск отключён для ноды, нужен человек")
    # (г) глобальные лимиты, строго по одному
    if fleet.pending_nodes and any(p != obs.node_id for p in fleet.pending_nodes):
        return Decision("wait", "идёт проверка другого перезапуска — строго по одному")
    last_any = [t for t in (_parse(r.get("ts")) for r in fleet.restarts if r.get("source") == "auto") if t]
    if last_any and now - max(last_any) < timedelta(minutes=int(cfg["fleet_gap_min"])):
        return Decision("wait", f"по парку не чаще 1 перезапуска за {cfg['fleet_gap_min']} мин")
    if _count(fleet.restarts, None, now - timedelta(hours=1)) >= int(cfg["fleet_per_hour"]):
        return Decision("wait", f"по парку не больше {cfg['fleet_per_hour']} перезапусков в час")
    # (д) окно
    critical = bool(alarm.get("critical"))
    if not critical and not _in_window(now, cfg):
        return Decision("wait_window", f"не критично — ждём тихое окно {cfg['window_start']:02d}:00–{cfg['window_end']:02d}:00 МСК"
                        if int(cfg["utc_offset"]) == 3 else
                        f"не критично — ждём тихое окно {cfg['window_start']:02d}:00–{cfg['window_end']:02d}:00")
    return Decision("restart", "критично, перезапуск сразу" if critical else "тихое окно")


# ── база: настройки, состояние, журнал ───────────────────────────────────────

async def get_config(db: AsyncSession) -> dict:
    return clean_config(await subwatch._json_setting(db, CONFIG_KEY, DEFAULTS))


async def save_config(db: AsyncSession, cfg: dict) -> None:
    await subwatch._save_json(db, CONFIG_KEY, clean_config(cfg))


async def get_state(db: AsyncSession) -> dict:
    st = await subwatch._json_setting(db, STATE_KEY, {"nodes": {}, "restarts": []})
    if not isinstance(st.get("nodes"), dict):
        st["nodes"] = {}
    if not isinstance(st.get("restarts"), list):
        st["restarts"] = []
    return st


async def get_log(db: AsyncSession) -> list[dict]:
    data = await subwatch._json_setting(db, LOG_KEY, {"rows": []})
    rows = data.get("rows")
    return rows if isinstance(rows, list) else []


async def _append_log(db: AsyncSession, rows: list[dict]) -> None:
    if not rows:
        return
    old = await get_log(db)
    await subwatch._save_json(db, LOG_KEY, {"rows": (old + rows)[-LOG_KEEP:]})


async def effective_chat(db: AsyncSession, cfg: dict) -> str:
    return (cfg.get("chat_id") or "").strip() or \
        ((await subwatch.get_config(db)).get("chat_id") or "").strip()


async def bot_ready(db: AsyncSession, cfg: dict) -> bool:
    return bool(await subwatch.bot_token(db)) and bool(await effective_chat(db, cfg))


async def _notify(db: AsyncSession, cfg: dict, text: str) -> bool:
    """Телеграм, если бот настроен. Нет бота или сбой — не падаем: журнал уже записан."""
    if not cfg.get("notify"):
        return False
    try:
        token = await subwatch.bot_token(db)
        chat = await effective_chat(db, cfg)
        if not token or not chat:
            return False
        err = await asyncio.to_thread(send_message, token, chat, text)
        if err:
            log.warning("memwatch: уведомление не ушло: %s", err)
            return False
        return True
    except Exception:  # noqa: BLE001
        log.exception("memwatch: уведомление упало")
        return False


def _prefix(cfg: dict) -> str:
    return "🧪 [сухой прогон] " if cfg.get("dry_run") else ""


def _row(now: datetime, obs: Obs | None, kind: str, text: str, cfg: dict, **extra) -> dict:
    return {
        "ts": _iso(now), "kind": kind, "node_id": obs.node_id if obs else None,
        "name": obs.name if obs else None, "text": text,
        "dry_run": bool(cfg.get("dry_run")), "notified": False, **extra,
    }


_ICONS = {"alarm": "🔴", "alarm_critical": "🚨", "alarm_remind": "🔁", "alarm_clear": "🟢",
          "relapse": "⛔", "restart_failed": "⛔", "restart_ok": "✅", "restart_start": "🔄",
          "restart_plan": "🧪", "blocked": "⛔", "manual_restart": "🔄", "restart_error": "⛔"}


def tg_text(cfg: dict, name: str, kind: str, text: str) -> str:
    return f"{_ICONS.get(kind, 'ℹ️')} <b>{html.escape(name)}</b>: {html.escape(text)}"


# ── панель ───────────────────────────────────────────────────────────────────

def _panel_restart(uuid: str) -> str | None:
    """None — панель приняла; иначе текст ошибки. Звать из to_thread."""
    from app.services.remnawave_api import RemnawaveApiError, rw_api

    try:
        rw_api(f"/api/nodes/{uuid}/actions/restart", "POST", {"forceRestart": True})
        return None
    except RemnawaveApiError as exc:
        return exc.message


# ── SSH-замер RSS для старых агентов ─────────────────────────────────────────

_PS_CMD = ("ps -C rw-core -o rss=,etimes= 2>/dev/null; ps -C xray -o rss=,etimes= 2>/dev/null; true")


def parse_ps(out: str) -> tuple[float | None, int | None]:
    """(RSS МБ, возраст с) самого тяжёлого процесса из вывода ps."""
    best: tuple[int, int] | None = None
    for line in out.splitlines():
        m = re.match(r"^\s*(\d+)\s+(\d+)\s*$", line)
        if not m:
            continue
        rss, age = int(m.group(1)), int(m.group(2))
        if best is None or rss > best[0]:
            best = (rss, age)
    if best is None:
        return None, None
    return round(best[0] / 1024, 1), best[1]


def _ssh_probe(node) -> tuple[float | None, int | None]:
    """Только чтение: ps на ноде. Звать из to_thread, только для нод в тревоге."""
    from app.services.crypto import decrypt_secret
    from app.services.ssh_client import run_command, ssh_connect

    password = decrypt_secret(node.password_enc) if node.password_enc else None
    key = decrypt_secret(node.private_key_enc) if node.private_key_enc else None
    with ssh_connect(host=node.host, port=node.ssh_port, username=node.ssh_user,
                     auth_type=node.auth_type, password=password, private_key=key,
                     timeout=10.0) as client:
        _code, out, _err = run_command(client, _PS_CMD, timeout=15.0)
    return parse_ps(out)


# ── тик ──────────────────────────────────────────────────────────────────────

def _prune_restarts(rows: list[dict], now: datetime) -> list[dict]:
    keep = []
    for r in rows:
        ts = _parse(r.get("ts"))
        if ts and now - ts <= RESTARTS_KEEP:
            keep.append(r)
    return keep


def _priority(obs: Obs, st: dict) -> tuple:
    alarm = st.get("alarm") or {}
    return (0 if alarm.get("critical") else 1, -(obs.rss_mb or 0))


async def tick(db: AsyncSession, cfg: dict, observations: list[Obs],
               nodes_by_id: dict | None = None, now: datetime | None = None,
               existing_ids: set[str] | None = None) -> list[dict]:
    """Один проход: тревоги, решения, (при боевом режиме) один перезапуск.

    Возвращает записи журнала — тесты и ручной прогон видят решение, ничего
    не отправляя наружу. Коммит делаем сами: перезапуск должен быть записан
    в базу ДО обращения к панели, иначе сбой между ними мог бы его повторить.
    """
    now = now or _now()
    async with _lock:
        state = await get_state(db)
        before = repr(state)
        nodes_state: dict = state["nodes"]
        restarts = _prune_restarts(state["restarts"], now)
        state["restarts"] = restarts
        rows: list[dict] = []
        to_notify: list[tuple[dict, str]] = []

        # Забытые ноды (удалены из рагента) убираем.
        active = {o.node_id for o in observations}
        live = active if existing_ids is None else existing_ids
        for gone in [k for k in nodes_state if k not in live]:
            nodes_state.pop(gone, None)
        for paused in live - active:
            if paused in nodes_state:
                st = nodes_state[paused]
                for key in ("bad", "ok"):
                    st[key] = 0
                for key in ("alarm", "last", "decision", "dry_key", "probe_at", "oom_until"):
                    st[key] = {} if key == "last" else None
                st.pop("probe", None)

        verdicts: dict[str, Verdict | None] = {}
        for obs in observations:
            st = nodes_state.setdefault(obs.node_id, new_node_state())
            # RSS по SSH — только когда нода уже под подозрением, агент RSS не
            # прислал и недавно не спрашивали.
            if obs.answered and obs.rss_mb is None and nodes_by_id and obs.node_id in nodes_by_id:
                prelim = verdict(obs, cfg, False)
                probe_at = _parse(st.get("probe_at"))
                if (prelim.kinds or st.get("alarm")) and obs.has_core is not False and \
                        not is_excluded(obs.name, cfg) and \
                        (probe_at is None or now - probe_at >= SSH_PROBE_EVERY):
                    st["probe_at"] = _iso(now)
                    try:
                        rss, age = await asyncio.wait_for(
                            asyncio.to_thread(_ssh_probe, nodes_by_id[obs.node_id]), timeout=25)
                        if rss is not None:
                            st["probe"] = {"rss_mb": rss, "age_s": age, "at": _iso(now)}
                    except Exception as exc:  # noqa: BLE001
                        log.info("memwatch: SSH-замер %s не удался: %s", obs.name, exc)
                        st.pop("probe", None)
                pr = st.get("probe") or {}
                pr_at = _parse(pr.get("at"))
                if pr.get("rss_mb") is not None and pr_at and now - pr_at < timedelta(minutes=15):
                    obs.rss_mb, obs.rss_source = pr["rss_mb"], "ssh"
                    if obs.core_age_s is None:
                        obs.core_age_s = pr.get("age_s")
            elif obs.rss_mb is not None:
                obs.rss_source = obs.rss_source or "agent"
            v, events = advance(st, obs, cfg, now)
            verdicts[obs.node_id] = v
            for ev in events:
                row = _row(now, obs, ev["kind"], ev["text"], cfg)
                rows.append(row)
                to_notify.append((row, tg_text(cfg, obs.name, ev["kind"], _prefix(cfg) + ev["text"])))

        # Решения.
        fleet = FleetCtx(
            restarts=restarts,
            pending_nodes=[k for k, s in nodes_state.items() if k in active and s.get("pending")],
        )
        candidates: list[tuple[Obs, dict, Decision]] = []
        by_id = {o.node_id: o for o in observations}
        for obs in observations:
            st = nodes_state[obs.node_id]
            d = decide(obs, st, verdicts.get(obs.node_id), fleet, cfg, now)
            st["decision"] = {"action": d.action, "reason": d.reason}
            if d.action == "restart":
                candidates.append((obs, st, d))
            elif d.action == "blocked" and st.get("alarm") and st.get("block_logged") != (st["alarm"] or {}).get("since"):
                # Отказ защиты озвучиваем один раз за тревогу: это тоже действие.
                st["block_logged"] = st["alarm"]["since"]
                row = _row(now, obs, "blocked", d.reason, cfg)
                rows.append(row)
                to_notify.append((row, tg_text(cfg, obs.name, "blocked", d.reason)))

        candidates.sort(key=lambda c: _priority(c[0], c[1]))
        for extra_obs, extra_st, _d in candidates[1:]:
            extra_st["decision"] = {"action": "wait", "reason": "очередь: перезапуски строго по одному"}

        chosen = candidates[0] if candidates else None
        if chosen is not None:
            obs, st, d = chosen
            v = verdicts.get(obs.node_id)
            why = "; ".join(v.reasons) if v else ""
            plan = (f"rw-core {obs.rss_mb:.0f} МБ, {why}. Причина: {d.reason}. "
                    f"Перезапуск ядра в {clock(now, cfg)} МСК.")
            if cfg.get("dry_run"):
                key = f"{(st.get('alarm') or {}).get('since')}|restart"
                if st.get("dry_key") != key:
                    st["dry_key"] = key
                    row = _row(now, obs, "restart_plan", "сделал бы перезапуск: " + plan, cfg)
                    rows.append(row)
                    to_notify.append((row, tg_text(cfg, obs.name, "restart_plan", _prefix(cfg) + "сделал бы перезапуск: " + plan)))
            else:
                # ДО: запись и состояние в базу, потом панель.
                st["pending"] = {"at": _iso(now), "uptime_before": obs.core_uptime_s,
                                 "rss_before": obs.rss_mb, "source": "auto"}
                st["last_restart_at"] = _iso(now)
                restarts.append({"ts": _iso(now), "node_id": obs.node_id, "source": "auto"})
                st["decision"] = {"action": "restarting", "reason": d.reason}
                row = _row(now, obs, "restart_start", "перезапускаю ядро: " + plan, cfg,
                           rss_before=obs.rss_mb)
                rows.append(row)
                to_notify.append((row, tg_text(cfg, obs.name, "restart_start", "перезапускаю ядро: " + plan)))
                state["nodes"] = nodes_state
                await subwatch._save_json(db, STATE_KEY, state)
                await _append_log(db, rows)
                rows = []
                await db.commit()
                err = await asyncio.to_thread(_panel_restart, obs.panel_uuid or "")
                if err:
                    st["pending"] = None
                    st["blocked"] = {"reason": "api_error", "at": _iso(now)}
                    row = _row(now, obs, "restart_error",
                               f"панель не приняла перезапуск: {err}. Повторных попыток не будет.", cfg)
                    rows.append(row)
                    to_notify.append((row, tg_text(cfg, obs.name, "restart_error", row["text"])))

        if repr(state) != before or rows:
            state["nodes"] = nodes_state
            await subwatch._save_json(db, STATE_KEY, state)
        if rows:
            await _append_log(db, rows)
        # Сначала в базу, потом — наружу: журнал не зависит от Телеграма.
        await db.commit()
        for row, text in to_notify:
            if await _notify(db, cfg, text):
                row["notified"] = True
        return [r for r, _ in to_notify]


async def manual_restart(db: AsyncSession, node, obs: Obs, now: datetime | None = None) -> dict:
    """Ручной перезапуск ядра из карточки ноды. Идёт в журнал и в паузу, но не в лимиты авто."""
    now = now or _now()
    cfg = await get_config(db)
    async with _lock:
        if not obs.panel_uuid:
            return {"ok": False, "error": "узел в панели не найден по адресу ноды"}
        if obs.panel_dup:
            return {"ok": False, "error": "адрес ноды занят несколькими узлами панели — цель неоднозначна"}
        if obs.panel_connected is not True:
            return {"ok": False, "error": "нода не connected в панели"}
        state = await get_state(db)
        st = state["nodes"].setdefault(obs.node_id, new_node_state())
        if st.get("pending"):
            return {"ok": False, "error": "предыдущий перезапуск ещё проверяется"}
        what = f"rw-core {obs.rss_mb:.0f} МБ" if obs.rss_mb is not None else "RSS ядра неизвестен"
        text = f"ручной перезапуск ядра ({what}) в {clock(now, cfg)} МСК"
        st["pending"] = {"at": _iso(now), "uptime_before": obs.core_uptime_s,
                         "rss_before": obs.rss_mb, "source": "manual"}
        st["last_restart_at"] = _iso(now)
        state["restarts"] = _prune_restarts(state["restarts"], now) + [
            {"ts": _iso(now), "node_id": obs.node_id, "source": "manual"}]
        row = _row(now, obs, "manual_restart", text, cfg, dry_run=False, rss_before=obs.rss_mb)
        await subwatch._save_json(db, STATE_KEY, state)
        await _append_log(db, [row])
        await db.commit()
        err = await asyncio.to_thread(_panel_restart, obs.panel_uuid)
        rows = []
        if err:
            st["pending"] = None
            rows.append(_row(now, obs, "restart_error", f"панель не приняла ручной перезапуск: {err}", cfg, dry_run=False))
        await subwatch._save_json(db, STATE_KEY, state)
        await _append_log(db, rows)
        await db.commit()
    await _notify(db, cfg, tg_text(cfg, obs.name, "manual_restart", text))
    if err:
        return {"ok": False, "error": err}
    return {"ok": True, "message": text}


async def reset_block(db: AsyncSession, node_id: str, now: datetime | None = None) -> bool:
    """Ручной сброс блокировки автоперезапуска (повторный разгон и т. п.)."""
    now = now or _now()
    async with _lock:
        state = await get_state(db)
        st = state["nodes"].get(node_id)
        if not st or not st.get("blocked"):
            return False
        reason = (st["blocked"] or {}).get("reason")
        st["blocked"] = None
        st["block_logged"] = None
        # Лимиты 24 ч / 7 дн. и пауза остаются: сброс снимает только флаг.
        await subwatch._save_json(db, STATE_KEY, state)
        await _append_log(db, [{"ts": _iso(now), "kind": "reset_block", "node_id": node_id,
                                "name": None, "text": f"блокировка автоперезапуска снята вручную ({reason})",
                                "dry_run": False, "notified": False}])
        await db.commit()
        return True


# ── вход от сэмплера ─────────────────────────────────────────────────────────

def build_obs(node, ag, panel_info: dict | None, oom_delta: int | None, answered: bool) -> Obs:
    """Свести ответ агента и данные панели в одно наблюдение."""
    info = panel_info or {}
    present = bool(getattr(ag, "present", False)) if ag is not None else False
    g = (lambda k: getattr(ag, k, None)) if present else (lambda k: None)
    return Obs(
        node_id=str(node.id), name=node.name, host=node.host,
        answered=bool(answered and present),
        mem_total_mb=g("mem_total_mb"), mem_available_mb=g("mem_available_mb"),
        mem_percent=g("mem_percent"), swap_percent=g("swap_percent"),
        rss_mb=g("xray_rss_mb"), rss_source="agent" if g("xray_rss_mb") is not None else None,
        core_age_s=g("xray_age_sec"), oom_delta=oom_delta,
        has_core=g("remnanode_running"),
        panel_uuid=info.get("uuid"), panel_connected=info.get("connected"),
        panel_uptime_s=info.get("xray_uptime"), panel_dup=bool(info.get("dup")),
        panel_disabled=bool(info.get("disabled")),
    )


async def observe(db: AsyncSession, nodes: list, agents: dict, answered: dict,
                  panel: dict, deltas: dict) -> list[dict]:
    """Раз в опрос, со всеми нодами. Сбой памятьсторожа не должен стоить графикам отсчёта."""
    cfg = await get_config(db)
    if not cfg.get("enabled"):
        return []
    obs_list = []
    for n in nodes:
        nid = str(n.id)
        if not getattr(n, "monitoring_enabled", True) or not n.agent_token_enc:
            continue
        ans = answered.get(nid)
        ok = bool(ans[0]) if isinstance(ans, tuple) else bool(ans)
        obs_list.append(build_obs(n, agents.get(nid), panel.get(n.host), (deltas.get(nid) or {}).get("oom_kills"), ok))
    return await tick(db, cfg, obs_list, {str(n.id): n for n in nodes},
                      existing_ids={str(n.id) for n in nodes})
