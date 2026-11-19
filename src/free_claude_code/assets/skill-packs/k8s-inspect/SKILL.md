# Kubernetes inspect

Read-only cluster inspection and evidence collection.

1. Always report the current kubectl context and namespace first; findings without a context are worthless.
2. Default to read-only verbs: get, describe, logs, rollout status, events. Never apply, delete, scale or edit without explicit approval.
3. For a failing workload, collect: deployment/pod status, restart counts, the last terminated state, recent events and bounded log tails.
4. Redact secret values in any captured output before reporting.
