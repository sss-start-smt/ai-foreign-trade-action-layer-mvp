# D8 Test Report: Risk/Action Judgment → Deterministic Action Intent

**Status:** ALL TESTS PASSED  
**Date:** 2026-08-12 (Revised Round 3)  
**Test File:** `tests/test_d8_action_case.py`

---

## 1. Test Summary

| Metric | Result |
|--------|--------|
| D8 Tests | **49 passed / 0 failed** (40 original + 6 B-series + 3 C-series) |
| D7 Regression | **102 passed / 0 failed** |
| Full Regression | **419 passed / 26 skipped / 0 failed** |
| vs Baseline (D7) | +49 new tests, 0 regression |

## 2. Scenario Coverage

### S01: Same Intent Reuse (10 consecutive reconciliations)
| Test | Result |
|------|--------|
| test_10_reconciles_produce_one_case | ✅ 10 consecutive reconciliations → 1 ACTIVE case, same case_id |
| test_case_id_is_deterministic | ✅ action_intent_key = v1:SUPPLIER_FOLLOWUP |

### S02: Two Different Intents
| Test | Result |
|------|--------|
| test_two_intents_two_cases | ✅ CUSTOMER_CONFIRMATION + LOGISTICS_RECOVERY → 2 ACTIVE cases |

### S03: Bucket Change Does Not Create New Case
| Test | Result |
|------|--------|
| test_bucket_change_reuses_case | ✅ DO_TODAY → DO_NOW, same case_id, bucket updated |
| test_stage_unchanged_on_bucket_change | ✅ stage remains READY_FOR_ACTION |

### S04: Severity Change Does Not Create New Case
| Test | Result |
|------|--------|
| test_severity_change_reuses_case | ✅ MEDIUM → HIGH, same case_id, severity updated |

### S05: CLOSED + Reopen = New Case
| Test | Result |
|------|--------|
| test_closed_then_reopen_creates_new | ✅ CLOSED case + same intent reappears → new ACTIVE case, old history preserved |
| test_closed_history_preserved | ✅ Both CLOSED and ACTIVE cases exist with same intent_key, different case_id |

### S06: Risk Disappears → NOT_OBSERVED, Not CLOSED
| Test | Result |
|------|--------|
| test_risk_disappears_not_closed | ✅ observation_status=NOT_OBSERVED, lifecycle_status stays ACTIVE |

### S07: Illegal FSM Transition Rejected
| Test | Result |
|------|--------|
| test_illegal_transition_rejected | ✅ NEEDS_JUDGMENT → IN_PROGRESS rejected |
| test_closed_cannot_transition | ✅ CLOSED → ANY rejected |
| test_close_reason_validation | ✅ Missing/invalid close_reason rejected |

### S08: SOURCE_CONFLICT → NEEDS_JUDGMENT
| Test | Result |
|------|--------|
| test_source_conflict_needs_judgment | ✅ FACT_CONFLICT_RESOLUTION starts at NEEDS_JUDGMENT |

### S09: DELIVERY_RISK Only → DELIVERY_RECOVERY
| Test | Result |
|------|--------|
| test_delivery_only_creates_delivery_recovery | ✅ v1:DELIVERY_RECOVERY created |

### S10: DELIVERY_RISK + LOGISTICS_EXCEPTION Suppression
| Test | Result |
|------|--------|
| test_delivery_suppressed_by_logistics | ✅ Only LOGISTICS_RECOVERY created, DELIVERY_RECOVERY suppressed |
| test_delivery_evidence_preserved | ✅ DELIVERY_RISK evidence ("交期临近") preserved in LOGISTICS_RECOVERY case |

### S11: DELIVERY_RISK + SUPPLIER_COMMITMENT_OVERDUE Suppression
| Test | Result |
|------|--------|
| test_delivery_suppressed_by_supplier | ✅ Only SUPPLIER_FOLLOWUP created, DELIVERY_RECOVERY suppressed |

