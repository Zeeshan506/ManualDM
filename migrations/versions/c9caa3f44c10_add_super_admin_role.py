"""add_super_admin_role

Revision ID: c9caa3f44c10
Revises: 1e8242b85a5a
Create Date: 2026-02-25 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "c9caa3f44c10"
down_revision: Union[str, None] = "1e8242b85a5a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("ALTER TYPE user_role ADD VALUE IF NOT EXISTS 'super_admin'")


def downgrade() -> None:
    # PostgreSQL enum value removal is destructive and intentionally left as no-op.
    pass
