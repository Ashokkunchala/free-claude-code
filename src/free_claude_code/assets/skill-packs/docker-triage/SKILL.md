# Docker triage

Diagnose container and image problems.

1. Confirm the daemon is reachable and which context is active before inspecting anything.
2. Prefer read-only inspection: docker ps, inspect, logs --tail, stats --no-stream, compose ps and logs.
3. Restarting, removing or rebuilding anything is a privileged change: state the blast radius and get explicit approval first.
4. Never prune. Cleanup commands are destructive and out of scope for triage.
