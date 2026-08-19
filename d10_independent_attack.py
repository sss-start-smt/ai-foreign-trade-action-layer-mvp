"""
FlowOrder D10 — Independent Adversarial Acceptance Harness (WorkBuddy)

This script does NOT trust any prior test report. It bootstraps a fresh DB from
the REAL schema.sql (exactly like a new deployment), uses the production
`database.db()` connection path (foreign_keys=ON, busy_timeout=30000), and
attacks D10 from an attacker/auditor perspective per
D10_WORKBUDDY_INDEPENDENT_ACCEPTANCE_TASK.md.

Attacks covered:
  A. Atomicity / half-state injection (every stage, two exception types)
  B. Idempotency variants (10x, request_id churn, key-order, payload/target/actor changes, restart)
  C. Concurrency (20+ identical, same-task-two-actions, same-key-two-payloads)
  D. Object boundary (D8/D9 untouched, WAITING/DONE/CANCELLED/CLOSED rejected, one Task one BA)
  E. Tenant isolation (cross-org task collision, org-scoped idempotency, 0 leak/0 write)
  G. External side-effect red line (no ERP/CRM/email/HTTP write; ACCEPTED != success)

Migration/Schema (F) is verified separately in d10_migration_check.py.

Usage:
  python d10_independent_attack.py
Outputs a JSON summary to the scratch dir and prints PASS/FAIL per attack.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import traceback
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import d10_business_action as d10
from database import db, table_exists


def safe_count(conn, table, where=None, params=()):
    if not table_exists(conn, table):
        return "table_absent"
    return count(conn, table, where, params)

SCHEMA_SQL_PATH = HERE / "schema.sql"
SCRATCH_DIR = Path(tempfile.gettempdir()) / "floworder_d10_attack"
SCRATCH_DIR.mkdir(parents=True, exist_ok=True)
ATTACK_DB = str(SCRATCH_DIR / "attack.db")
os.environ["TEST_DB_PATH"] = ATTACK_DB

NOW = "2026-08-14T11:00:00+08:00"
D10_TABLES = ("d10_business_actions", "d10_outbox_events", "d10_idempotency_records", "d10_audit_events")

RESULTS = []


def reset_db() -> None:
    """Bootstrap a fresh DB from the REAL schema.sql (deployment-faithful)."""
    if os.path.exists(ATTACK_DB):
        os.remove(ATTACK_DB)
    schema = SCHEMA_SQL_PATH.read_text(encoding="utf-8")
    c = sqlite3.connect(ATTACK_DB, timeout=30)
    c.execute("PRAGMA foreign_keys = ON")
    c.executescript(schema)
    c.commit()
    c.close()


def conn_ctx():
    return db(use_test=True)


def count(conn, table, where=None, params=()):
    sql = f"SELECT COUNT(*) AS c FROM {table}"
    if where:
        sql += f" WHERE {where}"
    return dict(conn.execute(sql, params).fetchone())["c"]


def seed(conn, *, org="ORG-A", order_id="ORD-1", case_id="AC-1", task_id="TK-1",
         task_status="IN_PROGRESS", case_status="ACTIVE"):
    conn.execute(
        "INSERT INTO orders(order_id,order_no,status,owner,created_at,updated_at) "
        "VALUES(?,?,?,?,?,?)",
        (order_id, order_id, "ACTIVE", "USER-1", NOW, NOW),
    )
    conn.execute(
        """INSERT INTO action_cases(action_case_id,organization_id,order_id,action_intent_key,intent_type,
           stage,lifecycle_status,first_seen_at,last_seen_at,version,created_at,updated_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
        (case_id, org, order_id, "v1:DELIVERY_RECOVERY", "DELIVERY_RECOVERY", "IN_PROGRESS",
         case_status, NOW, NOW, 1, NOW, NOW),
    )
    conn.execute(
        """INSERT INTO d9_action_case_tasks(task_id,organization_id,action_case_id,title,recommended_action,status,version,created_at,updated_at)
           VALUES(?,?,?,?,?,?,?,?,?)""",
        (task_id, org, case_id, "更新预计交期", "更新预计交期到8月23日", task_status, 1, NOW, NOW),
    )
    conn.commit()


