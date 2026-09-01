"""node metric samples (ping / cpu / disk history)

Revision ID: 006
Revises: 005
Create Date: 2026-09-01

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "006"
down_revision: Union[str, None] = "005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "node_metric_samples",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("node_id", sa.Uuid(), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("online", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("ping_ms", sa.Float(), nullable=True),
        sa.Column("cpu_percent", sa.Float(), nullable=True),
        sa.Column("mem_percent", sa.Float(), nullable=True),
        sa.Column("disk_percent", sa.Float(), nullable=True),
        sa.ForeignKeyConstraint(["node_id"], ["nodes.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_node_metric_samples_node_recorded",
        "node_metric_samples",
        ["node_id", "recorded_at"],
    )
    op.create_index(
        "ix_node_metric_samples_recorded_at",
        "node_metric_samples",
        ["recorded_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_node_metric_samples_recorded_at", table_name="node_metric_samples")
    op.drop_index("ix_node_metric_samples_node_recorded", table_name="node_metric_samples")
    op.drop_table("node_metric_samples")
