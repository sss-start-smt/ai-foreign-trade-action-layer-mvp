# D8 Implementation Report: Risk/Action Judgment → Deterministic Action Intent

**Status:** D8 FROZEN  
**Date:** 2026-08-12 (Revised Round 3)  
**Baseline:** D7 Final (102 passed / 0 failed / 26 skipped)  
**D8 Tests:** 49 passed / 0 failed (40 original + 6 B-series + 3 C-series)  
**Full Regression:** 419 passed / 26 skipped / 0 failed

---

## 1. Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                        FlowOrder D8 Pipeline                     │
│                                                                 │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │  D7 Risk Engine (MINIMAL CHANGE — 1 new field)            │  │
│  │  ├── risk_assessment → risk_signals                       │  │
│  │  ├── action_bucketing → my_action_items, team_action_items │  │
│  │  ├── ranking → priority_score                              │  │
│  │  └── order_results → action_case_observations  ← NEW      │  │
│  └─────────────────────────┬─────────────────────────────────┘  │
│                            │                                    │
│                            ▼                                    │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │  D8 Action Case Module (d8_action_case.py)               │  │
│  │                                                           │  │
│  │  ① _validate_order_authority()                            │  │
│  │     DB is authority for organization_id and owner         │  │
│  │     Two failure categories:                               │  │
│  │     - ReconcileAuthError (critical: abort)                │  │
│  │     - OrderNotAuthorizedError (non-critical: skip)        │  │
│  │                                                           │  │
│  │  ② derive_action_intents()                                │  │
│  │     D7 output → deterministic action_intent_key V1        │  │
│  │     Root-cause suppression (DELIVERY_RISK → LOGISTICS_...)│  │
│  │     INFORMATION_GAP → INFORMATION_COMPLETION auto-derived │  │
│  │                                                           │  │
│  │  ③ reconcile_action_cases()  ← AUTHORIZATION BOUNDARY    │  │
│  │     Uses action_case_observations (full feed), NOT       │  │
│  │     ranked queues (my_action_items, etc.)                 │  │
│  │     For each intent:                                      │  │
│  │     ├─ ACTIVE case exists → reuse (update evidence/bucket)│  │
│  │     └─ No ACTIVE case → create new ACTIVE case            │  │
│  │     NOT_OBSERVED limited to scope_order_ids               │  │
│  │     ONLY catches IntegrityError; all others raise         │  │
│  │                                                           │  │
│  │  ④ transition_action_case()  ← AUTHORIZATION BOUNDARY    │  │
│  │     authorize_case_transition() → role+org+owner check   │  │
│  │     FSM validation + optimistic concurrency (rowcount)   │  │
│  │     Manager/Admin/Supervisor: own org only               │  │
│  │     Operator: own org + order.owner == user_id           │  │
│  │                                                           │  │
│  │  ⑤ Permission helpers                                    │  │
│  │     list_my_cases() / get_my_case()                      │  │
│  │     Operator: own orders only                            │  │
│  │     Manager: all in organization                         │  │
│  └─────────────────────────┬─────────────────────────────────┘  │
│                            │                                    │
│                            ▼                                    │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │  Database (action_cases table)                            │  │
│  │  ├── action_case_id TEXT PK                              │  │
│  │  ├── organization_id + order_id + action_intent_key      │  │
│  │  ├── stage / lifecycle_status / version                   │  │
│  │  ├── Partial unique index on ACTIVE cases                 │  │
│  │  └── FK → orders.order_id                                │  │
│  └───────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
```

## 2. Files Changed / Created

### New Files
| File | Description |
|------|-------------|
| `d8_action_case.py` | Core D8 module: intent derivation, reconciliation, FSM, CRUD, permissions |
| `tests/test_d8_action_case.py` | 46 test cases: S01-S15 + edge + A01-A07 + B01-B06 |
| `alembic/versions/f8a3b7c2d1e4_add_action_cases.py` | Alembic migration for action_cases table |
| `D8_ACTION_CASE_CONTRACT.md` | Contract document |
| `D8_IMPLEMENTATION_REPORT.md` | This report |
| `D8_TEST_REPORT.md` | Test report |

### Modified Files (Minimal)
| File | Change |
|------|--------|
| `d7_risk_engine.py` | Added `action_case_observations` field to pipeline output (Manager + Operator paths). Set to `order_results` (full evaluation results before ranking). Zero changes to Risk/Ranking/Bucket logic. |
| `schema.sql` | Added action_cases table + indexes |

### D7 Modules (UNCHANGED)
- `d7_action_bucket.py` — ZERO modifications
- `d7_risk_policy.py` — ZERO modifications
- `d7_risk_signal.py` — ZERO modifications
- `d7_risk_ranking.py` — ZERO modifications

## 3. Data Model

```sql
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

