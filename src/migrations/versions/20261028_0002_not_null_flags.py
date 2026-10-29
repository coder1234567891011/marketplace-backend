"""make flag and counter columns NOT NULL

These columns were created nullable in Supabase even though they all have
defaults. Any NULLs are backfilled with the column default first.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-28
"""
from collections.abc import Sequence

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# table -> {column: value used to backfill NULLs}
COLUMNS = {
    "cards": {"is_foil_available": "true", "last_synced_at": "now()"},
    "users": {
        "stripe_connect_onboarded": "false",
        "email_verified": "false",
        "seller_rating_count": "0",
        "buyer_rating_count": "0",
        "total_sales": "0",
    },
    "addresses": {"is_default": "false"},
    "collections": {"is_public": "false"},
    "collection_items": {"is_foil": "false", "recognition_status": "'pending'"},
    "listings": {"is_foil": "false"},
}


def upgrade() -> None:
    for table, columns in COLUMNS.items():
        for column, fill in columns.items():
            op.execute(f"UPDATE {table} SET {column} = {fill} WHERE {column} IS NULL")
            op.alter_column(table, column, nullable=False)


def downgrade() -> None:
    for table, columns in COLUMNS.items():
        for column in columns:
            op.alter_column(table, column, nullable=True)
