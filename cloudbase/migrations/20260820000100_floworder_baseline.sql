-- FlowOrder CloudBase PostgreSQL baseline generated from the frozen Alembic head p7q8r9s0t1u2.

-- Apply through CloudBase PG migration/SQL management, not the runtime direct DB user.

CREATE SCHEMA IF NOT EXISTS floworder;

SET search_path TO floworder, public;

CREATE TABLE IF NOT EXISTS agent_chat_jobs (
	job_id VARCHAR NOT NULL, 
	organization_id VARCHAR DEFAULT 'ORG-DEMO' NOT NULL, 
	current_user_id VARCHAR NOT NULL, 
	"current_role" VARCHAR NOT NULL, 
	question VARCHAR NOT NULL, 
	status VARCHAR DEFAULT 'QUEUED' NOT NULL, 
	request_json TEXT DEFAULT '{}' NOT NULL, 
	result_json TEXT, 
	error_message VARCHAR, 
	conversation_id VARCHAR, 
	linked_run_id VARCHAR, 
	duration_ms INTEGER, 
	created_at VARCHAR NOT NULL, 
	started_at VARCHAR, 
	completed_at VARCHAR, 
	updated_at VARCHAR NOT NULL, 
	PRIMARY KEY (job_id)
);

CREATE TABLE IF NOT EXISTS agent_runs (
	run_id VARCHAR NOT NULL, 
	organization_id VARCHAR DEFAULT 'ORG-DEMO' NOT NULL, 
	current_user_id VARCHAR NOT NULL, 
	"current_role" VARCHAR NOT NULL, 
	goal VARCHAR NOT NULL, 
	trigger_type VARCHAR DEFAULT 'USER' NOT NULL, 
	status VARCHAR NOT NULL, 
	max_tool_calls INTEGER DEFAULT 8 NOT NULL, 
	max_duration_seconds INTEGER DEFAULT 60 NOT NULL, 
	result_json TEXT, 
	stop_reason VARCHAR, 
	duration_ms INTEGER, 
	started_at VARCHAR, 
	completed_at VARCHAR, 
	created_at VARCHAR NOT NULL, 
	PRIMARY KEY (run_id)
);

CREATE TABLE IF NOT EXISTS analytics_events (
	event_id VARCHAR NOT NULL, 
	event_name VARCHAR NOT NULL, 
	organization_id VARCHAR, 
	user_id VARCHAR, 
	user_role VARCHAR, 
	session_id VARCHAR, 
	order_id VARCHAR, 
	run_id VARCHAR, 
	source VARCHAR DEFAULT 'server' NOT NULL, 
	app_version VARCHAR DEFAULT '6.1.0' NOT NULL, 
	properties_json TEXT DEFAULT '{}' NOT NULL, 
	client_timestamp VARCHAR, 
	server_timestamp VARCHAR NOT NULL, 
	PRIMARY KEY (event_id)
);

CREATE TABLE IF NOT EXISTS audit_logs (
	audit_id VARCHAR NOT NULL, 
	organization_id VARCHAR NOT NULL, 
	actor_user_id VARCHAR NOT NULL, 
	actor_role VARCHAR NOT NULL, 
	action VARCHAR NOT NULL, 
	entity_type VARCHAR NOT NULL, 
	entity_id VARCHAR NOT NULL, 
	result VARCHAR DEFAULT 'SUCCESS' NOT NULL, 
	details_json TEXT NOT NULL, 
	created_at VARCHAR NOT NULL, 
	PRIMARY KEY (audit_id)
);

CREATE TABLE IF NOT EXISTS bulk_update_batches (
	batch_id VARCHAR NOT NULL, 
	organization_id VARCHAR DEFAULT 'ORG-DEMO' NOT NULL, 
	current_user_id VARCHAR NOT NULL, 
	"current_role" VARCHAR NOT NULL, 
	source_text VARCHAR NOT NULL, 
	parser_mode VARCHAR DEFAULT 'hybrid_rules_v1' NOT NULL, 
	status VARCHAR DEFAULT 'PARSED' NOT NULL, 
	summary_json TEXT DEFAULT '{}' NOT NULL, 
	created_at VARCHAR NOT NULL, 
	confirmed_at VARCHAR, 
	PRIMARY KEY (batch_id)
);

CREATE TABLE IF NOT EXISTS communication_drafts (
	draft_id VARCHAR NOT NULL, 
	request_id VARCHAR NOT NULL, 
	order_id VARCHAR, 
	order_no VARCHAR, 
	draft_type VARCHAR NOT NULL, 
	recipient_role VARCHAR NOT NULL, 
	channel VARCHAR, 
	result_json TEXT NOT NULL, 
	ai_subject VARCHAR, 
	ai_draft TEXT, 
	edited_subject VARCHAR, 
	edited_draft TEXT, 
	final_text TEXT, 
	facts_used_json TEXT DEFAULT '[]' NOT NULL, 
	missing_facts_json TEXT DEFAULT '[]' NOT NULL, 
	questions_to_ask_json TEXT DEFAULT '[]' NOT NULL, 
	risk_flags_json TEXT DEFAULT '[]' NOT NULL, 
	run_status VARCHAR NOT NULL, 
	approval_status VARCHAR, 
	human_status VARCHAR DEFAULT 'PENDING' NOT NULL, 
	reviewer_id VARCHAR, 
	review_note VARCHAR, 
	approved_at VARCHAR, 
	copied_at VARCHAR, 
	created_at VARCHAR NOT NULL, 
	updated_at VARCHAR NOT NULL, 
	PRIMARY KEY (draft_id), 
	UNIQUE (request_id)
);

CREATE TABLE IF NOT EXISTS communication_events (
	event_id VARCHAR NOT NULL, 
	entity_type VARCHAR NOT NULL, 
	entity_id VARCHAR NOT NULL, 
	event_type VARCHAR NOT NULL, 
	payload_json TEXT NOT NULL, 
	operator_id VARCHAR, 
	created_at VARCHAR NOT NULL, 
	PRIMARY KEY (event_id)
);

CREATE TABLE IF NOT EXISTS communication_task_candidates (
	candidate_id VARCHAR NOT NULL, 
	request_id VARCHAR NOT NULL, 
	source_message_id VARCHAR, 
	order_id VARCHAR, 
	order_no VARCHAR, 
	communication_text VARCHAR NOT NULL, 
	sender_role VARCHAR, 
	channel VARCHAR, 
	result_json TEXT NOT NULL, 
	task_candidate_json TEXT NOT NULL, 
	run_status VARCHAR NOT NULL, 
	review_status VARCHAR DEFAULT 'PENDING' NOT NULL, 
	reviewer_id VARCHAR, 
	review_note VARCHAR, 
	ft03_result_json TEXT, 
	created_at VARCHAR NOT NULL, 
	reviewed_at VARCHAR, 
	PRIMARY KEY (candidate_id), 
	UNIQUE (request_id)
);

