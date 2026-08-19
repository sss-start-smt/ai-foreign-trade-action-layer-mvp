"""
FlowOrder D8 — WorkBuddy INDEPENDENT acceptance verification.

This script does NOT trust the repo's existing test report or test names.
It exercises the real D8 functions (and the real D7->D8 bridge) with
hand-crafted attack scenarios written by the independent verifier.

Run:  PYTHONPATH=. python wb_d8_independent_verification.py
"""

import os
import sys
import tempfile
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine, text

sys.path.insert(0, os.path.dirname(__file__))

import d8_action_case as d8  # noqa: E402
from database import _ConnectionWrapper  # noqa: E402

CN_TZ = timezone(timedelta(hours=8))

RESULTS = []


def record(name, ok, detail=""):
    RESULTS.append((name, ok, detail))
    status = "PASS" if ok else "FAIL"
    print(f"[{status}] {name}" + (f"  -- {detail}" if detail else ""))


# --------------------------------------------------------------------------
# DB setup
# --------------------------------------------------------------------------

SCHEMA = """
CREATE TABLE orders (
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
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE tasks (
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
    updated_at TEXT NOT NULL
);
CREATE TABLE logistics_events (
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
    updated_at TEXT NOT NULL
);
CREATE TABLE commitment_history (
    commitment_id TEXT PRIMARY KEY,
    order_id TEXT NOT NULL,
    commitment_type TEXT NOT NULL,
    commitment_value TEXT NOT NULL,
    source_message_id TEXT,
    confirmed_by TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE action_cases (
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
CREATE UNIQUE INDEX uq_action_cases_active
    ON action_cases(organization_id, order_id, action_intent_key)
    WHERE lifecycle_status = 'ACTIVE';
CREATE INDEX idx_action_cases_org_order
    ON action_cases(organization_id, order_id, lifecycle_status);
CREATE INDEX idx_action_cases_stage
    ON action_cases(stage, lifecycle_status);
CREATE INDEX idx_action_cases_intent
    ON action_cases(action_intent_key, lifecycle_status);
"""


def make_db():
    tmp = tempfile.mkdtemp(prefix="wb_d8_verify_")
    db_path = os.path.join(tmp, "verify.db")
    engine = create_engine(f"sqlite:///{db_path}")
    raw = engine.connect()
    wrapper = _ConnectionWrapper(raw, is_sqlite=True)
    wrapper.executescript(SCHEMA)
    wrapper.commit()
    return engine, wrapper


def insert_order(conn, **kw):
    defaults = dict(
        order_id="ORD", order_no=None, customer_name="C", product_name="P",
        packaging_method="X", requested_delivery_date=None,
        latest_supplier_commitment=None, current_progress=None, current_node=None,
        status="ACTIVE", owner=None, organization_id="ORG-A",
        action_readiness="BASE_ONLY", contact_status="UNKNOWN", issue_status="UNKNOWN",
        created_at="2026-08-01T00:00:00+08:00", updated_at="2026-08-01T00:00:00+08:00",
    )
    defaults.update(kw)
    if not defaults.get("order_no"):
        defaults["order_no"] = defaults["order_id"]
    cols = list(defaults.keys())
    ph = ",".join(["?"] * len(cols))
    conn.execute(
        f"INSERT INTO orders ({','.join(cols)}) VALUES ({ph})",
        tuple(defaults[c] for c in cols),
    )
    conn.commit()


def insert_logistics(conn, event_id, order_id, status, resolved_at=None,
                     description="exception"):
    conn.execute(
        "INSERT INTO logistics_events (logistics_event_id, order_id, event_type, "
        "status, description, resolved_at, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (event_id, order_id, "EXCEPTION", status, description, resolved_at,
         "2026-08-01T00:00:00+08:00", "2026-08-01T00:00:00+08:00"),
    )
    conn.commit()


def obs_item(order_id, org, risk_signals, bucket="DO_NOW", severity="HIGH",
             recommended="act"):
    return dict(
        order_id=order_id,
        organization_id=org,
        risk_signals=risk_signals,
        action_bucket=bucket,
        severity=severity,
        recommended_action=recommended,
        evidence=[f"ev-{order_id}"],
    )


def rs(risk_type, severity="HIGH", evidence=None, rs_id=None):
    return dict(
        risk_signal_id=rs_id or f"RS-{uuid.uuid4().hex[:8].upper()}",
        order_id="",
        risk_type=risk_type,
        severity=severity,
        status="OPEN",
        evidence=evidence or [f"{risk_type} evidence"],
        missing_information=[],
    )


def d7_result_with_observations(items, org="ORG-A", user="USER-1", role="operator",
                                topn_items=None):
    return dict(
        policy_version="D7_RISK_POLICY_V1",
        scope=dict(organization_id=org, user_id=user, user_role=role),
        my_action_items=topn_items if topn_items is not None else items,
        team_action_items=[],
        unassigned_orders=[],
        items=topn_items if topn_items is not None else items,
        information_gaps=[],
        action_case_observations=items,
    )


def ident(user, org, role):
    return dict(user_id=user, organization_id=org, role=role)


# ==========================================================================
# ATTACKS
# ==========================================================================