-- Partial unique index: only one ACTIVE case per (org, order, intent_key)
CREATE UNIQUE INDEX uq_action_cases_active
ON action_cases(organization_id, order_id, action_intent_key)
WHERE lifecycle_status = 'ACTIVE';

-- Additional indexes for common query patterns
CREATE INDEX idx_action_cases_org_order
ON action_cases(organization_id, order_id, lifecycle_status);
CREATE INDEX idx_action_cases_stage
ON action_cases(stage, lifecycle_status);
CREATE INDEX idx_action_cases_intent
ON action_cases(action_intent_key, lifecycle_status);
```

## 4. Complete Observation Feed Design (Round 3 Core Fix)

### Problem: Ranked Queue ≠ Observation Snapshot

**Before (Round 2)**: D8 consumed `my_action_items` / `team_action_items` (D7's ranked/display queues). These are affected by Top-N truncation:

```
my_action_items: [ORD-1, ORD-2, ORD-3]  # Top-3 only
# ORD-4 is not in the queue → incorrectly treated as "risk disappeared"
```

**After (Round 3)**: D8 consumes `action_case_observations` (full authoritative feed):

```
action_case_observations: [ORD-1, ORD-2, ORD-3, ORD-4]  # ALL evaluated orders
# ORD-4 still in feed → NOT_OBSERVED based on actual risk signals, not UI presence
```

### Implementation in D7

Added a **separate** `action_case_observations` list in both Manager and Operator pipeline output, independent of the ranked `order_results`:

```python
# In run_d7_pipeline():
order_results: list[dict] = []           # For ranking/UI only (risk-signal orders)
action_case_observations: list[dict] = []  # For D8 consumption (ALL screened orders)

for row in rows:
    risk_signals = assess_risks_from_facts(...)

    if not risk_signals:
        # Zero-risk order: add to observations, skip ranking
        action_case_observations.append({
            **row, "risk_signals": [], "is_screened": True, ...
        })
        continue

    # Build item_base with risk signals
    order_results.append(item_base)              # For ranked UI queues
    action_case_observations.append(item_base)    # For D8 full feed

# Return action_case_observations in BOTH Manager and Operator paths:
return {
    ...,
    "action_case_observations": action_case_observations,  # ALL screened orders
    ...
}
```

`action_case_observations` is the **complete screened snapshot** containing:
- Orders with real risk signals (same as `order_results`)
- INFORMATION_GAP-only orders (present in `order_results` via bucket assignment)
- **Zero-risk orders** (NOT in `order_results`, but present in `action_case_observations`)

Key difference from Round 2: previously `action_case_observations` was simply `order_results`, which excluded zero-risk orders. Now it's an independent list that captures every screened order regardless of risk status.

### D8 Reconciliation Flow

```python
def reconcile_action_cases(conn, d7_result, identity):
    # 1. Use action_case_observations if present (authoritative)
    observations = d7_result.get("action_case_observations")
    if observations is None:
        # Fallback: legacy ranked queues (backward compat)
        observations = collect_from_queues(d7_result)

    # 2. DB authority validation for ALL orders
    for item in observations:
        try:
            _validate_order_authority(conn, item["order_id"], identity)
        except OrderNotAuthorizedError:
            continue  # Non-critical: skip operator's non-owned orders
        # ReconcileAuthError propagates up (critical: abort)

    # 3. Derive intents from validated observations
    intents = [derive_action_intents(item) for item in validated_items]

    # 4. Create/reuse cases
    for intent in intents:
        existing = get_active_case(...)
        if existing:
            update_case(...)  # REUSED
        else:
            create_case(...)  # CREATED

    # 5. Mark NOT_OBSERVED (SCOPED!)
    mark_cases_not_observed(
        organization_id=org_id,
        observed_case_keys=observed_keys,
        scope_order_ids=set(validated_order_ids),  # ← SCOPE
    )
```

## 5. DB Authority Validation

### Two-Tier Validation

```python
def _validate_order_authority(conn, order_id, identity, claimed_org_id):
    # Query DB for real order data
    order = SELECT * FROM orders WHERE order_id = ?

    # ── CRITICAL failures (ReconcileAuthError) ──
    if not order:
        raise ReconcileAuthError("Order not found")
    if order.organization_id != identity.organization_id:
        raise ReconcileAuthError("Cross-org attack detected")
    if claimed_org_id and claimed_org_id != order.organization_id:
        raise ReconcileAuthError("Payload tampering detected")

    # ── NON-CRITICAL failures (OrderNotAuthorizedError) ──
    if role == "operator":
        if not order.owner:
            raise OrderNotAuthorizedError("No owner assigned")
        if order.owner != identity.user_id:
            raise OrderNotAuthorizedError("Not order owner")

    return order