CREATE TABLE IF NOT EXISTS communication_workflow_runs (
	run_id VARCHAR NOT NULL, 
	workflow_code VARCHAR NOT NULL, 
	workflow_id VARCHAR, 
	request_id VARCHAR, 
	status VARCHAR NOT NULL, 
	input_json TEXT NOT NULL, 
	output_json TEXT, 
	error_code VARCHAR, 
	error_message VARCHAR, 
	debug_url VARCHAR, 
	duration_ms INTEGER, 
	created_at VARCHAR NOT NULL, 
	PRIMARY KEY (run_id)
);

CREATE TABLE IF NOT EXISTS confirmation_snapshots (
	confirmation_id VARCHAR NOT NULL, 
	idempotency_key VARCHAR NOT NULL, 
	operator_id VARCHAR, 
	payload_json TEXT NOT NULL, 
	created_at VARCHAR NOT NULL, 
	PRIMARY KEY (confirmation_id)
);

CREATE TABLE IF NOT EXISTS d10_idempotency_records (
	organization_id VARCHAR NOT NULL, 
	idempotency_key VARCHAR NOT NULL, 
	request_hash VARCHAR NOT NULL, 
	business_action_id VARCHAR NOT NULL, 
	result_json TEXT NOT NULL, 
	created_at VARCHAR NOT NULL, 
	PRIMARY KEY (organization_id, idempotency_key)
);

CREATE TABLE IF NOT EXISTS d13_agent_runs (
	run_id VARCHAR NOT NULL, 
	organization_id VARCHAR NOT NULL, 
	current_user_id VARCHAR NOT NULL, 
	"current_role" VARCHAR NOT NULL, 
	trigger_type VARCHAR NOT NULL, 
	trigger_ref VARCHAR, 
	goal TEXT NOT NULL, 
	status VARCHAR DEFAULT 'RUNNING' NOT NULL, 
	stop_reason VARCHAR, 
	skill_version VARCHAR NOT NULL, 
	tool_contract_version VARCHAR NOT NULL, 
	transcription_version VARCHAR NOT NULL, 
	model_provider VARCHAR, 
	model_name VARCHAR, 
	system_current_datetime VARCHAR NOT NULL, 
	timezone VARCHAR DEFAULT 'Asia/Shanghai' NOT NULL, 
	context_refs_json TEXT DEFAULT '[]' NOT NULL, 
	tool_call_count INTEGER DEFAULT '0' NOT NULL, 
	distinct_task_count INTEGER DEFAULT '0' NOT NULL, 
	final_response TEXT, 
	external_effect_executed INTEGER DEFAULT '0' NOT NULL, 
	created_at VARCHAR NOT NULL, 
	updated_at VARCHAR NOT NULL, 
	completed_at VARCHAR, 
	PRIMARY KEY (run_id)
);

CREATE TABLE IF NOT EXISTS d16_feature_flag_events (
	event_id TEXT NOT NULL, 
	organization_id TEXT NOT NULL, 
	flag_key TEXT NOT NULL, 
	scope_type TEXT NOT NULL, 
	scope_id TEXT NOT NULL, 
	action TEXT NOT NULL, 
	old_value_json TEXT DEFAULT '{}' NOT NULL, 
	new_value_json TEXT DEFAULT '{}' NOT NULL, 
	actor TEXT NOT NULL, 
	created_at TEXT NOT NULL, 
	PRIMARY KEY (event_id)
);

CREATE TABLE IF NOT EXISTS d16_feature_flag_overrides (
	override_id TEXT NOT NULL, 
	organization_id TEXT NOT NULL, 
	scope_type TEXT NOT NULL, 
	scope_id TEXT NOT NULL, 
	flag_key TEXT NOT NULL, 
	enabled INTEGER NOT NULL, 
	rollout_percent INTEGER DEFAULT '100' NOT NULL, 
	reason TEXT, 
	updated_by TEXT NOT NULL, 
	created_at TEXT NOT NULL, 
	updated_at TEXT NOT NULL, 
	PRIMARY KEY (override_id), 
	CONSTRAINT uq_d16_flag_scope UNIQUE (scope_type, scope_id, flag_key)
);

CREATE TABLE IF NOT EXISTS d9_trace_events (
	trace_id VARCHAR NOT NULL, 
	organization_id VARCHAR NOT NULL, 
	trace_kind VARCHAR NOT NULL, 
	entity_type VARCHAR NOT NULL, 
	entity_id VARCHAR NOT NULL, 
	event_type VARCHAR NOT NULL, 
	payload_json TEXT DEFAULT '{}' NOT NULL, 
	actor VARCHAR, 
	created_at VARCHAR NOT NULL, 
	PRIMARY KEY (trace_id)
);

CREATE TABLE IF NOT EXISTS erp_read_snapshots (
	snapshot_id TEXT NOT NULL, 
	organization_id TEXT NOT NULL, 
	doctype TEXT NOT NULL, 
	external_id TEXT NOT NULL, 
	source_modified_at TEXT, 
	normalized_json TEXT NOT NULL, 
	raw_sha256 TEXT NOT NULL, 
	fetched_at TEXT NOT NULL, 
	created_at TEXT NOT NULL, 
	updated_at TEXT NOT NULL, 
	PRIMARY KEY (snapshot_id), 
	UNIQUE (organization_id, doctype, external_id)
);

CREATE TABLE IF NOT EXISTS erp_sync_state (
	organization_id TEXT NOT NULL, 
	doctype TEXT NOT NULL, 
	last_success_cursor TEXT, 
	last_success_at TEXT, 
	last_attempt_at TEXT, 
	sync_status TEXT DEFAULT 'NEVER_SYNCED' NOT NULL, 
	last_error_code TEXT, 
	records_seen INTEGER DEFAULT 0 NOT NULL, 
	records_changed INTEGER DEFAULT 0 NOT NULL, 
	updated_at TEXT NOT NULL, 
	PRIMARY KEY (organization_id, doctype)
);

CREATE TABLE IF NOT EXISTS event_logs (
	event_id VARCHAR NOT NULL, 
	entity_type VARCHAR NOT NULL, 
	entity_id VARCHAR, 
	event_type VARCHAR NOT NULL, 
	payload_json TEXT NOT NULL, 
	operator_id VARCHAR, 
	created_at VARCHAR NOT NULL, 
	organization_id VARCHAR, 
	PRIMARY KEY (event_id)
);

CREATE TABLE IF NOT EXISTS idempotency_records (
	idempotency_key VARCHAR NOT NULL, 
	result_status VARCHAR NOT NULL, 
	result_json TEXT NOT NULL, 
	created_at VARCHAR NOT NULL, 
	PRIMARY KEY (idempotency_key)
);

