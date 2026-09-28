"""tcp syn retransmits and data segments sent in node samples

Revision ID: 012
Revises: 011
Create Date: 2026-09-28

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "012"
down_revision: Union[str, None] = "011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Дельты TcpExt TCPSynRetrans и TCPOrigDataSent за интервал сэмплера. По ним
    # доля ретрансмитов считается без повторов SYN и на сегмент с данными.
    # Nullable: агенты старше 0.1.22 их не присылают, прошлая история остаётся на
    # старой формуле.
    op.add_column(
        "node_metric_samples",
        sa.Column("tcp_syn_retrans", sa.BigInteger(), nullable=True),
    )
    op.add_column(
        "node_metric_samples",
        sa.Column("tcp_orig_data_sent", sa.BigInteger(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("node_metric_samples", "tcp_orig_data_sent")
    op.drop_column("node_metric_samples", "tcp_syn_retrans")
