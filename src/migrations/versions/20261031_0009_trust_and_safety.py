"""Trust and safety: order issues and their messages, partial refunds on
orders, reports, moderators and their actions, and the email outbox.

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-31
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '0009'
down_revision: str | None = '0008'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Emails go to auth.users.email. Supabase has it; add it to the stand-in
    # the baseline creates on plain Postgres. Never alters Supabase's table.
    op.execute("""
        DO $$ BEGIN
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                           WHERE table_schema = 'auth' AND table_name = 'users' AND column_name = 'email') THEN
                ALTER TABLE auth.users ADD COLUMN email text;
            END IF;
        END $$
    """)
    op.create_table('emails',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('template', sa.Text(), nullable=False),
    sa.Column('data', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
    sa.Column('status', sa.Text(), server_default=sa.text("'pending'"), nullable=False),
    sa.Column('attempts', sa.Integer(), server_default=sa.text('0'), nullable=False),
    sa.Column('last_error', sa.Text(), nullable=True),
    sa.Column('sent_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("status IN ('pending', 'sent', 'failed', 'skipped')", name=op.f('emails_status_check')),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('emails_user_id_fkey'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('emails_pkey'))
    )
    op.create_index('idx_emails_pending', 'emails', ['created_at'], unique=False, postgresql_where=sa.text("status = 'pending'"))
    op.create_table('moderation_actions',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('admin_id', sa.UUID(), nullable=False),
    sa.Column('action', sa.Text(), nullable=False),
    sa.Column('target_id', sa.UUID(), nullable=False),
    sa.Column('details', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
    sa.Column('note', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("action IN ('set_account_status', 'remove_listing', 'remove_rating', 'resolve_report', 'resolve_issue')", name=op.f('moderation_actions_action_check')),
    sa.ForeignKeyConstraint(['admin_id'], ['users.id'], name=op.f('moderation_actions_admin_id_fkey')),
    sa.PrimaryKeyConstraint('id', name=op.f('moderation_actions_pkey'))
    )
    op.create_index('idx_moderation_actions_target', 'moderation_actions', ['target_id', 'created_at'], unique=False)
    op.create_table('reports',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('reporter_id', sa.UUID(), nullable=False),
    sa.Column('target_type', sa.Text(), nullable=False),
    sa.Column('target_id', sa.UUID(), nullable=False),
    sa.Column('reason', sa.Text(), nullable=False),
    sa.Column('details', sa.Text(), nullable=True),
    sa.Column('status', sa.Text(), server_default=sa.text("'open'"), nullable=False),
    sa.Column('resolved_by', sa.UUID(), nullable=True),
    sa.Column('resolution_note', sa.Text(), nullable=True),
    sa.Column('resolved_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("reason IN ('counterfeit', 'misdescribed', 'scam', 'harassment', 'spam', 'other')", name=op.f('reports_reason_check')),
    sa.CheckConstraint("status IN ('open', 'actioned', 'dismissed')", name=op.f('reports_status_check')),
    sa.CheckConstraint("target_type IN ('listing', 'user', 'rating')", name=op.f('reports_target_type_check')),
    sa.ForeignKeyConstraint(['reporter_id'], ['users.id'], name=op.f('reports_reporter_id_fkey'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['resolved_by'], ['users.id'], name=op.f('reports_resolved_by_fkey')),
    sa.PrimaryKeyConstraint('id', name=op.f('reports_pkey'))
    )
    op.create_index('idx_reports_status', 'reports', ['status', 'created_at'], unique=False)
    op.create_index('idx_reports_target', 'reports', ['target_type', 'target_id'], unique=False)
    op.create_index('uq_reports_open', 'reports', ['reporter_id', 'target_type', 'target_id'], unique=True, postgresql_where=sa.text("status = 'open'"))
    op.create_table('order_issues',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('order_id', sa.UUID(), nullable=False),
    sa.Column('buyer_id', sa.UUID(), nullable=False),
    sa.Column('seller_id', sa.UUID(), nullable=False),
    sa.Column('reason', sa.Text(), nullable=False),
    sa.Column('status', sa.Text(), server_default=sa.text("'open'"), nullable=False),
    sa.Column('resolution', sa.Text(), nullable=True),
    sa.Column('refund_cents', sa.Integer(), server_default=sa.text('0'), nullable=False),
    sa.Column('resolved_by', sa.UUID(), nullable=True),
    sa.Column('escalated_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('resolved_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("reason IN ('not_received', 'not_as_described', 'other')", name=op.f('order_issues_reason_check')),
    sa.CheckConstraint("resolution IN ('refunded', 'no_refund', 'withdrawn')", name=op.f('order_issues_resolution_check')),
    sa.CheckConstraint("status IN ('open', 'escalated', 'resolved')", name=op.f('order_issues_status_check')),
    sa.ForeignKeyConstraint(['buyer_id'], ['users.id'], name=op.f('order_issues_buyer_id_fkey')),
    sa.ForeignKeyConstraint(['order_id'], ['orders.id'], name=op.f('order_issues_order_id_fkey')),
    sa.ForeignKeyConstraint(['resolved_by'], ['users.id'], name=op.f('order_issues_resolved_by_fkey')),
    sa.ForeignKeyConstraint(['seller_id'], ['users.id'], name=op.f('order_issues_seller_id_fkey')),
    sa.PrimaryKeyConstraint('id', name=op.f('order_issues_pkey'))
    )
    op.create_index('idx_order_issues_status', 'order_issues', ['status', 'created_at'], unique=False)
    op.create_index('uq_order_issues_unresolved', 'order_issues', ['order_id'], unique=True, postgresql_where=sa.text("status <> 'resolved'"))
    op.create_table('issue_messages',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('issue_id', sa.UUID(), nullable=False),
    sa.Column('author_id', sa.UUID(), nullable=False),
    sa.Column('body', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['author_id'], ['users.id'], name=op.f('issue_messages_author_id_fkey')),
    sa.ForeignKeyConstraint(['issue_id'], ['order_issues.id'], name=op.f('issue_messages_issue_id_fkey'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('issue_messages_pkey'))
    )
    op.create_index('idx_issue_messages_issue', 'issue_messages', ['issue_id', 'created_at'], unique=False)
    op.add_column('orders', sa.Column('refunded_cents', sa.Integer(), server_default=sa.text('0'), nullable=False))
    op.add_column('orders', sa.Column('seller_refunded_cents', sa.Integer(), server_default=sa.text('0'), nullable=False))
    op.add_column('users', sa.Column('is_admin', sa.Boolean(), server_default=sa.text('false'), nullable=False))


def downgrade() -> None:
    op.drop_column('users', 'is_admin')
    op.drop_column('orders', 'seller_refunded_cents')
    op.drop_column('orders', 'refunded_cents')
    op.drop_index('idx_issue_messages_issue', table_name='issue_messages')
    op.drop_table('issue_messages')
    op.drop_index('uq_order_issues_unresolved', table_name='order_issues', postgresql_where=sa.text("status <> 'resolved'"))
    op.drop_index('idx_order_issues_status', table_name='order_issues')
    op.drop_table('order_issues')
    op.drop_index('uq_reports_open', table_name='reports', postgresql_where=sa.text("status = 'open'"))
    op.drop_index('idx_reports_target', table_name='reports')
    op.drop_index('idx_reports_status', table_name='reports')
    op.drop_table('reports')
    op.drop_index('idx_moderation_actions_target', table_name='moderation_actions')
    op.drop_table('moderation_actions')
    op.drop_index('idx_emails_pending', table_name='emails', postgresql_where=sa.text("status = 'pending'"))
    op.drop_table('emails')
