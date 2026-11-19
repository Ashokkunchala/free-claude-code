# Terraform drift

Plan review and drift detection. This skill never applies.

1. Report the working directory, backend, workspace and terraform version before analyzing.
2. Use read-only commands: plan, show, state list, state show, output. Plan output is the source of truth for pending changes; state is the record of what exists.
3. For drift review, separate: configuration vs state (someone changed code), state vs reality (someone changed infrastructure), and provider-version noise.
4. Destroy and replacement entries always get called out explicitly with their resource addresses; they are the dangerous lines in any plan.
5. terraform apply, destroy, state rm and state mv are out of scope and require a separate explicit approval path.
