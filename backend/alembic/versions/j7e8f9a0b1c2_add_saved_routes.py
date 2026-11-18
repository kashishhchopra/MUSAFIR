"""add saved_routes

Revision ID: j7e8f9a0b1c2
Revises: a84a1d67676e
Create Date: 2026-11-18 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'j7e8f9a0b1c2'
down_revision: Union[str, Sequence[str], None] = 'a84a1d67676e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('saved_routes',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('tourist_id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=120), nullable=False),
    sa.Column('mode', sa.String(length=20), nullable=False),
    sa.Column('origin_name', sa.String(length=200), nullable=False),
    sa.Column('origin_lat', sa.Float(), nullable=False),
    sa.Column('origin_lng', sa.Float(), nullable=False),
    sa.Column('dest_name', sa.String(length=200), nullable=False),
    sa.Column('dest_lat', sa.Float(), nullable=False),
    sa.Column('dest_lng', sa.Float(), nullable=False),
    sa.Column('stops_json', sa.Text(), nullable=False),
    sa.Column('distance_km', sa.Float(), nullable=True),
    sa.Column('duration_min', sa.Float(), nullable=True),
    sa.Column('geometry_json', sa.Text(), nullable=False),
    sa.Column('risk_level', sa.String(length=20), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('last_opened_at', sa.DateTime(), nullable=True),
    sa.ForeignKeyConstraint(['tourist_id'], ['tourists.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('saved_routes', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_saved_routes_tourist_id'), ['tourist_id'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('saved_routes', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_saved_routes_tourist_id'))
    op.drop_table('saved_routes')
