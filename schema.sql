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
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS source_messages (
    message_id TEXT PRIMARY KEY,
    order_id TEXT,
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
CREATE INDEX IF NOT EXISTS idx_tasks_waiting ON tasks(waiting_on, promised_reply_at);
CREATE INDEX IF NOT EXISTS idx_risks_order ON risk_signals(order_id);
CREATE INDEX IF NOT EXISTS idx_events_entity ON event_logs(entity_type, entity_id);


CREATE TABLE IF NOT EXISTS candidate_reviews (
    review_id TEXT PRIMARY KEY,
    source_message_id TEXT,
    order_id TEXT,
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
