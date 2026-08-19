import sqlite3
import os

DB_PATH = "data/action_layer.db"

if not os.path.exists(DB_PATH):
    print(f"Database not found at {DB_PATH}")
    exit(1)

conn = sqlite3.connect(DB_PATH)

# Add organization_id to orders if not exists
cols = [r[1] for r in conn.execute('PRAGMA table_info(orders)')]
if 'organization_id' not in cols:
    conn.execute('ALTER TABLE orders ADD COLUMN organization_id TEXT')
    print('Added organization_id to orders')
else:
    print('orders.organization_id already exists')

# Add organization_id to tasks if not exists
cols = [r[1] for r in conn.execute('PRAGMA table_info(tasks)')]
if 'organization_id' not in cols:
    conn.execute('ALTER TABLE tasks ADD COLUMN organization_id TEXT')
    print('Added organization_id to tasks')
else:
    print('tasks.organization_id already exists')

# Add organization_id to event_logs if not exists
cols = [r[1] for r in conn.execute('PRAGMA table_info(event_logs)')]
if 'organization_id' not in cols:
    conn.execute('ALTER TABLE event_logs ADD COLUMN organization_id TEXT')
    print('Added organization_id to event_logs')
else:
    print('event_logs.organization_id already exists')

# Add organization_id to approval_requests if not exists
cols = [r[1] for r in conn.execute('PRAGMA table_info(approval_requests)')]
if 'organization_id' not in cols:
    conn.execute('ALTER TABLE approval_requests ADD COLUMN organization_id TEXT')
    print('Added organization_id to approval_requests')
else:
    print('approval_requests.organization_id already exists')

# Create audit_logs table if not exists
cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='audit_logs'")
if not cursor.fetchone():
    conn.execute('''CREATE TABLE audit_logs (
        audit_id TEXT PRIMARY KEY,
        organization_id TEXT NOT NULL,
        actor_user_id TEXT NOT NULL,
        actor_role TEXT NOT NULL,
        action TEXT NOT NULL,
        entity_type TEXT NOT NULL,
        entity_id TEXT NOT NULL,
        result TEXT NOT NULL DEFAULT 'SUCCESS',
        details_json TEXT NOT NULL,
        created_at TEXT NOT NULL
    )''')
    print('Created audit_logs table')
else:
    print('audit_logs table already exists')

# Create indexes
conn.execute('CREATE INDEX IF NOT EXISTS idx_orders_org ON orders(organization_id)')
conn.execute('CREATE INDEX IF NOT EXISTS idx_tasks_org ON tasks(organization_id)')
conn.execute('CREATE INDEX IF NOT EXISTS idx_events_org ON event_logs(organization_id)')
conn.execute('CREATE INDEX IF NOT EXISTS idx_approvals_org ON approval_requests(organization_id)')
print('Created org indexes')

conn.commit()
conn.close()
print('Migration complete')
