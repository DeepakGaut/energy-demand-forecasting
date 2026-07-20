"""add total_cores to hardware_specs

Revision ID: b7c1d2e3f4a5
Revises: 764000c99946
Create Date: 2026-07-18 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b7c1d2e3f4a5'
down_revision: Union[str, Sequence[str], None] = '764000c99946'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Add with a temporary server_default so any pre-existing rows are valid,
    # then drop the default so future inserts must supply a real core count
    # (matching the NOT NULL, no-default model definition).
    op.add_column(
        'hardware_specs',
        sa.Column('total_cores', sa.Integer(), nullable=False, server_default='1'),
    )
    op.alter_column('hardware_specs', 'total_cores', server_default=None)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('hardware_specs', 'total_cores')
