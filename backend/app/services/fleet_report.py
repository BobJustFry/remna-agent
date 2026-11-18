"""Fleet load report for Telegram: verdicts from 14-day PEAKS, formatting, schedule.

The pure part (evaluate / format_* / due_slot / day_totals) touches neither the
database nor the network; tests live on it. The data part reads the 30 s samples
in node_metric_samples (kept 180 days) and aggregates them per Moscow day, so a
quiet daytime never hides an evening overload: verdicts use p95 / max / number
of days over the threshold, not the current sample.
"""
from __future__ import annotations

import asyncio
import html
import logging
import math
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import FleetReportLog, Node
from app.services import subwatch
from app.services.telegram import send_message

log = logging.getLogger("remna.fleetreport")

CONFIG_KEY = "fleet_report_config"
STATE_KEY = "fleet_report_state"
OFFSET_KEY = "fleet_bot_offset"

DEFAULTS: dict = {
    "enabled": False,               # the owner turns it on from the browser
    "commands": True,               # /status /report /nodes (only while enabled)
    "chat_id": "",                  # empty: the chat of the subscription watcher
    "full_hours": [12, 21, 0],      # local hours of the full report
    "short_hours": [6, 9, 15, 18],  # local hours of the short one
    "utc_offset": 3,                # Moscow
    "window_days": 14,
    "mem_red_p95": 85,              # daily memory p95 at/over this ...
    "red_days": 3,                  # ... on at least this many days -> red
    "mem_orange_p95": 70,           # daily memory p95 at/over this ...
    "orange_days": 3,               # ... on at least this many days -> orange
    "trend_pp": 8,                  # week-over-week growth of the peak, points -> orange
    "chan_pct": 90,                 # channel utilisation in peaks
    "chan_days": 2,
    "save_mem_pct": 20,
    "save_cpu_pct": 15,
    "save_min_gb": 2,               # only nodes with a "big" plan can be saved on
    "headroom_pct": 30,
}
LIMITS: dict[str, tuple[int, int]] = {
    "utc_offset": (-12, 14), "window_days": (3, 60), "mem_red_p95": (30, 99),
    "red_days": (1, 30), "mem_orange_p95": (20, 98), "orange_days": (1, 30), "trend_pp": (1, 50), "chan_pct": (50, 100), "chan_days": (1, 30),
    "save_mem_pct": (1, 60), "save_cpu_pct": (1, 60), "save_min_gb": (1, 64),
    "headroom_pct": (0, 200),
}

# Nominal plan sizes (GB) and the cores that normally come with them.
SIZES_GB = [1, 2, 4, 8, 16, 32]
CORES_FOR_GB = {1: 1, 2: 2, 4: 2, 8: 4, 16: 8, 32: 8}
LEVEL_ORDER = {"red": 3, "orange": 2, "ok": 1, "nodata": 0}
LEVEL_RU = {"red": "красный", "orange": "оранжевый", "ok": "норма", "nodata": "нет данных"}
CATCHUP_MIN = 30        # a slot is still sent this many minutes after its hour started


def clean_config(cfg: dict) -> dict:
    out = {**DEFAULTS, **(cfg or {})}
    for k, (lo, hi) in LIMITS.items():
        try:
            out[k] = int(max(lo, min(hi, float(out[k]))))
        except (TypeError, ValueError):
            out[k] = DEFAULTS[k]
    for k in ("enabled", "commands"):
        out[k] = bool(out.get(k))
    for k in ("full_hours", "short_hours"):
        out[k] = parse_hours(out.get(k), DEFAULTS[k])
    out["chat_id"] = str(out.get("chat_id") or "").strip()
    return out


def parse_hours(raw, fallback: list[int]) -> list[int]:
    if isinstance(raw, str):
        raw = [p for p in raw.replace(";", ",").replace(" ", ",").split(",") if p.strip()]
    if not isinstance(raw, (list, tuple)):
        return list(fallback)
    hours: set[int] = set()
    for item in raw:
        try:
            h = int(item)
        except (TypeError, ValueError):
            continue
        if 0 <= h <= 23:
            hours.add(h)
    return sorted(hours)


# ── model ────────────────────────────────────────────────────────────────────

@dataclass
class DayStat:
    day: date
    mem_max: float | None = None
    mem_p95: float | None = None
    cpu_max: float | None = None
    rss_max: float | None = None
    net_p99_bps: float | None = None
    online_max: int | None = None
    oom: int = 0                # OOM kills that day (sampler deltas)


@dataclass
class NodeProfile:
    node_id: str
    name: str
    hosting: str = ""
    mem_total_mb: float | None = None
    bandwidth_mbps: float | None = None
    has_agent: bool = True
    up: bool | None = None
    days: list[DayStat] = field(default_factory=list)
    mem_p95: float | None = None
    mem_max: float | None = None
    cpu_p95: float | None = None
    cpu_max: float | None = None
    oom_14d: int = 0
    # current sample, for /status and /nodes
    cur_mem: float | None = None
    cur_cpu: float | None = None
    cur_online: int | None = None
    cur_bps: float | None = None
    sample_age_s: float | None = None
    # RAM size changed inside the window: samples only store mem_percent, so percentages from
    # before the change are of another total. Set by apply_resize(); days up to it are dropped.
    resized_day: date | None = None
    mem_prev_mb: float | None = None
    restart_days: list[date] = field(default_factory=list)   # watchdog rw-core restarts (local days)