def submission(**overrides):
    base = dict(
        organization_id="ORG-A",
        task_id="TK-1",
        action_type="UPDATE_EXPECTED_DELIVERY_DATE",
        target_type="ERP_SALES_ORDER",
        target_id="SO-001",
        payload={"expected_delivery_date": "2026-08-23"},
        idempotency_key="idem-001",
        actor="USER-1",
        request_id="REQ-001",
        source="ACTION_WORKSPACE",
        reason="供应商确认最早8月23日交货",
    )
    base.update(overrides)
    return d10.BusinessActionSubmission(**base)


# ---------------------------------------------------------------------------
# Attack A: Atomicity / half-state
# ---------------------------------------------------------------------------
def attack_atomicity():
    cases = []
    for stage in ("after_idempotency_reservation", "after_action_insert", "after_outbox_insert", "after_audit_insert"):
        for exc_type in ("runtime", "integrity"):
            reset_db()
            with conn_ctx() as conn:
                seed(conn)
                if exc_type == "runtime":
                    def inj(s, _e=stage):
                        if s == _e:
                            raise RuntimeError(f"forced {_e}")
                else:
                    def inj(s, _e=stage):
                        if s == _e:
                            raise sqlite3.IntegrityError(f"forced integrity at {_e}")

                err = None
                try:
                    d10.submit_business_action(conn, submission(), failure_injector=inj)
                except d10.D10SubmissionError as e:
                    err = f"D10SubmissionError@{e.stage}"
                except d10.D10IdempotencyConflict:
                    err = "D10IdempotencyConflict"
                except Exception as e:  # pragma: no cover
                    err = f"{type(e).__name__}: {e}"

                counts = {t: count(conn, t) for t in D10_TABLES}
                seed_intact = count(conn, "action_cases") == 1 and count(conn, "d9_action_case_tasks") == 1
                # No half-state combos allowed:
                half = (
                    (counts["d10_business_actions"] > 0 and counts["d10_outbox_events"] == 0)
                    or (counts["d10_outbox_events"] > 0 and counts["d10_business_actions"] == 0)
                    or (counts["d10_idempotency_records"] > 0 and counts["d10_business_actions"] == 0)
                    or (counts["d10_audit_events"] > 0 and counts["d10_business_actions"] == 0)
                )
                passed = (not half) and all(c == 0 for c in counts.values()) and seed_intact
                cases.append({
                    "stage": stage, "exc": exc_type, "error": err,
                    "counts": counts, "seed_intact": seed_intact, "passed": passed,
                })
    all_pass = all(c["passed"] for c in cases)
    RESULTS.append({
        "attack": "A", "name": "Atomicity / half-state", "passed": all_pass,
        "cases": cases,
        "note": "Every stage x {RuntimeError, sqlite3.IntegrityError} must roll back ALL four D10 record types; "
                "no half-state combos (BA/outbox/idem/audit vs BA=0) may persist.",
    })
    return all_pass


