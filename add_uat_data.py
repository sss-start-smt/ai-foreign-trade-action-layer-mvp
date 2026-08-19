"""Script to add UAT test data to the running server's database."""
import sqlite3
import os

# The server uses action_layer.db
db_path = "data/action_layer.db"

print(f"Using database: {db_path}")

if not os.path.exists(db_path):
    print(f"ERROR: Database not found at {db_path}")
    exit(1)

conn = sqlite3.connect(db_path)
conn.row_factory = sqlite3.Row

NOW = "2026-08-14T15:30:00+08:00"

# Insert UAT order
try:
    conn.execute(
        """INSERT OR IGNORE INTO orders(order_id,order_no,customer_name,product_name,requested_delivery_date,
           latest_supplier_commitment,current_node,status,owner,organization_id,created_at,updated_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
        ("ORD-D11-UAT", "SO-D11-UAT", "Northwind UAT", "帆布包", "2026-08-20", None,
         "生产中", "ACTIVE", "USER-1", "ORG-A", NOW, NOW),
    )
    print("Inserted UAT order")
except Exception as e:
    print(f"Error inserting order: {e}")

# Insert action case for the UAT order
try:
    conn.execute(
        """INSERT OR IGNORE INTO action_cases(action_case_id,organization_id,order_id,action_intent_key,intent_type,
           stage,lifecycle_status,title,latest_action_bucket,latest_severity,latest_recommended_action,
           latest_evidence_json,observation_status,first_seen_at,last_seen_at,source_policy_version,
           version,created_at,updated_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        ("AC-D11-UAT", "ORG-A", "ORD-D11-UAT", "D11:DELIVERY_RECOVERY:SO-D11-UAT",
         "DELIVERY_RECOVERY", "IN_PROGRESS", "ACTIVE", "解决 SO-D11-UAT 交期异常",
         "DO_NOW", "high", "先确认供应商能否按 8 月 20 日交货",
         '["客户正式交期为 8 月 20 日", "供应商尚未给出确认承诺"]',
         "OBSERVED", NOW, NOW, "D11_UAT_SEED", 1, NOW, NOW),
    )
    print("Inserted action case")
except Exception as e:
    print(f"Error inserting action case: {e}")

# Insert task for the action case
try:
    conn.execute(
        """INSERT OR IGNORE INTO d9_action_case_tasks(task_id,organization_id,action_case_id,title,recommended_action,
           status,version,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)""",
        ("TK-D11-UAT-1", "ORG-A", "AC-D11-UAT", "联系供应商确认 8 月 20 日能否交货",
         "联系供应商，要求给出明确可交付日期", "TODO", 1, NOW, NOW),
    )
    print("Inserted task")
except Exception as e:
    print(f"Error inserting task: {e}")

conn.commit()
conn.close()
print("Done! UAT data added successfully.")
