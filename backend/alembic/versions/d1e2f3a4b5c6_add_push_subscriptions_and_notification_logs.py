"""add push subscriptions and notification logs

Revision ID: d1e2f3a4b5c6
Revises: c7d8e9f0a1b2
Create Date: 2026-11-13 20:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from app.core.crypto import EncryptedString

revision: str = 'd1e2f3a4b5c6'
down_revision: Union[str, Sequence[str], None] = 'c7d8e9f0a1b2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('push_subscriptions',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('endpoint', sa.String(), nullable=False),
    sa.Column('p256dh', sa.String(), nullable=False),
    sa.Column('auth', sa.String(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=True),
    sa.Column('guardian_id', sa.Integer(), nullable=True),
    sa.Column('role', sa.String(), nullable=False),
    sa.Column('user_agent', sa.String(), nullable=False),
    sa.Column('active', sa.Boolean(), nullable=False),
    sa.Column('failure_count', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('last_success_at', sa.DateTime(), nullable=True),
    sa.ForeignKeyConstraint(['guardian_id'], ['trip_guardians.id'], ),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_push_subscriptions_endpoint'), 'push_subscriptions', ['endpoint'], unique=True)
    op.create_index(op.f('ix_push_subscriptions_guardian_id'), 'push_subscriptions', ['guardian_id'], unique=False)
    op.create_index(op.f('ix_push_subscriptions_user_id'), 'push_subscriptions', ['user_id'], unique=False)
    op.create_table('notification_logs',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('event_type', sa.String(), nullable=False),
    sa.Column('channel', sa.String(), nullable=False),
    sa.Column('recipient_type', sa.String(), nullable=False),
    sa.Column('recipient_label', sa.String(), nullable=False),
    sa.Column('address', EncryptedString(), nullable=True),
    sa.Column('subscription_id', sa.Integer(), nullable=True),
    sa.Column('incident_id', sa.Integer(), nullable=True),
    sa.Column('tourist_id', sa.Integer(), nullable=True),
    sa.Column('title', sa.String(), nullable=False),
    sa.Column('body', sa.Text(), nullable=False),
    sa.Column('url', sa.String(), nullable=False),
    sa.Column('severity', sa.String(), nullable=False),
    sa.Column('status', sa.String(), nullable=False),
    sa.Column('attempts', sa.Integer(), nullable=False),
    sa.Column('last_error', sa.Text(), nullable=True),
    sa.Column('next_retry_at', sa.DateTime(), nullable=True),
    sa.Column('provider_id', sa.String(), nullable=True),
    sa.Column('demo', sa.Boolean(), nullable=False),
    sa.Column('dedupe_key', sa.String(), nullable=True),
    sa.Column('sent_at', sa.DateTime(), nullable=True),
    sa.Column('payload', sa.Text(), nullable=False),
    sa.ForeignKeyConstraint(['incident_id'], ['incidents.id'], ),
    sa.ForeignKeyConstraint(['subscription_id'], ['push_subscriptions.id'], ),
    sa.ForeignKeyConstraint(['tourist_id'], ['tourists.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('dedupe_key')
    )
    for col in ('created_at', 'event_type', 'channel', 'status', 'next_retry_at', 'incident_id', 'tourist_id'):
        op.create_index(op.f(f'ix_notification_logs_{col}'), 'notification_logs', [col], unique=False)


def downgrade() -> None:
    for col in ('created_at', 'event_type', 'channel', 'status', 'next_retry_at', 'incident_id', 'tourist_id'):
        op.drop_index(op.f(f'ix_notification_logs_{col}'), table_name='notification_logs')
    op.drop_table('notification_logs')
    op.drop_index(op.f('ix_push_subscriptions_user_id'), table_name='push_subscriptions')
    op.drop_index(op.f('ix_push_subscriptions_guardian_id'), table_name='push_subscriptions')
    op.drop_index(op.f('ix_push_subscriptions_endpoint'), table_name='push_subscriptions')
    op.drop_table('push_subscriptions')
