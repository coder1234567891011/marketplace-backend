"""Scans: photos of cards, the text read from them, and the printings they
matched.

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-30
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '0007'
down_revision: str | None = '0006'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('scans',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('status', sa.Text(), server_default=sa.text("'pending'"), nullable=False),
    sa.Column('photo_key', sa.Text(), nullable=True),
    sa.Column('collector_text', sa.Text(), nullable=True),
    sa.Column('name_text', sa.Text(), nullable=True),
    sa.Column('detected_language', sa.Text(), nullable=True),
    sa.Column('detected_foil', sa.Boolean(), nullable=True),
    sa.Column('candidates', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"),
              nullable=False),
    sa.Column('collection_item_id', sa.UUID(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("status IN ('pending', 'matched', 'unmatched', 'confirmed')",
                       name=op.f('scans_status_check')),
    sa.ForeignKeyConstraint(['collection_item_id'], ['collection_items.id'],
                            name=op.f('scans_collection_item_id_fkey'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('scans_user_id_fkey'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('scans_pkey'))
    )
    op.create_index('idx_scans_user', 'scans', ['user_id', 'created_at'], unique=False)


def downgrade() -> None:
    op.drop_index('idx_scans_user', table_name='scans')
    op.drop_table('scans')