```

### Error Handling

| Error | Raised By | Caught By | Effect |
|-------|----------|----------|--------|
| `ReconcileAuthError` | Missing order / cross-org / payload tampering | **Not caught** | Rejects entire reconciliation |
| `OrderNotAuthorizedError` | Operator not owner / null owner | **Caught** | Skips this order, continues with others |

## 6. Create vs Reuse Statistics

| Scenario | Created | Reused | Notes |
|----------|---------|--------|-------|
| First reconcile | 1 | 0 | New ACTIVE case created |
| Second reconcile (same intent) | 0 | 1 | Existing case reused, evidence merged |
| Tenth reconcile (same intent) | 0 | 1 | Same case_id throughout |
| After CLOSED + new intent | 1 | 0 | New case, old CLOSED history preserved |
| Cross-organization | Isolated | Isolated | No leakage between orgs |

## 7. Root-Cause Suppression Implementation

```python
# pseudo-code for suppression logic
delivery_suppressed = False
if "DELIVERY_RISK" in risk_types:
    has_root_cause = risk_types & {
        "LOGISTICS_EXCEPTION",
        "SUPPLIER_COMMITMENT_OVERDUE",
        "CUSTOMER_CONFIRMATION_BLOCKING",
    }
    if has_root_cause:
        delivery_suppressed = True
        # Collect evidence from suppressed DELIVERY_RISK
        suppressed_evidence = collect_evidence("DELIVERY_RISK")

# When creating non-suppressed intents:
# - Include suppressed_evidence in their evidence list
# - Don't create separate DELIVERY_RECOVERY intent
```

**Evidence preservation**: When DELIVERY_RISK is suppressed, its evidence (e.g., "交期临近") is appended to the remaining intent's evidence list. This ensures that D9 (future) can see the full risk picture even though only a specific root cause action_case was created.

## 8. FSM Transition Service

### Transition Matrix

```
                    ┌──────────────────┐
                    │ NEEDS_JUDGMENT   │
                    └────────┬─────────┘
                             │
                 ┌──────────┼──────────┐
                 ▼          │          ▼
          ┌─────────────┐  │  ┌─────────────┐
          │ READY_FOR_  │  │  │   CLOSED    │
          │ ACTION      │  │  │ (RESOLVED,  │
          └──────┬──────┘  │  │  DISMISSED, │
                 │          │  │  etc.)      │
      ┌──────────┤          │  └─────────────┘
      ▼          ▼          │
┌──────────┐  ┌─────────────┴──┐
│IN_PROGRESS│  │                 │
└────┬─────┘  │                 │
     │        │                 │
     ├──► WAITING_RESULT        │
     │         │                │
     │         ▼                │
     │    ┌─────────────┐       │
     │    │RESUMED_OR_  │       │
     │    │ESCALATED    │       │
     │    └──────┬──────┘       │
     │           │              │
     │    ┌──────┼──────┐       │
     │    ▼      ▼      ▼       │
     │  READY  IN_  WAITING     │
     │  _FOR_  PROGRESS _RESULT │
     │  ACTION                   │
     │                           │
     └───────────────────────────┘
                 + CLOSED (from any stage)
```

### Implementation Details

- **Authorization boundary**: `authorize_case_transition()` runs BEFORE any DB mutation
- **Optimistic concurrency**: `UPDATE ... WHERE action_case_id=? AND version=?` → version+1
- **Row count check**: `rowcount == 0` → `ActionCaseVersionConflict` (CAS miss)
- **Illegal transition**: `ActionCaseFSMError` raised; database unchanged
- **CLOSED is terminal**: No further transitions allowed
- **close_reason required**: Must be one of 8 allowed values

### Authorization Rules (Frozen)

| Role | Can Transition | Constraint |
|------|---------------|------------|
| Manager | Own organization only | No cross-org |
| Admin | Own organization only | No cross-org |
| Supervisor | Own organization only | No cross-org |
| Operator | Own organization only | AND order.owner == user_id |
| Operator (owner=NULL) | **Rejected** | No anonymous transitions |

**Exceptions**: None. Cross-organization attempts always rejected with `ActionCaseAuthError`.

## 9. Reconciliation Flow (Revised Round 3)

```
1. Identity authentication:
   - Extract identity.organization_id as the write scope
   - Validate payload scope.organization_id (if present) == identity.org_id
   - CRITICAL: Validate ALL orders against DB authority BEFORE any write

