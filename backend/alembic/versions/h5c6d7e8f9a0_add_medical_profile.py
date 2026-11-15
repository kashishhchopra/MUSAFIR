"""add emergency medical profile + QR medical access audit columns

Revision ID: h5c6d7e8f9a0
Revises: g4b5c6d7e8f9
Create Date: 2026-11-15 10:00:00.000000

New table `medical_profiles` (one row per tourist, clinical columns encrypted
at rest by the application). Nullable columns on the existing QR token and
scan-audit tables: `tourist_id_tokens.expires_at`, `tourist_id_scans.purpose`,
`tourist_id_scans.request_id`. Existing rows are untouched.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'h5c6d7e8f9a0'
down_revision: Union[str, Sequence[str], None] = 'g4b5c6d7e8f9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'medical_profiles',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('tourist_id', sa.Integer(), sa.ForeignKey('tourists.id', ondelete='CASCADE'),
                  nullable=False),
        sa.Column('blood_group', sa.String(), nullable=True),
        sa.Column('allergies_status', sa.String(), nullable=False, server_default='not_provided'),
        sa.Column('conditions_status', sa.String(), nullable=False, server_default='not_provided'),
        sa.Column('medications_status', sa.String(), nullable=False, server_default='not_provided'),
        sa.Column('allergies', sa.String(), nullable=False, server_default='[]'),
        sa.Column('medical_conditions', sa.String(), nullable=False, server_default='[]'),
        sa.Column('medications', sa.String(), nullable=False, server_default='[]'),
        sa.Column('emergency_contacts', sa.String(), nullable=False, server_default='[]'),
        sa.Column('additional_notes', sa.String(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('profile_updated_at', sa.DateTime(), nullable=False),
    )
    op.create_index('ix_medical_profiles_tourist_id', 'medical_profiles', ['tourist_id'], unique=True)

    with op.batch_alter_table('tourist_id_tokens') as b:
        b.add_column(sa.Column('expires_at', sa.DateTime(), nullable=True))
    with op.batch_alter_table('tourist_id_scans') as b:
        b.add_column(sa.Column('purpose', sa.String(), nullable=True))
        b.add_column(sa.Column('request_id', sa.String(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('tourist_id_scans') as b:
        b.drop_column('request_id')
        b.drop_column('purpose')
    with op.batch_alter_table('tourist_id_tokens') as b:
        b.drop_column('expires_at')
    op.drop_index('ix_medical_profiles_tourist_id', table_name='medical_profiles')
    op.drop_table('medical_profiles')
