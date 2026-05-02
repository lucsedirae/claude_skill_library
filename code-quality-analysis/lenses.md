# Analysis Lenses

Before launching agents, read `docs/code-quality/stack-discovery.md` and extract the backend root, frontend root, test roots, asset directories, migration directories, API client path, and test commands. If the file is missing, abort and return to step 0. Skip any agent whose primary path is recorded as "not present".

Launch up to 4 Explore agents in parallel. Pass each agent its relevant paths from the discovery file. Observations only — `file:line` cited. No prescriptions.

---

## SOLID principles reference
Apply these to both Agent 1 and Agent 2 using the idiomatic tells for each context below.

| Principle | Core smell |
|-----------|-----------|
| **SRP** | Unit has >1 reason to change |
| **OCP** | Adding entity/status/role requires editing, not extending |
| **LSP** | Subclass/impl narrows preconditions, widens postconditions, or raises unexpected errors |
| **ISP** | Consumer depends on surface area it doesn't use |
| **DIP** | High-level module directly imports concrete low-level concern |

For each principle, sample breadth before concluding. Report violations with `file:line` and a one-sentence smell description.

---

## Agent 1 — SOLID (backend)
**Path**: backend root from stack discovery.

Idiomatic tells per principle:
- **SRP** — HTTP handling + persistence + business rules + formatting mixed in one place.
- **OCP** — type-switches and isinstance/switch chains that grow with each new case.
- **LSP** — subclasses or interface implementations that raise errors callers don't expect.
- **ISP** — fat base classes, catch-all utility modules.
- **DIP** — direct imports of DB session, ORM model, or HTTP client inside business logic.

**Verification rule**: Before reporting any finding, read the full function or class body at the cited line. Do not infer a violation from a file name, directory placement, or function signature alone. Discard if the smell is absent when read in full.

---

## Agent 2 — SOLID (frontend)
**Path**: frontend root from stack discovery.

Idiomatic tells per principle:
- **SRP** — components that fetch + transform + validate + render; hooks owning unrelated state.
- **OCP** — adding a field/entity/status requires touching N files in lockstep.
- **LSP** — wrappers that silently change the contract callers expect.
- **ISP** — prop interfaces with optionals only one caller uses; contexts that expose everything to everyone.
- **DIP** — components importing API functions or data sources directly instead of receiving them as props or via context.

Also: is client-side state ownership (local state / hook / server cache) legible from the code alone?

**Verification rule**: Before claiming a concern is not separated, explicitly state what IS already in a separate file or hook. Only report an SRP violation if the unseparated logic is actually present in the cited file — not merely absent from a dedicated module.

---

## Agent 3 — Agentic discoverability
Answer with evidence: "If an agent added a new entity tomorrow (routes, service, repo, frontend list + form + detail), what would it get wrong, and why?"

- **Pattern legibility** — can the canonical pattern for any concern be inferred from reading one representative file?
- **Naming truthfulness** — do filenames and class names match their actual contents?
- **Convergence vs. divergence** — how many variants exist per concern? Which dominates by count?
- **Stale artifacts** — files or docs describing something other than the current codebase.
- **Doc truthfulness** — compare `README.md`, `CLAUDE.md`, `AGENTS.md` against reality.
- **Recent drift** — skim `git log --oneline -15`; has recent work diverged without doc updates?

Do not label anything "canonical" unless the code has clearly converged on it.

---

## Agent 4 — Orphaned artifacts
**Paths**: all source roots, test roots, asset directories, and migration directories from stack discovery. Also use the API client path for route cross-referencing.

Goal: find anything no longer actively used by the running application. Prioritize evidence over suspicion — only report what you can cite.

### Routes
- List all backend route definitions (`file:line`).
- For each, search the API client layer (path from stack discovery) for a corresponding call. If no call exists anywhere in the frontend or API client source, flag as potentially orphaned.
- **Exceptions**: routes that are clearly infrastructure (health checks, metrics, webhooks called by third parties) are not orphaned — note them but do not flag as issues.

### Dead code
- Exported functions, classes, or modules with no import sites anywhere in the project (grep/glob the full source tree).
- Commented-out code blocks longer than 5 lines.
- Feature-flagged code where the flag is hardcoded to off, or the flag name no longer appears in any config or env file.

### Orphaned files
- Source files not imported or required by any other file and not an entry point.
- Config files referencing paths, commands, or services that no longer exist.
- Migration files for tables or entities that no longer exist in the current schema.

### Orphaned tests
- Test files whose subject module has been deleted or renamed (file no longer at the imported path).
- Test helpers or fixtures imported by no test file.
- Tests that pass vacuously: empty test body, `skip` with no resolution date, assertions that can never fail.

### Orphaned assets
- Static files (images, fonts, icons, CSS) not referenced in any source file or template.
- Translation / i18n keys defined but never referenced in source.
- Environment variables declared in `.env.example` but never read anywhere in source.

### Recent work rule
Run `git log --oneline -15`. If a finding touches code committed in the last 5 commits, note this explicitly — it may be a migration in progress rather than dead code.

### Confirmation rule
Before flagging anything as orphaned, do a second-pass grep across the full source tree using alternative search terms (partial name, string interpolation patterns). Dynamic references (string interpolation, reflection, `eval`, `require(variable)`) can mask real usage — if you see evidence of dynamic dispatch, downgrade the finding to MEDIUM and note the reason.
