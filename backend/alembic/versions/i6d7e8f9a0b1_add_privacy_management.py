"""add DPDP-oriented privacy management tables

Revision ID: i6d7e8f9a0b1
Revises: h5c6d7e8f9a0
Create Date: 2026-11-15 12:00:00.000000

New tables only (no existing table or row is altered):
consent_records, retention_policies, retention_exceptions, deletion_jobs,
data_export_requests, account_deletion_requests, privacy_grievances,
privacy_grievance_responses.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'i6d7e8f9a0b1'
down_revision: Union[str, Sequence[str], None] = 'h5c6d7e8f9a0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'consent_records',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('consent_id', sa.String(), nullable=False),
        sa.Column('tourist_id', sa.Integer(), sa.ForeignKey('tourists.id', ondelete='SET NULL'), nullable=True),
        sa.Column('subject_ref', sa.String(), nullable=False),
        sa.Column('purpose', sa.String(), nullable=False),
        sa.Column('data_categories', sa.Text(), nullable=False, server_default='[]'),
        sa.Column('status', sa.String(), nullable=False, server_default='granted'),
        sa.Column('consent_version', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('notice_version', sa.String(), nullable=False),
        sa.Column('consented_at', sa.DateTime(), nullable=False),
        sa.Column('withdrawn_at', sa.DateTime(), nullable=True),
        sa.Column('source', sa.String(), nullable=False, server_default='privacy_dashboard'),
        sa.Column('evidence', sa.Text(), nullable=False, server_default='{}'),
    )
    op.create_index('ix_consent_records_consent_id', 'consent_records', ['consent_id'], unique=True)
    op.create_index('ix_consent_records_tourist_id', 'consent_records', ['tourist_id'])
    op.create_index('ix_consent_records_purpose', 'consent_records', ['purpose'])
    op.create_index('ix_consent_subject_purpose', 'consent_records', ['subject_ref', 'purpose'])

    op.create_table(
        'retention_policies',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('category', sa.String(), nullable=False),
        sa.Column('title', sa.String(), nullable=False, server_default=''),
        sa.Column('purpose', sa.Text(), nullable=False, server_default=''),
        sa.Column('legal_basis', sa.Text(), nullable=False, server_default=''),
        sa.Column('retention_days', sa.Integer(), nullable=True),
        sa.Column('start_condition', sa.String(), nullable=False, server_default='created'),
        sa.Column('deletion_action', sa.String(), nullable=False, server_default='delete'),
        sa.Column('account_deletion_action', sa.String(), nullable=False, server_default='delete'),
        sa.Column('per_tourist', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('requires_review', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('note', sa.Text(), nullable=False, server_default=''),
        sa.Column('updated_by', sa.String(), nullable=False, server_default='system'),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.Column('last_reviewed_at', sa.DateTime(), nullable=True),
    )
    op.create_index('ix_retention_policies_category', 'retention_policies', ['category'], unique=True)

    op.create_table(
        'retention_exceptions',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('category', sa.String(), nullable=False),
        sa.Column('subject_ref', sa.String(), nullable=True),
        sa.Column('tourist_id', sa.Integer(), nullable=True),
        sa.Column('reason', sa.Text(), nullable=False),
        sa.Column('basis', sa.Text(), nullable=False, server_default=''),
        sa.Column('created_by', sa.String(), nullable=False, server_default='system'),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('review_by', sa.DateTime(), nullable=True),
        sa.Column('released_at', sa.DateTime(), nullable=True),
        sa.Column('active', sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.create_index('ix_retention_exceptions_category', 'retention_exceptions', ['category'])
    op.create_index('ix_retention_exceptions_subject_ref', 'retention_exceptions', ['subject_ref'])
    op.create_index('ix_retention_exceptions_tourist_id', 'retention_exceptions', ['tourist_id'])
    op.create_index('ix_retention_exceptions_active', 'retention_exceptions', ['active'])

    op.create_table(
        'deletion_jobs',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('job_id', sa.String(), nullable=False),
        sa.Column('category', sa.String(), nullable=False),
        sa.Column('job_type', sa.String(), nullable=False, server_default='scheduled'),
        sa.Column('status', sa.String(), nullable=False, server_default='running'),
        sa.Column('subject_ref', sa.String(), nullable=True),
        sa.Column('started_at', sa.DateTime(), nullable=False),
        sa.Column('finished_at', sa.DateTime(), nullable=True),
        sa.Column('records_processed', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('records_deleted', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('records_retained', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('attempts', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('failure_reason', sa.Text(), nullable=True),
    )
    op.create_index('ix_deletion_jobs_job_id', 'deletion_jobs', ['job_id'], unique=True)
    op.create_index('ix_deletion_jobs_category', 'deletion_jobs', ['category'])
    op.create_index('ix_deletion_jobs_started_at', 'deletion_jobs', ['started_at'])

    op.create_table(
        'data_export_requests',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('export_id', sa.String(), nullable=False),
        sa.Column('tourist_id', sa.Integer(), sa.ForeignKey('tourists.id', ondelete='CASCADE'), nullable=False),
        sa.Column('status', sa.String(), nullable=False, server_default='pending'),
        sa.Column('export_format', sa.String(), nullable=False, server_default='json'),
        sa.Column('file_name', sa.String(), nullable=True),
        sa.Column('size_bytes', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('ready_at', sa.DateTime(), nullable=True),
        sa.Column('expires_at', sa.DateTime(), nullable=True),
        sa.Column('downloaded_at', sa.DateTime(), nullable=True),
        sa.Column('download_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('failure_reason', sa.Text(), nullable=True),
    )
    op.create_index('ix_data_export_requests_export_id', 'data_export_requests', ['export_id'], unique=True)
    op.create_index('ix_data_export_requests_tourist_id', 'data_export_requests', ['tourist_id'])

    op.create_table(
        'account_deletion_requests',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('request_id', sa.String(), nullable=False),
        sa.Column('tourist_id', sa.Integer(), nullable=True),
        sa.Column('user_id', sa.Integer(), nullable=True),
        sa.Column('subject_ref', sa.String(), nullable=False),
        sa.Column('status', sa.String(), nullable=False, server_default='awaiting_confirmation'),
        sa.Column('requested_at', sa.DateTime(), nullable=False),
        sa.Column('confirmed_at', sa.DateTime(), nullable=True),
        sa.Column('process_after', sa.DateTime(), nullable=True),
        sa.Column('started_at', sa.DateTime(), nullable=True),
        sa.Column('completed_at', sa.DateTime(), nullable=True),
        sa.Column('attempts', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('failure_reason', sa.Text(), nullable=True),
        sa.Column('steps', sa.Text(), nullable=False, server_default='[]'),
        sa.Column('retained', sa.Text(), nullable=False, server_default='[]'),
        sa.Column('processors', sa.Text(), nullable=False, server_default='[]'),
    )
    op.create_index('ix_account_deletion_requests_request_id', 'account_deletion_requests', ['request_id'], unique=True)
    op.create_index('ix_account_deletion_requests_tourist_id', 'account_deletion_requests', ['tourist_id'])
    op.create_index('ix_account_deletion_requests_subject_ref', 'account_deletion_requests', ['subject_ref'])
    op.create_index('ix_account_deletion_requests_status', 'account_deletion_requests', ['status'])

    op.create_table(
        'privacy_grievances',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('grievance_id', sa.String(), nullable=False),
        sa.Column('tourist_id', sa.Integer(), nullable=True),
        sa.Column('subject_ref', sa.String(), nullable=False),
        sa.Column('category', sa.String(), nullable=False, server_default='other'),
        sa.Column('description', sa.Text(), nullable=False),
        sa.Column('status', sa.String(), nullable=False, server_default='open'),
        sa.Column('submitted_at', sa.DateTime(), nullable=False),
        sa.Column('due_at', sa.DateTime(), nullable=True),
        sa.Column('resolved_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
    )
    op.create_index('ix_privacy_grievances_grievance_id', 'privacy_grievances', ['grievance_id'], unique=True)
    op.create_index('ix_privacy_grievances_tourist_id', 'privacy_grievances', ['tourist_id'])
    op.create_index('ix_privacy_grievances_subject_ref', 'privacy_grievances', ['subject_ref'])
    op.create_index('ix_privacy_grievances_status', 'privacy_grievances', ['status'])

    op.create_table(
        'privacy_grievance_responses',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('grievance_pk', sa.Integer(),
                  sa.ForeignKey('privacy_grievances.id', ondelete='CASCADE'), nullable=False),
        sa.Column('author_role', sa.String(), nullable=False, server_default='admin'),
        sa.Column('message', sa.Text(), nullable=False),
        sa.Column('status_after', sa.String(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
    )
    op.create_index('ix_privacy_grievance_responses_grievance_pk',
                    'privacy_grievance_responses', ['grievance_pk'])


def downgrade() -> None:
    for table in (
        'privacy_grievance_responses', 'privacy_grievances', 'account_deletion_requests',
        'data_export_requests', 'deletion_jobs', 'retention_exceptions',
        'retention_policies', 'consent_records',
    ):
        op.drop_table(table)
