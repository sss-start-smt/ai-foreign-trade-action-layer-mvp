# FlowOrder D19 GitHub Repair Manifest

This repair package was generated against the uploaded current repository archive:
`ai-foreign-trade-action-layer-mvp-main (1).zip`

Reason:
Some files from the previous web upload were flattened into the repository root instead of preserving
`static/`, `tests/`, and `alembic/` directories.

Upload rule:
- Extract this ZIP.
- On GitHub -> Add file -> Upload files.
- Drag the following DIRECTORY ITEMS themselves, preserving their folder names:
  - `static`
  - `tests`
  - `alembic`
  - `.gitignore`
- Do NOT open those folders and select all nested files separately.

Repair file count: 28

Files repaired:
- `.gitignore`
- `alembic/README`
- `alembic/env.py`
- `alembic/script.py.mako`
- `alembic/versions/57f7ae97bf3c_initial_schema.py`
- `alembic/versions/a1b2c3d4e5f6_add_tracking_columns.py`
- `alembic/versions/b7c9e1d3f5a8_d4_second_round.py`
- `alembic/versions/c8d9e1f2a3b4_d5_conflict_and_retry.py`
- `alembic/versions/d45b6b640e68_add_communication_and_import_tables.py`
- `alembic/versions/d6e7f8a9b0c1_erpnext_readonly.py`
- `alembic/versions/e5a1b7c2d8f4_add_org_id_and_audit.py`
- `alembic/versions/f8a3b7c2d1e4_add_action_cases.py`
- `alembic/versions/g9d0e1f2a3b4_add_d9_task_waiting.py`
- `alembic/versions/h0e1f2a3b4c5_add_d10_business_action_outbox.py`
- `alembic/versions/j1k2l3m4n5o6_add_org_id_to_jobs_and_messages.py`
- `alembic/versions/k2l3m4n5o6p7_enforce_tenant_not_null.py`
- `alembic/versions/l3m4n5o6p7q8_add_d12_human_reviews.py`
- `alembic/versions/m4n5o6p7q8r9_add_d13_agent_runtime_trace.py`
- `alembic/versions/n5o6p7q8r9s0_add_d14_quality_and_fact_conflicts.py`
- `alembic/versions/o6p7q8r9s0t1_add_d15_durable_execution.py`
- `alembic/versions/p7q8r9s0t1u2_add_d16_observability_flags.py`
- `static/app.js`
- `static/d19.css`
- `static/d19_app.js`
- `static/index.html`
- `static/styles.css`
- `tests/conftest.py`
- `tests/test_d19_shadow_ui.py`

Important:
The repository currently also contains harmless flattened duplicates at the root (for example
`d19.css`, `d19_app.js`, migration scripts, and `test_d19_shadow_ui.py`). They are not used by
the deployed runtime once the correct directories are restored. They can be cleaned in a later
repository hygiene commit.