CREATE TABLE IF NOT EXISTS order_corrections (
	correction_id TEXT NOT NULL, 
	order_id TEXT NOT NULL, 
	source_order_key TEXT, 
	batch_id TEXT, 
	actor_user_id TEXT NOT NULL, 
	target_type TEXT NOT NULL, 
	target_id TEXT, 
	changes_json TEXT NOT NULL, 
	created_at TEXT NOT NULL, 
	PRIMARY KEY (correction_id)
);

CREATE TABLE IF NOT EXISTS order_import_batches (
	batch_id VARCHAR NOT NULL, 
	source_filename VARCHAR NOT NULL, 
	source_sha256 VARCHAR NOT NULL, 
	status VARCHAR NOT NULL, 
	total_rows INTEGER DEFAULT 0 NOT NULL, 
	importable_rows INTEGER DEFAULT 0 NOT NULL, 
	error_rows INTEGER DEFAULT 0 NOT NULL, 
	mapping_json TEXT NOT NULL, 
	summary_json TEXT NOT NULL, 
	created_at VARCHAR NOT NULL, 
	committed_at VARCHAR, 
	started_at TEXT, 
	preflight_completed_at TEXT, 
	commit_completed_at TEXT, 
	preflight_duration_ms INTEGER, 
	commit_duration_ms INTEGER, 
	end_to_end_duration_ms INTEGER, 
	processing_duration_ms INTEGER, 
	projection_hash TEXT, 
	warning_count INTEGER, 
	block_count INTEGER, 
	success_count INTEGER, 
	success_with_warning_count INTEGER, 
	commit_failed_count INTEGER, 
	retry_of_batch_id TEXT, 
	retry_attempt INTEGER, 
	duplicate_noop_count INTEGER, 
	conflict_count INTEGER, 
	corrected_count INTEGER, 
	source_file_name TEXT, 
	source_file_size INTEGER, 
	file_sha256 TEXT, 
	has_header INTEGER, 
	start_row INTEGER, 
	organization_id TEXT, 
	created_by TEXT, 
	PRIMARY KEY (batch_id)
);

CREATE TABLE IF NOT EXISTS order_lines (
	line_id TEXT NOT NULL, 
	order_id TEXT NOT NULL, 
	source_system TEXT, 
	source_order_key TEXT, 
	source_line_key TEXT, 
	product_name TEXT, 
	order_qty INTEGER, 
	completed_qty INTEGER, 
	notes TEXT, 
	created_at TEXT, 
	updated_at TEXT, 
	PRIMARY KEY (line_id)
);

CREATE TABLE IF NOT EXISTS orders (
	order_id VARCHAR NOT NULL, 
	order_no VARCHAR NOT NULL, 
	customer_name VARCHAR, 
	product_name VARCHAR, 
	packaging_method VARCHAR, 
	requested_delivery_date VARCHAR, 
	latest_supplier_commitment VARCHAR, 
	current_progress FLOAT, 
	current_node VARCHAR, 
	status VARCHAR DEFAULT 'ACTIVE' NOT NULL, 
	owner VARCHAR, 
	action_readiness VARCHAR DEFAULT 'BASE_ONLY' NOT NULL, 
	contact_status VARCHAR DEFAULT 'UNKNOWN' NOT NULL, 
	issue_status VARCHAR DEFAULT 'UNKNOWN' NOT NULL, 
	initialization_waiting_on VARCHAR, 
	initialization_promised_reply_at VARCHAR, 
	initialization_note VARCHAR, 
	initialization_source VARCHAR, 
	initialized_at VARCHAR, 
	last_dynamic_update_at VARCHAR, 
	created_at VARCHAR NOT NULL, 
	updated_at VARCHAR NOT NULL, 
	organization_id VARCHAR, 
	source_system TEXT, 
	source_order_key TEXT, 
	PRIMARY KEY (order_id), 
	UNIQUE (order_no)
);

CREATE TABLE IF NOT EXISTS user_settings (
	user_id VARCHAR NOT NULL, 
	settings_json TEXT NOT NULL, 
	updated_at VARCHAR NOT NULL, 
	PRIMARY KEY (user_id)
);

CREATE TABLE IF NOT EXISTS workflow_runs (
	run_id VARCHAR NOT NULL, 
	workflow_key VARCHAR NOT NULL, 
	workflow_id VARCHAR NOT NULL, 
	status VARCHAR NOT NULL, 
	input_json TEXT NOT NULL, 
	output_json TEXT, 
	coze_code INTEGER, 
	coze_msg VARCHAR, 
	debug_url VARCHAR, 
	duration_ms INTEGER, 
	created_at VARCHAR NOT NULL, 
	PRIMARY KEY (run_id)
);

CREATE TABLE IF NOT EXISTS action_cases (
	action_case_id VARCHAR NOT NULL, 
	organization_id VARCHAR NOT NULL, 
	order_id VARCHAR NOT NULL, 
	action_intent_key VARCHAR NOT NULL, 
	intent_type VARCHAR NOT NULL, 
	stage VARCHAR NOT NULL, 
	lifecycle_status VARCHAR DEFAULT 'ACTIVE' NOT NULL, 
	title VARCHAR, 
	latest_action_bucket VARCHAR, 
	latest_severity VARCHAR, 
	latest_recommended_action VARCHAR, 
	latest_evidence_json TEXT DEFAULT '[]' NOT NULL, 
	observation_status VARCHAR DEFAULT 'OBSERVED' NOT NULL, 
	first_seen_at VARCHAR NOT NULL, 
	last_seen_at VARCHAR NOT NULL, 
	last_reconciled_at VARCHAR, 
	source_policy_version VARCHAR, 
	version INTEGER DEFAULT 1 NOT NULL, 
	close_reason VARCHAR, 
	closed_at VARCHAR, 
	created_at VARCHAR NOT NULL, 
	updated_at VARCHAR NOT NULL, 
	PRIMARY KEY (action_case_id), 
	FOREIGN KEY(order_id) REFERENCES orders (order_id)
);

CREATE TABLE IF NOT EXISTS agent_tool_calls (
	call_id VARCHAR NOT NULL, 
	run_id VARCHAR, 
	tool_name VARCHAR NOT NULL, 
	request_json TEXT NOT NULL, 
	response_json TEXT, 
	status VARCHAR NOT NULL, 
	error_code VARCHAR, 
	error_message VARCHAR, 
	duration_ms INTEGER, 
	created_at VARCHAR NOT NULL, 
	PRIMARY KEY (call_id), 
	FOREIGN KEY(run_id) REFERENCES agent_runs (run_id)
);

