"""hostings dictionary, replace provider

Revision ID: 002
Revises: 001
Create Date: 2026-08-11

"""
from typing import Sequence, Union
import uuid

from alembic import op
import sqlalchemy as sa

revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "hostings",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", name="uq_hostings_name"),
    )
    op.create_index("ix_hostings_name", "hostings", ["name"])

    op.add_column("nodes", sa.Column("hosting_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "fk_nodes_hosting_id",
        "nodes",
        "hostings",
        ["hosting_id"],
        ["id"],
        ondelete="SET NULL",
    )

    conn = op.get_bind()
    rows = conn.execute(sa.text("SELECT DISTINCT provider FROM nodes WHERE provider IS NOT NULL AND provider <> ''")).fetchall()
    name_to_id: dict[str, uuid.UUID] = {}
    for (provider_name,) in rows:
        hid = uuid.uuid4()
        name_to_id[provider_name] = hid
        conn.execute(
            sa.text("INSERT INTO hostings (id, name) VALUES (:id, :name)"),
            {"id": str(hid), "name": provider_name},
        )
    for provider_name, hid in name_to_id.items():
        conn.execute(
            sa.text("UPDATE nodes SET hosting_id = :hid WHERE provider = :name"),
            {"hid": str(hid), "name": provider_name},
        )

    op.drop_column("nodes", "provider")


def downgrade() -> None:
    op.add_column("nodes", sa.Column("provider", sa.String(length=128), nullable=True))
    conn = op.get_bind()
    conn.execute(
        sa.text(
            """
            UPDATE nodes AS n
            SET provider = h.name
            FROM hostings AS h
            WHERE n.hosting_id = h.id
            """
        )
    )
    op.drop_constraint("fk_nodes_hosting_id", "nodes", type_="foreignkey")
    op.drop_column("nodes", "hosting_id")
    op.drop_index("ix_hostings_name", table_name="hostings")
    op.drop_table("hostings")