def attack_identity_10x_reuse():
    """IV: same (org, order, intent) reconciled 10x => 1 ACTIVE, stable id."""
    engine, conn = make_db()
    insert_order(conn, order_id="ORD-1", owner="USER-1", organization_id="ORG-A")
    idy = ident("USER-1", "ORG-A", "operator")
    first_id = None
    for i in range(10):
        d7 = d7_result_with_observations(
            [obs_item("ORD-1", "ORG-A", [rs("DELIVERY_RISK")])],
            user="USER-1", role="operator")
        d8.reconcile_action_cases(conn, d7, identity=idy)
        cases = d8.list_cases(conn, organization_id="ORG-A", order_id="ORD-1",
                               lifecycle_status="ACTIVE")
        if len(cases) != 1:
            record("IV.identity.10x_reuse", False,
                   f"round {i}: expected 1 ACTIVE, got {len(cases)}")
            return
        if first_id is None:
            first_id = cases[0]["action_case_id"]
        elif cases[0]["action_case_id"] != first_id:
            record("IV.identity.10x_reuse", False,
                   f"round {i}: case_id changed {first_id} -> {cases[0]['action_case_id']}")
            return
    record("IV.identity.10x_reuse", True,
           f"10 reconciles => 1 stable ACTIVE case {first_id}, intent={cases[0]['action_intent_key']}")


def attack_identity_deterministic_independent():
    """IV: intent key must not depend on UUID/priority_score/bucket."""
    engine, conn = make_db()
    insert_order(conn, order_id="ORD-D", owner="USER-1", organization_id="ORG-A")
    idy = ident("USER-1", "ORG-A", "operator")
    # First reconcile with one rs_id, high priority, DO_NOW bucket
    d7a = d7_result_with_observations(
        [obs_item("ORD-D", "ORG-A", [rs("DELIVERY_RISK", rs_id="RS-AAAAAAAAAA")],
                  bucket="DO_NOW", severity="HIGH")],
        user="USER-1", role="operator")
    d7a["my_action_items"][0]["priority_score"] = 999.0
    d8.reconcile_action_cases(conn, d7a, identity=idy)
    cases_a = d8.list_cases(conn, organization_id="ORG-A", order_id="ORD-D",
                            lifecycle_status="ACTIVE")
    id_a = cases_a[0]["action_case_id"]
    key_a = cases_a[0]["action_intent_key"]
    # Second reconcile with DIFFERENT rs_id, different priority, different bucket
    d7b = d7_result_with_observations(
        [obs_item("ORD-D", "ORG-A", [rs("DELIVERY_RISK", rs_id="RS-BBBBBBBBBB")],
                  bucket="DO_TODAY", severity="LOW")],
        user="USER-1", role="operator")
    d7b["my_action_items"][0]["priority_score"] = 1.0
    d8.reconcile_action_cases(conn, d7b, identity=idy)
    cases_b = d8.list_cases(conn, organization_id="ORG-A", order_id="ORD-D",
                            lifecycle_status="ACTIVE")
    if len(cases_b) != 1:
        record("IV.identity.deterministic", False, f"expected reuse, got {len(cases_b)} cases")
        return
    if cases_b[0]["action_case_id"] != id_a or cases_b[0]["action_intent_key"] != key_a:
        record("IV.identity.deterministic", False,
               f"identity changed despite only rs_id/priority/bucket differing")
        return
    record("IV.identity.deterministic", True,
           f"intent key={key_a} stable; rs_id/priority_score/bucket ignored")


def attack_multi_intent():
    """IV: same order, CUSTOMER_CONFIRMATION + LOGISTICS_RECOVERY => 2 cases."""
    engine, conn = make_db()
    insert_order(conn, order_id="ORD-M", owner="USER-1", organization_id="ORG-A")
    idy = ident("USER-1", "ORG-A", "operator")
    d7 = d7_result_with_observations(
        [obs_item("ORD-M", "ORG-A", [rs("CUSTOMER_CONFIRMATION_BLOCKING"),
                                      rs("LOGISTICS_EXCEPTION")])],
        user="USER-1", role="operator")
    d8.reconcile_action_cases(conn, d7, identity=idy)
    cases = d8.list_cases(conn, organization_id="ORG-A", order_id="ORD-M",
                          lifecycle_status="ACTIVE")
    keys = {c["action_intent_key"] for c in cases}
    ok = len(cases) == 2 and "v1:CUSTOMER_CONFIRMATION" in keys and "v1:LOGISTICS_RECOVERY" in keys
    record("IV.multi_intent.parallel", ok,
           f"2 parallel ACTIVE cases: {sorted(keys)}" if ok else f"got {keys}")


def attack_rootcause_suppression():
    """V: DELIVERY_RISK + LOGISTICS_EXCEPTION => only LOGISTICS_RECOVERY."""
    engine, conn = make_db()
    insert_order(conn, order_id="ORD-R1", owner="USER-1", organization_id="ORG-A")
    idy = ident("USER-1", "ORG-A", "operator")
    d7 = d7_result_with_observations(
        [obs_item("ORD-R1", "ORG-A", [rs("DELIVERY_RISK"), rs("LOGISTICS_EXCEPTION")])],
        user="USER-1", role="operator")
    d8.reconcile_action_cases(conn, d7, identity=idy)
    cases = d8.list_cases(conn, organization_id="ORG-A", order_id="ORD-R1",
                          lifecycle_status="ACTIVE")
    keys = {c["action_intent_key"] for c in cases}
    ok = len(cases) == 1 and keys == {"v1:LOGISTICS_RECOVERY"}
    record("V.rootcause.delivery+logistics", ok,
           f"only LOGISTICS_RECOVERY: {keys}" if ok else f"WRONG: {keys}")

    # DELIVERY_RISK + SUPPLIER_COMMITMENT_OVERDUE => only SUPPLIER_FOLLOWUP
    engine, conn = make_db()
    insert_order(conn, order_id="ORD-R2", owner="USER-1", organization_id="ORG-A")
    d7 = d7_result_with_observations(
        [obs_item("ORD-R2", "ORG-A", [rs("DELIVERY_RISK"), rs("SUPPLIER_COMMITMENT_OVERDUE")])],
        user="USER-1", role="operator")
    d8.reconcile_action_cases(conn, d7, identity=idy)
    cases = d8.list_cases(conn, organization_id="ORG-A", order_id="ORD-R2",
                          lifecycle_status="ACTIVE")
    keys = {c["action_intent_key"] for c in cases}
    ok = len(cases) == 1 and keys == {"v1:SUPPLIER_FOLLOWUP"}
    record("V.rootcause.delivery+supplier", ok,
           f"only SUPPLIER_FOLLOWUP: {keys}" if ok else f"WRONG: {keys}")

    # DELIVERY_RISK only => DELIVERY_RECOVERY
    engine, conn = make_db()
    insert_order(conn, order_id="ORD-R3", owner="USER-1", organization_id="ORG-A")
    d7 = d7_result_with_observations(
        [obs_item("ORD-R3", "ORG-A", [rs("DELIVERY_RISK")])],
        user="USER-1", role="operator")
    d8.reconcile_action_cases(conn, d7, identity=idy)
    cases = d8.list_cases(conn, organization_id="ORG-A", order_id="ORD-R3",
                          lifecycle_status="ACTIVE")
    keys = {c["action_intent_key"] for c in cases}
    ok = len(cases) == 1 and keys == {"v1:DELIVERY_RECOVERY"}
    record("V.rootcause.delivery_only", ok,
           f"DELIVERY_RECOVERY created: {keys}" if ok else f"WRONG: {keys}")


