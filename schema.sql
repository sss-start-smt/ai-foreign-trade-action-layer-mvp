PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS orders (
    order_id TEXT PRIMARY KEY,
    order_no TEXT UNIQUE NOT NULL,
    customer_name TEXT,
    product_name TEXT,
    packaging_method TEXT,
    requested_delivery_date TEXT,
    latest_supplier_commitment TEXT,
    current_progress REAL,
    current_node TEXT,
    status TEXT NOT NULL DEFAULT 'ACTIVE',
    owner TEXT,
    organization_id TEXT,
    action_readiness TEXT NOT NULL DEFAULT 'BASE_ONLY',
    contact_status TEXT NOT NULL DEFAULT 'UNKNOWN',
    issue_status TEXT NOT NULL DEFAULT 'UNKNOWN',
    initialization_waiting_on TEXT,
    initialization_promised_reply_at TEXT,
    initialization_note TEXT,
    initialization_source TEXT,
    initialized_at TEXT,
    last_dynamic_update_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS source_messages (
    message_id TEXT PRIMARY KEY,
    order_id TEXT,
    organization_id TEXT NOT NULL,
    source_channel TEXT NOT NULL,
    sender_role TEXT NOT NULL,
    message_type TEXT,
    raw_content TEXT NOT NULL,
    source_time TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY(order_id) REFERENCES orders(order_id)
);

CREATE TABLE IF NOT EXISTS tasks (
    task_id TEXT PRIMARY KEY,
    related_order_id TEXT,
    title TEXT NOT NULL,
    recommended_action TEXT,
    target TEXT,
    status TEXT NOT NULL DEFAULT 'OPEN',
    owner_user_id TEXT,
    responsibility_status TEXT NOT NULL DEFAULT 'assigned',
    waiting_on TEXT,
    promised_reply_at TEXT,
    next_action_at TEXT,
    business_deadline TEXT,
    last_contact_at TEXT,
    risk_level TEXT NOT NULL DEFAULT 'none',
    urgent INTEGER NOT NULL DEFAULT 0,
    pending_confirmation INTEGER NOT NULL DEFAULT 0,
    source_message_id TEXT,
    evidence_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(related_order_id) REFERENCES orders(order_id),
    FOREIGN KEY(source_message_id) REFERENCES source_messages(message_id)
);

CREATE TABLE IF NOT EXISTS risk_signals (
    risk_id TEXT PRIMARY KEY,
    order_id TEXT,
    task_id TEXT,
    risk_type TEXT NOT NULL,
    risk_level TEXT NOT NULL,
    evidence TEXT,
    rule_id TEXT,
    status TEXT NOT NULL DEFAULT 'OPEN',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(order_id) REFERENCES orders(order_id),
    FOREIGN KEY(task_id) REFERENCES tasks(task_id)
);

CREATE TABLE IF NOT EXISTS commitment_history (
    commitment_id TEXT PRIMARY KEY,
    order_id TEXT NOT NULL,
    commitment_type TEXT NOT NULL,
    commitment_value TEXT NOT NULL,
    source_message_id TEXT,
    confirmed_by TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY(order_id) REFERENCES orders(order_id),
    FOREIGN KEY(source_message_id) REFERENCES source_messages(message_id)
);

CREATE TABLE IF NOT EXISTS confirmation_snapshots (
    confirmation_id TEXT PRIMARY KEY,
    idempotency_key TEXT NOT NULL,
    operator_id TEXT,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS event_logs (
    event_id TEXT PRIMARY KEY,
    entity_type TEXT NOT NULL,
    entity_id TEXT,
    event_type TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    operator_id TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS idempotency_records (
    idempotency_key TEXT PRIMARY KEY,
    result_status TEXT NOT NULL,
    result_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_tasks_order ON tasks(related_order_id);
CREATE INDEX IF NOT EXISTS idx_tasks_owner ON tasks(owner_user_id);
CREATE INDEX IF NOT EXISTS idx_orders_owner ON orders(owner);
CREATE INDEX IF NOT EXISTS idx_orders_org ON orders(organization_id);
CREATE INDEX IF NOT EXISTS idx_tasks_waiting ON tasks(waiting_on, promised_reply_at);
CREATE INDEX IF NOT EXISTS idx_risks_order ON risk_signals(order_id);
CREATE INDEX IF NOT EXISTS idx_events_entity ON event_logs(entity_type, entity_id);
CREATE INDEX IF NOT EXISTS idx_orders_action_readiness ON orders(action_readiness, requested_delivery_date);


CREATE TABLE IF NOT EXISTS candidate_reviews (
    review_id TEXT PRIMARY KEY,
    source_message_id TEXT,
    order_id TEXT,
    organization_id TEXT NOT NULL,
    workflow_source TEXT NOT NULL DEFAULT 'LOCAL_RULE_DEMO',
    candidate_json TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'PENDING',
    reviewer_id TEXT,
    created_at TEXT NOT NULL,
    reviewed_at TEXT,
    FOREIGN KEY(source_message_id) REFERENCES source_messages(message_id),
    FOREIGN KEY(order_id) REFERENCES orders(order_id)
);

CREATE TABLE IF NOT EXISTS user_settings (
    user_id TEXT PRIMARY KEY,
    settings_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_reviews_status ON candidate_reviews(status, created_at);
CREATE INDEX IF NOT EXISTS idx_reviews_order ON candidate_reviews(order_id);

CREATE TABLE IF NOT EXISTS workflow_runs (
    run_id TEXT PRIMARY KEY,
    workflow_key TEXT NOT NULL,
    workflow_id TEXT NOT NULL,
    status TEXT NOT NULL,
    input_json TEXT NOT NULL,
    output_json TEXT,
    coze_code INTEGER,
    coze_msg TEXT,
    debug_url TEXT,
    duration_ms INTEGER,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS task_rankings (
    current_user_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    action_state TEXT NOT NULL,
    recommended_action TEXT,
    target TEXT,
    next_action_at TEXT,
    ranking_suppressed INTEGER NOT NULL DEFAULT 0,
    priority_score REAL NOT NULL DEFAULT 0,
    priority_reasons_json TEXT NOT NULL DEFAULT '[]',
    evidence_json TEXT NOT NULL DEFAULT '[]',
    workflow_run_id TEXT,
    calculated_at TEXT NOT NULL,
    PRIMARY KEY(current_user_id, task_id),
    FOREIGN KEY(task_id) REFERENCES tasks(task_id),
    FOREIGN KEY(workflow_run_id) REFERENCES workflow_runs(run_id)
);

CREATE INDEX IF NOT EXISTS idx_workflow_runs_key_time ON workflow_runs(workflow_key, created_at);
CREATE INDEX IF NOT EXISTS idx_rankings_user_score ON task_rankings(current_user_id, priority_score DESC);


CREATE TABLE IF NOT EXISTS intake_jobs (
    job_id TEXT PRIMARY KEY,
    organization_id TEXT NOT NULL,
    status TEXT NOT NULL,
    workflow_key TEXT NOT NULL,
    order_id TEXT,
    request_json TEXT NOT NULL,
    result_json TEXT,
    error_json TEXT,
    review_id TEXT,
    message_id TEXT,
    progress_message TEXT,
    created_at TEXT NOT NULL,
    started_at TEXT,
    completed_at TEXT,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(order_id) REFERENCES orders(order_id)
);

CREATE INDEX IF NOT EXISTS idx_intake_jobs_status_time ON intake_jobs(status, created_at);

-- FlowOrder Agent V6.0 tables
CREATE TABLE IF NOT EXISTS order_dependencies (
    dependency_id TEXT PRIMARY KEY,
    order_id TEXT NOT NULL,
    dependency_type TEXT NOT NULL,
    dependency_name TEXT NOT NULL,
    sequence_no INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'PENDING',
    blocking_party TEXT,
    due_at TEXT,
    evidence_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(order_id) REFERENCES orders(order_id)
);

CREATE TABLE IF NOT EXISTS logistics_events (
    logistics_event_id TEXT PRIMARY KEY,
    order_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    status TEXT NOT NULL,
    location TEXT,
    description TEXT,
    event_time TEXT,
    estimated_arrival_at TEXT,
    source TEXT NOT NULL DEFAULT 'SYNTHETIC_OR_MANUAL',
    resolved_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(order_id) REFERENCES orders(order_id)
);

-- D14 quality fact contract: only structured blocking events create QUALITY_BLOCKING.
-- Free-text messages are NOT parsed into quality risk automatically.
CREATE TABLE IF NOT EXISTS quality_events (
    quality_event_id TEXT PRIMARY KEY,
    order_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'OPEN',
    description TEXT,
    is_delivery_blocking INTEGER NOT NULL DEFAULT 0,
    rework_required INTEGER NOT NULL DEFAULT 0,
    expected_resolution_at TEXT,
    event_time TEXT,
    source TEXT NOT NULL DEFAULT 'SYNTHETIC_OR_MANUAL',
    source_message_id TEXT,
    resolved_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(order_id) REFERENCES orders(order_id),
    FOREIGN KEY(source_message_id) REFERENCES source_messages(message_id)
);

CREATE INDEX IF NOT EXISTS idx_quality_events_order ON quality_events(order_id, resolved_at);

-- D14 R6 internal fact-conflict contract. Trusted structured facts only; no text parsing.
CREATE TABLE IF NOT EXISTS fact_conflicts (
    conflict_id TEXT PRIMARY KEY,
    order_id TEXT NOT NULL,
    field_name TEXT NOT NULL,
    source_a TEXT NOT NULL,
    value_a TEXT,
    source_b TEXT NOT NULL,
    value_b TEXT,
    status TEXT NOT NULL DEFAULT 'OPEN',
    detected_at TEXT NOT NULL,
    resolved_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(order_id) REFERENCES orders(order_id)
);
CREATE INDEX IF NOT EXISTS idx_fact_conflicts_order ON fact_conflicts(order_id, resolved_at);


CREATE TABLE IF NOT EXISTS agent_chat_jobs (
    job_id TEXT PRIMARY KEY,
    organization_id TEXT NOT NULL DEFAULT 'ORG-DEMO',
    current_user_id TEXT NOT NULL,
    current_role TEXT NOT NULL,
    question TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'QUEUED',
    request_json TEXT NOT NULL DEFAULT '{}',
    result_json TEXT,
    error_message TEXT,
    conversation_id TEXT,
    linked_run_id TEXT,
    duration_ms INTEGER,
    created_at TEXT NOT NULL,
    started_at TEXT,
    completed_at TEXT,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_agent_chat_jobs_user_status
ON agent_chat_jobs(current_user_id, status, created_at);

CREATE TABLE IF NOT EXISTS agent_runs (
    run_id TEXT PRIMARY KEY,
    organization_id TEXT NOT NULL DEFAULT 'ORG-DEMO',
    current_user_id TEXT NOT NULL,
    current_role TEXT NOT NULL,
    goal TEXT NOT NULL,
    trigger_type TEXT NOT NULL DEFAULT 'USER',
    status TEXT NOT NULL,
    max_tool_calls INTEGER NOT NULL DEFAULT 8,
    max_duration_seconds INTEGER NOT NULL DEFAULT 60,
    result_json TEXT,
    stop_reason TEXT,
    duration_ms INTEGER,
    started_at TEXT,
    completed_at TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS agent_tool_calls (
    call_id TEXT PRIMARY KEY,
    run_id TEXT,
    tool_name TEXT NOT NULL,
    request_json TEXT NOT NULL,
    response_json TEXT,
    status TEXT NOT NULL,
    error_code TEXT,
    error_message TEXT,
    duration_ms INTEGER,
    created_at TEXT NOT NULL,
    FOREIGN KEY(run_id) REFERENCES agent_runs(run_id)
);

CREATE TABLE IF NOT EXISTS anomaly_candidates (
    candidate_id TEXT PRIMARY KEY,
    run_id TEXT,
    order_id TEXT NOT NULL,
    anomaly_type TEXT NOT NULL,
    severity TEXT NOT NULL,
    confidence REAL NOT NULL DEFAULT 0,
    score REAL NOT NULL DEFAULT 0,
    evidence_json TEXT NOT NULL DEFAULT '[]',
    missing_information_json TEXT NOT NULL DEFAULT '[]',
    recommended_action TEXT,
    status TEXT NOT NULL DEFAULT 'ANOMALY_CANDIDATE',
    created_by TEXT,
    confirmed_by TEXT,
    confirmed_at TEXT,
    resolution_note TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(run_id) REFERENCES agent_runs(run_id),
    FOREIGN KEY(order_id) REFERENCES orders(order_id)
);

CREATE TABLE IF NOT EXISTS approval_requests (
    approval_id TEXT PRIMARY KEY,
    run_id TEXT,
    candidate_id TEXT,
    order_id TEXT,
    action_type TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'PENDING',
    requested_by TEXT NOT NULL,
    required_role TEXT NOT NULL DEFAULT 'operator_or_manager',
    idempotency_key TEXT UNIQUE NOT NULL,
    decided_by TEXT,
    decision_note TEXT,
    decided_at TEXT,
    result_json TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(run_id) REFERENCES agent_runs(run_id),
    FOREIGN KEY(candidate_id) REFERENCES anomaly_candidates(candidate_id),
    FOREIGN KEY(order_id) REFERENCES orders(order_id)
);

CREATE TABLE IF NOT EXISTS daily_inspection_reports (
    report_id TEXT PRIMARY KEY,
    run_id TEXT,
    organization_id TEXT NOT NULL DEFAULT 'ORG-DEMO',
    current_user_id TEXT NOT NULL,
    inspection_date TEXT NOT NULL,
    timezone TEXT NOT NULL DEFAULT 'Asia/Shanghai',
    scope_json TEXT NOT NULL,
    report_json TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(run_id) REFERENCES agent_runs(run_id)
);

CREATE INDEX IF NOT EXISTS idx_dependencies_order_status ON order_dependencies(order_id,status);
CREATE INDEX IF NOT EXISTS idx_logistics_order_status ON logistics_events(order_id,status,event_time);
CREATE INDEX IF NOT EXISTS idx_agent_runs_user_time ON agent_runs(current_user_id,created_at DESC);
CREATE INDEX IF NOT EXISTS idx_agent_calls_run_time ON agent_tool_calls(run_id,created_at);
CREATE INDEX IF NOT EXISTS idx_anomalies_order_status ON anomaly_candidates(order_id,status,severity);
CREATE INDEX IF NOT EXISTS idx_approvals_status_role ON approval_requests(status,required_role,created_at);
CREATE INDEX IF NOT EXISTS idx_reports_user_date ON daily_inspection_reports(current_user_id,inspection_date DESC);

-- FlowOrder V6.1 composite tools, bulk natural-language updates and analytics
CREATE TABLE IF NOT EXISTS bulk_update_batches (
    batch_id TEXT PRIMARY KEY,
    organization_id TEXT NOT NULL DEFAULT 'ORG-DEMO',
    current_user_id TEXT NOT NULL,
    current_role TEXT NOT NULL,
    source_text TEXT NOT NULL,
    parser_mode TEXT NOT NULL DEFAULT 'hybrid_rules_v1',
    status TEXT NOT NULL DEFAULT 'PARSED',
    summary_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    confirmed_at TEXT
);

CREATE TABLE IF NOT EXISTS bulk_update_candidates (
    update_id TEXT PRIMARY KEY,
    batch_id TEXT NOT NULL,
    order_id TEXT,
    order_no TEXT,
    source_segment TEXT NOT NULL,
    match_confidence REAL NOT NULL DEFAULT 0,
    field_name TEXT NOT NULL,
    old_value_json TEXT,
    new_value_json TEXT,
    confidence REAL NOT NULL DEFAULT 0,
    risk_level TEXT NOT NULL DEFAULT 'normal',
    requires_approval INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'PENDING',
    edited_value_json TEXT,
    approval_id TEXT,
    created_at TEXT NOT NULL,
    decided_at TEXT,
    FOREIGN KEY(batch_id) REFERENCES bulk_update_batches(batch_id),
    FOREIGN KEY(order_id) REFERENCES orders(order_id)
);

CREATE TABLE IF NOT EXISTS analytics_events (
    event_id TEXT PRIMARY KEY,
    event_name TEXT NOT NULL,
    organization_id TEXT,
    user_id TEXT,
    user_role TEXT,
    session_id TEXT,
    order_id TEXT,
    run_id TEXT,
    source TEXT NOT NULL DEFAULT 'server',
    app_version TEXT NOT NULL DEFAULT '6.1.0',
    properties_json TEXT NOT NULL DEFAULT '{}',
    client_timestamp TEXT,
    server_timestamp TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_bulk_update_batch_status ON bulk_update_candidates(batch_id,status);
CREATE INDEX IF NOT EXISTS idx_bulk_update_order ON bulk_update_candidates(order_id,created_at DESC);
CREATE INDEX IF NOT EXISTS idx_analytics_event_time ON analytics_events(event_name,server_timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_analytics_user_time ON analytics_events(user_id,server_timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_analytics_run ON analytics_events(run_id,server_timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_analytics_order ON analytics_events(order_id,server_timestamp DESC);

-- FlowOrder D8: Action Case table
CREATE TABLE IF NOT EXISTS action_cases (
    action_case_id TEXT PRIMARY KEY,
    organization_id TEXT NOT NULL,
    order_id TEXT NOT NULL,
    action_intent_key TEXT NOT NULL,
    intent_type TEXT NOT NULL,
    stage TEXT NOT NULL,
    lifecycle_status TEXT NOT NULL DEFAULT 'ACTIVE',
    title TEXT,
    latest_action_bucket TEXT,
    latest_severity TEXT,
    latest_recommended_action TEXT,
    latest_evidence_json TEXT NOT NULL DEFAULT '[]',
    observation_status TEXT NOT NULL DEFAULT 'OBSERVED',
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    last_reconciled_at TEXT,
    source_policy_version TEXT,
    version INTEGER NOT NULL DEFAULT 1,
    close_reason TEXT,
    closed_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(order_id) REFERENCES orders(order_id)
);

-- Partial unique index: one ACTIVE case per (org, order, intent_key)
CREATE UNIQUE INDEX IF NOT EXISTS uq_action_cases_active
ON action_cases(organization_id, order_id, action_intent_key)
WHERE lifecycle_status = 'ACTIVE';

CREATE INDEX IF NOT EXISTS idx_action_cases_org_order ON action_cases(organization_id, order_id, lifecycle_status);
CREATE INDEX IF NOT EXISTS idx_action_cases_stage ON action_cases(stage, lifecycle_status);
CREATE INDEX IF NOT EXISTS idx_action_cases_intent ON action_cases(action_intent_key, lifecycle_status);

-- FlowOrder D9-P0: Action Case → Task → Waiting (separate layers, never one state machine)
CREATE TABLE IF NOT EXISTS d9_action_case_tasks (
    task_id TEXT PRIMARY KEY,
    organization_id TEXT NOT NULL,
    action_case_id TEXT NOT NULL,
    title TEXT NOT NULL,
    recommended_action TEXT,
    status TEXT NOT NULL DEFAULT 'TODO',
    version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(action_case_id) REFERENCES action_cases(action_case_id)
);

CREATE INDEX IF NOT EXISTS idx_d9_tasks_case_status ON d9_action_case_tasks(action_case_id, status);
CREATE INDEX IF NOT EXISTS idx_d9_tasks_org_case ON d9_action_case_tasks(organization_id, action_case_id);

CREATE TABLE IF NOT EXISTS d9_action_case_waitings (
    waiting_id TEXT PRIMARY KEY,
    organization_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    action_case_id TEXT NOT NULL,
    waiting_type TEXT NOT NULL,
    reason TEXT,
    due_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'ACTIVE',
    source_trace_id TEXT,
    reply_count INTEGER NOT NULL DEFAULT 0,
    latest_reply_json TEXT NOT NULL DEFAULT '[]',
    version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    resolved_at TEXT,
    expired_at TEXT,
    cancelled_at TEXT,
    cancel_reason TEXT,
    FOREIGN KEY(task_id) REFERENCES d9_action_case_tasks(task_id),
    FOREIGN KEY(action_case_id) REFERENCES action_cases(action_case_id)
);

CREATE INDEX IF NOT EXISTS idx_d9_waitings_case ON d9_action_case_waitings(action_case_id);
CREATE INDEX IF NOT EXISTS idx_d9_waitings_task_status ON d9_action_case_waitings(task_id, status);
CREATE INDEX IF NOT EXISTS idx_d9_waitings_due_scan ON d9_action_case_waitings(organization_id, status, due_at);

-- One ACTIVE waiting per task (invariant + idempotency guard)
CREATE UNIQUE INDEX IF NOT EXISTS uq_d9_waitings_active
ON d9_action_case_waitings(task_id)
WHERE status = 'ACTIVE';

CREATE TABLE IF NOT EXISTS d9_trace_events (
    trace_id TEXT PRIMARY KEY,
    organization_id TEXT NOT NULL,
    trace_kind TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}',
    actor TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_d9_trace_entity ON d9_trace_events(entity_type, entity_id, created_at);
CREATE INDEX IF NOT EXISTS idx_d9_trace_org ON d9_trace_events(organization_id);

-- FlowOrder D10: BusinessAction Submission + Transactional Outbox
-- D10 owns durable intent only; no ERP/CRM/email execution happens here.
CREATE TABLE IF NOT EXISTS d10_business_actions (
    business_action_id TEXT PRIMARY KEY,
    organization_id TEXT NOT NULL,
    action_case_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    order_id TEXT NOT NULL,
    action_type TEXT NOT NULL,
    target_type TEXT NOT NULL,
    target_id TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    request_id TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    effect_hash TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'ACCEPTED',
    actor TEXT NOT NULL,
    source TEXT NOT NULL,
    reason TEXT,
    policy_version TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(action_case_id) REFERENCES action_cases(action_case_id),
    FOREIGN KEY(task_id) REFERENCES d9_action_case_tasks(task_id),
    FOREIGN KEY(order_id) REFERENCES orders(order_id),
    UNIQUE(organization_id, task_id),
    UNIQUE(organization_id, idempotency_key)
);
CREATE INDEX IF NOT EXISTS idx_d10_business_actions_case
ON d10_business_actions(organization_id, action_case_id, created_at);

CREATE TABLE IF NOT EXISTS d10_outbox_events (
    event_id TEXT PRIMARY KEY,
    organization_id TEXT NOT NULL,
    business_action_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    dedupe_key TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'PENDING',
    attempt_count INTEGER NOT NULL DEFAULT 0,
    next_attempt_at TEXT,
    lease_owner TEXT,
    lease_until TEXT,
    published_at TEXT,
    last_error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(business_action_id) REFERENCES d10_business_actions(business_action_id),
    UNIQUE(organization_id, business_action_id),
    UNIQUE(organization_id, dedupe_key)
);
CREATE INDEX IF NOT EXISTS idx_d10_outbox_pending
ON d10_outbox_events(organization_id, status, next_attempt_at, created_at);

-- No FK on business_action_id here: this row is the transaction's idempotency
-- reservation and may be inserted before the BusinessAction row in the same UoW.
CREATE TABLE IF NOT EXISTS d10_idempotency_records (
    organization_id TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    business_action_id TEXT NOT NULL,
    result_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(organization_id, idempotency_key)
);

CREATE TABLE IF NOT EXISTS d10_audit_events (
    audit_id TEXT PRIMARY KEY,
    organization_id TEXT NOT NULL,
    actor TEXT NOT NULL,
    request_id TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    before_json TEXT NOT NULL,
    after_json TEXT NOT NULL,
    reason TEXT,
    source TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(entity_id) REFERENCES d10_business_actions(business_action_id)
);
CREATE INDEX IF NOT EXISTS idx_d10_audit_entity
ON d10_audit_events(organization_id, entity_type, entity_id, created_at);

-- FlowOrder D15: Durable Execution / RESULT_UNCERTAIN ---------------------
-- D15 is an overlay on D10 Outbox. D10 remains the owner of durable intent;
-- D15 governs external dispatch, finite retry, ambiguous result and reconciliation.
CREATE TABLE IF NOT EXISTS d15_outbox_execution_state (
    event_id TEXT PRIMARY KEY,
    organization_id TEXT NOT NULL,
    business_action_id TEXT NOT NULL,
    request_id TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'PENDING',
    retry_budget INTEGER NOT NULL DEFAULT 3,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    dispatch_started INTEGER NOT NULL DEFAULT 0,
    result_known INTEGER NOT NULL DEFAULT 0,
    external_effect_status TEXT NOT NULL DEFAULT 'UNKNOWN',
    error_kind TEXT,
    user_message_code TEXT NOT NULL DEFAULT 'ACTION_PENDING',
    reconciliation_status TEXT,
    next_attempt_at TEXT,
    last_attempt_at TEXT,
    resolved_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(event_id) REFERENCES d10_outbox_events(event_id),
    FOREIGN KEY(business_action_id) REFERENCES d10_business_actions(business_action_id),
    UNIQUE(organization_id, idempotency_key)
);
CREATE INDEX IF NOT EXISTS idx_d15_execution_state_queue
ON d15_outbox_execution_state(organization_id, state, next_attempt_at, updated_at);

CREATE TABLE IF NOT EXISTS d15_execution_trace_events (
    trace_id TEXT PRIMARY KEY,
    event_id TEXT NOT NULL,
    organization_id TEXT NOT NULL,
    sequence_no INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    state TEXT NOT NULL,
    error_kind TEXT,
    request_id TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    attempt INTEGER NOT NULL DEFAULT 0,
    dispatch_started INTEGER NOT NULL DEFAULT 0,
    result_known INTEGER NOT NULL DEFAULT 0,
    external_effect_status TEXT NOT NULL DEFAULT 'UNKNOWN',
    response_meta_json TEXT NOT NULL DEFAULT '{}',
    actor TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY(event_id) REFERENCES d10_outbox_events(event_id),
    UNIQUE(event_id, sequence_no)
);
CREATE INDEX IF NOT EXISTS idx_d15_trace_event_seq
ON d15_execution_trace_events(event_id, sequence_no);

-- D12 Human Review / Approval Gate -----------------------------------------
CREATE TABLE IF NOT EXISTS d12_human_reviews (
    review_id TEXT PRIMARY KEY,
    organization_id TEXT NOT NULL,
    order_id TEXT NOT NULL,
    action_case_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    action_type TEXT NOT NULL,
    target_type TEXT NOT NULL,
    target_id TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    state_version TEXT NOT NULL,
    state_snapshot_json TEXT NOT NULL,
    requested_by TEXT NOT NULL,
    requester_role TEXT NOT NULL,
    required_review TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    d10_request_id TEXT NOT NULL,
    reason TEXT,
    status TEXT NOT NULL DEFAULT 'PENDING',
    decision TEXT,
    reviewed_by TEXT,
    reviewer_role TEXT,
    created_at TEXT NOT NULL,
    reviewed_at TEXT,
    expires_at TEXT NOT NULL,
    consumed_at TEXT,
    business_action_id TEXT,
    result_json TEXT,
    policy_version TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(order_id) REFERENCES orders(order_id),
    FOREIGN KEY(action_case_id) REFERENCES action_cases(action_case_id),
    FOREIGN KEY(task_id) REFERENCES d9_action_case_tasks(task_id),
    FOREIGN KEY(business_action_id) REFERENCES d10_business_actions(business_action_id),
    UNIQUE(organization_id, idempotency_key)
);
CREATE INDEX IF NOT EXISTS idx_d12_reviews_queue
ON d12_human_reviews(organization_id, status, required_review, created_at);
CREATE INDEX IF NOT EXISTS idx_d12_reviews_task
ON d12_human_reviews(organization_id, task_id, created_at);

-- D13 Controlled Agent Runtime / Business Trace ---------------------------
-- Trace stores auditable business execution facts only; never hidden chain-of-thought.
CREATE TABLE IF NOT EXISTS d13_agent_runs (
    run_id TEXT PRIMARY KEY,
    organization_id TEXT NOT NULL,
    current_user_id TEXT NOT NULL,
    current_role TEXT NOT NULL,
    trigger_type TEXT NOT NULL,
    trigger_ref TEXT,
    goal TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'RUNNING',
    stop_reason TEXT,
    skill_version TEXT NOT NULL,
    tool_contract_version TEXT NOT NULL,
    transcription_version TEXT NOT NULL,
    model_provider TEXT,
    model_name TEXT,
    system_current_datetime TEXT NOT NULL,
    timezone TEXT NOT NULL DEFAULT 'Asia/Shanghai',
    context_refs_json TEXT NOT NULL DEFAULT '[]',
    tool_call_count INTEGER NOT NULL DEFAULT 0,
    distinct_task_count INTEGER NOT NULL DEFAULT 0,
    final_response TEXT,
    external_effect_executed INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    completed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_d13_runs_org_user_time
ON d13_agent_runs(organization_id, current_user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_d13_runs_status
ON d13_agent_runs(organization_id, status, created_at DESC);

CREATE TABLE IF NOT EXISTS d13_agent_trace_events (
    event_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    sequence_no INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    tool_name TEXT,
    task_id TEXT,
    mode TEXT,
    request_json TEXT NOT NULL DEFAULT '{}',
    response_json TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(run_id) REFERENCES d13_agent_runs(run_id),
    UNIQUE(run_id, sequence_no)
);
CREATE INDEX IF NOT EXISTS idx_d13_trace_run_seq
ON d13_agent_trace_events(run_id, sequence_no);


-- D16 Observability / Feature Flags ---------------------------------------
CREATE TABLE IF NOT EXISTS d16_feature_flag_overrides (
    override_id TEXT PRIMARY KEY,
    organization_id TEXT NOT NULL,
    scope_type TEXT NOT NULL,
    scope_id TEXT NOT NULL,
    flag_key TEXT NOT NULL,
    enabled INTEGER NOT NULL,
    rollout_percent INTEGER NOT NULL DEFAULT 100,
    reason TEXT,
    updated_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(scope_type, scope_id, flag_key)
);
CREATE INDEX IF NOT EXISTS idx_d16_flags_org_key
ON d16_feature_flag_overrides(organization_id, flag_key, scope_type, scope_id);

CREATE TABLE IF NOT EXISTS d16_feature_flag_events (
    event_id TEXT PRIMARY KEY,
    organization_id TEXT NOT NULL,
    flag_key TEXT NOT NULL,
    scope_type TEXT NOT NULL,
    scope_id TEXT NOT NULL,
    action TEXT NOT NULL,
    old_value_json TEXT NOT NULL DEFAULT '{}',
    new_value_json TEXT NOT NULL DEFAULT '{}',
    actor TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_d16_flag_events_org_time
ON d16_feature_flag_events(organization_id, created_at);
