"""history of AI-service access checks through every subscription host

Revision ID: 013
Revises: 012
Create Date: 2026-09-30

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "013"
down_revision: Union[str, None] = "012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "ai_runs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("trigger", sa.String(length=16), nullable=False),
        sa.Column("hosts_total", sa.Integer(), nullable=True),
        sa.Column("hosts_done", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("changes", sa.JSON(), nullable=True),
    )
    # Последний прогон и чистка по возрасту — обе выборки по времени старта.
    op.create_index("ix_ai_runs_started_at", "ai_runs", ["started_at"])

    # Строки хостов удаляются вместе с прогоном: чистим только ai_runs.
    op.create_table(
        "ai_checks",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "run_id",
            sa.Uuid(),
            sa.ForeignKey("ai_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "checked_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("host", sa.String(length=255), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("host_key", sa.String(length=255), nullable=False),
        sa.Column("tunnel", sa.String(length=16), nullable=False),
        sa.Column("exit_ip", sa.String(length=64), nullable=True),
        sa.Column("exit_country", sa.String(length=8), nullable=True),
        sa.Column("ai_ip", sa.String(length=64), nullable=True),
        sa.Column("ai_country", sa.String(length=8), nullable=True),
        sa.Column("ai_warp", sa.String(length=8), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("results", sa.JSON(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
    )
    op.create_index("ix_ai_checks_run_id", "ai_checks", ["run_id"])
    # История одного хоста за N дней.
    op.create_index("ix_ai_checks_host_key_checked_at", "ai_checks", ["host_key", "checked_at"])


def downgrade() -> None:
    op.drop_index("ix_ai_checks_host_key_checked_at", table_name="ai_checks")
    op.drop_index("ix_ai_checks_run_id", table_name="ai_checks")
    op.drop_table("ai_checks")
    op.drop_index("ix_ai_runs_started_at", table_name="ai_runs")
    op.drop_table("ai_runs")
