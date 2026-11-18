"""fleet load report log

Revision ID: 020
Revises: 019
Create Date: 2026-10-23

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "020"
down_revision: Union[str, None] = "019"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # History of reports sent to Telegram (slot, kind, text). The per-node samples
    # the report is computed from live in node_metric_samples (30 s, 180 days).
    op.create_table(
        "fleet_report_log",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("slot", sa.String(16), nullable=False),
        sa.Column("kind", sa.String(8), nullable=False),
        sa.Column("delivered", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("text", sa.Text(), nullable=False),
    )
    op.create_index("ix_fleet_report_log_sent_at", "fleet_report_log", ["sent_at"])


def downgrade() -> None:
    op.drop_index("ix_fleet_report_log_sent_at", table_name="fleet_report_log")
    op.drop_table("fleet_report_log")