@dataclass
class Verdict:
    level: str = "ok"                       # red | orange | ok | nodata
    channel: bool = False
    save: bool = False
    reasons: list[str] = field(default_factory=list)
    hot_days: int = 0          # days with memory p95 >= red threshold
    warm_days: int = 0         # days with memory p95 >= orange threshold
    trend_pp: float = 0.0
    n_days: int = 0
    rising: bool = False
    required_mb: float | None = None
    advice_gb: int | None = None
    advice_cores: int | None = None
    chan_days: int = 0
    chan_peak_pct: float | None = None
    save_gb: int | None = None
    resized: bool = False      # RAM changed recently and fewer than MIN_DAYS days since: no advice


MIN_DAYS = 3


def nominal_gb(total_mb: float | None) -> int | None:
    """Nearest nominal plan size for the RAM the kernel reports (960 MB -> 1 GB)."""
    if not total_mb or total_mb <= 0:
        return None
    gb = total_mb / 1024.0
    return min(SIZES_GB, key=lambda s: abs(math.log(s) - math.log(gb)))


def _fits(size_gb: int, need_mb: float) -> bool:
    return size_gb * 1024 * 0.93 >= need_mb          # the kernel never shows the full nominal


def _next_size(current: int | None, need_mb: float | None, force_bigger: bool) -> int | None:
    if need_mb is None:
        return None
    for s in SIZES_GB:
        if current is not None and force_bigger and s <= current:
            continue
        if _fits(s, need_mb):
            return s
    return SIZES_GB[-1]


def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs)


def weekly_trend(peaks: list[float]) -> float | None:
    """Peak of the last 7 daily values minus the peak of the 7 before (points); None if too short."""
    if len(peaks) < 10:
        return None
    last, prev = peaks[-7:], peaks[-14:-7]
    if len(prev) < 3:
        return None
    return max(last) - max(prev)


TREND_FLOOR = 40.0      # growth to a still-small level is not worth a message


def track_total(rec: dict | None, total_mb: float | None, now: datetime) -> dict | None:
    """State record {mb, prev_mb, since} of a node's RAM size.

    The samples keep only mem_percent, not the total it was taken against, so the moment the
    size changes is remembered here: percentages before `since` belong to the old size.
    A change is a different nominal plan (1/2/4/8 GB); kernel jitter of a few MB is not one.
    The first sighting has since=None — nothing is known to have changed.
    """
    if not total_mb or total_mb <= 0:
        return rec
    if not isinstance(rec, dict) or not rec.get("mb"):
        return {"mb": float(total_mb), "since": None}
    if nominal_gb(float(rec["mb"])) != nominal_gb(total_mb):
        return {"mb": float(total_mb), "prev_mb": float(rec["mb"]), "since": now.isoformat()}
    return rec


def parse_since(rec: dict | None) -> datetime | None:
    if not isinstance(rec, dict) or not rec.get("since"):
        return None
    try:
        ts = datetime.fromisoformat(str(rec["since"]))
    except ValueError:
        return None
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


def apply_resize(p: NodeProfile, since: datetime | None, utc_offset: int, today: date,
                 window_days: int, prev_mb: float | None = None) -> None:
    """Drop the days measured against the old RAM size and recount the peaks and OOMs.

    The day of the change mixes both sizes, so it goes too; only whole days after it stay.
    Without a change inside the window the profile is left as loaded.
    """
    if since is None:
        return
    cut = (since + timedelta(hours=utc_offset)).date()
    if (today - cut).days >= window_days:
        return
    p.resized_day, p.mem_prev_mb = cut, prev_mb
    p.days = [d for d in p.days if d.day > cut]
    mems = [d for d in p.days if d.mem_max is not None]
    p.mem_max = max((d.mem_max for d in mems), default=None)
    # overall p95 over the new days is not in the SQL; the worst daily p95 is the cautious stand-in
    p.mem_p95 = max(((d.mem_p95 if d.mem_p95 is not None else d.mem_max) for d in mems), default=None)
    p.oom_14d = sum(d.oom for d in p.days) + sum(1 for rd in p.restart_days if rd > cut)


def _channel(v: Verdict, p: NodeProfile, days: list[DayStat], cfg: dict) -> None:
    # share of the sold bandwidth used in the daily 99th-percentile peaks
    if p.bandwidth_mbps:
        cap = p.bandwidth_mbps * 1e6
        utils = [100.0 * d.net_p99_bps / cap for d in days if d.net_p99_bps is not None]
        v.chan_days = sum(1 for u in utils if u >= cfg["chan_pct"])
        v.chan_peak_pct = max(utils) if utils else None
        v.channel = v.chan_days >= cfg["chan_days"]


