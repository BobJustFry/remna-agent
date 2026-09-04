"""per-node online users and capacity samples

Revision ID: 008
Revises: 007
Create Date: 2026-09-04

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "008"
down_revision: Union[str, None] = "007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Users the Xray core reports online at that moment. Null when the Remnawave
    # panel was unreachable on that tick — a gap in the chart is honest, a zero
    # would invent an outage that never happened.
    op.add_column(
        "node_metric_samples",
        sa.Column("users_online", sa.Integer(), nullable=True),
    )
    # Node capacity as computed then. Stored per sample rather than read live,
    # because it changes with the config: an old chart must be compared against
    # the ceiling that existed at the time, not today's.
    op.add_column(
        "node_metric_samples",
        sa.Column("capacity", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("node_metric_samples", "capacity")
    op.drop_column("node_metric_samples", "users_online")
