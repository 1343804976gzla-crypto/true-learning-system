"""add audit change log

Revision ID: 20260405_000002
Revises: 20260404_000001
Create Date: 2026-04-05 00:00:02
"""
from __future__ import annotations

from alembic import op

from database.audit import AUDIT_CHANGE_LOG_TABLE, AUDIT_METADATA

# revision identifiers, used by Alembic.
revision = "20260405_000002"
down_revision = "20260404_000001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    AUDIT_METADATA.create_all(bind=bind, tables=[AUDIT_CHANGE_LOG_TABLE], checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    AUDIT_CHANGE_LOG_TABLE.drop(bind=bind, checkfirst=True)
