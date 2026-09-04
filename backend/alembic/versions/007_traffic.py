"""hosting channel limit + per-node traffic rate samples

Revision ID: 007
Revises: 006
Create Date: 2026-09-03

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "007"
down_revision: Union[str, None] = "006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Channel the hosting sells, in Mbit/s. Null = unknown, no gauge is drawn.
    op.add_column(
        "hostings",
        sa.Column("bandwidth_mbps", sa.Integer(), nullable=True),
    )
    # Throughput on the node's default-route interface, bits per second.
    op.add_column(
        "node_metric_samples",
        sa.Column("net_rx_bps", sa.BigInteger(), nullable=True),
    )
    op.add_column(
        "node_metric_samples",
        sa.Column("net_tx_bps", sa.BigInteger(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("node_metric_samples", "net_tx_bps")
    op.drop_column("node_metric_samples", "net_rx_bps")
    op.drop_column("hostings", "bandwidth_mbps")
