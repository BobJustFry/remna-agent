"""geo cache for client addresses used by the sharing report

Revision ID: 019
Revises: 018
Create Date: 2026-10-18

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "019"
down_revision: Union[str, None] = "018"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "ip_geo_cache",
        sa.Column("ip", sa.String(45), primary_key=True),
        sa.Column("asn", sa.Integer(), nullable=True),
        sa.Column("isp", sa.String(200), nullable=True),
        sa.Column("city", sa.String(120), nullable=True),
        sa.Column("region", sa.String(120), nullable=True),
        sa.Column("country", sa.String(8), nullable=True),
        sa.Column("lat", sa.Float(), nullable=True),
        sa.Column("lon", sa.Float(), nullable=True),
        sa.Column("mobile", sa.Boolean(), nullable=True),
        sa.Column("hosting", sa.Boolean(), nullable=True),
        sa.Column("ok", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("fetched_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("ip_geo_cache")
