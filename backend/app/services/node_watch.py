"""Следилка за нодами: нода молчит дольше порога — сообщение в Телеграм.

Хостер может ночью приостановить ВПС на сутки, и узнаём мы об этом по нулю в
счётчике пользователей, через часы. Здесь то же самое видно через минуты:
сэмплер метрик и так опрашивает агент на каждой ноде раз в полминуты, остаётся
посчитать, сколько нода уже не отвечает.

Правила, чтобы чат не превратился в шум:
  • тревога — только непрерывное молчание дольше порога, а не пропущенный опрос;
  • одно сообщение, когда нода упала, и одно, когда поднялась; пока лежит —
    напоминание не чаще раза в N часов;
  • ноды, упавшие вместе, идут одним сообщением: у одного хостера падают пачкой;
  • разом замолчало большинство — это, скорее всего, ослеп сам рагент: одно
    сообщение про парк вместо десятка про ноды;
  • рагент до ноды не дотягивается, а панель видит её подключённой — нода
    жива, просто не для нас (рагент стоит в России, и часть зарубежных адресов
    ему режут). За такой нодой следим через панель: тревога — когда её
    потеряла и панель;
  • после перезапуска рагент помнит, о чём уже сообщил (тревоги лежат в базе),
    а первый опрос после старта отказом не считает.

Решение принимает step() — чистая функция от прошлого состояния и свежих
наблюдений; база, сеть и Телеграм живут вокруг неё.
"""
from __future__ import annotations

import asyncio
import html
import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import AppSetting, Node, NodeOutage
from app.services import subwatch
from app.services.telegram import send_message

log = logging.getLogger(__name__)

CONFIG_KEY = "node_watch_config"
STATE_KEY = "node_watch_state"

DEFAULTS = {
    "enabled": True,              # владелец просил именно оповещение
    "threshold_min": 10,          # сколько нода должна молчать подряд
    "remind_hours": 6,            # напоминать о лежащей; 0 — не напоминать
    "notify": True,
    "chat_id": "",                # пусто — тот же чат, что у следилки за подпиской
    "exclude": [],                # id нод, по которым не тревожим
    "skip_panel_disabled": True,  # выключенная в панели нода — не авария
    "panel_alive": True,          # рагент не достаёт, но панель видит — нода жива
    "utc_offset": 5,              # часовой пояс владельца для времени в сообщениях, часы от UTC
}

KEEP_DAYS = 90
# Ноды, замолчавшие с разницей в пару опросов, — одна авария: ждём отставших,
# чтобы сказать о них одним сообщением.
GROUP_WINDOW = timedelta(seconds=90)
# «Пропало большинство» считаем от тех нод, что ещё числились живыми. В парке
# из пары нод «большинство» — это одна нода, там правило не работает.
FLEET_MIN_NODES = 4
FLEET_SHARE = 0.5
# Опрос не приходил дольше этого — рагент сам стоял (или его усыпили):
# наблюдениям «до» верить нельзя, отсчёт порога начинаем заново.
STALL = timedelta(minutes=5)
# Не доставили сообщение — следующую попытку делаем не раньше, чем через это.
RETRY = timedelta(minutes=5)
OUTBOX_TRIES = 6
# Ноду без агента проверяем только по SSH. Пока порт открывается, стучимся в
# него не на каждом опросе: частые соединения хостер принимает за перебор.
KNOCK_EVERY = timedelta(minutes=2)
# С какого возраста молчание показываем в интерфейсе, не дожидаясь порога.
MARK_AFTER = timedelta(minutes=2)
MAX_LINES = 30

NODE = "node"
FLEET = "fleet"
PANEL = "panel"

# Что опрос говорит о ноде.
UP = "up"                # данные идут напрямую
VIA_PANEL = "via_panel"  # рагент не достаёт, панель видит подключённой
HELD = "held"            # следим через панель, а панель сейчас молчит — не гадаем
SILENT = "silent"        # молчит: кандидат в тревогу

RECOVERED = "recovered"
MUTED = "muted"
REMOVED = "removed"
STOPPED = "stopped"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def now() -> datetime:
    return _now()


# ── что видим и что помним ──────────────────────────────────────────────────

@dataclass
class Sighting:
    """Что рагент узнал о ноде за один опрос."""

    node_id: str
    name: str
    host: str
    hosting: str | None = None
    agent: bool | None = None       # None — агента на ноде нет
    agent_error: str | None = None  # "token" — агент жив, но не принимает наш токен
    ssh: bool | None = None         # None — не стучались: агент ответил
    panel: bool | None = None       # None — панель про ноду ничего не сказала
    muted: bool = False             # исключена в настройках или выключена в панели

    @property
    def alive(self) -> bool:
        # «Жива» — значит, от неё идут данные. Где агента нет, судить можно
        # только по тому, открывается ли SSH.
        return bool(self.agent) if self.agent is not None else bool(self.ssh)

    def signals(self) -> dict:
        out = {"agent": self.agent, "ssh": self.ssh, "panel": self.panel}
        if self.agent_error:
            out["agent_error"] = self.agent_error
        return out


@dataclass
class Outage:
    """Поднятая тревога — открытая строка node_outages."""

    node_id: str
    name: str
    host: str
    hosting: str | None
    since: datetime                       # с какого опроса молчит
    alerted_at: datetime                  # когда молчание перевалило за порог
    signals: dict = field(default_factory=dict)
    notified_at: datetime | None = None   # сообщение о падении ушло
    reminded_at: datetime | None = None
    id: uuid.UUID = field(default_factory=uuid.uuid4)


