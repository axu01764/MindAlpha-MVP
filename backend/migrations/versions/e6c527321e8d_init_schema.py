"""init schema

Revision ID: e6c527321e8d
Revises: 
Create Date: 2026-03-05 11:24:45.953671

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e6c527321e8d'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "action_logs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=True),
        sa.Column("action_type", sa.String(), nullable=True),
        sa.Column("market_snapshot", sa.String(), nullable=True),
        sa.Column("is_violation", sa.Boolean(), nullable=True),
        sa.Column("was_blocked", sa.Boolean(), nullable=True),
        sa.Column("timestamp", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_action_logs_id"), "action_logs", ["id"], unique=False)
    op.create_index(op.f("ix_action_logs_user_id"), "action_logs", ["user_id"], unique=False)

    op.create_table(
        "strategy_rules",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=True),
        sa.Column("raw_text", sa.String(), nullable=True),
        sa.Column("parsed_json", sa.String(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_strategy_rules_id"), "strategy_rules", ["id"], unique=False)
    op.create_index(op.f("ix_strategy_rules_user_id"), "strategy_rules", ["user_id"], unique=False)

    op.create_table(
        "user_psych_profiles",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=True),
        sa.Column("fomo_index", sa.Float(), nullable=True),
        sa.Column("discipline_score", sa.Float(), nullable=True),
        sa.Column("saved_capital", sa.Float(), nullable=True),
        sa.Column("last_updated", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_user_psych_profiles_id"), "user_psych_profiles", ["id"], unique=False)
    op.create_index(
        op.f("ix_user_psych_profiles_user_id"), "user_psych_profiles", ["user_id"], unique=True
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_user_psych_profiles_user_id"), table_name="user_psych_profiles")
    op.drop_index(op.f("ix_user_psych_profiles_id"), table_name="user_psych_profiles")
    op.drop_table("user_psych_profiles")

    op.drop_index(op.f("ix_strategy_rules_user_id"), table_name="strategy_rules")
    op.drop_index(op.f("ix_strategy_rules_id"), table_name="strategy_rules")
    op.drop_table("strategy_rules")

    op.drop_index(op.f("ix_action_logs_user_id"), table_name="action_logs")
    op.drop_index(op.f("ix_action_logs_id"), table_name="action_logs")
    op.drop_table("action_logs")
