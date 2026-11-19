# Repo recon

Read-only repository reconnaissance before touching code.

1. Map before acting: directory layout, entry points, test layout, build and CI configuration, and the manifests that declare dependencies.
2. Verify every path you cite on disk. A map with invented paths is worse than no map.
3. Use git history for change hotspots and recent authors; they point at where reviews and bugs concentrate.
4. Note the project's own conventions (formatters, type checkers, test commands) and follow them in any later change.
