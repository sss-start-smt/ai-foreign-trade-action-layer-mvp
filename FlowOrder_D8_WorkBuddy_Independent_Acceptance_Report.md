# FlowOrder D8 — WorkBuddy Independent Acceptance Report

**Verdict: PASS — READY_TO_FREEZE_D8**

Independent verifier: WorkBuddy (not ChatGPT / Trae / any in-repo report)
Date of verification: 2026-08-12
Scope of submission reviewed: `d8_action_case.py`, `d7_risk_engine.py`, `schema.sql`,
D8 Alembic migration `f8a3b7c2d1e4_add_action_cases.py`, `tests/test_d8_action_case.py`,
`D8_ACTION_CASE_CONTRACT.md`, `D8_IMPLEMENTATION_REPORT.md`, `D8_TEST_REPORT.md`.

Method: Read the implementation, ran the real test suites, and wrote a **separate**
attack harness (`wb_d8_independent_verification.py`) that drives the real D8 functions
and the real D7→D8 bridge with hand-crafted scenarios. Test names were not trusted;
behavior was asserted directly.

---

## 1. Executive Verdict

**PASS — READY_TO_FREEZE_D8.**

Every functional and security acceptance criterion in the brief was independently
verified by executing the real code:

- Action Case identity is stable and deterministic; 10× reconcile of the same
  `(org, order, intent)` yields exactly **one** ACTIVE case with an unchanged id.
- `action_intent_key` does **not** depend on UUID / `risk_signal_id` /
  `priority_score` / Action Bucket (verified by mutating those fields and confirming
  reuse of the same case).
- Multi-intent parallelism works: `CUSTOMER_CONFIRMATION + LOGISTICS_RECOVERY` on one
  order produce **two** distinct ACTIVE cases.
- Root-cause suppression is correct for all three required combinations.
- FSM: legal transitions succeed with version bump; illegal transitions are rejected
  with stage/version unchanged; `CLOSED` is terminal; close requires a valid reason and
  only `RESOLVED` marks success; an Action Bucket change (`DO_TODAY→DO_NOW`) does **not**
  auto-advance the lifecycle stage.
- Authorization: cross-org transition denied (state frozen); same-org other-operator
  transition denied (state frozen); manager legal op succeeds; reconcile payload
  injection (order belonging to another org, or non-existent order) rejects the **entire**
  reconcile with **no partial write**.
- Observation scope is correct: an operator's reconcile cannot flip another operator's
  cases to `NOT_OBSERVED`; `Top-N` absence does not cause a false `NOT_OBSERVED`.
- Zero-risk screened orders are detected via the independent `action_case_observations`
  feed: existing case → `NOT_OBSERVED` but still `ACTIVE` / not auto-closed; a zero-risk
  order with no prior case creates **no** case.
- Information Gap yields `v1:INFORMATION_COMPLETION` at `NEEDS_JUDGMENT`.
- Legacy fallback does **not** drive `mark_cases_not_observed`.
- Concurrency/CAS: the partial unique index blocks a second ACTIVE same-intent case;
  `reconcile` reuses on `IntegrityError`; optimistic-concurrency CAS miss raises
  `ActionCaseVersionConflict` and leaves state unchanged; `create_action_case` contains
  **no** bare `except Exception` swallowing DB errors.

One **Documentation Fix** is required (report numbers), but it is **not** a P0/P1 blocker
and does not affect code or evidence. See §18.

---

## 2. Scope

In scope (frozen D8 boundary):
- `action_case != task` separation (business-goal-level object; `tasks` table untouched and not conflated).
- Deterministic `action_intent_key` derivation.
- `reconcile_action_cases` create/reuse against the independent `action_case_observations` feed.
- Root-cause suppression of generic `DELIVERY_RECOVERY`.
- FSM (6 frozen stages + close reasons).
- Authorization boundary for `transition_action_case` and `reconcile_action_cases`.
- Observation scope (per-operator, per `(order_id, action_intent_key)`).
- `Top-N` ranking / UI queues vs. the authoritative observation snapshot.
- Zero-risk screened orders and Information Gap handling.
- Concurrency / optimistic locking.

Out of scope (explicitly **not** judged per §15 — absence is not a D8 defect):
Waiting Worker, `waiting_records` expiry recovery, Task lifecycle closure, Outbox,
Redis, Taskiq, ERP write-back, automatic owner assignment, BusinessAction, Agent execution.

