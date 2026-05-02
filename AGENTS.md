# Agent Instructions

## Editing Skills

This repository is a **source library** for skills. Each skill also has a deployed copy at `~/.claude/skills/<skill-name>/` that is actively used by Claude Code.

**Before editing any skill file, always confirm with the user:**

> I can update the skill in two places:
> **(a) Repo source** — `<repo-path>/SKILL.md` (tracked in git, not immediately active)
> **(b) Deployed copy** — `~/.claude/skills/<skill-name>/SKILL.md` (active immediately)
> **(c) Both** — keep them in sync
>
> Which would you like?

Do not assume — the user may want only one or both updated depending on whether they are iterating locally, preparing a commit, or fixing an active skill.
