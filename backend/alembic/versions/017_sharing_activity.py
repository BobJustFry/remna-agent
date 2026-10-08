"""hourly presence profile behind the sharing night-pause signal

Revision ID: 017
Revises: 016
Create Date: 2026-10-08

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "017"
down_revision: Union[str, None] = "016"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Строк немного: онлайн × 24 часа × окно хранения, чистится по возрасту.
    op.create_table(
        "sharing_activity",
        sa.Column("user_id", sa.Integer(), primary_key=True),
        sa.Column("bucket", sa.DateTime(timezone=True), primary_key=True),
        sa.Column("seen", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_index("ix_sharing_activity_bucket", "sharing_activity", ["bucket"])


def downgrade() -> None:
    op.drop_index("ix_sharing_activity_bucket", table_name="sharing_activity")
    op.drop_table("sharing_activity")
