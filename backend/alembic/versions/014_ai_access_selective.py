"""selective AI-access runs: run scope and per-host queue state

Revision ID: 014
Revises: 013
Create Date: 2026-09-30

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "014"
down_revision: Union[str, None] = "013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Все прежние прогоны шли по всему парку — им достаётся full по умолчанию.
    op.add_column(
        "ai_runs",
        sa.Column("scope", sa.String(length=16), nullable=False, server_default="full"),
    )
    # Список хостов прогона с их состоянием. У старых прогонов пусто: их состав
    # читается из ai_checks, как раньше.
    op.add_column("ai_runs", sa.Column("targets", sa.JSON(), nullable=True))


def downgrade() -> None:
    # Без scope выборочный прогон выглядел бы полным: подменил бы собой матрицу
    # и сдвинул расписание. Убираем такие прогоны вместе с их строками.
    op.execute("DELETE FROM ai_runs WHERE scope <> 'full'")
    # Статуса cancelled старый код не знает — для него это прерванный прогон.
    op.execute("UPDATE ai_runs SET status = 'aborted' WHERE status = 'cancelled'")
    op.drop_column("ai_runs", "targets")
    op.drop_column("ai_runs", "scope")
