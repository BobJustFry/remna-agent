"""Query and insert node metric history (ping / CPU / RAM / disk / TCP / swap / OOM / RU ping)."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Literal

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import NodeMetricSample
from app.services.counter_delta import retr_data_segs, retr_point

MetricRange = Literal["day", "week", "month", "all"]

# Keep ~400 points per series for charts.
_RANGE_WINDOW: dict[str, timedelta | None] = {
    "day": timedelta(days=1),
    "week": timedelta(days=7),
    "month": timedelta(days=31),
    "all": None,
}
_RANGE_STEP: dict[str, int] = {
    "day": 5 * 60,
    "week": 20 * 60,
    "month": 2 * 3600,
    "all": 6 * 3600,
}
_RETENTION = timedelta(days=180)
# Интервал сэмплера: живёт здесь, потому что по нему же история знает, сколько
# времени покрывает один отсчёт (порог объёма для ретрансмитов).
SAMPLE_INTERVAL_SEC = 30.0
_MAX_POINTS = 420


def _as_bps(value) -> int | None:
    """Bits per second as a whole number; averages over a bucket come back as float."""
    if value is None:
        return None
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return None


def _as_int(value) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _round(v: float | None) -> float | None:
    if v is None:
        return None
    return round(float(v), 1)


async def insert_samples(
    db: AsyncSession,
    rows: list[dict],
    *,
    recorded_at: datetime | None = None,
) -> int:
    now = recorded_at or datetime.now(timezone.utc)
    samples = [
        NodeMetricSample(
            id=uuid.uuid4(),
            node_id=row["node_id"],
            recorded_at=now,
            online=bool(row.get("online")),
            ping_ms=_round(row.get("ping_ms")),
            cpu_percent=_round(row.get("cpu_percent")),
            mem_percent=_round(row.get("mem_percent")),
            disk_percent=_round(row.get("disk_percent")),
            net_rx_bps=_as_bps(row.get("net_rx_bps")),
            net_tx_bps=_as_bps(row.get("net_tx_bps")),
            users_online=_as_int(row.get("users_online")),
            capacity=_as_int(row.get("capacity")),
            tcp_out_segs=_as_int(row.get("tcp_out_segs")),
            tcp_retrans_segs=_as_int(row.get("tcp_retrans_segs")),
            tcp_syn_retrans=_as_int(row.get("tcp_syn_retrans")),
            tcp_orig_data_sent=_as_int(row.get("tcp_orig_data_sent")),
            oom_kills=_as_int(row.get("oom_kills")),
            swap_percent=_round(row.get("swap_percent")),
            ru_ping_ms=_round(row.get("ru_ping_ms")),
            ru_loss_pct=_round(row.get("ru_loss_pct")),
        )
        for row in rows
    ]
    if not samples:
        return 0
    db.add_all(samples)
    await db.commit()
    return len(samples)


async def prune_old(db: AsyncSession) -> int:
    cutoff = datetime.now(timezone.utc) - _RETENTION
    result = await db.execute(delete(NodeMetricSample).where(NodeMetricSample.recorded_at < cutoff))
    await db.commit()
    return int(result.rowcount or 0)


async def clear_all_samples(db: AsyncSession) -> int:
    result = await db.execute(delete(NodeMetricSample))
    await db.commit()
    return int(result.rowcount or 0)


async def clear_node_samples(db: AsyncSession, node_id: uuid.UUID) -> int:
    """Стереть накопленные отсчёты одной ноды.

    Нужно, когда ноду переехали на другое железо или сменили хостинг: старые
    точки описывают уже не тот сервер и портят и график, и средние. Саму ноду
    и её доступы не трогаем — сбрасывается только история.
    """
    result = await db.execute(delete(NodeMetricSample).where(NodeMetricSample.node_id == node_id))
    await db.commit()
    return int(result.rowcount or 0)


async def fetch_series(
    db: AsyncSession,
    *,
    range_key: MetricRange,
    node_id: uuid.UUID | None = None,
) -> tuple[int, float, float, dict[str, list[dict]]]:
    now = datetime.now(timezone.utc)
    window = _RANGE_WINDOW[range_key]
    step = _RANGE_STEP[range_key]
    since = now - window if window else None

    oldest_q = select(func.min(NodeMetricSample.recorded_at))
    if node_id is not None:
        oldest_q = oldest_q.where(NodeMetricSample.node_id == node_id)
    if since is not None:
        oldest_q = oldest_q.where(NodeMetricSample.recorded_at >= since)
    oldest = (await db.execute(oldest_q)).scalar_one_or_none()

    if range_key == "all":
        if oldest is not None:
            if oldest.tzinfo is None:
                oldest = oldest.replace(tzinfo=timezone.utc)
            span = max(60.0, (now - oldest).total_seconds())
            step = max(60, int(span / _MAX_POINTS))
            since = oldest
        else:
            since = now - timedelta(days=1)

    from_ts = since.timestamp() if since is not None else now.timestamp() - 86400
    to_ts = now.timestamp()

    epoch = func.extract("epoch", NodeMetricSample.recorded_at)
    bucket = (func.floor(epoch / float(step)) * float(step)).label("t")

    stmt = select(
        NodeMetricSample.node_id,
        bucket,
        func.avg(NodeMetricSample.ping_ms).label("ping_ms"),
        func.avg(NodeMetricSample.cpu_percent).label("cpu_percent"),
        func.avg(NodeMetricSample.mem_percent).label("mem_percent"),
        func.avg(NodeMetricSample.disk_percent).label("disk_percent"),
        func.avg(NodeMetricSample.net_rx_bps).label("net_rx_bps"),
        func.avg(NodeMetricSample.net_tx_bps).label("net_tx_bps"),
        func.avg(NodeMetricSample.users_online).label("users_online"),
        func.max(NodeMetricSample.capacity).label("capacity"),
        # Дельты складываем: за шаг графика важно, сколько всего ушло и сколько
        # из этого пришлось повторить, а доля считается уже от сумм.
        func.sum(NodeMetricSample.tcp_out_segs).label("tcp_out_segs"),
        func.sum(NodeMetricSample.tcp_retrans_segs).label("tcp_retrans_segs"),
        # Для новой формулы ретрансмиты берём только из отсчётов, где есть и SYN,
        # и сегменты с данными: в шаге, где агент обновился, старые отсчёты иначе
        # попали бы в числитель без своей пары. Число таких отсчётов — чтобы
        # пересчитать порог объёма на время, которое точка реально покрывает.
        func.sum(NodeMetricSample.tcp_retrans_segs)
        .filter(NodeMetricSample.tcp_orig_data_sent.is_not(None))
        .label("tcp_retrans_new"),
        func.sum(NodeMetricSample.tcp_syn_retrans).label("tcp_syn_retrans"),
        func.sum(NodeMetricSample.tcp_orig_data_sent).label("tcp_orig_data_sent"),
        func.count(NodeMetricSample.tcp_orig_data_sent).label("data_samples"),
        func.sum(NodeMetricSample.oom_kills).label("oom_kills"),
        func.avg(NodeMetricSample.swap_percent).label("swap_percent"),
        func.avg(NodeMetricSample.ru_ping_ms).label("ru_ping_ms"),
        func.avg(NodeMetricSample.ru_loss_pct).label("ru_loss_pct"),
    )
    if since is not None:
        stmt = stmt.where(NodeMetricSample.recorded_at >= since)
    if node_id is not None:
        stmt = stmt.where(NodeMetricSample.node_id == node_id)
    stmt = stmt.group_by(NodeMetricSample.node_id, bucket).order_by(bucket)

    series: dict[str, list[dict]] = {}
    result = await db.execute(stmt)
    for (
        node_uuid,
        t_raw,
        ping_ms,
        cpu_percent,
        mem_percent,
        disk_percent,
        net_rx_bps,
        net_tx_bps,
        users_online,
        capacity,
        tcp_out_segs,
        tcp_retrans_segs,
        tcp_retrans_new,
        tcp_syn_retrans,
        tcp_orig_data_sent,
        data_samples,
        oom_kills,
        swap_percent,
        ru_ping_ms,
        ru_loss_pct,
    ) in result.all():
        if t_raw is None:
            continue
        key = str(node_uuid)
        pct, low_volume = retr_point(
            retrans=tcp_retrans_segs,
            out=tcp_out_segs,
            retrans_new=tcp_retrans_new,
            syn_retrans=tcp_syn_retrans,
            orig_data=tcp_orig_data_sent,
            covered_sec=min(float(step), float(data_samples or 0) * SAMPLE_INTERVAL_SEC),
            min_segs_5min=settings.retr_min_data_segs_5min,
        )
        new_formula = tcp_syn_retrans is not None and tcp_orig_data_sent is not None
        series.setdefault(key, []).append(
            {
                "t": round(float(t_raw), 3),
                "ping_ms": _round(ping_ms),
                "cpu_percent": _round(cpu_percent),
                "mem_percent": _round(mem_percent),
                "disk_percent": _round(disk_percent),
                "net_rx_bps": _as_bps(net_rx_bps),
                "net_tx_bps": _as_bps(net_tx_bps),
                "users_online": _round(users_online),
                "capacity": _as_int(capacity),
                "tcp_out_segs": _as_int(tcp_out_segs),
                "tcp_retrans_segs": _as_int(tcp_retrans_segs),
                "tcp_syn_retrans": _as_int(tcp_syn_retrans),
                "tcp_orig_data_sent": _as_int(tcp_orig_data_sent),
                "tcp_retrans_no_syn": (
                    retr_data_segs(tcp_retrans_new, tcp_syn_retrans) if new_formula else None
                ),
                "retr_pct": pct,
                "retr_low_volume": low_volume,
                "oom_kills": _as_int(oom_kills),
                "swap_percent": _round(swap_percent),
                "ru_ping_ms": _round(ru_ping_ms),
                "ru_loss_pct": _round(ru_loss_pct),
            }
        )
    return step, from_ts, to_ts, series
