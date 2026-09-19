"""add archived and archive_path to message

Revision ID: 0001_add_archived_archive_path
Revises: 
Create Date: 2026-08-13 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '0001_add_archived_archive_path'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    # SQLite supports adding columns; use batch_alter_table for safety
    with op.batch_alter_table('message', schema=None) as batch_op:
        batch_op.add_column(sa.Column('archived', sa.Integer(), nullable=False, server_default='0'))
        batch_op.add_column(sa.Column('archive_path', sa.String(length=255), nullable=True))


def downgrade():
    # Removing columns in SQLite requires table rebuild; for simplicity, skip downgrade
    with op.batch_alter_table('message', schema=None) as batch_op:
        try:
            batch_op.drop_column('archive_path')
        except Exception:
            pass
        try:
            batch_op.drop_column('archived')
        except Exception:
            pass
