# FlowOrder D19 Demo Ensure Fix

Fixes the symptom: demo orders flash for about one second and then disappear.

Root cause:
- static/index.html contains design-time sample orders.
- d19_app.js then loads the real /api/orders response.
- If Railway's actual runtime database is still empty, the real response replaces the sample with an empty state.

Fix:
1. Keep the business shell hidden until real API data is loaded.
2. When /api/orders is empty and SEED_D19_DEMO_DATA=true, call POST /api/d19/demo/ensure.
3. The server seeds D19-DEMO data into the SAME runtime database used by that request.
4. Re-fetch /api/dashboard and /api/orders.
5. The ensure operation is idempotent and only supports ORG-A demo scope.
6. Bump d19_app.js cache version to d19-shadow-v2.

Verified locally on a fresh database:
- 17 demo orders
- 14 open demo tasks
- 4 pending candidate reviews
- second ensure inserts 0 duplicate rows
- node --check static/d19_app.js passed

Upload these paths preserving directories:
- d19_ui_api.py
- static/index.html
- static/d19_app.js
- tests/test_d19_demo_ensure.py