def attack_fsm_legal():
    """VI: legal transitions succeed with version bump."""
    engine, conn = make_db()
    insert_order(conn, order_id="ORD-F", owner="USER-1", organization_id="ORG-A")
    created = d8.create_action_case(
        conn, organization_id="ORG-A", order_id="ORD-F",
        action_intent_key="v1:INFORMATION_COMPLETION",
        intent_type="INFORMATION_COMPLETION", stage="NEEDS_JUDGMENT")
    cid = created["action_case_id"]
    idy = ident("MANAGER-A", "ORG-A", "manager")
    path = ["READY_FOR_ACTION", "IN_PROGRESS", "WAITING_RESULT",
            "RESUMED_OR_ESCALATED", "CLOSED"]
    prev = "NEEDS_JUDGMENT"
    v = 1
    ok = True
    detail = ""
    for nxt in path:
        try:
            updated = d8.transition_action_case(
                conn, cid, nxt,
                close_reason="RESOLVED" if nxt == "CLOSED" else None,
                identity=idy)
        except Exception as e:  # noqa: BLE001
            ok = False
            detail = f"legal {prev}->{nxt} raised {type(e).__name__}: {e}"
            break
        v += 1
        if updated["stage"] != nxt or updated["version"] != v:
            ok = False
            detail = f"legal {prev}->{nxt}: stage={updated['stage']} version={updated['version']} expected {nxt}/{v}"
            break
        prev = nxt
    record("VI.fsm.legal_path", ok, detail or "NEEDS_JUDGMENT->...->CLOSED OK, versions 1..6")


def attack_fsm_illegal():
    """VI: illegal transitions rejected, stage/version unchanged; CLOSED frozen."""
    engine, conn = make_db()
    insert_order(conn, order_id="ORD-X", owner="USER-1", organization_id="ORG-A")
    created = d8.create_action_case(
        conn, organization_id="ORG-A", order_id="ORD-X",
        action_intent_key="v1:LOGISTICS_RECOVERY",
        intent_type="LOGISTICS_RECOVERY", stage="NEEDS_JUDGMENT")
    cid = created["action_case_id"]
    idy = ident("MANAGER-A", "ORG-A", "manager")
    illegal = [
        ("NEEDS_JUDGMENT", "IN_PROGRESS"),
        ("NEEDS_JUDGMENT", "WAITING_RESULT"),
        ("READY_FOR_ACTION", "WAITING_RESULT"),
        ("READY_FOR_ACTION", "RESUMED_OR_ESCALATED"),
        ("IN_PROGRESS", "READY_FOR_ACTION"),
        ("WAITING_RESULT", "IN_PROGRESS"),
    ]
    # bring to specific stage for each illegal test by recreating fresh case
    all_ok = True
    msgs = []
    for from_stage, to_stage in illegal:
        e2, c2 = make_db()
        insert_order(c2, order_id="O", owner="USER-1", organization_id="ORG-A")
        # build a case at from_stage via legal path
        c = d8.create_action_case(c2, organization_id="ORG-A", order_id="O",
                                  action_intent_key="v1:X", intent_type="X",
                                  stage=from_stage)
        ccid = c["action_case_id"]
        before_stage = from_stage
        before_ver = c["version"]
        raised = False
        try:
            d8.transition_action_case(c2, ccid, to_stage, identity=idy)
        except d8.ActionCaseFSMError:
            raised = True
        except Exception as e:  # noqa: BLE001
            msgs.append(f"{from_stage}->{to_stage}: wrong exc {type(e).__name__}")
            all_ok = False
        after = d8.get_case_by_id(c2, ccid)
        if not raised:
            all_ok = False
            msgs.append(f"{from_stage}->{to_stage}: NOT rejected")
        if after["stage"] != before_stage or after["version"] != before_ver:
            all_ok = False
            msgs.append(f"{from_stage}->{to_stage}: state changed stage={after['stage']} ver={after['version']}")
    # CLOSED cannot advance
    e3, c3 = make_db()
    insert_order(c3, order_id="O", owner="USER-1", organization_id="ORG-A")
    c = d8.create_action_case(c3, organization_id="ORG-A", order_id="O",
                              action_intent_key="v1:X", intent_type="X",
                              stage="READY_FOR_ACTION")
    d8.transition_action_case(c3, c["action_case_id"], "CLOSED",
                              close_reason="RESOLVED", identity=idy)
    raised = False
    try:
        d8.transition_action_case(c3, c["action_case_id"], "READY_FOR_ACTION", identity=idy)
    except d8.ActionCaseFSMError:
        raised = True
    except Exception:
        raised = False
    if not raised:
        all_ok = False
        msgs.append("CLOSED->READY_FOR_ACTION: NOT rejected")
    record("VI.fsm.illegal_rejected", all_ok, "; ".join(msgs) or "all illegal transitions rejected, state frozen")