@dataclass
class Fleet:
    """Эпизод «пропало большинство»: рагент разом перестал видеть парк."""

    since: datetime
    down: int = 0
    total: int = 0
    by_hosting: dict[str, list[int]] = field(default_factory=dict)   # хостер: [молчит, всего]
    alerted_at: datetime | None = None
    notified_at: datetime | None = None
    reminded_at: datetime | None = None


@dataclass
class Reach:
    """Нода, до которой рагент не дотягивается: её сигнал жизни — панель."""

    since: datetime
    panel: bool | None = True   # что панель сказала в последний раз; None — промолчала


@dataclass
class PanelLoss:
    """Панель Remnawave не отвечает рагенту."""

    since: datetime
    alerted_at: datetime | None = None
    notified_at: datetime | None = None


@dataclass
class Watch:
    """Что переживает перезапуск, кроме самих тревог (они в node_outages)."""

    pending: dict[str, datetime] = field(default_factory=dict)   # молчит, порог ещё не вышел
    fleet: Fleet | None = None
    unreached: dict[str, Reach] = field(default_factory=dict)    # наблюдаются через панель
    panel: PanelLoss | None = None

    def dump(self) -> dict:
        out: dict = {"pending": {k: v.isoformat() for k, v in sorted(self.pending.items())}}
        if self.unreached:
            out["unreached"] = {k: {"since": v.since.isoformat(), "panel": v.panel}
                                for k, v in sorted(self.unreached.items())}
        if self.panel is not None:
            out["panel"] = {"since": self.panel.since.isoformat(),
                            "alerted_at": _iso(self.panel.alerted_at),
                            "notified_at": _iso(self.panel.notified_at)}
        if self.fleet is not None:
            f = self.fleet
            out["fleet"] = {
                "since": f.since.isoformat(), "down": f.down, "total": f.total,
                "by_hosting": f.by_hosting,
                "alerted_at": _iso(f.alerted_at), "notified_at": _iso(f.notified_at),
                "reminded_at": _iso(f.reminded_at),
            }
        return out

    @classmethod
    def load(cls, data: dict | None) -> "Watch":
        data = data if isinstance(data, dict) else {}
        pending: dict[str, datetime] = {}
        for key, value in (data.get("pending") or {}).items():
            when = _parse(value)
            if when is not None:
                pending[str(key)] = when
        fleet = None
        raw = data.get("fleet")
        if isinstance(raw, dict) and _parse(raw.get("since")) is not None:
            fleet = Fleet(
                since=_parse(raw.get("since")), down=int(raw.get("down") or 0),
                total=int(raw.get("total") or 0), by_hosting=dict(raw.get("by_hosting") or {}),
                alerted_at=_parse(raw.get("alerted_at")), notified_at=_parse(raw.get("notified_at")),
                reminded_at=_parse(raw.get("reminded_at")),
            )
        unreached: dict[str, Reach] = {}
        for key, value in (data.get("unreached") or {}).items():
            since = _parse(value.get("since")) if isinstance(value, dict) else None
            if since is not None:
                seen = value.get("panel")
                unreached[str(key)] = Reach(since=since,
                                            panel=seen if isinstance(seen, bool) else None)
        panel = None
        raw = data.get("panel")
        if isinstance(raw, dict) and _parse(raw.get("since")) is not None:
            panel = PanelLoss(since=_parse(raw.get("since")),
                              alerted_at=_parse(raw.get("alerted_at")),
                              notified_at=_parse(raw.get("notified_at")))
        return cls(pending=pending, fleet=fleet, unreached=unreached, panel=panel)


@dataclass
class Runtime:
    """Память процесса. Перезапуск её стирает — и это правильно: рагент, который
    только что поднялся, ещё ничего не наблюдал."""

    # С какого момента наблюдениям можно верить. None — первого опроса не было.
    trusted_from: datetime | None = None
    last_tick: datetime | None = None
    retry_after: datetime | None = None
    # Недоставленные сообщения о восстановлении: [текст, сколько раз пробовали].
    outbox: list[list] = field(default_factory=list)
    last_prune: datetime | None = None
    idle: bool = False   # следилка выключена, хвосты уже убраны
    # Адреса нод, выключенных в панели, — как панель ответила в последний раз.
    panel_disabled: set[str] = field(default_factory=set)
    # Когда SSH ноды без агента в последний раз открылся, по id ноды.
    knocked_ok: dict[str, datetime] = field(default_factory=dict)


_runtime = Runtime()


@dataclass
class Message:
    # down | up | remind | fleet_down | fleet_up | fleet_remind | panel_down | panel_up
    kind: str
    text: str
    outages: list[Outage] = field(default_factory=list)


@dataclass
class Plan:
    """Что step() решил сделать: записать в журнал и сказать в чат."""

    opened: list[Outage] = field(default_factory=list)
    closed: list[tuple[Outage, datetime, str]] = field(default_factory=list)
    fleet_opened: Fleet | None = None
    fleet_closed: datetime | None = None
    panel_opened: PanelLoss | None = None
    panel_closed: datetime | None = None
    messages: list[Message] = field(default_factory=list)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _parse(value) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    try:
        when = datetime.fromisoformat(value)
    except ValueError:
        return None
    return when if when.tzinfo is not None else when.replace(tzinfo=timezone.utc)


# ── тексты ──────────────────────────────────────────────────────────────────