def evaluate(p: NodeProfile, cfg: dict) -> Verdict:
    v = Verdict()
    days = [d for d in p.days if d.mem_max is not None]
    v.n_days = len(days)
    if p.has_agent and p.resized_day is not None and v.n_days < MIN_DAYS:
        # RAM was just changed: old percentages are gone and the new ones are too few to judge
        # the size, so no "buy more"; a crash on the new size is still worth a look
        v.resized = True
        note = f"ОЗУ сменили {p.resized_day:%d.%m}, после — {v.n_days} дн."
        if p.oom_14d > 0:
            v.level, v.reasons = "red", [f"OOM/рестарт ядра ×{p.oom_14d}", note]
        else:
            v.level, v.reasons = "ok", [note]
        _channel(v, p, days, cfg)
        return v
    if not p.has_agent or v.n_days < MIN_DAYS or p.mem_max is None:
        v.level = "nodata"
        return v

    red_thr, org_thr = float(cfg["mem_red_p95"]), float(cfg["mem_orange_p95"])
    p95s = [d.mem_p95 if d.mem_p95 is not None else d.mem_max for d in days]
    v.hot_days = sum(1 for m in p95s if m >= red_thr)
    v.warm_days = sum(1 for m in p95s if m >= org_thr)
    tr = weekly_trend(p95s)
    v.trend_pp = tr or 0.0
    v.rising = tr is not None and tr >= cfg["trend_pp"] and max(p95s[-7:]) >= TREND_FLOOR
    nom = nominal_gb(p.mem_total_mb)
    if p.mem_total_mb:
        v.required_mb = round(p.mem_max / 100.0 * p.mem_total_mb * (1 + cfg["headroom_pct"] / 100.0))

    red = []
    if p.oom_14d > 0:
        red.append(f"OOM/рестарт ядра ×{p.oom_14d}")
    if v.hot_days >= cfg["red_days"]:
        red.append(f"p95 памяти ≥{int(red_thr)}% в {v.hot_days} из {v.n_days} дн.")
    orange = []
    if v.warm_days >= cfg["orange_days"]:
        orange.append(f"p95 памяти ≥{int(org_thr)}% в {v.warm_days} из {v.n_days} дн.")
    if v.rising:
        orange.append(f"память растёт: +{v.trend_pp:.0f} п.п. за неделю")

    if red:
        v.level, v.reasons = "red", red
    elif orange:
        v.level, v.reasons = "orange", orange
    if v.level in ("red", "orange"):
        # the smallest standard size that holds peak + headroom, and only when it is bigger than
        # the current one; if the current plan already suffices there is nothing to buy
        if p.mem_total_mb and v.required_mb is not None and v.required_mb > p.mem_total_mb:
            best = next((g for g in SIZES_GB if _fits(g, v.required_mb) and g * 1024 * 0.93 > p.mem_total_mb),
                        SIZES_GB[-1])
            v.advice_gb = best
            v.advice_cores = CORES_FOR_GB.get(best)

    _channel(v, p, days, cfg)

    # saving: nearly idle node on a big plan
    if (v.level == "ok" and nom and nom >= cfg["save_min_gb"] and v.n_days >= 7
            and (p.mem_p95 or 0) < cfg["save_mem_pct"] and (p.cpu_p95 or 0) < cfg["save_cpu_pct"]):
        v.save = True
        smaller = [s for s in SIZES_GB if s < nom and v.required_mb is not None and _fits(s, v.required_mb)]
        v.save_gb = max(smaller) if smaller else None
        v.save = v.save_gb is not None
    return v


# ── formatting ───────────────────────────────────────────────────────────────

def _e(s: object) -> str:
    return html.escape(str(s))


def fmt_gb(mb: float | None) -> str:
    if not mb:
        return "?"
    g = nominal_gb(mb)
    return f"{g} ГБ" if g else "?"


def fmt_bps(bps: float | None) -> str:
    if bps is None:
        return "—"
    if bps >= 1e9:
        return f"{bps / 1e9:.2f} Гбит/с"
    return f"{bps / 1e6:.0f} Мбит/с"


def _gb_num(mb: float | None) -> str:
    g = nominal_gb(mb)
    if not g or not mb:
        return "?"
    return str(g) if mb >= g * 1024 * 0.85 else f"{mb / 1024:.1f}"     # 512 MB plan is not "1 GB"


def _node_cells(p: NodeProfile, v: Verdict) -> tuple[str, str]:
    """(size cell, rest) for one problem node of the full report."""
    if v.advice_gb:
        size = f"{_gb_num(p.mem_total_mb)}→{v.advice_gb} ГБ"
    elif v.rising and not p.oom_14d:
        return "", f"рост +{v.trend_pp:.0f} п.п. за неделю"
    elif p.mem_total_mb:
        size = f"{_gb_num(p.mem_total_mb)} ГБ"
    else:
        size = ""
    rest = [f"пам {p.mem_p95 or 0:.0f}%"]
    if p.oom_14d:
        rest.append(f"OOM ×{p.oom_14d}")
    if v.rising and not v.advice_gb:
        rest.append(f"рост +{v.trend_pp:.0f} п.п.")
    return size, " · ".join(rest)


def _pre_rows(rows: list[tuple[str, str, str]]) -> list[str]:
    """Aligned monospace rows (name, size cell, rest) inside one <pre> block."""
    if not rows:
        return []
    w_name = max(len(r[0]) for r in rows)
    w_size = max((len(r[1]) for r in rows if r[1]), default=0)
    out = ["<pre>"]
    for name, size, rest in rows:
        cells = [name.ljust(w_name)]
        if size:
            cells.append(size.ljust(w_size) + (" · " + rest if rest else ""))
        else:
            cells.append(rest)
        out.append(_e("  ".join(cells).rstrip()))
    out.append("</pre>")
    return out


def _names(items: list[str]) -> str:
    return " · ".join(_e(x) for x in items)


@dataclass
class DayTotals:
    online_max: int | None = None
    online_min: int | None = None
    bps_avg: float | None = None
    bps_peak: float | None = None
    buckets: int = 0


