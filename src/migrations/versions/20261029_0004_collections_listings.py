"""Collections and listings: finish and language instead of is_foil, listing
notes, seller shipping rates, browse indexes; set type and promo flag on cards
(the next Scryfall sync fills them in).

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-29

Existing rows keep their foil flag as finish ('foil' or 'nonfoil') and get
language 'en'.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0004'
down_revision: str | None = '0003'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

FINISH_CHECK = "finish IN ('nonfoil', 'foil', 'etched')"


def upgrade() -> None:
    op.add_column('cards', sa.Column('set_type', sa.Text(), nullable=True))
    op.add_column('cards', sa.Column('promo', sa.Boolean(), server_default=sa.text('false'), nullable=False))

    for table in ('collection_items', 'listings'):
        op.add_column(table, sa.Column('finish', sa.Text(), server_default=sa.text("'nonfoil'"), nullable=False))
        op.add_column(table, sa.Column('language', sa.Text(), server_default=sa.text("'en'"), nullable=False))
        op.execute(f"UPDATE {table} SET finish = CASE WHEN is_foil THEN 'foil' ELSE 'nonfoil' END")
        op.drop_column(table, 'is_foil')
        op.create_check_constraint(op.f(f'{table}_finish_check'), table, FINISH_CHECK)
    op.add_column('listings', sa.Column('notes', sa.Text(), nullable=True))

    op.create_index('idx_collection_items_collection', 'collection_items', ['collection_id', 'created_at'], unique=False)
    op.create_index('idx_listings_card_price', 'listings', ['card_id', 'price_cents'], unique=False,
                    postgresql_where=sa.text("status = 'active'"))
    op.create_index('idx_listings_seller', 'listings', ['seller_id', 'created_at'], unique=False)
    op.create_index('idx_listings_collection_item', 'listings', ['collection_item_id'], unique=False)

    op.create_table('shipping_rates',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('seller_id', sa.UUID(), nullable=False),
    sa.Column('name', sa.Text(), nullable=False),
    sa.Column('price_cents', sa.Integer(), nullable=False),
    sa.Column('currency', sa.Text(), server_default=sa.text("'usd'"), nullable=False),
    sa.Column('tracked', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['seller_id'], ['users.id'], name=op.f('shipping_rates_seller_id_fkey'),
                            ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('shipping_rates_pkey'))
    )
    op.create_index('idx_shipping_rates_seller', 'shipping_rates', ['seller_id'], unique=False)


def downgrade() -> None:
    op.drop_index('idx_shipping_rates_seller', table_name='shipping_rates')
    op.drop_table('shipping_rates')
    op.drop_index('idx_listings_collection_item', table_name='listings')
    op.drop_index('idx_listings_seller', table_name='listings')
    op.drop_index('idx_listings_card_price', table_name='listings', postgresql_where=sa.text("status = 'active'"))
    op.drop_index('idx_collection_items_collection', table_name='collection_items')
    op.drop_column('listings', 'notes')
    for table in ('collection_items', 'listings'):
        op.drop_constraint(op.f(f'{table}_finish_check'), table, type_='check')
        op.add_column(table, sa.Column('is_foil', sa.Boolean(), server_default=sa.text('false'), nullable=False))
        # Etched becomes foil: the closest the old flag can say.
        op.execute(f"UPDATE {table} SET is_foil = finish <> 'nonfoil'")
        op.drop_column(table, 'language')
        op.drop_column(table, 'finish')
    op.drop_column('cards', 'promo')
    op.drop_column('cards', 'set_type')
