# API runner

Careful API integration work.

1. Record method, URL and status for every call; keep bodies bounded.
2. Paginate with a page cap and honor Retry-After and rate-limit headers instead of fixed sleeps.
3. Retry only idempotent requests, with backoff. A retried POST without an idempotency key can duplicate effects.
4. Mutating calls (POST/PUT/PATCH/DELETE) are privileged: state the effect, get approval, and prefer idempotency keys where the API supports them.
5. Never log Authorization headers or tokens; redact them in captured output.