def day_totals(rows: list[tuple[int | None, float | None, int]]) -> DayTotals:
    """rows: per 5-minute bucket (sum online, sum rx+tx bps, nodes reporting)."""
    t = DayTotals()
    if not rows:
        return t
    full = max(r[2] for r in rows)
    ok = [r for r in rows if r[2] >= max(1, full * 0.8)]      # skip buckets with half the fleet missing
    onl = [r[0] for r in ok if r[0] is not None]
    bps = [r[1] for r in ok if r[1] is not None]
    t.buckets = len(ok)
    if onl:
        t.online_max, t.online_min = int(max(onl)), int(min(onl))
    if bps:
        t.bps_avg, t.bps_peak = _mean(bps), max(bps)
    return t


@dataclass
class Snapshot:
    profiles: list[NodeProfile]
    verdicts: dict[str, Verdict]
    online_now: int | None = None
    bps_now: float | None = None
    up: int = 0
    total: int = 0


def header_totals(snap: Snapshot) -> str:
    on = "—" if snap.online_now is None else str(snap.online_now)
    return f"Онлайн {on} · трафик {fmt_bps(snap.bps_now)} · ноды {snap.up}/{snap.total}"


def _level_icon(level: str) -> str:
    return {"red": "🔴", "orange": "🟠", "ok": "🟢"}.get(level, "⚪")


def _short_report(snap: Snapshot, order: list[str], pro: dict[str, NodeProfile], changes: list[str],
                  prev_levels: dict[str, str], head: str) -> str:
    buy: dict[int, list[str]] = {}
    other_red, watch, chan, save, down = [], [], [], [], []
    n_ok = 0
    for nid in order:
        v, p = snap.verdicts[nid], pro[nid]
        if p.up is False:
            down.append(p.name)
        if v.level == "ok":
            n_ok += 1
        elif v.level == "red":
            (buy.setdefault(v.advice_gb, []) if v.advice_gb else other_red).append(p.name)
        elif v.level == "orange":
            watch.append(p.name)
        if v.channel:
            chan.append(p.name)
        if v.save:
            save.append(p.name)
    out = [head]
    if changes:
        out.append("🔁 Изменения: " + " · ".join(changes))
    elif prev_levels:
        out.append("🟢 без изменений")
    out.append("")
    groups = []
    for gb in sorted(buy):
        groups.append(f"🔴 Взять {gb} ГБ → {_names(buy[gb])}")
    if other_red:
        groups.append(f"🔴 Разобраться (OOM, памяти хватает) → {_names(other_red)}")
    if watch:
        groups.append(f"🟠 Следить → {_names(watch)}")
    if chan:
        groups.append(f"🟡 Канал → {_names(chan)}")
    if save:
        groups.append(f"💤 Можно сэкономить → {_names(save)}")
    if down:
        groups.append(f"⚫ Не отвечают → {_names(down)}")
    out.extend(groups)
    out.append(f"✅ Остальные {n_ok} нод в норме" if groups else f"✅ Все {n_ok} нод в норме")
    rated = [p for p in snap.profiles if snap.verdicts[p.node_id].level != "nodata" and p.mem_p95 is not None]
    worst = sorted(rated, key=lambda p: -(p.mem_p95 or 0))[:3]
    falls = sorted((p for p in snap.profiles if p.oom_14d > 0), key=lambda p: -p.oom_14d)[:3]
    if worst or falls:
        out.append("")
    if worst:
        out.append("Хуже всех: " + " · ".join(f"{_e(p.name)} {p.mem_p95:.0f}%" for p in worst))
    if falls:
        out.append("Чаще всех падают: " + " · ".join(f"{_e(p.name)} ×{p.oom_14d}" for p in falls))
    return "\n".join(out)


