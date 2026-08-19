# D19 Demo Seed Guide

Purpose: populate the deployed FlowOrder database with isolated demo orders so D19 Shadow/Smoke can exercise the real UI and APIs.

## Safety boundary

- Demo rows use deterministic `D19-DEMO` IDs and `initialization_source=D19_DEMO_SEED_V1`.
- Default execution is idempotent: missing demo rows are inserted; existing demo rows are not overwritten.
- `--reset` deletes **only this D19 demo namespace** and recreates it.
- The seed does not write to ERPNext, does not send messages, and does not enable external dispatch.

## Railway usage

Recommended first deployment: set the Railway service variable below, then redeploy once:

```text
SEED_D19_DEMO_DATA=true
```

Startup will add only missing demo rows. Keeping the variable enabled is safe because the default seed is idempotent.

Manual first deployment / top-up (if using a service shell):

```bash
python seed_d19_demo.py
```

Reset the demo dataset to its original state:

```bash
python seed_d19_demo.py --reset
```

Remove all D19 demo orders and their derived rows without recreating them:

```bash
python seed_d19_demo.py --clean
```

Expected summary after a clean reset:

- 17 demo orders
- 14 open demo tasks
- 4 pending candidate reviews
- 12 actionable order rows before Waiting suppression
- 2 Waiting orders
- 3 normal/no-open-task orders
- 5-day handled trend with 4 / 6 / 5 / 7 / 8 completed-task proxies

Representative cases:

- SO-1048: delivery risk / supplier raw-material delay
- SO-1061: logistics confirmation risk
- SO-1032: packaging change / important confirmation
- SO-1027: supplier promised-reply overdue
- SO-1054: missing owner / escalation semantics
- SO-1084: quality blocking
- SO-1102: progress-deadline mismatch
- SO-1009: source conflict + formal delivery-date change requiring manager approval
- SO-1015 / SO-1124: Waiting
- SO-1088 / SO-1112 / SO-1120: normal progress