---

## 3. Independent Test Environment

- OS: win32 (Windows)
- Python: managed `3.13.12` (`C:\Users\smt10\.workbuddy\binaries\python\versions\3.13.12`)
- SQLAlchemy: `2.0.35`
- pytest: `9.1.1`
- Working dir: `D:\FlowOrder\02_ENGINEERING_CURRENT\source\floworder`
- Backend exercised: **SQLite** (same engine used by the repo's own D8 tests and by the
  migration's `sqlite_where` partial index). The partial unique index is asserted both via
  the migration (`uq_action_cases_active` with `sqlite_where`/`postgresql_where`) and via
  the live DB-level constraint test.
- Independent harness: `wb_d8_independent_verification.py` (fresh in-memory temp SQLite DB
  per attack, real `d8_action_case` + real `d7_risk_engine.run_d7_pipeline`).

---

## 4. Actual pytest stdout summary

Commands run verbatim in this environment:

```
PYTHONPATH=. pytest -q tests/test_d8_action_case.py
PYTHONPATH=. pytest -q tests/test_d7_risk_engine.py tests/test_d7_integration.py
PYTHONPATH=. pytest -q
```

Results (captured from real stdout, **not** copied from any report):

| Suite | Result (actual stdout) |
|-------|------------------------|
| `tests/test_d8_action_case.py` | **49 passed** in 6.32s |
| `tests/test_d7_risk_engine.py` + `tests/test_d7_integration.py` | **102 passed** in 4.02s |
| Full suite (`pytest -q`) | **419 passed, 26 skipped, 0 failed** in 38.35s |

These match the numbers ChatGPT self-reported (49 / 102 / 419·26). They do **not** match
the numbers printed inside `D8_IMPLEMENTATION_REPORT.md` / `D8_TEST_REPORT.md`
("422 passed / 23 skipped") — see §18.

Independent attack harness (`wb_d8_independent_verification.py`):
**23 attack groups passed / 0 failed**.

---

## 5. Action Case Identity

Verified in `IV.identity.*`:

- `reconcile_action_cases` keys identity on `(organization_id, order_id, action_intent_key)`.
- 10 consecutive reconciles of the same `(ORG-A, ORD-1, v1:DELIVERY_RECOVERY)` produced
  exactly **1** ACTIVE case; `action_case_id` was identical on every round.
- Determinism check: same `(org, order, intent)` but with **different** `risk_signal_id`,
  **different** `priority_score` (999 vs 1), and **different** Action Bucket
  (`DO_NOW` vs `DO_TODAY`) → still reused the same case with the same key.
  ⇒ `action_intent_key` is independent of UUID / `risk_signal_id` / score / bucket.
- `action_intent_key` format is `v1:{INTENT_TYPE}`, derived only from the D7 risk type
  via the frozen `_RISK_TO_INTENT` map.

**Result: PASS.**

---

## 6. Create / Reuse

- New risk → `create_action_case` (version 1, `OBSERVED`, `ACTIVE`).
- Same risk seen again → `update_action_case_reconcile` (refreshes bucket/severity/
  evidence, sets `OBSERVED`); stage and lifecycle unchanged.
- Confirmed both via the 10× reuse attack and the cross-environment D7→D8 bridge.

**Result: PASS.**

---

## 7. Multi-intent

Same order with `CUSTOMER_CONFIRMATION_BLOCKING` + `LOGISTICS_EXCEPTION`:
→ exactly **2** parallel ACTIVE cases:
`v1:CUSTOMER_CONFIRMATION` and `v1:LOGISTICS_RECOVERY`.

This matches the contract's explicit example and the "one order may have multiple
parallel Action Cases" rule. `action_case` is demonstrably distinct from `task`
(the `tasks` table is never written by D8).

**Result: PASS.**

---

## 8. Root-cause Suppression

| Input risk signals | Expected ACTIVE case(s) | Actual (verified) |
|--------------------|-------------------------|-------------------|
| `DELIVERY_RISK` + `LOGISTICS_EXCEPTION` | only `LOGISTICS_RECOVERY` | only `LOGISTICS_RECOVERY` ✓ |
| `DELIVERY_RISK` + `SUPPLIER_COMMITMENT_OVERDUE` | only `SUPPLIER_FOLLOWUP` | only `SUPPLIER_FOLLOWUP` ✓ |
| `DELIVERY_RISK` only | `DELIVERY_RECOVERY` | `DELIVERY_RECOVERY` ✓ |

Suppressed `DELIVERY_RISK` evidence is retained in the surviving case's evidence
(`derive_action_intents` appends `suppressed_evidence`), so no information is lost.

**Result: PASS.**

---

## 9. FSM

Verified in `VI.fsm.*`:

- **Legal path** `NEEDS_JUDGMENT → READY_FOR_ACTION → IN_PROGRESS → WAITING_RESULT →
  RESUMED_OR_ESCALATED → CLOSED` succeeded; version incremented 1→6 monotonically.
- **Illegal transitions rejected**, stage & version **unchanged** (each of):
  `NEEDS_JUDGMENT→IN_PROGRESS`, `NEEDS_JUDGMENT→WAITING_RESULT`,
  `READY_FOR_ACTION→WAITING_RESULT`, `READY_FOR_ACTION→RESUMED_OR_ESCALATED`,
  `IN_PROGRESS→READY_FOR_ACTION`, `WAITING_RESULT→IN_PROGRESS`.
- **`CLOSED` is terminal**: `CLOSED → READY_FOR_ACTION` raised `ActionCaseFSMError`.
- **Close reason**: transition to `CLOSED` without a reason → `ValueError`; with an
  invalid reason → `ValueError`; with `RESOLVED` → success (`lifecycle_status=CLOSED`,
  `close_reason=RESOLVED`); with `DISMISSED` → success but not a "success" resolution.
- **Action Bucket ≠ lifecycle**: a `DO_TODAY → DO_NOW` change updated
  `latest_action_bucket` but left `stage=READY_FOR_ACTION` and `version=1` unchanged.

**Result: PASS.**

---

## 10. Authorization Attack Results

| Attack | Setup | Expected | Actual |
|--------|-------|----------|--------|
| **A. Cross-org transition** | ORG-B case; ORG-A Manager calls `transition` | rejected; stage/version unchanged | rejected; stage=`READY_FOR_ACTION`, version=1 unchanged ✓ |
| **B. Same-org other operator** | ORD-1 `owner=USER-1`; USER-2 operator calls `transition` | rejected | rejected; state frozen ✓ |
| **C. Manager legal op** | ORG-A case; ORG-A Manager calls `transition` | success | advanced to `IN_PROGRESS`, version 2 ✓ |
| **D. Reconcile injection (cross-org order)** | payload has ORD-A (valid) + ORD-B (DB org=ORG-B, claimed ORG-A) | whole reconcile rejected; no partial write | `ReconcileAuthError`; 0 cases created ✓ |
| **D. Reconcile injection (ghost order)** | payload has ORD-A + ORD-GHOST (not in DB) | whole reconcile rejected; no partial write | `ReconcileAuthError`; 0 cases created ✓ |

The authorization boundary lives **inside** `transition_action_case` /
`_validate_order_authority`, not in the caller. `_validate_order_authority` treats the
DB `orders` row as the sole authority for `organization_id`/`owner`; the payload
`organization_id` is only a tamper-consistency check and is **never** trusted as the
write scope. The whole reconcile is validated against the DB **before any** INSERT/UPDATE.

**Result: PASS (no P0 permission漏洞).**

---

## 11. Reconcile Authority Results

Covered by §10.D. Key property confirmed:
- DB `orders` is the authority. A payload claiming `organization_id=ORG-A` for an order
  whose DB row is `ORG-B` is rejected.
- A payload referencing an order that does not exist in the DB is rejected.
- On rejection, **no** Action Case is created, including for genuinely valid same-org
  items in the same payload (no partial write).

**Result: PASS.**

---

## 12. Observation Snapshot vs Ranked Queue

Verified in `IX.*`:

- **Pre-Top-N feed is broader than the ranked queue**: with 10 risky orders and
  `top_n=3`, D7 returned `action_case_observations=10` while `my_action_items` (Top-N)
  was truncated to `3`. The observation feed is built **before** ranking/Top-N.
- **Top-N absence does not cause `NOT_OBSERVED`**: ORD-1 had a `LOGISTICS_RECOVERY` case
  (`OBSERVED`); in the next round ORD-1 was removed from `my_action_items` (Top-N empty)
  but kept in `action_case_observations`. After reconcile the case remained `OBSERVED`.
- `reconcile_action_cases` uses **only** `action_case_observations` for status decisions;
  it never infers risk disappearance from the absence of an order in the ranked queues.

**Result: PASS.**

---

## 13. Zero-risk Screened Observation

Verified end-to-end via the real D7→D8 bridge in `X.zero_risk.*`:

- **Round 1**: ORD-Z had a `LOGISTICS_EXCEPTION` → D7 `action_case_observations` contained
  ORD-Z with that signal → D8 created `v1:LOGISTICS_RECOVERY`, `OBSERVED` + `ACTIVE`.
- **Round 2**: the logistics exception was resolved → D7 still returned ORD-Z in
  `action_case_observations` but with `risk_signals=[]` (empty). D8 then set the existing
  case to `observation_status=NOT_OBSERVED` while `lifecycle_status` remained `ACTIVE`,
  `stage` unchanged, and it was **not** auto-closed (`close_reason` still `NULL`).
- **Zero-risk order with no prior case**: ORD-0 (healthy, no signals) appeared in the
  observation feed with empty `risk_signals`; D8 created **0** Action Cases for it.

This is exactly the ChatGPT-flagged Blocker scenario, independently confirmed fixed.

**Result: PASS.**

---

## 14. Information Gap

Verified end-to-end in `XI.information_gap`:

An order missing `current_node` / `current_progress` / `latest_supplier_commitment`
(with a future delivery date so no real risk) was assessed as `INFORMATION_GAP` only and
remained in `action_case_observations`. D8 produced `v1:INFORMATION_COMPLETION` at initial
stage `NEEDS_JUDGMENT`, `ACTIVE` — independent of whether it entered the operator's
`my_action_items`.

**Result: PASS.**

---

## 15. NOT_OBSERVED Semantics

- `NOT_OBSERVED` is set **only** via `mark_cases_not_observed`, which updates
  `observation_status` **and nothing else** (no stage/lifecycle/close change).
- Scope of `mark_cases_not_observed` is bound to `scope_order_ids` (this round's
  authoritative order set) and excludes `observed_case_keys`, so it cannot reach other
  operators' orders or unrelated orders.
- Risk disappearance ⇒ `NOT_OBSERVED` + still `ACTIVE` + stage unchanged (§13). This is the
  correct "risk disappearance ≠ business resolution" semantics from the contract.

**Result: PASS.**

---

## 16. Concurrency / CAS

Verified in `XIII.*`:

1. **No swallowed DB errors**: `create_action_case` contains **no** `except Exception`
   / `except:` block (static check on source). Real `IntegrityError` propagates.
2. **DB partial unique index**: inserting a second `ACTIVE` same-`(org, order, intent)`
   row raised `IntegrityError`; exactly **1** ACTIVE row remained. This is the hard
   guarantee against duplicate ACTIVE cases even under concurrency.
3. **Reconcile reuse under conflict**: two back-to-back reconciles of the same intent
   → 1 ACTIVE case (`IntegrityError` → re-read → reuse path).
4. **Optimistic concurrency (CAS)**: a real transition bumped version 1→2. A subsequent
   transition attempted with a stale `_expected_version=1` raised
   `ActionCaseVersionConflict` and left state at version 2 / `IN_PROGRESS` (no
   success faked via a trailing `SELECT`).

**Result: PASS.**

---

## 17. Regression

- D7 regression: **102 passed / 0 failed** — no D7 core regression.
- Full regression: **419 passed, 26 skipped, 0 failed** — no D7/D8 regression; the
  new D8 capability did not break existing suites.

**Result: PASS.**

---

## 18. Documentation Accuracy

The brief required treating "do report numbers match the actual run?" as a separate
acceptance item.

- **Actual** (this verification): D8 = 49 passed; D7 = 102 passed; **Full = 419 passed /
  26 skipped / 0 failed**.
- **Repo reports state** (`D8_IMPLEMENTATION_REPORT.md` line 7, `D8_TEST_REPORT.md` line 15
  & 309): **Full = 422 passed / 23 skipped / 0 failed**.

⇒ The report files are **wrong** on both counts (passed 422 vs 419; skipped 23 vs 26).
This is a **pure documentation number error**. It does **not** affect the code, the
schema, the migration, or any acceptance evidence. Per the verdict rules it is a
**Documentation Fix**, not a P0/P1 blocker.

Required fix before/at freeze (non-blocking): correct the two report files to
`419 passed / 26 skipped / 0 failed` (and keep D8=49, D7=102). No code change needed.

---

## 19. P0 / P1 / P2 Findings

| ID | Severity | Area | Finding | Action |
|----|----------|------|---------|--------|
| — | — | — | No P0 or P1 finding. All functional/security criteria PASS. | — |
| DOC-1 | **P2 (Documentation Fix)** | `D8_IMPLEMENTATION_REPORT.md`, `D8_TEST_REPORT.md` | Full-regression counts printed as `422 passed / 23 skipped` but actual run is `419 passed / 26 skipped`. | Correct the numbers in the two report files. Non-blocking. |

No P0/P1 items. The single P2 is a documentation number correction only.

---

## 20. Final Freeze Recommendation

**PASS — READY_TO_FREEZE_D8.**

The D8 Action Case + FSM layer satisfies every acceptance criterion in the brief through
independent execution of the real code:

- Stable, deterministic Action Case identity; correct create/reuse; correct multi-intent
  parallelism.
- Correct root-cause suppression (no duplicate generic `DELIVERY_RECOVERY`).
- Correct, strictly-validated FSM with terminal `CLOSED`, valid close reasons, and clear
  separation between Action Bucket and lifecycle stage.
- Correct authorization isolation (cross-org and same-org-other-operator transitions
  denied with frozen state; manager legal op succeeds; reconcile payload injection
  rejected wholesale with no partial write).
- Correct observation scope (no cross-operator pollution; `Top-N` absence does not cause
  false `NOT_OBSERVED`; independent `action_case_observations` feed confirmed pre-Top-N).
- Correct zero-risk screened handling (existing case → `NOT_OBSERVED`/still `ACTIVE`/not
  auto-closed; zero-risk orders with no prior case create nothing).
- Correct Information Gap → `INFORMATION_COMPLETION` (`NEEDS_JUDGMENT`).
- Correct concurrency/CAS guarantees (partial unique index, reconcile reuse, CAS miss
  detection, no swallowed DB errors).
- D7 regression clean; full regression 0 failed.

**One Documentation Fix required (non-blocking):** update `D8_IMPLEMENTATION_REPORT.md`
and `D8_TEST_REPORT.md` to report the actual Full-regression result
**`419 passed / 26 skipped / 0 failed`** (D8 `49 passed`, D7 `102 passed`).

D9 items were deliberately **not** evaluated and their absence is **not** a D8 defect
(per §15).

---

### Appendix A — Independent attack harness

File: `wb_d8_independent_verification.py` (in the same directory).
Run: `PYTHONPATH=. python wb_d8_independent_verification.py`

Attack groups (all PASS, 23/23):

```
[PASS] IV.identity.10x_reuse
[PASS] IV.identity.deterministic
[PASS] IV.multi_intent.parallel
[PASS] V.rootcause.delivery+logistics
[PASS] V.rootcause.delivery+supplier
[PASS] V.rootcause.delivery_only
[PASS] VI.fsm.legal_path
[PASS] VI.fsm.illegal_rejected
[PASS] VI.fsm.close_reason
[PASS] VI.fsm.bucket_no_auto_advance
[PASS] VII.A.cross_org_transition
[PASS] VII.B.other_operator
[PASS] VII.C.manager_legal
[PASS] VII.D.reconcile_injection
[PASS] VIII.observation_pollution
[PASS] IX.topn_not_observation
[PASS] X.zero_risk.screened
[PASS] X.zero_risk.no_case_created
[PASS] XI.information_gap
[PASS] XII.legacy_fallback.no_not_observed
[PASS] IX.observation_feed_pre_topn
[PASS] XIII.concurrency_cas
[PASS] XIII.no_except_swallow
INDEPENDENT ATTACK SUMMARY: 23 passed / 0 failed
```

### Appendix B — Commands actually executed

```
PYTHONPATH=. pytest -q tests/test_d8_action_case.py        -> 49 passed
PYTHONPATH=. pytest -q tests/test_d7_risk_engine.py tests/test_d7_integration.py -> 102 passed
PYTHONPATH=. pytest -q                                    -> 419 passed, 26 skipped, 0 failed
PYTHONPATH=. python wb_d8_independent_verification.py     -> 23 passed / 0 failed
```
