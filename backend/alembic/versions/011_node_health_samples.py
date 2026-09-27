"""tcp retransmits, swap, oom kills and ping to Russia in node samples

Revision ID: 011
Revises: 010
Create Date: 2026-09-27

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "011"
down_revision: Union[str, None] = "010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Все поля nullable: агенты старше 0.1.21 их не присылают, а прошлая история
    # остаётся как есть — пустое место на графике, не выдуманный ноль.
    # Сегменты, ретрансмиты и OOM-kill хранятся дельтами за интервал сэмплера.
    op.add_column(
        "node_metric_samples",
        sa.Column("tcp_out_segs", sa.BigInteger(), nullable=True),
    )
    op.add_column(
        "node_metric_samples",
        sa.Column("tcp_retrans_segs", sa.BigInteger(), nullable=True),
    )
    op.add_column(
        "node_metric_samples",
        sa.Column("oom_kills", sa.Integer(), nullable=True),
    )
    op.add_column(
        "node_metric_samples",
        sa.Column("swap_percent", sa.Float(), nullable=True),
    )
    # Пинг и потери до якорей RIPE Atlas в Москве, замер с самой ноды.
    op.add_column(
        "node_metric_samples",
        sa.Column("ru_ping_ms", sa.Float(), nullable=True),
    )
    op.add_column(
        "node_metric_samples",
        sa.Column("ru_loss_pct", sa.Float(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("node_metric_samples", "ru_loss_pct")
    op.drop_column("node_metric_samples", "ru_ping_ms")
    op.drop_column("node_metric_samples", "swap_percent")
    op.drop_column("node_metric_samples", "oom_kills")
    op.drop_column("node_metric_samples", "tcp_retrans_segs")
    op.drop_column("node_metric_samples", "tcp_out_segs")
