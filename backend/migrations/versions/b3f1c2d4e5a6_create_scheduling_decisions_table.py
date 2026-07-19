"""create scheduling_decisions table

Revision ID: b3f1c2d4e5a6
Revises: b7c1d2e3f4a5
Create Date: 2026-07-19 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b3f1c2d4e5a6'
down_revision: Union[str, Sequence[str], None] = 'b7c1d2e3f4a5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'scheduling_decisions',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('job_id', sa.String(length=64), nullable=False),
        sa.Column('submitted_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('default_region', sa.String(length=10), nullable=False),
        sa.Column('recommended_region', sa.String(length=10), nullable=False),
        sa.Column('recommended_time', sa.DateTime(timezone=True), nullable=True),
        sa.Column('predicted_saving_gco2e', sa.Float(), nullable=False),
        sa.Column('urgency_weight', sa.Float(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'ix_scheduling_decisions_job_id',
        'scheduling_decisions',
        ['job_id'],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_scheduling_decisions_job_id', table_name='scheduling_decisions')
    op.drop_table('scheduling_decisions')