def attack_fsm_close_reason():
    """VI: close requires valid reason; RESOLVED is the success marker."""
    engine, conn = make_db()
    insert_order(conn, order_id="ORD-C", owner="USER-1", organization_id="ORG-A")
    created = d8.create_action_case(conn, organization_id="ORG-A", order_id="ORD-C",
                                    action_intent_key="v1:X", intent_type="X",
                                    stage="READY_FOR_ACTION")
    cid = created["action_case_id"]
    idy = ident("MANAGER-A", "ORG-A", "manager")
    # no reason
    no_reason = False
    try:
        d8.transition_action_case(conn, cid, "CLOSED", identity=idy)
    except ValueError:
        no_reason = True
    # invalid reason
    bad_reason = False
    try:
        d8.transition_action_case(conn, cid, "CLOSED", close_reason="NONSENSE", identity=idy)
    except ValueError:
        bad_reason = True
    # valid RESOLVED
    ok_resolved = True
    try:
        u = d8.transition_action_case(conn, cid, "CLOSED", close_reason="RESOLVED", identity=idy)
        if u["lifecycle_status"] != "CLOSED" or u["close_reason"] != "RESOLVED":
            ok_resolved = False
    except Exception as e:  # noqa: BLE001
        ok_resolved = False
        bad_reason = bad_reason  # keep
    # valid DISMISSED (not a "success" resolution)
    e2, c2 = make_db()
    insert_order(c2, order_id="ORD-C2", owner="USER-1", organization_id="ORG-A")
    c2c = d8.create_action_case(c2, organization_id="ORG-A", order_id="ORD-C2",
                                action_intent_key="v1:X", intent_type="X",
                                stage="READY_FOR_ACTION")
    dismissed_ok = True
    try:
        u = d8.transition_action_case(c2, c2c["action_case_id"], "CLOSED",
                                      close_reason="DISMISSED", identity=idy)
        if u["close_reason"] != "DISMISSED":
            dismissed_ok = False
    except Exception:  # noqa: BLE001
        dismissed_ok = False
    ok = no_reason and bad_reason and ok_resolved and dismissed_ok
    record("VI.fsm.close_reason", ok,
           f"no_reason={no_reason} bad_reason={bad_reason} resolved={ok_resolved} dismissed={dismissed_ok}")


def attack_bucket_no_auto_advance():
    """VI: Action Bucket DO_TODAY->DO_NOW must NOT auto-advance FSM stage."""
    engine, conn = make_db()
    insert_order(conn, order_id="ORD-BK", owner="USER-1", organization_id="ORG-A")
    d7 = d7_result_with_observations(
        [obs_item("ORD-BK", "ORG-A", [rs("DELIVERY_RISK")], bucket="DO_TODAY")],
        user="USER-1", role="operator")
    d8.reconcile_action_cases(conn, d7, identity=ident("USER-1", "ORG-A", "operator"))
    before = d8.list_cases(conn, organization_id="ORG-A", order_id="ORD-BK",
                           lifecycle_status="ACTIVE")[0]
    # Next round: bucket flips to DO_NOW (priority change), but FSM stays READY_FOR_ACTION
    d7b = d7_result_with_observations(
        [obs_item("ORD-BK", "ORG-A", [rs("DELIVERY_RISK")], bucket="DO_NOW")],
        user="USER-1", role="operator")
    d8.reconcile_action_cases(conn, d7b, identity=ident("USER-1", "ORG-A", "operator"))
    after = d8.list_cases(conn, organization_id="ORG-A", order_id="ORD-BK",
                          lifecycle_status="ACTIVE")[0]
    ok = (after["stage"] == "READY_FOR_ACTION"
          and after["stage"] == before["stage"]
          and after["version"] == before["version"] == 1
          and after["latest_action_bucket"] == "DO_NOW")
    record("VI.fsm.bucket_no_auto_advance", ok,
           f"stage={after['stage']} ver={after['version']} bucket={after['latest_action_bucket']}")


def attack_auth_cross_org_transition():
    """VII.A: ORG-B case, ORG-A manager transition => rejected, state frozen."""
    engine, conn = make_db()
    insert_order(conn, order_id="ORD-BX", owner="USER-9", organization_id="ORG-B")
    created = d8.create_action_case(conn, organization_id="ORG-B", order_id="ORD-BX",
                                    action_intent_key="v1:LOGISTICS_RECOVERY",
                                    intent_type="LOGISTICS_RECOVERY",
                                    stage="READY_FOR_ACTION")
    cid = created["action_case_id"]
    before_ver = created["version"]
    idy_a = ident("MANAGER-A", "ORG-A", "manager")
    rejected = False
    try:
        d8.transition_action_case(conn, cid, "IN_PROGRESS", identity=idy_a)
    except d8.ActionCaseAuthError:
        rejected = True
    after = d8.get_case_by_id(conn, cid)
    ok = rejected and after["stage"] == "READY_FOR_ACTION" and after["version"] == before_ver
    record("VII.A.cross_org_transition", ok,
           f"rejected={rejected} stage={after['stage']} ver={after['version']}")