CREATE TABLE IF NOT EXISTS anomaly_candidates (
	candidate_id VARCHAR NOT NULL, 
	run_id VARCHAR, 
	order_id VARCHAR NOT NULL, 
	anomaly_type VARCHAR NOT NULL, 
	severity VARCHAR NOT NULL, 
	confidence FLOAT DEFAULT 0 NOT NULL, 
	score FLOAT DEFAULT 0 NOT NULL, 
	evidence_json TEXT DEFAULT '[]' NOT NULL, 
	missing_information_json TEXT DEFAULT '[]' NOT NULL, 
	recommended_action VARCHAR, 
	status VARCHAR DEFAULT 'ANOMALY_CANDIDATE' NOT NULL, 
	created_by VARCHAR, 
	confirmed_by VARCHAR, 
	confirmed_at VARCHAR, 
	resolution_note VARCHAR, 
	created_at VARCHAR NOT NULL, 
	updated_at VARCHAR NOT NULL, 
	PRIMARY KEY (candidate_id), 
	FOREIGN KEY(order_id) REFERENCES orders (order_id), 
	FOREIGN KEY(run_id) REFERENCES agent_runs (run_id)
);

CREATE TABLE IF NOT EXISTS bulk_update_candidates (
	update_id VARCHAR NOT NULL, 
	batch_id VARCHAR NOT NULL, 
	order_id VARCHAR, 
	order_no VARCHAR, 
	source_segment VARCHAR NOT NULL, 
	match_confidence FLOAT DEFAULT 0 NOT NULL, 
	field_name VARCHAR NOT NULL, 
	old_value_json TEXT, 
	new_value_json TEXT, 
	confidence FLOAT DEFAULT 0 NOT NULL, 
	risk_level VARCHAR DEFAULT 'normal' NOT NULL, 
	requires_approval INTEGER DEFAULT 0 NOT NULL, 
	status VARCHAR DEFAULT 'PENDING' NOT NULL, 
	edited_value_json TEXT, 
	approval_id VARCHAR, 
	created_at VARCHAR NOT NULL, 
	decided_at VARCHAR, 
	PRIMARY KEY (update_id), 
	FOREIGN KEY(batch_id) REFERENCES bulk_update_batches (batch_id), 
	FOREIGN KEY(order_id) REFERENCES orders (order_id)
);

CREATE TABLE IF NOT EXISTS d13_agent_trace_events (
	event_id VARCHAR NOT NULL, 
	run_id VARCHAR NOT NULL, 
	sequence_no INTEGER NOT NULL, 
	event_type VARCHAR NOT NULL, 
	tool_name VARCHAR, 
	task_id VARCHAR, 
	mode VARCHAR, 
	request_json TEXT DEFAULT '{}' NOT NULL, 
	response_json TEXT DEFAULT '{}' NOT NULL, 
	status VARCHAR NOT NULL, 
	created_at VARCHAR NOT NULL, 
	PRIMARY KEY (event_id), 
	FOREIGN KEY(run_id) REFERENCES d13_agent_runs (run_id), 
	CONSTRAINT uq_d13_trace_run_seq UNIQUE (run_id, sequence_no)
);

CREATE TABLE IF NOT EXISTS daily_inspection_reports (
	report_id VARCHAR NOT NULL, 
	run_id VARCHAR, 
	organization_id VARCHAR DEFAULT 'ORG-DEMO' NOT NULL, 
	current_user_id VARCHAR NOT NULL, 
	inspection_date VARCHAR NOT NULL, 
	timezone VARCHAR DEFAULT 'Asia/Shanghai' NOT NULL, 
	scope_json TEXT NOT NULL, 
	report_json TEXT NOT NULL, 
	status VARCHAR NOT NULL, 
	created_at VARCHAR NOT NULL, 
	PRIMARY KEY (report_id), 
	FOREIGN KEY(run_id) REFERENCES agent_runs (run_id)
);

CREATE TABLE IF NOT EXISTS fact_conflicts (
	conflict_id TEXT NOT NULL, 
	order_id TEXT NOT NULL, 
	field_name TEXT NOT NULL, 
	source_a TEXT NOT NULL, 
	value_a TEXT, 
	source_b TEXT NOT NULL, 
	value_b TEXT, 
	status TEXT DEFAULT 'OPEN' NOT NULL, 
	detected_at TEXT NOT NULL, 
	resolved_at TEXT, 
	created_at TEXT NOT NULL, 
	updated_at TEXT NOT NULL, 
	PRIMARY KEY (conflict_id), 
	FOREIGN KEY(order_id) REFERENCES orders (order_id)
);

CREATE TABLE IF NOT EXISTS intake_jobs (
	job_id VARCHAR NOT NULL, 
	status VARCHAR NOT NULL, 
	workflow_key VARCHAR NOT NULL, 
	order_id VARCHAR, 
	request_json TEXT NOT NULL, 
	result_json TEXT, 
	error_json TEXT, 
	review_id VARCHAR, 
	message_id VARCHAR, 
	progress_message VARCHAR, 
	created_at VARCHAR NOT NULL, 
	started_at VARCHAR, 
	completed_at VARCHAR, 
	updated_at VARCHAR NOT NULL, 
	organization_id VARCHAR NOT NULL, 
	PRIMARY KEY (job_id), 
	FOREIGN KEY(order_id) REFERENCES orders (order_id)
);

CREATE TABLE IF NOT EXISTS logistics_events (
	logistics_event_id VARCHAR NOT NULL, 
	order_id VARCHAR NOT NULL, 
	event_type VARCHAR NOT NULL, 
	status VARCHAR NOT NULL, 
	location VARCHAR, 
	description VARCHAR, 
	event_time VARCHAR, 
	estimated_arrival_at VARCHAR, 
	source VARCHAR DEFAULT 'SYNTHETIC_OR_MANUAL' NOT NULL, 
	resolved_at VARCHAR, 
	created_at VARCHAR NOT NULL, 
	updated_at VARCHAR NOT NULL, 
	PRIMARY KEY (logistics_event_id), 
	FOREIGN KEY(order_id) REFERENCES orders (order_id)
);

CREATE TABLE IF NOT EXISTS order_dependencies (
	dependency_id VARCHAR NOT NULL, 
	order_id VARCHAR NOT NULL, 
	dependency_type VARCHAR NOT NULL, 
	dependency_name VARCHAR NOT NULL, 
	sequence_no INTEGER DEFAULT 0 NOT NULL, 
	status VARCHAR DEFAULT 'PENDING' NOT NULL, 
	blocking_party VARCHAR, 
	due_at VARCHAR, 
	evidence_json TEXT DEFAULT '[]' NOT NULL, 
	created_at VARCHAR NOT NULL, 
	updated_at VARCHAR NOT NULL, 
	PRIMARY KEY (dependency_id), 
	FOREIGN KEY(order_id) REFERENCES orders (order_id)
);

