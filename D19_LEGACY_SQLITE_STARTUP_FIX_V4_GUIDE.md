# FlowOrder D19 Legacy SQLite Startup Fix V4

Confirmed Railway readiness error:
    OperationalError: no such column: organization_id

Root cause:
- Railway is currently using a legacy SQLite database.
- Existing `orders` was created before `organization_id` existed.
- `init_db()` executed current `schema.sql` first.
- `CREATE TABLE IF NOT EXISTS orders` does not add new columns to an existing SQLite table.
- `schema.sql` then immediately tried to create `idx_orders_org` on `orders(organization_id)`.
- Startup failed before `ensure_activation_schema()` could add the missing column.

Fix:
- Add a narrow additive SQLite preflight before executing `schema.sql`.
- For an existing orders table, add any missing activation/tenant columns first.
- Add organization_id to existing tasks/event_logs/approval_requests when needed.
- Commit the additive migration, then execute the full schema and normal migration helpers.
- No table is dropped and no existing business row is deleted.

Verification performed:
1. Legacy SQLite with one existing order and no organization_id.
2. Run current init_db().
3. Existing order is preserved.
4. organization_id is added and backfilled to ORG-A.
5. idx_orders_org and current schema are created.
6. D19 Demo Seed then inserts 17 demo orders and produces 14 open demo tasks.

Focused tests:
- Legacy SQLite startup migration + D19 seed/ensure/coexistence: 6 passed.

Deployment:
1. Overlay this package onto the current GitHub repository, preserving `tests/`.
2. Commit and push.
3. Keep Railway variable: SEED_D19_DEMO_DATA=true.
4. Wait for Railway redeploy.
5. Open /ready. Expected: status=ready, database_ready=true.
6. Refresh FlowOrder. D19 demo data should then be ensured.
