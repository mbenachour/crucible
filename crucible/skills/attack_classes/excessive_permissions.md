---
name: excessive_permissions
version: 0.1.0
description: An IAM/RBAC grant is broader than the workload needs, so a foothold escalates to wide access.
languages: [hcl, yaml, json]
builtin: true
---

# Methodology

Load this body only when a Hunt task is scoped to `excessive_permissions` (§7).

## Attacker & boundary
Default attacker: someone who compromises the workload / role / service account
(via any other bug, a leaked token, SSRF to the metadata endpoint). Boundary
crossed: "least privilege for this task" → the broad rights actually granted.
Assumption broken: "a foothold here is contained".

## Where to look
- IAM policies with `Action: "*"` / `Resource: "*"`, `iam:PassRole` unscoped,
  `sts:AssumeRole` with a wide trust policy, `*:*` on data stores
- managed policies like `AdministratorAccess` / `PowerUserAccess` on a
  workload role
- Kubernetes `ClusterRole` with `*` verbs/resources, `create pods` +
  `pods/exec`, secrets `list` cluster-wide, binding to `cluster-admin`
- wildcards that enable privilege escalation paths (`iam:CreatePolicyVersion`,
  `iam:AttachRolePolicy`, `lambda:UpdateFunctionCode` on a privileged fn)
- service accounts / instance profiles shared across many workloads
- overly broad trust: `Principal: "*"`, account-wide instead of role-specific

## Move into execution (§9.2)
Statically analyze the policy documents. Enumerate what the grant *actually*
allows vs what the workload's code uses (grep its SDK calls). Name a concrete
escalation: "this role can `iam:PassRole` any role + `lambda:CreateFunction` →
admin". No live cloud calls (egress blocked, §10).

## PoC shape
`poc_test` FAILS clean (asserts the wildcard / escalation-enabling action in
the parsed policy) and PASSES patched (scoped actions + resources, condition
keys, split roles). No source edits outside the patch (§9.4).

## Output
`threat_model.boundary_crossed`: "contained foothold → <broad grant>". Severity
`high`, `critical` when a direct path to account/cluster admin exists.

## Anti-patterns to reject in your own output
- the workload genuinely needs the breadth (an admin/automation tool) and the
  role is not otherwise reachable
- `*` scoped by a tight `Condition` (aws:PrincipalOrgID, resource tags)
- a nit on a read-only `Describe*` with no escalation