def build_report(snap: Snapshot, kind: str, now_local: datetime, prev_levels: dict[str, str],
                 totals: DayTotals | None = None, restarts: list[str] | None = None,
                 upgrades: dict[str, str] | None = None, day_label: str = "") -> str:
    """kind: full | short. Short groups nodes by action; full is one aligned line per node."""
    full = kind == "full"
    pro = {p.node_id: p for p in snap.profiles}
    order = sorted(snap.verdicts, key=lambda n: (-LEVEL_ORDER[snap.verdicts[n].level],
                                                 -(pro[n].mem_p95 or 0), pro[n].name))
    changes = []
    for nid in order:
        v, p = snap.verdicts[nid], pro[nid]
        before = prev_levels.get(nid)
        if before and before != v.level and before in LEVEL_ORDER and not (before == "nodata" or v.level == "nodata"):
            changes.append(f"{_e(p.name)} {_level_icon(before)}→{_level_icon(v.level)}")

    on = "—" if snap.online_now is None else str(snap.online_now)
    stamp = f"{now_local:%d.%m %H:%M}"
    if not full:
        return _short_report(snap, order, pro, changes, prev_levels, f"📊 <b>Парк</b> · {stamp} · онлайн {on}")

    out = [f"📊 <b>Парк</b> · {stamp}",
           f"Онлайн {on} · {fmt_bps(snap.bps_now)} · ноды {snap.up}/{snap.total}"]
    red = [(pro[n], snap.verdicts[n]) for n in order if snap.verdicts[n].level == "red"]
    orange = [(pro[n], snap.verdicts[n]) for n in order if snap.verdicts[n].level == "orange"]
    chan = [(pro[n], snap.verdicts[n]) for n in order if snap.verdicts[n].channel]
    save = [(pro[n], snap.verdicts[n]) for n in order if snap.verdicts[n].save]
    down = [pro[n].name for n in order if pro[n].up is False]
    nodata = [pro[n].name for n in order if snap.verdicts[n].level == "nodata" and pro[n].up is not False]
    n_ok = sum(1 for v in snap.verdicts.values() if v.level == "ok")

    def section(icon: str, name: str, rows: list[tuple[str, str, str]]) -> None:
        if rows:
            out.extend(["", f"{icon} <b>{name}</b> ({len(rows)})", *_pre_rows(rows)])

    section("🔴", "Менять сейчас", [(p.name, *_node_cells(p, v)) for p, v in red])
    section("🟠", "Скоро", [(p.name, *_node_cells(p, v)) for p, v in orange])
    section("🟡", "Канал", [(p.name, f"{p.bandwidth_mbps:.0f} Мбит/с" if p.bandwidth_mbps else "?",
                            f"пик {v.chan_peak_pct or 0:.0f}% · ≥90% в {v.chan_days} дн.")
                           for p, v in chan])
    section("💤", "Можно сэкономить", [(p.name, f"{_gb_num(p.mem_total_mb)}→{v.save_gb} ГБ",
                                       f"пам {p.mem_p95 or 0:.0f}% · CPU {p.cpu_p95 or 0:.0f}%")
                                      for p, v in save])
    if down:
        out.extend(["", f"⚫ Не отвечают: {_names(down)}"])
    if not (red or orange or chan or save or down):
        out.extend(["", "✅ Перегруженных нод нет."])
    out.extend(["", f"Остальные {n_ok} нод в норме."])
    if changes:
        out.extend(["", "🔁 Изменения: " + " · ".join(changes)])
    if nodata:
        out.append("ℹ️ Мало данных / нет агента: " + _names(nodata[:15])
                   + (f" и ещё {len(nodata) - 15}" if len(nodata) > 15 else ""))
    resized = [pro[n] for n in order if snap.verdicts[n].resized]
    if resized:
        out.append(f"🔄 ОЗУ сменили, оценка — через {MIN_DAYS} дн. новых данных: " + " · ".join(
            f"{_e(p.name)} {_gb_num(p.mem_prev_mb) + '→' if p.mem_prev_mb else ''}{_gb_num(p.mem_total_mb)} ГБ"
            f" с {p.resized_day:%d.%m}" for p in resized))
    if totals is not None:
        out.extend(["", f"🌙 <b>Итоги дня{(' ' + day_label) if day_label else ''}</b>"])
        if totals.online_max is not None:
            out.append(f"Онлайн: макс {totals.online_max}, мин {totals.online_min}")
        if totals.bps_avg is not None:
            out.append(f"Трафик Σ: средний {fmt_bps(totals.bps_avg)}, пик {fmt_bps(totals.bps_peak)}")
        out.append("Рестарты ядер по памяти: " + (_names(restarts) if restarts else "не было"))
        if upgrades:
            out.append("Список на апгрейд: " + "; ".join(
                f"{_e(n)} → {_e(a)}" for n, a in sorted(upgrades.items())))
        else:
            out.append("Список на апгрейд пуст.")
    return "\n".join(out)


def build_status(snap: Snapshot, ai_line: str, last_report: str, next_report: str) -> str:
    cur = [p for p in snap.profiles if p.up]
    top_mem = sorted((p for p in cur if p.cur_mem is not None), key=lambda p: -p.cur_mem)[:5]
    top_net = []
    for p in cur:
        if p.cur_bps is not None and p.bandwidth_mbps:
            top_net.append((100.0 * p.cur_bps / (p.bandwidth_mbps * 1e6), p))
    top_net.sort(key=lambda t: -t[0])
    out = ["📡 <b>Статус парка</b>", header_totals(snap), "", "<b>Топ-5 по памяти</b>"]
    for p in top_mem:
        out.append(f"• {_e(p.name)} · {fmt_gb(p.mem_total_mb)} · память {p.cur_mem:.0f}% · "
                   f"CPU {(p.cur_cpu or 0):.0f}% · онлайн {p.cur_online if p.cur_online is not None else '—'}")
    if not top_mem:
        out.append("нет данных")
    out += ["", "<b>Топ-3 по каналу</b>"]
    for util, p in top_net[:3]:
        out.append(f"• {_e(p.name)} · {util:.0f}% ({fmt_bps(p.cur_bps)} из {p.bandwidth_mbps:.0f} Мбит/с)")
    if not top_net:
        out.append("нет данных")
    out += ["", f"🧠📱 ИИ-доступ: {ai_line}", f"Отчёт: прошлый {last_report}, следующий {next_report}"]
    return "\n".join(out)


def build_nodes(snap: Snapshot) -> str:
    out = [f"🖥 <b>Ноды</b> · {snap.up}/{snap.total} на связи"]
    for p in sorted(snap.profiles, key=lambda p: p.name.lower()):
        v = snap.verdicts.get(p.node_id)
        icon = "⚫" if p.up is False else {"red": "🔴", "orange": "🟠"}.get(v.level if v else "", "🟢")
        if p.up is False or p.cur_mem is None:
            out.append(f"{icon} {_e(p.name)} · {fmt_gb(p.mem_total_mb)} · нет данных")
        else:
            out.append(f"{icon} {_e(p.name)} · {fmt_gb(p.mem_total_mb)} · память {p.cur_mem:.0f}% · "
                       f"CPU {(p.cur_cpu or 0):.0f}% · онлайн {p.cur_online if p.cur_online is not None else '—'}")
    return "\n".join(out)