### S12: Operator Visibility
| Test | Result |
|------|--------|
| test_operator_sees_own_cases | ✅ Operator sees only cases for orders they own |

### S13: Manager Visibility
| Test | Result |
|------|--------|
| test_manager_sees_all_in_org | ✅ Manager sees all cases in organization + OWNER_ASSIGNMENT for unassigned orders |

### S14: Cross-Organization Isolation
| Test | Result |
|------|--------|
| test_org_a_cannot_read_org_b_cases | ✅ ORG-A operator/manager cannot read ORG-B cases |
| test_cross_org_transition_blocked | ✅ Cross-org transition ATTEMPTED, blocked at authorization boundary, DB unchanged |

### S15: DB Unique Constraint
| Test | Result |
|------|--------|
| test_duplicate_create_blocked_by_db | ✅ Direct INSERT of duplicate ACTIVE (org, order, intent) blocked by DB constraint |
| test_closed_allows_new_active | ✅ CLOSED old case + new ACTIVE case with same intent_key → 1 ACTIVE + 1 CLOSED |

### Additional Edge Cases
| Test | Result |
|------|--------|
| test_full_fsm_lifecycle | ✅ Full FSM: NEEDS_JUDGMENT → READY_FOR_ACTION → IN_PROGRESS → WAITING_RESULT → RESUMED_OR_ESCALATED → CLOSED |
| test_resumed_can_go_back_to_ready | ✅ RESUMED_OR_ESCALATED → READY_FOR_ACTION (loop back) |
| test_intent_key_format | ✅ All 7 risk types map to correct v1:{INTENT_TYPE} keys |
| test_version_increments_on_transition | ✅ version increments: 1→2→3 on transitions |
| test_full_pipeline | ✅ run_d8_pipeline() runs D7 + D8 end-to-end |
| test_same_input_same_output | ✅ Deterministic: same input → same case_id on repeat |
| test_source_policy_version_stored | ✅ source_policy_version correctly stored |

## 3. Adversarial Tests (A01-A07) — ChatGPT Review Round 2

### A01: Cross-Org Actual Transition Attack
| Test | Result |
|------|--------|
| test_org_a_manager_cannot_transition_org_b_case | ✅ ORG-A manager attempts transition on ORG-B case → ActionCaseAuthError, DB unchanged |
| test_org_a_operator_cannot_transition_org_b_case | ✅ ORG-A operator attempts transition on ORG-B case → ActionCaseAuthError, DB unchanged |

### A02: Same-Org Other-Operator Transition Attack
| Test | Result |
|------|--------|
| test_operator_cannot_transition_other_operator_case | ✅ USER-2 attempts transition USER-1's case → ActionCaseAuthError, DB unchanged |
| test_operator_cannot_transition_null_owner_case | ✅ Operator attempts transition owner=NULL case → ActionCaseAuthError, DB unchanged |

### A03: Manager Same-Org Valid Transition
| Test | Result |
|------|--------|
| test_manager_can_transition_any_case_in_org | ✅ Manager can transition any case in org (IN_PROGRESS + CLOSED) |
| test_admin_can_transition_any_case_in_org | ✅ Admin can transition any case in org |

### A04: Cross-Org Reconcile Payload Injection
| Test | Result |
|------|--------|
| test_reconcile_rejects_cross_org_item | ✅ Identity=ORG-A, item.org=ORG-B (DB) → ReconcileAuthError, no partial writes |
| test_reconcile_rejects_mismatched_scope | ✅ Identity=ORG-A, payload scope=ORG-B → ReconcileAuthError, no partial writes |

### A05: Same-Order One-Intent Disappears
| Test | Result |
|------|--------|
| test_one_intent_disappears_other_stays_observed | ✅ Round 1: 2 intents observed; Round 2: 1 intent disappears → NOT_OBSERVED per intent, stays ACTIVE |

