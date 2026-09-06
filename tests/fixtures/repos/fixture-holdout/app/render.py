"""Planted bug hold-ti-1: server-side template injection.

Held-out fixture — do not tune prompts against this file (specs.md §12).
"""

from jinja2 import Environment


def render(user_template: str, ctx: dict) -> str:
    # BUG(hold-ti-1): user_template is attacker-controlled; compiling it as a
    # template exposes the sandbox-escape / RCE surface.
    return Environment().from_string(user_template).render(**ctx)
