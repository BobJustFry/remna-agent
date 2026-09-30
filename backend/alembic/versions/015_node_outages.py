"""node outages: alarm state and the down/up journal of the node watcher

Revision ID: 015
Revises: 014
Create Date: 2026-09-30

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "015"
down_revision: Union[str, None] = "014"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "node_outages",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("kind", sa.String(length=8), nullable=False, server_default="node"),
        # Нода может быть удалена, а запись о её простое остаётся в журнале.
        sa.Column(
            "node_id",
            sa.Uuid(),
            sa.ForeignKey("nodes.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("node_name", sa.String(length=255), nullable=False),
        sa.Column("host", sa.String(length=255), nullable=True),
        sa.Column("hosting", sa.String(length=128), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("alerted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolution", sa.String(length=16), nullable=True),
        sa.Column("notified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reminded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("signals", sa.JSON(), nullable=True),
    )
    op.create_index("ix_node_outages_node_id", "node_outages", ["node_id"])
    # Журнал за N дней и чистка по возрасту — обе выборки по времени начала.
    op.create_index("ix_node_outages_started_at", "node_outages", ["started_at"])


def downgrade() -> None:
    op.drop_index("ix_node_outages_started_at", table_name="node_outages")
    op.drop_index("ix_node_outages_node_id", table_name="node_outages")
    op.drop_table("node_outages")
    # Состояние следилки ссылается на строки журнала, которого больше нет.
    # Настройки (node_watch_config) оставляем: старому коду они не мешают,
    # а при повторном подъёме не придётся вбивать их заново.
    op.execute("DELETE FROM app_settings WHERE key = 'node_watch_state'")