def attack_auth_other_operator():
    """VII.B: ORD-1 owner=USER-1; USER-2 operator transition => rejected."""
    engine, conn = make_db()
    insert_order(conn, order_id="ORD-1", owner="USER-1", organization_id="ORG-A")
    created = d8.create_action_case(conn, organization_id="ORG-A", order_id="ORD-1",
                                    action_intent_key="v1:LOGISTICS_RECOVERY",
                                    intent_type="LOGISTICS_RECOVERY",
                                    stage="READY_FOR_ACTION")
    cid = created["action_case_id"]
    before_ver = created["version"]
    idy2 = ident("USER-2", "ORG-A", "operator")
    rejected = False
    try:
        d8.transition_action_case(conn, cid, "IN_PROGRESS", identity=idy2)
    except d8.ActionCaseAuthError:
        rejected = True
    after = d8.get_case_by_id(conn, cid)
    ok = rejected and after["stage"] == "READY_FOR_ACTION" and after["version"] == before_ver
    record("VII.B.other_operator", ok,
           f"rejected={rejected} stage={after['stage']} ver={after['version']}")


def attack_auth_manager_legal():
    """VII.C: ORG-A manager operates ORG-A case => success."""
    engine, conn = make_db()
    insert_order(conn, order_id="ORD-AM", owner="USER-9", organization_id="ORG-A")
    created = d8.create_action_case(conn, organization_id="ORG-A", order_id="ORD-AM",
                                    action_intent_key="v1:LOGISTICS_RECOVERY",
                                    intent_type="LOGISTICS_RECOVERY",
                                    stage="READY_FOR_ACTION")
    cid = created["action_case_id"]
    idy = ident("MANAGER-A", "ORG-A", "manager")
    ok = False
    detail = ""
    try:
        u = d8.transition_action_case(conn, cid, "IN_PROGRESS", identity=idy)
        ok = u["stage"] == "IN_PROGRESS" and u["version"] == 2
        detail = f"manager advanced to {u['stage']} v{u['version']}"
    except Exception as e:  # noqa: BLE001
        detail = f"unexpected {type(e).__name__}: {e}"
    record("VII.C.manager_legal", ok, detail)


def attack_reconcile_injection():
    """VII.D: payload order belongs to ORG-B (claimed ORG-A) => whole reconcile
    rejected, NO partial write; also order-not-in-DB => rejected."""
    # Case 1: mixed payload, injection among valid items
    engine, conn = make_db()
    insert_order(conn, order_id="ORD-A", owner="USER-1", organization_id="ORG-A")
    insert_order(conn, order_id="ORD-B", owner="USER-1", organization_id="ORG-B")
    idy = ident("USER-1", "ORG-A", "operator")
    d7 = d7_result_with_observations([
        obs_item("ORD-A", "ORG-A", [rs("DELIVERY_RISK")]),          # valid
        obs_item("ORD-B", "ORG-A", [rs("DELIVERY_RISK")]),          # injection: DB org is ORG-B
    ], user="USER-1", role="operator")
    rejected = False
    try:
        d8.reconcile_action_cases(conn, d7, identity=idy)
    except d8.ReconcileAuthError:
        rejected = True
    all_cases = d8.list_cases(conn, organization_id="ORG-A")
    no_partial = len(all_cases) == 0
    ok1 = rejected and no_partial

    # Case 2: order not in DB at all
    engine, conn = make_db()
    insert_order(conn, order_id="ORD-A", owner="USER-1", organization_id="ORG-A")
    d7 = d7_result_with_observations([
        obs_item("ORD-A", "ORG-A", [rs("DELIVERY_RISK")]),
        obs_item("ORD-GHOST", "ORG-A", [rs("DELIVERY_RISK")]),
    ], user="USER-1", role="operator")
    rejected2 = False
    try:
        d8.reconcile_action_cases(conn, d7, identity=idy)
    except d8.ReconcileAuthError:
        rejected2 = True
    cases2 = d8.list_cases(conn, organization_id="ORG-A")
    ok2 = rejected2 and len(cases2) == 0
    record("VII.D.reconcile_injection", ok1 and ok2,
           f"mixed_injection_rejected={rejected} no_partial_write={no_partial}; "
           f"ghost_order_rejected={rejected2} no_write={len(cases2)==0}")


def attack_observation_pollution():
    """VIII: USER-2 reconciles only ORD-2; USER-1/ORD-1 stays OBSERVED."""
    engine, conn = make_db()
    insert_order(conn, order_id="ORD-1", owner="USER-1", organization_id="ORG-A")
    insert_order(conn, order_id="ORD-2", owner="USER-2", organization_id="ORG-A")
    # USER-1 reconcile ORD-1
    d7 = d7_result_with_observations([obs_item("ORD-1", "ORG-A", [rs("DELIVERY_RISK")])],
                                     user="USER-1", role="operator")
    d8.reconcile_action_cases(conn, d7, identity=ident("USER-1", "ORG-A", "operator"))
    # USER-2 reconcile only ORD-2
    d7 = d7_result_with_observations([obs_item("ORD-2", "ORG-A", [rs("DELIVERY_RISK")])],
                                     user="USER-2", role="operator")
    d8.reconcile_action_cases(conn, d7, identity=ident("USER-2", "ORG-A", "operator"))
    # USER-2 reconciles AGAIN (ORD-1 not in his scope)
    d7 = d7_result_with_observations([obs_item("ORD-2", "ORG-A", [rs("DELIVERY_RISK")])],
                                     user="USER-2", role="operator")
    d8.reconcile_action_cases(conn, d7, identity=ident("USER-2", "ORG-A", "operator"))
    c1 = d8.list_cases(conn, organization_id="ORG-A", order_id="ORD-1",
                       lifecycle_status="ACTIVE")
    ok = len(c1) == 1 and c1[0]["observation_status"] == "OBSERVED"
    record("VIII.observation_pollution", ok,
           f"USER-1/ORD-1 observation_status={c1[0]['observation_status'] if c1 else 'NONE'}")


