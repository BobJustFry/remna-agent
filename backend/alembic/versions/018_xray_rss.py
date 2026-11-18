"""xray core rss in node samples

Revision ID: 018
Revises: 017
Create Date: 2026-10-17

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "018"
down_revision: Union[str, None] = "017"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # RSS процесса rw-core, МБ. Nullable: агенты старше 0.1.23 его не присылают.
    op.add_column("node_metric_samples", sa.Column("xray_rss_mb", sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column("node_metric_samples", "xray_rss_mb")
