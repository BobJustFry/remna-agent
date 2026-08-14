"""hosting website url and favicon

Revision ID: 003
Revises: 002
Create Date: 2026-08-11

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "003"
down_revision: Union[str, None] = "002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("hostings", sa.Column("website_url", sa.String(length=512), nullable=True))
    op.add_column("hostings", sa.Column("favicon_data", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("hostings", "favicon_data")
    op.drop_column("hostings", "website_url")
