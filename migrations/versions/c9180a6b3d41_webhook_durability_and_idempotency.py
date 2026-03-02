"""Webhook durability and idempotency fields

Revision ID: c9180a6b3d41
Revises: a4b7fd2b1c31
Create Date: 2026-03-02 00:00:01.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "c9180a6b3d41"
down_revision: Union[str, None] = "a4b7fd2b1c31"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("webhook_events", sa.Column("idempotency_key", sa.String(), nullable=True))
    op.add_column("webhook_events", sa.Column("processing_state", sa.String(), nullable=False, server_default="received"))
    op.add_column("webhook_events", sa.Column("enqueue_status", sa.String(), nullable=False, server_default="pending"))
    op.add_column("webhook_events", sa.Column("enqueue_attempts", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("webhook_events", sa.Column("processing_attempts", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("webhook_events", sa.Column("last_error", sa.Text(), nullable=True))
    op.add_column("webhook_events", sa.Column("queued_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("webhook_events", sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("webhook_events", sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True))

    bind = op.get_bind()
    rows = bind.execute(sa.text("SELECT id FROM webhook_events"))
    for row in rows:
        bind.execute(
            sa.text("UPDATE webhook_events SET idempotency_key = :key WHERE id = :id"),
            {"key": f"legacy:{row.id}", "id": row.id},
        )

    op.alter_column("webhook_events", "idempotency_key", nullable=False)

    op.create_index("ix_webhook_events_idempotency_key", "webhook_events", ["idempotency_key"], unique=True)
    op.create_index("ix_webhook_events_processing_state", "webhook_events", ["processing_state"], unique=False)
    op.create_index("ix_webhook_events_enqueue_status", "webhook_events", ["enqueue_status"], unique=False)
    op.create_index("ix_webhook_enqueue_next_retry", "webhook_events", ["enqueue_status", "next_retry_at"], unique=False)

    op.alter_column("webhook_events", "processing_state", server_default=None)
    op.alter_column("webhook_events", "enqueue_status", server_default=None)
    op.alter_column("webhook_events", "enqueue_attempts", server_default=None)
    op.alter_column("webhook_events", "processing_attempts", server_default=None)


def downgrade() -> None:
    op.drop_index("ix_webhook_enqueue_next_retry", table_name="webhook_events")
    op.drop_index("ix_webhook_events_enqueue_status", table_name="webhook_events")
    op.drop_index("ix_webhook_events_processing_state", table_name="webhook_events")
    op.drop_index("ix_webhook_events_idempotency_key", table_name="webhook_events")

    op.drop_column("webhook_events", "next_retry_at")
    op.drop_column("webhook_events", "processed_at")
    op.drop_column("webhook_events", "queued_at")
    op.drop_column("webhook_events", "last_error")
    op.drop_column("webhook_events", "processing_attempts")
    op.drop_column("webhook_events", "enqueue_attempts")
    op.drop_column("webhook_events", "enqueue_status")
    op.drop_column("webhook_events", "processing_state")
    op.drop_column("webhook_events", "idempotency_key")