CREATE TABLE IF NOT EXISTS order_import_rows (
	row_id VARCHAR NOT NULL, 
	batch_id VARCHAR NOT NULL, 
	row_number INTEGER NOT NULL, 
	raw_json TEXT NOT NULL, 
	normalized_json TEXT NOT NULL, 
	classification VARCHAR NOT NULL, 
	issues_json TEXT NOT NULL, 
	changes_json TEXT NOT NULL, 
	existing_order_id VARCHAR, 
	commit_status VARCHAR, 
	commit_message VARCHAR, 
	source_system TEXT, 
	source_order_key TEXT, 
	source_line_key TEXT, 
	conflict_type TEXT, 
	conflict_details_json TEXT, 
	order_action TEXT, 
	PRIMARY KEY (row_id), 
	FOREIGN KEY(batch_id) REFERENCES order_import_batches (batch_id)
);

CREATE TABLE IF NOT EXISTS source_messages (
	message_id VARCHAR NOT NULL, 
	order_id VARCHAR, 
	source_channel VARCHAR NOT NULL, 
	sender_role VARCHAR NOT NULL, 
	message_type VARCHAR, 
	raw_content VARCHAR NOT NULL, 
	source_time VARCHAR, 
	created_at VARCHAR NOT NULL, 
	organization_id VARCHAR NOT NULL, 
	PRIMARY KEY (message_id), 
	FOREIGN KEY(order_id) REFERENCES orders (order_id)
);

CREATE TABLE IF NOT EXISTS approval_requests (
	approval_id VARCHAR NOT NULL, 
	run_id VARCHAR, 
	candidate_id VARCHAR, 
	order_id VARCHAR, 
	action_type VARCHAR NOT NULL, 
	payload_json TEXT NOT NULL, 
	status VARCHAR DEFAULT 'PENDING' NOT NULL, 
	requested_by VARCHAR NOT NULL, 
	required_role VARCHAR DEFAULT 'operator_or_manager' NOT NULL, 
	idempotency_key VARCHAR NOT NULL, 
	decided_by VARCHAR, 
	decision_note VARCHAR, 
	decided_at VARCHAR, 
	result_json TEXT, 
	created_at VARCHAR NOT NULL, 
	updated_at VARCHAR NOT NULL, 
	organization_id VARCHAR, 
	PRIMARY KEY (approval_id), 
	FOREIGN KEY(candidate_id) REFERENCES anomaly_candidates (candidate_id), 
	FOREIGN KEY(order_id) REFERENCES orders (order_id), 
	FOREIGN KEY(run_id) REFERENCES agent_runs (run_id), 
	UNIQUE (idempotency_key)
);

CREATE TABLE IF NOT EXISTS candidate_reviews (
	review_id VARCHAR NOT NULL, 
	source_message_id VARCHAR, 
	order_id VARCHAR, 
	workflow_source VARCHAR DEFAULT 'LOCAL_RULE_DEMO' NOT NULL, 
	candidate_json TEXT NOT NULL, 
	status VARCHAR DEFAULT 'PENDING' NOT NULL, 
	reviewer_id VARCHAR, 
	created_at VARCHAR NOT NULL, 
	reviewed_at VARCHAR, 
	organization_id VARCHAR NOT NULL, 
	PRIMARY KEY (review_id), 
	FOREIGN KEY(order_id) REFERENCES orders (order_id), 
	FOREIGN KEY(source_message_id) REFERENCES source_messages (message_id)
);

CREATE TABLE IF NOT EXISTS commitment_history (
	commitment_id VARCHAR NOT NULL, 
	order_id VARCHAR NOT NULL, 
	commitment_type VARCHAR NOT NULL, 
	commitment_value VARCHAR NOT NULL, 
	source_message_id VARCHAR, 
	confirmed_by VARCHAR, 
	created_at VARCHAR NOT NULL, 
	PRIMARY KEY (commitment_id), 
	FOREIGN KEY(order_id) REFERENCES orders (order_id), 
	FOREIGN KEY(source_message_id) REFERENCES source_messages (message_id)
);

CREATE TABLE IF NOT EXISTS d9_action_case_tasks (
	task_id VARCHAR NOT NULL, 
	organization_id VARCHAR NOT NULL, 
	action_case_id VARCHAR NOT NULL, 
	title VARCHAR NOT NULL, 
	recommended_action TEXT, 
	status VARCHAR DEFAULT 'TODO' NOT NULL, 
	version INTEGER DEFAULT 1 NOT NULL, 
	created_at VARCHAR NOT NULL, 
	updated_at VARCHAR NOT NULL, 
	PRIMARY KEY (task_id), 
	FOREIGN KEY(action_case_id) REFERENCES action_cases (action_case_id)
);

CREATE TABLE IF NOT EXISTS quality_events (
	quality_event_id TEXT NOT NULL, 
	order_id TEXT NOT NULL, 
	event_type TEXT NOT NULL, 
	status TEXT DEFAULT 'OPEN' NOT NULL, 
	description TEXT, 
	is_delivery_blocking INTEGER DEFAULT '0' NOT NULL, 
	rework_required INTEGER DEFAULT '0' NOT NULL, 
	expected_resolution_at TEXT, 
	event_time TEXT, 
	source TEXT DEFAULT 'SYNTHETIC_OR_MANUAL' NOT NULL, 
	source_message_id TEXT, 
	resolved_at TEXT, 
	created_at TEXT NOT NULL, 
	updated_at TEXT NOT NULL, 
	PRIMARY KEY (quality_event_id), 
	FOREIGN KEY(order_id) REFERENCES orders (order_id), 
	FOREIGN KEY(source_message_id) REFERENCES source_messages (message_id)
);

CREATE TABLE IF NOT EXISTS tasks (
	task_id VARCHAR NOT NULL, 
	related_order_id VARCHAR, 
	title VARCHAR NOT NULL, 
	recommended_action VARCHAR, 
	target VARCHAR, 
	status VARCHAR DEFAULT 'OPEN' NOT NULL, 
	owner_user_id VARCHAR, 
	responsibility_status VARCHAR DEFAULT 'assigned' NOT NULL, 
	waiting_on VARCHAR, 
	promised_reply_at VARCHAR, 
	next_action_at VARCHAR, 
	business_deadline VARCHAR, 
	last_contact_at VARCHAR, 
	risk_level VARCHAR DEFAULT 'none' NOT NULL, 
	urgent INTEGER DEFAULT 0 NOT NULL, 
	pending_confirmation INTEGER DEFAULT 0 NOT NULL, 
	source_message_id VARCHAR, 
	evidence_json TEXT DEFAULT '[]' NOT NULL, 
	created_at VARCHAR NOT NULL, 
	updated_at VARCHAR NOT NULL, 
	organization_id VARCHAR, 
	PRIMARY KEY (task_id), 
	FOREIGN KEY(related_order_id) REFERENCES orders (order_id), 
	FOREIGN KEY(source_message_id) REFERENCES source_messages (message_id)
);