def attack_topn_not_observation():
    """IX: ORD-1 drops out of my_action_items Top-N but stays OBSERVED via full feed."""
    engine, conn = make_db()
    insert_order(conn, order_id="ORD-1", owner="USER-1", organization_id="ORG-A")
    # Round 1: full feed includes ORD-1
    d7 = d7_result_with_observations([obs_item("ORD-1", "ORG-A", [rs("LOGISTICS_EXCEPTION")])],
                                     user="USER-1", role="operator")
    d8.reconcile_action_cases(conn, d7, identity=ident("USER-1", "ORG-A", "operator"))
    # Round 2: Top-N (my_action_items) EMPTY, but full observation feed still has ORD-1
    d7 = dict(
        policy_version="D7_RISK_POLICY_V1",
        scope=dict(organization_id="ORG-A", user_id="USER-1", user_role="operator"),
        my_action_items=[],            # Top-N truncated: ORD-1 dropped
        team_action_items=[],
        unassigned_orders=[],
        items=[],
        information_gaps=[],
        action_case_observations=[obs_item("ORD-1", "ORG-A", [rs("LOGISTICS_EXCEPTION")])],
    )
    d8.reconcile_action_cases(conn, d7, identity=ident("USER-1", "ORG-A", "operator"))
    c1 = d8.list_cases(conn, organization_id="ORG-A", order_id="ORD-1",
                       lifecycle_status="ACTIVE")
    ok = len(c1) == 1 and c1[0]["observation_status"] == "OBSERVED"
    record("IX.topn_not_observation", ok,
           f"ORD-1 observation_status={c1[0]['observation_status'] if c1 else 'NONE'} "
           f"(Top-N empty but full feed kept it OBSERVED)")


def attack_zero_risk_e2e():
    """X: zero-risk screened order => NOT_OBSERVED, still ACTIVE, not closed;
    and a zero-risk order with no prior case creates nothing."""
    import d7_risk_engine as d7  # noqa: E402
    CUR = "2026-08-12T12:00:00+08:00"

    # Round 1: ORD-Z has LOGISTICS_EXCEPTION
    engine, conn = make_db()
    insert_order(conn, order_id="ORD-Z", owner="USER-1", organization_id="ORG-A",
                 requested_delivery_date="2026-09-30", current_progress=0.8,
                 current_node="PACKING", latest_supplier_commitment="2026-09-20")
    insert_logistics(conn, "LE-Z", "ORD-Z", "EXCEPTION", resolved_at=None)
    idy = ident("USER-1", "ORG-A", "operator")
    r1 = d7.run_d7_pipeline(conn, idy, top_n=7, current_time=CUR,
                            include_erp_snapshot=False)
    obs_z1 = [o for o in r1["action_case_observations"] if o["order_id"] == "ORD-Z"]
    has_exception = bool(obs_z1) and any(
        s["risk_type"] == "LOGISTICS_EXCEPTION" for s in obs_z1[0].get("risk_signals", []))
    d8.reconcile_action_cases(conn, r1, identity=idy)
    cz1 = d8.list_cases(conn, organization_id="ORG-A", order_id="ORD-Z",
                        lifecycle_status="ACTIVE")
    created_observed = len(cz1) == 1 and cz1[0]["observation_status"] == "OBSERVED"

    # Round 2: resolve logistics -> zero risk, ORD-Z still in scope
    conn.execute(
        "UPDATE logistics_events SET resolved_at=? WHERE logistics_event_id=?",
        ("2026-08-12T12:05:00+08:00", "LE-Z"))
    conn.commit()
    r2 = d7.run_d7_pipeline(conn, idy, top_n=7, current_time=CUR,
                            include_erp_snapshot=False)
    obs_z2 = [o for o in r2["action_case_observations"] if o["order_id"] == "ORD-Z"]
    zero_signal = bool(obs_z2) and len(obs_z2[0].get("risk_signals", [])) == 0
    d8.reconcile_action_cases(conn, r2, identity=idy)
    cz2 = d8.list_cases(conn, organization_id="ORG-A", order_id="ORD-Z",
                        lifecycle_status="ACTIVE")
    not_observed = (len(cz2) == 1
                    and cz2[0]["observation_status"] == "NOT_OBSERVED"
                    and cz2[0]["lifecycle_status"] == "ACTIVE"
                    and cz2[0]["stage"] == cz1[0]["stage"]
                    and cz2[0]["close_reason"] is None)
    record("X.zero_risk.screened", has_exception and created_observed and zero_signal and not_observed,
           f"r1_has_exception={has_exception} created_observed={created_observed} "
           f"r2_zero_signal={zero_signal} not_observed_active={not_observed}")

    # Zero-risk order with no prior case => no case created
    engine, conn = make_db()
    insert_order(conn, order_id="ORD-0", owner="USER-1", organization_id="ORG-A",
                 requested_delivery_date="2026-09-30", current_progress=0.8,
                 current_node="PACKING", latest_supplier_commitment="2026-09-20")
    r0 = d7.run_d7_pipeline(conn, idy, top_n=7, current_time=CUR,
                            include_erp_snapshot=False)
    obs0 = [o for o in r0["action_case_observations"] if o["order_id"] == "ORD-0"]
    zero0 = bool(obs0) and len(obs0[0].get("risk_signals", [])) == 0
    d8.reconcile_action_cases(conn, r0, identity=idy)
    c0 = d8.list_cases(conn, organization_id="ORG-A", order_id="ORD-0",
                       lifecycle_status="ACTIVE")
    record("X.zero_risk.no_case_created", zero0 and len(c0) == 0,
           f"ORD-0 in feed={bool(obs0)} zero_signal={zero0} cases_created={len(c0)}")