class Clock:
    """Время в сообщениях — местное для владельца, а не UTC сервера."""

    def __init__(self, offset_hours, now: datetime) -> None:
        try:
            hours = max(-12, min(14, int(offset_hours)))
        except (TypeError, ValueError):
            hours = DEFAULTS["utc_offset"]
        self.tz = timezone(timedelta(hours=hours))
        self.label = "МСК" if hours == 3 else f"UTC{hours:+d}"
        self.today = now.astimezone(self.tz).date()

    def at(self, when: datetime) -> str:
        local = when.astimezone(self.tz)
        # Дату пишем, только если это было не сегодня: «с 03:12» читается быстрее.
        return local.strftime("%H:%M") if local.date() == self.today else local.strftime("%d.%m %H:%M")


def span(delta: timedelta) -> str:
    minutes = max(1, int(delta.total_seconds() // 60))
    if minutes < 60:
        return f"{minutes} мин"
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return f"{hours} ч {minutes} мин" if minutes else f"{hours} ч"
    days, hours = divmod(hours, 24)
    return f"{days} сут {hours} ч" if hours else f"{days} сут"


def _plural(n: int, one: str, few: str, many: str) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def _nodes(n: int) -> str:
    return f"{n} {_plural(n, 'нода', 'ноды', 'нод')}"


def _who(o: Outage) -> str:
    parts = [f"<b>{html.escape(o.name)}</b>", html.escape(o.host)]
    if o.hosting:
        parts.append(html.escape(o.hosting))
    return " · ".join(parts)


def signals_text(sig: dict | None) -> str:
    """Что именно не отвечает: агент, SSH, панель — какие сигналы есть."""
    sig = sig or {}
    parts: list[str] = []
    if sig.get("agent") is None:
        parts.append("агента нет")
    elif sig.get("agent_error") == "token":
        parts.append("агент не принимает токен")
    elif not sig.get("agent"):
        parts.append("агент молчит")
    if sig.get("ssh") is not None:
        parts.append("SSH отвечает" if sig["ssh"] else "SSH молчит")
    if sig.get("panel") is not None:
        parts.append("панель видит" if sig["panel"] else "панель не видит")
    return " · ".join(parts)


def _cap(text: str) -> str:
    return text[:1].upper() + text[1:]


def _server_alive(o: Outage) -> bool:
    return bool((o.signals or {}).get("ssh"))


def _bullets(lines: list[str]) -> list[str]:
    if len(lines) <= MAX_LINES:
        return lines
    return [*lines[:MAX_LINES], f"… и ещё {len(lines) - MAX_LINES}"]


def down_text(items: list[Outage], now: datetime, clock: Clock) -> str:
    items = sorted(items, key=lambda o: (o.since, o.name))
    # SSH открывается — сервер цел, молчит только агент: это другая поломка,
    # и бежать к хостеру из-за неё не надо.
    agent_only = all(_server_alive(o) for o in items)
    if len(items) == 1:
        o = items[0]
        head = ("🟠 <b>Нода жива, но агент не отдаёт данные</b>" if agent_only
                else "🔴 <b>Нода недоступна</b>")
        return "\n".join([
            head,
            _who(o),
            f"Не отвечает с {clock.at(o.since)} {clock.label} — уже {span(now - o.since)}.",
            _cap(signals_text(o.signals)),
        ])
    head = (f"🟠 <b>Агент не отдаёт данные: {_nodes(len(items))}</b>" if agent_only
            else f"🔴 <b>Недоступны: {_nodes(len(items))}</b>")
    lines = [f"• {_who(o)} — с {clock.at(o.since)}: {signals_text(o.signals)}" for o in items]
    return "\n".join([head, *_bullets(lines), f"Время — {clock.label}."])


def up_text(items: list[Outage], now: datetime, clock: Clock,
            via_panel: frozenset[str] = frozenset()) -> str:
    """via_panel — id нод, которые вернулись в панель, а рагенту так и не отвечают."""
    items = sorted(items, key=lambda o: (o.since, o.name))
    if len(items) == 1:
        o = items[0]
        seen = o.node_id in via_panel
        lines = [
            "🟢 <b>Нода снова в панели</b>" if seen else "🟢 <b>Нода снова отвечает</b>",
            _who(o),
            f"Лежала {span(now - o.since)}: с {clock.at(o.since)} до {clock.at(now)} {clock.label}.",
        ]
        if seen:
            lines.append("Рагент до неё по-прежнему не дотягивается — слежу через панель.")
        return "\n".join(lines)
    lines = [f"• {_who(o)} — лежала {span(now - o.since)}, с {clock.at(o.since)}"
             + (" (вернулась в панель)" if o.node_id in via_panel else "") for o in items]
    return "\n".join([f"🟢 <b>Снова отвечают: {_nodes(len(items))}</b>", *_bullets(lines),
                      f"Время — {clock.label}."])


def remind_text(items: list[Outage], now: datetime, clock: Clock) -> str:
    items = sorted(items, key=lambda o: (o.since, o.name))
    lines = [f"• {_who(o)} — с {clock.at(o.since)}, уже {span(now - o.since)}: "
             f"{signals_text(o.signals)}" for o in items]
    return "\n".join([f"⏰ <b>Всё ещё не отвечают: {_nodes(len(items))}</b>", *_bullets(lines),
                      f"Время — {clock.label}."])


def _hostings_line(fleet: Fleet) -> str:
    rows = sorted(((name, pair) for name, pair in fleet.by_hosting.items() if pair[0]),
                  key=lambda item: (-item[1][0], item[0]))
    if not rows:
        return ""
    shown = ", ".join(f"{html.escape(name)} — {pair[0]} из {pair[1]}" for name, pair in rows[:8])
    line = f"По хостерам: {shown}" + (f" и ещё {len(rows) - 8}" if len(rows) > 8 else "") + "."
    # Молчит ровно один хостер целиком, остальные отвечают — тогда дело не в рагенте.
    if len(rows) == 1 and rows[0][1][0] == rows[0][1][1] and len(fleet.by_hosting) > 1:
        line += f"\nМолчат только ноды {html.escape(rows[0][0])} — похоже, дело в хостере."
    return line


def fleet_down_text(fleet: Fleet, now: datetime, clock: Clock) -> str:
    lines = [
        f"⚠️ <b>Рагент не видит {fleet.down} из {fleet.total} нод</b>",
        f"С {clock.at(fleet.since)} {clock.label}, уже {span(now - fleet.since)}. Столько разом "
        "не падает — возможно, проблема на стороне рагента или его сети. По нодам отдельно "
        "не тревожу.",
        _hostings_line(fleet),
    ]
    return "\n".join(x for x in lines if x)


def fleet_remind_text(fleet: Fleet, now: datetime, clock: Clock) -> str:
    lines = [
        f"⏰ <b>Рагент всё ещё не видит {fleet.down} из {fleet.total} нод</b>",
        f"С {clock.at(fleet.since)} {clock.label}, уже {span(now - fleet.since)}.",
        _hostings_line(fleet),
    ]
    return "\n".join(x for x in lines if x)


def fleet_up_text(fleet: Fleet, silent: list[Sighting], total: int, now: datetime,
                  threshold: timedelta, clock: Clock) -> str:
    lines = [
        "🟢 <b>Рагент снова видит ноды</b>",
        f"Отвечают {total - len(silent)} из {total}. Не видел парк с {clock.at(fleet.since)} "
        f"до {clock.at(now)} {clock.label} — {span(now - fleet.since)}.",
    ]
    if silent:
        names = ", ".join(html.escape(s.name) for s in sorted(silent, key=lambda s: s.name)[:10])
        more = f" и ещё {len(silent) - 10}" if len(silent) > 10 else ""
        one = len(silent) == 1
        lines.append(f"Ещё {'молчит' if one else 'молчат'}: {names}{more}. Если не "
                     f"{'ответит' if one else 'ответят'} за {span(threshold)} — сообщу отдельно.")
    return "\n".join(lines)


def panel_down_text(loss: PanelLoss, watched: list[str], now: datetime, clock: Clock) -> str:
    lines = [
        "⚠️ <b>Рагент не видит панель Remnawave</b>",
        f"С {clock.at(loss.since)} {clock.label}, уже {span(now - loss.since)}.",
    ]
    if watched:
        names = ", ".join(html.escape(n) for n in sorted(watched)[:10])
        more = f" и ещё {len(watched) - 10}" if len(watched) > 10 else ""
        lines.append(f"Только через неё наблюдаются: {names}{more} — пока панель молчит, "
                     "об их падении не узнаю.")
    return "\n".join(lines)


def panel_up_text(loss: PanelLoss, now: datetime, clock: Clock) -> str:
    return "\n".join([
        "🟢 <b>Панель Remnawave снова отвечает</b>",
        f"Рагент не видел её с {clock.at(loss.since)} до {clock.at(now)} {clock.label} — "
        f"{span(now - loss.since)}.",
    ])


# ── решение ─────────────────────────────────────────────────────────────────

def _by_hosting(pool: list[Sighting], silent: list[Sighting]) -> dict[str, list[int]]:
    gone = {s.node_id for s in silent}
    out: dict[str, list[int]] = {}
    for s in pool:
        pair = out.setdefault(s.hosting or "без хостера", [0, 0])
        pair[1] += 1
        if s.node_id in gone:
            pair[0] += 1
    return out


def verdict(s: Sighting, watch: Watch, use_panel: bool) -> str:
    """Что опрос говорит о ноде: жива, жива по словам панели, неизвестно или молчит."""
    if s.alive:
        return UP
    # SSH открылся — рагент до сервера достаёт, значит молчит сам агент, и панель
    # тут ни при чём.
    if use_panel and s.ssh is not True:
        if s.panel is True:
            return VIA_PANEL
        # Следили через панель, а она сейчас про ноду ничего не говорит. Не
        # гадаем: тревогу поднимем, только когда панель ответит «не вижу».
        if s.panel is None and s.node_id in watch.unreached:
            return HELD
    return SILENT


def step(watch: Watch, rt: Runtime, outages: dict[str, Outage], sightings: list[Sighting],
         cfg: dict, now: datetime, panel_ok: bool | None = True) -> Plan:
    """Один опрос: что изменилось и о чём пора сказать.

    watch и rt меняются на месте. outages — открытые тревоги по id ноды: что
    с ними делать в журнале, возвращается в плане. panel_ok — ответила ли
    панель на этот опрос; None — панель у рагента не настроена вовсе.
    """
    plan = Plan()
    threshold = timedelta(minutes=max(1, int(cfg.get("threshold_min") or 10)))
    hours = float(cfg.get("remind_hours") or 0)
    remind = timedelta(hours=hours) if hours > 0 else None
    clock = Clock(cfg.get("utc_offset"), now)

    # Первый опрос после старта отказом не считаем: рагент только поднялся, и
    # его «не ответила» пока ничего не значит. То же — после долгой паузы.
    first = rt.trusted_from is None
    if first or (rt.last_tick is not None and now - rt.last_tick > STALL):
        rt.trusted_from = now
    rt.last_tick = now
    may_send = rt.retry_after is None or now >= rt.retry_after

    seen = {s.node_id: s for s in sightings}
    watched = {nid: s for nid, s in seen.items() if not s.muted}

    # Рагент не дотягивается, а панель видит ноду подключённой — нода жива, и
    # дальше её сигнал жизни — панель. Без панели (или с выключенной настройкой)
    # такого состояния нет: молчит — значит молчит.
    use_panel = bool(cfg.get("panel_alive", True)) and panel_ok is not None
    if not use_panel:
        watch.unreached.clear()
    state = {nid: verdict(s, watch, use_panel) for nid, s in watched.items()}
    for nid in list(watch.unreached):
        s = watched.get(nid)
        # Ответила напрямую (или открылся SSH) — рагент до неё снова достаёт.
        if s is None or s.alive or s.ssh is True:
            del watch.unreached[nid]
        else:
            watch.unreached[nid].panel = s.panel
    for nid, kind in state.items():
        if kind == VIA_PANEL and nid not in watch.unreached:
            watch.unreached[nid] = Reach(since=now)

    # Открытые тревоги: снять те, за чьими нодами больше не следим (молча), и
    # те, чьи ноды ответили или вернулись в панель (с сообщением).
    still: dict[str, Outage] = {}
    back: list[Outage] = []
    for nid, o in outages.items():
        s = seen.get(nid)
        if s is None:
            plan.closed.append((o, now, REMOVED))
        elif s.muted:
            plan.closed.append((o, now, MUTED))
        elif state[nid] in (UP, VIA_PANEL):
            plan.closed.append((o, now, RECOVERED))
            back.append(o)
        else:
            o.signals = s.signals()   # для напоминания: что молчит сейчас, а не тогда
            still[nid] = o
    if back:
        via = frozenset(o.node_id for o in back if state[o.node_id] == VIA_PANEL)
        plan.messages.append(Message("up", up_text(back, now, clock, via), back))

    # Ответила раньше порога — моргнула сеть, это не тревога.
    for nid in list(watch.pending):
        if state.get(nid) in (None, UP, VIA_PANEL):
            del watch.pending[nid]

    pool = [s for nid, s in watched.items() if nid not in still]
    silent = [s for s in pool if state[s.node_id] == SILENT]
    if not first:
        for s in silent:
            watch.pending.setdefault(s.node_id, now)

    # Разом замолчало больше половины тех, кто числился живым, — ослеп рагент.
    majority = len(pool) >= FLEET_MIN_NODES and len(silent) > len(pool) * FLEET_SHARE
    if majority and (watch.fleet is not None or not first):
        fleet = watch.fleet or Fleet(since=now)
        watch.fleet = fleet
        fleet.down, fleet.total = len(silent), len(pool)
        fleet.by_hosting = _by_hosting(pool, silent)
        if fleet.alerted_at is None and now - max(fleet.since, rt.trusted_from) >= threshold:
            fleet.alerted_at = now
            plan.fleet_opened = fleet
        if fleet.alerted_at is not None and may_send:
            if fleet.notified_at is None:
                plan.messages.append(Message("fleet_down", fleet_down_text(fleet, now, clock)))
            elif remind and now - (fleet.reminded_at or fleet.notified_at) >= remind:
                plan.messages.append(Message("fleet_remind", fleet_remind_text(fleet, now, clock)))
        # Пока рагент слеп, по нодам отдельно не тревожим и не напоминаем.
        return plan

    if watch.fleet is not None:
        fleet, watch.fleet = watch.fleet, None
        # Всё, что рагент «видел», пока был слеп, не в счёт: молчащим нодам
        # порог отсчитываем заново, хотя время начала за ними остаётся.
        rt.trusted_from = now
        if fleet.alerted_at is not None:
            plan.fleet_closed = now
            plan.messages.append(Message(
                "fleet_up", fleet_up_text(fleet, silent, len(pool), now, threshold, clock)))

    # Панель не отвечает дольше порога — одно сообщение про саму панель. Пока
    # рагент слеп целиком (ветка выше), сюда не доходим: это та же беда.
    if panel_ok is False:
        if watch.panel is None and not first:
            watch.panel = PanelLoss(since=now)
        loss = watch.panel
        if loss is not None:
            if loss.alerted_at is None and now - max(loss.since, rt.trusted_from) >= threshold:
                loss.alerted_at = now
                plan.panel_opened = loss
            if loss.alerted_at is not None and loss.notified_at is None and may_send:
                names = [watched[nid].name for nid in watch.unreached if nid in watched]
                plan.messages.append(Message(
                    "panel_down", panel_down_text(loss, names, now, clock)))
    elif watch.panel is not None:
        loss, watch.panel = watch.panel, None
        if loss.alerted_at is not None:
            plan.panel_closed = now
            plan.messages.append(Message("panel_up", panel_up_text(loss, now, clock)))

    def age(nid: str) -> timedelta:
        return now - max(watch.pending[nid], rt.trusted_from)

    # HELD сюда не попадает: нода, за которой следим через панель, дозревает до
    # тревоги, только пока панель прямо говорит «не вижу».
    due = [nid for nid in watch.pending if age(nid) >= threshold and state[nid] == SILENT]
    if due:
        # Кто-то вот-вот дозреет — подождём его, но не дольше окна.
        window = min(GROUP_WINDOW, threshold / 2)
        soon = any(threshold - window <= age(nid) < threshold for nid in watch.pending)
        waited = max(age(nid) for nid in due) - threshold
        if not (soon and waited < window):
            for nid in due:
                s = watched[nid]
                o = Outage(node_id=nid, name=s.name, host=s.host, hosting=s.hosting,
                           since=watch.pending.pop(nid), alerted_at=now, signals=s.signals())
                plan.opened.append(o)
                still[nid] = o

    if may_send:
        # Сюда же попадают тревоги, про которые сказать не вышло: Телеграм был
        # недоступен или уведомления тогда были выключены.
        fresh = [o for o in still.values() if o.notified_at is None]
        if fresh:
            plan.messages.append(Message("down", down_text(fresh, now, clock), fresh))
        told = [o for o in still.values() if o.notified_at is not None]
        if remind and any(now - (o.reminded_at or o.notified_at) >= remind for o in told):
            # Напоминаем про всех разом: так напоминания сходятся в одно сообщение.
            plan.messages.append(Message("remind", remind_text(told, now, clock), told))
    return plan


# ── настройки и состояние ───────────────────────────────────────────────────

async def _json_setting(db: AsyncSession, key: str, fallback: dict) -> dict:
    row = await db.get(AppSetting, key)
    if not row or not row.value_enc:
        return dict(fallback)
    try:
        data = json.loads(row.value_enc)
    except ValueError:
        return dict(fallback)
    return {**fallback, **data} if isinstance(data, dict) else dict(fallback)


async def _save_json(db: AsyncSession, key: str, data: dict) -> None:
    row = await db.get(AppSetting, key)
    if row is None:
        row = AppSetting(key=key, value_enc=None)
        db.add(row)
        await db.flush()
    row.value_enc = json.dumps(data, ensure_ascii=False)


async def get_config(db: AsyncSession) -> dict:
    return await _json_setting(db, CONFIG_KEY, DEFAULTS)


async def save_config(db: AsyncSession, cfg: dict) -> None:
    await _save_json(db, CONFIG_KEY, cfg)


async def effective_chat(db: AsyncSession, cfg: dict) -> str:
    """Свой чат, а если не задан — общий, из следилки за подпиской."""
    return (cfg.get("chat_id") or "").strip() or \
        ((await subwatch.get_config(db)).get("chat_id") or "").strip()


# ── база ────────────────────────────────────────────────────────────────────

def _outage(row: NodeOutage) -> Outage:
    return Outage(
        id=row.id, node_id=str(row.node_id), name=row.node_name, host=row.host or "",
        hosting=row.hosting, since=row.started_at, alerted_at=row.alerted_at,
        signals=dict(row.signals or {}), notified_at=row.notified_at, reminded_at=row.reminded_at,
    )


async def _db_load(db: AsyncSession) -> tuple[Watch, dict[str, Outage]]:
    watch = Watch.load(await _json_setting(db, STATE_KEY, {}))
    rows = await db.execute(
        select(NodeOutage).where(NodeOutage.kind == NODE, NodeOutage.ended_at.is_(None))
        .order_by(NodeOutage.started_at.asc()))
    outages: dict[str, Outage] = {}
    for row in rows.scalars():
        # node_id пуст — ноду удалили, база сама отвязала строку. Дубль по ноде
        # взяться не должен, но открытой тогда оставляем одну.
        if row.node_id is None or str(row.node_id) in outages:
            row.ended_at, row.resolution = _now(), REMOVED
            continue
        outages[str(row.node_id)] = _outage(row)
    return watch, outages


async def _db_apply(db: AsyncSession, plan: Plan, watch: Watch, before: dict) -> None:
    """Записать решение: журнал и состояние — одной транзакцией."""
    for o in plan.opened:
        db.add(NodeOutage(
            id=o.id, kind=NODE, node_id=uuid.UUID(o.node_id), node_name=o.name, host=o.host,
            hosting=o.hosting, started_at=o.since, alerted_at=o.alerted_at, signals=o.signals))
    for o, ended, resolution in plan.closed:
        await db.execute(update(NodeOutage).where(NodeOutage.id == o.id)
                         .values(ended_at=ended, resolution=resolution))
    if plan.fleet_opened is not None:
        f = plan.fleet_opened
        db.add(NodeOutage(
            kind=FLEET, node_id=None, node_name=f"{f.down} из {f.total} нод", host=None,
            hosting=None, started_at=f.since, alerted_at=f.alerted_at or _now(),
            signals={"down": f.down, "total": f.total, "by_hosting": f.by_hosting}))
    if plan.fleet_closed is not None:
        await db.execute(update(NodeOutage)
                         .where(NodeOutage.kind == FLEET, NodeOutage.ended_at.is_(None))
                         .values(ended_at=plan.fleet_closed, resolution=RECOVERED))
    if plan.panel_opened is not None:
        p = plan.panel_opened
        db.add(NodeOutage(
            kind=PANEL, node_id=None, node_name="панель Remnawave", host=None, hosting=None,
            started_at=p.since, alerted_at=p.alerted_at or _now(), signals={"panel": False}))
    if plan.panel_closed is not None:
        await db.execute(update(NodeOutage)
                         .where(NodeOutage.kind == PANEL, NodeOutage.ended_at.is_(None))
                         .values(ended_at=plan.panel_closed, resolution=RECOVERED))
    # Состояние пишем, только когда оно изменилось: опрос идёт раз в полминуты.
    if watch.dump() != before:
        await _save_json(db, STATE_KEY, watch.dump())
    await db.commit()


async def _db_delivered(db: AsyncSession, when: datetime, notified: list[uuid.UUID],
                        reminded: list[uuid.UUID], watch: Watch | None) -> None:
    """Отметить, о чём удалось сказать. watch — если отметка легла в состояние
    (эпизод «парк» или «панель»), а не в строку журнала."""
    if notified:
        await db.execute(update(NodeOutage).where(NodeOutage.id.in_(notified))
                         .values(notified_at=when))
    if reminded:
        await db.execute(update(NodeOutage).where(NodeOutage.id.in_(reminded))
                         .values(reminded_at=when))
    if watch is not None:
        await _save_json(db, STATE_KEY, watch.dump())
    await db.commit()


async def _send(db: AsyncSession, cfg: dict, text: str) -> str | None:
    """None — доставлено; иначе текст ошибки. Бот у нас один — из следилки за подпиской."""
    token = await subwatch.bot_token(db)
    chat = await effective_chat(db, cfg)
    if not token or not chat:
        return "бот или чат не настроены"
    return await asyncio.to_thread(send_message, token, chat, text)


async def _can_notify(db: AsyncSession, cfg: dict) -> bool:
    if not cfg.get("notify"):
        return False
    return bool(await subwatch.bot_token(db)) and bool(await effective_chat(db, cfg))


# ── один опрос ──────────────────────────────────────────────────────────────

async def tick(db: AsyncSession, cfg: dict, sightings: list[Sighting],
               now: datetime | None = None, panel_ok: bool | None = True) -> Plan:
    """Принять наблюдения опроса, обновить журнал и сказать в чат, что положено."""
    now = now or _now()
    rt = _runtime
    watch, outages = await _db_load(db)
    before = watch.dump()
    plan = step(watch, rt, outages, sightings, cfg, now, panel_ok)
    await _db_apply(db, plan, watch, before)

    if not await _can_notify(db, cfg):
        # Тревоги остаются неотправленными: включат уведомления — скажем о тех,
        # что ещё лежат. Поднявшиеся молча — уже история, слать её потом незачем.
        rt.outbox.clear()
        return plan

    # Сначала то, что не ушло в прошлый раз.
    if rt.outbox and (rt.retry_after is None or now >= rt.retry_after):
        item = rt.outbox[0]
        if await _send(db, cfg, item[0]) is None:
            rt.outbox.pop(0)
        else:
            item[1] += 1
            if item[1] >= OUTBOX_TRIES:
                rt.outbox.pop(0)
            rt.retry_after = now + RETRY

    notified: list[uuid.UUID] = []
    reminded: list[uuid.UUID] = []
    fleet_touched = False   # отметка легла в состояние, а не в строку журнала
    for msg in plan.messages:
        err = await _send(db, cfg, msg.text)
        if err:
            log.warning("node-watch: сообщение не ушло (%s): %s", msg.kind, err)
            rt.retry_after = now + RETRY
            # Про падение напомнит само состояние: тревога осталась неотправленной.
            # Восстановление запомнить негде, кроме как здесь.
            if msg.kind in ("up", "fleet_up"):
                rt.outbox.append([msg.text, 1])
            continue
        if msg.kind == "down":
            notified += [o.id for o in msg.outages]
        elif msg.kind == "remind":
            reminded += [o.id for o in msg.outages]
        elif msg.kind == "fleet_down" and watch.fleet is not None:
            watch.fleet.notified_at = now
            fleet_touched = True
        elif msg.kind == "fleet_remind" and watch.fleet is not None:
            watch.fleet.reminded_at = now
            fleet_touched = True
        elif msg.kind == "panel_down" and watch.panel is not None:
            watch.panel.notified_at = now
            fleet_touched = True
    if notified or reminded or fleet_touched:
        await _db_delivered(db, now, notified, reminded, watch if fleet_touched else None)
    return plan


async def stand_down(db: AsyncSession) -> None:
    """Следилку выключили: открытых тревог и подозрений не оставляем.

    Иначе после включения через неделю рагент «вспомнил» бы простой недельной
    давности и отчитался о восстановлении.
    """
    global _runtime
    _runtime = Runtime(idle=True)
    await db.execute(update(NodeOutage).where(NodeOutage.ended_at.is_(None))
                     .values(ended_at=_now(), resolution=STOPPED))
    row = await db.get(AppSetting, STATE_KEY)
    if row is not None and row.value_enc:
        row.value_enc = None
    await db.commit()


async def prune(db: AsyncSession) -> int:
    cutoff = _now() - timedelta(days=KEEP_DAYS)
    res = await db.execute(delete(NodeOutage).where(NodeOutage.ended_at.is_not(None),
                                                    NodeOutage.ended_at < cutoff))
    await db.commit()
    return res.rowcount or 0


async def _knock(host: str, port: int) -> bool:
    """Открывается ли SSH-порт ноды."""
    # Импорт здесь: пинговалка тянет icmplib, а решениям следилки он не нужен.
    from app.services.ping import check_tcp

    return await check_tcp(host, port)


async def observe(db: AsyncSession, nodes: list[Node],
                  agents: dict[str, tuple[bool | None, str | None]],
                  panel: dict[str, dict], panel_ok: bool | None = True) -> None:
    """Вход от сэмплера метрик: раз в опрос, со всеми нодами сразу.

    agents — по id ноды: (ответил ли агент сейчас, почему нет); None вместо
    ответа — агента на ноде нет. panel — что о нодах знает Remnawave, по адресу.
    """
    cfg = await get_config(db)
    if not cfg.get("enabled"):
        if not _runtime.idle:
            await stand_down(db)
        return
    _runtime.idle = False

    # Панель в этот опрос могла не ответить. Кто в ней выключен, помним с
    # прошлого раза: иначе нода на обслуживании подняла бы тревогу только
    # оттого, что панель моргнула.
    if panel:
        _runtime.panel_disabled = {addr for addr, info in panel.items() if info.get("disabled")}
    parked = _runtime.panel_disabled if cfg.get("skip_panel_disabled") else set()

    excluded = {str(x) for x in cfg.get("exclude") or []}
    sightings: list[Sighting] = []
    for node in nodes:
        nid = str(node.id)
        info = panel.get(node.host) or {}
        answered, why = agents.get(nid, (None, None))
        sightings.append(Sighting(
            node_id=nid, name=node.name, host=node.host,
            hosting=node.hosting.name if node.hosting is not None else None,
            agent=answered, agent_error=why, panel=info.get("connected"),
            muted=not getattr(node, "monitoring_enabled", True) or nid in excluded or node.host in parked,
        ))

    # В SSH стучимся только там, где агент не ответил (или его нет): по этому
    # видно, лежит сервер целиком или молчит один агент.
    now = _now()
    rt = _runtime
    ports = {str(n.id): n.ssh_port for n in nodes}
    ask: list[Sighting] = []
    for s in sightings:
        if s.muted or s.agent:
            continue
        last_ok = rt.knocked_ok.get(s.node_id)
        if s.agent is None and last_ok is not None and now - last_ok < KNOCK_EVERY:
            s.ssh = True    # открывался только что — считаем, что открыт и сейчас
        else:
            ask.append(s)
    results = await asyncio.gather(*(_knock(s.host, ports[s.node_id]) for s in ask),
                                   return_exceptions=True)
    for s, ok in zip(ask, results):
        s.ssh = ok if isinstance(ok, bool) else False
        if s.ssh and s.agent is None:
            rt.knocked_ok[s.node_id] = now
        else:
            rt.knocked_ok.pop(s.node_id, None)

    await tick(db, cfg, sightings, now, panel_ok)

    if rt.last_prune is None or now - rt.last_prune > timedelta(days=1):
        rt.last_prune = now
        await prune(db)


# ── для интерфейса ──────────────────────────────────────────────────────────

async def _nodes_by_id(db: AsyncSession, keys) -> dict[str, Node]:
    ids = []
    for nid in keys:
        try:
            ids.append(uuid.UUID(nid))
        except ValueError:
            continue
    if not ids:
        return {}
    found = await db.execute(select(Node).options(selectinload(Node.hosting))
                             .where(Node.id.in_(ids)))
    return {str(n.id): n for n in found.scalars()}


async def current(db: AsyncSession) -> dict:
    """Что следилка знает прямо сейчас.

    down — поднятые тревоги и те, у кого порог ещё не вышел; via_panel — ноды,
    до которых рагент не достаёт и которые наблюдаются через панель; fleet и
    panel_lost — рагент не видит парк или панель.
    """
    watch = Watch.load(await _json_setting(db, STATE_KEY, {}))
    rows = await db.execute(
        select(NodeOutage).where(NodeOutage.kind == NODE, NodeOutage.ended_at.is_(None),
                                 NodeOutage.node_id.is_not(None))
        .order_by(NodeOutage.started_at.asc()))
    down = [{"node_id": str(r.node_id), "name": r.node_name, "host": r.host, "hosting": r.hosting,
             "since": r.started_at, "alerted": True, "signals": r.signals or {}}
            for r in rows.scalars()]

    # Молчит меньше пары минут — ещё не повод рисовать пометку: моргнула сеть.
    floor = _now() - MARK_AFTER
    waiting = {nid: since for nid, since in watch.pending.items() if since <= floor}
    # Через панель наблюдаем тех, про кого она не сказала «не вижу»: сказала —
    # это уже молчащая нода, она среди down.
    busy = {d["node_id"] for d in down} | set(watch.pending)
    reach = {nid: r for nid, r in watch.unreached.items()
             if nid not in busy and r.panel is not False}
    nodes = await _nodes_by_id(db, [*waiting, *reach])

    def card(node: Node) -> dict:
        return {"node_id": str(node.id), "name": node.name, "host": node.host,
                "hosting": node.hosting.name if node.hosting is not None else None}

    for nid, since in waiting.items():
        if nid in nodes:
            down.append({**card(nodes[nid]), "since": since, "alerted": False, "signals": {}})
    via_panel = [{**card(nodes[nid]), "since": r.since, "panel_seen": r.panel is True}
                 for nid, r in reach.items() if nid in nodes]
    via_panel.sort(key=lambda item: item["name"])

    fleet = None
    if watch.fleet is not None:
        f = watch.fleet
        fleet = {"since": f.since, "alerted": f.alerted_at is not None,
                 "down": f.down, "total": f.total}
    panel_lost = None
    if watch.panel is not None:
        panel_lost = {"since": watch.panel.since, "alerted": watch.panel.alerted_at is not None}
    return {"down": down, "via_panel": via_panel, "fleet": fleet, "panel_lost": panel_lost}


async def events(db: AsyncSession, days: int = 30, limit: int = 200) -> list[NodeOutage]:
    since = _now() - timedelta(days=days)
    rows = await db.execute(
        select(NodeOutage).where(NodeOutage.started_at >= since)
        .order_by(NodeOutage.started_at.desc()).limit(limit))
    return list(rows.scalars())
