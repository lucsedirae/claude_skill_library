# Stack Discovery Format

Discover the project's shape by reading actual files in the repo. Write the result to `docs/code-quality/stack-discovery.md`, creating `docs/code-quality/` if it does not exist.

## What to discover

### Language & framework
Detect from: `package.json`, `pyproject.toml`, `Cargo.toml`, `go.mod`, `pom.xml`, `*.csproj`, `build.gradle`, `requirements.txt`, `Gemfile`.
Record: primary language(s), framework(s), version(s) where visible.

### Source layout
- **Backend root**: directory containing server-side entry points and modules (look for `src/`, `app/`, `server/`, `api/`, or a framework-specific convention).
- **Frontend root**: directory containing client-side source files, if present (look for `src/`, `client/`, `web/`, `frontend/`).
- **Test root(s)**: directories containing test files (look for `tests/`, `test/`, `__tests__/`, `spec/`).
- **Asset directories**: static files, public assets (look for `public/`, `static/`, `assets/`).
- **Migration directories**: database migration files, if present.

### Test commands
Detect from: `package.json` scripts, `Makefile`, `pyproject.toml` `[tool.pytest]`, `Dockerfile`, `docker-compose.yml` / `compose.yml`, CI config (`.github/workflows/`, `.gitlab-ci.yml`, `.circleci/`).
List all candidate commands. Flag any that are ambiguous (multiple test runners, no obvious default).

### API client convention
Where frontend-to-backend calls are defined: look for `api/`, `services/`, `lib/api`, `hooks/use*Query`, tRPC routers, OpenAPI generated clients, etc.
Record the directory path and the pattern used (fetch wrapper, axios, RTK Query, SWR, tRPC, GraphQL client, etc.).

### Key config files
List files that define the project's shape and expectations for agents:
`CLAUDE.md`, `AGENTS.md`, `README.md`, `docker-compose.yml`, `.env.example`, `.tool-versions`, `.nvmrc`.

## Output file format

Write `docs/code-quality/stack-discovery.md` with this structure:

```markdown
# Stack Discovery

## Language & framework
- Language(s): ...
- Framework(s): ...

## Source layout
- Backend root: `path/to/backend`
- Frontend root: `path/to/frontend` (or "not present")
- Test root(s): `path/to/tests`
- Asset directories: `path/to/assets` (or "not present")
- Migration directories: `path/to/migrations` (or "not present")

## Test commands
- Backend: `<command>`
- Frontend: `<command>` (or "not present")
- Ambiguous: <note if unclear>

## API client convention
- Path: `path/to/api/client/layer`
- Pattern: <fetch wrapper | axios | RTK Query | SWR | tRPC | GraphQL | other>

## Key config files
- `CLAUDE.md` — present / not present
- `AGENTS.md` — present / not present
- `README.md` — present / not present
- `docker-compose.yml` — present / not present
- `.env.example` — present / not present

## Last verified
<ISO date, e.g. 2026-05-02>
```

## Re-verify rules

When the file already exists, check each field:
1. Do the recorded source paths still exist as directories on disk?
2. Do the test commands still appear in their source files?
3. Does `git log --oneline -5` show any commits touching project config files since `## Last verified`?

Update only changed fields. Always refresh `## Last verified`.
If a previously recorded path no longer exists, note it as `REMOVED` rather than deleting the line — this itself may be a discoverability finding.
