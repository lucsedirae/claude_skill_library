---
name: code-quality-analysis
description: Use when asked to analyze a codebase for design coherence, SOLID violations, orphaned artifacts, or agentic discoverability. Triggers on "analyze the codebase", "code quality review", "find design inconsistencies", "make this more agent-friendly", "apply SOLID", "find dead code", "clean up the codebase", "find orphaned code".
---

# Code Quality Analysis

Analyze any codebase through four lenses: **SOLID violations** (backend), **SOLID violations** (frontend), **agentic discoverability**, and **orphaned artifacts** — whether the codebase is tidy and easy for a new agent to navigate correctly.

You are in discovery mode. Do not seed agents with named files, known issues, or "canonical" patterns — those are theirs to find.

## The loop

**Step 0 — Stack discovery**
Check for `docs/code-quality/stack-discovery.md` in the project root.
- **Not found**: consult [stack-discovery-format.md](stack-discovery-format.md), run full discovery, and write the file.
- **Found**: re-verify — confirm key source paths still exist as directories, test commands still appear in their source files, and check `git log --oneline -5` for config-touching commits since the last verified date. Update only changed fields and refresh the `## Last verified` timestamp. Do not consult stack-discovery-format.md unless rewriting the file from scratch.

**Step 1 — Clarify**
Ask the 3 questions in [questions.md](questions.md). Wait for answers before proceeding.

**Step 2 — Analyze**
Launch up to 4 Explore agents in parallel, one per lens in [lenses.md](lenses.md). Pass each agent the relevant paths from `docs/code-quality/stack-discovery.md`. Skip any agent whose primary path is recorded as "not present" in the discovery file — note the skip in the report. Each agent returns `file:line` observations only — no prescriptions.

**Step 3 — Report**
Synthesize findings into a ranked report using the format in [report.md](report.md).

**Step 4 — Offer a plan**
Ask: *"Want an implementation plan? All findings, a severity band, or a subset you name?"* If yes, write to `.claude/plans/` using [plan.md](plan.md).

## Hard rules

- Do not skip step 0 or step 1.
- Do not tell step 2 agents what to find.
- Every observation cites `file:line`. Uncited observations are discarded.
- The report describes; only the plan prescribes.
- If a category has no findings, say so explicitly. Do not invent findings.
- Only change what the user asks for in step 4.