CREATE TABLE IF NOT EXISTS d10_business_actions (
	business_action_id VARCHAR NOT NULL, 
	organization_id VARCHAR NOT NULL, 
	action_case_id VARCHAR NOT NULL, 
	task_id VARCHAR NOT NULL, 
	order_id VARCHAR NOT NULL, 
	action_type VARCHAR NOT NULL, 
	target_type VARCHAR NOT NULL, 
	target_id VARCHAR NOT NULL, 
	payload_json TEXT NOT NULL, 
	request_id VARCHAR NOT NULL, 
	idempotency_key VARCHAR NOT NULL, 
	request_hash VARCHAR NOT NULL, 
	effect_hash VARCHAR NOT NULL, 
	status VARCHAR DEFAULT 'ACCEPTED' NOT NULL, 
	actor VARCHAR NOT NULL, 
	source VARCHAR NOT NULL, 
	reason TEXT, 
	policy_version VARCHAR NOT NULL, 
	created_at VARCHAR NOT NULL, 
	updated_at VARCHAR NOT NULL, 
	PRIMARY KEY (business_action_id), 
	FOREIGN KEY(action_case_id) REFERENCES action_cases (action_case_id), 
	FOREIGN KEY(task_id) REFERENCES d9_action_case_tasks (task_id), 
	FOREIGN KEY(order_id) REFERENCES orders (order_id), 
	CONSTRAINT uq_d10_business_action_task UNIQUE (organization_id, task_id), 
	CONSTRAINT uq_d10_business_action_idem UNIQUE (organization_id, idempotency_key)
);

CREATE TABLE IF NOT EXISTS d9_action_case_waitings (
	waiting_id VARCHAR NOT NULL, 
	organization_id VARCHAR NOT NULL, 
	task_id VARCHAR NOT NULL, 
	action_case_id VARCHAR NOT NULL, 
	waiting_type VARCHAR NOT NULL, 
	reason TEXT, 
	due_at VARCHAR NOT NULL, 
	status VARCHAR DEFAULT 'ACTIVE' NOT NULL, 
	source_trace_id VARCHAR, 
	reply_count INTEGER DEFAULT 0 NOT NULL, 
	latest_reply_json TEXT DEFAULT '[]' NOT NULL, 
	version INTEGER DEFAULT 1 NOT NULL, 
	created_at VARCHAR NOT NULL, 
	updated_at VARCHAR NOT NULL, 
	resolved_at VARCHAR, 
	expired_at VARCHAR, 
	cancelled_at VARCHAR, 
	cancel_reason VARCHAR, 
	PRIMARY KEY (waiting_id), 
	FOREIGN KEY(task_id) REFERENCES d9_action_case_tasks (task_id), 
	FOREIGN KEY(action_case_id) REFERENCES action_cases (action_case_id)
);

CREATE TABLE IF NOT EXISTS risk_signals (
	risk_id VARCHAR NOT NULL, 
	order_id VARCHAR, 
	task_id VARCHAR, 
	risk_type VARCHAR NOT NULL, 
	risk_level VARCHAR NOT NULL, 
	evidence VARCHAR, 
	rule_id VARCHAR, 
	status VARCHAR DEFAULT 'OPEN' NOT NULL, 
	created_at VARCHAR NOT NULL, 
	updated_at VARCHAR NOT NULL, 
	PRIMARY KEY (risk_id), 
	FOREIGN KEY(order_id) REFERENCES orders (order_id), 
	FOREIGN KEY(task_id) REFERENCES tasks (task_id)
);

CREATE TABLE IF NOT EXISTS task_rankings (
	current_user_id VARCHAR NOT NULL, 
	task_id VARCHAR NOT NULL, 
	action_state VARCHAR NOT NULL, 
	recommended_action VARCHAR, 
	target VARCHAR, 
	next_action_at VARCHAR, 
	ranking_suppressed INTEGER DEFAULT 0 NOT NULL, 
	priority_score FLOAT DEFAULT 0 NOT NULL, 
	priority_reasons_json TEXT DEFAULT '[]' NOT NULL, 
	evidence_json TEXT DEFAULT '[]' NOT NULL, 
	workflow_run_id VARCHAR, 
	calculated_at VARCHAR NOT NULL, 
	PRIMARY KEY (current_user_id, task_id), 
	FOREIGN KEY(task_id) REFERENCES tasks (task_id), 
	FOREIGN KEY(workflow_run_id) REFERENCES workflow_runs (run_id)
);

CREATE TABLE IF NOT EXISTS d10_audit_events (
	audit_id VARCHAR NOT NULL, 
	organization_id VARCHAR NOT NULL, 
	actor VARCHAR NOT NULL, 
	request_id VARCHAR NOT NULL, 
	entity_type VARCHAR NOT NULL, 
	entity_id VARCHAR NOT NULL, 
	event_type VARCHAR NOT NULL, 
	before_json TEXT NOT NULL, 
	after_json TEXT NOT NULL, 
	reason TEXT, 
	source VARCHAR NOT NULL, 
	created_at VARCHAR NOT NULL, 
	PRIMARY KEY (audit_id), 
	FOREIGN KEY(entity_id) REFERENCES d10_business_actions (business_action_id)
);

CREATE TABLE IF NOT EXISTS d10_outbox_events (
	event_id VARCHAR NOT NULL, 
	organization_id VARCHAR NOT NULL, 
	business_action_id VARCHAR NOT NULL, 
	event_type VARCHAR NOT NULL, 
	payload_json TEXT NOT NULL, 
	dedupe_key VARCHAR NOT NULL, 
	status VARCHAR DEFAULT 'PENDING' NOT NULL, 
	attempt_count INTEGER DEFAULT 0 NOT NULL, 
	next_attempt_at VARCHAR, 
	lease_owner VARCHAR, 
	lease_until VARCHAR, 
	published_at VARCHAR, 
	last_error TEXT, 
	created_at VARCHAR NOT NULL, 
	updated_at VARCHAR NOT NULL, 
	PRIMARY KEY (event_id), 
	FOREIGN KEY(business_action_id) REFERENCES d10_business_actions (business_action_id), 
	CONSTRAINT uq_d10_outbox_action UNIQUE (organization_id, business_action_id), 
	CONSTRAINT uq_d10_outbox_dedupe UNIQUE (organization_id, dedupe_key)
);

