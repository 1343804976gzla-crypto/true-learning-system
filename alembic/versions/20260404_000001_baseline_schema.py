"""baseline schema

Revision ID: 20260404_000001
Revises:
Create Date: 2026-04-04 00:00:01
"""
from __future__ import annotations

from alembic import op

from database.alembic_support import get_target_metadata

# revision identifiers, used by Alembic.
revision = "20260404_000001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    for metadata in get_target_metadata():
        metadata.create_all(bind=bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for metadata in reversed(tuple(get_target_metadata())):
        metadata.drop_all(bind=bind, checkfirst=True)
