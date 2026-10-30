"""Wants lists: cards a user is looking for, any printing or an exact one,
with an optional target price.

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-30
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0008'
down_revision: str | None = '0007'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('wants_lists',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('name', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('wants_lists_user_id_fkey'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('wants_lists_pkey'))
    )
    op.create_index('idx_wants_lists_user', 'wants_lists', ['user_id', 'created_at'], unique=False)
    op.create_table('want_items',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('wants_list_id', sa.UUID(), nullable=False),
    sa.Column('card_id', sa.UUID(), nullable=False),
    sa.Column('oracle_id', sa.UUID(), nullable=False),
    sa.Column('any_printing', sa.Boolean(), server_default=sa.text('true'), nullable=False),
    sa.Column('quantity', sa.Integer(), nullable=False),
    sa.Column('min_condition', sa.Text(), nullable=True),
    sa.Column('finish', sa.Text(), nullable=True),
    sa.Column('language', sa.Text(), nullable=True),
    sa.Column('max_price_cents', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("finish IN ('nonfoil', 'foil', 'etched')", name=op.f('want_items_finish_check')),
    sa.CheckConstraint("min_condition IN ('NM', 'LP', 'MP', 'HP', 'DMG')",
                       name=op.f('want_items_min_condition_check')),
    sa.ForeignKeyConstraint(['card_id'], ['cards.id'], name=op.f('want_items_card_id_fkey')),
    sa.ForeignKeyConstraint(['wants_list_id'], ['wants_lists.id'], name=op.f('want_items_wants_list_id_fkey'),
                            ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('want_items_pkey'))
    )
    op.create_index('idx_want_items_list', 'want_items', ['wants_list_id'], unique=False)
    op.create_index('idx_want_items_oracle', 'want_items', ['oracle_id'], unique=False)


def downgrade() -> None:
    op.drop_index('idx_want_items_oracle', table_name='want_items')
    op.drop_index('idx_want_items_list', table_name='want_items')
    op.drop_table('want_items')
    op.drop_index('idx_wants_lists_user', table_name='wants_lists')
    op.drop_table('wants_lists')
