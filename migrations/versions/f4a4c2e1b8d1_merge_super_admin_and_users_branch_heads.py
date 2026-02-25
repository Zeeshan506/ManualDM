"""merge super admin and users branch heads

Revision ID: f4a4c2e1b8d1
Revises: 65983730a7c2, c9caa3f44c10
Create Date: 2026-02-25 12:45:00.000000

"""
from typing import Sequence, Union


# revision identifiers, used by Alembic.
revision: str = "f4a4c2e1b8d1"
down_revision: Union[str, Sequence[str], None] = ("65983730a7c2", "c9caa3f44c10")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
