"""add FCM (Android app) push subscriptions

Revision ID: e7f8a9b0c1d2
Revises: d1e2f3a4b5c6
Create Date: 2026-11-14 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'e7f8a9b0c1d2'
down_revision: Union[str, Sequence[str], None] = 'd1e2f3a4b5c6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('push_subscriptions') as batch:
        batch.add_column(sa.Column('kind', sa.String(), nullable=False, server_default='webpush'))
        batch.alter_column('p256dh', existing_type=sa.String(), nullable=True)
        batch.alter_column('auth', existing_type=sa.String(), nullable=True)


def downgrade() -> None:
    # FCM rows have no Web Push keys, so they cannot survive the NOT NULL constraint.
    op.execute("DELETE FROM notification_logs WHERE subscription_id IN "
               "(SELECT id FROM push_subscriptions WHERE kind = 'fcm')")
    op.execute("DELETE FROM push_subscriptions WHERE kind = 'fcm'")
    with op.batch_alter_table('push_subscriptions') as batch:
        batch.alter_column('auth', existing_type=sa.String(), nullable=False)
        batch.alter_column('p256dh', existing_type=sa.String(), nullable=False)
        batch.drop_column('kind')
