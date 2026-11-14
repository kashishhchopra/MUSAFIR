"""add fall_events (fall & crash detection countdown)

Revision ID: e2f3a4b5c6d7
Revises: d1e2f3a4b5c6
Create Date: 2026-11-14 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'e2f3a4b5c6d7'
down_revision: Union[str, Sequence[str], None] = 'd1e2f3a4b5c6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('fall_events',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('tourist_id', sa.Integer(), nullable=False),
    sa.Column('client_event_id', sa.String(length=64), nullable=False),
    sa.Column('event_type', sa.String(length=16), nullable=False),
    sa.Column('source', sa.String(length=16), nullable=False),
    sa.Column('device_id', sa.String(length=64), nullable=True),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('detected_at', sa.DateTime(), nullable=False),
    sa.Column('countdown_deadline', sa.DateTime(), nullable=False),
    sa.Column('resolved_at', sa.DateTime(), nullable=True),
    sa.Column('resolution', sa.String(length=48), nullable=True),
    sa.Column('lat', sa.Float(), nullable=True),
    sa.Column('lng', sa.Float(), nullable=True),
    sa.Column('accuracy_m', sa.Float(), nullable=True),
    sa.Column('metadata_json', sa.Text(), nullable=False),
    sa.Column('incident_id', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['incident_id'], ['incidents.id'], ),
    sa.ForeignKeyConstraint(['tourist_id'], ['tourists.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('incident_id'),
    sa.UniqueConstraint('tourist_id', 'client_event_id', name='uq_fall_events_tourist_client_id')
    )
    op.create_index(op.f('ix_fall_events_tourist_id'), 'fall_events', ['tourist_id'], unique=False)
    op.create_index(op.f('ix_fall_events_status'), 'fall_events', ['status'], unique=False)
    op.create_index(op.f('ix_fall_events_countdown_deadline'), 'fall_events', ['countdown_deadline'], unique=False)
    op.create_index(op.f('ix_fall_events_created_at'), 'fall_events', ['created_at'], unique=False)
    op.create_index('uq_fall_events_one_pending', 'fall_events', ['tourist_id'], unique=True,
                    sqlite_where=sa.text("status = 'pending'"),
                    postgresql_where=sa.text("status = 'pending'"))


def downgrade() -> None:
    op.drop_index('uq_fall_events_one_pending', table_name='fall_events',
                  sqlite_where=sa.text("status = 'pending'"),
                  postgresql_where=sa.text("status = 'pending'"))
    op.drop_index(op.f('ix_fall_events_created_at'), table_name='fall_events')
    op.drop_index(op.f('ix_fall_events_countdown_deadline'), table_name='fall_events')
    op.drop_index(op.f('ix_fall_events_status'), table_name='fall_events')
    op.drop_index(op.f('ix_fall_events_tourist_id'), table_name='fall_events')
    op.drop_table('fall_events')