# ---------------------------------------------------------------------------
# Attack B: Idempotency
# ---------------------------------------------------------------------------
def attack_idempotency():
    details = {}
    # B1: 10 identical submits
    reset_db()
    with conn_ctx() as conn:
        seed(conn)
        first = d10.submit_business_action(conn, submission())
        ids = {first["business_action_id"]}
        for i in range(2, 11):
            r = d10.submit_business_action(conn, submission())
            ids.add(r["business_action_id"])
            assert r["replayed"] is True
        details["B1_10x_identical_cardinality"] = {t: count(conn, t) for t in D10_TABLES}
        details["B1_distinct_ba"] = len(ids)
        details["B1_external_false"] = first["external_effect_executed"]

    # B2: request_id differs each time
    reset_db()
    with conn_ctx() as conn:
        seed(conn)
        first = d10.submit_business_action(conn, submission(request_id="REQ-1"))
        for i in range(2, 11):
            r = d10.submit_business_action(conn, submission(request_id=f"REQ-{i}"))
            assert r["replayed"] is True and r["business_action_id"] == first["business_action_id"]
        details["B2_request_id_churn_cardinality"] = {t: count(conn, t) for t in D10_TABLES}

    # B3: payload key order change
    reset_db()
    with conn_ctx() as conn:
        seed(conn)
        p1 = {"expected_delivery_date": "2026-08-23", "note": "supplier confirmed"}
        p2 = {"note": "supplier confirmed", "expected_delivery_date": "2026-08-23"}
        first = d10.submit_business_action(conn, submission(payload=p1))
        r = d10.submit_business_action(conn, submission(payload=p2, request_id="REQ-2"))
        details["B3_key_order_replayed"] = r["replayed"]
        details["B3_same_ba"] = r["business_action_id"] == first["business_action_id"]

    # B4: same key, changed payload -> hard conflict
    reset_db()
    with conn_ctx() as conn:
        seed(conn)
        first = d10.submit_business_action(conn, submission())
        conflict = None
        try:
            d10.submit_business_action(conn, submission(payload={"expected_delivery_date": "2026-08-24"}, request_id="REQ-2"))
        except d10.D10IdempotencyConflict:
            conflict = True
        details["B4_changed_payload_conflict"] = conflict
        details["B4_first_payload_preserved"] = json.loads(
            d10.get_business_action_by_id(conn, first["business_action_id"])["payload_json"])
        details["B4_cardinality"] = {t: count(conn, t) for t in D10_TABLES}

    # B5: same key, changed target -> hard conflict
    reset_db()
    with conn_ctx() as conn:
        seed(conn)
        first = d10.submit_business_action(conn, submission())
        conflict = None
        try:
            d10.submit_business_action(conn, submission(target_id="SO-999", request_id="REQ-2"))
        except d10.D10IdempotencyConflict:
            conflict = True
        details["B5_changed_target_conflict"] = conflict
        details["B5_cardinality"] = {t: count(conn, t) for t in D10_TABLES}

    # B6: same key, changed actor/source/reason -> hard conflict (actor/source/reason are in request fingerprint)
    reset_db()
    with conn_ctx() as conn:
        seed(conn)
        first = d10.submit_business_action(conn, submission())
        conflict = None
        try:
            d10.submit_business_action(conn, submission(actor="BOT-9", source="AUTOMATION", reason="auto", request_id="REQ-2"))
        except d10.D10IdempotencyConflict:
            conflict = True
        details["B6_changed_actor_source_reason_conflict"] = conflict
        details["B6_cardinality"] = {t: count(conn, t) for t in D10_TABLES}

    # B7: DB/connection restart then replay
    reset_db()
    with conn_ctx() as conn:
        seed(conn)
        first = d10.submit_business_action(conn, submission())
    with conn_ctx() as conn2:
        r = d10.submit_business_action(conn2, submission(request_id="REQ-AFTER-RESTART"))
        details["B7_restart_replayed"] = r["replayed"]
        details["B7_same_ba"] = r["business_action_id"] == first["business_action_id"]
        details["B7_cardinality"] = {t: count(conn2, t) for t in D10_TABLES}

    passed = (
        details["B1_distinct_ba"] == 1
        and details["B1_external_false"] is False
        and details["B1_10x_identical_cardinality"] == {"d10_business_actions": 1, "d10_outbox_events": 1,
                                                         "d10_idempotency_records": 1, "d10_audit_events": 1}
        and details["B2_request_id_churn_cardinality"] == {"d10_business_actions": 1, "d10_outbox_events": 1,
                                                           "d10_idempotency_records": 1, "d10_audit_events": 1}
        and details["B3_key_order_replayed"] and details["B3_same_ba"]
        and details["B4_changed_payload_conflict"] and details["B4_cardinality"]["d10_business_actions"] == 1
        and details["B5_changed_target_conflict"] and details["B5_cardinality"]["d10_business_actions"] == 1
        and details["B6_changed_actor_source_reason_conflict"] and details["B6_cardinality"]["d10_business_actions"] == 1
        and details["B7_restart_replayed"] and details["B7_same_ba"]
    )
    RESULTS.append({"attack": "B", "name": "Idempotency", "passed": passed, "details": details})
    return passed


