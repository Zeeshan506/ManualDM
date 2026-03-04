"""Add notification events table

Revision ID: f2d8a7c1b9e4
Revises: c9180a6b3d41
Create Date: 2026-03-04 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "f2d8a7c1b9e4"
down_revision: Union[str, None] = "c9180a6b3d41"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "notification_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(), nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column("lead_id", sa.Integer(), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["lead_id"], ["leads.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_notification_events_event_type", "notification_events", ["event_type"], unique=False)
    op.create_index("ix_notification_events_lead_id", "notification_events", ["lead_id"], unique=False)
    op.create_index("ix_notification_events_created_at", "notification_events", ["created_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_notification_events_created_at", table_name="notification_events")
    op.drop_index("ix_notification_events_lead_id", table_name="notification_events")
    op.drop_index("ix_notification_events_event_type", table_name="notification_events")
    op.drop_table("notification_events")
