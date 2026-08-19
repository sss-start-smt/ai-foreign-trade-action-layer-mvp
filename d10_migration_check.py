"""
FlowOrder D10 — Migration / Schema independent verification (Attack F).

Runs `alembic upgrade head` on a FRESH DB (deployment-faithful), then proves:
  F1. head reached; 4 D10 tables exist.
  F3. UNIQUE(organization_id, task_id) and PK(organization_id, idempotency_key)
      are actually enforced (raw duplicate insert -> IntegrityError).
  F4. FK from d10_business_actions.task_id -> d9_action_case_tasks is enforced.
  F5. downgrade -1 then upgrade head round-trips (tables dropped then recreated).

Also verifies the schema.sql bootstrap path creates the 4 D10 tables (F2).
"""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
PY = r"C:\Users\smt10\.workbuddy\binaries\python\versions\3.13.12\python.exe"
SCRATCH = Path(tempfile.gettempdir()) / "floworder_d10_attack"
SCRATCH.mkdir(parents=True, exist_ok=True)
ALEMBIC_DB = SCRATCH / "alembic.db"
SCHEMA_DB = SCRATCH / "schema_bootstrap.db"
URL = f"sqlite:///{str(ALEMBIC_DB).replace(chr(92), '/')}"

D10_TABLES = ("d10_business_actions", "d10_outbox_events", "d10_idempotency_records", "d10_audit_events")
NOW = "2026-08-14T11:00:00+08:00"
RESULTS = {}


def run_alembic(args):
    env = {**os.environ, "DATABASE_URL": URL, "PYTHONPATH": str(HERE)}
    p = subprocess.run([PY, "-m", "alembic", *args], cwd=str(HERE),
                       env=env, capture_output=True, text=True)
    return p


def tables_present(path):
    c = sqlite3.connect(str(path))
    c.execute("PRAGMA foreign_keys = ON")
    rows = [r[0] for r in c.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name IN "
        "(?,?,?,?)", D10_TABLES).fetchall()]
    c.close()
    return sorted(rows)


def index_names(path):
    c = sqlite3.connect(str(path))
    rows = [r[0] for r in c.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'uq_d10%' "
        "OR name LIKE 'idx_d10%'").fetchall()]
    c.close()
    return sorted(rows)