CREATE TABLE IF NOT EXISTS d12_human_reviews (
	review_id VARCHAR NOT NULL, 
	organization_id VARCHAR NOT NULL, 
	order_id VARCHAR NOT NULL, 
	action_case_id VARCHAR NOT NULL, 
	task_id VARCHAR NOT NULL, 
	action_type VARCHAR NOT NULL, 
	target_type VARCHAR NOT NULL, 
	target_id VARCHAR NOT NULL, 
	payload_json TEXT NOT NULL, 
	payload_hash VARCHAR NOT NULL, 
	state_version VARCHAR NOT NULL, 
	state_snapshot_json TEXT NOT NULL, 
	requested_by VARCHAR NOT NULL, 
	requester_role VARCHAR NOT NULL, 
	required_review VARCHAR NOT NULL, 
	idempotency_key VARCHAR NOT NULL, 
	d10_request_id VARCHAR NOT NULL, 
	reason TEXT, 
	status VARCHAR DEFAULT 'PENDING' NOT NULL, 
	decision TEXT, 
	reviewed_by VARCHAR, 
	reviewer_role VARCHAR, 
	created_at VARCHAR NOT NULL, 
	reviewed_at VARCHAR, 
	expires_at VARCHAR NOT NULL, 
	consumed_at VARCHAR, 
	business_action_id VARCHAR, 
	result_json TEXT, 
	policy_version VARCHAR NOT NULL, 
	updated_at VARCHAR NOT NULL, 
	PRIMARY KEY (review_id), 
	FOREIGN KEY(order_id) REFERENCES orders (order_id), 
	FOREIGN KEY(action_case_id) REFERENCES action_cases (action_case_id), 
	FOREIGN KEY(task_id) REFERENCES d9_action_case_tasks (task_id), 
	FOREIGN KEY(business_action_id) REFERENCES d10_business_actions (business_action_id), 
	CONSTRAINT uq_d12_review_org_idem UNIQUE (organization_id, idempotency_key)
);

CREATE TABLE IF NOT EXISTS d15_execution_trace_events (
	trace_id TEXT NOT NULL, 
	event_id TEXT NOT NULL, 
	organization_id TEXT NOT NULL, 
	sequence_no INTEGER NOT NULL, 
	event_type TEXT NOT NULL, 
	state TEXT NOT NULL, 
	error_kind TEXT, 
	request_id TEXT NOT NULL, 
	idempotency_key TEXT NOT NULL, 
	attempt INTEGER DEFAULT '0' NOT NULL, 
	dispatch_started INTEGER DEFAULT '0' NOT NULL, 
	result_known INTEGER DEFAULT '0' NOT NULL, 
	external_effect_status TEXT DEFAULT 'UNKNOWN' NOT NULL, 
	response_meta_json TEXT DEFAULT '{}' NOT NULL, 
	actor TEXT, 
	created_at TEXT NOT NULL, 
	PRIMARY KEY (trace_id), 
	FOREIGN KEY(event_id) REFERENCES d10_outbox_events (event_id), 
	CONSTRAINT uq_d15_trace_event_seq UNIQUE (event_id, sequence_no)
);

CREATE TABLE IF NOT EXISTS d15_outbox_execution_state (
	event_id TEXT NOT NULL, 
	organization_id TEXT NOT NULL, 
	business_action_id TEXT NOT NULL, 
	request_id TEXT NOT NULL, 
	idempotency_key TEXT NOT NULL, 
	state TEXT DEFAULT 'PENDING' NOT NULL, 
	retry_budget INTEGER DEFAULT '3' NOT NULL, 
	attempt_count INTEGER DEFAULT '0' NOT NULL, 
	dispatch_started INTEGER DEFAULT '0' NOT NULL, 
	result_known INTEGER DEFAULT '0' NOT NULL, 
	external_effect_status TEXT DEFAULT 'UNKNOWN' NOT NULL, 
	error_kind TEXT, 
	user_message_code TEXT DEFAULT 'ACTION_PENDING' NOT NULL, 
	reconciliation_status TEXT, 
	next_attempt_at TEXT, 
	last_attempt_at TEXT, 
	resolved_at TEXT, 
	created_at TEXT NOT NULL, 
	updated_at TEXT NOT NULL, 
	PRIMARY KEY (event_id), 
	FOREIGN KEY(event_id) REFERENCES d10_outbox_events (event_id), 
	FOREIGN KEY(business_action_id) REFERENCES d10_business_actions (business_action_id), 
	CONSTRAINT uq_d15_execution_idempotency UNIQUE (organization_id, idempotency_key)
);

CREATE INDEX IF NOT EXISTS idx_agent_chat_jobs_user_status ON agent_chat_jobs (current_user_id, status, created_at);

CREATE INDEX IF NOT EXISTS idx_agent_runs_user_time ON agent_runs (current_user_id, created_at);

CREATE INDEX IF NOT EXISTS idx_runs_org ON agent_runs (organization_id);

CREATE INDEX IF NOT EXISTS idx_analytics_event_time ON analytics_events (event_name, server_timestamp);

CREATE INDEX IF NOT EXISTS idx_analytics_order ON analytics_events (order_id, server_timestamp);

CREATE INDEX IF NOT EXISTS idx_analytics_run ON analytics_events (run_id, server_timestamp);

CREATE INDEX IF NOT EXISTS idx_analytics_user_time ON analytics_events (user_id, server_timestamp);

CREATE INDEX IF NOT EXISTS idx_audit_action ON audit_logs (action);

CREATE INDEX IF NOT EXISTS idx_audit_actor ON audit_logs (actor_user_id);

CREATE INDEX IF NOT EXISTS idx_audit_org ON audit_logs (organization_id);

CREATE INDEX IF NOT EXISTS idx_comm_drafts_order ON communication_drafts (order_id, order_no);

CREATE INDEX IF NOT EXISTS idx_comm_drafts_status ON communication_drafts (human_status, run_status);

CREATE INDEX IF NOT EXISTS idx_comm_events_entity ON communication_events (entity_type, entity_id, created_at);

CREATE INDEX IF NOT EXISTS idx_comm_candidates_order ON communication_task_candidates (order_id, order_no);

CREATE INDEX IF NOT EXISTS idx_comm_candidates_status ON communication_task_candidates (review_status, run_status);

CREATE INDEX IF NOT EXISTS idx_comm_runs_workflow ON communication_workflow_runs (workflow_code, created_at);

CREATE INDEX IF NOT EXISTS idx_d13_runs_org_user_time ON d13_agent_runs (organization_id, current_user_id, created_at);

CREATE INDEX IF NOT EXISTS idx_d13_runs_status ON d13_agent_runs (organization_id, status, created_at);

CREATE INDEX IF NOT EXISTS idx_d16_flag_events_org_time ON d16_feature_flag_events (organization_id, created_at);

CREATE INDEX IF NOT EXISTS idx_d16_flags_org_key ON d16_feature_flag_overrides (organization_id, flag_key, scope_type, scope_id);

CREATE INDEX IF NOT EXISTS idx_d9_trace_entity ON d9_trace_events (entity_type, entity_id, created_at);

CREATE INDEX IF NOT EXISTS idx_d9_trace_org ON d9_trace_events (organization_id);

CREATE INDEX IF NOT EXISTS idx_erp_snapshots_external ON erp_read_snapshots (doctype, external_id);

CREATE INDEX IF NOT EXISTS idx_erp_snapshots_fetched ON erp_read_snapshots (fetched_at);

