# Enterprise classroom adapter

This course pre-enables the manifest-allowlisted return/cancellation capabilities. No Client negotiation tool is required. The bundled client_api/openapi.yaml is the complete public endpoint catalog. Deployed response variations and asynchronous workflows still require robust handling. Business policies and grading remain unchanged.

Use the project-root README for local tests and remote t1/p1/t2. The upstream run_local_test tool is NOT exposed by this adapter. Local unit tests are not formal evaluation. workspace/ means agent/.

Retail field mapping: evidence user_id -> API customer_id; evidence payment_history -> API payments. Each payment retains its amount, payment_method_id and transaction_type. Do not infer a refund from cancellation alone; inspect the returned state.
