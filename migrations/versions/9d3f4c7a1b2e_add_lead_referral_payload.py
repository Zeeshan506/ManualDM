"""add lead referral payload

Revision ID: 9d3f4c7a1b2e
Revises: 8a1f2d6c9b4e
Create Date: 2026-02-26 18:20:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "9d3f4c7a1b2e"
down_revision: Union[str, None] = "8a1f2d6c9b4e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("leads", sa.Column("referral_payload", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("leads", "referral_payload")
