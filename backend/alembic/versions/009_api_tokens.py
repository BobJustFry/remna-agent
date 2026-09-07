"""api tokens for scripts and integrations

Revision ID: 009
Revises: 008
Create Date: 2026-09-07

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "009"
down_revision: Union[str, None] = "008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Токен целиком в базе не лежит — только его SHA-256. Поэтому колонка
    # уникальна и индексирована: по ней ищем на каждом Bearer-запросе.
    op.create_table(
        "api_tokens",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("prefix", sa.String(length=16), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_api_tokens_token_hash", "api_tokens", ["token_hash"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_api_tokens_token_hash", table_name="api_tokens")
    op.drop_table("api_tokens")
