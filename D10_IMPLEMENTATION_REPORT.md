# FlowOrder D10 P0 Implementation Report

**Date:** 2026-08-14  
**Scope:** BusinessActionSubmission / BusinessActionPlan / short UoW / Transactional Outbox / scoped idempotency / minimal immutable audit  
**Status:** `IMPLEMENTED_PENDING_INDEPENDENT_ACCEPTANCE`

## 1. Product boundary preserved

D10 is implemented strictly downstream of the frozen object model:

`Risk Signal → Action Case → Task → BusinessAction → Outbox`

No D8 Action Case FSM code and no D9 Task/Waiting FSM code was modified.

D10 V1 freezes `Task 1 → 0..1 primary BusinessAction`. If two side effects can independently succeed/fail/retry, they must be modeled as separate Tasks.

## 2. New code

- `d10_business_action.py`
  - `BusinessActionSubmission`
  - `BusinessActionPlan`
  - deterministic `request_hash` / `effect_hash`
  - organization / Task / Case boundary validation
  - atomic submit UoW
  - same-request idempotent replay
  - changed-request idempotency conflict
  - concurrent duplicate convergence
- `D10_BUSINESS_ACTION_OUTBOX_CONTRACT.md`
- Alembic revision `h0e1f2a3b4c5`
- `schema.sql` D10 tables
- `tests/test_d10_business_action.py`

## 3. New persistence objects

### d10_business_actions
Durable record of the formal business action request. D10 only uses status `ACCEPTED`.

### d10_outbox_events
One `PENDING` `BUSINESS_ACTION_REQUESTED` event per BusinessAction. D10 does not dispatch it.

### d10_idempotency_records
Scoped by `(organization_id, idempotency_key)`, with `request_hash` to distinguish true retry from accidental key reuse.

### d10_audit_events
Immutable minimum audit evidence: org, actor, request, entity, before/after, reason, source, timestamp.

## 4. Atomicity

A new submission owns one short transaction:

`idempotency reservation → BusinessAction → Outbox → Audit → COMMIT`

Forced exceptions at all intermediate stages are verified to roll back all four D10 record types.

## 5. Important semantic guard

`ACCEPTED != external success`.

The return object explicitly includes:

`external_effect_executed = false`

No ERPNext/CRM/email write is performed by D10.

## 6. Concurrency finding and fix

During full regression, a concurrent-duplicate attack found a return-semantics race: the DB correctly created only one BusinessAction, but one duplicate request could see the Task-unique action before its earlier idempotency read and incorrectly return `D10TaskActionConflict`.

Fix: the Task-unique BusinessAction is now a second convergence boundary. If its `idempotency_key + request_hash` matches, the request returns the original BusinessAction/Outbox as an idempotent replay. If the request differs, conflict is still hard rejected.

20 concurrent identical submissions now converge to exactly:

- 1 BusinessAction
- 1 Outbox
- 1 Idempotency record
- 1 Audit event

## 7. Explicit non-goals

Not implemented in D10:

- ERPNext production write
- any external write adapter
- approval policy / supervisor approval
- Redis / Taskiq / Dispatcher
- Outbox consumption/retry worker
- RESULT_UNCERTAIN reconciliation

These are intentionally reserved for later Days.