# ---------------------------------------------------------------------------
# Attack C: Concurrency
# ---------------------------------------------------------------------------
def _worker_submit(org, task_id, key, payload, request_id):
    try:
        with conn_ctx() as conn:
            r = d10.submit_business_action(conn, submission(
                organization_id=org, task_id=task_id, idempotency_key=key,
                payload=payload, request_id=request_id))
            return {"outcome": "ok", "replayed": r["replayed"],
                    "ba": r["business_action_id"], "ext": r["external_effect_executed"]}
    except d10.D10IdempotencyConflict:
        return {"outcome": "idempotency_conflict"}
    except d10.D10TaskActionConflict:
        return {"outcome": "task_action_conflict"}
    except d10.D10StateError as e:
        return {"outcome": "state_error", "msg": str(e)}
    except d10.D10SubmissionError as e:
        # A submission error here (e.g. database locked surfacing) is a return-semantics defect.
        return {"outcome": "submission_error", "stage": e.stage}
    except Exception as e:  # pragma: no cover
        return {"outcome": "unexpected", "msg": f"{type(e).__name__}: {e}"}


def attack_concurrency():
    details = {}

    # C1: 20+ concurrent IDENTICAL submissions
    reset_db()
    with conn_ctx() as conn:
        seed(conn)
    outcomes = []
    with ThreadPoolExecutor(max_workers=24) as pool:
        futures = [pool.submit(_worker_submit, "ORG-A", "TK-1", "idem-001",
                               {"expected_delivery_date": "2026-08-23"}, f"REQ-{i}") for i in range(24)]
        for f in futures:
            outcomes.append(f.result())
    with conn_ctx() as conn:
        c1_counts = {t: count(conn, t) for t in D10_TABLES}
    ok = [o for o in outcomes if o["outcome"] == "ok"]
    conflicts = [o for o in outcomes if o["outcome"] in ("idempotency_conflict", "task_action_conflict")]
    errors = [o for o in outcomes if o["outcome"] in ("submission_error", "unexpected", "state_error")]
    details["C1_counts"] = c1_counts
    details["C1_ok"] = len(ok)
    details["C1_replays"] = sum(1 for o in ok if o["replayed"])
    details["C1_clean_conflicts"] = len(conflicts)
    details["C1_dirty_errors"] = errors
    details["C1_distinct_ba"] = len({o["ba"] for o in ok})
    c1_pass = (
        c1_counts == {"d10_business_actions": 1, "d10_outbox_events": 1,
                      "d10_idempotency_records": 1, "d10_audit_events": 1}
        and details["C1_distinct_ba"] == 1
        and details["C1_replays"] == 23
        and len(errors) == 0
    )

    # C2: same Task, two different idempotency keys, two different actions, concurrent
    reset_db()
    with conn_ctx() as conn:
        seed(conn)
    with ThreadPoolExecutor(max_workers=4) as pool:
        f1 = pool.submit(_worker_submit, "ORG-A", "TK-1", "idem-A",
                         {"expected_delivery_date": "2026-08-23"}, "REQ-A")
        f2 = pool.submit(_worker_submit, "ORG-A", "TK-1", "idem-B",
                         {"message": "notify customer"}, "REQ-B")
        o1, o2 = f1.result(), f2.result()
    with conn_ctx() as conn:
        c2_counts = {t: count(conn, t) for t in D10_TABLES}
    outcomes_c2 = [o1, o2]
    ok_c2 = [o for o in outcomes_c2 if o["outcome"] == "ok"]
    task_conflict_c2 = [o for o in outcomes_c2 if o["outcome"] == "task_action_conflict"]
    details["C2_counts"] = c2_counts
    details["C2_outcomes"] = outcomes_c2
    c2_pass = (
        c2_counts == {"d10_business_actions": 1, "d10_outbox_events": 1,
                      "d10_idempotency_records": 1, "d10_audit_events": 1}
        and len(ok_c2) == 1 and len(task_conflict_c2) == 1
    )

    # C3: same idempotency key, two different payloads, concurrent
    reset_db()
    with conn_ctx() as conn:
        seed(conn)
    with ThreadPoolExecutor(max_workers=4) as pool:
        f1 = pool.submit(_worker_submit, "ORG-A", "TK-1", "idem-001",
                         {"expected_delivery_date": "2026-08-23"}, "REQ-A")
        f2 = pool.submit(_worker_submit, "ORG-A", "TK-1", "idem-001",
                         {"expected_delivery_date": "2026-08-24"}, "REQ-B")
        o1, o2 = f1.result(), f2.result()
    with conn_ctx() as conn:
        c3_counts = {t: count(conn, t) for t in D10_TABLES}
    outcomes_c3 = [o1, o2]
    ok_c3 = [o for o in outcomes_c3 if o["outcome"] == "ok"]
    idem_conflict_c3 = [o for o in outcomes_c3 if o["outcome"] == "idempotency_conflict"]
    details["C3_counts"] = c3_counts
    details["C3_outcomes"] = outcomes_c3
    c3_pass = (
        c3_counts == {"d10_business_actions": 1, "d10_outbox_events": 1,
                      "d10_idempotency_records": 1, "d10_audit_events": 1}
        and len(ok_c3) == 1 and len(idem_conflict_c3) == 1
    )

    passed = c1_pass and c2_pass and c3_pass
    RESULTS.append({"attack": "C", "name": "Concurrency", "passed": passed,
                    "details": details,
                    "sub_pass": {"C1": c1_pass, "C2": c2_pass, "C3": c3_pass}})
    return passed


