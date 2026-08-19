"""add organization_id to intake_jobs, source_messages, candidate_reviews

Revision ID: j1k2l3m4n5o6
Revises: h0e1f2a3b4c5
Create Date: 2026-08-15 20:00:00.000000

This migration adds organization_id columns to tables that were
missing org isolation: intake_jobs, source_messages, candidate_reviews.
It also creates indexes and backfills data from related orders/source messages.

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'j1k2l3m4n5o6'
down_revision: Union[str, None] = 'h0e1f2a3b4c5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _column_exists(table_name: str, column_name: str) -> bool:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    columns = {col["name"] for col in inspector.get_columns(table_name)}
    return column_name in columns


def _index_exists(table_name: str, index_name: str) -> bool:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    indexes = {idx["name"] for idx in inspector.get_indexes(table_name)}
    return index_name in indexes


def _add_column_if_not(table_name: str, column: sa.Column) -> None:
    if not _column_exists(table_name, column.name):
        op.add_column(table_name, column)


def _create_index_if_not(table_name: str, index_name: str, columns: list) -> None:
    if not _index_exists(table_name, index_name):
        op.create_index(index_name, table_name, columns)


def upgrade() -> None:
    """Add organization_id columns and backfill data."""
    
    # 1. Add organization_id to intake_jobs
    _add_column_if_not('intake_jobs', sa.Column('organization_id', sa.String(), nullable=True))
    _create_index_if_not('intake_jobs', 'idx_intake_jobs_org', ['organization_id'])
    
    # Backfill intake_jobs.organization_id from orders
    conn = op.get_bind()
    conn.execute(sa.text("""
        UPDATE intake_jobs SET organization_id = (
            SELECT o.organization_id FROM orders o WHERE o.order_id = intake_jobs.order_id
        )
        WHERE organization_id IS NULL AND order_id IS NOT NULL
    """))
    # Set remaining NULL to empty string (cannot determine org)
    conn.execute(sa.text("""
        UPDATE intake_jobs SET organization_id = '' WHERE organization_id IS NULL
    """))
    
    # 2. Add organization_id to source_messages
    _add_column_if_not('source_messages', sa.Column('organization_id', sa.String(), nullable=True))
    _create_index_if_not('source_messages', 'idx_source_messages_org', ['organization_id'])
    
    # Backfill source_messages.organization_id from orders
    conn.execute(sa.text("""
        UPDATE source_messages SET organization_id = (
            SELECT o.organization_id FROM orders o WHERE o.order_id = source_messages.order_id
        )
        WHERE organization_id IS NULL AND order_id IS NOT NULL
    """))
    # Set remaining NULL to empty string
    conn.execute(sa.text("""
        UPDATE source_messages SET organization_id = '' WHERE organization_id IS NULL
    """))
    
    # 3. Add organization_id to candidate_reviews
    _add_column_if_not('candidate_reviews', sa.Column('organization_id', sa.String(), nullable=True))
    _create_index_if_not('candidate_reviews', 'idx_reviews_org', ['organization_id'])
    
    # Backfill candidate_reviews.organization_id from candidate source_messages
    conn.execute(sa.text("""
        UPDATE candidate_reviews SET organization_id = (
            SELECT sm.organization_id FROM source_messages sm WHERE sm.message_id = candidate_reviews.source_message_id
        )
        WHERE organization_id IS NULL AND source_message_id IS NOT NULL
    """))
    # Backfill from orders as fallback
    conn.execute(sa.text("""
        UPDATE candidate_reviews SET organization_id = (
            SELECT o.organization_id FROM orders o WHERE o.order_id = candidate_reviews.order_id
        )
        WHERE organization_id IS NULL AND order_id IS NOT NULL
    """))
    # Set remaining NULL to empty string
    conn.execute(sa.text("""
        UPDATE candidate_reviews SET organization_id = '' WHERE organization_id IS NULL
    """))


def downgrade() -> None:
    """Remove organization_id columns without swallowing DDL failures."""
    for table_name, index_name in (
        ("candidate_reviews", "idx_reviews_org"),
        ("source_messages", "idx_source_messages_org"),
        ("intake_jobs", "idx_intake_jobs_org"),
    ):
        if _index_exists(table_name, index_name):
            op.drop_index(index_name, table_name=table_name)
        if _column_exists(table_name, "organization_id"):
            op.drop_column(table_name, "organization_id")
