# Terminal agent policy: network egress controls

Roadmap section 7 requires network egress controls and explicit allowlists
for remote execution. The policy engine (`terminal/policy.py`) enforces a
network boundary on top of the R0-R3 risk tiers, configured through two
environment variables.

## Configuration

| Variable | Values | Default |
|---|---|---|
| `FCC_NETWORK_EGRESS` | `open`, `allowlist`, `deny` | `open` |
| `FCC_NETWORK_ALLOW` | Comma-separated hostnames | empty |

- `open` keeps the risk-tier flow unchanged (current behavior).
- `allowlist` denies network-capable commands unless every target host is in
  `FCC_NETWORK_ALLOW`. A leading dot matches subdomains:
  `FCC_NETWORK_ALLOW=github.com,.example.com` permits `github.com`,
  `example.com` and `api.example.com`.
- `deny` refuses every network-capable command.

An unknown `FCC_NETWORK_EGRESS` value raises `ValueError` at startup, so a
misspelled mode can never silently weaken the boundary.

## What counts as network-capable

- Remote execution and transfer: `curl`, `wget`, `ssh`, `scp`, `sftp`,
  `rsync`, `nc`/`ncat`/`netcat`, `telnet`.
- Package managers: `pip`, `pip3`, `npm`, `npx`, `pnpm`, `yarn`, `uv`.
- Cloud and cluster CLIs: `aws`, `az`, `gcloud`, `gsutil`, `azcopy`,
  `kubectl`, `helm`, `docker`, `podman`, `terraform`, `tofu`.
- `git`, only for subcommands that talk to a remote: `clone`, `fetch`,
  `pull`, `push`, `remote`, `submodule`. Local git work is unaffected.

## Allowlist semantics

Target hosts are extracted from URL arguments (`https://`, `ssh://`, ...)
and scp-style arguments (`git@host:path`, `user@host`). In `allowlist`
mode the engine **denies** a network-capable command when:

- any extracted host is not allowlisted (`deny:egress-allowlist`), or
- no target host can be determined (`deny:egress-unverifiable`) - for
  example `git push origin main` names a configured remote rather than a
  host, cloud CLIs hide endpoints behind profiles, and a bare `ssh -N` can
  still smuggle a connection through `-o ProxyCommand`.

The boundary denies rather than falling back to an approval prompt: an
approval must not bypass a configured network control. The egress check
also runs before `extra_allow`, so an auto-allowed executable is still
bound by it. Explicit `extra_deny` continues to win in `open` mode.

To permit a currently denied target, add its host to `FCC_NETWORK_ALLOW`
and retry. To pin traffic to one provider during an incident, set for
example `FCC_NETWORK_EGRESS=allowlist` with
`FCC_NETWORK_ALLOW=api.anthropic.com`.
