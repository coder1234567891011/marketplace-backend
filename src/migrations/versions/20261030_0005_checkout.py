"""Cart and checkout: cart items, checkouts (one Stripe payment covering an
order per seller), fee and payout fields on orders, card details on order
items, held copies on listings, processed Stripe webhook events, and a
recipient name on addresses.

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-30

The payment intent moves from orders to checkouts (one payment now covers
several orders). Existing order items get their card, condition, finish and
language from their listing.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '0005'
down_revision: str | None = '0004'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('addresses', sa.Column('recipient_name', sa.Text(), nullable=True))
    op.add_column('listings', sa.Column('quantity_reserved', sa.Integer(), server_default=sa.text('0'),
                                        nullable=False))

    op.create_table('cart_items',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('listing_id', sa.UUID(), nullable=False),
    sa.Column('quantity', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['listing_id'], ['listings.id'], name=op.f('cart_items_listing_id_fkey'),
                            ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('cart_items_user_id_fkey'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('cart_items_pkey')),
    sa.UniqueConstraint('user_id', 'listing_id', name=op.f('cart_items_user_id_listing_id_key'))
    )

    op.create_table('checkouts',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('buyer_id', sa.UUID(), nullable=False),
    sa.Column('status', sa.Text(), server_default=sa.text("'open'"), nullable=False),
    sa.Column('total_cents', sa.Integer(), nullable=False),
    sa.Column('currency', sa.Text(), server_default=sa.text("'usd'"), nullable=False),
    sa.Column('stripe_checkout_session_id', sa.Text(), nullable=True),
    sa.Column('stripe_checkout_url', sa.Text(), nullable=True),
    sa.Column('stripe_payment_intent_id', sa.Text(), nullable=True),
    sa.Column('stripe_charge_id', sa.Text(), nullable=True),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('paid_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("status IN ('open', 'paid', 'expired')", name=op.f('checkouts_status_check')),
    sa.ForeignKeyConstraint(['buyer_id'], ['users.id'], name=op.f('checkouts_buyer_id_fkey')),
    sa.PrimaryKeyConstraint('id', name=op.f('checkouts_pkey')),
    sa.UniqueConstraint('stripe_checkout_session_id', name=op.f('checkouts_stripe_checkout_session_id_key'))
    )
    op.create_index('idx_checkouts_buyer', 'checkouts', ['buyer_id', 'created_at'], unique=False)

    op.drop_constraint(op.f('orders_stripe_payment_intent_id_key'), 'orders', type_='unique')
    op.drop_column('orders', 'stripe_payment_intent_id')
    op.add_column('orders', sa.Column('checkout_id', sa.UUID(), nullable=True))
    op.create_foreign_key(op.f('orders_checkout_id_fkey'), 'orders', 'checkouts', ['checkout_id'], ['id'])
    op.add_column('orders', sa.Column('currency', sa.Text(), server_default=sa.text("'usd'"), nullable=False))
    op.add_column('orders', sa.Column('fee_percent', sa.Numeric(precision=5, scale=2), server_default=sa.text('0'),
                                      nullable=False))
    for column in ('fee_fixed_cents', 'platform_fee_cents', 'seller_payout_cents'):
        op.add_column('orders', sa.Column(column, sa.Integer(), server_default=sa.text('0'), nullable=False))
    op.add_column('orders', sa.Column('shipping_address', postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    for column in ('shipping_rate_name', 'stripe_transfer_id', 'stripe_refund_id'):
        op.add_column('orders', sa.Column(column, sa.Text(), nullable=True))
    for column in ('paid_at', 'shipped_at', 'delivered_at', 'paid_out_at'):
        op.add_column('orders', sa.Column(column, sa.DateTime(timezone=True), nullable=True))
    op.create_index('idx_orders_buyer', 'orders', ['buyer_id', 'created_at'], unique=False)
    op.create_index('idx_orders_seller', 'orders', ['seller_id', 'created_at'], unique=False)
    op.create_index('idx_orders_checkout', 'orders', ['checkout_id'], unique=False)

    for column in ('card_id', 'condition', 'finish', 'language'):
        op.add_column('order_items', sa.Column(column, sa.UUID() if column == 'card_id' else sa.Text(),
                                               nullable=True))
    op.execute("""
        UPDATE order_items oi
        SET card_id = l.card_id, condition = l.condition, finish = l.finish, language = l.language
        FROM listings l WHERE l.id = oi.listing_id
    """)
    for column in ('card_id', 'condition', 'finish', 'language'):
        op.alter_column('order_items', column, nullable=False)
    op.create_foreign_key(op.f('order_items_card_id_fkey'), 'order_items', 'cards', ['card_id'], ['id'])
    op.create_check_constraint(op.f('order_items_condition_check'), 'order_items',
                               "condition IN ('NM', 'LP', 'MP', 'HP', 'DMG')")
    op.create_check_constraint(op.f('order_items_finish_check'), 'order_items',
                               "finish IN ('nonfoil', 'foil', 'etched')")
    op.create_index('idx_order_items_order', 'order_items', ['order_id'], unique=False)

    op.create_table('stripe_events',
    sa.Column('id', sa.Text(), nullable=False),
    sa.Column('type', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('stripe_events_pkey'))
    )


def downgrade() -> None:
    op.drop_table('stripe_events')

    op.drop_index('idx_order_items_order', table_name='order_items')
    op.drop_constraint(op.f('order_items_finish_check'), 'order_items', type_='check')
    op.drop_constraint(op.f('order_items_condition_check'), 'order_items', type_='check')
    op.drop_constraint(op.f('order_items_card_id_fkey'), 'order_items', type_='foreignkey')
    for column in ('language', 'finish', 'condition', 'card_id'):
        op.drop_column('order_items', column)

    op.drop_index('idx_orders_checkout', table_name='orders')
    op.drop_index('idx_orders_seller', table_name='orders')
    op.drop_index('idx_orders_buyer', table_name='orders')
    for column in ('paid_out_at', 'delivered_at', 'shipped_at', 'paid_at', 'stripe_refund_id', 'stripe_transfer_id',
                   'shipping_rate_name', 'shipping_address', 'seller_payout_cents', 'platform_fee_cents',
                   'fee_fixed_cents', 'fee_percent', 'currency'):
        op.drop_column('orders', column)
    op.add_column('orders', sa.Column('stripe_payment_intent_id', sa.Text(), nullable=True))
    # Several orders shared one payment; the old one-per-order column keeps the first.
    op.execute("""
        UPDATE orders o SET stripe_payment_intent_id = c.stripe_payment_intent_id
        FROM checkouts c
        WHERE c.id = o.checkout_id AND o.id = (SELECT min(o2.id::text)::uuid FROM orders o2
                                              WHERE o2.checkout_id = c.id)
    """)
    op.create_unique_constraint(op.f('orders_stripe_payment_intent_id_key'), 'orders',
                                ['stripe_payment_intent_id'])
    op.drop_constraint(op.f('orders_checkout_id_fkey'), 'orders', type_='foreignkey')
    op.drop_column('orders', 'checkout_id')

    op.drop_index('idx_checkouts_buyer', table_name='checkouts')
    op.drop_table('checkouts')
    op.drop_table('cart_items')
    op.drop_column('listings', 'quantity_reserved')
    op.drop_column('addresses', 'recipient_name')
