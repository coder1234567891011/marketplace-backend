"""Market prices: the current marketplace price per card, finish and
language (market_prices), its history (price_snapshots), and an index for
looking up a card's sales.

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-30
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0006'
down_revision: str | None = '0005'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('market_prices',
    sa.Column('card_id', sa.UUID(), nullable=False),
    sa.Column('finish', sa.Text(), nullable=False),
    sa.Column('language', sa.Text(), nullable=False),
    sa.Column('price_cents', sa.Integer(), nullable=False),
    sa.Column('source', sa.Text(), nullable=False),
    sa.Column('sales_count', sa.Integer(), server_default=sa.text('0'), nullable=False),
    sa.Column('computed_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("finish IN ('nonfoil', 'foil', 'etched')", name=op.f('market_prices_finish_check')),
    sa.CheckConstraint("source IN ('sales', 'listings')", name=op.f('market_prices_source_check')),
    sa.ForeignKeyConstraint(['card_id'], ['cards.id'], name=op.f('market_prices_card_id_fkey'),
                            ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('card_id', 'finish', 'language', name=op.f('market_prices_pkey'))
    )
    op.create_table('price_snapshots',
    sa.Column('card_id', sa.UUID(), nullable=False),
    sa.Column('finish', sa.Text(), nullable=False),
    sa.Column('language', sa.Text(), nullable=False),
    sa.Column('day', sa.Date(), nullable=False),
    sa.Column('price_cents', sa.Integer(), nullable=False),
    sa.Column('source', sa.Text(), nullable=False),
    sa.CheckConstraint("finish IN ('nonfoil', 'foil', 'etched')", name=op.f('price_snapshots_finish_check')),
    sa.CheckConstraint("source IN ('sales', 'listings')", name=op.f('price_snapshots_source_check')),
    sa.ForeignKeyConstraint(['card_id'], ['cards.id'], name=op.f('price_snapshots_card_id_fkey'),
                            ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('card_id', 'finish', 'language', 'day', name=op.f('price_snapshots_pkey'))
    )
    op.create_index('idx_order_items_card', 'order_items', ['card_id'], unique=False)


def downgrade() -> None:
    op.drop_index('idx_order_items_card', table_name='order_items')
    op.drop_table('price_snapshots')
    op.drop_table('market_prices')
