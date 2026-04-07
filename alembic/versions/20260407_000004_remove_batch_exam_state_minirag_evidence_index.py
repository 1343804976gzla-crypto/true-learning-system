"""remove batch exam state minirag evidence index

Revision ID: 20260407_000004
Revises: 20260407_000003
Create Date: 2026-04-07 00:00:04
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20260407_000004"
down_revision = "20260407_000003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("batch_exam_states") as batch_op:
        batch_op.drop_column("minirag_evidence_index")


def downgrade() -> None:
    with op.batch_alter_table("batch_exam_states") as batch_op:
        batch_op.add_column(sa.Column("minirag_evidence_index", sa.JSON(), nullable=True))
