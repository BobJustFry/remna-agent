import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class AuthType(str, enum.Enum):
    password = "password"
    private_key = "private_key"


class Hosting(Base):
    __tablename__ = "hostings"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(128), nullable=False, unique=True, index=True)
    website_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    favicon_data: Mapped[str | None] = mapped_column(Text, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Channel the hosting sells, Mbit/s. Null — unknown, the gauge stays blank.
    bandwidth_mbps: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    nodes: Mapped[list["Node"]] = relationship(back_populates="hosting")


class Node(Base):
    __tablename__ = "nodes"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    host: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    ssh_port: Mapped[int] = mapped_column(Integer, nullable=False, default=22)
    ssh_user: Mapped[str] = mapped_column(String(128), nullable=False, default="root")
    auth_type: Mapped[str] = mapped_column(String(32), nullable=False)
    password_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    private_key_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    hosting_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("hostings.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    country_code: Mapped[str | None] = mapped_column(String(8), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    agent_token_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    agent_port: Mapped[int] = mapped_column(Integer, nullable=False, default=7422)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    hosting: Mapped[Hosting | None] = relationship(back_populates="nodes")
    metric_samples: Mapped[list["NodeMetricSample"]] = relationship(
        back_populates="node", cascade="all, delete-orphan"
    )


class NodeMetricSample(Base):
    __tablename__ = "node_metric_samples"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    node_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("nodes.id", ondelete="CASCADE"), nullable=False, index=True
    )
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    online: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    ping_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    cpu_percent: Mapped[float | None] = mapped_column(Float, nullable=True)
    mem_percent: Mapped[float | None] = mapped_column(Float, nullable=True)
    disk_percent: Mapped[float | None] = mapped_column(Float, nullable=True)
    net_rx_bps: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    net_tx_bps: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    # Онлайн из ядра Xray и ёмкость ноды на момент отсчёта. Ёмкость пишем рядом,
    # а не берём текущую: она меняется при смене конфига, и старый график иначе
    # сравнивался бы с потолком, которого тогда не было.
    users_online: Mapped[int | None] = mapped_column(Integer, nullable=True)
    capacity: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # TCP-сегменты и ретрансмиты, OOM-kill — ДЕЛЬТЫ за интервал между отсчётами,
    # а не накопительные счётчики: так шаг графика суммирует их без оглядки на
    # перезагрузки ноды. Null — старый агент, первый тик или счётчик сбросился.
    tcp_out_segs: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    tcp_retrans_segs: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    # TcpExt TCPSynRetrans / TCPOrigDataSent — тоже дельты, с агента 0.1.22. По ним
    # retr_pct = (retrans − syn) / orig_data: без повторов SYN-ACK сканерам и без
    # чистых ACK в знаменателе. Null — старый агент, считаем по старой формуле.
    tcp_syn_retrans: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    tcp_orig_data_sent: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    oom_kills: Mapped[int | None] = mapped_column(Integer, nullable=True)
    swap_percent: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Пинг и потери до якорей RIPE Atlas в Москве, как их видит сама нода.
    ru_ping_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    ru_loss_pct: Mapped[float | None] = mapped_column(Float, nullable=True)

    node: Mapped["Node"] = relationship(back_populates="metric_samples")


class AppSetting(Base):
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class ApiToken(Base):
    """Персональный токен доступа к API панели.

    Хранится только SHA-256 от токена: утечка базы не даёт готовых ключей.
    ``prefix`` — первые символы токена, чтобы человек узнал его в списке.
    """

    __tablename__ = "api_tokens"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    prefix: Mapped[str] = mapped_column(String(16), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SubCheck(Base):
    """Одна попытка забрать подписку так, как её забирает клиент.

    Пишем и удачи тоже: без них не видно, с какого момента лежит и сколько
    длилась прошлая авария. Строки чистятся по возрасту, история короткая.
    """

    __tablename__ = "sub_checks"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    checked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
    via: Mapped[str] = mapped_column(String(16), nullable=False)   # cdn | origin
    ok: Mapped[bool] = mapped_column(Boolean, nullable=False)
    http_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    hosts: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cert_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class NodeOutage(Base):
    """Журнал простоев: нода (или разом большинство нод) молчала дольше порога.

    Открытая строка (ended_at пуст) — тревога, которая ещё не снята. По ней
    рагент после перезапуска помнит, о чём уже сообщил, и не поднимает шум по
    второму разу. Имя, адрес и хостер пишутся как были на момент тревоги:
    журнал переживает и переименование ноды, и её удаление.
    """

    __tablename__ = "node_outages"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    # node — одна нода; fleet — рагент разом перестал видеть большинство нод.
    kind: Mapped[str] = mapped_column(
        String(8), nullable=False, default="node", server_default="node"
    )
    node_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("nodes.id", ondelete="SET NULL"), nullable=True, index=True
    )
    node_name: Mapped[str] = mapped_column(String(255), nullable=False)
    host: Mapped[str | None] = mapped_column(String(255), nullable=True)
    hosting: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # С какого опроса молчит — и когда молчание перевалило за порог.
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    alerted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # recovered — снова ответила; muted — исключили или выключили в панели;
    # removed — ноду удалили; stopped — следилку выключили.
    resolution: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # Когда сообщение о падении реально ушло. Пусто — не доставлено (или
    # уведомления выключены): попробуем ещё раз.
    notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reminded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Что именно молчало на момент тревоги: {"agent", "ssh", "panel"}; у fleet —
    # {"down", "total", "by_hosting"}.
    signals: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class AiRun(Base):
    """Один прогон проверки доступа к ИИ: по всем хостам подписки или по выборке.

    Прогон длится минуты, поэтому заводится сразу со статусом running и
    дописывается по мере хода: интерфейс показывает прогресс по hosts_done.

    Полный прогон (scope=full) — опора матрицы, расписания и сводки в Телеграм.
    Выборочный (partial) обновляет только свои хосты и ни на что из этого не
    влияет.
    """

    __tablename__ = "ai_runs"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # running | done | failed | aborted (рагент перезапустился) | cancelled (отменил человек)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    trigger: Mapped[str] = mapped_column(String(16), nullable=False)  # schedule | manual
    scope: Mapped[str] = mapped_column(
        String(16), nullable=False, default="full", server_default="full"
    )   # full | partial
    hosts_total: Mapped[int | None] = mapped_column(Integer, nullable=True)
    hosts_done: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Отличия от прошлого удачного полного прогона — считаются один раз, в конце.
    changes: Mapped[list | None] = mapped_column(JSON, nullable=True)
    # Хосты прогона и что с каждым: [{"key", "host", "pos", "state", "error"}].
    # Пишется по ходу; у прогонов до миграции 014 пусто.
    targets: Mapped[list | None] = mapped_column(JSON, nullable=True)


class AiCheck(Base):
    """Результат одного хоста в прогоне: вердикт по каждому сервису и выходы.

    host — имя хоста как в подписке, host_key — оно же без служебных значков:
    по нему склеивается история, когда хост переименовывают.
    """

    __tablename__ = "ai_checks"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("ai_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    checked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    host: Mapped[str] = mapped_column(String(255), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)   # место в подписке
    host_key: Mapped[str] = mapped_column(String(255), nullable=False)
    tunnel: Mapped[str] = mapped_column(String(16), nullable=False)   # up | down | error
    exit_ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    exit_country: Mapped[str | None] = mapped_column(String(8), nullable=True)
    ai_ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    ai_country: Mapped[str | None] = mapped_column(String(8), nullable=True)
    ai_warp: Mapped[str | None] = mapped_column(String(8), nullable=True)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # {сервис: {"v": вердикт, "note": признак, "tries": попыток}}
    results: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
