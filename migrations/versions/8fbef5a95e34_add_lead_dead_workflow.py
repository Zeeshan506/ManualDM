"""Add lead dead workflow fields

Revision ID: 8fbef5a95e34
Revises: f2d8a7c1b9e4
Create Date: 2026-03-04 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "8fbef5a95e34"
down_revision: Union[str, None] = "f2d8a7c1b9e4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("leads", sa.Column("dead_requested", sa.Boolean(), nullable=False, server_default=sa.text("false")))
    op.add_column("leads", sa.Column("dead_requested_by_user_id", sa.Integer(), nullable=True))
    op.add_column("leads", sa.Column("dead_requested_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("leads", sa.Column("dead_marked_by_user_id", sa.Integer(), nullable=True))
    op.add_column("leads", sa.Column("dead_marked_at", sa.DateTime(timezone=True), nullable=True))

    op.create_foreign_key(
        "fk_leads_dead_requested_by_user_id_users",
        "leads",
        "users",
        ["dead_requested_by_user_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_leads_dead_marked_by_user_id_users",
        "leads",
        "users",
        ["dead_marked_by_user_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.create_index("ix_leads_dead_requested", "leads", ["dead_requested"], unique=False)
    op.create_index("ix_leads_dead_requested_by_user_id", "leads", ["dead_requested_by_user_id"], unique=False)
    op.create_index("ix_leads_dead_marked_by_user_id", "leads", ["dead_marked_by_user_id"], unique=False)

    op.alter_column("leads", "dead_requested", server_default=None)


def downgrade() -> None:
    op.drop_index("ix_leads_dead_marked_by_user_id", table_name="leads")
    op.drop_index("ix_leads_dead_requested_by_user_id", table_name="leads")
    op.drop_index("ix_leads_dead_requested", table_name="leads")

    op.drop_constraint("fk_leads_dead_marked_by_user_id_users", "leads", type_="foreignkey")
    op.drop_constraint("fk_leads_dead_requested_by_user_id_users", "leads", type_="foreignkey")

    op.drop_column("leads", "dead_marked_at")
    op.drop_column("leads", "dead_marked_by_user_id")
    op.drop_column("leads", "dead_requested_at")
    op.drop_column("leads", "dead_requested_by_user_id")
    op.drop_column("leads", "dead_requested")
