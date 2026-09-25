"""Deterministic CWE tagging, derived from `attack_class` (issue #94).

Every built-in Hunt task is seeded with a known `attack_class` before the
Hunter ever runs (specs.md §9.1) — a closed, code-owned classification, not
something the model decides. That makes a plain lookup table possible here,
with no LLM judgment and no hallucination risk, in contrast to asking a
model to self-report a CWE id per finding (see issue #94's discussion of
VVAH's unvalidated per-finding LLM guess).

`ATTACK_CLASS_CWE` only maps classes where the CWE is unambiguous. A class
that's too broad to map confidently (`misconfiguration`, `protocol_parsing`,
`dynamic_dispatch`, `api_misuse`, `webview_injection`) is left out rather
than guessed — same principle as specs.md §1.8: deterministic code does
deterministic work, and an absent classification beats a wrong one. A
Recon-invented repo-specific attack class (free text, not in this table)
also resolves to no CWE — this module never guesses.
"""

from __future__ import annotations

# attack_class (crucible/skills/attack_classes/*.md stem) -> CWE id.
# Deliberately incomplete: only classes with one unambiguous CWE mapping.
ATTACK_CLASS_CWE: dict[str, str] = {
    "argument_injection":     "CWE-88",
    "auth_bypass":            "CWE-287",
    "cert_pinning_bypass":    "CWE-295",
    "command_injection":      "CWE-78",
    "deeplink_handling":      "CWE-939",
    "excessive_permissions":  "CWE-250",
    "exported_component":     "CWE-926",
    "exposed_secret":         "CWE-200",
    "format_string":          "CWE-134",
    "hardcoded_secret":       "CWE-798",
    "injection_passthrough":  "CWE-74",
    "insecure_storage":       "CWE-312",
    "integer_overflow":       "CWE-190",
    "memory_oob_read":        "CWE-125",
    "memory_oob_write":       "CWE-787",
    "path_traversal":         "CWE-22",
    "sql_injection":          "CWE-89",
    "ssrf":                   "CWE-918",
    "supply_chain":           "CWE-829",
    "template_injection":     "CWE-1336",
    "unsafe_deserialization": "CWE-502",
    "use_after_free":         "CWE-416",
    "xxe":                    "CWE-611",
}

# CWE id -> canonical MITRE name, for the ids this table actually emits.
CWE_NAMES: dict[str, str] = {
    "CWE-22":   "Path Traversal",
    "CWE-74":   "Injection",
    "CWE-78":   "OS Command Injection",
    "CWE-88":   "Argument Injection or Modification",
    "CWE-89":   "SQL Injection",
    "CWE-125":  "Out-of-bounds Read",
    "CWE-134":  "Use of Externally-Controlled Format String",
    "CWE-190":  "Integer Overflow or Wraparound",
    "CWE-200":  "Exposure of Sensitive Information to an Unauthorized Actor",
    "CWE-250":  "Execution with Unnecessary Privileges",
    "CWE-287":  "Improper Authentication",
    "CWE-295":  "Improper Certificate Validation",
    "CWE-312":  "Cleartext Storage of Sensitive Information",
    "CWE-416":  "Use After Free",
    "CWE-502":  "Deserialization of Untrusted Data",
    "CWE-611":  "Improper Restriction of XML External Entity Reference",
    "CWE-787":  "Out-of-bounds Write",
    "CWE-798":  "Use of Hard-coded Credentials",
    "CWE-829":  "Inclusion of Functionality from Untrusted Control Sphere",
    "CWE-918":  "Server-Side Request Forgery (SSRF)",
    "CWE-926":  "Improper Export of Android Application Components",
    "CWE-939":  "Improper Authorization in Handler for Custom URL Scheme",
    "CWE-1336": "Improper Neutralization of Special Elements Used in a Template Engine",
}


def cwe_for_attack_class(attack_class: str) -> str | None:
    """The CWE id for a built-in attack class, or None — never a guess."""
    return ATTACK_CLASS_CWE.get(attack_class)


def cwe_label(cwe_id: str | None) -> str:
    """'CWE-89 - SQL Injection', or just 'CWE-89' if the name isn't in
    `CWE_NAMES`, or '' for None."""
    if not cwe_id:
        return ""
    name = CWE_NAMES.get(cwe_id, "")
    return f"{cwe_id} - {name}" if name else cwe_id
