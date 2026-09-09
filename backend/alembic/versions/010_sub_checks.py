"""history of subscription probes

Revision ID: 010
Revises: 009
Create Date: 2026-09-09

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "010"
down_revision: Union[str, None] = "009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Индекс по времени: показываем последние проверки и чистим старые — оба
    # запроса ходят только по этой колонке.
    op.create_table(
        "sub_checks",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "checked_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("via", sa.String(length=16), nullable=False),
        sa.Column("ok", sa.Boolean(), nullable=False),
        sa.Column("http_status", sa.Integer(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("hosts", sa.Integer(), nullable=True),
        sa.Column("cert_days", sa.Integer(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
    )
    op.create_index("ix_sub_checks_checked_at", "sub_checks", ["checked_at"])


def downgrade() -> None:
    op.drop_index("ix_sub_checks_checked_at", table_name="sub_checks")
    op.drop_table("sub_checks")
