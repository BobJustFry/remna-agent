"""history of Yandex Cloud balance and VM status polls

Revision ID: 016
Revises: 015
Create Date: 2026-10-03

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "016"
down_revision: Union[str, None] = "015"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Как у sub_checks: читаем последние и чистим старые — только по времени.
    op.create_table(
        "yc_checks",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "checked_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("ok", sa.Boolean(), nullable=False),
        sa.Column("balance", sa.Float(), nullable=True),
        sa.Column("currency", sa.String(length=8), nullable=True),
        sa.Column("account_active", sa.Boolean(), nullable=True),
        sa.Column("vms", sa.JSON(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error", sa.Text(), nullable=True),
    )
    op.create_index("ix_yc_checks_checked_at", "yc_checks", ["checked_at"])


def downgrade() -> None:
    op.drop_index("ix_yc_checks_checked_at", table_name="yc_checks")
    op.drop_table("yc_checks")