def split_message(text_: str, limit: int = 3900) -> list[str]:
    """Split on line boundaries; a <pre> block cut in two is closed and reopened (valid HTML)."""
    chunks: list[str] = []
    cur, in_pre = "", False
    for line in text_.split("\n"):
        if len(cur) + len(line) + 8 > limit and cur.strip():
            chunks.append(cur.rstrip("\n") + ("\n</pre>" if in_pre else ""))
            cur = "<pre>\n" if in_pre else ""
        cur += line + "\n"
        if line.startswith("<pre>"):
            in_pre = True
        if line.startswith("</pre>"):
            in_pre = False
    if cur.strip():
        chunks.append(cur.rstrip("\n"))
    return chunks


# ── schedule ─────────────────────────────────────────────────────────────────

def local_now(now: datetime, cfg: dict) -> datetime:
    return (now.astimezone(timezone.utc) + timedelta(hours=int(cfg["utc_offset"]))).replace(tzinfo=None)


def due_slot(now: datetime, cfg: dict, last_slot: str | None) -> tuple[str, str] | None:
    """(slot key, kind) when a report is due now and was not sent for this slot yet."""
    loc = local_now(now, cfg)
    if loc.minute >= CATCHUP_MIN:
        return None
    if loc.hour in cfg["full_hours"]:
        kind = "full"
    elif loc.hour in cfg["short_hours"]:
        kind = "short"
    else:
        return None
    slot = f"{loc:%Y-%m-%d %H}"
    return None if slot == last_slot else (slot, kind)


def next_report(now: datetime, cfg: dict) -> datetime | None:
    """Local time of the next scheduled slot."""
    hours = sorted(set(cfg["full_hours"]) | set(cfg["short_hours"]))
    if not hours:
        return None
    loc = local_now(now, cfg).replace(minute=0, second=0, microsecond=0)
    for add in range(1, 49):
        cand = loc + timedelta(hours=add)
        if cand.hour in hours:
            return cand
    return None


def is_day_close(now: datetime, cfg: dict) -> bool:
    """The 00:00 report also carries the totals of the day that just ended."""
    return local_now(now, cfg).hour == 0


# ── data ─────────────────────────────────────────────────────────────────────

_DAILY_SQL = text("""
SELECT node_id,
       ((recorded_at AT TIME ZONE 'UTC') + make_interval(hours => :off))::date AS d,
       max(mem_percent) AS mem_max,
       max(cpu_percent) AS cpu_max,
       max(xray_rss_mb) AS rss_max,
       percentile_cont(0.99) WITHIN GROUP (ORDER BY greatest(coalesce(net_rx_bps,0), coalesce(net_tx_bps,0))) AS net_p99,
       max(users_online) AS online_max,
       coalesce(sum(oom_kills),0) AS oom,
       percentile_cont(0.95) WITHIN GROUP (ORDER BY mem_percent) AS mem_p95
FROM node_metric_samples
WHERE recorded_at >= :since
GROUP BY node_id, d
ORDER BY node_id, d
""")

_OVERALL_SQL = text("""
SELECT node_id,
       max(mem_percent), percentile_cont(0.95) WITHIN GROUP (ORDER BY mem_percent),
       max(cpu_percent), percentile_cont(0.95) WITHIN GROUP (ORDER BY cpu_percent)
FROM node_metric_samples
WHERE recorded_at >= :since
GROUP BY node_id
""")

_LATEST_SQL = text("""
SELECT DISTINCT ON (node_id) node_id, recorded_at, online, cpu_percent, mem_percent,
       net_rx_bps, net_tx_bps, users_online
FROM node_metric_samples
WHERE recorded_at >= :since
ORDER BY node_id, recorded_at DESC
""")

_BUCKET_SQL = text("""
SELECT sum(uo), sum(coalesce(rx,0)+coalesce(tx,0)), count(*) FROM (
  SELECT node_id, floor(extract(epoch FROM recorded_at)/300) AS b,
         avg(users_online) AS uo, avg(net_rx_bps) AS rx, avg(net_tx_bps) AS tx
  FROM node_metric_samples
  WHERE recorded_at >= :a AND recorded_at < :b
  GROUP BY node_id, b) t
GROUP BY b ORDER BY b
""")

FRESH_S = 180.0
_lock = asyncio.Lock()
_snap_cache: dict = {"at": 0.0, "snap": None}


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def get_config(db: AsyncSession) -> dict:
    return clean_config(await subwatch._json_setting(db, CONFIG_KEY, DEFAULTS))


async def save_config(db: AsyncSession, cfg: dict) -> None:
    await subwatch._save_json(db, CONFIG_KEY, clean_config(cfg))


async def get_state(db: AsyncSession) -> dict:
    st = await subwatch._json_setting(db, STATE_KEY, {"last_slot": None, "last_sent_at": None,
                                                      "last_kind": None, "levels": {},
                                                      "upgrades": {}, "totals": {}, "mem_since": {}})
    for k in ("levels", "upgrades", "totals", "mem_since"):
        if not isinstance(st.get(k), dict):
            st[k] = {}
    return st


async def save_state(db: AsyncSession, st: dict) -> None:
    await subwatch._save_json(db, STATE_KEY, st)


async def effective_chat(db: AsyncSession, cfg: dict) -> str:
    return (cfg.get("chat_id") or "").strip() or \
        ((await subwatch.get_config(db)).get("chat_id") or "").strip()