2. Observation source selection:
   - If action_case_observations present → authoritative full feed
   - If action_case_observations missing → legacy fallback (ranked queues)
     * Legacy fallback: ALLOWED for create/reuse ONLY
     * Legacy fallback: PROHIBITED for mark_cases_not_observed()
     * Rationale: Top-N absence ≠ risk disappearance

3. DB Authority Validation:
   For each observation item:
   a. SELECT order FROM orders WHERE order_id = ?
   b. CRITICAL: order.organization_id == identity.organization_id
      → Mismatch → ReconcileAuthError → abort entire reconcile
   c. CRITICAL: claimed_org_id consistency check
      → Tampering → ReconcileAuthError → abort entire reconcile
   d. NON-CRITICAL: Operator ownership check
      → Not owner → OrderNotAuthorizedError → skip this order

4. Derive intents from FULL observation feed:
   - Use action_case_observations (not ranked queues)
   - INFORMATION_GAP orders included (not filtered by Top-N)
   - Zero-risk orders included → no intents derived → no false NOT_OBSERVED
   - For each item → derive_action_intents()
   - Apply root-cause suppression
   - Return list of deterministic intents

5. For each intent:
   a. SELECT ACTIVE case WHERE (org, order, intent_key)
   b. IF exists: update (evidence, bucket, severity) → REUSED
   c. IF not: INSERT new ACTIVE case → CREATED
   d. ONLY IntegrityError caught → retry SELECT → REUSED or re-raise
   e. All other exceptions raised immediately

6. mark_cases_not_observed() — SCOPED:
   - ONLY executed when using authoritative observation feed
   - SKIPPED when using legacy fallback (ranked queues are incomplete)
   - Uses scope_order_ids (validated order IDs from this reconcile)
   - Granularity: (order_id, action_intent_key) per intent
   - ACTIVE cases in scope with intent keys NOT in current cycle
   - observation_status → NOT_OBSERVED
   - lifecycle_status stays ACTIVE
   - No auto-closure
   - Operator isolation: cannot affect other operators' cases
```

## 10. Error Handling Contract

| Error Class | Trigger | Behavior |
|-------------|---------|----------|
| `ActionCaseAuthError` | Cross-org transition / owner mismatch / missing identity | Raised before any DB mutation |
| `ActionCaseFSMError` | Illegal FSM transition | Raised; DB unchanged |
| `ActionCaseVersionConflict` | CAS miss (rowcount=0) | Raised; DB unchanged |
| `ReconcileAuthError` | Cross-org payload injection / missing order / payload tampering | Raised; no partial writes |
| `OrderNotAuthorizedError` | Operator not owner / owner=NULL | Caught; order skipped |
| `IntegrityError` (SQLAlchemy) | Unique constraint race | Retry SELECT; re-raise if no ACTIVE found |
| All other exceptions | Unexpected DB errors | Raise immediately; never swallowed |

## 11. D7 Changes (Minimal — Separate Observation List)

The D7 change introduces a **separate `action_case_observations` list** alongside the existing `order_results`. Zero-risk orders that would have been silently skipped are now captured in this independent list. Both Manager and Operator return paths expose it.

**What stays unchanged in D7:**
- Risk Signal rules (all `_assess_*` functions)
- Action Bucket algorithm
- Ranking algorithm and Top-N truncation
- `my_action_items`, `team_action_items`, `unassigned_orders`, `information_gaps` outputs
- D7 scoring, evidence collection, root-cause logic

**What changes in D7:**
- New independent list `action_case_observations` = ALL screened orders (including zero-risk)
- Existing `order_results` unchanged — still only risk-signal orders for ranking
- No behavior change for any existing consumer of D7 output
- Zero-risk orders now visible to D8 via `action_case_observations`, enabling correct NOT_OBSERVED marking when risks truly disappear

## 12. Known Limitations (Deferred to D9+)

- **No task creation**: action_case lifecycle is managed, but task creation is D9
- **No waiting recovery**: NOT_OBSERVED cases are not auto-recovered
- **No outbox pattern**: reconciliation is synchronous
- **No ERP write-back**: D8 does not write to ERPNext
- **No agent auto-execution**: actions are recommended, not executed
- **No batch reconciliation optimization**: per-order reconciliation only
- **No partial close**: case can only be fully CLOSED, not partially resolved