def attack_information_gap_e2e():
    """XI: INFORMATION_GAP-only order => v1:INFORMATION_COMPLETION, NEEDS_JUDGMENT."""
    import d7_risk_engine as d7  # noqa: E402
    CUR = "2026-08-12T12:00:00+08:00"
    engine, conn = make_db()
    # Missing current_node / current_progress / latest_supplier_commitment => INFORMATION_GAP
    insert_order(conn, order_id="ORD-IG", owner="USER-1", organization_id="ORG-A",
                 requested_delivery_date="2026-09-30", current_progress=None,
                 current_node=None, latest_supplier_commitment=None)
    idy = ident("USER-1", "ORG-A", "operator")
    r = d7.run_d7_pipeline(conn, idy, top_n=7, current_time=CUR,
                           include_erp_snapshot=False)
    obs_ig = [o for o in r["action_case_observations"] if o["order_id"] == "ORD-IG"]
    is_gap = bool(obs_ig) and any(
        s["risk_type"] == "INFORMATION_GAP" for s in obs_ig[0].get("risk_signals", []))
    d8.reconcile_action_cases(conn, r, identity=idy)
    c = d8.list_cases(conn, organization_id="ORG-A", order_id="ORD-IG",
                      lifecycle_status="ACTIVE")
    ok = is_gap and len(c) == 1 and c[0]["action_intent_key"] == "v1:INFORMATION_COMPLETION" \
        and c[0]["stage"] == "NEEDS_JUDGMENT"
    record("XI.information_gap", ok,
           f"gap_in_feed={is_gap} intent={c[0]['action_intent_key'] if c else None} "
           f"stage={c[0]['stage'] if c else None}")


def attack_legacy_fallback_no_not_observed():
    """XII: legacy fallback (no action_case_observations) must NOT drive NOT_OBSERVED."""
    engine, conn = make_db()
    insert_order(conn, order_id="ORD-1", owner="USER-1", organization_id="ORG-A")
    # First create a case via full feed
    d7 = d7_result_with_observations([obs_item("ORD-1", "ORG-A", [rs("DELIVERY_RISK")])],
                                     user="USER-1", role="operator")
    d8.reconcile_action_cases(conn, d7, identity=ident("USER-1", "ORG-A", "operator"))
    # Now reconcile with NO action_case_observations key, and Top-N empty (order dropped)
    d7_legacy = dict(
        policy_version="D7_RISK_POLICY_V1",
        scope=dict(organization_id="ORG-A", user_id="USER-1", user_role="operator"),
        my_action_items=[],     # empty: ORD-1 dropped from ranked queue
        team_action_items=[],
        unassigned_orders=[],
        items=[],
        information_gaps=[],
        # NOTE: no action_case_observations key at all
    )
    d8.reconcile_action_cases(conn, d7_legacy, identity=ident("USER-1", "ORG-A", "operator"))
    c = d8.list_cases(conn, organization_id="ORG-A", order_id="ORD-1",
                      lifecycle_status="ACTIVE")
    # Contrast: prove that IF legacy fallback wrongly called mark_cases_not_observed,
    # it WOULD flip to NOT_OBSERVED. The fact it stays OBSERVED proves the guard.
    would_have_flipped = False
    if c:
        d8.mark_cases_not_observed(conn, organization_id="ORG-A",
                                   observed_case_keys=set(),
                                   scope_order_ids={"ORD-1"})
        c2 = d8.get_case_by_id(conn, c[0]["action_case_id"])
        would_have_flipped = c2["observation_status"] == "NOT_OBSERVED"
    ok = len(c) == 1 and c[0]["observation_status"] == "OBSERVED" and would_have_flipped
    record("XII.legacy_fallback.no_not_observed", ok,
           f"case stayed OBSERVED under legacy fallback; "
           f"(direct mark_cases_not_observed WOULD flip it={would_have_flipped})")


