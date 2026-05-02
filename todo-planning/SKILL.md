---
name: todo-planning
description: Executes task lists from .claude/todo-planning/todo.md. Use this skill whenever the user says "run my todo list", "work through my todos", "execute the todo plan", "do the todo", "start the todo", "run todo-planning", or any similar phrase indicating they want Claude to read and perform the tasks in their todo planning file. Always use this skill when the user references .todo_planning or todo.md and wants tasks carried out.
---

# Todo Planning

This skill reads `.claude/todo-planning/todo.md`, performs each task using available tools, tracks progress inline, and archives the completed list.

## Step 1: Find the Todo File

Read `.claude/todo-planning/todo.md`. If it doesn't exist, tell the user and stop — do not proceed.

## Step 2: Parse the List

Determine the list type by looking at how items are written:

- **Unordered list** — items start with `-`, `*`, or `+`. Complete these in whatever order makes the most sense given the tasks (e.g., dependencies, logical grouping). Order is flexible.
- **Ordered list** — items start with numbers (`1.`, `2.`, etc.). Complete these strictly in numbered sequence.

Apply the same rule recursively to nested sublists: unordered sub-items can be done in any order, ordered sub-items must follow their sequence.

Skip any items already marked complete (`[x]` or `[X]`).

## Step 3: Assess Task Complexity

Before executing anything, review every uncompleted task and judge its scope. Flag any task that would require more than a few basic implementation steps — for example: designing a new system, implementing a multi-part feature, refactoring across many files, or anything that realistically warrants its own implementation plan.

For each flagged task, prompt the user (one at a time, not all at once):

> **Task:** "[task text]"
> This task looks too complex to batch in a todo list. How would you like to handle it?
> **(a) Save as plan** — extract it into a dedicated plan file
> **(b) Defer** — mark it skipped for now
> **(c) Keep anyway** — leave it in the list and execute it

Handle the response:

- **(a) Save as plan** — Attempt to invoke the `superpowers:writing-plans` skill. If it is unavailable or fails, create a stub at `.claude/plans/<task-name>.md` containing the task description and a note that it needs full planning. Then mark the item in `todo.md` as `- [→] task text — moved to plan: .claude/plans/<task-name>.md` so it is skipped during execution.
- **(b) Defer** — Mark it in `todo.md` as `- [~] task text — deferred: too complex`. It will be skipped during execution.
- **(c) Keep anyway** — Leave the item unchanged. It will be executed normally in Step 4.

Once all flagged tasks are resolved, proceed to Step 4.

## Step 4: Execute Each Task

For every task:

1. **Announce** what you're starting: e.g., `Starting: Add error handling to the payment service`
2. **Do the work** — interpret the natural language description and carry it out using your available tools (read files, edit code, run commands, etc.)
3. **Mark it complete** — update `todo.md` by changing the item to use `[x]`:
   - `- task text` → `- [x] task text`
   - `- [ ] task text` → `- [x] task text`
   - `1. task text` → `1. [x] task text`
4. **Continue** to the next task

### If a Task Fails

Stop immediately. Report:
- Which task failed
- What went wrong and why

Then ask: **"Should I continue to the next task, or abort the todo list?"** Wait for the user's answer before doing anything else. If they say abort, stop. If they say continue, mark the failed task with a note (e.g., `- [!] task text — failed: reason`) and move on.

## Step 5: Archive the Completed List

Once all tasks are checked off:

1. Scan `.claude/todo-planning/` for files matching `N_todo.md` (where N is a number)
2. Find the **highest** existing number — if `1_todo.md` and `3_todo.md` both exist, the highest is `3`
3. Rename `todo.md` to `{highest + 1}_todo.md` (e.g., `4_todo.md`)
4. Confirm to the user: `All tasks complete. todo.md archived as 4_todo.md.`

If no numbered files exist yet, rename to `1_todo.md`.
