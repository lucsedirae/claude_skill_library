Write to `.claude/plans/<descriptive-name>.md`:

1. Context — the SOLID/discoverability problem being addressed.
2. Chosen canonical pattern — for each finding, state the target pattern and why. (First place prescription is allowed.)
3. Per-finding changes — create/modify/delete with file:line refs.
4. Sequence — dependency order with rationale.
5. Verification — `docker compose run --rm backend pytest tests/`, `docker compose run --rm frontend npm test`, plus change-specific smoke steps.
6. Files summary — create / modify / delete lists.