CREATE INDEX IF NOT EXISTS idx_erp_snapshots_org ON erp_read_snapshots (organization_id, doctype);

CREATE INDEX IF NOT EXISTS idx_erp_sync_state_org ON erp_sync_state (organization_id);

CREATE INDEX IF NOT EXISTS idx_erp_sync_state_status ON erp_sync_state (sync_status);

CREATE INDEX IF NOT EXISTS idx_events_entity ON event_logs (entity_type, entity_id);

CREATE INDEX IF NOT EXISTS idx_events_org ON event_logs (organization_id);

CREATE INDEX IF NOT EXISTS idx_order_corrections_batch ON order_corrections (batch_id);

CREATE INDEX IF NOT EXISTS idx_order_corrections_order ON order_corrections (order_id);

CREATE INDEX IF NOT EXISTS idx_orders_action_readiness ON orders (action_readiness, requested_delivery_date);

CREATE INDEX IF NOT EXISTS idx_orders_org ON orders (organization_id);

CREATE INDEX IF NOT EXISTS idx_orders_owner ON orders (owner);

CREATE INDEX IF NOT EXISTS idx_workflow_runs_key_time ON workflow_runs (workflow_key, created_at);

CREATE INDEX IF NOT EXISTS idx_action_cases_intent ON action_cases (action_intent_key, lifecycle_status);

CREATE INDEX IF NOT EXISTS idx_action_cases_org_order ON action_cases (organization_id, order_id, lifecycle_status);

CREATE INDEX IF NOT EXISTS idx_action_cases_stage ON action_cases (stage, lifecycle_status);

CREATE UNIQUE INDEX IF NOT EXISTS uq_action_cases_active ON action_cases (organization_id, order_id, action_intent_key);

CREATE INDEX IF NOT EXISTS idx_agent_calls_run_time ON agent_tool_calls (run_id, created_at);

CREATE INDEX IF NOT EXISTS idx_anomalies_order_status ON anomaly_candidates (order_id, status, severity);

CREATE INDEX IF NOT EXISTS idx_bulk_update_batch_status ON bulk_update_candidates (batch_id, status);

CREATE INDEX IF NOT EXISTS idx_bulk_update_order ON bulk_update_candidates (order_id, created_at);

CREATE INDEX IF NOT EXISTS idx_d13_trace_run_seq ON d13_agent_trace_events (run_id, sequence_no);

CREATE INDEX IF NOT EXISTS idx_reports_user_date ON daily_inspection_reports (current_user_id, inspection_date);

CREATE INDEX IF NOT EXISTS idx_fact_conflicts_order ON fact_conflicts (order_id, resolved_at);

CREATE INDEX IF NOT EXISTS idx_intake_jobs_org ON intake_jobs (organization_id);

CREATE INDEX IF NOT EXISTS idx_intake_jobs_status_time ON intake_jobs (status, created_at);

CREATE INDEX IF NOT EXISTS idx_logistics_order_status ON logistics_events (order_id, status, event_time);

CREATE INDEX IF NOT EXISTS idx_dependencies_order_status ON order_dependencies (order_id, status);

CREATE INDEX IF NOT EXISTS idx_order_import_rows_batch ON order_import_rows (batch_id);

CREATE INDEX IF NOT EXISTS idx_order_import_rows_classification ON order_import_rows (classification);

CREATE INDEX IF NOT EXISTS idx_source_messages_org ON source_messages (organization_id);

CREATE INDEX IF NOT EXISTS idx_approvals_org ON approval_requests (organization_id);

CREATE INDEX IF NOT EXISTS idx_approvals_status_role ON approval_requests (status, required_role, created_at);

CREATE INDEX IF NOT EXISTS idx_reviews_order ON candidate_reviews (order_id);

CREATE INDEX IF NOT EXISTS idx_reviews_org ON candidate_reviews (organization_id);

CREATE INDEX IF NOT EXISTS idx_reviews_status ON candidate_reviews (status, created_at);

CREATE INDEX IF NOT EXISTS idx_d9_tasks_case_status ON d9_action_case_tasks (action_case_id, status);

CREATE INDEX IF NOT EXISTS idx_d9_tasks_org_case ON d9_action_case_tasks (organization_id, action_case_id);

CREATE INDEX IF NOT EXISTS idx_quality_events_order ON quality_events (order_id, resolved_at);

CREATE INDEX IF NOT EXISTS idx_tasks_order ON tasks (related_order_id);

CREATE INDEX IF NOT EXISTS idx_tasks_org ON tasks (organization_id);

CREATE INDEX IF NOT EXISTS idx_tasks_owner ON tasks (owner_user_id);

CREATE INDEX IF NOT EXISTS idx_tasks_waiting ON tasks (waiting_on, promised_reply_at);

CREATE INDEX IF NOT EXISTS idx_d10_business_actions_case ON d10_business_actions (organization_id, action_case_id, created_at);

CREATE INDEX IF NOT EXISTS idx_d9_waitings_case ON d9_action_case_waitings (action_case_id);

CREATE INDEX IF NOT EXISTS idx_d9_waitings_due_scan ON d9_action_case_waitings (organization_id, status, due_at);

CREATE INDEX IF NOT EXISTS idx_d9_waitings_task_status ON d9_action_case_waitings (task_id, status);

CREATE UNIQUE INDEX IF NOT EXISTS uq_d9_waitings_active ON d9_action_case_waitings (task_id);

CREATE INDEX IF NOT EXISTS idx_risks_order ON risk_signals (order_id);

CREATE INDEX IF NOT EXISTS idx_rankings_user_score ON task_rankings (current_user_id, priority_score);

CREATE INDEX IF NOT EXISTS idx_d10_audit_entity ON d10_audit_events (organization_id, entity_type, entity_id, created_at);

CREATE INDEX IF NOT EXISTS idx_d10_outbox_pending ON d10_outbox_events (organization_id, status, next_attempt_at, created_at);

CREATE INDEX IF NOT EXISTS idx_d12_reviews_queue ON d12_human_reviews (organization_id, status, required_review, created_at);

CREATE INDEX IF NOT EXISTS idx_d12_reviews_task ON d12_human_reviews (organization_id, task_id, created_at);

CREATE INDEX IF NOT EXISTS idx_d15_trace_event_seq ON d15_execution_trace_events (event_id, sequence_no);

CREATE INDEX IF NOT EXISTS idx_d15_execution_state_queue ON d15_outbox_execution_state (organization_id, state, next_attempt_at, updated_at);

-- Backend-only FlowOrder uses its own authenticated RBAC layer. The direct
-- CloudBase PostgreSQL login performs server-side transactions; never expose
-- this credential to browser code.
GRANT USAGE ON SCHEMA floworder TO cloudbase_postgres;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA floworder TO cloudbase_postgres;
ALTER DEFAULT PRIVILEGES IN SCHEMA floworder
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO cloudbase_postgres;
