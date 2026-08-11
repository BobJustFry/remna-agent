"""create nodes table

Revision ID: 001
Revises:
Create Date: 2026-08-11

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "nodes",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("host", sa.String(length=255), nullable=False),
        sa.Column("ssh_port", sa.Integer(), nullable=False, server_default="22"),
        sa.Column("ssh_user", sa.String(length=128), nullable=False, server_default="root"),
        sa.Column("auth_type", sa.String(length=32), nullable=False),
        sa.Column("password_enc", sa.Text(), nullable=True),
        sa.Column("private_key_enc", sa.Text(), nullable=True),
        sa.Column("provider", sa.String(length=128), nullable=True),
        sa.Column("country_code", sa.String(length=8), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_nodes_name", "nodes", ["name"])
    op.create_index("ix_nodes_host", "nodes", ["host"])


def downgrade() -> None:
    op.drop_index("ix_nodes_host", table_name="nodes")
    op.drop_index("ix_nodes_name", table_name="nodes")
    op.drop_table("nodes")