def count_restarts(restarts: list, since: datetime) -> dict[str, int]:
    """Automatic (watchdog) rw-core restarts per node since `since`; manual ones do not count."""
    out: dict[str, int] = {}
    for r in restarts or []:
        if not isinstance(r, dict) or r.get("source") == "manual":
            continue
        try:
            ts = datetime.fromisoformat(str(r.get("ts")))
        except ValueError:
            continue
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        if ts >= since:
            nid = str(r.get("node_id"))
            out[nid] = out.get(nid, 0) + 1
    return out


def restart_local_days(restarts: list, since: datetime, utc_offset: int) -> dict[str, list[date]]:
    """Watchdog rw-core restarts per node since `since`, as local days (one entry per restart)."""
    out: dict[str, list[date]] = {}
    for r in restarts or []:
        if not isinstance(r, dict) or r.get("source") == "manual":
            continue
        try:
            ts = datetime.fromisoformat(str(r.get("ts")))
        except ValueError:
            continue
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        if ts >= since:
            out.setdefault(str(r.get("node_id")), []).append((ts + timedelta(hours=utc_offset)).date())
    return out


async def load_snapshot(db: AsyncSession, cfg: dict, state: dict | None = None,
                        max_age_s: float = 0.0) -> Snapshot:
    """Profiles + verdicts. max_age_s>0 reuses a recent snapshot (for chat commands)."""
    import time

    from app.services.agent_metrics import peek_cached_status

    async with _lock:
        if max_age_s and _snap_cache["snap"] is not None and time.monotonic() - _snap_cache["at"] < max_age_s:
            return _snap_cache["snap"]
        now = _now()
        off = int(cfg["utc_offset"])
        days = int(cfg["window_days"])
        since = now - timedelta(days=days)
        nodes = list((await db.execute(select(Node).options(selectinload(Node.hosting)))).scalars().all())
        daily = (await db.execute(_DAILY_SQL, {"since": since, "off": off})).all()
        overall = {str(r[0]): r for r in (await db.execute(_OVERALL_SQL, {"since": since})).all()}
        latest = {str(r[0]): r for r in
                  (await db.execute(_LATEST_SQL, {"since": now - timedelta(minutes=10)})).all()}

        by_node_days: dict[str, list[DayStat]] = {}
        oom: dict[str, int] = {}
        for r in daily:
            nid = str(r[0])
            by_node_days.setdefault(nid, []).append(DayStat(
                day=r[1], mem_max=r[2], cpu_max=r[3], rss_max=r[4],
                net_p99_bps=r[5], online_max=r[6], mem_p95=r[8], oom=int(r[7] or 0)))
            oom[nid] = oom.get(nid, 0) + int(r[7] or 0)

        from app.services import mem_watch
        restart_days = restart_local_days((await mem_watch.get_state(db)).get("restarts"), since, off)
        for nid, ds in restart_days.items():
            oom[nid] = oom.get(nid, 0) + len(ds)

        totals_mem = dict((state or {}).get("totals") or {})
        mem_since = dict((state or {}).get("mem_since") or {})
        today = local_now(now, cfg).date()
        profiles: list[NodeProfile] = []
        for n in nodes:
            nid = str(n.id)
            ag = peek_cached_status(nid)
            total = getattr(ag, "mem_total_mb", None) if ag is not None else None
            if total:
                totals_mem[nid] = total
            bw = n.hosting.bandwidth_mbps if n.hosting and n.hosting.bandwidth_mbps else None
            if not bw and ag is not None and getattr(ag, "net_link_mbps", None):
                bw = ag.net_link_mbps
            p = NodeProfile(node_id=nid, name=n.name, hosting=n.hosting.name if n.hosting else "",
                            mem_total_mb=total or totals_mem.get(nid), bandwidth_mbps=bw,
                            has_agent=bool(n.agent_token_enc))
            o = overall.get(nid)
            if o:
                p.mem_max, p.mem_p95, p.cpu_max, p.cpu_p95 = o[1], o[2], o[3], o[4]
            p.days = by_node_days.get(nid, [])[-days:]
            p.oom_14d = oom.get(nid, 0)
            p.restart_days = restart_days.get(nid, [])
            rec = track_total(mem_since.get(nid), p.mem_total_mb, now)
            if rec is not None:
                mem_since[nid] = rec
                apply_resize(p, parse_since(rec), off, today, days, rec.get("prev_mb"))
            lt = latest.get(nid)
            if lt is not None:
                age = (now - lt[1]).total_seconds()
                p.sample_age_s = age
                fresh = age <= FRESH_S
                p.up = bool(fresh and (lt[3] is not None or lt[2]))
                if fresh:
                    p.cur_cpu, p.cur_mem, p.cur_online = lt[3], lt[4], lt[7]
                    if lt[5] is not None or lt[6] is not None:
                        p.cur_bps = float((lt[5] or 0) + (lt[6] or 0))
            else:
                p.up = False
            profiles.append(p)
        if state is not None:
            state["totals"] = totals_mem
            state["mem_since"] = mem_since
        verdicts = {p.node_id: evaluate(p, cfg) for p in profiles}
        up_nodes = [p for p in profiles if p.up]
        onl = [p.cur_online for p in up_nodes if p.cur_online is not None]
        bps = [p.cur_bps for p in up_nodes if p.cur_bps is not None]
        snap = Snapshot(profiles=profiles, verdicts=verdicts,
                        online_now=int(sum(onl)) if onl else None,
                        bps_now=float(sum(bps)) if bps else None,
                        up=len(up_nodes), total=len(profiles))
        _snap_cache.update(at=time.monotonic(), snap=snap)
        return snap