### A06: Create Unexpected DB Error Not Swallowed
| Test | Result |
|------|--------|
| test_integrity_error_without_existing_case_reraises | ✅ IntegrityError with no ACTIVE found → re-raised, not swallowed; CLOSED+new ACTIVE → new ACTIVE created |

### A07: Optimistic Concurrency CAS Miss
| Test | Result |
|------|--------|
| test_stale_version_prevents_transition | ✅ Stale _expected_version=1 vs actual version=2 → ActionCaseVersionConflict, DB unchanged |
| test_concurrent_transition_only_one_succeeds | ✅ Sequential transitions: version increments correctly (1→2→3) |

## 4. Adversarial Tests (B01-B06) — ChatGPT Review Round 3

### B01: Operator Same-Org Write Attack
**Scenario**: ORD-1 owned by USER-1 (ORG-A). USER-2 (ORG-A operator) tries to reconcile ORD-1.
| Test | Result |
|------|--------|
| test_operator_cannot_reconcile_same_org_other_owner | ✅ USER-2's reconcile skips ORD-1 (not owner). 0 cases created. 0 changed. |

### B02: Operator Observation Pollution
**Scenario**: USER-1 has ORD-1 ACTIVE OBSERVED case. USER-2 reconciles only ORD-2.
| Test | Result |
|------|--------|
| test_operator_cannot_change_other_operators_observation | ✅ USER-1's ORD-1 case remains OBSERVED. NOT corrupted by USER-2's reconcile. |

### B03: DB Org Is Authority
**Scenario**: DB has ORD-X with org=ORG-B. Payload claims org=ORG-A. Identity=ORG-A manager.
| Test | Result |
|------|--------|
| test_payload_org_cannot_override_db_org | ✅ ReconcileAuthError raised. Entire reconcile rejected. 0 cases created. |

### B04: Information Gap Enters Action Case
**Scenario**: Operator order has risk_signals=[INFORMATION_GAP]. Goes to information_gaps (not my_action_items).
| Test | Result |
|------|--------|
| test_information_gap_creates_information_completion_case | ✅ v1:INFORMATION_COMPLETION created. stage=NEEDS_JUDGMENT. OBSERVED. |

### B05: Top-N Absence ≠ NOT_OBSERVED
**Scenario**: Same operator, same order. ORD-1 in action_case_observations with risk but NOT in my_action_items (Top-N truncation).
| Test | Result |
|------|--------|
| test_case_remains_observed_when_not_in_top_n | ✅ Case remains OBSERVED. Top-N UI absence does NOT cause NOT_OBSERVED. |

### B06: Real Disappearance (Verified Screen, Risk Gone)
**Scenario**: Full Observation Snapshot includes ORD-1. But LOGISTICS_EXCEPTION truly gone from risk_signals.
| Test | Result |
|------|--------|
| test_real_risk_disappearance_marks_not_observed | ✅ LOGISTICS_RECOVERY → NOT_OBSERVED. lifecycle=ACTIVE. stage unchanged. New DELIVERY_RECOVERY case created. |

### B05 vs B06 — Critical Distinction

| Aspect | B05 | B06 |
|--------|-----|-----|
| Order in action_case_observations? | ✅ Yes | ✅ Yes |
| Risk signal still present? | ✅ Yes (LOGISTICS_EXCEPTION) | ❌ No (LOGISTICS_EXCEPTION gone) |
| Order in my_action_items (Top-N)? | ❌ No | ✅ Yes |
| Result | **OBSERVED** (UI absence ≠ risk gone) | **NOT_OBSERVED** (real risk disappearance) |

These two tests together prove:
- **B05**: UI Top-N absence does NOT mean risk disappeared
- **B06**: Full snapshot + real risk disappearance DOES mean NOT_OBSERVED

## 5. Adversarial Tests (C01-C03) — Complete Screened Snapshot

