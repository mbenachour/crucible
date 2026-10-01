"""Patch rewrite: turn a Hunter's `fix_plan` into a `proposed_patch` (issue #111).

The Hunter says where and what to change. A patch-rewrite model is shown each
file of the plan (`crucible.validation.patching.resolve_plan` picks the text)
and returns it with the fix made, as a plain chat completion — no tool call,
no diff. Git writes the diff (`patching.build_patch`).

Every reply passes deterministic guards first: a reply cut off at the output
limit, one that is unchanged, keeps under half of the original lines, or drops
the edges of an excerpt is rejected. The built patch then runs through
`mechanical.patch_problems`. A rejection or a problem earns one more round with
the reason; after that the finding goes on with an empty `proposed_patch` and
Pass A fails it. A rewrite failure never goes back to the Hunter — its plan
was fine.

Which model: `ModelRegistry.patch_rewrite_endpoint` (`PATCH_REWRITE_MODEL`).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from crucible.validation.mechanical import patch_problems
from crucible.validation.patching import (
    Target,
    build_patch,
    normalize_rewrite,
    rewrite_problem,
    splice,
)

log = logging.getLogger("crucible.patch_rewrite")

PROMPT = "patch/rewrite.md"
MAX_ROUNDS = 2  # the first try plus one retry carrying the reason


@dataclass
class RewriteOutcome:
    patch: str = ""                                    # "" when nothing usable was built
    calls: int = 0                                     # model calls spent
    errors: list[str] = field(default_factory=list)    # every rejection / patch problem, in order


def prompt_version() -> str:
    from crucible.skills import skill_front_matter

    return skill_front_matter(PROMPT).get("version", "0")


def rewrite(model, targets: list[Target], repo: str, *, context: str) -> RewriteOutcome:
    """Rewrite every target, build the patch, check it; one retry round."""
    from crucible.skills import load_skill

    system = load_skill(PROMPT)
    out = RewriteOutcome()
    accepted: dict[str, str] = {}
    feedback: dict[str, str] = {}
    for _round in range(MAX_ROUNDS):
        for t in targets:
            if t.path in accepted:
                continue
            out.calls += 1
            text, problem = _rewrite_one(model, system, t, context, feedback.get(t.path, ""))
            if problem:
                out.errors.append(f"{t.path}: {problem}")
                feedback[t.path] = problem
            else:
                accepted[t.path] = text
        if len(accepted) < len(targets):
            continue
        patch = build_patch(repo, {t.path: splice(t, accepted[t.path]) for t in targets})
        problems = patch_problems(patch, repo)
        if not problems:
            out.patch = patch
            return out
        out.errors += problems
        reason = "the patch built from your text failed a check: " + "; ".join(problems)
        feedback = {t.path: reason for t in targets}
        accepted.clear()
    log.warning("patch rewrite: no usable patch after %d call(s): %s",
                out.calls, "; ".join(out.errors)[:400])
    return out


def _rewrite_one(model, system: str, t: Target, context: str, feedback: str) -> tuple[str, str]:
    """(normalized text, "") or ("", why it was rejected)."""
    from langchain_core.messages import HumanMessage, SystemMessage

    try:
        reply = model.invoke([SystemMessage(content=system),
                              HumanMessage(content=_ask(t, context, feedback))])
    except Exception as e:  # noqa: BLE001 — provider error: treat as a rejected round
        return "", f"model call failed: {type(e).__name__}: {str(e).splitlines()[0][:200]}"
    meta = getattr(reply, "response_metadata", None) or {}
    if meta.get("finish_reason") == "length":
        return "", "the reply was cut off at the output-token limit"
    text = normalize_rewrite(t, extract_text(_content(reply)))
    problem = rewrite_problem(t, text)
    return ("", problem) if problem else (text, "")


def _ask(t: Target, context: str, feedback: str) -> str:
    steps = "\n".join(
        f"- {'new file' if s.new_file else f'lines {s.line_start}-{s.line_end}'}: {s.change.strip()}"
        for s in t.steps
    )
    if t.new_file:
        head = f"## `{t.path}` — a new file\n\nWrite its complete contents."
        body = ""
    else:
        if t.whole:
            head = f"## `{t.path}` — the whole file ({len(t.lines)} lines)"
        else:
            head = (f"## `{t.path}` — an excerpt: file lines {t.start + 1}-{t.end} "
                    f"of {len(t.lines)}. Return the whole excerpt.")
        fence = "`" * max(3, _longest_backtick_run(t.text) + 1)
        body = f"{fence}\n{t.text}{'' if t.text.endswith(chr(10)) else chr(10)}{fence}"
    ask = f"# The finding\n\n{context.strip()}\n\n# Fix plan for this file\n\n{steps}\n\n{head}\n\n{body}"
    if feedback:
        ask += (f"\n\nYour previous answer for this file was rejected: {feedback}\n"
                "Start again from the text above.")
    return ask.rstrip() + "\n"


def _content(reply) -> str:
    c = getattr(reply, "content", "")
    if isinstance(c, str):
        return c
    return "".join(p.get("text", "") if isinstance(p, dict) else str(p) for p in c or [])


def extract_text(reply: str) -> str:
    """The text inside the reply's fenced block — outermost fences, so a file
    that itself contains ``` survives — or the whole reply if it has none."""
    lines = reply.splitlines(keepends=True)
    fences = [i for i, ln in enumerate(lines) if ln.lstrip().startswith("```")]
    if len(fences) >= 2:
        return "".join(lines[fences[0] + 1:fences[-1]])
    if len(fences) == 1:
        i = fences[0]
        # a lone opening fence (reply cut short) or a lone closing one
        return "".join(lines[i + 1:] if i < len(lines) / 2 else lines[:i])
    return reply


def _longest_backtick_run(text: str) -> int:
    best = run = 0
    for ch in text:
        run = run + 1 if ch == "`" else 0
        best = max(best, run)
    return best
