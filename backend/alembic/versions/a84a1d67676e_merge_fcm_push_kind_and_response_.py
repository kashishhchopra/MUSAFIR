"""merge the Android FCM push-kind migration with main's migration chain

(main: response management -> medical profile -> privacy management)

Revision ID: a84a1d67676e
Revises: e7f8a9b0c1d2, i6d7e8f9a0b1
Create Date: 2026-11-16 01:03:02.685560

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a84a1d67676e'
down_revision: Union[str, Sequence[str], None] = ('e7f8a9b0c1d2', 'i6d7e8f9a0b1')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