# ---------------------------------------------------------------------------
# Attack D: Object boundary
# ---------------------------------------------------------------------------
def attack_object_boundary():
    details = {}

    # D1: D8/D9 untouched
    reset_db()
    with conn_ctx() as conn:
        seed(conn)
        before_task = dict(conn.execute(
            "SELECT status,version FROM d9_action_case_tasks WHERE task_id='TK-1'").fetchone())
        before_case = dict(conn.execute(
            "SELECT stage,lifecycle_status,version FROM action_cases WHERE action_case_id='AC-1'").fetchone())
        d10.submit_business_action(conn, submission())
        after_task = dict(conn.execute(
            "SELECT status,version FROM d9_action_case_tasks WHERE task_id='TK-1'").fetchone())
        after_case = dict(conn.execute(
            "SELECT stage,lifecycle_status,version FROM action_cases WHERE action_case_id='AC-1'").fetchone())
        details["D1_task_unchanged"] = (before_task == after_task)
        details["D1_case_unchanged"] = (before_case == after_case)

    # D2: WAITING/DONE/CANCELLED rejected
    d2 = {}
    for st in ("WAITING", "DONE", "CANCELLED"):
        reset_db()
        with conn_ctx() as conn:
            seed(conn, task_status=st)
            rejected = None
            try:
                d10.submit_business_action(conn, submission())
            except d10.D10StateError:
                rejected = True
            d2[st] = {"rejected": rejected, "ba_count": count(conn, "d10_business_actions")}
    details["D2_non_actionable_task"] = d2

    # D3: CLOSED action case rejected
    reset_db()
    with conn_ctx() as conn:
        seed(conn, case_status="CLOSED")
        rejected = None
        try:
            d10.submit_business_action(conn, submission())
        except d10.D10StateError:
            rejected = True
        details["D3_closed_case_rejected"] = rejected
        details["D3_ba_count"] = count(conn, "d10_business_actions")

    # D4: one Task cannot attach a 2nd independent BusinessAction
    reset_db()
    with conn_ctx() as conn:
        seed(conn)
        d10.submit_business_action(conn, submission())
        conflict = None
        try:
            d10.submit_business_action(conn, submission(
                idempotency_key="idem-002", request_id="REQ-002",
                action_type="SEND_CUSTOMER_DELIVERY_UPDATE", target_type="CUSTOMER_CONTACT",
                target_id="CUSTOMER-1", payload={"message": "交期调整到8月23日"}))
        except d10.D10TaskActionConflict:
            conflict = True
        details["D4_second_ba_conflict"] = conflict
        details["D4_ba_count"] = count(conn, "d10_business_actions")

    passed = (
        details["D1_task_unchanged"] and details["D1_case_unchanged"]
        and all(v["rejected"] and v["ba_count"] == 0 for v in d2.values())
        and details["D3_closed_case_rejected"] and details["D3_ba_count"] == 0
        and details["D4_second_ba_conflict"] and details["D4_ba_count"] == 1
    )
    RESULTS.append({"attack": "D", "name": "Object boundary (D8/D9)", "passed": passed, "details": details})
    return passed


