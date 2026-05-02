Launch 3 Explore agents in parallel. One lens each. Observations only, `file:line` cited.

## Agent 1 — SOLID (backend, `backend/app/`)
For each principle, sample breadth before concluding. Report violations with file:line and a one-sentence smell description.
- SRP — units with >1 reason to change (HTTP + persistence + rules + formatting mixed).
- OCP — new entity/status/role requires editing, not extending. Type-switches and isinstance chains are tells.
- LSP — subclasses/implementations that narrow preconditions, widen postconditions, or raise unexpected errors.
- ISP — consumers depending on surface area they don't use. Fat bases, catch-all utility modules.
- DIP — high-level modules importing concrete low-level (DB session, ORM, HTTP client) instead of receiving abstractions.

**Verification rule**: Before reporting any finding, read the full function or class body at the cited line. Do not infer a violation from a file name, directory placement, or function signature alone. If the code does not contain the smell when read in full, discard the finding.

## Agent 2 — SOLID (frontend, `frontend/src/`)
Same principles, frontend idiom:
- SRP — components that fetch + transform + validate + render; hooks owning unrelated state.
- OCP — adding a field/entity/status requires touching N files in lockstep.
- LSP — wrappers that silently change the contract callers expect.
- ISP — prop interfaces with one-caller optionals; contexts exposing everything.
- DIP — components importing API functions directly instead of receiving them.
Also: is client-side state ownership (local / hook / server cache) legible from code alone?

**Verification rule**: Before claiming a concern is not separated, explicitly state what IS already in a separate file or hook. Only report an SRP violation if the unseparated logic is actually present in the cited file — not merely absent from a dedicated module.

## Agent 3 — Agentic discoverability
Answer with evidence: "If an agent added a new entity (routes, service, repo, frontend list+form+detail) tomorrow, what would it get wrong, and why?"
- Pattern legibility — can the canonical pattern for any concern be inferred from one file?
- Naming truthfulness — do filenames/class names match their contents?
- Convergence vs. divergence — how many variants exist per concern? Which dominates by count?
- Stale artifacts — files/docs describing something other than the current codebase.
- Doc truthfulness — README/CLAUDE.md/AGENTS.md vs. reality.
- Recent drift — skim `git log`; has recent work diverged without doc updates?

**Route orphan rule**: When flagging a backend route as potentially stale or orphaned, check the corresponding `frontend/src/api/` file before reporting. If the frontend calls that route method (GET/POST/PATCH/DELETE), it is not orphaned — do not report it.

**Recent work rule**: Run `git log --oneline -15`. If a finding touches code committed in the last 5 commits, note this explicitly so the synthesizer can assess whether it is regression or surviving debt.

Do not label anything "canonical" unless the code has clearly converged on it.
