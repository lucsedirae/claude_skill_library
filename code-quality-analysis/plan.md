Write to `.claude/plans/<descriptive-name>.md`:

1. **Context** — the SOLID / discoverability / orphan problem being addressed.
2. **Chosen canonical pattern** — for each finding, state the target pattern and why. (First place prescription is allowed.)
3. **Per-finding changes** — create / modify / delete with `file:line` refs.
4. **Sequence** — dependency order with rationale.
5. **Verification** — use test commands from `docs/code-quality/stack-discovery.md`. If a command is flagged as ambiguous in that file, prompt the user to confirm before writing it into the plan. Include change-specific smoke steps alongside the test commands.
6. **Files summary** — create / modify / delete lists.
