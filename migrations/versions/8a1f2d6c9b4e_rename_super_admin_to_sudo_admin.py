"""Rename super_admin role value to sudo_admin

Revision ID: 8a1f2d6c9b4e
Revises: 503c6bb65dd2
Create Date: 2026-02-25 14:10:00.000000

"""

from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "8a1f2d6c9b4e"
down_revision: Union[str, None] = "503c6bb65dd2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM pg_type t
                JOIN pg_enum e ON t.oid = e.enumtypid
                WHERE t.typname = 'user_role'
                  AND e.enumlabel = 'super_admin'
            ) THEN
                ALTER TYPE user_role RENAME VALUE 'super_admin' TO 'sudo_admin';
            END IF;
        END$$;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM pg_type t
                JOIN pg_enum e ON t.oid = e.enumtypid
                WHERE t.typname = 'user_role'
                  AND e.enumlabel = 'sudo_admin'
            ) THEN
                ALTER TYPE user_role RENAME VALUE 'sudo_admin' TO 'super_admin';
            END IF;
        END$$;
        """
    )
