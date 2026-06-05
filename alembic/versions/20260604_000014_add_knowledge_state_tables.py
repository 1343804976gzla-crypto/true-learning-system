"""add knowledge state tables

Revision ID: 20260604_000014
Revises: 20260407_000004
Create Date: 2026-06-04
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260604_000014"
down_revision = "20260407_000004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("question_records", sa.Column("primary_key_point", sa.String(), nullable=True))
    op.add_column("wrong_answers_v2", sa.Column("scope_key", sa.String(), nullable=True))
    op.create_index("ix_wrong_answers_v2_scope_key", "wrong_answers_v2", ["scope_key"])

    op.create_table(
        "knowledge_state_profiles",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("user_id", sa.String(), nullable=True),
        sa.Column("device_id", sa.String(), nullable=True),
        sa.Column("scope_key", sa.String(), nullable=False),
        sa.Column("knowledge_point", sa.String(), nullable=False),
        sa.Column("current_state", sa.String(), nullable=False),
        sa.Column("state_confidence", sa.String(), nullable=False, server_default="low"),
        sa.Column("stability_score", sa.Float(), nullable=True, server_default="0.0"),
        sa.Column("calibration_score", sa.Float(), nullable=True, server_default="0.0"),
        sa.Column("last_transition", sa.String(), nullable=True),
        sa.Column("last_session_id", sa.String(), nullable=True),
        sa.Column("evidence_snapshot", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("scope_key", "knowledge_point", name="uq_knowledge_state_scope_point"),
    )
    op.create_index("ix_knowledge_state_profiles_id", "knowledge_state_profiles", ["id"])
    op.create_index("ix_knowledge_state_profiles_user_id", "knowledge_state_profiles", ["user_id"])
    op.create_index("ix_knowledge_state_profiles_device_id", "knowledge_state_profiles", ["device_id"])
    op.create_index("ix_knowledge_state_profiles_scope_key", "knowledge_state_profiles", ["scope_key"])
    op.create_index("ix_knowledge_state_profiles_knowledge_point", "knowledge_state_profiles", ["knowledge_point"])
    op.create_index("ix_knowledge_state_profiles_current_state", "knowledge_state_profiles", ["current_state"])
    op.create_index("ix_knowledge_state_profiles_last_transition", "knowledge_state_profiles", ["last_transition"])
    op.create_index("ix_knowledge_state_profiles_last_session_id", "knowledge_state_profiles", ["last_session_id"])
    op.create_index("ix_knowledge_state_profiles_scope_state", "knowledge_state_profiles", ["scope_key", "current_state"])

    op.create_table(
        "knowledge_state_events",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column(
            "profile_id",
            sa.Integer(),
            sa.ForeignKey("knowledge_state_profiles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("user_id", sa.String(), nullable=True),
        sa.Column("device_id", sa.String(), nullable=True),
        sa.Column("scope_key", sa.String(), nullable=False),
        sa.Column("session_id", sa.String(), nullable=False),
        sa.Column("knowledge_point", sa.String(), nullable=False),
        sa.Column("previous_state", sa.String(), nullable=True),
        sa.Column("current_state", sa.String(), nullable=False),
        sa.Column("transition", sa.String(), nullable=False),
        sa.Column("state_confidence", sa.String(), nullable=False, server_default="low"),
        sa.Column("evidence_packet", sa.JSON(), nullable=True),
        sa.Column("llm_analysis", sa.JSON(), nullable=True),
        sa.Column("guardrail_flags", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint(
            "scope_key",
            "session_id",
            "knowledge_point",
            name="uq_knowledge_state_events_scope_session_point",
        ),
    )
    op.create_index("ix_knowledge_state_events_id", "knowledge_state_events", ["id"])
    op.create_index("ix_knowledge_state_events_profile_id", "knowledge_state_events", ["profile_id"])
    op.create_index("ix_knowledge_state_events_user_id", "knowledge_state_events", ["user_id"])
    op.create_index("ix_knowledge_state_events_device_id", "knowledge_state_events", ["device_id"])
    op.create_index("ix_knowledge_state_events_scope_key", "knowledge_state_events", ["scope_key"])
    op.create_index("ix_knowledge_state_events_session_id", "knowledge_state_events", ["session_id"])
    op.create_index("ix_knowledge_state_events_knowledge_point", "knowledge_state_events", ["knowledge_point"])
    op.create_index("ix_knowledge_state_events_current_state", "knowledge_state_events", ["current_state"])
    op.create_index("ix_knowledge_state_events_transition", "knowledge_state_events", ["transition"])
    op.create_index("ix_knowledge_state_events_created_at", "knowledge_state_events", ["created_at"])
    op.create_index("ix_knowledge_state_events_scope_session", "knowledge_state_events", ["scope_key", "session_id"])
    op.create_index("ix_knowledge_state_events_point_created", "knowledge_state_events", ["knowledge_point", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_knowledge_state_events_point_created", table_name="knowledge_state_events")
    op.drop_index("ix_knowledge_state_events_scope_session", table_name="knowledge_state_events")
    op.drop_table("knowledge_state_events")
    op.drop_index("ix_knowledge_state_profiles_scope_state", table_name="knowledge_state_profiles")
    op.drop_table("knowledge_state_profiles")
    op.drop_index("ix_wrong_answers_v2_scope_key", table_name="wrong_answers_v2")
    op.drop_column("wrong_answers_v2", "scope_key")
    op.drop_column("question_records", "primary_key_point")
