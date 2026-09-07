---
name: misconfiguration
version: 0.1.0
description: An infrastructure/framework setting exposes a resource or capability that should be private or off.
languages: [hcl, yaml, json, dockerfile, ini]
builtin: true
---

# Methodology

Load this body only when a Hunt task is scoped to `misconfiguration` (§7).
IaC / config repos and framework config within an app.

## Attacker & boundary
Default attacker: an unauthenticated internet client, or any principal in a
broader scope than intended. Boundary crossed: "internal / disabled" → exposed
by a config value. Assumption broken: name the control that the setting turns
off (network isolation, auth requirement, encryption, logging).

## Where to look
- storage/buckets: public-read / public-write ACL, no bucket policy,
  `BlockPublicAccess` off
- security groups / firewalls: `0.0.0.0/0` to `22`, `3389`, `5432`, `6379`,
  `9200`, `27017`, admin ports; NACLs allow-all
- databases / caches: publicly accessible, no auth (`requirepass` unset), no
  TLS, default credentials
- management planes: dashboards / metrics / actuator / debug endpoints exposed;
  `DEBUG=True`, stack traces on; directory listing on
- containers: `privileged`, host network/PID, docker socket mounted, running as
  root, `:latest`
- Kubernetes: no `NetworkPolicy`, `hostPath`, `automountServiceAccountToken`,
  permissive `PodSecurity`
- TLS/encryption at rest disabled; logging/audit disabled

## Move into execution (§9.2)
Statically evaluate the manifest/plan (`terraform plan` output, the rendered
compose/k8s spec). Show the resulting exposure concretely — "this SG permits
`0.0.0.0/0:5432` and the RDS instance is `publicly_accessible = true`". No live
cloud calls (egress blocked, §10); reason from the plan.

## PoC shape
`poc_test` FAILS clean (asserts the insecure value in the parsed/rendered
config) and PASSES patched (scoped CIDR, private flag, auth required,
encryption on). No source edits outside the patch (§9.4).

## Output
`threat_model.boundary_crossed`: "intended private/off → exposed by <setting>".
Severity `critical` for unauthenticated internet exposure of data/admin,
`high` for over-broad internal scope.

## Anti-patterns to reject in your own output
- the resource is public *by design* (a CDN origin, a static site bucket)
- a compensating control elsewhere in the same config closes it (check)
- a lint-style nit with no exposure ("could add a tag")