def seed_minimal(path):
    c = sqlite3.connect(str(path), timeout=30)
    c.execute("PRAGMA foreign_keys = ON")
    c.execute("DELETE FROM orders WHERE order_id='ORD-1'")
    c.execute("DELETE FROM action_cases WHERE action_case_id='AC-1'")
    c.execute("DELETE FROM d9_action_case_tasks WHERE task_id='TK-1'")
    c.execute("INSERT INTO orders(order_id,order_no,status,owner,created_at,updated_at) VALUES(?,?,?,?,?,?)",
              ("ORD-1", "ORD-1", "ACTIVE", "U1", NOW, NOW))
    c.execute("""INSERT INTO action_cases(action_case_id,organization_id,order_id,action_intent_key,intent_type,
              stage,lifecycle_status,first_seen_at,last_seen_at,version,created_at,updated_at)
              VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
              ("AC-1", "ORG-A", "ORD-1", "v1:DELIVERY_RECOVERY", "DELIVERY_RECOVERY", "IN_PROGRESS",
               "ACTIVE", NOW, NOW, 1, NOW, NOW))
    c.execute("""INSERT INTO d9_action_case_tasks(task_id,organization_id,action_case_id,title,status,version,created_at,updated_at)
              VALUES(?,?,?,?,?,?,?,?)""",
              ("TK-1", "ORG-A", "AC-1", "t", "IN_PROGRESS", 1, NOW, NOW))
    c.commit()
    c.close()


# ---- F1: alembic upgrade head on fresh DB ----
if ALEMBIC_DB.exists():
    ALEMBIC_DB.unlink()
p = run_alembic(["upgrade", "head"])
RESULTS["F1_upgrade_head_returncode"] = p.returncode
RESULTS["F1_upgrade_head_stderr"] = p.stderr.strip()[-2000:] if p.stderr else ""
RESULTS["F1_tables_present"] = tables_present(ALEMBIC_DB)
RESULTS["F1_tables_ok"] = sorted(RESULTS["F1_tables_present"]) == sorted(D10_TABLES)
RESULTS["F1_indexes"] = index_names(ALEMBIC_DB)
# confirm alembic_version == head
c = sqlite3.connect(str(ALEMBIC_DB))
ver = c.execute("SELECT version_num FROM alembic_version").fetchone()
c.close()
RESULTS["F1_alembic_version"] = ver[0] if ver else None
RESULTS["F1_head_expected"] = "h0e1f2a3b4c5"

# ---- F3: unique constraints actually enforced ----
seed_minimal(ALEMBIC_DB)
c = sqlite3.connect(str(ALEMBIC_DB), timeout=30)
c.execute("PRAGMA foreign_keys = ON")
f3 = {}
try:
    c.execute("""INSERT INTO d10_business_actions(business_action_id,organization_id,action_case_id,task_id,
                order_id,action_type,target_type,target_id,payload_json,request_id,idempotency_key,
                request_hash,effect_hash,status,actor,source,reason,policy_version,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
              ("BA-X1", "ORG-A", "AC-1", "TK-1", "ORD-1", "A", "T", "SO-1", "{}", "R1", "K1",
               "h1", "e1", "ACCEPTED", "U1", "S", None, "P", NOW, NOW))
    c.commit()
    # duplicate (org, task_id) must fail
    c.execute("""INSERT INTO d10_business_actions(business_action_id,organization_id,action_case_id,task_id,
                order_id,action_type,target_type,target_id,payload_json,request_id,idempotency_key,
                request_hash,effect_hash,status,actor,source,reason,policy_version,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
              ("BA-X2", "ORG-A", "AC-1", "TK-1", "ORD-1", "A", "T", "SO-1", "{}", "R2", "K2",
               "h2", "e2", "ACCEPTED", "U1", "S", None, "P", NOW, NOW))
    f3["duplicate_task_unique_raised"] = False
except sqlite3.IntegrityError:
    f3["duplicate_task_unique_raised"] = True
try:
    c.execute("""INSERT INTO d10_idempotency_records(organization_id,idempotency_key,request_hash,business_action_id,result_json,created_at)
                VALUES(?,?,?,?,?,?)""", ("ORG-A", "K1", "h1", "BA-X1", "{}", NOW))
    c.commit()
    c.execute("""INSERT INTO d10_idempotency_records(organization_id,idempotency_key,request_hash,business_action_id,result_json,created_at)
                VALUES(?,?,?,?,?,?)""", ("ORG-A", "K1", "h9", "BA-OTHER", "{}", NOW))
    f3["duplicate_idem_pk_raised"] = False
except sqlite3.IntegrityError:
    f3["duplicate_idem_pk_raised"] = True
c.close()
RESULTS["F3"] = f3
RESULTS["F3_ok"] = f3.get("duplicate_task_unique_raised") and f3.get("duplicate_idem_pk_raised")

# ---- F4: FK enforcement ----
c = sqlite3.connect(str(ALEMBIC_DB), timeout=30)
c.execute("PRAGMA foreign_keys = ON")
f4 = {}
try:
    c.execute("""INSERT INTO d10_business_actions(business_action_id,organization_id,action_case_id,task_id,
                order_id,action_type,target_type,target_id,payload_json,request_id,idempotency_key,
                request_hash,effect_hash,status,actor,source,reason,policy_version,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
              ("BA-FK1", "ORG-A", "AC-1", "TK-DNE", "ORD-1", "A", "T", "SO-1", "{}", "R3", "KF1",
               "h3", "e3", "ACCEPTED", "U1", "S", None, "P", NOW, NOW))
    f4["fk_task_violation_raised"] = False
except sqlite3.IntegrityError:
    f4["fk_task_violation_raised"] = True
c.close()
RESULTS["F4"] = f4
RESULTS["F4_ok"] = f4.get("fk_task_violation_raised")

# ---- F5: downgrade -1 then upgrade head round-trip ----
p_down = run_alembic(["downgrade", "-1"])
RESULTS["F5_downgrade_returncode"] = p_down.returncode
RESULTS["F5_tables_after_downgrade"] = tables_present(ALEMBIC_DB)
p_up = run_alembic(["upgrade", "head"])
RESULTS["F5_upgrade_returncode"] = p_up.returncode
RESULTS["F5_tables_after_upgrade"] = tables_present(ALEMBIC_DB)
RESULTS["F5_ok"] = (
    sorted(RESULTS["F5_tables_after_downgrade"]) == []
    and sorted(RESULTS["F5_tables_after_upgrade"]) == sorted(D10_TABLES)
)

# ---- F2: schema.sql bootstrap creates 4 D10 tables ----
if SCHEMA_DB.exists():
    SCHEMA_DB.unlink()
schema = (HERE / "schema.sql").read_text(encoding="utf-8")
c = sqlite3.connect(str(SCHEMA_DB), timeout=30)
c.execute("PRAGMA foreign_keys = ON")
c.executescript(schema)
c.commit()
c.close()
RESULTS["F2_schema_bootstrap_tables"] = tables_present(SCHEMA_DB)
RESULTS["F2_ok"] = sorted(RESULTS["F2_schema_bootstrap_tables"]) == sorted(D10_TABLES)

RESULTS["F_overall"] = (
    RESULTS["F1_tables_ok"] and RESULTS["F1_alembic_version"] == "h0e1f2a3b4c5"
    and RESULTS["F3_ok"] and RESULTS["F4_ok"] and RESULTS["F5_ok"] and RESULTS["F2_ok"]
)

(SCRATCH / "d10_migration_results.json").write_text(json.dumps(RESULTS, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(RESULTS, ensure_ascii=False, indent=2))
print("\nF OVERALL:", "PASS" if RESULTS["F_overall"] else "FAIL")
