"""D14 structured quality and internal fact-conflict contracts.

Revision ID: n5o6p7q8r9s0
Revises: m4n5o6p7q8r9
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "n5o6p7q8r9s0"
down_revision: Union[str, None] = "m4n5o6p7q8r9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    tables = set(insp.get_table_names())
    if "quality_events" not in tables:
        op.create_table("quality_events",
            sa.Column("quality_event_id", sa.Text(), primary_key=True),
            sa.Column("order_id", sa.Text(), nullable=False),
            sa.Column("event_type", sa.Text(), nullable=False),
            sa.Column("status", sa.Text(), nullable=False, server_default="OPEN"),
            sa.Column("description", sa.Text()),
            sa.Column("is_delivery_blocking", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("rework_required", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("expected_resolution_at", sa.Text()), sa.Column("event_time", sa.Text()),
            sa.Column("source", sa.Text(), nullable=False, server_default="SYNTHETIC_OR_MANUAL"),
            sa.Column("source_message_id", sa.Text()), sa.Column("resolved_at", sa.Text()),
            sa.Column("created_at", sa.Text(), nullable=False), sa.Column("updated_at", sa.Text(), nullable=False),
            sa.ForeignKeyConstraint(["order_id"],["orders.order_id"]),
            sa.ForeignKeyConstraint(["source_message_id"],["source_messages.message_id"]))
        op.create_index("idx_quality_events_order","quality_events",["order_id","resolved_at"])
    tables = set(sa.inspect(bind).get_table_names())
    if "fact_conflicts" not in tables:
        op.create_table("fact_conflicts",
            sa.Column("conflict_id", sa.Text(), primary_key=True), sa.Column("order_id", sa.Text(), nullable=False),
            sa.Column("field_name", sa.Text(), nullable=False), sa.Column("source_a", sa.Text(), nullable=False),
            sa.Column("value_a", sa.Text()), sa.Column("source_b", sa.Text(), nullable=False), sa.Column("value_b", sa.Text()),
            sa.Column("status", sa.Text(), nullable=False, server_default="OPEN"), sa.Column("detected_at", sa.Text(), nullable=False),
            sa.Column("resolved_at", sa.Text()), sa.Column("created_at", sa.Text(), nullable=False), sa.Column("updated_at", sa.Text(), nullable=False),
            sa.ForeignKeyConstraint(["order_id"],["orders.order_id"]))
        op.create_index("idx_fact_conflicts_order","fact_conflicts",["order_id","resolved_at"])

def downgrade() -> None:
    bind = op.get_bind(); tables=set(sa.inspect(bind).get_table_names())
    if "fact_conflicts" in tables:
        op.drop_index("idx_fact_conflicts_order", table_name="fact_conflicts"); op.drop_table("fact_conflicts")
    tables=set(sa.inspect(bind).get_table_names())
    if "quality_events" in tables:
        op.drop_index("idx_quality_events_order", table_name="quality_events"); op.drop_table("quality_events")