### C01: Zero-Risk Screened Order in Observation Feed
**Scenario**: Order fully screened by D7 but has zero risk_signals.
| Test | Result |
|------|--------|
| test_zero_risk_order_in_action_case_observations | ✅ Order appears in action_case_observations with risk_signals=[]. UI queues (my_action_items, information_gaps) remain empty. |

### C02: Complete Risk Disappearance (Verified Screen)
**Scenario**: Round 1 — ORD-C02 has LOGISTICS_EXCEPTION → creates LOGISTICS_RECOVERY. Round 2 — ORD-C02 still fully screened (in action_case_observations) but risk_signals=[].
| Test | Result |
|------|--------|
| test_zero_risk_causes_not_observed | ✅ LOGISTICS_RECOVERY → NOT_OBSERVED. lifecycle=ACTIVE. stage unchanged. No auto-closure. |

### C03: No-Risk Observation Must Not Create Case
**Scenario**: action_case_observation contains ORD-C03 with risk_signals=[]. No historical Action Case.
| Test | Result |
|------|--------|
| test_zero_risk_does_not_create_case | ✅ created_count=0. intents_count=0. No cases in DB. Observation Scope ≠ Business Intent. |

### C-Series Design Principles

| Aspect | C01 | C02 | C03 |
|--------|-----|-----|-----|
| Tests | D7 output structure | D8 reconciliation | D8 case creation |
| Zero-risk order in feed? | ✅ Yes | ✅ Yes | ✅ Yes |
| NOT_OBSERVED triggered? | N/A | ✅ Yes | N/A (no prior case) |
| New case created? | N/A | ❌ No | ❌ No |
| UI queue polluted? | ❌ No | N/A | N/A |

These tests together verify:
- **C01**: Zero-risk orders are captured in the full observation feed without polluting UI queues
- **C02**: When a risk truly disappears (verified by full screen), NOT_OBSERVED is correctly applied
- **C03**: Observation events (screening) are strictly separated from business intent creation

## 6. Test Code Snippets

### S01 Core Assertion (Deterministic Reuse)
```python
for i in range(10):
    result = d8.reconcile_action_cases(db_conn, d7_result, identity=identity)
    assert result["created_count"] == (1 if i == 0 else 0)
    assert result["reused_count"] == (0 if i == 0 else 1)
    active = [c for c in cases if c["lifecycle_status"] == "ACTIVE"]
    assert len(active) == 1
    if first_case_id is None:
        first_case_id = active[0]["action_case_id"]
    else:
        assert active[0]["action_case_id"] == first_case_id  # Same ID every time
```

### S10 Suppression Assertion
```python
# DELIVERY_RISK + LOGISTICS_EXCEPTION → only LOGISTICS_RECOVERY
assert len(cases) == 1
assert cases[0]["action_intent_key"] == "v1:LOGISTICS_RECOVERY"
assert "v1:DELIVERY_RECOVERY" not in {c["action_intent_key"] for c in cases}

# Evidence preserved
evidence = json.loads(cases[0]["latest_evidence_json"])
assert "交期临近" in evidence  # DELIVERY_RISK evidence carried forward
```

### B05 Top-N Absence Test
```python
# Round 1: ORD-B05 in both my_action_items and action_case_observations
d8.reconcile_action_cases(db_conn, d7_r1, identity=identity)
assert cases_r1[0]["observation_status"] == "OBSERVED"

# Round 2: ORD-B05 in action_case_observations, but NOT in my_action_items
d7_r2 = _make_d7_result(
    [],  # Top-N doesn't include ORD-B05
    action_case_observations=[item],  # Full feed still has it
)
d8.reconcile_action_cases(db_conn, d7_r2, identity=identity)

# Case MUST remain OBSERVED — NOT_OBSERVED uses full feed, not ranked queue
assert cases_r2[0]["observation_status"] == "OBSERVED"
```

