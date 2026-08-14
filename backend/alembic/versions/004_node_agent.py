"""node agent fields

Revision ID: 004
Revises: 003
Create Date: 2026-08-11

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "004"
down_revision: Union[str, None] = "003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("nodes", sa.Column("agent_token_enc", sa.Text(), nullable=True))
    op.add_column(
        "nodes",
        sa.Column("agent_port", sa.Integer(), nullable=False, server_default="7422"),
    )


def downgrade() -> None:
    op.drop_column("nodes", "agent_port")
    op.drop_column("nodes", "agent_token_enc")
