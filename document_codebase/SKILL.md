---
name: document-codebase
description: Use when auditing, cleaning up, or improving documentation in a codebase. Provides analysis scripts that flag obsolete comments (commented-out code, redundant comments, stale TODO/FIXME markers) and identify undocumented public functions, classes, and modules. Designed to make codebases more discoverable by agents and humans. Works language-agnostically across Python, JavaScript, TypeScript, C#, Java, and other common languages.
---

# Document Codebase

## Overview

Audit and improve codebase documentation health across two axes:

1. **Remove noise** — flag commented-out code, redundant comments, and stale TODO/FIXME markers that obscure the signal-to-noise ratio.
2. **Ensure coverage** — identify public functions, classes, and modules that lack docstrings or doc comments.

The goal is a codebase where every public symbol has enough documentation for an agent or human to understand its contract without reading its implementation.

## When to Apply

- Auditing a codebase before a team handoff or an agent-assisted workflow
- Preparing a legacy codebase for refactoring
- Reviewing a pull request where comment or documentation quality is a concern
- Establishing a documentation baseline before onboarding new contributors
- Any request to "clean up comments," "improve docs," or "make the codebase more readable"

## Quick Reference

| Category | What it flags | Script |
|---|---|---|
| Commented-out code | Consecutive comment lines containing code tokens | `check_obsolete_comments.py` |
| Redundant comments | Inline comments that restate adjacent identifiers | `check_obsolete_comments.py` |
| Stale markers | TODO/FIXME with no ticket link or an old date | `check_stale_todos.py` |
| Missing docs | Undocumented public functions, classes, modules | `check_doc_coverage.py` |

## Audit Workflow

Run one or more scripts against a file or directory:

```
wsl python3 scripts/check_obsolete_comments.py <path>
wsl python3 scripts/check_stale_todos.py <path>
wsl python3 scripts/check_doc_coverage.py <path>
```

**Default behavior:** Summarize findings and suggest removals or additions.

**Rewrite mode:** Pass `--rewrite` to apply changes in-place. For `check_obsolete_comments.py` this removes flagged commented-out code; for `check_stale_todos.py` this prepends `[STALE?]` tags; for `check_doc_coverage.py` this inserts stub docstrings. Only use when the user explicitly asks for rewrites.

**Additional flags:**
- `--verbose` — show all files, including clean ones
- `--json` — machine-readable JSON output
- `--max-age-days N` — staleness threshold for `check_stale_todos.py` (default: 365)
- `--min-coverage N` — fail if any file documents fewer than N% of public symbols (default: off)
- `--min-echo-ratio R` — redundant-comment sensitivity for `check_obsolete_comments.py` (default: 0.85)

To perform a full audit, run all three scripts sequentially against the target path, then review findings grouped by file before making changes.

## Documentation Generation Guidelines

When adding or rewriting docstrings, apply these directives:

1. **State the contract, not the implementation.** Describe what the function guarantees to its caller — inputs, outputs, exceptions, side effects — not the steps it takes internally.
2. **Document parameters when they carry constraints.** Type hints alone are not enough when units, allowed values, or ordering matter. `amount: int` does not tell the caller whether it is cents or dollars.
3. **Document return values that can be absent or polymorphic.** Always note when a function can return `None`/`null`/`undefined` or when the return type varies by condition.
4. **Name side effects explicitly.** If the function writes to disk, mutates shared state, or makes a network call, say so.
5. **Write for agents and humans equally.** Use specific nouns. Avoid openers like "This function..." — start with a verb or the subject of the contract (e.g., "Submits a payment request...").

## Comment Removal Guidelines

When removing or flagging comments, apply these directives:

1. **Commented-out code:** Always safe to remove. Version control preserves history. If uncertain, confirm with the user before deleting.
2. **Redundant comments:** Only remove when the comment restates the adjacent code verbatim. Never remove comments that explain *why* a particular approach was chosen.
3. **Stale TODOs:** Flag for human resolution rather than deleting outright. The comment may contain context that belongs in a ticket.
4. **Tooling directives:** Never remove `# noqa`, `@ts-ignore`, `eslint-disable`, license headers, copyright notices, or encoding declarations, even if `check_obsolete_comments.py` flags them.

## References

Load these guides when making judgment calls about specific findings:

- `references/obsolete_comment_patterns.md` — when determining whether a comment is genuinely obsolete vs. legitimately explanatory; includes language-specific edge cases and a decision checklist
- `references/documentation_standards.md` — when writing or evaluating docstrings; includes patterns for Python, TypeScript, C#, Java, and Go, plus the minimum documentation checklist