### B06 Real Disappearance Test
```python
# Round 1: LOGISTICS_EXCEPTION exists → LOGISTICS_RECOVERY OBSERVED
d8.reconcile_action_cases(db_conn, d7_r1, identity=identity)

# Round 2: ORD-B06 in full feed, but LOGISTICS_EXCEPTION truly gone
d7_r2 = _make_d7_result(
    [item_r2],
    action_case_observations=[item_r2],  # item has only DELIVERY_RISK
)
d8.reconcile_action_cases(db_conn, d7_r2, identity=identity)

# LOGISTICS_RECOVERY → NOT_OBSERVED (real risk gone)
assert logistics_case["observation_status"] == "NOT_OBSERVED"
assert logistics_case["lifecycle_status"] == "ACTIVE"
```

## 7. Regression Impact

| Test Suite | Before D8 | After D8 |
|------------|-----------|----------|
| D7 (test_d7_risk_engine + test_d7_integration) | 102 passed | **102 passed** (unchanged) |
| Full Regression | 370 passed / 26 skipped | **419 passed / 26 skipped** (+49 new, 0 unskipped) |
| **Total** | 396 tests | **445 tests** |

**Key**: Zero D7 tests modified. Zero D7 expectations changed. D8 is fully additive. The `action_case_observations` field is additive — it doesn't remove or change any existing D7 output. Zero-risk orders captured in `action_case_observations` do not affect ranking, UI queues, or any D7 consumer behavior.

## 8. Test Infrastructure

### Test Database
- Fresh SQLite database per test (tempfile)
- Full schema: orders + tasks + logistics_events + commitment_history + action_cases
- Partial unique index for ACTIVE case constraint

### Test Helpers
- `_make_order()` / `_insert_order()` — order fixture
- `_make_risk_signal()` — risk signal fixture
- `_make_d7_item()` — D7 action item fixture
- `_make_d7_result()` — full D7 pipeline result fixture
- `_make_identity()` — identity fixture (operator/manager roles)

## 9. Command Reference

```bash
# D8 tests only
PYTHONPATH=. python -m pytest -q tests/test_d8_action_case.py -v

# D7 regression
PYTHONPATH=. python -m pytest -q tests/test_d7_risk_engine.py tests/test_d7_integration.py

# Full regression
PYTHONPATH=. python -m pytest -q
```

## 10. Final Gate Verification

**Gate**: "同一个业务意图重复计算不会重复创建 ACTIVE action_case"

| Verification | Result |
|-------------|--------|
| Same intent × 10 reconciles → 1 ACTIVE case | ✅ S01 |
| Same intent × 10 reconciles → same case_id | ✅ S01 |
| DB unique constraint blocks duplicates | ✅ S15 |
| CLOSED + new → new ACTIVE (old history kept) | ✅ S05, S15 |
| Deterministic: same input → same output | ✅ DeterministicReconciliation |

**Gate**: "D8 使用完整 Observation Feed 而非 Ranked Queue"

| Verification | Result |
|-------------|--------|
| action_case_observations 作为 authoritative source | ✅ B05, B06 |
| Top-N 缺席 ≠ NOT_OBSERVED (B05) | ✅ B05 |
| 真实风险消失 = NOT_OBSERVED (B06) | ✅ B06 |
| DB authority 验证防止 payload 篡改 | ✅ B03 |
| Operator 隔离防止 Observation 污染 | ✅ B02 |
| INFORMATION_GAP 进入 Action Case | ✅ B04 |
| Operator same-org write attack 被阻止 | ✅ B01 |

**Gate**: "完整 Screened Observation Snapshot 闭环"

| Verification | Result |
|-------------|--------|
| 零风险订单出现在 action_case_observations | ✅ C01 |
| 零风险订单不出现在 UI Queue | ✅ C01 |
| 真实风险消失 → NOT_OBSERVED | ✅ C02 |
| NOT_OBSERVED 后 lifecycle 保持 ACTIVE | ✅ C02 |
| 零风险 observation 不创建新 Case | ✅ C03 |
| Legacy fallback 禁止 NOT_OBSERVED | ✅ S06/A05 使用权威 feed 验证 |