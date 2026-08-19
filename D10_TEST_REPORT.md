# FlowOrder D10 Test Report

**Date:** 2026-08-14  
**Status:** ChatGPT local P0 tests PASS; independent WorkBuddy acceptance still required before freeze.

## D10 targeted tests

`19 passed / 0 failed`

Coverage includes:

1. normal durable acceptance;
2. BusinessAction + Outbox + idempotency + audit cardinality = 1;
3. `ACCEPTED` and `PENDING` semantics;
4. no D8/D9 state mutation;
5. same idempotency request replay;
6. transport `request_id` may change on retry without creating a new action;
7. canonical JSON key order does not break idempotency;
8. same key + changed payload hard conflict;
9. rollback after idempotency reservation;
10. rollback after BusinessAction insert;
11. rollback after Outbox insert;
12. rollback after Audit insert;
13. restart/reconnect replay remains idempotent;
14. one Task cannot silently create a second independent BusinessAction;
15. WAITING/DONE/CANCELLED Task rejection;
16. CLOSED Action Case rejection;
17. cross-organization rejection;
18. Plan reads Case/Order relationship from DB;
19. 20 concurrent duplicate submissions converge to one durable action.

## D8 + D9 + D10 focused regression

`108 passed / 0 failed`

## Full FlowOrder local regression

`478 passed / 26 skipped / 0 failed`

Skipped tests are pre-existing environment-dependent cases; D10 introduced no new skip.

## Migration verification

Fresh SQLite DB:

`alembic upgrade head`

successfully upgraded the full chain through:

`g9d0e1f2a3b4 → h0e1f2a3b4c5 (head)`

`schema.sql` bootstrap was also independently checked to create all four D10 tables.

## Freeze status

Do **not** mark D10 frozen yet. Next Gate is independent adversarial acceptance, especially:

- rollback/half-state attacks;
- concurrent same/different idempotency attacks;
- organization isolation;
- Task/Case boundary integrity;
- `ACCEPTED` not being misrepresented as external success;
- proof that no external/ERP write path is invoked.
