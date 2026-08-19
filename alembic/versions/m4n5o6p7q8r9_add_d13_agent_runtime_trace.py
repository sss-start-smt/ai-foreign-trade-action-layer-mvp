"""add D13 controlled Agent runtime trace

Revision ID: m4n5o6p7q8r9
Revises: l3m4n5o6p7q8
Create Date: 2026-08-17
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "m4n5o6p7q8r9"
down_revision: Union[str, None] = "l3m4n5o6p7q8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "d13_agent_runs",
        sa.Column("run_id", sa.String(), primary_key=True),
        sa.Column("organization_id", sa.String(), nullable=False),
        sa.Column("current_user_id", sa.String(), nullable=False),
        sa.Column("current_role", sa.String(), nullable=False),
        sa.Column("trigger_type", sa.String(), nullable=False),
        sa.Column("trigger_ref", sa.String(), nullable=True),
        sa.Column("goal", sa.Text(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="RUNNING"),
        sa.Column("stop_reason", sa.String(), nullable=True),
        sa.Column("skill_version", sa.String(), nullable=False),
        sa.Column("tool_contract_version", sa.String(), nullable=False),
        sa.Column("transcription_version", sa.String(), nullable=False),
        sa.Column("model_provider", sa.String(), nullable=True),
        sa.Column("model_name", sa.String(), nullable=True),
        sa.Column("system_current_datetime", sa.String(), nullable=False),
        sa.Column("timezone", sa.String(), nullable=False, server_default="Asia/Shanghai"),
        sa.Column("context_refs_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("tool_call_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("distinct_task_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("final_response", sa.Text(), nullable=True),
        sa.Column("external_effect_executed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("updated_at", sa.String(), nullable=False),
        sa.Column("completed_at", sa.String(), nullable=True),
    )
    op.create_index(
        "idx_d13_runs_org_user_time", "d13_agent_runs",
        ["organization_id", "current_user_id", "created_at"],
    )
    op.create_index(
        "idx_d13_runs_status", "d13_agent_runs",
        ["organization_id", "status", "created_at"],
    )
    op.create_table(
        "d13_agent_trace_events",
        sa.Column("event_id", sa.String(), primary_key=True),
        sa.Column("run_id", sa.String(), nullable=False),
        sa.Column("sequence_no", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(), nullable=False),
        sa.Column("tool_name", sa.String(), nullable=True),
        sa.Column("task_id", sa.String(), nullable=True),
        sa.Column("mode", sa.String(), nullable=True),
        sa.Column("request_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("response_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["d13_agent_runs.run_id"]),
        sa.UniqueConstraint("run_id", "sequence_no", name="uq_d13_trace_run_seq"),
    )
    op.create_index("idx_d13_trace_run_seq", "d13_agent_trace_events", ["run_id", "sequence_no"])


def downgrade() -> None:
    op.drop_index("idx_d13_trace_run_seq", table_name="d13_agent_trace_events")
    op.drop_table("d13_agent_trace_events")
    op.drop_index("idx_d13_runs_status", table_name="d13_agent_runs")
    op.drop_index("idx_d13_runs_org_user_time", table_name="d13_agent_runs")
    op.drop_table("d13_agent_runs")
