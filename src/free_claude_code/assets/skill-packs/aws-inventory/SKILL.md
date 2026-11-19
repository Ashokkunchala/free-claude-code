# AWS inventory

Read-only cloud inventory and diagnostics.

1. Establish identity first: aws sts get-caller-identity, the active profile and the region. Report them with every finding.
2. Stay read-only: describe, list and get calls only. Any create, update, delete, put or tag mutation is a privileged change requiring explicit per-action approval.
3. Diagnose by layer: IAM (policies, roles), networking (VPCs, subnets, security groups, route tables), DNS (hosted zones, records), TLS (certificates, expiry), load balancers (target health) and storage (bucket configuration, encryption).
4. Reference secrets by ARN or name only; never print secret values.
