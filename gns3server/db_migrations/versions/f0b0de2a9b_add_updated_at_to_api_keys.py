"""add updated_at column to api_keys table

Revision ID: f0b0de2a9b
Revises: f0b0de2a9
Create Date: 2026-06-11 23:15:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'f0b0de2a9b'
down_revision = 'f0b0de2a9'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('api_keys', sa.Column('updated_at', sa.DateTime(), server_default=sa.func.current_timestamp(), nullable=False))


def downgrade() -> None:
    op.drop_column('api_keys', 'updated_at')
