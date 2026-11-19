# CI triage

Diagnose continuous-integration failures without changing the repository.

1. Identify the failing workflow, job and step; capture the log excerpt that shows the failure.
2. Classify the failure: regression caused by the change, pre-existing failure, flaky test, or infrastructure degradation (runner slowdowns, network, service outage).
3. For flakes, compare at least two runs of the same commit before blaming code. A test that passes on rerun at unchanged code is a flake or an infrastructure issue, not a regression.
4. Report the root cause, the evidence, and the smallest next step. Never retry loops blindly; two reruns is the default budget before escalating.