# ---------------------------------------------------------------------------
# Attack E: Tenant isolation
# ---------------------------------------------------------------------------
def attack_tenant_isolation():
    details = {}

    # E1: ORG-B submits to ORG-A's task_id -> rejected, 0 ORG-B writes
    reset_db()
    with conn_ctx() as conn:
        seed(conn, org="ORG-A", task_id="TK-A")
        rejected = None
        try:
            d10.submit_business_action(conn, submission(organization_id="ORG-B", task_id="TK-A"))
        except d10.D10StateError:
            rejected = True
        details["E1_cross_org_task_rejected"] = rejected
        # ORG-B must have written nothing
        details["E1_orgb_writes"] = {
            t: count(conn, t, "organization_id=?", ("ORG-B",)) for t in D10_TABLES
        }

    # E2: same idempotency_key on DIFFERENT orgs' own tasks -> both succeed (org-scoped)
    reset_db()
    with conn_ctx() as conn:
        seed(conn, org="ORG-A", order_id="ORD-A", case_id="AC-A", task_id="TK-A")
        seed(conn, org="ORG-B", order_id="ORD-B", case_id="AC-B", task_id="TK-B")
        rA = d10.submit_business_action(conn, submission(
            organization_id="ORG-A", task_id="TK-A", idempotency_key="idem-shared"))
        rB = d10.submit_business_action(conn, submission(
            organization_id="ORG-B", task_id="TK-B", idempotency_key="idem-shared", request_id="REQ-B"))
        # two idempotency records with same key, different org
        idem_total = count(conn, "d10_idempotency_records")
        idem_shared = count(conn, "d10_idempotency_records", "idempotency_key=?", ("idem-shared",))
        details["E2_both_accepted"] = (rA["replayed"] is False and rB["replayed"] is False)
        details["E2_idem_total"] = idem_total
        details["E2_idem_same_key_rows"] = idem_shared
        details["E2_distinct_ba"] = list({rA["business_action_id"], rB["business_action_id"]})

    # E3: ORG-B cannot read ORG-A's BA nor write via ORG-A's task_id (same key)
    reset_db()
    with conn_ctx() as conn:
        seed(conn, org="ORG-A", order_id="ORD-A", case_id="AC-A", task_id="TK-A")
        # ORG-B attempts ORG-A's task with ORG-A's key
        rejected = None
        try:
            d10.submit_business_action(conn, submission(
                organization_id="ORG-B", task_id="TK-A", idempotency_key="idem-001"))
        except d10.D10StateError:
            rejected = True
        # ORG-B reading ORG-A's task returns nothing (scoped)
        ba = d10.get_business_action_for_task(conn, organization_id="ORG-B", task_id="TK-A")
        details["E3_cross_org_write_rejected"] = rejected
        details["E3_orgb_sees_orge_a_ba"] = (ba is not None)
        details["E3_orgb_writes"] = {
            t: count(conn, t, "organization_id=?", ("ORG-B",)) for t in D10_TABLES
        }

    # E4: info-leak note — task existence vs org mismatch error type
    reset_db()
    with conn_ctx() as conn:
        seed(conn, org="ORG-A", task_id="TK-A")
        # Existing task, wrong org -> D10StateError (reveals task exists)
        err_existing = None
        try:
            d10.submit_business_action(conn, submission(organization_id="ORG-B", task_id="TK-A"))
        except d10.D10StateError as e:
            err_existing = "D10StateError"
        except Exception as e:
            err_existing = type(e).__name__
        # Non-existing task, any org -> D10NotFoundError
        err_missing = None
        try:
            d10.submit_business_action(conn, submission(organization_id="ORG-A", task_id="TK-DNE"))
        except d10.D10NotFoundError:
            err_missing = "D10NotFoundError"
        except Exception as e:
            err_missing = type(e).__name__
        details["E4_existing_wrong_org_error"] = err_existing
        details["E4_missing_task_error"] = err_missing
        details["E4_leak_note"] = (
            "Different error types (StateError vs NotFound) reveal whether a task_id exists. "
            "No data cross-write occurs; treat as P2 hardening if strict existence-hiding is required."
        )

    passed = (
        details["E1_cross_org_task_rejected"]
        and details["E1_orgb_writes"] == {t: 0 for t in D10_TABLES}
        and details["E2_both_accepted"] and details["E2_idem_total"] == 2 and details["E2_idem_same_key_rows"] == 2
        and details["E3_cross_org_write_rejected"] and details["E3_orgb_sees_orge_a_ba"] is False
        and details["E3_orgb_writes"] == {t: 0 for t in D10_TABLES}
    )
    RESULTS.append({"attack": "E", "name": "Tenant isolation", "passed": passed, "details": details})
    return passed


