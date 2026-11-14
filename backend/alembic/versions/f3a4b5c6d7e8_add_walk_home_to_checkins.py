"""add Walk-Me-Home columns to checkins

Revision ID: f3a4b5c6d7e8
Revises: e2f3a4b5c6d7
Create Date: 2026-11-14 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'f3a4b5c6d7e8'
down_revision: Union[str, Sequence[str], None] = 'e2f3a4b5c6d7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_WHERE = "kind = 'walk_home' AND status IN ('planned','missed','escalated')"


def upgrade() -> None:
    with op.batch_alter_table('checkins') as b:
        b.add_column(sa.Column('kind', sa.String(length=16), nullable=False, server_default='outing'))
        b.add_column(sa.Column('started_at', sa.DateTime(), nullable=True))
        b.add_column(sa.Column('arrive_by', sa.DateTime(), nullable=True))
        b.add_column(sa.Column('check_interval_minutes', sa.Integer(), nullable=True))
        b.add_column(sa.Column('grace_minutes', sa.Integer(), nullable=True))
        b.add_column(sa.Column('last_checkin_at', sa.DateTime(), nullable=True))
        b.add_column(sa.Column('checkin_count', sa.Integer(), nullable=False, server_default='0'))
        b.add_column(sa.Column('missed_at', sa.DateTime(), nullable=True))
        b.add_column(sa.Column('ended_at', sa.DateTime(), nullable=True))
        b.add_column(sa.Column('extensions', sa.Integer(), nullable=False, server_default='0'))
        b.add_column(sa.Column('start_lat', sa.Float(), nullable=True))
        b.add_column(sa.Column('start_lng', sa.Float(), nullable=True))
        b.add_column(sa.Column('last_lat', sa.Float(), nullable=True))
        b.add_column(sa.Column('last_lng', sa.Float(), nullable=True))
        b.add_column(sa.Column('last_accuracy_m', sa.Float(), nullable=True))
        b.add_column(sa.Column('last_loc_at', sa.DateTime(), nullable=True))
        b.add_column(sa.Column('guardian_ids_json', sa.Text(), nullable=False, server_default='[]'))
        b.add_column(sa.Column('incident_id', sa.Integer(), nullable=True))
        b.create_foreign_key('fk_checkins_incident_id', 'incidents', ['incident_id'], ['id'])
        b.create_index('ix_checkins_kind', ['kind'])
        b.create_index('ix_checkins_incident_id', ['incident_id'])
    op.create_index(
        'uq_checkins_one_active_walk', 'checkins', ['tourist_id'], unique=True,
        sqlite_where=sa.text(_WHERE), postgresql_where=sa.text(_WHERE),
    )


def downgrade() -> None:
    op.drop_index('uq_checkins_one_active_walk', table_name='checkins')
    with op.batch_alter_table('checkins') as b:
        b.drop_index('ix_checkins_incident_id')
        b.drop_index('ix_checkins_kind')
        b.drop_constraint('fk_checkins_incident_id', type_='foreignkey')
        for c in ('incident_id', 'guardian_ids_json', 'last_loc_at', 'last_accuracy_m', 'last_lng',
                  'last_lat', 'start_lng', 'start_lat', 'extensions', 'ended_at', 'missed_at',
                  'checkin_count', 'last_checkin_at', 'grace_minutes', 'check_interval_minutes',
                  'arrive_by', 'started_at', 'kind'):
            b.drop_column(c)
