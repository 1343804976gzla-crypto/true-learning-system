"""add batch exam state restore fields

Revision ID: 20260407_000003
Revises: 20260405_000002
Create Date: 2026-04-07 00:00:03
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20260407_000003"
down_revision = "20260405_000002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("batch_exam_states", sa.Column("paper_title", sa.String(), nullable=True))
    op.add_column("batch_exam_states", sa.Column("summary", sa.JSON(), nullable=True))
    op.add_column("batch_exam_states", sa.Column("minirag_evidence_index", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("batch_exam_states", "minirag_evidence_index")
    op.drop_column("batch_exam_states", "summary")
    op.drop_column("batch_exam_states", "paper_title")
