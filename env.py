"""Alembic environment configuration for FlowOrder.

Reads DATABASE_URL from environment, defines the full schema metadata
from schema.sql using SQLAlchemy Core Table definitions, and configures
migrations for PostgreSQL.
"""

from logging.config import fileConfig
import os
import sys
from pathlib import Path

from sqlalchemy import (
    engine_from_config,
    pool,
    MetaData,
    Table,
    Column,
    String,
    Integer,
    Float,
    DateTime,
    Text,
    Index,
    UniqueConstraint,
    ForeignKey,
    PrimaryKeyConstraint,
)

import sqlalchemy as sa

from alembic import context

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

_db_url = os.getenv("DATABASE_URL", "").strip()
if _db_url:
    config.set_main_option("sqlalchemy.url", _db_url)

target_metadata = MetaData()


def _build_schema_metadata() -> MetaData:
    meta = MetaData()

    # --- orders ---
    Table(
        "orders", meta,
        Column("order_id", String(), primary_key=True),
        Column("order_no", String(), unique=True, nullable=False),
        Column("customer_name", String()),
        Column("product_name", String()),
        Column("packaging_method", String()),
        Column("requested_delivery_date", String()),
        Column("latest_supplier_commitment", String()),
        Column("current_progress", Float()),
        Column("current_node", String()),
        Column("status", String(), nullable=False, server_default=sa.text("'ACTIVE'")),
        Column("owner", String()),
        Column("action_readiness", String(), nullable=False, server_default=sa.text("'BASE_ONLY'")),
        Column("contact_status", String(), nullable=False, server_default=sa.text("'UNKNOWN'")),
        Column("issue_status", String(), nullable=False, server_default=sa.text("'UNKNOWN'")),
        Column("initialization_waiting_on", String()),
        Column("initialization_promised_reply_at", String()),
        Column("initialization_note", String()),
        Column("initialization_source", String()),
        Column("initialized_at", String()),
        Column("last_dynamic_update_at", String()),
        Column("organization_id", String(), nullable=True),
        Column("created_at", String(), nullable=False),
        Column("updated_at", String(), nullable=False),
        Index("idx_orders_owner", "owner"),
        Index("idx_orders_action_readiness", "action_readiness", "requested_delivery_date"),
        Index("idx_orders_org", "organization_id"),
    )

    # --- source_messages ---
    Table(
        "source_messages", meta,
        Column("message_id", String(), primary_key=True),
        Column("order_id", String(), ForeignKey("orders.order_id")),
        Column("source_channel", String(), nullable=False),
        Column("sender_role", String(), nullable=False),
        Column("message_type", String()),
        Column("raw_content", String(), nullable=False),
        Column("source_time", String()),
        Column("organization_id", String(), nullable=False),
        Column("created_at", String(), nullable=False),
        Index("idx_source_messages_org", "organization_id"),
    )

    # --- tasks ---
    Table(
        "tasks", meta,
        Column("task_id", String(), primary_key=True),
        Column("related_order_id", String(), ForeignKey("orders.order_id")),
        Column("title", String(), nullable=False),
        Column("recommended_action", String()),
        Column("target", String()),
        Column("status", String(), nullable=False, server_default=sa.text("'OPEN'")),
        Column("owner_user_id", String()),
        Column("responsibility_status", String(), nullable=False, server_default=sa.text("'assigned'")),
        Column("waiting_on", String()),
        Column("promised_reply_at", String()),
        Column("next_action_at", String()),
        Column("business_deadline", String()),
        Column("last_contact_at", String()),
        Column("risk_level", String(), nullable=False, server_default=sa.text("'none'")),
        Column("urgent", Integer(), nullable=False, server_default=sa.text("0")),
        Column("pending_confirmation", Integer(), nullable=False, server_default=sa.text("0")),
        Column("source_message_id", String(), ForeignKey("source_messages.message_id")),
        Column("evidence_json", Text(), nullable=False, server_default=sa.text("'[]'")),
        Column("organization_id", String(), nullable=True),
        Column("created_at", String(), nullable=False),
        Column("updated_at", String(), nullable=False),
        Index("idx_tasks_order", "related_order_id"),
        Index("idx_tasks_owner", "owner_user_id"),
        Index("idx_tasks_waiting", "waiting_on", "promised_reply_at"),
        Index("idx_tasks_org", "organization_id"),
    )

    # --- risk_signals ---
    Table(
        "risk_signals", meta,
        Column("risk_id", String(), primary_key=True),
        Column("order_id", String(), ForeignKey("orders.order_id")),
        Column("task_id", String(), ForeignKey("tasks.task_id")),
        Column("risk_type", String(), nullable=False),
        Column("risk_level", String(), nullable=False),
        Column("evidence", String()),
        Column("rule_id", String()),
        Column("status", String(), nullable=False, server_default=sa.text("'OPEN'")),
        Column("created_at", String(), nullable=False),
        Column("updated_at", String(), nullable=False),
        Index("idx_risks_order", "order_id"),
    )

    # --- commitment_history ---
    Table(
        "commitment_history", meta,
        Column("commitment_id", String(), primary_key=True),
        Column("order_id", String(), ForeignKey("orders.order_id"), nullable=False),
        Column("commitment_type", String(), nullable=False),
        Column("commitment_value", String(), nullable=False),
        Column("source_message_id", String(), ForeignKey("source_messages.message_id")),
        Column("confirmed_by", String()),
        Column("created_at", String(), nullable=False),
    )

    # --- confirmation_snapshots ---
    Table(
        "confirmation_snapshots", meta,
        Column("confirmation_id", String(), primary_key=True),
        Column("idempotency_key", String(), nullable=False),
        Column("operator_id", String()),
        Column("payload_json", Text(), nullable=False),
        Column("created_at", String(), nullable=False),
    )

    # --- event_logs ---
    Table(
        "event_logs", meta,
        Column("event_id", String(), primary_key=True),
        Column("entity_type", String(), nullable=False),
        Column("entity_id", String()),
        Column("event_type", String(), nullable=False),
        Column("payload_json", Text(), nullable=False),
        Column("operator_id", String()),
        Column("organization_id", String(), nullable=True),
        Column("created_at", String(), nullable=False),
        Index("idx_events_entity", "entity_type", "entity_id"),
        Index("idx_events_org", "organization_id"),
    )

    # --- idempotency_records ---
    Table(
        "idempotency_records", meta,
        Column("idempotency_key", String(), primary_key=True),
        Column("result_status", String(), nullable=False),
        Column("result_json", Text(), nullable=False),
        Column("created_at", String(), nullable=False),
    )

    # --- candidate_reviews ---
    Table(
        "candidate_reviews", meta,
        Column("review_id", String(), primary_key=True),
        Column("source_message_id", String(), ForeignKey("source_messages.message_id")),
        Column("order_id", String(), ForeignKey("orders.order_id")),
        Column("organization_id", String(), nullable=False),
        Column("workflow_source", String(), nullable=False, server_default=sa.text("'LOCAL_RULE_DEMO'")),
        Column("candidate_json", Text(), nullable=False),
        Column("status", String(), nullable=False, server_default=sa.text("'PENDING'")),
        Column("reviewer_id", String()),
        Column("created_at", String(), nullable=False),
        Column("reviewed_at", String()),
        Index("idx_reviews_status", "status", "created_at"),
        Index("idx_reviews_order", "order_id"),
        Index("idx_reviews_org", "organization_id"),
    )

    # --- user_settings ---
    Table(
        "user_settings", meta,
        Column("user_id", String(), primary_key=True),
        Column("settings_json", Text(), nullable=False),
        Column("updated_at", String(), nullable=False),
    )

    # --- workflow_runs ---
    Table(
        "workflow_runs", meta,
        Column("run_id", String(), primary_key=True),
        Column("workflow_key", String(), nullable=False),
        Column("workflow_id", String(), nullable=False),
        Column("status", String(), nullable=False),
        Column("input_json", Text(), nullable=False),
        Column("output_json", Text()),
        Column("coze_code", Integer()),
        Column("coze_msg", String()),
        Column("debug_url", String()),
        Column("duration_ms", Integer()),
        Column("created_at", String(), nullable=False),
        Index("idx_workflow_runs_key_time", "workflow_key", "created_at"),
    )

    # --- task_rankings ---
    Table(
        "task_rankings", meta,
        Column("current_user_id", String(), nullable=False),
        Column("task_id", String(), ForeignKey("tasks.task_id"), nullable=False),
        Column("action_state", String(), nullable=False),
        Column("recommended_action", String()),
        Column("target", String()),
        Column("next_action_at", String()),
        Column("ranking_suppressed", Integer(), nullable=False, server_default=sa.text("0")),
        Column("priority_score", Float(), nullable=False, server_default=sa.text("0")),
        Column("priority_reasons_json", Text(), nullable=False, server_default=sa.text("'[]'")),
        Column("evidence_json", Text(), nullable=False, server_default=sa.text("'[]'")),
        Column("workflow_run_id", String(), ForeignKey("workflow_runs.run_id")),
        Column("calculated_at", String(), nullable=False),
        PrimaryKeyConstraint("current_user_id", "task_id"),
        Index("idx_rankings_user_score", "current_user_id", sa.desc("priority_score")),
    )

    # --- intake_jobs ---
    Table(
        "intake_jobs", meta,
        Column("job_id", String(), primary_key=True),
        Column("organization_id", String(), nullable=False),
        Column("status", String(), nullable=False),
        Column("workflow_key", String(), nullable=False),
        Column("order_id", String(), ForeignKey("orders.order_id")),
        Column("request_json", Text(), nullable=False),
        Column("result_json", Text()),
        Column("error_json", Text()),
        Column("review_id", String()),
        Column("message_id", String()),
        Column("progress_message", String()),
        Column("created_at", String(), nullable=False),
        Column("started_at", String()),
        Column("completed_at", String()),
        Column("updated_at", String(), nullable=False),
        Index("idx_intake_jobs_status_time", "status", "created_at"),
        Index("idx_intake_jobs_org", "organization_id"),
    )

    # --- order_dependencies ---
    Table(
        "order_dependencies", meta,
        Column("dependency_id", String(), primary_key=True),
        Column("order_id", String(), ForeignKey("orders.order_id"), nullable=False),
        Column("dependency_type", String(), nullable=False),
        Column("dependency_name", String(), nullable=False),
        Column("sequence_no", Integer(), nullable=False, server_default=sa.text("0")),
        Column("status", String(), nullable=False, server_default=sa.text("'PENDING'")),
        Column("blocking_party", String()),
        Column("due_at", String()),
        Column("evidence_json", Text(), nullable=False, server_default=sa.text("'[]'")),
        Column("created_at", String(), nullable=False),
        Column("updated_at", String(), nullable=False),
        Index("idx_dependencies_order_status", "order_id", "status"),
    )

    # --- logistics_events ---
    Table(
        "logistics_events", meta,
        Column("logistics_event_id", String(), primary_key=True),
        Column("order_id", String(), ForeignKey("orders.order_id"), nullable=False),
        Column("event_type", String(), nullable=False),
        Column("status", String(), nullable=False),
        Column("location", String()),
        Column("description", String()),
        Column("event_time", String()),
        Column("estimated_arrival_at", String()),
        Column("source", String(), nullable=False, server_default=sa.text("'SYNTHETIC_OR_MANUAL'")),
        Column("resolved_at", String()),
        Column("created_at", String(), nullable=False),
        Column("updated_at", String(), nullable=False),
        Index("idx_logistics_order_status", "order_id", "status", "event_time"),
    )

    # --- agent_chat_jobs ---
    Table(
        "agent_chat_jobs", meta,
        Column("job_id", String(), primary_key=True),
        Column("organization_id", String(), nullable=False, server_default=sa.text("'ORG-DEMO'")),
        Column("current_user_id", String(), nullable=False),
        Column("current_role", String(), nullable=False),
        Column("question", String(), nullable=False),
        Column("status", String(), nullable=False, server_default=sa.text("'QUEUED'")),
        Column("request_json", Text(), nullable=False, server_default=sa.text("'{}'")),
        Column("result_json", Text()),
        Column("error_message", String()),
        Column("conversation_id", String()),
        Column("linked_run_id", String()),
        Column("duration_ms", Integer()),
        Column("created_at", String(), nullable=False),
        Column("started_at", String()),
        Column("completed_at", String()),
        Column("updated_at", String(), nullable=False),
        Index("idx_agent_chat_jobs_user_status", "current_user_id", "status", "created_at"),
    )

    # --- agent_runs ---
    Table(
        "agent_runs", meta,
        Column("run_id", String(), primary_key=True),
        Column("organization_id", String(), nullable=False, server_default=sa.text("'ORG-DEMO'")),
        Column("current_user_id", String(), nullable=False),
        Column("current_role", String(), nullable=False),
        Column("goal", String(), nullable=False),
        Column("trigger_type", String(), nullable=False, server_default=sa.text("'USER'")),
        Column("status", String(), nullable=False),
        Column("max_tool_calls", Integer(), nullable=False, server_default=sa.text("8")),
        Column("max_duration_seconds", Integer(), nullable=False, server_default=sa.text("60")),
        Column("result_json", Text()),
        Column("stop_reason", String()),
        Column("duration_ms", Integer()),
        Column("started_at", String()),
        Column("completed_at", String()),
        Column("created_at", String(), nullable=False),
        Index("idx_agent_runs_user_time", "current_user_id", sa.desc("created_at")),
    )

    # --- agent_tool_calls ---
    Table(
        "agent_tool_calls", meta,
        Column("call_id", String(), primary_key=True),
        Column("run_id", String(), ForeignKey("agent_runs.run_id")),
        Column("tool_name", String(), nullable=False),
        Column("request_json", Text(), nullable=False),
        Column("response_json", Text()),
        Column("status", String(), nullable=False),
        Column("error_code", String()),
        Column("error_message", String()),
        Column("duration_ms", Integer()),
        Column("created_at", String(), nullable=False),
        Index("idx_agent_calls_run_time", "run_id", "created_at"),
    )

    # --- anomaly_candidates ---
    Table(
        "anomaly_candidates", meta,
        Column("candidate_id", String(), primary_key=True),
        Column("run_id", String(), ForeignKey("agent_runs.run_id")),
        Column("order_id", String(), ForeignKey("orders.order_id"), nullable=False),
        Column("anomaly_type", String(), nullable=False),
        Column("severity", String(), nullable=False),
        Column("confidence", Float(), nullable=False, server_default=sa.text("0")),
        Column("score", Float(), nullable=False, server_default=sa.text("0")),
        Column("evidence_json", Text(), nullable=False, server_default=sa.text("'[]'")),
        Column("missing_information_json", Text(), nullable=False, server_default=sa.text("'[]'")),
        Column("recommended_action", String()),
        Column("status", String(), nullable=False, server_default=sa.text("'ANOMALY_CANDIDATE'")),
        Column("created_by", String()),
        Column("confirmed_by", String()),
        Column("confirmed_at", String()),
        Column("resolution_note", String()),
        Column("created_at", String(), nullable=False),
        Column("updated_at", String(), nullable=False),
        Index("idx_anomalies_order_status", "order_id", "status", "severity"),
    )

    # --- approval_requests ---
    Table(
        "approval_requests", meta,
        Column("approval_id", String(), primary_key=True),
        Column("run_id", String(), ForeignKey("agent_runs.run_id")),
        Column("candidate_id", String(), ForeignKey("anomaly_candidates.candidate_id")),
        Column("order_id", String(), ForeignKey("orders.order_id")),
        Column("action_type", String(), nullable=False),
        Column("payload_json", Text(), nullable=False),
        Column("status", String(), nullable=False, server_default=sa.text("'PENDING'")),
        Column("requested_by", String(), nullable=False),
        Column("required_role", String(), nullable=False, server_default=sa.text("'operator_or_manager'")),
        Column("idempotency_key", String(), unique=True, nullable=False),
        Column("organization_id", String(), nullable=True),
        Column("decided_by", String()),
        Column("decision_note", String()),
        Column("decided_at", String()),
        Column("result_json", Text()),
        Column("created_at", String(), nullable=False),
        Column("updated_at", String(), nullable=False),
        Index("idx_approvals_status_role", "status", "required_role", "created_at"),
        Index("idx_approvals_org", "organization_id"),
    )

    # --- daily_inspection_reports ---
    Table(
        "daily_inspection_reports", meta,
        Column("report_id", String(), primary_key=True),
        Column("run_id", String(), ForeignKey("agent_runs.run_id")),
        Column("organization_id", String(), nullable=False, server_default=sa.text("'ORG-DEMO'")),
        Column("current_user_id", String(), nullable=False),
        Column("inspection_date", String(), nullable=False),
        Column("timezone", String(), nullable=False, server_default=sa.text("'Asia/Shanghai'")),
        Column("scope_json", Text(), nullable=False),
        Column("report_json", Text(), nullable=False),
        Column("status", String(), nullable=False),
        Column("created_at", String(), nullable=False),
        Index("idx_reports_user_date", "current_user_id", sa.desc("inspection_date")),
    )

    # --- bulk_update_batches ---
    Table(
        "bulk_update_batches", meta,
        Column("batch_id", String(), primary_key=True),
        Column("organization_id", String(), nullable=False, server_default=sa.text("'ORG-DEMO'")),
        Column("current_user_id", String(), nullable=False),
        Column("current_role", String(), nullable=False),
        Column("source_text", String(), nullable=False),
        Column("parser_mode", String(), nullable=False, server_default=sa.text("'hybrid_rules_v1'")),
        Column("status", String(), nullable=False, server_default=sa.text("'PARSED'")),
        Column("summary_json", Text(), nullable=False, server_default=sa.text("'{}'")),
        Column("created_at", String(), nullable=False),
        Column("confirmed_at", String()),
    )

    # --- bulk_update_candidates ---
    Table(
        "bulk_update_candidates", meta,
        Column("update_id", String(), primary_key=True),
        Column("batch_id", String(), ForeignKey("bulk_update_batches.batch_id"), nullable=False),
        Column("order_id", String(), ForeignKey("orders.order_id")),
        Column("order_no", String()),
        Column("source_segment", String(), nullable=False),
        Column("match_confidence", Float(), nullable=False, server_default=sa.text("0")),
        Column("field_name", String(), nullable=False),
        Column("old_value_json", Text()),
        Column("new_value_json", Text()),
        Column("confidence", Float(), nullable=False, server_default=sa.text("0")),
        Column("risk_level", String(), nullable=False, server_default=sa.text("'normal'")),
        Column("requires_approval", Integer(), nullable=False, server_default=sa.text("0")),
        Column("status", String(), nullable=False, server_default=sa.text("'PENDING'")),
        Column("edited_value_json", Text()),
        Column("approval_id", String()),
        Column("created_at", String(), nullable=False),
        Column("decided_at", String()),
        Index("idx_bulk_update_batch_status", "batch_id", "status"),
        Index("idx_bulk_update_order", "order_id", sa.desc("created_at")),
    )

    # --- analytics_events ---
    Table(
        "analytics_events", meta,
        Column("event_id", String(), primary_key=True),
        Column("event_name", String(), nullable=False),
        Column("organization_id", String()),
        Column("user_id", String()),
        Column("user_role", String()),
        Column("session_id", String()),
        Column("order_id", String()),
        Column("run_id", String()),
        Column("source", String(), nullable=False, server_default=sa.text("'server'")),
        Column("app_version", String(), nullable=False, server_default=sa.text("'6.1.0'")),
        Column("properties_json", Text(), nullable=False, server_default=sa.text("'{}'")),
        Column("client_timestamp", String()),
        Column("server_timestamp", String(), nullable=False),
        Index("idx_analytics_event_time", "event_name", sa.desc("server_timestamp")),
        Index("idx_analytics_user_time", "user_id", sa.desc("server_timestamp")),
        Index("idx_analytics_run", "run_id", sa.desc("server_timestamp")),
        Index("idx_analytics_order", "order_id", sa.desc("server_timestamp")),
    )

    # --- communication_events ---
    Table(
        "communication_events", meta,
        Column("event_id", String(), primary_key=True),
        Column("entity_type", String(), nullable=False),
        Column("entity_id", String(), nullable=False),
        Column("event_type", String(), nullable=False),
        Column("payload_json", Text(), nullable=False),
        Column("operator_id", String()),
        Column("created_at", String(), nullable=False),
        Index("idx_comm_events_entity", "entity_type", "entity_id", "created_at"),
    )

    # --- communication_workflow_runs ---
    Table(
        "communication_workflow_runs", meta,
        Column("run_id", String(), primary_key=True),
        Column("workflow_code", String(), nullable=False),
        Column("workflow_id", String()),
        Column("request_id", String()),
        Column("status", String(), nullable=False),
        Column("input_json", Text(), nullable=False),
        Column("output_json", Text()),
        Column("error_code", String()),
        Column("error_message", String()),
        Column("debug_url", String()),
        Column("duration_ms", Integer()),
        Column("created_at", String(), nullable=False),
        Index("idx_comm_runs_workflow", "workflow_code", "created_at"),
    )

    # --- communication_drafts ---
    Table(
        "communication_drafts", meta,
        Column("draft_id", String(), primary_key=True),
        Column("request_id", String(), unique=True, nullable=False),
        Column("order_id", String()),
        Column("order_no", String()),
        Column("draft_type", String(), nullable=False),
        Column("recipient_role", String(), nullable=False),
        Column("channel", String()),
        Column("result_json", Text(), nullable=False),
        Column("ai_subject", String()),
        Column("ai_draft", Text()),
        Column("edited_subject", String()),
        Column("edited_draft", Text()),
        Column("final_text", Text()),
        Column("facts_used_json", Text(), server_default=sa.text("'[]'"), nullable=False),
        Column("missing_facts_json", Text(), server_default=sa.text("'[]'"), nullable=False),
        Column("questions_to_ask_json", Text(), server_default=sa.text("'[]'"), nullable=False),
        Column("risk_flags_json", Text(), server_default=sa.text("'[]'"), nullable=False),
        Column("run_status", String(), nullable=False),
        Column("approval_status", String()),
        Column("human_status", String(), server_default=sa.text("'PENDING'"), nullable=False),
        Column("reviewer_id", String()),
        Column("review_note", String()),
        Column("approved_at", String()),
        Column("copied_at", String()),
        Column("created_at", String(), nullable=False),
        Column("updated_at", String(), nullable=False),
        Index("idx_comm_drafts_order", "order_id", "order_no"),
        Index("idx_comm_drafts_status", "human_status", "run_status"),
    )

    # --- communication_task_candidates ---
    Table(
        "communication_task_candidates", meta,
        Column("candidate_id", String(), primary_key=True),
        Column("request_id", String(), unique=True, nullable=False),
        Column("source_message_id", String()),
        Column("order_id", String()),
        Column("order_no", String()),
        Column("communication_text", String(), nullable=False),
        Column("sender_role", String()),
        Column("channel", String()),
        Column("result_json", Text(), nullable=False),
        Column("task_candidate_json", Text(), nullable=False),
        Column("run_status", String(), nullable=False),
        Column("review_status", String(), server_default=sa.text("'PENDING'"), nullable=False),
        Column("reviewer_id", String()),
        Column("review_note", String()),
        Column("ft03_result_json", Text()),
        Column("created_at", String(), nullable=False),
        Column("reviewed_at", String()),
        Index("idx_comm_candidates_order", "order_id", "order_no"),
        Index("idx_comm_candidates_status", "review_status", "run_status"),
    )

    # --- order_import_batches ---
    Table(
        "order_import_batches", meta,
        Column("batch_id", String(), primary_key=True),
        Column("source_filename", String(), nullable=False),
        Column("source_sha256", String(), nullable=False),
        Column("status", String(), nullable=False),
        Column("total_rows", Integer(), server_default=sa.text("'0'"), nullable=False),
        Column("importable_rows", Integer(), server_default=sa.text("'0'"), nullable=False),
        Column("error_rows", Integer(), server_default=sa.text("'0'"), nullable=False),
        Column("mapping_json", Text(), nullable=False),
        Column("summary_json", Text(), nullable=False),
        Column("created_at", String(), nullable=False),
        Column("committed_at", String()),
    )

    # --- order_import_rows ---
    Table(
        "order_import_rows", meta,
        Column("row_id", String(), primary_key=True),
        Column("batch_id", String(), ForeignKey("order_import_batches.batch_id"), nullable=False),
        Column("row_number", Integer(), nullable=False),
        Column("raw_json", Text(), nullable=False),
        Column("normalized_json", Text(), nullable=False),
        Column("classification", String(), nullable=False),
        Column("issues_json", Text(), nullable=False),
        Column("changes_json", Text(), nullable=False),
        Column("existing_order_id", String()),
        Column("commit_status", String()),
        Column("commit_message", String()),
        Index("idx_order_import_rows_batch", "batch_id"),
        Index("idx_order_import_rows_classification", "classification"),
    )

    # --- audit_logs ---
    Table(
        "audit_logs", meta,
        Column("audit_id", String(), primary_key=True),
        Column("organization_id", String(), nullable=False),
        Column("actor_user_id", String(), nullable=False),
        Column("actor_role", String(), nullable=False),
        Column("action", String(), nullable=False),
        Column("entity_type", String(), nullable=False),
        Column("entity_id", String(), nullable=False),
        Column("result", String(), nullable=False, server_default=sa.text("'SUCCESS'")),
        Column("details_json", Text(), nullable=False),
        Column("created_at", String(), nullable=False),
        Index("idx_audit_org", "organization_id"),
        Index("idx_audit_actor", "actor_user_id"),
        Index("idx_audit_action", "action"),
    )

    # --- erp_sync_state ---
    Table(
        "erp_sync_state", meta,
        Column("organization_id", String(), nullable=False),
        Column("doctype", String(), nullable=False),
        Column("last_success_cursor", String(), nullable=True),
        Column("last_success_at", String(), nullable=True),
        Column("last_attempt_at", String(), nullable=True),
        Column("sync_status", String(), nullable=False, server_default=sa.text("'NEVER_SYNCED'")),
        Column("last_error_code", String(), nullable=True),
        Column("records_seen", Integer(), nullable=False, server_default=sa.text("0")),
        Column("records_changed", Integer(), nullable=False, server_default=sa.text("0")),
        Column("updated_at", String(), nullable=False),
        PrimaryKeyConstraint("organization_id", "doctype"),
        Index("idx_erp_sync_state_org", "organization_id"),
        Index("idx_erp_sync_state_status", "sync_status"),
    )

    # --- erp_read_snapshots ---
    Table(
        "erp_read_snapshots", meta,
        Column("snapshot_id", String(), primary_key=True),
        Column("organization_id", String(), nullable=False),
        Column("doctype", String(), nullable=False),
        Column("external_id", String(), nullable=False),
        Column("source_modified_at", String(), nullable=True),
        Column("normalized_json", Text(), nullable=False),
        Column("raw_sha256", String(), nullable=False),
        Column("fetched_at", String(), nullable=False),
        Column("created_at", String(), nullable=False),
        Column("updated_at", String(), nullable=False),
        UniqueConstraint("organization_id", "doctype", "external_id"),
        Index("idx_erp_snapshots_org", "organization_id", "doctype"),
        Index("idx_erp_snapshots_external", "doctype", "external_id"),
        Index("idx_erp_snapshots_fetched", "fetched_at"),
    )

    return meta


target_metadata = _build_schema_metadata()


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection, target_metadata=target_metadata
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()