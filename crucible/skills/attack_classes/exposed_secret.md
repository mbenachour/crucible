---
name: exposed_secret
version: 0.1.0
description: A secret is committed to or rendered by infrastructure code / state / templates where an attacker can read it.
languages: [hcl, yaml, json, dockerfile, ini]
builtin: true
---

# Methodology

Load this body only when a Hunt task is scoped to `exposed_secret` (§7). The
IaC-repo sibling of `hardcoded_secret` — focus on config, state, and templates.

## Attacker & boundary
Default attacker: anyone with repo read, CI log access, a copy of Terraform
state, a rendered manifest, or an image layer. Boundary crossed: secret →
readable IaC artifact. Assumption broken: "this value is injected at deploy
from a secret store".

## Where to look
- literal passwords / keys / tokens in `*.tfvars`, `variables.tf` defaults,
  `terraform.tfstate` (state stores secrets in plaintext), committed
  `*.auto.tfvars`
- `env:` / `environment:` blocks in compose / k8s / task definitions with
  literal credentials instead of `secretKeyRef` / `valueFrom`
- Dockerfile `ENV`/`ARG` secrets, `RUN` commands with inline tokens (persist in
  layer history)
- Helm `values.yaml` with real credentials; `ConfigMap` (not `Secret`) holding
  a password
- CI YAML with plaintext secrets instead of masked vars; `echo`ing secrets
- cloud-init / user-data scripts embedding keys

## Move into execution (§9.2)
Grep the tree and any committed state / rendered output. Show the secret is
real and present in a readable artifact (the state file, the image layer, the
CI log). Do not use it against a live service.

## PoC shape
`poc_test` FAILS clean (asserts the secret literal in the file / rendered
manifest / layer) and PASSES patched (`secretKeyRef` / vault data source /
`--secret` mount / masked CI var). No source edits outside the patch (§9.4).
Rotation → `wishlist_write`.

## Output
`threat_model.boundary_crossed`: "secret → readable IaC artifact". Severity
mirrors the credential's scope; committed cloud admin keys are `critical`.

## Anti-patterns to reject in your own output
- the value is a reference / placeholder resolved from a secret manager
- a non-sensitive config value (region, instance type) flagged as a "secret"
- state file is not committed and its backend is encrypted + access-controlled
