---
name: clawcket-quality-analysis
description: Use when asked to analyze the Clawcket codebase for design coherence, SOLID violations, or agentic discoverability. Triggers on "analyze the codebase", "code quality review", "find design inconsistencies", "make this more agent-friendly", "apply SOLID".
---

# Clawcket Quality Analysis

Analyze the Clawcket codebase (FastAPI + React/Vite + PostgreSQL) through two lenses: **SOLID violations** and **agentic discoverability** — whether an agent joining fresh would form correct assumptions from any single file.

You are in discovery mode. Do not seed agents with named files, known issues, or "canonical" patterns — those are theirs to find.

## The loop

1. **Clarify** — ask the 3 questions in [questions.md](questions.md). Wait for answers.
2. **Discover** — launch 3 Explore agents in parallel, one per lens in [lenses.md](lenses.md). Each returns `file:line` observations only, no prescriptions.
3. **Report** — synthesize into a ranked findings report using the format in [report.md](report.md).
4. **Offer a plan** — ask: *"Want an implementation plan? All findings, a severity band, or a subset you name?"* If yes, write to `.claude/plans/` using [plan.md](plan.md).

## Hard rules

- Do not skip step 1.
- Do not tell Phase 2 agents what to find.
- Every observation cites `file:line`. Uncited observations are discarded.
- The report describes; only the plan prescribes.
- If a category has no issue, say so. Do not invent findings.
- Only change what the user asks for in step 4.
