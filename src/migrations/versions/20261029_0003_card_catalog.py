"""Card catalog: Scryfall fields on cards, card_prices, name search indexes.

Revision ID: 0003
Revises: d04756ad8f4c
Create Date: 2026-10-29

Cards that already exist get placeholder values for the new required
columns (oracle_id = scryfall_id, set_name = set code, layout "normal",
released today). The first Scryfall sync overwrites them with real data:
it matches rows by scryfall_id, or by set, collector number and language.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '0003'
down_revision: str | None = 'd04756ad8f4c'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Trigram matching for name autocomplete. Supabase allows the postgres
    # role to create it.
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    op.add_column('cards', sa.Column('oracle_id', sa.UUID(), nullable=True))
    op.add_column('cards', sa.Column('set_name', sa.Text(), nullable=True))
    op.add_column('cards', sa.Column('lang', sa.Text(), server_default=sa.text("'en'::text"), nullable=False))
    op.add_column('cards', sa.Column('released_at', sa.Date(), nullable=True))
    op.add_column('cards', sa.Column('layout', sa.Text(), nullable=True))
    op.add_column('cards', sa.Column('mana_cost', sa.Text(), nullable=True))
    op.add_column('cards', sa.Column('cmc', sa.Double(), nullable=True))
    op.add_column('cards', sa.Column('type_line', sa.Text(), nullable=True))
    op.add_column('cards', sa.Column('oracle_text', sa.Text(), nullable=True))
    op.add_column('cards', sa.Column('colors', postgresql.ARRAY(sa.Text()), nullable=True))
    op.add_column('cards', sa.Column('color_identity', postgresql.ARRAY(sa.Text()),
                                     server_default=sa.text("'{}'::text[]"), nullable=False))
    op.add_column('cards', sa.Column('finishes', postgresql.ARRAY(sa.Text()),
                                     server_default=sa.text("'{}'::text[]"), nullable=False))
    op.add_column('cards', sa.Column('image_uris', postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.add_column('cards', sa.Column('card_faces', postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.execute("""
        UPDATE cards
        SET oracle_id = scryfall_id, set_name = upper(set_code), released_at = current_date, layout = 'normal'
    """)
    for column in ('oracle_id', 'set_name', 'released_at', 'layout'):
        op.alter_column('cards', column, nullable=False)
    op.drop_column('cards', 'image_url')
    op.drop_column('cards', 'is_foil_available')

    # Scryfall numbers a printing by set, collector number and language.
    op.drop_constraint('cards_set_code_collector_number_key', 'cards', type_='unique')
    op.create_unique_constraint('cards_set_code_collector_number_lang_key', 'cards',
                                ['set_code', 'collector_number', 'lang'])
    op.create_index('idx_cards_oracle_id', 'cards', ['oracle_id'], unique=False)
    op.create_index('idx_cards_name_trgm', 'cards', ['name'], unique=False,
                    postgresql_using='gin', postgresql_ops={'name': 'gin_trgm_ops'})

    op.create_table('card_prices',
    sa.Column('card_id', sa.UUID(), nullable=False),
    sa.Column('prices', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['card_id'], ['cards.id'], name=op.f('card_prices_card_id_fkey'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('card_id', name=op.f('card_prices_pkey'))
    )


def downgrade() -> None:
    op.drop_table('card_prices')
    op.drop_index('idx_cards_name_trgm', table_name='cards', postgresql_using='gin',
                  postgresql_ops={'name': 'gin_trgm_ops'})
    op.drop_index('idx_cards_oracle_id', table_name='cards')
    op.drop_constraint('cards_set_code_collector_number_lang_key', 'cards', type_='unique')
    op.create_unique_constraint('cards_set_code_collector_number_key', 'cards', ['set_code', 'collector_number'])
    op.add_column('cards', sa.Column('is_foil_available', sa.Boolean(), server_default=sa.text('true'),
                                     nullable=False))
    op.add_column('cards', sa.Column('image_url', sa.Text(), nullable=True))
    for column in ('card_faces', 'image_uris', 'finishes', 'color_identity', 'colors', 'oracle_text',
                   'type_line', 'cmc', 'mana_cost', 'layout', 'released_at', 'lang', 'set_name', 'oracle_id'):
        op.drop_column('cards', column)
    # pg_trgm is left installed; other objects may use it.