async def day_totals_for(db: AsyncSession, cfg: dict, local_day: date) -> DayTotals:
    off = timedelta(hours=int(cfg["utc_offset"]))
    start = datetime(local_day.year, local_day.month, local_day.day, tzinfo=timezone.utc) - off
    rows = (await db.execute(_BUCKET_SQL, {"a": start, "b": start + timedelta(days=1)})).all()
    return day_totals([(float(r[0]) if r[0] is not None else None,
                        float(r[1]) if r[1] is not None else None, int(r[2])) for r in rows])


def restart_names(state_mem: dict, names: dict[str, str], start: datetime, end: datetime) -> list[str]:
    out = []
    for r in state_mem.get("restarts") or []:
        try:
            ts = datetime.fromisoformat(str(r.get("ts")))
        except ValueError:
            continue
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        if start <= ts < end:
            out.append(names.get(str(r.get("node_id")), "?"))
    return out


def update_upgrades(upgrades: dict[str, str], snap: Snapshot) -> dict[str, str]:
    """Accumulated list: nodes flagged red/orange stay until they leave the red zone."""
    out = dict(upgrades)
    names_now = {}
    for nid, v in snap.verdicts.items():
        p = next(x for x in snap.profiles if x.node_id == nid)
        names_now[p.name] = v
    for name, v in names_now.items():
        if v.level in ("red", "orange") and v.advice_gb:
            out[name] = f"{v.advice_gb} ГБ / {v.advice_cores} яд."
        elif v.level == "ok" or v.resized:      # RAM just changed: the old advice is done or stale
            out.pop(name, None)
    return {k: val for k, val in out.items() if k in names_now}


async def ai_summary_line(db: AsyncSession) -> str:
    from app.services import ai_access

    try:
        shown, _prev, rows = await ai_access.load_matrix(db)
    except Exception:  # noqa: BLE001
        return "нет данных"
    if not rows:
        return "нет данных"
    good = 0
    for r in rows:
        c = r.check
        ai = [x for k, x in (c.results or {}).items()
              if ai_access.service_group(k) == ai_access.GROUP_AI]
        if c.tunnel == "up" and ai and all((x or {}).get("v") == ai_access.OK for x in ai):
            good += 1
    return f"{good}/{len(rows)} хостов, где доступны все нейросети"


async def send_text(db: AsyncSession, cfg: dict, text_: str) -> str | None:
    """Send (split to Telegram limits). None — delivered, else an error text."""
    token = await subwatch.bot_token(db)
    chat = await effective_chat(db, cfg)
    if not token or not chat:
        return "не заданы токен бота или чат"
    for chunk in split_message(text_):
        err = await asyncio.to_thread(send_message, token, chat, chunk)
        if err:
            return err
    return None


async def compose(db: AsyncSession, cfg: dict, kind: str, state: dict, now: datetime,
                  with_day: bool | None = None) -> tuple[str, Snapshot]:
    from app.services import mem_watch

    snap = await load_snapshot(db, cfg, state)
    loc = local_now(now, cfg)
    totals = restarts = None
    day_label = ""
    upgrades = update_upgrades(state.get("upgrades") or {}, snap)
    close = is_day_close(now, cfg) if with_day is None else with_day
    if close and kind == "full":
        day = loc.date() - timedelta(days=1) if loc.hour == 0 else loc.date()
        totals = await day_totals_for(db, cfg, day)
        off = timedelta(hours=int(cfg["utc_offset"]))
        start = datetime(day.year, day.month, day.day, tzinfo=timezone.utc) - off
        mem_state = await mem_watch.get_state(db)
        restarts = restart_names(mem_state, {p.node_id: p.name for p in snap.profiles},
                                 start, start + timedelta(days=1))
        day_label = day.strftime("%d.%m")
    text_ = build_report(snap, kind, loc, state.get("levels") or {}, totals, restarts, upgrades, day_label)
    state["upgrades"] = upgrades
    state["levels"] = {nid: v.level for nid, v in snap.verdicts.items()}
    return text_, snap


async def run_report(db: AsyncSession, cfg: dict, kind: str, slot: str, now: datetime | None = None,
                     send: bool = True) -> tuple[str, str | None]:
    """Compose, optionally send, log and persist state. Returns (text, error)."""
    now = now or _now()
    state = await get_state(db)
    text_, _ = await compose(db, cfg, kind, state, now)
    err = await send_text(db, cfg, text_) if send else None
    if send:
        db.add(FleetReportLog(id=uuid.uuid4(), slot=slot, kind=kind, delivered=err is None,
                              error=err, text=text_))
        if err is None:
            state.update(last_slot=slot if kind != "manual" else state.get("last_slot"),
                         last_sent_at=now.isoformat(), last_kind=kind)
        await save_state(db, state)
    await db.commit()
    return text_, err


async def run_fleet_report() -> None:
    """Scheduler: wakes every 30 s, sends the report when a slot is due. Off by default."""
    from app.db import SessionLocal

    await asyncio.sleep(45)
    while True:
        try:
            async with SessionLocal() as db:
                cfg = await get_config(db)
                if cfg["enabled"]:
                    state = await get_state(db)
                    due = due_slot(_now(), cfg, state.get("last_slot"))
                    if due:
                        slot, kind = due
                        _text, err = await run_report(db, cfg, kind, slot)
                        if err:
                            log.warning("fleet report not delivered: %s", err)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("fleet report tick failed")
        await asyncio.sleep(30)
