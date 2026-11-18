"""fare_checks.route_estimated

Revision ID: k8f9a0b1c2d3
Revises: j7e8f9a0b1c2
Create Date: 2026-11-18 00:10:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'k8f9a0b1c2d3'
down_revision: Union[str, Sequence[str], None] = 'j7e8f9a0b1c2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('fare_checks', schema=None) as batch_op:
        batch_op.add_column(sa.Column('route_estimated', sa.Boolean(), nullable=False, server_default='0'))


def downgrade() -> None:
    with op.batch_alter_table('fare_checks', schema=None) as batch_op:
        batch_op.drop_column('route_estimated')
