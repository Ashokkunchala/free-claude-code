# SQL inspect

Read-only database inspection and migration review.

1. Report the connection target (host, database, role) with every finding; a right answer against the wrong database is a wrong answer.
2. Read-only statements only: SELECT, EXPLAIN, SHOW. INSERT, UPDATE, DELETE, DDL and COPY are privileged and need explicit approval.
3. Bound every query (LIMIT, timeout) and prefer EXPLAIN over execution for expensive checks.
4. Migration review checks: lock duration, table rewrites, data loss, missing indexes added concurrently, and whether the down migration actually reverses the up.