def attack_concurrency_cas():
    """XIII: partial unique index blocks 2 ACTIVE same-intent; CAS miss fails."""
    # 1) DB partial unique index: second ACTIVE insert with same key => IntegrityError
    engine, conn = make_db()
    insert_order(conn, order_id="ORD-1", owner="USER-1", organization_id="ORG-A")
    sql = ("INSERT INTO action_cases (action_case_id, organization_id, order_id, "
           "action_intent_key, intent_type, stage, lifecycle_status, title, "
           "observation_status, first_seen_at, last_seen_at, created_at, updated_at, version) "
           "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)")
    p = ("AC-1", "ORG-A", "ORD-1", "v1:LOGISTICS_RECOVERY", "LOGISTICS_RECOVERY",
         "READY_FOR_ACTION", "ACTIVE", "t", "OBSERVED", "2026-08-01T00:00:00+08:00",
         "2026-08-01T00:00:00+08:00", "2026-08-01T00:00:00+08:00",
         "2026-08-01T00:00:00+08:00", 1)
    conn.execute(sql, p)
    conn.commit()
    dup_failed = False
    try:
        conn.execute(sql, ("AC-2", "ORG-A", "ORD-1", "v1:LOGISTICS_RECOVERY",
                           "LOGISTICS_RECOVERY", "READY_FOR_ACTION", "ACTIVE", "t",
                           "OBSERVED", "2026-08-01T00:00:00+08:00",
                           "2026-08-01T00:00:00+08:00",
                           "2026-08-01T00:00:00+08:00",
                           "2026-08-01T00:00:00+08:00", 1))
        conn.commit()
    except Exception:  # noqa: BLE001
        dup_failed = True
    remaining = d8.list_cases(conn, organization_id="ORG-A", order_id="ORD-1",
                              lifecycle_status="ACTIVE")
    index_ok = dup_failed and len(remaining) == 1

    # 2) reconcile reuse on IntegrityError: two reconciles => 1 ACTIVE
    engine, conn = make_db()
    insert_order(conn, order_id="ORD-1", owner="USER-1", organization_id="ORG-A")
    idy = ident("USER-1", "ORG-A", "operator")
    d7 = d7_result_with_observations([obs_item("ORD-1", "ORG-A", [rs("DELIVERY_RISK")])],
                                     user="USER-1", role="operator")
    d8.reconcile_action_cases(conn, d7, identity=idy)
    d8.reconcile_action_cases(conn, d7, identity=idy)
    reuse_ok = len(d8.list_cases(conn, organization_id="ORG-A", order_id="ORD-1",
                                 lifecycle_status="ACTIVE")) == 1

    # 3) CAS miss => ActionCaseVersionConflict, state unchanged
    engine, conn = make_db()
    insert_order(conn, order_id="ORD-CAS", owner="USER-1", organization_id="ORG-A")
    created = d8.create_action_case(conn, organization_id="ORG-A", order_id="ORD-CAS",
                                    action_intent_key="v1:X", intent_type="X",
                                    stage="READY_FOR_ACTION")
    cid = created["action_case_id"]
    idy = ident("MANAGER-A", "ORG-A", "manager")
    # valid transition bumps version 1->2
    u = d8.transition_action_case(conn, cid, "IN_PROGRESS", identity=idy)
    ok_version = u["version"] == 2 and u["stage"] == "IN_PROGRESS"
    # stale transition with _expected_version=1 (already 2) => CAS miss
    cas_miss = False
    try:
        d8.transition_action_case(conn, cid, "WAITING_RESULT",
                                  identity=idy, _expected_version=1)
    except d8.ActionCaseVersionConflict:
        cas_miss = True
    after = d8.get_case_by_id(conn, cid)
    cas_state_ok = after["version"] == 2 and after["stage"] == "IN_PROGRESS"
    record("XIII.concurrency_cas", index_ok and reuse_ok and ok_version and cas_miss and cas_state_ok,
           f"partial_index_blocks_dup={index_ok} reconcile_reuse={reuse_ok} "
           f"version_bump={ok_version} cas_miss={cas_miss} state_unchanged={cas_state_ok}")


def attack_no_except_swallow():
    """XIII.1: create_action_case must not swallow real DB errors with bare except."""
    src = open(os.path.join(os.path.dirname(__file__), "d8_action_case.py"),
               encoding="utf-8").read()
    start = src.index("def create_action_case(")
    end = src.index("\ndef update_action_case_reconcile(", start)
    body = src[start:end]
    # A bare 'except Exception' or 'except:' inside create would swallow DB errors.
    has_bare = ("except Exception" in body) or ("except:" in body)
    record("XIII.no_except_swallow", not has_bare,
           "no bare 'except Exception' in create_action_case" if not has_bare
           else "FOUND bare except in create_action_case (swallows DB errors)")


def attack_observation_feed_pre_topn():
    """IX/核心命题: D7 action_case_observations is BROADER than Top-N my_action_items.

    With 10 risky orders and top_n=3, the ranked queue must be truncated to 3
    while the observation feed still contains all 10 screened orders.
    """
    import d7_risk_engine as d7  # noqa: E402
    CUR = "2026-08-12T12:00:00+08:00"
    engine, conn = make_db()
    for i in range(10):
        oid = f"ORD-P{i:02d}"
        insert_order(conn, order_id=oid, owner="USER-1", organization_id="ORG-A",
                     requested_delivery_date="2026-09-30", current_progress=0.8,
                     current_node="PACKING", latest_supplier_commitment="2026-09-20")
        insert_logistics(conn, f"LE-P{i:02d}", oid, "EXCEPTION", resolved_at=None)
    idy = ident("USER-1", "ORG-A", "operator")
    r = d7.run_d7_pipeline(conn, idy, top_n=3, current_time=CUR,
                           include_erp_snapshot=False)
    n_obs = len(r["action_case_observations"])
    n_top = len(r["my_action_items"])
    ok = n_obs == 10 and n_top == 3 and n_obs > n_top
    record("IX.observation_feed_pre_topn", ok,
           f"action_case_observations={n_obs} my_action_items(Top-N)={n_top} "
           f"(feed is pre-truncation, broader than ranked queue)")


def main():
    print("=" * 70)
    print("FlowOrder D8 — WorkBuddy INDEPENDENT acceptance verification")
    print("=" * 70)
    attack_identity_10x_reuse()
    attack_identity_deterministic_independent()
    attack_multi_intent()
    attack_rootcause_suppression()
    attack_fsm_legal()
    attack_fsm_illegal()
    attack_fsm_close_reason()
    attack_bucket_no_auto_advance()
    attack_auth_cross_org_transition()
    attack_auth_other_operator()
    attack_auth_manager_legal()
    attack_reconcile_injection()
    attack_observation_pollution()
    attack_topn_not_observation()
    attack_zero_risk_e2e()
    attack_information_gap_e2e()
    attack_legacy_fallback_no_not_observed()
    attack_observation_feed_pre_topn()
    attack_concurrency_cas()
    attack_no_except_swallow()

    passed = sum(1 for _, ok, _ in RESULTS if ok)
    failed = len(RESULTS) - passed
    print("=" * 70)
    print(f"INDEPENDENT ATTACK SUMMARY: {passed} passed / {failed} failed "
          f"(of {len(RESULTS)} attack groups)")
    print("=" * 70)
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
