# FlowOrder D19 Demo Coexist Fix V3

Confirmed Railway symptom:
- /api/orders returns existing EXCEL_BASE_IMPORT orders (database is not empty).
- Those existing orders have no generated tasks/risks.
- Previous frontend only called demo/ensure when the ENTIRE order list was empty.
- Therefore D19 demo data was never ensured.

V3 fix:
1. Always query /api/d19/demo/status.
2. Trigger /api/d19/demo/ensure when the D19-DEMO namespace is missing/incomplete,
   regardless of whether unrelated imported orders already exist.
3. Keep existing imported/ERP orders untouched.
4. Re-fetch /api/dashboard and /api/orders after ensure.
5. Return expected_order_count=17 from demo status.
6. Fix review-summary PostgreSQL portability by avoiding TIMESTAMP LIKE text.
7. Bump d19_app.js cache key to d19-shadow-v3.

Regression verification:
- node --check static/d19_app.js: PASS
- D19 seed/ensure/coexistence targeted tests: 5 passed
- Coexistence case specifically verifies 1 pre-existing non-demo order survives,
  17 D19 demo orders are added, total becomes 18, and 14 demo open tasks appear.
