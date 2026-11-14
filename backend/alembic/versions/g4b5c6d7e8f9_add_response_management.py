"""add intelligent response management (classification, escalation, SLO)

Revision ID: g4b5c6d7e8f9
Revises: f3a4b5c6d7e8
Create Date: 2026-11-14 18:00:00.000000

Adds nullable/defaulted columns to incidents (existing rows are untouched and
simply have no response target) plus three new tables.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'g4b5c6d7e8f9'
down_revision: Union[str, Sequence[str], None] = 'f3a4b5c6d7e8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_COLS = [
    sa.Column('baseline_severity', sa.String(), nullable=True),
    sa.Column('classification_reason', sa.Text(), nullable=False, server_default=''),
    sa.Column('classified_at', sa.DateTime(), nullable=True),
    sa.Column('needs_review', sa.Boolean(), nullable=False, server_default='0'),
    sa.Column('response_target_seconds', sa.Integer(), nullable=True),
    sa.Column('response_deadline', sa.DateTime(), nullable=True),
    sa.Column('assigned_at', sa.DateTime(), nullable=True),
    sa.Column('acknowledged_by', sa.Integer(), nullable=True),
    sa.Column('acknowledgment_seconds', sa.Float(), nullable=True),
    sa.Column('slo_met', sa.Boolean(), nullable=True),
    sa.Column('response_stage', sa.Integer(), nullable=False, server_default='1'),
    sa.Column('response_stage_deadline', sa.DateTime(), nullable=True),
    sa.Column('escalation_started_at', sa.DateTime(), nullable=True),
    sa.Column('global_timeout_deadline', sa.DateTime(), nullable=True),
    sa.Column('global_timeout_at', sa.DateTime(), nullable=True),
    sa.Column('escalation_status', sa.String(), nullable=False, server_default='none'),
]


def upgrade() -> None:
    with op.batch_alter_table('incidents') as b:
        for c in _COLS:
            b.add_column(c)
        b.create_foreign_key('fk_incidents_acknowledged_by', 'users', ['acknowledged_by'], ['id'])
        b.create_index('ix_incidents_response_deadline', ['response_deadline'])
        b.create_index('ix_incidents_response_stage_deadline', ['response_stage_deadline'])
        b.create_index('ix_incidents_escalation_status', ['escalation_status'])

    op.create_table(
        'severity_classifications',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('incident_id', sa.Integer(), sa.ForeignKey('incidents.id'), nullable=False),
        sa.Column('incident_type', sa.String(), nullable=False, server_default=''),
        sa.Column('severity', sa.String(), nullable=False),
        sa.Column('previous_severity', sa.String(), nullable=True),
        sa.Column('baseline_severity', sa.String(), nullable=False),
        sa.Column('trigger', sa.String(), nullable=False, server_default='initial'),
        sa.Column('rules_json', sa.Text(), nullable=False, server_default='[]'),
        sa.Column('evidence_json', sa.Text(), nullable=False, server_default='{}'),
        sa.Column('reason', sa.Text(), nullable=False, server_default=''),
        sa.Column('risk_score', sa.Float(), nullable=True),
        sa.Column('needs_review', sa.Boolean(), nullable=False, server_default='0'),
        sa.Column('manual_override', sa.Boolean(), nullable=False, server_default='0'),
        sa.Column('actor_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=True),
        sa.Column('override_reason', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
    )
    op.create_index('ix_severity_classifications_incident_id', 'severity_classifications', ['incident_id'])
    op.create_index('ix_severity_classifications_created_at', 'severity_classifications', ['created_at'])

    op.create_table(
        'escalation_events',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('incident_id', sa.Integer(), sa.ForeignKey('incidents.id'), nullable=False),
        sa.Column('kind', sa.String(), nullable=False),
        sa.Column('from_stage', sa.Integer(), nullable=True),
        sa.Column('to_stage', sa.Integer(), nullable=True),
        sa.Column('dedupe_key', sa.String(), nullable=False),
        sa.Column('note', sa.Text(), nullable=False, server_default=''),
        sa.Column('actor_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.UniqueConstraint('incident_id', 'dedupe_key', name='uq_escalation_events_dedupe'),
    )
    op.create_index('ix_escalation_events_incident_id', 'escalation_events', ['incident_id'])
    op.create_index('ix_escalation_events_kind', 'escalation_events', ['kind'])
    op.create_index('ix_escalation_events_created_at', 'escalation_events', ['created_at'])

    op.create_table(
        'response_settings',
        sa.Column('key', sa.String(), primary_key=True),
        sa.Column('value', sa.String(), nullable=False),
        sa.Column('updated_by', sa.Integer(), sa.ForeignKey('users.id'), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table('response_settings')
    op.drop_table('escalation_events')
    op.drop_table('severity_classifications')
    with op.batch_alter_table('incidents') as b:
        b.drop_index('ix_incidents_escalation_status')
        b.drop_index('ix_incidents_response_stage_deadline')
        b.drop_index('ix_incidents_response_deadline')
        b.drop_constraint('fk_incidents_acknowledged_by', type_='foreignkey')
        for c in reversed(_COLS):
            b.drop_column(c.name)