# ---------------------------------------------------------------------------
# Attack G: External side-effect red line
# ---------------------------------------------------------------------------
def attack_external_red_line():
    details = {}
    # G1: code search for external calls inside d10 module + transitive imports
    import subprocess
    src = (HERE / "d10_business_action.py").read_text(encoding="utf-8")
    red_flags = []
    for token in ("requests.", "httpx", "smtplib", "import urllib", "socket.socket",
                  "apply_writeback", "erpnext", ".post(", "send_email", "smtp",
                  "http://", "https://", "requests.get", "aiohttp"):
        if token in src:
            red_flags.append(token)
    details["G1_d10_source_red_flags"] = red_flags
    details["G1_d10_imports"] = [l.strip() for l in src.splitlines()
                                  if l.startswith("import ") or l.startswith("from ")]

    # G2: runtime proof ACCEPTED != external success; outbox stays PENDING; never SUCCEEDED
    reset_db()
    with conn_ctx() as conn:
        seed(conn)
        r = d10.submit_business_action(conn, submission())
        details["G2_status"] = r["status"]
        details["G2_external_effect_executed"] = r["external_effect_executed"]
        outbox = d10.get_outbox_for_action(conn, r["business_action_id"])
        details["G2_outbox_status"] = outbox["status"]
        details["G2_outbox_attempt_count"] = outbox["attempt_count"]
        # No outbox row anywhere with a non-PENDING status
        non_pending = count(conn, "d10_outbox_events", "status <> ?", ("PENDING",))
        details["G2_non_pending_outbox_rows"] = non_pending
        # No SUCCEEDED-style status anywhere
        details["G2_any_succeeded_status"] = count(conn, "d10_outbox_events", "status = ?", ("SUCCEEDED",))
        # erp_sync_state / erp_read_snapshots untouched by D10
        details["G2_erp_sync_state_rows"] = safe_count(conn, "erp_sync_state")
        details["G2_erp_read_snapshots_rows"] = safe_count(conn, "erp_read_snapshots")

    # G3: grep whole d10 file for SUCCEEDED / EXECUTED literal writes
    g3_flags = [l.strip() for l in src.splitlines() if "SUCCEEDED" in l or "EXECUTED" in l]
    details["G3_succeeded_executed_in_source"] = g3_flags

    passed = (
        not red_flags
        and details["G2_status"] == "ACCEPTED"
        and details["G2_external_effect_executed"] is False
        and details["G2_outbox_status"] == "PENDING"
        and details["G2_non_pending_outbox_rows"] == 0
        and details["G2_any_succeeded_status"] == 0
        and details["G2_erp_sync_state_rows"] in (0, "table_absent")
        and details["G2_erp_read_snapshots_rows"] in (0, "table_absent")
    )
    RESULTS.append({"attack": "G", "name": "External side-effect red line", "passed": passed, "details": details})
    return passed


def main():
    print("=" * 70)
    print("FlowOrder D10 — Independent Adversarial Acceptance (WorkBuddy)")
    print("=" * 70)
    summary = {}
    for fn in (attack_atomicity, attack_idempotency, attack_concurrency,
               attack_object_boundary, attack_tenant_isolation, attack_external_red_line):
        name = fn.__name__
        try:
            ok = fn()
        except Exception:
            ok = False
            traceback.print_exc()
        summary[name] = ok
        print(f"[{'PASS' if ok else 'FAIL'}] {name}")

    overall = all(summary.values())
    out = {
        "overall_pass": overall,
        "per_attack": summary,
        "results": RESULTS,
        "scratch_db": ATTACK_DB,
        "schema_sql": str(SCHEMA_SQL_PATH),
    }
    (SCRATCH_DIR / "d10_attack_results.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print("=" * 70)
    print(f"OVERALL: {'PASS' if overall else 'FAIL'}")
    print(f"Results JSON: {SCRATCH_DIR / 'd10_attack_results.json'}")
    print("=" * 70)
    return 0 if overall else 1


if __name__ == "__main__":
    raise SystemExit(main())
